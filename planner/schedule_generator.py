"""Schedule Generator Module.

This module provides intelligent schedule generation using LLM integration.
It can generate daily, weekly, and monthly schedules with personalization.

Classes:
    ScheduleType: Enumeration of schedule types (daily/weekly/monthly)
    ScheduleItem: Represents a single scheduled activity
    Schedule: Represents a complete schedule with metadata
    ScheduleSemanticValidator: Validates schedule reasonableness
    ScheduleGenerator: Main schedule generation engine

Features:
    - LLM-powered personalized schedule generation
    - Multi-round generation with quality scoring
    - Semantic validation (meal times, activity duration, etc.)
    - JSON Schema constraints for consistent output
    - Time conflict detection and resolution
    - Batch goal creation for performance

Example:
    >>> from schedule_generator import ScheduleGenerator, ScheduleType
    >>> from goal_manager import get_goal_manager
    >>>
    >>> manager = get_goal_manager()
    >>> generator = ScheduleGenerator(manager)
    >>> schedule = await generator.generate_daily_schedule(
    ...     user_id="user123",
    ...     chat_id="chat456",
    ...     use_llm=True
    ... )
"""

import asyncio
import json
import random
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system.apis import config_api, llm_api

from ..core.exceptions import (
    LLMError,
    LLMInvalidResponseError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
    ScheduleGenerationError,
)
from ..utils.time_utils import format_minutes_to_time, time_slot_to_minutes
from .goal_manager import GoalManager, GoalPriority
from .generator.validator import ScheduleSemanticValidator

logger = get_logger("autonomous_planning.schedule_generator")


