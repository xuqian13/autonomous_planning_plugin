"""自主规划插件 - 事件处理器模块"""

import asyncio
import time
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

from src.plugin_system import BaseEventHandler, EventType, MaiMessages, CustomEventHandlerResult
from src.common.logger import get_logger

from ..planner.goal_manager import get_goal_manager
from ..planner.schedule_generator import ScheduleGenerator
from ..cache import LRUCache
from ..utils.time_utils import parse_time_window

logger = get_logger("autonomous_planning.handlers")

class AutonomousPlannerEventHandler(BaseEventHandler):
    """自主规划事件处理器 - 定期清理过期目标"""

    event_type = EventType.ON_START
    handler_name = "autonomous_planner"
    handler_description = "定期清理过期的日程目标"
    weight = 10
    intercept_message = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.goal_manager = get_goal_manager()
        self.check_task: Optional[asyncio.Task] = None
        self.is_running = False
        self.enabled = self.get_config("plugin.enabled", True)
        self.cleanup_interval = self.get_config("autonomous_planning.cleanup_interval", 3600)  # 每小时清理一次
        logger.info(f"自主规划维护任务初始化完成 (清理间隔: {self.cleanup_interval}秒)")

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """处理启动事件，启动后台清理循环"""
        if not self.enabled:
            return True, True, None, None, None

        if not self.is_running:
            self.is_running = True
            self.check_task = asyncio.create_task(self._cleanup_loop())
            logger.info("目标清理循环已启动")

        return True, True, None, None, None

    async def _cleanup_loop(self):
        """定期清理过期目标"""
        logger.info("🧹 麦麦目标清理系统启动")

        while self.is_running:
            try:
                await self._cleanup_old_goals()
            except Exception as e:
                logger.error(f"清理目标异常: {e}", exc_info=True)

            # 等待下一个清理周期（使用短间隔检查，支持快速退出）
            for _ in range(int(self.cleanup_interval)):
                if not self.is_running:
                    break
                await asyncio.sleep(1)

        logger.info("🛑 目标清理循环已停止")

    async def shutdown(self):
        """
        优雅停止清理循环

        调用此方法停止后台清理任务
        """
        if self.is_running:
            logger.info("正在停止目标清理循环...")
            self.is_running = False

            # 等待任务结束（最多3秒）
            if self.check_task:
                try:
                    await asyncio.wait_for(self.check_task, timeout=3.0)
                except asyncio.TimeoutError:
                    logger.warning("清理任务停止超时，强制取消")
                    self.check_task.cancel()
                except Exception as e:
                    logger.error(f"停止清理任务异常: {e}")

            logger.info("✅ 目标清理循环已停止")

    async def _cleanup_old_goals(self):
        """清理旧目标和过期日程"""
        try:
            # 1. 清理过期的日程（昨天及更早的ACTIVE日程）
            expired_schedules = self.goal_manager.cleanup_expired_schedules()
            if expired_schedules > 0:
                logger.info(f"🧹 清理了 {expired_schedules} 个过期日程（昨天及更早）")

            # 2. 清理已完成/已取消的旧目标（保留30天）
            cleanup_days = self.get_config("autonomous_planning.cleanup_old_goals_days", 30)
            cleaned_count = self.goal_manager.cleanup_old_goals(days=cleanup_days)
            if cleaned_count > 0:
                logger.info(f"🧹 清理了 {cleaned_count} 个旧目标（{cleanup_days}天前）")
        except Exception as e:
            logger.error(f"清理旧目标失败: {e}", exc_info=True)


