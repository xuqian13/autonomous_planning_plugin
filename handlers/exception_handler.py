"""异常处理装饰器模块

提供统一的异常处理装饰器,消除handlers.py中的重复try-except代码。
遵循DRY原则,减少样板代码,提高可维护性。
"""

import functools
from typing import Any, Callable, Optional, Tuple
from src.common.logger import get_logger

logger = get_logger("autonomous_planning.exception_handler")


def handle_exception(
    error_message: str,
    log_level: str = "error",
    exc_info: bool = False,
    default_return: Any = None,
    reraise: bool = False
):
    """异常处理装饰器

    统一处理函数中的异常,自动记录日志并返回默认值。
    消除重复的try-except代码块,符合DRY原则。

    Args:
        error_message: 错误日志消息模板,可使用{e}占位符表示异常对象
        log_level: 日志级别 ("debug", "info", "warning", "error")
        exc_info: 是否包含异常堆栈信息
        default_return: 异常发生时的默认返回值
        reraise: 是否在记录日志后重新抛出异常

    Example:
        >>> @handle_exception("清理目标失败: {e}", log_level="error", exc_info=True)
        ... async def cleanup_old_goals(self):
        ...     # 业务逻辑
        ...     pass

    Returns:
        装饰后的函数,自动包含异常处理逻辑
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # 格式化错误消息
                formatted_message = error_message.format(e=e)

                # 根据指定的日志级别记录日志
                log_func = getattr(logger, log_level, logger.error)
                if exc_info:
                    log_func(formatted_message, exc_info=True)
                else:
                    log_func(formatted_message)

                # 是否重新抛出异常
                if reraise:
                    raise

                # 返回默认值
                return default_return

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # 格式化错误消息
                formatted_message = error_message.format(e=e)

                # 根据指定的日志级别记录日志
                log_func = getattr(logger, log_level, logger.error)
                if exc_info:
                    log_func(formatted_message, exc_info=True)
                else:
                    log_func(formatted_message)

                # 是否重新抛出异常
                if reraise:
                    raise

                # 返回默认值
                return default_return

        # 根据函数类型返回对应的wrapper
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def handle_exception_with_default(
    error_message: str,
    default: Any = None
):
    """简化版异常处理装饰器 - 使用默认配置

    适用于大多数场景:
    - log_level="error"
    - exc_info=True
    - 返回指定的默认值

    Args:
        error_message: 错误日志消息模板
        default: 异常发生时的默认返回值

    Example:
        >>> @handle_exception_with_default("清理失败: {e}", default=False)
        ... async def cleanup(self):
        ...     # 业务逻辑
        ...     pass
    """
    return handle_exception(
        error_message=error_message,
        log_level="error",
        exc_info=True,
        default_return=default,
        reraise=False
    )


def handle_exception_silent(
    error_message: str,
    log_level: str = "debug"
):
    """静默异常处理装饰器 - 用于非关键操作

    适用于可以容忍失败的操作:
    - log_level="debug"或"warning"
    - exc_info=False (不记录堆栈)
    - 返回None

    Args:
        error_message: 错误日志消息模板
        log_level: 日志级别 ("debug"或"warning")

    Example:
        >>> @handle_exception_silent("缓存预热失败: {e}", log_level="warning")
        ... def warm_cache(self):
        ...     # 业务逻辑
        ...     pass
    """
    return handle_exception(
        error_message=error_message,
        log_level=log_level,
        exc_info=False,
        default_return=None,
        reraise=False
    )
