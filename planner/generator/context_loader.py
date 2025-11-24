"""Context Loader Module.

This module provides historical context loading functionality for schedule generation.
Separated from BaseScheduleGenerator to follow Single Responsibility Principle.
"""

from datetime import timedelta
from typing import List, Optional

from src.common.logger import get_logger

# 类型提示导入
from ...utils.timezone_manager import TimezoneManager
from ..goal_manager import GoalManager

logger = get_logger("autonomous_planning.context_loader")

# 常量定义
MAX_YESTERDAY_ACTIVITIES = 10  # 昨日日程显示的最大活动数


class ScheduleContextLoader:
    """日程上下文加载器 - 单一职责：加载历史上下文

    该类负责：
    1. 加载昨日日程摘要
    2. 格式化上下文信息
    3. 提供给Prompt使用

    与BaseScheduleGenerator的区别：
    - 只负责上下文加载，不涉及Prompt构建或Schema定义
    - 通过构造函数接收所有依赖（依赖注入）
    """

    def __init__(self, goal_manager: GoalManager, tz_manager: TimezoneManager):
        """初始化上下文加载器

        Args:
            goal_manager: 目标管理器
            tz_manager: 时区管理器
        """
        self.goal_manager = goal_manager
        self.tz_manager = tz_manager

    def _extract_time_window(self, goal) -> Optional[List[int]]:
        """从目标中提取time_window

        Args:
            goal: 目标对象

        Returns:
            time_window列表，如果不存在则返回None
        """
        if goal.parameters and "time_window" in goal.parameters:
            return goal.parameters["time_window"]
        elif goal.conditions and "time_window" in goal.conditions:
            return goal.conditions["time_window"]
        return None

    def load_yesterday_schedule_summary(self) -> Optional[str]:
        """加载昨日日程摘要，用于生成今日日程的上下文

        Returns:
            昨日日程摘要字符串，如果加载失败则返回默认文本
        """
        try:
            # 使用时区管理器获取昨天日期
            yesterday = self.tz_manager.get_now() - timedelta(days=1)
            yesterday_str = yesterday.strftime("%Y-%m-%d")

            # 获取昨天的所有目标
            goals = self.goal_manager.get_all_goals(chat_id="global")
            yesterday_activities = []

            for goal in goals:
                # 使用提取方法获取time_window
                time_window = self._extract_time_window(goal)

                if time_window:
                    # 将分钟数转换为时间字符串
                    start_minutes = time_window[0] if isinstance(time_window, list) else 0
                    hour = start_minutes // 60
                    minute = start_minutes % 60
                    time_str = f"{hour:02d}:{minute:02d}"

                    yesterday_activities.append(f"{time_str} {goal.name}: {goal.description}")

            if yesterday_activities:
                summary = "昨天我的日程:\n" + "\n".join(yesterday_activities[:MAX_YESTERDAY_ACTIVITIES])
                logger.debug(f"加载昨日日程摘要: {len(yesterday_activities)} 条活动")
                return summary
            else:
                logger.debug("未找到昨日日程")
                return "昨天没有记录具体日程，就是普通的一天"

        except Exception as e:
            logger.warning(f"加载昨日日程失败: {e}")
            return "昨天的事情记不太清了"
