"""Schedule Semantic Validator Module.

This module provides semantic validation for schedule items.
"""

from typing import Dict, List, Optional, Tuple

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.validator")


class ScheduleSemanticValidator:
    """
    日程语义验证器

    检查日程的语义合理性，包括：
    - 时间合理性（用餐时间、作息时间等）
    - 活动持续时间
    - 优先级匹配
    """

    # 合理时间范围（小时）- 基于常识的时间安排
    REASONABLE_TIME_RANGES = {
        "meal": {
            # 用餐时间遵循常规生活习惯
            "早餐": (6, 9),    # 早餐 6:00-9:00
            "午餐": (11, 14),  # 午餐 11:00-14:00
            "晚餐": (17, 20),  # 晚餐 17:00-20:00
            "早饭": (6, 9),
            "午饭": (11, 14),
            "晚饭": (17, 20),
        },
        "daily_routine": {
            "睡觉": [(22, 24), (0, 6)],  # 22点-次日6点（跨午夜）
            "睡前": [(21, 24), (0, 2)],  # 睡前活动：21点-次日2点
            "起床": (6, 10),
            "洗漱": (6, 23),
        },
        "social_maintenance": {
            "夜聊": (20, 24),  # 夜聊：20点-24点
            "晚安": (21, 24),  # 晚安：21点-24点
        },
        "study": {
            "上课": (8, 18),
            "自习": (8, 23),
            "学习": (8, 23),
        },
        "exercise": {
            "运动": [(6, 9), (17, 22)],  # 早上或晚上
            "健身": [(6, 9), (17, 22)],
        }
    }

    def validate(self, items: List[Dict]) -> Tuple[List[Dict], List[str]]:
        """
        语义验证

        Args:
            items: 日程项列表

        Returns:
            (有效项列表, 警告列表)
        """
        valid_items = []
        warnings = []

        for idx, item in enumerate(items):
            item_warnings = []

            # 1. 检查时间合理性
            time_warning = self._check_time_reasonableness(item)
            if time_warning:
                item_warnings.append(time_warning)

            # 2. 检查活动持续时间
            duration_warning = self._check_duration(item, items)
            if duration_warning:
                item_warnings.append(duration_warning)

            # 3. 检查优先级合理性
            priority_warning = self._check_priority_match(item)
            if priority_warning:
                item_warnings.append(priority_warning)

            if item_warnings:
                warnings.append(f"第{idx+1}项 ({item.get('name', '未命名')}): " + "; ".join(item_warnings))

            # 即使有警告也保留该项（只是记录）
            valid_items.append(item)

        return valid_items, warnings

    def _check_time_reasonableness(self, item: Dict) -> Optional[str]:
        """检查时间是否合理"""
        time_slot = item.get("time_slot", "")
        goal_type = item.get("goal_type")
        name = item.get("name", "")

        if not time_slot:
            return None

        try:
            hour = int(time_slot.split(":")[0])
        except (ValueError, IndexError, AttributeError) as e:
            logger.warning(f"时间格式错误: {time_slot} - {e}")
            return "时间格式错误"

        # 检查用餐时间
        if goal_type == "meal":
            for meal_name, (start_h, end_h) in self.REASONABLE_TIME_RANGES["meal"].items():
                if meal_name in name:
                    if not (start_h <= hour <= end_h):
                        return f"{meal_name}时间不合理（{time_slot}），建议{start_h:02d}:00-{end_h:02d}:00"

        # 检查作息时间
        if goal_type == "daily_routine":
            for routine_name, time_range in self.REASONABLE_TIME_RANGES["daily_routine"].items():
                if routine_name in name:
                    if isinstance(time_range, list):
                        # 跨午夜的时间段
                        in_range = any(start <= hour <= end for start, end in time_range)
                        if not in_range:
                            return f"{routine_name}时间不合理（{time_slot}）"
                    else:
                        start_h, end_h = time_range
                        if not (start_h <= hour <= end_h):
                            return f"{routine_name}时间不合理（{time_slot}），建议{start_h:02d}:00-{end_h:02d}:00"

        # 检查学习时间
        if goal_type == "study":
            for study_name, (start_h, end_h) in self.REASONABLE_TIME_RANGES["study"].items():
                if study_name in name:
                    if not (start_h <= hour <= end_h):
                        return f"{study_name}时间不合理（{time_slot}），建议{start_h:02d}:00-{end_h:02d}:00"

        # 检查运动时间
        if goal_type == "exercise":
            for exercise_name, time_ranges in self.REASONABLE_TIME_RANGES["exercise"].items():
                if exercise_name in name:
                    in_range = any(start <= hour <= end for start, end in time_ranges)
                    if not in_range:
                        return f"{exercise_name}时间不合理（{time_slot}），建议早上6-9点或晚上17-22点"

        # 检查社交活动时间（如夜聊等）
        if goal_type == "social_maintenance":
            for social_name, time_range in self.REASONABLE_TIME_RANGES.get("social_maintenance", {}).items():
                if social_name in name:
                    if isinstance(time_range, list):
                        # 跨午夜的时间段
                        in_range = any(start <= hour <= end for start, end in time_range)
                        if not in_range:
                            return f"{social_name}时间不合理（{time_slot}）"
                    else:
                        start_h, end_h = time_range
                        if not (start_h <= hour <= end_h):
                            return f"{social_name}时间不合理（{time_slot}），建议{start_h:02d}:00-{end_h:02d}:00"

        return None

    def _check_duration(self, item: Dict, all_items: List[Dict]) -> Optional[str]:
        """检查活动持续时间是否合理"""
        time_slot = item.get("time_slot", "")
        name = item.get("name", "")

        if not time_slot:
            return None

        # 找到下一个活动的时间
        current_minutes = self._parse_time_to_minutes(time_slot)

        next_minutes = None
        for other in all_items:
            if other != item:
                other_minutes = self._parse_time_to_minutes(other.get("time_slot", ""))
                if other_minutes > current_minutes:
                    if next_minutes is None or other_minutes < next_minutes:
                        next_minutes = other_minutes

        if next_minutes:
            duration = next_minutes - current_minutes

            # 检查持续时间是否合理
            if duration < 15:
                return f"持续时间过短（{duration}分钟），建议至少15分钟"

            # 睡觉、休息、自由时间可以超过3小时
            if duration > 180 and "自由" not in name and "休息" not in name and "睡" not in name and "安睡" not in name:
                return f"持续时间过长（{duration}分钟），建议不超过3小时"

        return None

    def _check_priority_match(self, item: Dict) -> Optional[str]:
        """检查优先级是否与活动类型匹配"""
        goal_type = item.get("goal_type")
        priority = item.get("priority")
        name = item.get("name", "")

        # 吃饭、睡觉应该是high或medium优先级
        if goal_type in ["meal", "daily_routine"]:
            if "睡觉" in name or "吃" in name or "早饭" in name or "午饭" in name or "晚饭" in name:
                if priority == "low":
                    return "基本生理需求应该设为medium或high优先级"

        return None

    @staticmethod
    def _parse_time_to_minutes(time_str: str) -> int:
        """将HH:MM转换为分钟数"""
        try:
            parts = time_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError, AttributeError):
            return 0
