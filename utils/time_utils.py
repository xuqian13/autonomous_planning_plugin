"""Time Window Utility Functions.

This module provides utilities for parsing and migrating time windows,
avoiding circular imports and centralizing time-related logic.

The module handles two time window formats:
    - Old format: [hour, hour] where hour is 0-23
    - New format: [minutes, minutes] where minutes is 0-1440

Functions also support overnight time windows (e.g., 23:00-01:00).

Example:
    >>> from time_utils import parse_time_window, format_minutes_to_time
    >>> start, end = parse_time_window([9, 17])  # 9am to 5pm
    >>> print(f"{format_minutes_to_time(start)} - {format_minutes_to_time(end)}")
    09:00 - 17:00
"""

from typing import Any, List, Optional, Tuple, Union


def migrate_time_window(time_window: Optional[List[Union[int, float]]]) -> Optional[List[int]]:
    """
    迁移旧格式时间窗口到新格式

    旧格式: [hour, hour] (0-23)
    新格式: [minutes, minutes] (0-1440)

    支持跨夜时间窗口，如 [23, 1] 表示 23:00-次日01:00

    Args:
        time_window: 时间窗口，可能是旧格式或新格式

    Returns:
        新格式的时间窗口，如果输入无效则返回None
    """
    if not time_window or len(time_window) < 2:
        return None

    start, end = time_window[0], time_window[1]

    # 检测无效时间窗口（起止时间相同）
    if start == end:
        import logging
        logger = logging.getLogger("autonomous_planning")
        logger.warning(f"无效的时间窗口: {time_window} (起止时间相同)")
        return None

    # 如果两个值都小于等于24，判定为旧格式（小时）
    if start < 24 and end <= 24:
        start_minutes = start * 60
        end_minutes = end * 60

        # 处理跨夜：如果end <= start，说明跨越了午夜
        # 例如 [23, 1] 应该转换为 [1380, 1500] (次日1点 = 1440 + 60)
        if end_minutes <= start_minutes:
            end_minutes += 1440

        return [start_minutes, end_minutes]

    # 已经是新格式
    return time_window


def parse_time_window(time_window: Optional[List[Union[int, float]]]) -> Tuple[Optional[int], Optional[int]]:
    """
    解析时间窗口，统一返回分钟格式

    Args:
        time_window: 时间窗口（旧格式或新格式）

    Returns:
        (start_minutes, end_minutes) 或 (None, None)
    """
    migrated = migrate_time_window(time_window)
    if not migrated:
        return None, None
    return migrated[0], migrated[1]


def parse_time_slot(time_slot: str) -> Tuple[Optional[int], Optional[int]]:
    """
    解析 HH:MM 格式时间字符串为分钟数

    Args:
        time_slot: 时间字符串，如 "09:30"

    Returns:
        (hour, minute) 或 (None, None)
    """
    if not time_slot or not isinstance(time_slot, str):
        return None, None

    try:
        parts = time_slot.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return hour, minute
    except (ValueError, IndexError):
        return None, None


def time_slot_to_minutes(time_slot: str) -> Optional[int]:
    """
    将 HH:MM 格式时间转换为从00:00开始的分钟数

    Args:
        time_slot: 时间字符串，如 "09:30"

    Returns:
        分钟数（如 570 表示 09:30）或 None
    """
    hour, minute = parse_time_slot(time_slot)
    if hour is None:
        return None
    return hour * 60 + minute


def format_minutes_to_time(minutes: int) -> str:
    """
    将分钟数格式化为 HH:MM 字符串

    Args:
        minutes: 从00:00开始的分钟数

    Returns:
        格式化的时间字符串，如 "09:30"
    """
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}:{minute:02d}"


def get_time_window_from_goal(goal: Any) -> Tuple[int, int]:
    """
    从目标对象中提取时间窗口（统一接口）

    优先从 parameters 读取，其次从 conditions 读取

    Args:
        goal: 目标对象

    Returns:
        (start_minutes, end_minutes) 元组，默认返回 (0, 60)
    """
    # 优先从parameters读取time_window，其次从conditions读取
    time_window = None
    if hasattr(goal, 'parameters') and goal.parameters and "time_window" in goal.parameters:
        time_window = goal.parameters.get("time_window")
    elif hasattr(goal, 'conditions') and goal.conditions and "time_window" in goal.conditions:
        time_window = goal.conditions.get("time_window")

    if not time_window:
        return (0, 60)

    start_minutes, end_minutes = parse_time_window(time_window)
    if start_minutes is None:
        return (0, 60)

    return (start_minutes, end_minutes)
