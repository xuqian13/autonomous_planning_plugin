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

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
import asyncio
import logging

from ..core.exceptions import (
    LLMError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    LLMTimeoutError,
    ScheduleGenerationError,
)
from ..core.models import Schedule, ScheduleItem, ScheduleType
from ..utils.llm_logger import log_llm_call
from ..utils.timezone_manager import TimezoneManager
from .generator import (
    BaseScheduleGenerator,
    LLMResponseParser,
    ScheduleGeneratorConfig,
    ScheduleQualityScorer,
    ScheduleSemanticValidator,
)
from .generator.continuity_state import find_continuity_violations
from .goal_manager import GoalManager

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        goal_manager: GoalManager,
        config: Optional[Dict[str, Any]] = None,
        plugin: Optional[Any] = None,
    ):
        """初始化日程生成器

        Args:
            goal_manager: 目标管理器
            config: 配置字典
            plugin: 插件实例引用（用于通过 ctx.llm 调用 LLM；v4 必需）
        """
        self.goal_manager = goal_manager
        self._plugin = plugin  # v4 新增：用于访问 ctx.llm.generate

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

        logger.debug(f"ScheduleGenerator初始化完成: {self.config}")

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
        logger.debug(f"生成每日计划: user={user_id}, chat={chat_id}")

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

                    # 🔧 修复：如果priority是枚举对象，转换为字符串
                    priority_str = goal.priority.value if hasattr(goal.priority, 'value') else goal.priority

                    schedule_items.append(ScheduleItem(
                        name=goal.name,
                        description=goal.description,
                        goal_type=goal.goal_type,
                        priority=priority_str,
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

        # 加载最近 N 天日程作为上下文（防止交替式重复）
        recent_days = int(self.config._raw_config.get("recent_schedule_days", 3))
        self.base_generator.yesterday_schedule_summary = \
            self.base_generator.context_loader.load_recent_schedule_summary(days=recent_days)

        # 加载今日 pending_commitments 作为上下文
        today_str = self.tz_manager.get_now().strftime("%Y-%m-%d")
        pending = self.goal_manager.get_pending_commitments(today_str)
        self.base_generator.pending_commitments = [
            {
                "time": (p.parameters or {}).get("time", ""),
                "title": p.name,
                "notes": (p.parameters or {}).get("notes", ""),
            }
            for p in pending
        ] or None
        if pending:
            logger.info(f"📌 今日有 {len(pending)} 条未来约定将被纳入日程生成")

        # 加载跨群历史 + 知识库（D 阶段；失败静默不阻塞生成）
        raw_cfg = self.config._raw_config if hasattr(self.config, "_raw_config") else {}
        allowed = raw_cfg.get("allowed_streams", []) or []
        history_limit = int(raw_cfg.get("history_message_limit", 0) or 0)
        knowledge_limit = int(raw_cfg.get("knowledge_search_limit", 0) or 0)
        try:
            self.base_generator.history_context = (
                await self.base_generator.context_loader.load_recent_history_across_streams(
                    self._plugin, allowed, history_limit,
                )
            )
        except Exception as exc:
            logger.debug(f"跨群历史加载失败: {exc}")
            self.base_generator.history_context = ""
        try:
            self.base_generator.knowledge_context = (
                await self.base_generator.context_loader.load_relevant_knowledge(
                    self._plugin, query_hint=today_str, limit=knowledge_limit,
                )
            )
        except Exception as exc:
            logger.debug(f"知识库检索失败: {exc}")
            self.base_generator.knowledge_context = ""

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
            metadata={"preferences": preferences, "schedule_date": today_str}
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
        logger.debug(f"生成每周计划: user={user_id}, chat={chat_id}")

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
        today = self.tz_manager.get_now()
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
        logger.debug(f"生成每月计划: user={user_id}, chat={chat_id}")

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

        today = self.tz_manager.get_now()
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
        logger.debug(f"应用日程: {schedule.name}")

        goals_data = []

        # v4.4.5：先把"今日 pending_commitments"映射到 schedule.items 上
        # —— LLM 生成日程时不一定显式标记哪个 item 来自约定，这里用"最接近的时间窗口"
        # 找出每条约定对应的 item，给那个 item 注入 ``is_commitment`` / ``commitment_*``
        # 元数据，落库后 ProactiveService 才能识别出"这是约定，要用强指令叫 LLM 发送"。
        commitment_metadata_by_item_idx: Dict[int, Dict[str, Any]] = {}
        if schedule.schedule_type == ScheduleType.DAILY:
            try:
                today_str = self.tz_manager.get_now().strftime("%Y-%m-%d")
                commitment_metadata_by_item_idx = self._match_commitments_to_items(
                    schedule.items, today_str,
                )
            except Exception as exc:
                logger.warning(f"约定→日程项 匹配失败（不影响落库）: {exc}", exc_info=True)

        for idx, item in enumerate(schedule.items):
            try:
                # 设置时间窗口
                parameters = item.parameters.copy() if item.parameters else {}

                # v4.4.6：显式记录日程所属日期。旧逻辑只按 created_at 判断日期，
                # 一旦调度/测试在目标日前后创建记录，跨日上下文就会读错。
                schedule_date = ""
                if schedule.metadata:
                    schedule_date = str(schedule.metadata.get("schedule_date") or "").strip()
                if not schedule_date:
                    schedule_date = self.tz_manager.get_now().strftime("%Y-%m-%d")
                parameters["schedule_date"] = schedule_date

                # v4.4.5：把匹配到的 commitment 元数据注入 parameters
                commitment_meta = commitment_metadata_by_item_idx.get(idx)
                if commitment_meta:
                    parameters.update(commitment_meta)

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

                    # 跨天活动支持：开关开启时保留 end_minutes > 1440（数据层的跨夜分支会处理）
                    cross_day_enabled = bool(self.config._raw_config.get("cross_day_activity", True))
                    if not cross_day_enabled and end_minutes > 24 * 60:
                        end_minutes = 24 * 60

                    parameters["time_window"] = [start_minutes, end_minutes]

                # 准备目标数据
                # 🔧 修复：如果priority是枚举对象，转换为字符串
                priority_str = item.priority.value if hasattr(item.priority, 'value') else item.priority

                goals_data.append({
                    "name": item.name,
                    "description": item.description,
                    "goal_type": item.goal_type,
                    "creator_id": user_id,
                    "chat_id": chat_id,
                    "priority": priority_str,
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

            # 消费今日 pending_commitments（成功 apply 后才标记）
            if schedule.schedule_type == ScheduleType.DAILY:
                today_str = self.tz_manager.get_now().strftime("%Y-%m-%d")
                try:
                    consumed = self.goal_manager.consume_pending_commitments(today_str)
                    if consumed:
                        logger.info(f"📌 已消费 {len(consumed)} 条未来约定")
                except Exception as exc:
                    logger.warning(f"消费未来约定失败: {exc}", exc_info=True)

            return created_goal_ids
        else:
            logger.warning("没有有效的日程项可以应用")
            return []

    def _match_commitments_to_items(
        self,
        items: List[ScheduleItem],
        today_str: str,
    ) -> Dict[int, Dict[str, Any]]:
        """把今日 pending_commitments 一对一匹配到 schedule items。

        LLM 生成日程 JSON 时**不会显式标记**哪个 item 来自约定（prompt 里只是
        要求"把上述约定安排到合适时间段"），落库后无法直接区分。本方法在
        :meth:`apply_schedule` 落库前，按以下规则把约定与 item 建立映射：

        1. 优先按时间匹配：约定 ``time="HH:MM"`` 与 item ``time_slot`` 差 ≤ 30 分钟
        2. 若约定无时间，按标题子串匹配（约定 title 出现在 item.name/description 中）
        3. 一对一约束：每个约定只匹配一个 item，每个 item 最多接收一个约定
           （首次匹配优先；同时段多个约定时按 commitment 加入顺序）

        Args:
            items: LLM 生成的当日日程项列表（未落库）
            today_str: ``YYYY-MM-DD``，用于查询当日 pending_commitments

        Returns:
            ``{item_index: {"is_commitment": True, "commitment_title": ..., ...}}``
            映射，调用方把对应 dict ``update`` 到 item 的 parameters 中即可。
        """
        pending = self.goal_manager.get_pending_commitments(today_str)
        if not pending:
            return {}

        # 解析 item.time_slot 为分钟数（与 parse_time_window 一致的格式）
        def _item_start_minutes(it: ScheduleItem) -> Optional[int]:
            if not it.time_slot:
                return None
            try:
                parts = it.time_slot.split(":")
                return int(parts[0]) * 60 + (int(parts[1]) if len(parts) > 1 else 0)
            except (ValueError, IndexError):
                return None

        result: Dict[int, Dict[str, Any]] = {}
        used_item_idx: set[int] = set()

        for commit in pending:
            params = commit.parameters or {}
            commit_time = str(params.get("time") or "").strip()
            commit_title = commit.name.strip()
            commit_notes = str(params.get("notes") or "").strip()
            commit_reason = str(params.get("reason") or "").strip()

            best_idx: Optional[int] = None

            # 规则 1：按时间匹配（≤ 30 分钟）
            if commit_time and ":" in commit_time:
                try:
                    th, tm = commit_time.split(":", 1)
                    commit_minutes = int(th) * 60 + int(tm)
                except ValueError:
                    commit_minutes = None
                if commit_minutes is not None:
                    best_diff = 31
                    for idx, it in enumerate(items):
                        if idx in used_item_idx:
                            continue
                        item_minutes = _item_start_minutes(it)
                        if item_minutes is None:
                            continue
                        diff = abs(item_minutes - commit_minutes)
                        if diff < best_diff:
                            best_diff = diff
                            best_idx = idx

            # 规则 2：标题子串匹配（兜底；约定无 time 或时间匹配未命中时）
            if best_idx is None and commit_title:
                for idx, it in enumerate(items):
                    if idx in used_item_idx:
                        continue
                    haystack = f"{it.name or ''}{it.description or ''}"
                    if commit_title in haystack:
                        best_idx = idx
                        break

            if best_idx is None:
                logger.warning(
                    "约定 %r 未在日程项中找到对应活动（time=%s）",
                    commit_title, commit_time or "未指定",
                )
                continue

            result[best_idx] = {
                "is_commitment": True,
                "commitment_title": commit_title,
                "commitment_notes": commit_notes,
                "commitment_reason": commit_reason,
                "commitment_time": commit_time,
            }
            used_item_idx.add(best_idx)
            logger.info(
                "🔗 约定 %r → 日程项[%d] %r（time_slot=%s）",
                commit_title, best_idx, items[best_idx].name, items[best_idx].time_slot,
            )

        return result

    def get_schedule_summary(self, schedule: Schedule) -> str:
        """获取日程摘要（简洁版 - 显示时间范围）"""
        lines = [f"📅 {schedule.name}"]

        for item in schedule.items:
            if item.time_slot:
                # 计算结束时间
                time_parts = item.time_slot.split(":")
                start_hour = int(time_parts[0])
                start_minute = int(time_parts[1]) if len(time_parts) > 1 else 0

                # 使用 duration_hours 计算结束时间
                if item.duration_hours:
                    total_minutes = start_hour * 60 + start_minute + int(item.duration_hours * 60)
                    end_hour = total_minutes // 60
                    end_minute = total_minutes % 60
                    time_range = f"{start_hour:02d}:{start_minute:02d}-{end_hour:02d}:{end_minute:02d}"
                else:
                    time_range = item.time_slot

                lines.append(f"{time_range} {item.name}")
            else:
                lines.append(item.name)

        return "\n".join(lines)

    async def regenerate_today_schedule(
        self,
        user_id: str,
        chat_id: str,
        *,
        extra_prompt: str = "",
        auto_apply: bool = True,
    ) -> Schedule:
        """重生成今日日程：先删除今天已有 schedule_goals，再走标准生成 + apply。

        Args:
            user_id: 用户 ID。
            chat_id: 聊天 ID（保持与 generate_daily_schedule 一致）。
            extra_prompt: 临时追加到 custom_prompt 的描述（如角色裁判通过的请求）。
            auto_apply: 是否自动 apply 到 goals 表；False 时仅返回 Schedule。

        Returns:
            新生成的 Schedule 对象。
        """
        today_str = self.tz_manager.get_now().strftime("%Y-%m-%d")

        # 临时叠加 extra_prompt 到 custom_prompt
        original_custom = self.config._raw_config.get("custom_prompt", "")
        if extra_prompt:
            merged = (original_custom + "\n\n" + extra_prompt).strip() if original_custom else extra_prompt
            self.config._raw_config["custom_prompt"] = merged
            self.base_generator.config["custom_prompt"] = merged
            self.base_generator.prompt_builder.config["custom_prompt"] = merged

        try:
            # 先生成新日程，成功后才删旧的——防止生成失败导致今天变空。
            schedule = await self.generate_daily_schedule(
                user_id=user_id,
                chat_id=chat_id,
                use_llm=True,
                force_regenerate=True,
            )

            if auto_apply:
                # 删旧 → 落新，保证原子感（删旧失败不影响落新）
                existing = self.goal_manager.get_schedule_goals(chat_id=chat_id, date_str=today_str)
                for goal in existing:
                    try:
                        self.goal_manager.delete_goal(goal.goal_id)
                    except Exception as exc:
                        logger.warning(f"删除旧日程失败: {goal.goal_id} - {exc}")
                if existing:
                    logger.info(f"🧹 已清理 {len(existing)} 个今日旧日程，应用新日程")

                await self.apply_schedule(schedule=schedule, user_id=user_id, chat_id=chat_id)
            return schedule
        finally:
            # 还原 custom_prompt
            self.config._raw_config["custom_prompt"] = original_custom
            self.base_generator.config["custom_prompt"] = original_custom
            self.base_generator.prompt_builder.config["custom_prompt"] = original_custom

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
        best_blocking_warnings: List[str] = []

        for round_num in range(1, max_rounds + 1):
            logger.debug(f"🔄 第{round_num}轮生成...")

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
                warnings.extend(self._validate_continuity_against_context(validated_items))
                score = self.quality_scorer.calculate_score(validated_items, warnings)
                blocking_warnings = self._extract_blocking_warnings(warnings)

                logger.debug(f"📊 第{round_num}轮质量分数: {score:.2f}")

                # 更新最佳结果
                should_replace_best = (
                    best_schedule is None
                    or (best_blocking_warnings and not blocking_warnings)
                    or (bool(best_blocking_warnings) == bool(blocking_warnings) and score > best_score)
                )
                if should_replace_best:
                    best_schedule = validated_items
                    best_score = score
                    validation_warnings = warnings
                    best_blocking_warnings = blocking_warnings

                # 如果分数足够高，提前结束
                if score >= quality_threshold and not blocking_warnings:
                    logger.debug(f"✅ 质量达标，结束生成")
                    break

            except (LLMQuotaExceededError, LLMRateLimitError) as e:
                # 致命 LLM 错误（配额耗尽 / 速率限制）不应通过多轮重试解决，
                # 立即向上抛出，让调用方感知并停止后续浪费配额的尝试。
                logger.error(f"第{round_num}轮遇到致命 LLM 错误，停止多轮生成: {e}")
                raise
            except LLMTimeoutError as e:
                # 超时是网络抖动型错误，可重试
                logger.warning(f"第{round_num}轮 LLM 超时，将重试: {e}")
                continue
            except Exception as e:
                # 业务可重试错误（响应解析失败 / Schema 校验失败等）
                logger.warning(f"第{round_num}轮生成失败: {e}")
                continue

        if best_schedule is None:
            raise ScheduleGenerationError(
                f"多轮生成全部失败（尝试了{max_rounds}轮）",
                attempt_count=max_rounds
            )

        if best_blocking_warnings:
            raise ScheduleGenerationError(
                "多轮生成未通过连续性硬约束，拒绝应用会丢失近期主线的日程："
                + "；".join(best_blocking_warnings[:3]),
                attempt_count=max_rounds,
            )

        # 转换为ScheduleItem对象
        schedule_items = self._dict_to_schedule_items(best_schedule)

        logger.debug(f"✅ 生成 {len(schedule_items)} 个日程项（质量: {best_score:.2f}）")
        return schedule_items

    async def _generate_single_round(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any]
    ) -> List[ScheduleItem]:
        """单轮生成"""
        logger.debug("使用单轮生成模式")

        # 构建Prompt
        schema = self.base_generator.build_json_schema()
        prompt = self.base_generator.build_schedule_prompt(
            schedule_type, preferences, schema
        )

        # 调用LLM
        raw_items = await self._call_llm(prompt)

        # 验证
        validated_items, warnings = self.validator.validate(raw_items)
        warnings.extend(self._validate_continuity_against_context(validated_items))
        blocking_warnings = self._extract_blocking_warnings(warnings)

        if warnings:
            logger.warning(f"语义验证发现 {len(warnings)} 个问题")
            for warning in warnings[:3]:
                logger.warning(f"  ⚠️ {warning}")

        if blocking_warnings:
            raise ScheduleGenerationError(
                "日程生成未通过连续性硬约束，拒绝应用会丢失近期主线的日程："
                + "；".join(blocking_warnings[:3]),
                attempt_count=1,
            )

        # 转换为ScheduleItem对象
        schedule_items = self._dict_to_schedule_items(validated_items)

        logger.info(f"✅ 生成 {len(schedule_items)} 个日程项")
        return schedule_items

    def _validate_continuity_against_context(self, items: List[Dict[str, Any]]) -> List[str]:
        """Validate generated items against recent schedule continuity state."""
        raw_cfg = self.config._raw_config if hasattr(self.config, "_raw_config") else {}
        if not bool(raw_cfg.get("continuity_validation_enabled", True)):
            return []
        recent_summary = self.base_generator.yesterday_schedule_summary or ""
        if not recent_summary:
            return []
        return find_continuity_violations(items, recent_summary)

    @staticmethod
    def _extract_blocking_warnings(warnings: List[str]) -> List[str]:
        """Continuity warnings are hard constraints, not cosmetic quality issues."""
        return [warning for warning in warnings if "连续性硬约束" in warning]

    async def _call_llm(self, prompt: str) -> List[Dict[str, Any]]:
        """调用 LLM 并解析响应（v4：通过 ctx.llm.generate）

        Args:
            prompt: 提示词

        Returns:
            日程项列表

        Raises:
            LLMError: LLM 调用失败
        """
        # 获取任务名 + 参数（v4：task_name 字符串，不再是 model_config 对象）
        task_name, max_tokens, temperature = self.base_generator.get_model_config()

        # v4：必须通过插件 ctx.llm 调用，未注入 plugin 视为编程错误
        if self._plugin is None:
            raise LLMError("ScheduleGenerator 未注入 plugin 实例，无法调用 ctx.llm.generate")

        # 把配置的 generation_timeout（秒）转为 RPC timeout_ms，突破默认 30s 限制。
        gen_timeout_sec = float(getattr(self.config, 'generation_timeout', 180.0) or 180.0)
        rpc_timeout_ms = max(60_000, int(gen_timeout_sec * 1000))

        llm_result = await self._plugin.ctx.llm.generate(
            prompt=prompt,
            model=task_name,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_ms=rpc_timeout_ms,
        )
        success = bool(llm_result.get("success", False))
        response = str(llm_result.get("response", ""))

        # 归档 LLM 调用（受配置开关控制；失败静默）
        self._archive_llm_call("schedule_generation", prompt, response, task_name, success)

        if not success:
            # 智能识别错误类型
            error_msg = str(response).lower()

            if any(kw in error_msg for kw in ["quota", "exceeded", "limit", "余额"]):
                raise LLMQuotaExceededError(f"LLM配额超限: {response}")

            if any(kw in error_msg for kw in ["rate limit", "too many", "频率"]):
                raise LLMRateLimitError(f"LLM速率限制: {response}", retry_after_seconds=10)

            if any(kw in error_msg for kw in ["timeout", "timed out", "超时"]):
                raise LLMTimeoutError(f"LLM调用超时: {response}", timeout_seconds=int(gen_timeout_sec))

            raise LLMError(f"LLM调用失败: {response}")

        # 🆕 使用ResponseParser解析（消除重复代码）
        items = self.response_parser.parse_schedule_response(response)

        return items

    def _archive_llm_call(
        self,
        call_type: str,
        prompt: str,
        response: str,
        model: str,
        success: bool,
    ) -> None:
        """把一次 LLM 调用归档到 ``data/llm_logs/``。

        受配置 ``llm_log_enabled`` 控制；失败时不影响主流程。
        """
        raw = self.config._raw_config if hasattr(self.config, "_raw_config") else {}
        if not raw.get("llm_log_enabled", True):
            return
        log_dir = raw.get("llm_log_dir")
        if not log_dir:
            # plugin 未注入路径时跳过（测试场景）
            if self._plugin is None or not hasattr(self._plugin, "_plugin_root"):
                return
            log_dir = self._plugin._plugin_root / "data" / "llm_logs"
        from pathlib import Path
        log_llm_call(call_type, prompt, response, model, success, Path(log_dir))

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