class ScheduleInjectEventHandler(BaseEventHandler):
    """日程注入事件处理器 - 在LLM调用前注入当前日程信息到prompt"""

    event_type = EventType.POST_LLM  # POST_LLM = 在规划器后、LLM调用前执行
    handler_name = "schedule_inject_handler"
    handler_description = "在LLM调用前注入当前日程信息到prompt"
    weight = 10
    intercept_message = True

    # 时间相关关键词（用于智能判断是否需要注入日程）
    TIME_KEYWORDS = {
        "现在", "当前", "正在", "在做", "在干",
        "今天", "今日", "今早", "今晚",
        "明天", "昨天", "后天", "前天",
        "几点", "什么时候", "多久", "时间",
        "安排", "计划", "日程", "行程",
        "接下来", "等下", "稍后", "之后",
        "早上", "中午", "下午", "晚上", "夜里",
        "忙", "空闲", "有空", "在忙",
        "做什么", "干什么", "要做",
    }

    # P1优化：预编译正则表达式，一次匹配所有关键词
    _TIME_KEYWORDS_PATTERN = __import__('re').compile('|'.join(TIME_KEYWORDS))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enabled = self.get_config("plugin.enabled", True)
        self.inject_schedule = self.get_config("autonomous_planning.schedule.inject_schedule", True)
        self.auto_generate_schedule = self.get_config("autonomous_planning.schedule.auto_generate", True)

        # P2优化：从配置读取缓存参数
        cache_max_size = self.get_config("autonomous_planning.schedule.cache_max_size", 100)
        self._schedule_cache = LRUCache(max_size=cache_max_size)

        # 缓存配置
        self._schedule_cache_ttl = self.get_config("autonomous_planning.schedule.cache_ttl", 300)
        self._cache_cleanup_interval = 600  # 10分钟清理一次
        self._last_cache_cleanup = 0  # 上次清理时间

        # 日程生成锁（防止并发生成）
        self._generate_lock = asyncio.Lock()
        self._last_schedule_check_date = None

        if self.enabled and self.inject_schedule:
            logger.info(f"日程注入功能已启用（缓存TTL: {self._schedule_cache_ttl}秒，最大{cache_max_size}项）")
            if self.auto_generate_schedule:
                logger.info("日程自动生成功能已启用")
            asyncio.create_task(self._preheat_cache())  # 启动缓存预热

    async def _preheat_cache(self):
        """预热缓存 - 启动时提前加载全局日程"""
        try:
            await asyncio.sleep(5)  # 等待系统初始化
            logger.info("🔥 开始预热日程缓存...")
            self._get_current_schedule("global")
            logger.info("✅ 日程缓存预热完成")
        except Exception as e:
            logger.warning(f"缓存预热失败: {e}")

    def _check_today_schedule_exists(self, chat_id: str = "global") -> bool:
        """
        检查今天是否已经有日程

        Args:
            chat_id: 聊天ID，默认为"global"

        Returns:
            True表示今天已有日程，False表示没有
        """
        try:
            goal_manager = get_goal_manager()
            goals = goal_manager.get_active_goals(chat_id=chat_id)

            if not goals:
                return False

            # 获取今天的日期字符串
            today_str = datetime.now().strftime("%Y-%m-%d")

            # 检查是否有今天创建的带time_window的目标
            for goal in goals:
                # 检查是否有time_window（日程类型的标志）
                has_time_window = False
                if goal.parameters and "time_window" in goal.parameters:
                    has_time_window = True
                elif goal.conditions and "time_window" in goal.conditions:
                    has_time_window = True

                if has_time_window:
                    # 检查创建时间是否是今天
                    goal_date = None
                    if goal.created_at:
                        try:
                            if isinstance(goal.created_at, str):
                                goal_date = goal.created_at.split("T")[0]
                            else:
                                goal_date = goal.created_at.strftime("%Y-%m-%d")
                        except Exception as e:
                            logger.debug(f"解析目标创建时间失败: {goal.created_at} - {e}")

                    if goal_date == today_str:
                        logger.debug(f"找到今天的日程目标: {goal.name}")
                        return True

            logger.debug("今天还没有日程")
            return False

        except Exception as e:
            logger.warning(f"检查今天日程失败: {e}")
            return False

    async def _auto_generate_today_schedule(self, user_id: str, chat_id: str = "global") -> bool:
        """
        自动生成今天的日程

        注意：此方法假设调用者已持有 _generate_lock，不会再次获取锁

        Args:
            user_id: 用户ID
            chat_id: 聊天ID，默认为"global"

        Returns:
            True表示生成成功，False表示失败
        """
        try:
            logger.info("🔄 开始自动生成今天的日程...")

            goal_manager = get_goal_manager()

            # 读取配置并传给ScheduleGenerator
            schedule_config = {
                "use_multi_round": self.get_config("autonomous_planning.schedule.use_multi_round", False),
                "max_rounds": self.get_config("autonomous_planning.schedule.max_rounds", 1),
                "quality_threshold": self.get_config("autonomous_planning.schedule.quality_threshold", 0.80),
                "min_activities": self.get_config("autonomous_planning.schedule.min_activities", 8),
                "max_activities": self.get_config("autonomous_planning.schedule.max_activities", 15),
                "min_description_length": self.get_config("autonomous_planning.schedule.min_description_length", 15),
                "max_description_length": self.get_config("autonomous_planning.schedule.max_description_length", 50),
                "max_tokens": self.get_config("autonomous_planning.schedule.max_tokens", 8192),
                "custom_prompt": self.get_config("autonomous_planning.schedule.custom_prompt", ""),
                "custom_model": {
                    "enabled": self.get_config("autonomous_planning.schedule.custom_model.enabled", False),
                    "model_name": self.get_config("autonomous_planning.schedule.custom_model.model_name", ""),
                    "api_base": self.get_config("autonomous_planning.schedule.custom_model.api_base", ""),
                    "api_key": self.get_config("autonomous_planning.schedule.custom_model.api_key", ""),
                    "provider": self.get_config("autonomous_planning.schedule.custom_model.provider", "openai"),
                    "temperature": self.get_config("autonomous_planning.schedule.custom_model.temperature", 0.7),
                },
            }
            schedule_generator = ScheduleGenerator(goal_manager, config=schedule_config)

            # 生成每日日程
            schedule = await schedule_generator.generate_daily_schedule(
                user_id=user_id,
                chat_id=chat_id,
                use_llm=True
            )

            # 应用日程
            created_ids = await schedule_generator.apply_schedule(
                schedule=schedule,
                user_id=user_id,
                chat_id=chat_id
            )

            if created_ids:
                logger.info(f"✅ 自动生成日程成功，创建了 {len(created_ids)} 个目标")
                # 清理缓存，强制重新加载
                self._schedule_cache.clear()
                self._last_schedule_check_date = datetime.now().strftime("%Y-%m-%d")
                return True
            else:
                logger.warning("⚠️ 日程生成失败，没有创建任何目标")
                return False

        except Exception as e:
            logger.error(f"自动生成日程失败: {e}", exc_info=True)
            return False

    def _should_inject_schedule(self, message: MaiMessages) -> bool:
        """
        智能判断是否需要注入日程信息

        判断规则：
        1. 用户消息包含时间相关关键词 → 需要注入
        2. 短消息（<5字）且包含问号 → 可能是询问，需要注入
        3. 其他情况 → 不注入

        Args:
            message: 消息对象

        Returns:
            是否需要注入日程
        """
        try:
            # 获取用户消息文本
            user_message = ""

            # 方式1: 从plain_text提取（MaiMessages标准属性）
            if hasattr(message, 'plain_text') and message.plain_text:
                user_message = str(message.plain_text)
                logger.debug(f"从plain_text提取到用户消息: '{user_message}'")

            # 方式2: 从raw_message提取（备选）
            if not user_message and hasattr(message, 'raw_message') and message.raw_message:
                user_message = str(message.raw_message)
                logger.debug(f"从raw_message提取到用户消息: '{user_message}'")

            if not user_message:
                logger.debug(f"未能提取到用户消息，跳过日程注入")
                return False

            # P1优化：使用预编译正则一次匹配所有关键词
            match = self._TIME_KEYWORDS_PATTERN.search(user_message)
            if match:
                logger.info(f"检测到时间关键词: {match.group()}，将注入日程")
                return True

            # 规则2：短消息 + 问号（可能是询问）
            if len(user_message) < 5 and "?" in user_message:
                logger.info("检测到短消息问句，将注入日程")
                return True

            # 其他情况不注入
            logger.debug("用户消息不涉及时间，跳过日程注入")
            return False

        except Exception as e:
            logger.warning(f"判断是否注入日程失败: {e}")
            # 失败时保守策略：不注入
            return False

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """执行日程注入（智能判断是否需要）"""
        if not self.enabled or not self.inject_schedule or not message or not message.llm_prompt:
            return True, True, None, None, None

        try:
            chat_id = message.stream_id if hasattr(message, 'stream_id') else None
            if not chat_id:
                return True, True, None, None, None

            # 🆕 智能判断：只在用户消息涉及时间时才注入日程
            if not self._should_inject_schedule(message):
                return True, True, None, None, None

            # P0修复：检查今天是否有日程，没有则自动生成（原子化操作）
            if self.auto_generate_schedule:
                today_str = datetime.now().strftime("%Y-%m-%d")

                # 使用锁确保检查+生成的原子性，防止竞态条件
                async with self._generate_lock:
                    # 只在今天还没检查过的情况下检查
                    if self._last_schedule_check_date != today_str:
                        has_schedule = self._check_today_schedule_exists(chat_id="global")

                        if not has_schedule:
                            logger.info("📅 今天还没有日程，准备自动生成...")

                            # 获取用户ID
                            user_id = "system"
                            if hasattr(message, 'message_base_info') and message.message_base_info:
                                user_id = message.message_base_info.get('user_id', 'system')

                            # P0修复：添加超时保护（可配置，默认3分钟）
                            generation_timeout = self.get_config("autonomous_planning.schedule.generation_timeout", 180.0)
                            generation_task = None
                            try:
                                # 🆕 创建任务以便超时时主动取消
                                generation_task = asyncio.create_task(
                                    self._auto_generate_today_schedule(user_id, chat_id="global")
                                )
                                generation_success = await asyncio.wait_for(
                                    generation_task,
                                    timeout=generation_timeout
                                )
                            except asyncio.TimeoutError:
                                logger.error(f"⏰ 日程生成超时（{generation_timeout}秒），跳过本次生成")
                                # 🆕 P0级：超时后主动取消任务，避免后台继续运行
                                if generation_task and not generation_task.done():
                                    generation_task.cancel()
                                    try:
                                        await generation_task
                                    except asyncio.CancelledError:
                                        logger.debug("已取消超时的日程生成任务")
                                generation_success = False
                            except Exception as e:
                                logger.error(f"日程生成异常: {e}", exc_info=True)
                                generation_success = False

                            if generation_success:
                                logger.info("✅ 日程自动生成完成，继续注入")
                            else:
                                logger.warning("⚠️ 日程自动生成失败")
                        else:
                            logger.debug("今天已有日程，跳过自动生成")

                        # 更新检查日期（无论是否生成成功）
                        self._last_schedule_check_date = today_str

            # 获取当前日程
            current_activity, current_description, next_activity, next_time = self._get_current_schedule(chat_id)

            # 构建日程提示
            schedule_prompt = ""
            if current_activity:
                schedule_prompt = f"\n【当前状态】\n这会儿正{current_activity}"
                if current_description:
                    schedule_prompt += f"（{current_description}）"
                schedule_prompt += f"\n回复时可以自然提到当前在做什么，不要刻意强调。"
                if next_activity and next_time:
                    schedule_prompt += f"\n等下{next_time}要{next_activity}。"
                schedule_prompt += "\n"

            # 注入日程信息到prompt
            if schedule_prompt:
                original_prompt = str(message.llm_prompt)
                new_prompt = schedule_prompt + "\n" + original_prompt
                message.modify_llm_prompt(new_prompt, suppress_warning=True)
                logger.info(f"✅ 已注入日程状态: {current_activity}")

            return True, True, None, None, message

        except Exception as e:
            logger.error(f"注入日程信息失败: {e}", exc_info=True)
            return True, True, None, None, None

    def _cleanup_expired_cache(self, current_time: float):
        """清理过期的缓存项（P0修复：线程安全）"""
        # 使用锁保护，防止与并发的get/set操作冲突
        with self._schedule_cache._lock:
            expired_keys = []

            # 使用list()创建快照避免迭代时修改
            for key, (_, cached_time) in list(self._schedule_cache.cache.items()):
                if current_time - cached_time > self._schedule_cache_ttl:
                    expired_keys.append(key)

            for key in expired_keys:
                if key in self._schedule_cache.cache:
                    del self._schedule_cache.cache[key]

            if expired_keys:
                logger.debug(f"清理了 {len(expired_keys)} 个过期缓存项")

    def _get_current_schedule(self, chat_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        获取当前日程信息（带优化缓存）

        优化：
        1. 缓存TTL从30秒提升到5分钟
        2. 缓存键改为按小时（而非5分钟窗口），提高命中率
        3. 定期清理过期缓存，避免内存泄漏

        Returns:
            (当前活动, 活动描述, 下一个活动, 下一个活动时间)
        """
        import time

        # 获取当前时间
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        current_time = time.time()

        # P1修复：按15分钟窗口缓存（而非按小时），提高精度同时保持命中率
        # 原因：同一小时内活动可能变化，但15分钟内基本稳定
        time_window = (current_hour * 60 + current_minute) // 15
        cache_key = f"{chat_id or 'global'}_{now.strftime('%Y%m%d')}_{time_window}"

        # 定期清理过期缓存（避免内存无限增长）
        if current_time - self._last_cache_cleanup > self._cache_cleanup_interval:
            self._cleanup_expired_cache(current_time)
            self._last_cache_cleanup = current_time

        # 检查缓存是否有效
        if cache_key in self._schedule_cache:
            cached_result, cached_time = self._schedule_cache[cache_key]
            if current_time - cached_time < self._schedule_cache_ttl:
                # 缓存命中
                return cached_result

        # 缓存过期或不存在，重新查询
        try:
            goal_manager = get_goal_manager()

            # 先尝试获取全局日程（chat_id="global"）
            goals = goal_manager.get_active_goals(chat_id="global")

            # 如果没有全局日程，再尝试获取当前聊天的日程
            if not goals and chat_id:
                goals = goal_manager.get_active_goals(chat_id=chat_id)

            if not goals:
                result = (None, None, None, None)
                self._schedule_cache[cache_key] = (result, current_time)
                return result

            current_time_minutes = current_hour * 60 + current_minute
            today_date = now.strftime("%Y-%m-%d")

            # 找到有时间窗口的目标，优先选择今天创建的
            scheduled_goals = []
            for goal in goals:
                # 向后兼容：优先从parameters读取time_window，其次从conditions读取
                time_window = None
                if goal.parameters and "time_window" in goal.parameters:
                    time_window = goal.parameters.get("time_window")
                elif goal.conditions:
                    time_window = goal.conditions.get("time_window")

                if time_window:
                    # 检查是否是今天创建的任务
                    # created_at 可能是 datetime 对象或字符串
                    is_today = False
                    if goal.created_at:
                        if isinstance(goal.created_at, str):
                            is_today = goal.created_at.startswith(today_date)
                        else:
                            # datetime 对象
                            is_today = goal.created_at.strftime("%Y-%m-%d") == today_date
                    scheduled_goals.append((goal, time_window, is_today))

            if not scheduled_goals:
                result = (None, None, None, None)
                self._schedule_cache[cache_key] = (result, current_time)
                return result

            # 排序：按开始时间（兼容新旧格式）
            def get_start_minutes(item):
                goal, time_window, is_today = item
                if not time_window or len(time_window) < 2:
                    return 0
                start_val = time_window[0]
                # 判断格式：end_val > 24 说明是分钟格式
                if time_window[1] > 24:
                    return start_val
                else:
                    return start_val * 60

            scheduled_goals.sort(key=get_start_minutes)

            # 查找当前活动（仅选择今天创建的任务）
            current_activity = None
            current_description = None
            current_goal_created_at = None

            for goal, time_window, is_today in scheduled_goals:
                start_minutes, end_minutes = parse_time_window(time_window)
                if start_minutes is None:
                    continue

                # 处理跨夜时间窗口（end_minutes > 1440）
                # 例如 23:00-01:00 会被转换为 [1380, 1500]
                is_in_window = False
                if end_minutes > 1440:
                    # 跨夜任务：检查当前时间是否在开始时间之后，或在（结束时间-1440）之前
                    # 例如：1380 <= 1410 < 1500 或 0 <= 30 < 60
                    is_in_window = (start_minutes <= current_time_minutes < 1440) or (0 <= current_time_minutes < (end_minutes - 1440))
                else:
                    # 普通任务
                    is_in_window = start_minutes <= current_time_minutes < end_minutes

                if is_in_window:
                    # 仅选择今天创建的任务
                    if is_today:
                        # 如果有多个今天的任务，选择创建时间最新的
                        if current_activity is None or (goal.created_at and goal.created_at > current_goal_created_at):
                            current_activity = goal.name
                            current_description = goal.description
                            current_goal_created_at = goal.created_at

            # 查找下一个活动（优先选择今天的任务）
            next_activity = None
            next_time = None
            for goal, time_window, is_today in scheduled_goals:
                start_val = time_window[0] if len(time_window) > 0 else 0
                end_val = time_window[1] if len(time_window) > 1 else start_val + 60

                # 判断格式并转换
                if end_val <= 24:
                    start_minutes = start_val * 60
                else:
                    start_minutes = start_val

                if start_minutes > current_time_minutes:
                    # 优先选择今天的任务
                    if next_activity is None or is_today:
                        next_activity = goal.name
                        # 转换为时:分格式
                        hour = start_minutes // 60
                        minute = start_minutes % 60
                        next_time = f"{hour:02d}:{minute:02d}"
                        if is_today:
                            break  # 找到今天的任务就停止

            result = (current_activity, current_description, next_activity, next_time)
            self._schedule_cache[cache_key] = (result, current_time)
            return result

        except Exception as e:
            logger.debug(f"获取日程信息失败: {e}")
            result = (None, None, None, None)
            self._schedule_cache[cache_key] = (result, current_time)
            return result


# ===== Commands =====

