"""Parameter Validator Module.

This module provides unified parameter validation functionality,
eliminating code duplication across multiple modules.
"""

from typing import Any, Dict, List, Optional

from .exceptions import InvalidParametersError, InvalidTimeWindowError

# 时间常量
MINUTES_PER_DAY = 1440  # 24小时 = 1440分钟
MIN_TIME_MINUTES = 0
MAX_TIME_MINUTES = MINUTES_PER_DAY


class ParameterValidator:
    """参数验证器 - 统一的参数验证逻辑

    该类负责：
    1. 时间窗口验证（格式、范围、逻辑）
    2. 目标参数验证（根据类型）
    3. 通用参数验证

    Example:
        >>> ParameterValidator.validate_time_window([480, 540])  # 08:00-09:00
        >>> ParameterValidator.validate_goal_parameters(params, "learn_topic")
    """

    @staticmethod
    def validate_time_window(time_window: Any, field_name: str = "time_window") -> None:
        """验证时间窗口格式和值范围

        Args:
            time_window: 时间窗口（应为[start_minutes, end_minutes]格式）
            field_name: 字段名称（用于错误消息）

        Raises:
            InvalidTimeWindowError: 时间窗口无效

        验证规则：
        - 必须是列表
        - 必须有2个元素
        - 元素必须是整数
        - 值必须在0-1440范围内（24小时 = 1440分钟）
        - 起始时间必须小于结束时间
        """
        if not isinstance(time_window, list):
            raise InvalidTimeWindowError(
                f"{field_name}必须是列表，当前类型: {type(time_window).__name__}",
                time_window=time_window
            )

        if len(time_window) != 2:
            raise InvalidTimeWindowError(
                f"{field_name}必须包含2个元素，当前: {len(time_window)}个",
                time_window=time_window
            )

        if not all(isinstance(x, int) for x in time_window):
            raise InvalidTimeWindowError(
                f"{field_name}的元素必须是整数，当前: {[type(x).__name__ for x in time_window]}",
                time_window=time_window
            )

        # 验证取值范围（使用常量）
        start, end = time_window
        if not (MIN_TIME_MINUTES <= start < MAX_TIME_MINUTES and MIN_TIME_MINUTES < end <= MAX_TIME_MINUTES):
            raise InvalidTimeWindowError(
                f"{field_name}的值必须在{MIN_TIME_MINUTES}-{MAX_TIME_MINUTES}范围内，当前: {time_window}",
                time_window=time_window
            )

        if start >= end:
            raise InvalidTimeWindowError(
                f"{field_name}的起始时间必须小于结束时间，当前: {time_window}",
                time_window=time_window
            )

    @staticmethod
    def validate_goal_parameters(params: Dict[str, Any], goal_type: str) -> None:
        """根据目标类型验证参数

        Args:
            params: 参数字典
            goal_type: 目标类型

        Raises:
            InvalidParametersError: 参数无效
        """
        # 根据不同的goal_type验证必需参数
        if goal_type == "learn_topic":
            if "topics" not in params:
                raise InvalidParametersError(
                    "learn_topic类型必须包含topics参数"
                )
            # 复用validate_list_field
            ParameterValidator.validate_list_field(params["topics"], "topics", min_items=1)

            # 验证topics中的每个元素都是字符串
            if not all(isinstance(t, str) for t in params["topics"]):
                raise InvalidParametersError(
                    "topics的元素必须都是字符串"
                )

            # 如果有depth参数，验证枚举值
            if "depth" in params:
                ParameterValidator.validate_enum_field(
                    params["depth"], "depth", ["basic", "intermediate", "advanced"]
                )

        elif goal_type == "exercise":
            # 验证运动相关参数
            if "duration" in params and not isinstance(params["duration"], (int, float)):
                raise InvalidParametersError(
                    "duration必须是数字类型"
                )
            if "duration" in params and params["duration"] <= 0:
                raise InvalidParametersError(
                    "duration必须大于0"
                )

        elif goal_type == "social_maintenance":
            # 验证社交相关参数
            if "greeting_type" in params:
                ParameterValidator.validate_enum_field(
                    params["greeting_type"],
                    "greeting_type",
                    ["morning", "afternoon", "evening", "casual"]
                )

        # 可以继续添加其他goal_type的验证规则

    @staticmethod
    def validate_list_field(value: Any, field_name: str, min_items: int = 1) -> None:
        """验证列表字段

        Args:
            value: 字段值
            field_name: 字段名称
            min_items: 最小元素数量

        Raises:
            InvalidParametersError: 字段无效
        """
        if not isinstance(value, list):
            raise InvalidParametersError(
                f"{field_name}必须是列表，当前类型: {type(value).__name__}"
            )

        if len(value) < min_items:
            raise InvalidParametersError(
                f"{field_name}至少需要{min_items}个元素，当前: {len(value)}个"
            )

    @staticmethod
    def validate_enum_field(value: Any, field_name: str, allowed_values: List[str]) -> None:
        """验证枚举字段

        Args:
            value: 字段值
            field_name: 字段名称
            allowed_values: 允许的值列表

        Raises:
            InvalidParametersError: 字段无效
        """
        if value not in allowed_values:
            raise InvalidParametersError(
                f"{field_name}必须是以下值之一: {', '.join(allowed_values)}，当前: {value}"
            )
