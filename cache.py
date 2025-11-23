"""缓存模块 - 自主规划插件

本模块提供性能优化的缓存机制：
    - LRUCache: 线程安全的LRU缓存，支持异步和同步接口
    - ConversationCache: 追踪对话上下文以智能注入日程信息

性能特性：
    - 自动缓存过期
    - 线程安全操作
    - 内存高效的LRU淘汰机制

使用示例：
    >>> from cache import LRUCache, ConversationCache
    >>> cache = LRUCache(max_size=100)
    >>> await cache.set("key", "value")
    >>> value = await cache.get("key")
"""

import asyncio
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.cache")


class LRUCache:
    """线程安全的LRU缓存，支持异步和同步接口

    功能特性：
        - 达到max_size时自动淘汰最少使用的项
        - 线程安全的锁机制
        - 同时支持异步和同步访问模式

    参数：
        max_size: 缓存的最大项数（默认：100）

    使用示例：
        >>> cache = LRUCache(max_size=50)
        >>> await cache.set("user_123", {"name": "Alice"})
        >>> data = await cache.get("user_123")
    """

    def __init__(self, max_size: int = 100):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    async def get(self, key: Any) -> Optional[Any]:
        """Get cached value (async, thread-safe).

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        async with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def get_sync(self, key: Any) -> Optional[Any]:
        """Get cached value (sync, thread-safe).

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found
        """
        with self._sync_lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    async def set(self, key: Any, value: Any) -> None:
        """Set cached value (async, thread-safe).

        Args:
            key: Cache key
            value: Value to cache
        """
        async with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def set_sync(self, key: Any, value: Any) -> None:
        """Set cached value (sync, thread-safe).

        Args:
            key: Cache key
            value: Value to cache
        """
        with self._sync_lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def clear(self) -> None:
        """Clear all cached items."""
        with self._sync_lock:
            self.cache.clear()

    def items(self) -> List[Tuple[Any, Any]]:
        """Return all cached key-value pairs.

        Returns:
            List of (key, value) tuples
        """
        with self._sync_lock:
            return list(self.cache.items())

    def __delitem__(self, key: Any) -> None:
        """Delete cached item.

        Args:
            key: Cache key to delete
        """
        with self._sync_lock:
            if key in self.cache:
                del self.cache[key]

    def __contains__(self, key: Any) -> bool:
        """Check if key exists in cache.

        Args:
            key: Cache key to check

        Returns:
            True if key exists, False otherwise
        """
        with self._sync_lock:
            return key in self.cache

    def __getitem__(self, key: Any) -> Any:
        """Get cached value without moving to end.

        Args:
            key: Cache key

        Returns:
            Cached value

        Raises:
            KeyError: If key not found
        """
        with self._sync_lock:
            return self.cache[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        """Set cached value (supports cache[key] = value syntax).

        Args:
            key: Cache key
            value: Value to cache
        """
        self.set_sync(key, value)


class ConversationCache:
    """对话上下文缓存，用于智能注入日程信息

    追踪对话状态以决定何时注入日程信息：
        - 自上次注入以来的消息数
        - 自上次注入以来的时间
        - 自动过期清理

    功能特性：
        - 按聊天ID独立追踪
        - 自动过期（默认30分钟）
        - 线程安全操作

    参数：
        expire_seconds: 缓存过期时间（秒，默认：1800）

    使用示例：
        >>> cache = ConversationCache(expire_seconds=1800)
        >>> cache.add_message("chat_123")
        >>> if cache.should_inject("chat_123", threshold=5):
        ...     # 注入日程信息
        ...     cache.mark_injected("chat_123")
    """

    def __init__(self, expire_seconds: int = 1800):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.expire_seconds = expire_seconds
        self.lock = threading.Lock()

    def add_message(self, chat_id: str) -> None:
        """Record a new message in the conversation.

        Args:
            chat_id: Chat identifier
        """
        with self.lock:
            current_time = time.time()

            if chat_id not in self.cache:
                self.cache[chat_id] = {
                    "message_count": 0,
                    "last_injection_time": 0,
                    "last_injection_message_count": 0,
                    "created_at": current_time,
                }

            self.cache[chat_id]["message_count"] += 1

    def should_inject(self, chat_id: str, message_threshold: int = 5,
                     time_threshold: int = 300) -> bool:
        """Check if schedule should be injected in current conversation.

        Args:
            chat_id: Chat identifier
            message_threshold: Minimum messages since last injection (default: 5)
            time_threshold: Minimum seconds since last injection (default: 300)

        Returns:
            True if schedule should be injected, False otherwise
        """
        with self.lock:
            if chat_id not in self.cache:
                return False

            current_time = time.time()
            cache_entry = self.cache[chat_id]

            # Check message count threshold
            messages_since_injection = (
                cache_entry["message_count"] -
                cache_entry["last_injection_message_count"]
            )

            # Check time threshold
            time_since_injection = (
                current_time - cache_entry["last_injection_time"]
            )

            return (
                messages_since_injection >= message_threshold and
                time_since_injection >= time_threshold
            )

    def mark_injected(self, chat_id: str) -> None:
        """Mark that schedule has been injected for this chat.

        Args:
            chat_id: Chat identifier
        """
        with self.lock:
            if chat_id in self.cache:
                current_time = time.time()
                self.cache[chat_id]["last_injection_time"] = current_time
                self.cache[chat_id]["last_injection_message_count"] = (
                    self.cache[chat_id]["message_count"]
                )

    def cleanup_expired(self, current_time: Optional[float] = None) -> int:
        """Remove expired cache entries.

        Args:
            current_time: Current timestamp (default: time.time())

        Returns:
            Number of entries removed
        """
        if current_time is None:
            current_time = time.time()

        with self.lock:
            expired_chats = [
                chat_id
                for chat_id, entry in self.cache.items()
                if current_time - entry["created_at"] > self.expire_seconds
            ]

            for chat_id in expired_chats:
                del self.cache[chat_id]

            if expired_chats:
                logger.debug(
                    f"Cleaned up {len(expired_chats)} expired conversation cache entries"
                )

            return len(expired_chats)

    def get_stats(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """Get conversation statistics for a chat.

        Args:
            chat_id: Chat identifier

        Returns:
            Dictionary with stats or None if chat not found
        """
        with self.lock:
            return self.cache.get(chat_id)

    def clear(self) -> None:
        """Clear all cached conversations."""
        with self.lock:
            self.cache.clear()
