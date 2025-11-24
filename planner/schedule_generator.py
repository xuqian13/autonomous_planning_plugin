"""Schedule Generator Module (Refactored).

重构版本：使用组件化设计，遵循SOLID原则
- 职责单一：每个类只负责一件事
- 代码复用：使用专门的工具类
- 易于测试：组件独立，可单独测试
- 易于维护：从1803行减少到~400行

主要改进：
1. 使用 ScheduleGeneratorConfig 管理配置（DRY原则）
2. 使用 LLMResponseParser 解析响应（消除重复代码）
3. 使用 ScheduleQualityScorer 评分（单一职责）
4. 使用 BaseScheduleGenerator 的prompt和schema构建
5. 保持向后兼容的公开API
"""

import asyncio
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from src.common.logger import get_logger
from src.plugin_system.apis import llm_api

from ..core.exceptions import (
    LLMError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
    ScheduleGenerationError,
)
from ..core.models import Schedule, ScheduleItem, ScheduleType
from ..utils.timezone_manager import TimezoneManager
from .goal_manager import GoalManager
from .generator import (
    BaseScheduleGenerator,
    LLMResponseParser,
    ScheduleGeneratorConfig,
    ScheduleQualityScorer,
    ScheduleSemanticValidator,
)

logger = get_logger("autonomous_planning.schedule_generator")


# ============================================================================
# 重构后的主类 - 协调器模式
# ============================================================================