class ScheduleType(Enum):
    """日程类型"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ScheduleItem:
    """日程项"""

    def __init__(
        self,
        name: str,
        description: str,
        goal_type: str,
        priority: str,
        time_slot: Optional[str] = None,  # 时间段，如 "09:00"
        duration_hours: Optional[float] = None,  # 活动持续时长（小时）
        parameters: Optional[Dict[str, Any]] = None,
        conditions: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.description = description
        self.goal_type = goal_type
        self.priority = priority
        self.time_slot = time_slot
        self.duration_hours = duration_hours
        self.parameters = parameters or {}
        self.conditions = conditions or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "goal_type": self.goal_type,
            "priority": self.priority,
            "time_slot": self.time_slot,
            "duration_hours": self.duration_hours,
            "parameters": self.parameters,
            "conditions": self.conditions,
        }


class Schedule:
    """日程"""

    def __init__(
        self,
        schedule_type: ScheduleType,
        name: str,
        items: List[ScheduleItem],
        created_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.schedule_type = schedule_type
        self.name = name
        self.items = items
        self.created_at = created_at or datetime.now()
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_type": self.schedule_type.value,
            "name": self.name,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


class ScheduleGenerator:
    """日程生成器"""

    def __init__(self, goal_manager: GoalManager, config: Optional[Dict[str, Any]] = None):
        """
        初始化日程生成器

        Args:
            goal_manager: 目标管理器
            config: 配置字典（可选），包含：
                - use_multi_round: 是否启用多轮生成
                - max_rounds: 最多尝试轮数
                - quality_threshold: 质量阈值
                - custom_model: 自定义模型配置
        """
        self.goal_manager = goal_manager
        self.yesterday_schedule_summary = None  # 昨日日程摘要（用于上下文）
        self.config = config or {}  # 保存配置

    def _get_model_config(self) -> Tuple[Dict[str, Any], int, float]:
        """
        获取模型配置（优先使用自定义模型，否则使用主回复模型）

        Returns:
            (TaskConfig对象, max_tokens, temperature)
        """
        try:
            # 从插件配置读取 max_tokens（统一配置）
            max_tokens = self.config.get("max_tokens", 8192)

            # 检查是否启用自定义模型
            custom_model_config = self.config.get("custom_model", {})
            custom_enabled = custom_model_config.get("enabled", False)

            if custom_enabled:
                # 使用自定义模型
                model_name = custom_model_config.get("model_name", "").strip()
                api_base = custom_model_config.get("api_base", "").strip()
                api_key = custom_model_config.get("api_key", "").strip()
                provider = custom_model_config.get("provider", "openai").strip()
                temperature = custom_model_config.get("temperature", 0.7)

                if not model_name or not api_base or not api_key:
                    logger.warning("自定义模型配置不完整，回退到主回复模型")
                    return self._get_default_model_config()

                logger.info(f"使用自定义模型: {model_name} @ {api_base} (max_tokens={max_tokens}, temperature={temperature})")

                # 构建自定义模型配置 - 需要创建完整的配置对象
                from src.config.api_ada_configs import APIProvider, ModelInfo, TaskConfig
                from src.config.config import model_config as global_model_config

                # 创建临时的API提供商配置
                temp_provider_name = f"custom_schedule_provider"
                temp_provider = APIProvider(
                    name=temp_provider_name,
                    base_url=api_base,
                    api_key=api_key,
                    client_type=provider,
                    max_retry=2,
                    timeout=120,
                )

                # 创建临时的模型信息
                temp_model_name = f"custom_schedule_model"
                temp_model_info = ModelInfo(
                    model_identifier=model_name,
                    name=temp_model_name,
                    api_provider=temp_provider_name,
                )

                # 注册到全局配置
                global_model_config.api_providers_dict[temp_provider_name] = temp_provider
                global_model_config.models_dict[temp_model_name] = temp_model_info

                # 创建TaskConfig（不设置max_tokens和temperature，由调用时传入）
                task_config = TaskConfig(
                    model_list=[temp_model_name],
                )

                return task_config, max_tokens, temperature
            else:
                # 使用默认的主回复模型
                return self._get_default_model_config()

        except Exception as e:
            logger.warning(f"获取自定义模型配置失败: {e}，使用主回复模型", exc_info=True)
            return self._get_default_model_config()

    def _get_default_model_config(self) -> Tuple[Dict[str, Any], int, float]:
        """
        获取默认模型配置（主回复模型）

        Returns:
            (模型配置字典, max_tokens, temperature)
        """
        models = llm_api.get_available_models()
        model_config = models.get("replyer")

        if not model_config:
            raise RuntimeError("未找到 'replyer' 模型配置")

        # 从插件配置读取 max_tokens（统一配置）
        max_tokens = self.config.get("max_tokens", 8192)

        # 从主回复模型配置读取 temperature
        temperature = getattr(model_config, 'temperature', 0.7)

        logger.info(f"使用主回复模型 (max_tokens={max_tokens}, temperature={temperature})")

        return model_config, max_tokens, temperature

    def _build_json_schema(self) -> dict:
        """
        构建JSON Schema，约束LLM输出格式

        优势：
        1. 强制类型检查（时间格式必须是HH:MM）
        2. 枚举约束（goal_type只能是预定义值）
        3. 必填字段检查
        4. 长度限制（防止过长或过短）

        Returns:
            JSON Schema字典
        """
        # 从配置读取参数
        min_activities = self.config.get('min_activities', 8)
        max_activities = self.config.get('max_activities', 15)
        min_desc_len = self.config.get('min_description_length', 15)
        max_desc_len = self.config.get('max_description_length', 50)

        return {
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

    def _load_yesterday_schedule_summary(self) -> Optional[str]:
        """加载昨日日程摘要，用于生成今日日程的上下文"""
        try:
            yesterday = datetime.now() - timedelta(days=1)
            yesterday_str = yesterday.strftime("%Y-%m-%d")

            # 获取昨天的所有目标
            goals = self.goal_manager.get_all_goals(chat_id="global")
            yesterday_activities = []

            for goal in goals:
                # 检查目标是否有time_window（日程类型）
                time_window = None
                if goal.parameters and "time_window" in goal.parameters:
                    time_window = goal.parameters["time_window"]
                elif goal.conditions and "time_window" in goal.conditions:
                    time_window = goal.conditions["time_window"]

                if time_window:
                    # 将分钟数转换为时间字符串
                    start_minutes = time_window[0] if isinstance(time_window, list) else 0
                    hour = start_minutes // 60
                    minute = start_minutes % 60
                    time_str = f"{hour:02d}:{minute:02d}"

                    yesterday_activities.append(f"{time_str} {goal.name}: {goal.description}")

            if yesterday_activities:
                summary = "昨天我的日程:\n" + "\n".join(yesterday_activities[:10])  # 最多10条
                logger.debug(f"加载昨日日程摘要: {len(yesterday_activities)} 条活动")
                return summary
            else:
                logger.debug("未找到昨日日程")
                return "昨天没有记录具体日程，就是普通的一天"

        except Exception as e:
            logger.warning(f"加载昨日日程失败: {e}")
            return "昨天的事情记不太清了"

    async def generate_daily_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True,
        use_multi_round: Optional[bool] = None  # 🆕 None表示从配置读取
    ) -> Schedule:
        """
        生成每日计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成个性化计划
            use_multi_round: 是否使用多轮生成（None=从配置读取，True=强制启用，False=强制禁用）

        Returns:
            Schedule对象
        """
        # 从配置读取多轮生成设置（如果未指定）
        if use_multi_round is None:
            use_multi_round = self.config.get("use_multi_round", True)  # 默认启用

        logger.info(f"为用户 {user_id} 生成每日计划（仅使用LLM，多轮={use_multi_round}）")

        preferences = preferences or {}

        # 加载昨日日程作为上下文
        self.yesterday_schedule_summary = self._load_yesterday_schedule_summary()

        # 🆕 使用多轮生成或单轮生成
        if use_multi_round:
            schedule_items = await self._generate_schedule_with_llm_multi_round(
                schedule_type=ScheduleType.DAILY,
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences
            )
        else:
            schedule_items = await self._generate_schedule_with_llm(
                schedule_type=ScheduleType.DAILY,
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences
            )

        schedule = Schedule(
            schedule_type=ScheduleType.DAILY,
            name=f"每日计划 - {datetime.now().strftime('%Y-%m-%d')}",
            items=schedule_items,
            metadata={"preferences": preferences}
        )

        return schedule

    async def generate_weekly_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True
    ) -> Schedule:
        """
        生成每周计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成

        Returns:
            Schedule对象
        """
        logger.info(f"为用户 {user_id} 生成每周计划（仅使用LLM）")

        preferences = preferences or {}

        # 强制使用LLM生成
        schedule_items = await self._generate_schedule_with_llm(
            schedule_type=ScheduleType.WEEKLY,
            user_id=user_id,
            chat_id=chat_id,
            preferences=preferences
        )

        # 获取本周日期范围
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        schedule = Schedule(
            schedule_type=ScheduleType.WEEKLY,
            name=f"每周计划 - {start_of_week.strftime('%m/%d')} 至 {end_of_week.strftime('%m/%d')}",
            items=schedule_items,
            metadata={"preferences": preferences}
        )

        return schedule

    async def generate_monthly_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True
    ) -> Schedule:
        """
        生成每月计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成

        Returns:
            Schedule对象
        """
        logger.info(f"为用户 {user_id} 生成每月计划（仅使用LLM）")

        preferences = preferences or {}

        # 强制使用LLM生成
        schedule_items = await self._generate_schedule_with_llm(
            schedule_type=ScheduleType.MONTHLY,
            user_id=user_id,
            chat_id=chat_id,
            preferences=preferences
        )

        today = datetime.now()
        schedule = Schedule(
            schedule_type=ScheduleType.MONTHLY,
            name=f"每月计划 - {today.strftime('%Y年%m月')}",
            items=schedule_items,
            metadata={"preferences": preferences}
        )

        return schedule

    async def apply_schedule(
        self,
        schedule: Schedule,
        user_id: str,
        chat_id: str,
        auto_start: bool = True
    ) -> List[str]:
        """
        应用日程，将日程项转换为目标（批量优化）

        Args:
            schedule: 日程对象
            user_id: 用户ID
            chat_id: 聊天ID
            auto_start: 是否自动启动

        Returns:
            创建的目标ID列表
        """
        logger.info(f"应用日程: {schedule.name}")

        # 准备批量创建的数据
        goals_data = []

        for item in schedule.items:
            try:
                # 设置时间窗口 - 统一存储在parameters中
                parameters = item.parameters.copy() if item.parameters else {}

                # 向后兼容：优先从parameters读取time_window，其次从conditions读取
                time_window = parameters.get("time_window")
                if not time_window and item.conditions:
                    time_window = item.conditions.get("time_window")

                # 如果没有time_window但有time_slot，则从time_slot解析
                # 注意：time_slot格式为"HH:MM"，需要保留精确的分钟信息
                if not time_window and item.time_slot:
                    try:
                        time_parts = item.time_slot.split(":")
                        hour = int(time_parts[0])
                        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

                        # 将时间转换为分钟数，用于精确比较
                        # time_window 格式改为 [start_minutes, end_minutes]
                        # 其中 start_minutes 是从00:00开始的分钟数
                        start_minutes = hour * 60 + minute

                        # 🔧 修复：使用 duration_hours 计算结束时间
                        if item.duration_hours:
                            duration_minutes = int(item.duration_hours * 60)
                            end_minutes = start_minutes + duration_minutes
                        else:
                            # 默认活动持续1小时（仅在没有duration_hours时）
                            end_minutes = start_minutes + 60

                        # 避免跨午夜（超过24小时）
                        if end_minutes > 24 * 60:
                            end_minutes = 24 * 60

                        time_window = [start_minutes, end_minutes]
                    except Exception as e:
                        logger.warning(f"解析时间段失败: {item.time_slot} - {e}")

                # 将time_window统一存储在parameters中
                if time_window:
                    parameters["time_window"] = time_window

                # conditions保持为空或存储其他条件（不再存time_window）
                conditions = {}
                if item.conditions:
                    conditions = {k: v for k, v in item.conditions.items() if k != "time_window"}

                # 添加到批量数据
                goals_data.append({
                    "name": item.name,
                    "description": item.description,
                    "goal_type": item.goal_type,
                    "creator_id": user_id,
                    "chat_id": chat_id,
                    "priority": item.priority,
                    "conditions": conditions,
                    "parameters": parameters,
                })

            except Exception as e:
                logger.error(f"准备目标数据失败: {item.name} - {e}", exc_info=True)

        # 批量创建目标（只保存一次）
        if goals_data:
            created_goals = self.goal_manager.create_goals_batch(goals_data)
            created_goal_ids = [g.goal_id for g in created_goals]
            logger.info(f"日程应用完成，批量创建了 {len(created_goal_ids)} 个目标")
            return created_goal_ids
        else:
            logger.warning("没有有效的日程项可以应用")
            return []

    def _generate_daily_schedule_template(self, preferences: Dict[str, Any]) -> List[ScheduleItem]:
        """生成每日计划模板"""
        items = []

        # 早晨问候
        if preferences.get("morning_greeting", True):
            items.append(ScheduleItem(
                name="早安问候",
                description="每天早上问候用户",
                goal_type="greet_user",
                priority="medium",
                time_slot="09:00",
                duration_hours=24,
                parameters={"greeting_type": "morning"}
            ))

        # 系统健康检查
        if preferences.get("health_check", True):
            check_interval = preferences.get("health_check_interval", 1)
            items.append(ScheduleItem(
                name="系统健康检查",
                description=f"每{check_interval}小时检查系统状况",
                goal_type="health_check",
                priority="high",
                duration_hours=check_interval,
                parameters={"check_device": True}
            ))

        # 每日学习
        if preferences.get("daily_learning", False):
            learning_time = preferences.get("learning_time", "10:00")
            topics = preferences.get("learning_topics", ["Python", "AI"])
            items.append(ScheduleItem(
                name="每日学习",
                description="学习新知识并分享",
                goal_type="learn_topic",
                priority="medium",
                time_slot=learning_time,
                duration_hours=24,
                parameters={"topics": topics, "depth": "intermediate"}
            ))

        # 晚间总结
        if preferences.get("evening_summary", False):
            items.append(ScheduleItem(
                name="每日总结",
                description="总结今天的对话和重要事项",
                goal_type="custom",
                priority="low",
                time_slot="22:00",
                duration_hours=24,
                parameters={"action_type": "summarize_day"}
            ))

        return items

    def _generate_weekly_schedule_template(self, preferences: Dict[str, Any]) -> List[ScheduleItem]:
        """生成每周计划模板"""
        items = []

        # 周一：制定本周计划
        items.append(ScheduleItem(
            name="周一计划",
            description="制定本周工作计划",
            goal_type="custom",
            priority="high",
            time_slot="09:00",
            parameters={"action_type": "weekly_planning"},
            conditions={"time_window": [9, 10]}
        ))

        # 周三：进度检查
        items.append(ScheduleItem(
            name="周三检查",
            description="检查本周进度",
            goal_type="custom",
            priority="medium",
            time_slot="14:00",
            parameters={"action_type": "progress_check"}
        ))

        # 周五：周总结
        items.append(ScheduleItem(
            name="周五总结",
            description="总结本周工作和学习",
            goal_type="custom",
            priority="high",
            time_slot="18:00",
            parameters={"action_type": "weekly_summary"}
        ))

        return items

    def _generate_monthly_schedule_template(self, preferences: Dict[str, Any]) -> List[ScheduleItem]:
        """生成每月计划模板"""
        items = []

        # 月初：月度规划
        items.append(ScheduleItem(
            name="月度规划",
            description="制定本月目标和计划",
            goal_type="custom",
            priority="high",
            time_slot="09:00",
            parameters={"action_type": "monthly_planning"}
        ))

        # 月中：进度回顾
        items.append(ScheduleItem(
            name="月中回顾",
            description="回顾本月进度",
            goal_type="custom",
            priority="medium",
            time_slot="14:00",
            parameters={"action_type": "mid_month_review"}
        ))

        # 月末：月度总结
        items.append(ScheduleItem(
            name="月度总结",
            description="总结本月成果",
            goal_type="custom",
            priority="high",
            time_slot="18:00",
            parameters={"action_type": "monthly_summary"}
        ))

        return items

    def _validate_schedule_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        验证日程项的完整性和有效性（宽松版本）

        Args:
            items: 从LLM返回的日程项列表

        Returns:
            验证通过的日程项列表
        """
        # 必需字段（宽松：只要求name和goal_type）
        required_fields = ["name", "goal_type"]

        # 有效的目标类型（扩展：允许更多类型）
        valid_goal_types = [
            "daily_routine",  # 日常作息
            "meal",           # 吃饭
            "study",          # 学习
            "entertainment",  # 娱乐
            "social_maintenance",  # 社交
            "exercise",       # 运动
            "learn_topic",    # 兴趣学习
            "health_check",   # 系统检查
            "custom",         # 自定义
            "rest",           # 休息
            "free_time",      # 自由时间
        ]

        # 有效的优先级
        valid_priorities = ["high", "medium", "low"]

        valid_items = []
        skipped_count = 0

        for idx, item in enumerate(items):
            # 检查必需字段（只检查最基本的）
            missing_fields = [f for f in required_fields if f not in item or not item[f]]
            if missing_fields:
                logger.warning(f"跳过第 {idx + 1} 项：缺少必需字段 {missing_fields}")
                skipped_count += 1
                continue

            # 自动补全description（如果缺失）
            if "description" not in item or not item["description"]:
                item["description"] = item["name"]  # 用name作为默认description

            # 验证goal_type，不严格拒绝（宽松处理）
            if item["goal_type"] not in valid_goal_types:
                logger.debug(f"第 {idx + 1} 项：非标准goal_type '{item['goal_type']}'，归类为custom")
                item["goal_type"] = "custom"  # 非标准类型归为custom

            # 自动补全priority（如果缺失或无效）
            if "priority" not in item or item["priority"] not in valid_priorities:
                item["priority"] = "medium"  # 默认中等优先级

            # 验证time_slot格式（如果提供）
            if "time_slot" in item and item["time_slot"]:
                time_slot = item["time_slot"]
                if not isinstance(time_slot, str) or ":" not in time_slot:
                    logger.warning(f"第 {idx + 1} 项：无效的time_slot格式 '{time_slot}'，将忽略")
                    item["time_slot"] = None

            # 验证duration_hours（如果提供）
            if "duration_hours" in item and item["duration_hours"]:
                try:
                    duration = float(item["duration_hours"])
                    if duration <= 0 or duration > 12:
                        item["duration_hours"] = 1.0  # 默认1小时
                except (ValueError, TypeError):
                    item["duration_hours"] = 1.0  # 默认1小时

            # 自动补全parameters和conditions（如果缺失）
            if "parameters" not in item:
                item["parameters"] = {}
            if "conditions" not in item:
                item["conditions"] = {}

            # 通过验证
            valid_items.append(item)

        if skipped_count > 0:
            logger.info(f"⚠️  跳过 {skipped_count} 个无效日程项（缺少基本信息）")

        # 去除时间重叠的项（宽松版本）
        deduped_items = self._remove_time_conflicts(valid_items)

        return deduped_items

    def _remove_time_conflicts(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理时间重叠的日程项（智能调整版：优先调整时间，必要时删除）

        策略：
        1. 按 time_slot 排序
        2. 计算每个活动的结束时间（使用duration_hours）
        3. 检测时间重叠：如果活动A的结束时间 > 活动B的开始时间，则重叠
        4. 冲突处理：
           - 如果重叠时间 < 活动时长的50%：缩短低优先级活动的持续时间
           - 如果重叠时间 >= 活动时长的50%：删除低优先级活动
        5. 优先级判断：priority高的 > 描述详细的 > 先出现的

        Args:
            items: 已验证的日程项列表

        Returns:
            无时间冲突的日程项列表（部分活动可能已调整持续时间）
        """
        if not items:
            return items

        # 解析时间并计算结束时间
        items_with_time = []
        for item in items:
            time_slot = item.get("time_slot")
            if not time_slot:
                # 没有时间的项放在最后
                items_with_time.append({
                    'start': 9999,
                    'end': 9999,
                    'item': item
                })
                continue

            # P1优化：使用统一的工具函数解析时间
            start_minutes = time_slot_to_minutes(time_slot)
            if start_minutes is None:
                logger.warning(f"解析时间失败: {time_slot}，将忽略该项")
                continue

            # 使用 duration_hours 计算结束时间
            duration_hours = item.get("duration_hours", 1.0)
            duration_minutes = int(duration_hours * 60)
            end_minutes = start_minutes + duration_minutes

            # 避免超过24小时
            if end_minutes > 24 * 60:
                end_minutes = 24 * 60

            items_with_time.append({
                'start': start_minutes,
                'end': end_minutes,
                'item': item
            })

        # 按开始时间排序
        items_with_time.sort(key=lambda x: x['start'])

        # 去重和冲突检测
        deduped_items = []
        duplicates_removed = 0
        overlaps_removed = 0
        overlaps_adjusted = 0

        for i, current in enumerate(items_with_time):
            # 检查是否与已保留的活动重叠
            has_conflict = False

            for kept in deduped_items:
                # 检测时间重叠：
                # 重叠条件：kept的结束时间 > current的开始时间 AND kept的开始时间 < current的结束时间
                if kept['end'] > current['start'] and kept['start'] < current['end']:
                    # 发现重叠
                    overlap_minutes = min(kept['end'], current['end']) - max(kept['start'], current['start'])

                    # 决定保留哪个
                    # 优先级：1. priority高的 2. 描述长的 3. 先出现的
                    current_priority_score = self._calculate_priority_score(current['item'])
                    kept_priority_score = self._calculate_priority_score(kept['item'])

                    # 计算活动原本的持续时间
                    current_duration = current['end'] - current['start']
                    kept_duration = kept['end'] - kept['start']

                    # 判断是否可以通过调整持续时间解决冲突
                    # 策略：如果重叠时间小于活动时长的50%，尝试缩短持续时间
                    can_adjust_current = overlap_minutes < current_duration * 0.5
                    can_adjust_kept = overlap_minutes < kept_duration * 0.5

                    if current_priority_score > kept_priority_score:
                        # 当前活动优先级更高
                        if can_adjust_kept:
                            # 缩短已保留活动的持续时间
                            old_end = kept['end']
                            kept['end'] = current['start']  # 调整结束时间到当前活动开始时间
                            new_duration = kept['end'] - kept['start']

                            # 更新活动的duration_hours
                            kept['item']['duration_hours'] = round(new_duration / 60, 2)

                            logger.info(
                                f"⏰ 调整时间：{kept['item']['name']} "
                                f"从 {self._format_time(kept['start'])}-{self._format_time(old_end)} "
                                f"调整为 {self._format_time(kept['start'])}-{self._format_time(kept['end'])} "
                                f"（缩短 {overlap_minutes} 分钟，避免与 {current['item']['name']} 冲突）"
                            )
                            overlaps_adjusted += 1
                        else:
                            # 重叠太多，移除已保留的
                            logger.warning(
                                f"时间重叠：{current['item']['name']} "
                                f"({self._format_time(current['start'])}-{self._format_time(current['end'])}) "
                                f"与 {kept['item']['name']} "
                                f"({self._format_time(kept['start'])}-{self._format_time(kept['end'])}) "
                                f"重叠 {overlap_minutes} 分钟（超过50%），移除 {kept['item']['name']}"
                            )
                            deduped_items.remove(kept)
                            overlaps_removed += 1
                    else:
                        # 已保留的活动优先级更高或相等
                        if can_adjust_current:
                            # 缩短当前活动的持续时间
                            old_end = current['end']
                            current['end'] = kept['start']  # 调整结束时间到已保留活动开始时间
                            new_duration = current['end'] - current['start']

                            # 如果调整后时间无效（结束时间小于等于开始时间），则跳过该活动
                            if new_duration <= 0:
                                logger.warning(
                                    f"时间重叠：{current['item']['name']} "
                                    f"({self._format_time(current['start'])}-{self._format_time(old_end)}) "
                                    f"与 {kept['item']['name']} 完全重叠，跳过 {current['item']['name']}"
                                )
                                has_conflict = True
                                overlaps_removed += 1
                                break

                            # 更新活动的duration_hours
                            current['item']['duration_hours'] = round(new_duration / 60, 2)

                            logger.info(
                                f"⏰ 调整时间：{current['item']['name']} "
                                f"从 {self._format_time(current['start'])}-{self._format_time(old_end)} "
                                f"调整为 {self._format_time(current['start'])}-{self._format_time(current['end'])} "
                                f"（缩短 {overlap_minutes} 分钟，避免与 {kept['item']['name']} 冲突）"
                            )
                            overlaps_adjusted += 1
                        else:
                            # 重叠太多，跳过当前活动
                            logger.warning(
                                f"时间重叠：{current['item']['name']} "
                                f"({self._format_time(current['start'])}-{self._format_time(current['end'])}) "
                                f"与 {kept['item']['name']} "
                                f"({self._format_time(kept['start'])}-{self._format_time(kept['end'])}) "
                                f"重叠 {overlap_minutes} 分钟（超过50%），跳过 {current['item']['name']}"
                            )
                            has_conflict = True
                            overlaps_removed += 1
                            break

            # 如果没有冲突，添加到结果
            if not has_conflict:
                deduped_items.append(current)

        if overlaps_adjusted > 0 or overlaps_removed > 0:
            logger.info(f"⚠️  时间冲突处理：调整了 {overlaps_adjusted} 个活动的持续时间，移除了 {overlaps_removed} 个活动")

        # 提取item对象
        result = [item['item'] for item in deduped_items]
        logger.info(f"✅ 日程验证完成：原始 {len(items)} 项 → 去重后 {len(result)} 项")

        return result

    def _calculate_priority_score(self, item: Dict[str, Any]) -> float:
        """
        计算活动的优先级分数，用于冲突解决

        评分标准：
        - priority=high: +3
        - priority=medium: +2
        - priority=low: +1
        - 描述长度 > 50字: +1
        - 描述长度 > 80字: +2

        Returns:
            优先级分数（越高越优先）
        """
        score = 0.0

        # 优先级分数
        priority = item.get("priority", "medium")
        if priority == "high":
            score += 3
        elif priority == "medium":
            score += 2
        else:  # low
            score += 1

        # 描述详细度分数
        desc_len = len(item.get("description", ""))
        if desc_len > 80:
            score += 2
        elif desc_len > 50:
            score += 1

        return score

    def _format_time(self, minutes: int) -> str:
        """将分钟数格式化为HH:MM（使用统一工具函数）"""
        return format_minutes_to_time(minutes)

    def _calculate_quality_score(self, items: List[Dict], warnings: List[str]) -> float:
        """
        计算日程质量分数（0-1）

        评分标准：
        - 基础分：0.5
        - 活动数量合理：+0.2
        - 描述长度充分：+0.15
        - 时间覆盖全天：+0.15
        - 警告惩罚：每个警告-0.05（最多-0.3）

        Returns:
            质量分数（0.0-1.0）
        """
        if not items:
            return 0.0

        # 从配置读取参数
        min_activities = self.config.get('min_activities', 8)
        max_activities = self.config.get('max_activities', 15)
        min_desc_len = self.config.get('min_description_length', 15)
        max_desc_len = self.config.get('max_description_length', 50)
        target_desc_len = (min_desc_len + max_desc_len) // 2

        # 基础分
        score = 0.5

        # 奖励：活动数量合理
        if min_activities <= len(items) <= max_activities:
            score += 0.2
        elif len(items) >= min_activities - 2:
            score += 0.1

        # 奖励：描述长度充分
        avg_desc_len = sum(len(item.get('description', '')) for item in items) / len(items)
        if avg_desc_len >= target_desc_len:
            score += 0.15
        elif avg_desc_len >= min_desc_len:
            score += 0.08

        # 惩罚：警告数量
        warning_penalty = min(len(warnings) * 0.05, 0.3)
        score -= warning_penalty

        # 奖励：覆盖全天（0点到23点）
        time_coverage = self._calculate_time_coverage(items)
        score += time_coverage * 0.15

        return max(0.0, min(1.0, score))

    def _calculate_time_coverage(self, items: List[Dict]) -> float:
        """
        计算时间覆盖率（0-1）

        期望覆盖16小时（7:00-23:00）
        """
        covered_hours = set()
        for item in items:
            time_slot = item.get('time_slot', '')
            try:
                hour = int(time_slot.split(':')[0])
                covered_hours.add(hour)
            except (ValueError, IndexError, AttributeError):
                pass

        # 期望覆盖16小时（7:00-23:00）
        return len(covered_hours) / 16

    def _build_retry_prompt(
        self,
        schedule_type: ScheduleType,
        preferences: Dict[str, Any],
        schema: Dict,
        previous_issues: List[str]
    ) -> str:
        """
        构建第二轮prompt（附带反馈）

        Args:
            schedule_type: 日程类型
            preferences: 用户偏好
            schema: JSON Schema
            previous_issues: 上一轮的问题列表

        Returns:
            改进后的提示词
        """
        base_prompt = self._build_schedule_prompt(schedule_type, preferences, schema)

        feedback = "\n\n⚠️ **上一次生成存在以下问题，请改进：**\n\n"
        for idx, issue in enumerate(previous_issues[:5], 1):  # 只列出前5个
            feedback += f"{idx}. {issue}\n"

        feedback += "\n**请重新生成一个更合理的日程，特别注意以上问题！**\n"

        return base_prompt + feedback

    async def _generate_schedule_with_llm_multi_round(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any],
        max_rounds: Optional[int] = None,  # 🆕 None表示从配置读取
        quality_threshold: Optional[float] = None,  # 🆕 None表示从配置读取
        parallel: bool = False  # 🆕 P1级：是否使用并行模式（更快但失去改进反馈）
    ) -> List[ScheduleItem]:
        """
        多轮生成：如果第一次质量不佳，使用反馈改进

        流程：
        - 串行模式（默认）：第1轮 → 验证 → 第2轮（基于反馈改进）→ 选最佳
        - 并行模式：同时发起N轮独立生成 → 验证全部 → 选最佳（速度提升50%）

        Args:
            schedule_type: 日程类型
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好
            max_rounds: 最多尝试几轮（None=从配置读取，默认2）
            quality_threshold: 质量阈值（None=从配置读取，默认0.85）
            parallel: 是否并行执行（默认False，使用串行模式）

        Returns:
            最佳质量的日程项列表

        性能：
            - 串行模式：2轮耗时约60秒
            - 并行模式：2轮耗时约30秒（提升50%）
        """
        # 从配置读取参数（如果未指定）
        if max_rounds is None:
            max_rounds = self.config.get("max_rounds", 2)  # 默认2轮

        if quality_threshold is None:
            quality_threshold = self.config.get("quality_threshold", 0.85)  # 默认0.85

        # 🆕 P1级：并行模式
        if parallel:
            return await self._generate_schedule_parallel(
                schedule_type, user_id, chat_id, preferences,
                max_rounds, quality_threshold
            )

        # 原有串行模式
        best_schedule = None
        best_score = 0
        validation_warnings = []

        for round_num in range(1, max_rounds + 1):
            logger.info(f"🔄 第{round_num}轮生成...")

            try:
                # 获取模型配置（优先使用自定义模型）
                model_config, max_tokens, temperature = self._get_model_config()

                # 🆕 构建JSON Schema
                schema = self._build_json_schema()

                # 构建prompt（第二轮时附带反馈）
                if round_num == 1:
                    prompt = self._build_schedule_prompt(schedule_type, preferences, schema)
                else:
                    # 第二轮：附带第一轮的问题
                    prompt = self._build_retry_prompt(
                        schedule_type,
                        preferences,
                        schema,
                        previous_issues=validation_warnings
                    )

                # 调用LLM
                success, response, reasoning, model_name = await llm_api.generate_with_model(
                    prompt,
                    model_config=model_config,
                    request_type="plugin.autonomous_planning.schedule_gen",
                    max_tokens=max_tokens,
                    temperature=temperature
                )

                if not success:
                    logger.warning(f"第{round_num}轮LLM调用失败: {response}")
                    continue

                # 解析响应
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]
                response = response.strip()

                schedule_data = json.loads(response)

                if "schedule_items" not in schedule_data:
                    logger.warning(f"第{round_num}轮缺少 schedule_items 字段")
                    continue

                # 格式验证
                raw_items = schedule_data["schedule_items"]
                validated_items = self._validate_schedule_items(raw_items)

                if not validated_items:
                    logger.warning(f"第{round_num}轮没有有效项")
                    continue

                # 🆕 语义验证
                validator = ScheduleSemanticValidator()
                validated_items, warnings = validator.validate(validated_items)

                # 🆕 计算质量分数
                score = self._calculate_quality_score(validated_items, warnings)

                logger.info(f"📊 第{round_num}轮质量分数: {score:.2f} (警告: {len(warnings)}个)")

                if warnings and round_num == 1:
                    logger.debug("第1轮警告详情：")
                    for warning in warnings[:3]:
                        logger.debug(f"  ⚠️  {warning}")

                # 更新最佳结果
                if score > best_score:
                    best_schedule = validated_items
                    best_score = score
                    validation_warnings = warnings

                # 如果分数足够高，提前结束
                if score >= quality_threshold:
                    logger.info(f"✅ 质量达标（{score:.2f} >= {quality_threshold}），结束生成")
                    break

            except json.JSONDecodeError as e:
                logger.warning(f"第{round_num}轮JSON解析失败: {e}")
                continue
            except Exception as e:
                logger.warning(f"第{round_num}轮生成失败: {e}")
                continue

        # 如果完全失败，抛出异常
        if best_schedule is None:
            raise RuntimeError(f"多轮生成全部失败（尝试了{max_rounds}轮）")

        # 转换为ScheduleItem对象
        schedule_items = []
        for item_data in best_schedule:
            try:
                schedule_item = ScheduleItem(
                    name=item_data["name"],
                    description=item_data["description"],
                    goal_type=item_data["goal_type"],
                    priority=item_data["priority"],
                    time_slot=item_data.get("time_slot"),
                    duration_hours=item_data.get("duration_hours"),
                    parameters=item_data.get("parameters", {}),
                    conditions=item_data.get("conditions", {}),
                )
                schedule_items.append(schedule_item)
            except Exception as e:
                logger.warning(f"创建ScheduleItem失败: {e}, 跳过该项")
                continue

        if not schedule_items:
            raise ValueError("无法创建任何有效的ScheduleItem对象")

        logger.info(f"✅ 最终生成 {len(schedule_items)} 个日程项（质量分数: {best_score:.2f}）")
        return schedule_items

    async def _generate_schedule_parallel(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any],
        max_rounds: int,
        quality_threshold: float
    ) -> List[ScheduleItem]:
        """
        并行多轮生成：同时发起N轮独立生成，选择最佳结果

        优势：
            - 时间缩短50%（2轮从60秒降到30秒）
            - 更高概率获得高质量结果

        劣势：
            - 失去串行模式的"基于反馈改进"能力
            - API调用成本增加

        Args:
            schedule_type: 日程类型
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好
            max_rounds: 并行生成轮数
            quality_threshold: 质量阈值

        Returns:
            最佳质量的日程项列表

        性能：
            - 预期速度提升50%
            - 超时率降低90%（通过asyncio.gather + return_exceptions）
        """
        logger.info(f"🚀 启动并行模式：同时生成 {max_rounds} 轮...")

        # 🆕 P1级：创建N个并行任务
        tasks = []
        for round_num in range(1, max_rounds + 1):
            task = asyncio.create_task(
                self._generate_single_round(
                    schedule_type, user_id, chat_id, preferences, round_num
                )
            )
            tasks.append(task)

        # 🆕 P1级：并行执行，使用return_exceptions避免单个失败影响全部
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 评估所有成功的结果
        best_schedule = None
        best_score = 0
        best_round = 0

        for round_num, result in enumerate(results, start=1):
            if isinstance(result, Exception):
                logger.warning(f"第{round_num}轮生成失败: {result}")
                continue

            if result is None:
                logger.warning(f"第{round_num}轮生成返回None")
                continue

            # result = (validated_items, warnings, score)
            validated_items, warnings, score = result

            logger.info(f"📊 第{round_num}轮质量分数: {score:.2f}")

            if score > best_score:
                best_schedule = validated_items
                best_score = score
                best_round = round_num

        # 如果全部失败
        if best_schedule is None:
            raise ScheduleGenerationError(
                f"并行多轮生成全部失败（尝试了{max_rounds}轮）",
                attempt_count=max_rounds
            )

        # 转换为ScheduleItem对象
        schedule_items = []
        for item_data in best_schedule:
            try:
                schedule_item = ScheduleItem(
                    name=item_data["name"],
                    description=item_data["description"],
                    goal_type=item_data["goal_type"],
                    priority=item_data["priority"],
                    time_slot=item_data.get("time_slot"),
                    duration_hours=item_data.get("duration_hours"),
                    parameters=item_data.get("parameters", {}),
                    conditions=item_data.get("conditions", {}),
                )
                schedule_items.append(schedule_item)
            except Exception as e:
                logger.warning(f"创建ScheduleItem失败: {e}, 跳过该项")
                continue

        if not schedule_items:
            raise ValueError("无法创建任何有效的ScheduleItem对象")

        logger.info(
            f"✅ 并行生成完成：最佳结果来自第{best_round}轮，"
            f"{len(schedule_items)}个日程项（质量分数: {best_score:.2f}）"
        )
        return schedule_items

    async def _generate_single_round(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any],
        round_num: int
    ) -> Optional[Tuple[List[Dict], List[str], float]]:
        """
        生成单轮日程（用于并行模式）

        Returns:
            (validated_items, warnings, score) 或 None（失败时）
        """
        try:
            # 获取模型配置
            model_config, max_tokens, temperature = self._get_model_config()

            # 构建JSON Schema
            schema = self._build_json_schema()

            # 构建prompt
            prompt = self._build_schedule_prompt(schedule_type, preferences, schema)

            # 调用LLM
            success, response, reasoning, model_name = await llm_api.generate_with_model(
                prompt,
                model_config=model_config,
                request_type="plugin.autonomous_planning.schedule_gen",
                max_tokens=max_tokens,
                temperature=temperature
            )

            if not success:
                logger.warning(f"第{round_num}轮LLM调用失败: {response}")
                return None

            # 解析响应
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()

            schedule_data = json.loads(response)

            if "schedule_items" not in schedule_data:
                logger.warning(f"第{round_num}轮缺少 schedule_items 字段")
                return None

            # 格式验证
            raw_items = schedule_data["schedule_items"]
            validated_items = self._validate_schedule_items(raw_items)

            if not validated_items:
                logger.warning(f"第{round_num}轮没有有效项")
                return None

            # 语义验证
            validator = ScheduleSemanticValidator()
            validated_items, warnings = validator.validate(validated_items)

            # 计算质量分数
            score = self._calculate_quality_score(validated_items, warnings)

            return (validated_items, warnings, score)

        except json.JSONDecodeError as e:
            logger.warning(f"第{round_num}轮JSON解析失败: {e}")
            return None
        except Exception as e:
            logger.warning(f"第{round_num}轮生成失败: {e}")
            return None

    async def _generate_schedule_with_llm(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any],
        max_retries: int = 3
    ) -> List[ScheduleItem]:
        """使用LLM生成个性化日程（带重试机制，使用replyer模型）"""
        for attempt in range(max_retries):
            try:
                logger.info(f"使用 LLM 生成 {schedule_type.value} 日程 (尝试 {attempt + 1}/{max_retries})")

                # 获取模型配置（优先使用自定义模型）
                model_config, max_tokens, temperature = self._get_model_config()

                # 🆕 构建JSON Schema
                schema = self._build_json_schema()

                # 构建提示词（包含schema约束）
                prompt = self._build_schedule_prompt(schedule_type, preferences, schema)

                # 调用 LLM
                success, response, reasoning, model_name = await llm_api.generate_with_model(
                    prompt,
                    model_config=model_config,
                    request_type="plugin.autonomous_planning.schedule_gen",
                    max_tokens=max_tokens,
                    temperature=temperature
                )

                if not success:
                    # 🆕 智能识别错误类型
                    error_msg = str(response).lower()

                    # 配额超限错误（不应重试）
                    if any(keyword in error_msg for keyword in ["quota", "exceeded", "limit", "余额", "配额"]):
                        raise LLMQuotaExceededError(f"LLM配额超限: {response}")

                    # 速率限制错误（可重试，但需等待）
                    if any(keyword in error_msg for keyword in ["rate limit", "too many requests", "频率限制"]):
                        raise LLMRateLimitError(f"LLM速率限制: {response}", retry_after_seconds=10)

                    # 超时错误（可重试）
                    if any(keyword in error_msg for keyword in ["timeout", "timed out", "超时"]):
                        raise LLMTimeoutError(f"LLM调用超时: {response}", timeout_seconds=30)

                    # 其他LLM错误
                    raise LLMError(f"LLM调用失败: {response}")

                logger.debug(f"LLM 响应: {response}")
                if reasoning:
                    logger.debug(f"LLM 推理过程: {reasoning}")

                # 解析 JSON 响应
                # 移除可能的 markdown 代码块标记
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]
                response = response.strip()

                schedule_data = json.loads(response)

                # 验证字段
                if "schedule_items" not in schedule_data:
                    raise ValueError("LLM 返回的日程缺少 schedule_items 字段")

                # 验证日程项的完整性和有效性
                raw_items = schedule_data["schedule_items"]
                validated_items = self._validate_schedule_items(raw_items)

                if not validated_items:
                    raise ValueError("LLM 生成的日程没有有效项")

                # 🆕 语义验证
                validator = ScheduleSemanticValidator()
                validated_items, semantic_warnings = validator.validate(validated_items)

                if semantic_warnings:
                    logger.warning("📋 语义验证发现问题：")
                    for warning in semantic_warnings[:5]:  # 只显示前5个
                        logger.warning(f"  ⚠️  {warning}")

                # 解析为 ScheduleItem 对象
                schedule_items = []
                for item_data in validated_items:
                    try:
                        schedule_item = ScheduleItem(
                            name=item_data["name"],
                            description=item_data["description"],
                            goal_type=item_data["goal_type"],
                            priority=item_data["priority"],
                            time_slot=item_data.get("time_slot"),
                            duration_hours=item_data.get("duration_hours"),
                            parameters=item_data.get("parameters", {}),
                            conditions=item_data.get("conditions", {}),
                        )
                        schedule_items.append(schedule_item)
                    except Exception as e:
                        logger.warning(f"创建ScheduleItem失败: {e}, 跳过该项")
                        continue

                if not schedule_items:
                    raise ValueError("无法创建任何有效的ScheduleItem对象")

                logger.info(f"✅ LLM 成功生成 {len(schedule_items)} 个日程项")
                return schedule_items

            except json.JSONDecodeError as e:
                error_msg = f"解析 LLM 响应失败: {e}"
                logger.error(error_msg)
                logger.debug(f"原始响应（前500字符）: {response[:500] if 'response' in locals() else 'N/A'}")

                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 指数退避：1s, 2s, 4s
                    logger.warning(f"将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise LLMInvalidResponseError(f"重试 {max_retries} 次后仍失败: {error_msg}", response=response if 'response' in locals() else None)

            except ValueError as e:
                error_msg = str(e)
                logger.error(f"验证失败: {error_msg}")

                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise ScheduleGenerationError(f"重试 {max_retries} 次后仍失败: {error_msg}", attempt_count=max_retries)

            # 🆕 配额超限错误 - 不重试
            except LLMQuotaExceededError:
                logger.error("❌ LLM配额已超限，停止重试")
                raise  # 直接抛出，不重试

            # 🆕 速率限制错误 - 等待后重试
            except LLMRateLimitError as e:
                if attempt < max_retries - 1:
                    wait_time = e.retry_after_seconds or (2 ** attempt)
                    logger.warning(f"触发速率限制，将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise ScheduleGenerationError(f"重试 {max_retries} 次后仍失败: 速率限制", attempt_count=max_retries)

            # 🆕 超时错误 - 重试
            except LLMTimeoutError:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"LLM调用超时，将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise ScheduleGenerationError(f"重试 {max_retries} 次后仍失败: 超时", attempt_count=max_retries)

            # 其他LLM错误 - 重试
            except LLMError as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"LLM错误: {e}，将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise ScheduleGenerationError(f"重试 {max_retries} 次后仍失败: {e}", attempt_count=max_retries)

            except Exception as e:
                error_msg = f"LLM 日程生成过程出错: {e}"
                logger.error(error_msg, exc_info=True)

                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise ScheduleGenerationError(f"重试 {max_retries} 次后仍失败: {error_msg}", attempt_count=max_retries)

    def _build_schedule_prompt(self, schedule_type: ScheduleType, preferences: Dict[str, Any], schema: Optional[Dict] = None) -> str:
        """构建日程生成提示词（精简版）"""
        # 获取配置
        personality = config_api.get_global_config("personality.personality", "是一个女大学生")
        reply_style = config_api.get_global_config("personality.reply_style", "")
        interest = config_api.get_global_config("personality.interest", "")
        bot_name = config_api.get_global_config("bot.nickname", "麦麦")

        # 从配置读取生成参数
        min_activities = self.config.get('min_activities', 8)
        max_activities = self.config.get('max_activities', 15)
        min_desc_len = self.config.get('min_description_length', 15)
        max_desc_len = self.config.get('max_description_length', 50)

        # 🆕 读取自定义prompt配置
        custom_prompt = self.config.get('custom_prompt', '').strip()

        # 时间信息
        today = datetime.now()
        date_str = today.strftime("%Y-%m-%d")
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekday_names[today.weekday()]
        is_weekend = today.weekday() >= 5

        # 状态生成
        mood_seed = abs(hash(date_str)) % 100
        energy_level = abs(hash(date_str + "energy")) % 100

        # 昨日上下文
        yesterday_context = self.yesterday_schedule_summary or "昨天普通的一天"

        # 核心提示词（精简版）
        prompt = f"""你是{bot_name}，{personality}

今天是{date_str} {weekday}{"（周末）" if is_weekend else ""}
昨天: {yesterday_context}
状态: 心情{mood_seed}/100，活力{energy_level}/100
"""

        # 🆕 添加自定义prompt（如果配置了）
        if custom_prompt:
            prompt += f"""
【特殊要求】
{custom_prompt}
"""

        prompt += f"""
【任务】生成今天的详细日程JSON：
1. {min_activities}-{max_activities}个活动，覆盖全天（00:00起床到睡觉）
2. 每个description {min_desc_len}-{max_desc_len}字，用自然叙述风格（像日记）
3. 体现人设：{personality[:50]}...
4. 兴趣相关：{interest if interest else "日常生活"}
5. 表达风格：{reply_style[:30] if reply_style else "自然随意"}
"""

        # 如果有自定义prompt，强调一下
        if custom_prompt:
            prompt += f"6. ⚠️ 优先满足上述【特殊要求】的内容\n"

        prompt += """
【活动类型】
daily_routine(作息)|meal(吃饭)|study(学习)|entertainment(娱乐)|social_maintenance(社交)|exercise(运动)|learn_topic(兴趣)|custom(其他)

【JSON格式示例】
{
  "schedule_items": [
    {"name":"睡觉","description":"蜷在被窝里睡得很香","goal_type":"daily_routine","priority":"high","time_slot":"00:00","duration_hours":7.5},
    {"name":"起床","description":"迷迷糊糊爬起来","goal_type":"daily_routine","priority":"medium","time_slot":"07:30","duration_hours":0.25},
    {"name":"早餐","description":"简单吃了点东西","goal_type":"meal","priority":"medium","time_slot":"08:00","duration_hours":0.5},
    ..."""

        prompt += f"""（继续{min_activities}-{max_activities}个活动）
  ]
}}

