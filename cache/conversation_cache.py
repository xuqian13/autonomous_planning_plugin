"""对话上下文缓存 - 自主规划插件

追踪对话上下文以智能注入日程信息。

功能特性：
    - 按聊天ID独立追踪
    - 自动过期（默认30分钟）
    - 线程安全操作

使用示例：
    >>> from cache.conversation_cache import ConversationCache
    >>> cache = ConversationCache(expire_seconds=1800)
    >>> cache.add_message("chat_123")
    >>> if cache.should_inject("chat_123", threshold=5):
    ...     cache.mark_injected("chat_123")
"""

import threading
import time
from typing import Any, Dict, Optional

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.cache.conversation")


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