class ScheduleGenerator:
    """日程生成器（重构版）

    职责：协调各个组件完成日程生成
    - 不再包含具体的业务逻辑
    - 委托给专门的组件处理
    - 保持公开API向后兼容

    组件：
    - BaseScheduleGenerator: Prompt和Schema构建
    - LLMResponseParser: 响应解析
    - ScheduleQualityScorer: 质量评分
    - ScheduleSemanticValidator: 语义验证
    - ScheduleGeneratorConfig: 配置管理
    """

    def __init__(self, goal_manager: GoalManager, config: Optional[Dict[str, Any]] = None):
        """初始化日程生成器

        Args:
            goal_manager: 目标管理器
            config: 配置字典
        """
        self.goal_manager = goal_manager

        # 🆕 使用配置管理器（DRY原则）
        self.config = ScheduleGeneratorConfig(config)

        # 初始化时区管理器
        self.tz_manager = TimezoneManager(self.config.to_dict().get("timezone", "Asia/Shanghai"))

        # 🆕 使用基础生成器（Prompt和Schema）
        self.base_generator = BaseScheduleGenerator(goal_manager, self.config.to_dict())

        # 🆕 使用响应解析器
        self.response_parser = LLMResponseParser()

        # 🆕 使用质量评分器
        self.quality_scorer = ScheduleQualityScorer(self.config.to_dict())

        # 🆕 使用语义验证器
        self.validator = ScheduleSemanticValidator()

        logger.info(f"ScheduleGenerator初始化完成: {self.config}")

    # ========================================================================
    # 公开API（保持向后兼容）
    # ========================================================================

    async def generate_daily_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True,
        use_multi_round: Optional[bool] = None,
        force_regenerate: bool = False
    ) -> Schedule:
        """生成每日计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成
            use_multi_round: 是否使用多轮生成（None=从配置读取）
            force_regenerate: 强制重新生成（跳过已有日程检查）

        Returns:
            Schedule对象
        """
        logger.info(f"生成每日计划: user={user_id}, chat={chat_id}")

        # 检查今天是否已有日程（防止重复生成）
        if not force_regenerate:
            today = self.tz_manager.get_now().strftime("%Y-%m-%d")
            existing_schedule = self.goal_manager.get_schedule_goals(chat_id=chat_id, date_str=today)

            if existing_schedule:
                logger.warning(f"今天已有 {len(existing_schedule)} 个日程，跳过重复生成。使用 force_regenerate=True 强制重新生成。")
                # 返回现有日程封装为Schedule对象
                schedule_items = []
                for goal in existing_schedule:
                    # 提取time_window
                    time_window = None
                    if goal.parameters and "time_window" in goal.parameters:
                        time_window = goal.parameters["time_window"]
                    elif goal.conditions and "time_window" in goal.conditions:
                        time_window = goal.conditions["time_window"]

                    # 转换为ScheduleItem
                    duration = None
                    if time_window and len(time_window) == 2:
                        duration = (time_window[1] - time_window[0]) / 60.0  # 分钟转小时

                    time_slot = None
                    if time_window:
                        hours = time_window[0] // 60
                        minutes = time_window[0] % 60
                        time_slot = f"{hours:02d}:{minutes:02d}"

                    schedule_items.append(ScheduleItem(
                        name=goal.name,
                        description=goal.description,
                        goal_type=goal.goal_type,
                        priority=goal.priority,
                        time_slot=time_slot,
                        duration_hours=duration
                    ))

                return Schedule(
                    schedule_type=ScheduleType.DAILY,
                    name=f"每日计划 - {today}",
                    items=schedule_items,
                    metadata={"preferences": preferences, "existing": True}
                )

        # 从配置读取多轮生成设置
        if use_multi_round is None:
            use_multi_round = self.config.use_multi_round

        preferences = preferences or {}

        # 加载昨日日程作为上下文
        self.base_generator.yesterday_schedule_summary = \
            self.base_generator.load_yesterday_schedule_summary()

        # 生成日程项
        if use_multi_round:
            schedule_items = await self._generate_with_multi_round(
                schedule_type=ScheduleType.DAILY,
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences
            )
        else:
            schedule_items = await self._generate_single_round(
                schedule_type=ScheduleType.DAILY,
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences
            )

        # 创建Schedule对象
        schedule = Schedule(
            schedule_type=ScheduleType.DAILY,
            name=f"每日计划 - {self.tz_manager.get_now().strftime('%Y-%m-%d')}",
            items=schedule_items,
            metadata={"preferences": preferences}
        )

        logger.info(f"✅ 每日计划生成完成: {len(schedule_items)}个活动")
        return schedule

    async def generate_weekly_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True,
        use_multi_round: Optional[bool] = None
    ) -> Schedule:
        """生成每周计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成（保留参数兼容性）
            use_multi_round: 是否使用多轮生成（None=从配置读取）

        Returns:
            Schedule对象
        """
        logger.info(f"生成每周计划: user={user_id}, chat={chat_id}")

        # 从配置读取多轮生成设置
        if use_multi_round is None:
            use_multi_round = self.config.use_multi_round

        preferences = preferences or {}

        # 生成日程项
        if use_multi_round:
            schedule_items = await self._generate_with_multi_round(
                schedule_type=ScheduleType.WEEKLY,
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences
            )
        else:
            schedule_items = await self._generate_single_round(
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

        logger.info(f"✅ 每周计划生成完成: {len(schedule_items)}个活动")
        return schedule

    async def generate_monthly_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True,
        use_multi_round: Optional[bool] = None
    ) -> Schedule:
        """生成每月计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成（保留参数兼容性）
            use_multi_round: 是否使用多轮生成（None=从配置读取）

        Returns:
            Schedule对象
        """
        logger.info(f"生成每月计划: user={user_id}, chat={chat_id}")

        # 从配置读取多轮生成设置
        if use_multi_round is None:
            use_multi_round = self.config.use_multi_round

        preferences = preferences or {}

        # 生成日程项
        if use_multi_round:
            schedule_items = await self._generate_with_multi_round(
                schedule_type=ScheduleType.MONTHLY,
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences
            )
        else:
            schedule_items = await self._generate_single_round(
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

        logger.info(f"✅ 每月计划生成完成: {len(schedule_items)}个活动")
        return schedule

    async def apply_schedule(
        self,
        schedule: Schedule,
        user_id: str,
        chat_id: str,
        auto_start: bool = True
    ) -> List[str]:
        """应用日程，将日程项转换为目标

        Args:
            schedule: 日程对象
            user_id: 用户ID
            chat_id: 聊天ID
            auto_start: 是否自动启动

        Returns:
            创建的目标ID列表
        """
        logger.info(f"应用日程: {schedule.name}")

        goals_data = []

        for item in schedule.items:
            try:
                # 设置时间窗口
                parameters = item.parameters.copy() if item.parameters else {}

                # 从time_slot解析时间窗口
                if item.time_slot:
                    time_parts = item.time_slot.split(":")
                    hour = int(time_parts[0])
                    minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                    start_minutes = hour * 60 + minute

                    # 使用duration_hours计算结束时间
                    if item.duration_hours:
                        duration_minutes = int(item.duration_hours * 60)
                        end_minutes = start_minutes + duration_minutes
                    else:
                        end_minutes = start_minutes + 60  # 默认1小时

                    # 避免跨午夜
                    if end_minutes > 24 * 60:
                        end_minutes = 24 * 60

                    parameters["time_window"] = [start_minutes, end_minutes]

                # 准备目标数据
                goals_data.append({
                    "name": item.name,
                    "description": item.description,
                    "goal_type": item.goal_type,
                    "creator_id": user_id,
                    "chat_id": chat_id,
                    "priority": item.priority,
                    "conditions": {},
                    "parameters": parameters,
                })

            except Exception as e:
                logger.error(f"准备目标数据失败: {item.name} - {e}", exc_info=True)

        # 批量创建目标
        if goals_data:
            created_goals = self.goal_manager.create_goals_batch(goals_data)
            created_goal_ids = [g.goal_id for g in created_goals]
            logger.info(f"✅ 批量创建了 {len(created_goal_ids)} 个目标")
            return created_goal_ids
        else:
            logger.warning("没有有效的日程项可以应用")
            return []

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

    # ========================================================================
    # 内部方法（生成逻辑）
    # ========================================================================

    async def _generate_with_multi_round(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any]
    ) -> List[ScheduleItem]:
        """多轮生成：如果第一次质量不佳，使用反馈改进"""
        max_rounds = self.config.max_rounds
        quality_threshold = self.config.quality_threshold

        best_schedule = None
        best_score = 0
        validation_warnings = []

        for round_num in range(1, max_rounds + 1):
            logger.info(f"🔄 第{round_num}轮生成...")

            try:
                # 构建Prompt
                schema = self.base_generator.build_json_schema()

                if round_num == 1:
                    prompt = self.base_generator.build_schedule_prompt(
                        schedule_type, preferences, schema
                    )
                else:
                    # 第二轮：附带第一轮的问题
                    prompt = self.base_generator.build_retry_prompt(
                        schedule_type, preferences, schema, validation_warnings
                    )

                # 调用LLM
                raw_items = await self._call_llm(prompt)

                # 验证和评分
                validated_items, warnings = self.validator.validate(raw_items)
                score = self.quality_scorer.calculate_score(validated_items, warnings)

                logger.info(f"📊 第{round_num}轮质量分数: {score:.2f}")

                # 更新最佳结果
                if score > best_score:
                    best_schedule = validated_items
                    best_score = score
                    validation_warnings = warnings

                # 如果分数足够高，提前结束
                if score >= quality_threshold:
                    logger.info(f"✅ 质量达标，结束生成")
                    break

            except Exception as e:
                logger.warning(f"第{round_num}轮生成失败: {e}")
                continue

        if best_schedule is None:
            raise ScheduleGenerationError(
                f"多轮生成全部失败（尝试了{max_rounds}轮）",
                attempt_count=max_rounds
            )

        # 转换为ScheduleItem对象
        schedule_items = self._dict_to_schedule_items(best_schedule)

        logger.info(f"✅ 生成 {len(schedule_items)} 个日程项（质量: {best_score:.2f}）")
        return schedule_items

    async def _generate_single_round(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any]
    ) -> List[ScheduleItem]:
        """单轮生成"""
        logger.info("使用单轮生成模式")

        # 构建Prompt
        schema = self.base_generator.build_json_schema()
        prompt = self.base_generator.build_schedule_prompt(
            schedule_type, preferences, schema
        )

        # 调用LLM
        raw_items = await self._call_llm(prompt)

        # 验证
        validated_items, warnings = self.validator.validate(raw_items)

        if warnings:
            logger.warning(f"语义验证发现 {len(warnings)} 个问题")
            for warning in warnings[:3]:
                logger.warning(f"  ⚠️ {warning}")

        # 转换为ScheduleItem对象
        schedule_items = self._dict_to_schedule_items(validated_items)

        logger.info(f"✅ 生成 {len(schedule_items)} 个日程项")
        return schedule_items

    async def _call_llm(self, prompt: str) -> List[Dict[str, Any]]:
        """调用LLM并解析响应

        Args:
            prompt: 提示词

        Returns:
            日程项列表

        Raises:
            LLMError: LLM调用失败
        """
        # 获取模型配置
        model_config, max_tokens, temperature = self.base_generator.get_model_config()

        # 调用LLM
        success, response, reasoning, model_name = await llm_api.generate_with_model(
            prompt,
            model_config=model_config,
            request_type="plugin.autonomous_planning.schedule_gen",
            max_tokens=max_tokens,
            temperature=temperature
        )

        if not success:
            # 智能识别错误类型
            error_msg = str(response).lower()

            if any(kw in error_msg for kw in ["quota", "exceeded", "limit", "余额"]):
                raise LLMQuotaExceededError(f"LLM配额超限: {response}")

            if any(kw in error_msg for kw in ["rate limit", "too many", "频率"]):
                raise LLMRateLimitError(f"LLM速率限制: {response}", retry_after_seconds=10)

            if any(kw in error_msg for kw in ["timeout", "timed out", "超时"]):
                raise LLMTimeoutError(f"LLM调用超时: {response}", timeout_seconds=30)

            raise LLMError(f"LLM调用失败: {response}")

        # 🆕 使用ResponseParser解析（消除重复代码）
        items = self.response_parser.parse_schedule_response(response)

        return items

    def _dict_to_schedule_items(self, items_dict: List[Dict]) -> List[ScheduleItem]:
        """将字典列表转换为ScheduleItem对象列表"""
        schedule_items = []

        for item_data in items_dict:
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

        return schedule_items
