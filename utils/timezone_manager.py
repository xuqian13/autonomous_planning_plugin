"""Timezone Manager Module.

This module provides centralized timezone handling functionality,
eliminating code duplication across multiple modules.
"""

from datetime import datetime
from typing import Optional

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.timezone_manager")


class TimezoneManager:
    """时区管理器 - 集中管理时区处理，避免重复代码

    该类负责：
    1. 统一的时区配置处理
    2. 安全的时区转换（自动降级到系统时间）
    3. 幂等的当前时间获取

    Example:
        >>> tz_manager = TimezoneManager("Asia/Shanghai")
        >>> now = tz_manager.get_now()
        >>> print(now.strftime("%Y-%m-%d %H:%M:%S"))
    """

    def __init__(self, timezone_str: str = "Asia/Shanghai"):
        """初始化时区管理器

        Args:
            timezone_str: 时区字符串（如 "Asia/Shanghai", "UTC" 等）
        """
        self.timezone_str = timezone_str
        self._tz = self._init_timezone()

    def _init_timezone(self):
        """初始化时区对象

        Returns:
            pytz时区对象，如果初始化失败则返回None
        """
        try:
            import pytz
            return pytz.timezone(self.timezone_str)
        except ImportError:
            logger.warning("pytz模块未安装，将使用系统时区")
            return None
        except Exception as e:
            logger.warning(f"时区初始化失败: {e}，将使用系统时区")
            return None

    def get_now(self) -> datetime:
        """获取当前时间（配置时区）

        如果时区对象可用，返回配置时区的当前时间；
        否则返回系统时区的当前时间。

        Returns:
            datetime: 当前时间对象
        """
        if self._tz:
            return datetime.now(self._tz)
        return datetime.now()
