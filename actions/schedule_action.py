"""
日程管理 Action
统一处理日程的查看和生成
"""

from typing import Tuple
from datetime import datetime

from src.plugin_system import BaseAction, ActionActivationType
from src.common.logger import get_logger

from ..planner.goal_manager import get_goal_manager
from ..planner.schedule_generator import ScheduleGenerator

logger = get_logger("autonomous_planning.schedule_action")


class ScheduleAction(BaseAction):
    """日程管理 Action - 智能处理日程查看和生成"""

    # ===== Action 元信息 =====
    action_name = "schedule"
    action_description = "管理日程：查看今天的日程安排，如果没有则自动生成"

    action_parameters = {
        "time_range": "today(今天)/week(本周)/monthly(本月)，默认 today"
    }

    action_require = [
        "当被问到今天有什么安排、计划、日程时使用",
        "当需要查看今天的日程时使用",
        "当被问'今天做什么''有什么计划'等问题时使用",
        "当被要求制定、生成或规划日程时使用",
        "当早上刚开始活动时（如早上6-9点）适合主动使用",
        "一天只生成一次每日日程，避免重复规划"
    ]

    # ===== 激活配置 =====
    activation_type = ActionActivationType.LLM_JUDGE
    parallel_action = True  # 可以和回复并行

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.goal_manager = get_goal_manager()
        self.schedule_generator = ScheduleGenerator(self.goal_manager)

    async def execute(self) -> Tuple[bool, str]:
        """执行日程管理"""
        try:
            time_range = self.action_data.get("time_range", "today")

            logger.info(f"{self.log_prefix}开始处理日程，范围: {time_range}")

            # 根据时间范围处理
            if time_range in ["today", "今天", "今日", "daily"]:
                success, reply = await self._handle_daily()
            elif time_range in ["week", "本周", "这周", "weekly"]:
                success, reply = await self._handle_weekly()
            elif time_range in ["month", "本月", "这月", "monthly"]:
                success, reply = await self._handle_monthly()
            else:
                # 默认处理今天
                success, reply = await self._handle_daily()

            if success:
                logger.info(f"{self.log_prefix}日程处理成功: {reply}")
            else:
                logger.warning(f"{self.log_prefix}日程处理失败: {reply}")

            return success, reply

        except Exception as e:
            logger.error(f"{self.log_prefix}处理日程失败: {e}", exc_info=True)
            return False, "处理日程时出了点问题..."

    async def _handle_daily(self) -> Tuple[bool, str]:
        """处理每日日程"""
        try:
            # 1. 检查今天是否已有日程
            existing_goals = self._get_today_schedule_goals()
            today_str = datetime.now().strftime("%Y-%m-%d")

            if existing_goals:
                # 已有日程，直接返回
                logger.info(f"{self.log_prefix}今天（{today_str}）已有 {len(existing_goals)} 个日程目标")

                # 根据当前时间智能回复
                now = datetime.now()
                current_hour = now.hour
                current_minute = now.minute
                current_time_minutes = current_hour * 60 + current_minute

                # 找到当前正在进行的活动
                current_goal = None
                for goal in existing_goals:
                    time_window = None
                    if goal.parameters and "time_window" in goal.parameters:
                        time_window = goal.parameters.get("time_window")
                    elif goal.conditions and "time_window" in goal.conditions:
                        time_window = goal.conditions.get("time_window")

                    if time_window and len(time_window) >= 2:
                        # time_window 可能是旧格式 [hour, hour] 或新格式 [minutes, minutes]
                        start_val, end_val = time_window[0], time_window[1]

                        # 判断格式：旧格式范围是0-23(小时)，新格式是0-1440(分钟)
                        # 使用end_val判断更准确：如果end_val<=24说明是小时，否则是分钟
                        if end_val <= 24:
                            # 旧格式：小时
                            start_minutes = start_val * 60
                            end_minutes = end_val * 60
                        else:
                            # 新格式：分钟
                            start_minutes = start_val
                            end_minutes = end_val

                        if start_minutes <= current_time_minutes < end_minutes:
                            current_goal = goal
                            break

                # 构建回复（更自然的风格）
                goal_names = [g.name for g in existing_goals[:5]]

                if current_goal:
                    # 有当前进行的活动，突出显示
                    if len(existing_goals) <= 3:
                        reply = f"现在{current_goal.name}呢 今天就{', '.join(goal_names)}这些"
                    elif len(existing_goals) <= 5:
                        reply = f"正{current_goal.name} 今天安排了{', '.join(goal_names)}"
                    else:
                        reply = f"正{current_goal.name} 今天一堆事：{', '.join(goal_names)}什么的"
                else:
                    # 没有当前活动，正常列举
                    if len(existing_goals) <= 3:
                        reply = f"今天就{', '.join(goal_names)}这些"
                    elif len(existing_goals) <= 5:
                        reply = f"今天安排了{', '.join(goal_names)}"
                    else:
                        reply = f"今天一堆事要干：{', '.join(goal_names)}什么的"

                # 不再私聊中主动发送详细日程，只返回简短回复给LLM处理
                return True, reply

            # 2. 没有日程，自动生成
            logger.info(f"{self.log_prefix}今天（{today_str}）还没有日程，开始生成...")

            # 获取用户偏好
            preferences = self.plugin_config.get("schedule", {}).get("preferences", {})

            # 使用 LLM 生成个性化日程
            schedule = await self.schedule_generator.generate_daily_schedule(
                user_id=self.user_id if self.user_id else "system",
                chat_id="global",  # 全局日程，所有聊天共享
                preferences=preferences,
                use_llm=True
            )

            # 3. 应用日程（批量创建目标）
            created_ids = await self.schedule_generator.apply_schedule(
                schedule=schedule,
                user_id=self.user_id if self.user_id else "system",
                chat_id="global",
                auto_start=True
            )

            if not created_ids:
                logger.warning(f"{self.log_prefix}日程生成成功，但没有创建任何目标")
                return False, "日程生成失败，没有创建任何计划项"

            # 4. 构建回复（更自然的风格）
            logger.info(f"{self.log_prefix}✅ 成功生成每日日程，创建了 {len(created_ids)} 个目标")

            # 获取前几个目标的名称
            created_goals = [self.goal_manager.get_goal(gid) for gid in created_ids[:5]]
            goal_names = [g.name for g in created_goals if g]

            # 随意的回复，更符合人格
            if len(created_ids) <= 3:
                reply = f"今天就{', '.join(goal_names)}这些吧"
            elif len(created_ids) <= 5:
                reply = f"今天安排了{', '.join(goal_names)}"
            else:
                reply = f"今天一堆事要干：{', '.join(goal_names)}什么的"

            # 不再私聊中主动发送详细日程，只返回简短回复给LLM处理
            return True, reply

        except Exception as e:
            logger.error(f"{self.log_prefix}处理每日日程失败: {e}", exc_info=True)
            return False, f"处理今日日程时出错了: {str(e)}"

    async def _handle_weekly(self) -> Tuple[bool, str]:
        """处理每周日程"""
        try:
            logger.info(f"{self.log_prefix}开始处理每周日程")

            preferences = self.plugin_config.get("schedule", {}).get("preferences", {})

            schedule = await self.schedule_generator.generate_weekly_schedule(
                user_id=self.user_id if self.user_id else "system",
                chat_id="global",
                preferences=preferences,
                use_llm=True
            )

            created_ids = await self.schedule_generator.apply_schedule(
                schedule=schedule,
                user_id=self.user_id if self.user_id else "system",
                chat_id="global",
                auto_start=True
            )

            if not created_ids:
                return False, "生成每周计划失败"

            logger.info(f"{self.log_prefix}✅ 成功生成每周计划，创建了 {len(created_ids)} 个目标")
            reply = f"本周安排了{len(created_ids)}个目标 就这些吧"

            return True, reply

        except Exception as e:
            logger.error(f"{self.log_prefix}处理每周日程失败: {e}", exc_info=True)
            return False, f"生成每周计划时出错: {str(e)}"

    async def _handle_monthly(self) -> Tuple[bool, str]:
        """处理每月日程"""
        try:
            logger.info(f"{self.log_prefix}开始处理每月日程")

            preferences = self.plugin_config.get("schedule", {}).get("preferences", {})

            schedule = await self.schedule_generator.generate_monthly_schedule(
                user_id=self.user_id if self.user_id else "system",
                chat_id="global",
                preferences=preferences,
                use_llm=True
            )

            created_ids = await self.schedule_generator.apply_schedule(
                schedule=schedule,
                user_id=self.user_id if self.user_id else "system",
                chat_id="global",
                auto_start=True
            )

            if not created_ids:
                return False, "生成每月计划失败"

            logger.info(f"{self.log_prefix}✅ 成功生成每月计划，创建了 {len(created_ids)} 个目标")
            reply = f"这个月安排了{len(created_ids)}个目标 应该差不多了"

            return True, reply

        except Exception as e:
            logger.error(f"{self.log_prefix}处理每月日程失败: {e}", exc_info=True)
            return False, f"生成每月计划时出错: {str(e)}"

    def _get_today_schedule_goals(self):
        """检查今天是否已有日程目标（带 time_window 的目标）"""
        goals = self.goal_manager.get_active_goals(chat_id="global")
        today_str = datetime.now().strftime("%Y-%m-%d")

        # 筛选出带时间窗口的日程目标
        schedule_goals = []
        for goal in goals:
            # 检查是否有 time_window（向后兼容）
            has_time_window = False
            if goal.parameters and "time_window" in goal.parameters:
                has_time_window = True
            elif goal.conditions and "time_window" in goal.conditions:
                has_time_window = True

            # 只返回今天创建的日程（避免旧日程残留导致无法生成新日程）
            if has_time_window:
                goal_date = None
                if goal.created_at:
                    # 解析 ISO 格式的时间戳，提取日期部分
                    try:
                        if isinstance(goal.created_at, str):
                            goal_date = goal.created_at.split("T")[0]
                        else:
                            goal_date = goal.created_at.strftime("%Y-%m-%d")
                    except Exception as e:
                        logger.warning(f"解析目标创建时间失败: {goal.created_at} - {e}")

                # 只添加今天创建的日程目标
                if goal_date == today_str:
                    schedule_goals.append(goal)

        return schedule_goals

    def _build_schedule_summary(self, goals):
        """构建日程摘要"""
        if not goals:
            return "暂无日程"

        lines = ["📅 我的今日日程：\n"]

        # 获取当前时间用于高亮
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        current_time_minutes = current_hour * 60 + current_minute

        for goal in goals:
            # 获取时间窗口
            time_window = None
            if goal.parameters and "time_window" in goal.parameters:
                time_window = goal.parameters.get("time_window")
            elif goal.conditions and "time_window" in goal.conditions:
                time_window = goal.conditions.get("time_window")

            if time_window and len(time_window) >= 2:
                # time_window 可能是旧格式 [hour, hour] 或新格式 [minutes, minutes]
                start_val, end_val = time_window[0], time_window[1]

                # 判断格式：旧格式范围是0-23(小时)，新格式是0-1440(分钟)
                # 使用end_val判断更准确：如果end_val<=24说明是小时，否则是分钟
                if end_val <= 24:
                    # 旧格式：小时
                    start_minutes = start_val * 60
                    end_minutes = end_val * 60
                else:
                    # 新格式：分钟
                    start_minutes = start_val
                    end_minutes = end_val

                # 转换回时:分格式显示
                start_hour = start_minutes // 60
                start_min = start_minutes % 60
                end_hour = end_minutes // 60
                end_min = end_minutes % 60

                time_str = f"{start_hour:02d}:{start_min:02d}-{end_hour:02d}:{end_min:02d}"

                # 检查是否是当前活动
                is_current = start_minutes <= current_time_minutes < end_minutes

                if is_current:
                    lines.append(f"→ {time_str}  {goal.name}  ←当前")
                else:
                    lines.append(f"  {time_str}  {goal.name}")
            else:
                lines.append(f"  {goal.name}")

        return "\n".join(lines)
