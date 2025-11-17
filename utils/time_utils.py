"""
时间窗口工具函数

提供时间窗口的解析和迁移功能，避免循环导入
"""


def migrate_time_window(time_window):
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


def parse_time_window(time_window):
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
