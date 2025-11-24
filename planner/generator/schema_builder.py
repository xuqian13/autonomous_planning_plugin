"""Schema Builder Module.

This module provides JSON Schema building functionality for schedule generation.
Separated from BaseScheduleGenerator to follow Single Responsibility Principle.
"""

from typing import Any, Dict

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.schema_builder")


class SchemaBuilder:
    """Schema构建器 - 单一职责：构建JSON Schema

    该类负责：
    1. 构建日程项的JSON Schema
    2. 配置约束规则（字段长度、枚举值等）
    3. 提供Schema验证标准

    与BaseScheduleGenerator的区别：
    - 只负责Schema构建，不涉及其他业务逻辑
    - 配置驱动，易于扩展
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化Schema构建器

        Args:
            config: 配置字典
        """
        self.config = config
        self._cached_schema = None  # 缓存Schema（避免重复构建）

    def build_json_schema(self) -> dict:
        """构建JSON Schema，约束LLM输出格式

        使用缓存机制，避免重复构建相同的Schema。

        优势：
        1. 强制类型检查（时间格式必须是HH:MM）
        2. 枚举约束（goal_type只能是预定义值）
        3. 必填字段检查
        4. 长度限制（防止过长或过短）

        Returns:
            JSON Schema字典
        """
        if self._cached_schema is not None:
            return self._cached_schema

        # 从配置读取参数
        min_activities = self.config.get('min_activities', 8)
        max_activities = self.config.get('max_activities', 15)
        min_desc_len = self.config.get('min_description_length', 15)
        max_desc_len = self.config.get('max_description_length', 50)

        self._cached_schema = {
            "type": "object",
            "required": ["schedule_items"],
            "properties": {
                "schedule_items": {
                    "type": "array",
                    "minItems": min_activities,
                    "maxItems": max_activities,
                    "items": {
                        "type": "object",
                        "required": ["name", "description", "time_slot", "goal_type", "priority"],
                        "properties": {
                            "name": {
                                "type": "string",
                                "minLength": 2,
                                "maxLength": 20,
                                "description": "活动名称"
                            },
                            "description": {
                                "type": "string",
                                "minLength": min_desc_len,
                                "maxLength": max_desc_len,
                                "description": f"活动描述（叙述风格，{min_desc_len}-{max_desc_len}字）"
                            },
                            "time_slot": {
                                "type": "string",
                                "pattern": "^([01]?[0-9]|2[0-3]):[0-5][0-9]$",
                                "description": "时间点，HH:MM格式（如09:30）"
                            },
                            "goal_type": {
                                "type": "string",
                                "enum": [
                                    "daily_routine",      # 日常作息
                                    "meal",               # 吃饭
                                    "study",              # 学习
                                    "entertainment",      # 娱乐
                                    "social_maintenance", # 社交
                                    "exercise",           # 运动
                                    "learn_topic",        # 兴趣学习
                                    "rest",               # 休息
                                    "free_time",          # 自由时间
                                    "custom"              # 自定义
                                ],
                                "description": "活动类型"
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "优先级"
                            },
                            "duration_hours": {
                                "type": "number",
                                "minimum": 0.25,
                                "maximum": 12,
                                "description": "活动持续时长（小时）"
                            },
                            "parameters": {
                                "type": "object",
                                "description": "额外参数"
                            },
                            "conditions": {
                                "type": "object",
                                "description": "执行条件"
                            }
                        }
                    }
                }
            }
        }

        return self._cached_schema