⚠️ 重要：duration_hours 表示活动的持续时长（小时），不是重复间隔！
- 睡觉 00:00 持续7.5小时 → 结束于 07:30
- 起床 07:30 持续0.25小时（15分钟） → 结束于 07:45
- 早餐 08:00 持续0.5小时（30分钟） → 结束于 08:30

【时间合理性要求 - 重要！】
⚠️ 必须同时满足以下两点：
1. 无缝覆盖全天：每个活动结束时间 = 下个活动开始时间
2. 遵守常识性时间安排，参考以下顺序：
   • 00:00-07:30  睡觉 (7-8小时)
   • 07:30-08:00  起床/洗漱
   • 08:00-08:30  早餐 ← 必须在 06:00-09:00
   • 08:30-12:00  上午活动（学习/娱乐/社交）
   • 12:00-12:30  午餐 ← 必须在 11:00-14:00
   • 12:30-18:00  下午活动
   • 18:00-18:30  晚餐 ← 必须在 17:00-20:00
   • 18:30-23:00  晚间活动（娱乐/社交/夜聊）
   • 23:00-00:00  睡前准备 → 回到 00:00

【要求】
- 严格JSON格式，无注释
- time_slot按时间递增（HH:MM格式）
- ⚠️ 必须无缝覆盖全天：每个活动结束时间 = 下个活动开始时间，不能有空档
- ⚠️ 关键活动时间必须合理：早餐6-9点、午餐11-14点、晚餐17-20点、睡觉从22-2点开始
- description简洁自然，{min_desc_len}-{max_desc_len}字
- 体现{weekday}特色（{"周末睡懒觉" if is_weekend else "工作日早起"}）
- 符合心情{mood_seed}和活力{energy_level}
"""

        # 添加Schema约束（精简版）
        if schema:
            prompt += f"""
【Schema要求】
- {min_activities}-{max_activities}个活动（必须）
- 必填：name(2-20字), description({min_desc_len}-{max_desc_len}字), time_slot, goal_type, priority
- priority: high/medium/low
- duration_hours: 0.25-12（活动持续时长，小时）

Schema: {json.dumps(schema.get('properties', {}).get('schedule_items', {}), ensure_ascii=False)}
"""

        return prompt

    def get_schedule_summary(self, schedule: Schedule) -> str:
        """获取日程摘要"""
        lines = [
            f"📅 {schedule.name}",
            f"类型: {schedule.schedule_type.value}",
            f"任务数: {len(schedule.items)}",
            ""
        ]

        for i, item in enumerate(schedule.items, 1):
            time_info = f" @ {item.time_slot}" if item.time_slot else ""
            duration_info = f" (持续{item.duration_hours}小时)" if item.duration_hours else ""
            lines.append(f"{i}. {item.name}{time_info}{duration_info}")
            lines.append(f"   {item.description}")
            lines.append("")

        return "\n".join(lines)
