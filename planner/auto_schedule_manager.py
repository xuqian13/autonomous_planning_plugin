"""
自动日程管理器
定时自动生成和应用日程计划
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import json
from pathlib import Path

from src.common.logger import get_logger

from .schedule_generator import ScheduleGenerator, ScheduleType
from .goal_manager import GoalManager

logger = get_logger("autonomous_planning.auto_schedule")


class AutoScheduleManager:
    """自动日程管理器"""

    def __init__(
        self,
        goal_manager: GoalManager,
        schedule_generator: ScheduleGenerator,
        config: Dict[str, Any]
    ):
        self.goal_manager = goal_manager
        self.schedule_generator = schedule_generator
        self.config = config

        # 历史记录文件
        self.data_dir = Path(__file__).parent.parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.data_dir / "schedule_history.json"

        # 加载历史
        self.generation_history = self._load_history()

    def _load_history(self) -> Dict[str, Any]:
        """加载生成历史"""
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载历史失败: {e}")

        return {
            "daily": None,  # 上次生成每日计划的日期
            "weekly": None,  # 上次生成每周计划的日期
            "monthly": None,  # 上次生成每月计划的日期
            "generated_schedules": []  # 生成的日程列表
        }

    def _save_history(self):
        """保存生成历史（自动清理旧记录）"""
        try:
            # 清理旧历史记录（保留最近30天）
            max_history_days = 30
            cutoff_date = (datetime.now() - timedelta(days=max_history_days)).strftime("%Y-%m-%d")

            if "generated_schedules" in self.generation_history:
                original_count = len(self.generation_history["generated_schedules"])

                # 过滤出最近30天的记录
                self.generation_history["generated_schedules"] = [
                    record for record in self.generation_history["generated_schedules"]
                    if record.get("date", "9999-99-99") >= cutoff_date or
                       record.get("week_start", "9999-99-99") >= cutoff_date or
                       record.get("month", "9999-99") >= cutoff_date[:7]  # 保留月份记录
                ]

                cleaned_count = original_count - len(self.generation_history["generated_schedules"])
                if cleaned_count > 0:
                    logger.debug(f"清理了 {cleaned_count} 条旧历史记录（{max_history_days}天前）")

            # 保存到文件
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.generation_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存历史失败: {e}")

    def _get_schedule_goals(self, chat_id: str = "global") -> list:
        """获取日程类型的目标"""
        goals = self.goal_manager.get_active_goals(chat_id=chat_id)
        schedule_goals = []

        for goal in goals:
            # 检查是否有time_window（表示是日程目标）
            has_time_window = False
            if goal.parameters and "time_window" in goal.parameters:
                has_time_window = True
            elif goal.conditions and "time_window" in goal.conditions:
                has_time_window = True

            if has_time_window:
                schedule_goals.append(goal)

        return schedule_goals

    def _check_and_clean_outdated_schedules(self, chat_id: str = "global") -> int:
        """
        检查并清理过期的日程

        Returns:
            清理的日程数量
        """
        schedule_goals = self._get_schedule_goals(chat_id)

        if not schedule_goals:
            logger.debug("没有找到日程目标")
            return 0

        today = datetime.now().strftime("%Y-%m-%d")
        deleted_count = 0

        # 检查历史记录中的日程日期
        for schedule_record in self.generation_history.get("generated_schedules", []):
            if schedule_record.get("type") == "daily":
                schedule_date = schedule_record.get("date")

                # 如果日程不是今天的，删除对应的目标
                if schedule_date and schedule_date != today:
                    goal_ids = schedule_record.get("goal_ids", [])
                    for goal_id in goal_ids:
                        if self.goal_manager.delete_goal(goal_id):
                            deleted_count += 1
                            logger.info(f"删除过期日程目标: {goal_id} (日期: {schedule_date})")

        if deleted_count > 0:
            logger.info(f"🧹 清理了 {deleted_count} 个过期日程目标")

        return deleted_count

    def should_generate_daily(self) -> bool:
        """判断是否应该生成每日计划"""
        auto_enabled = self.config.get("auto_generate_daily", False)
        logger.debug(f"检查每日计划生成: auto_generate_daily={auto_enabled}")

        if not auto_enabled:
            return False

        last_date = self.generation_history.get("daily")
        today = datetime.now().strftime("%Y-%m-%d")

        logger.debug(f"每日计划: last_date={last_date}, today={today}")

        # 情况1: 检查是否有日程目标
        schedule_goals = self._get_schedule_goals(chat_id="global")
        if not schedule_goals:
            logger.info("📋 没有日程目标，需要生成新日程")
            return True

        # 情况2: 检查日程是否是今天的
        if last_date != today:
            logger.info(f"📅 日程日期不匹配 (上次: {last_date}, 今天: {today})，需要生成新日程")
            return True

        # 情况3: 今天已有日程，但检查是否到了指定的生成时间（用于覆盖重新生成）
        # 这种情况可选，默认不启用
        force_regenerate = self.config.get("force_daily_regenerate", False)
        if force_regenerate:
            # 检查是否到了指定的生成时间
            trigger_time = self.config.get("daily_trigger_time", "06:00")
            try:
                trigger_hour, trigger_minute = map(int, trigger_time.split(":"))
                current_time = datetime.now()

                # 如果当前时间已经过了触发时间
                should_trigger = (current_time.hour > trigger_hour or
                    (current_time.hour == trigger_hour and current_time.minute >= trigger_minute))

                logger.debug(f"时间检查: 当前={current_time.strftime('%H:%M')}, 触发={trigger_time}, 应该触发={should_trigger}")

                if should_trigger:
                    logger.info(f"⏰ 到达触发时间，强制重新生成日程")
                    return True
            except Exception as e:
                logger.error(f"解析触发时间失败: {e}")

        logger.debug("✅ 今天已有日程，无需生成")
        return False

    def should_generate_weekly(self) -> bool:
        """判断是否应该生成每周计划"""
        if not self.config.get("auto_generate_weekly", False):
            return False

        # 获取本周的开始日期（周一）
        today = datetime.now()
        week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")

        last_week = self.generation_history.get("weekly")

        # 如果本周还没有生成过
        if last_week != week_start:
            # 检查是否是指定的生成日期（默认周一）
            trigger_weekday = self.config.get("weekly_trigger_weekday", 0)  # 0=周一
            if today.weekday() == trigger_weekday:
                # 检查时间
                trigger_time = self.config.get("weekly_trigger_time", "07:00")
                try:
                    trigger_hour, trigger_minute = map(int, trigger_time.split(":"))
                    if (today.hour > trigger_hour or
                        (today.hour == trigger_hour and today.minute >= trigger_minute)):
                        return True
                except Exception as e:
                    logger.error(f"解析触发时间失败: {e}")

        return False

    def should_generate_monthly(self) -> bool:
        """判断是否应该生成每月计划"""
        if not self.config.get("auto_generate_monthly", False):
            return False

        # 获取本月的标识
        today = datetime.now()
        month_id = today.strftime("%Y-%m")

        last_month = self.generation_history.get("monthly")

        # 如果本月还没有生成过
        if last_month != month_id:
            # 检查是否是指定的生成日期（默认每月1号）
            trigger_day = self.config.get("monthly_trigger_day", 1)
            if today.day == trigger_day:
                # 检查时间
                trigger_time = self.config.get("monthly_trigger_time", "08:00")
                try:
                    trigger_hour, trigger_minute = map(int, trigger_time.split(":"))
                    if (today.hour > trigger_hour or
                        (today.hour == trigger_hour and today.minute >= trigger_minute)):
                        return True
                except Exception as e:
                    logger.error(f"解析触发时间失败: {e}")

        return False

    async def generate_and_apply_daily(self, user_id: str = "system", chat_id: str = "global") -> bool:
        """生成并应用每日计划（全局）"""
        try:
            logger.info(f"开始自动生成每日计划（全局，chat_id={chat_id}）")

            # 先清理过期的日程
            deleted_count = self._check_and_clean_outdated_schedules(chat_id)
            if deleted_count > 0:
                logger.info(f"已清理 {deleted_count} 个过期日程目标")

            # 获取配置
            use_llm = self.config.get("use_llm_for_schedule", True)
            preferences = self.config.get("preferences", {})

            # 使用LLM生成日程
            schedule = await self.schedule_generator.generate_daily_schedule(
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences,
                use_llm=use_llm
            )
            schedule.metadata["auto_generated"] = True

            # 应用日程
            created_ids = await self.schedule_generator.apply_schedule(
                schedule=schedule,
                user_id=user_id,
                chat_id=chat_id,
                auto_start=True
            )

            # 更新历史
            today = datetime.now().strftime("%Y-%m-%d")
            self.generation_history["daily"] = today
            self.generation_history["generated_schedules"].append({
                "type": "daily",
                "date": today,
                "schedule_name": schedule.name,
                "goals_created": len(created_ids),
                "goal_ids": created_ids
            })
            self._save_history()

            logger.info(f"✅ 每日计划自动生成成功: {schedule.name}, 创建了 {len(created_ids)} 个目标")
            return True

        except Exception as e:
            logger.error(f"自动生成每日计划失败: {e}", exc_info=True)
            return False

    async def generate_and_apply_weekly(self, user_id: str = "system", chat_id: str = "global") -> bool:
        """生成并应用每周计划（全局）"""
        try:
            logger.info(f"开始自动生成每周计划（全局，chat_id={chat_id}）")

            use_llm = self.config.get("use_llm_for_schedule", True)
            preferences = self.config.get("preferences", {})

            schedule = await self.schedule_generator.generate_weekly_schedule(
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences,
                use_llm=use_llm
            )
            schedule.metadata["auto_generated"] = True

            created_ids = await self.schedule_generator.apply_schedule(
                schedule=schedule,
                user_id=user_id,
                chat_id=chat_id,
                auto_start=True
            )

            today = datetime.now()
            week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
            self.generation_history["weekly"] = week_start
            self.generation_history["generated_schedules"].append({
                "type": "weekly",
                "week_start": week_start,
                "schedule_name": schedule.name,
                "goals_created": len(created_ids),
                "goal_ids": created_ids
            })
            self._save_history()

            logger.info(f"✅ 每周计划自动生成成功: {schedule.name}, 创建了 {len(created_ids)} 个目标")
            return True

        except Exception as e:
            logger.error(f"自动生成每周计划失败: {e}", exc_info=True)
            return False

    async def generate_and_apply_monthly(self, user_id: str = "system", chat_id: str = "global") -> bool:
        """生成并应用每月计划（全局）"""
        try:
            logger.info(f"开始自动生成每月计划（全局，chat_id={chat_id}）")

            use_llm = self.config.get("use_llm_for_schedule", True)
            preferences = self.config.get("preferences", {})

            schedule = await self.schedule_generator.generate_monthly_schedule(
                user_id=user_id,
                chat_id=chat_id,
                preferences=preferences,
                use_llm=use_llm
            )
            schedule.metadata["auto_generated"] = True

            created_ids = await self.schedule_generator.apply_schedule(
                schedule=schedule,
                user_id=user_id,
                chat_id=chat_id,
                auto_start=True
            )

            month_id = datetime.now().strftime("%Y-%m")
            self.generation_history["monthly"] = month_id
            self.generation_history["generated_schedules"].append({
                "type": "monthly",
                "month": month_id,
                "schedule_name": schedule.name,
                "goals_created": len(created_ids),
                "goal_ids": created_ids
            })
            self._save_history()

            logger.info(f"✅ 每月计划自动生成成功: {schedule.name}, 创建了 {len(created_ids)} 个目标")
            return True

        except Exception as e:
            logger.error(f"自动生成每月计划失败: {e}", exc_info=True)
            return False

    async def check_and_generate(self, user_id: str = "system", chat_id: str = "global"):
        """检查并生成需要的日程"""
        logger.debug(f"开始检查日程生成条件...")
        tasks = []

        # 检查每日计划
        if self.should_generate_daily():
            logger.info("✅ 检测到需要生成每日计划")
            tasks.append(self.generate_and_apply_daily(user_id, chat_id))

        # 检查每周计划
        if self.should_generate_weekly():
            logger.info("✅ 检测到需要生成每周计划")
            tasks.append(self.generate_and_apply_weekly(user_id, chat_id))

        # 检查每月计划
        if self.should_generate_monthly():
            logger.info("✅ 检测到需要生成每月计划")
            tasks.append(self.generate_and_apply_monthly(user_id, chat_id))

        # 并发执行
        if tasks:
            logger.info(f"准备生成 {len(tasks)} 个日程")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = sum(1 for r in results if r is True)
            logger.info(f"自动日程生成完成: {success_count}/{len(tasks)} 成功")

            # 顺便清理旧目标（每次生成日程时执行）
            try:
                cleanup_days = self.config.get("cleanup_old_goals_days", 30)
                cleaned_count = self.goal_manager.cleanup_old_goals(days=cleanup_days)
                if cleaned_count > 0:
                    logger.info(f"🧹 已清理 {cleaned_count} 个旧目标")
            except Exception as e:
                logger.error(f"清理旧目标失败: {e}", exc_info=True)

            return success_count > 0
        else:
            logger.debug("当前没有需要生成的日程")

        return False

    def get_generation_summary(self) -> str:
        """获取生成历史摘要"""
        history = self.generation_history

        lines = ["📅 自动日程生成历史\n"]

        # 最近生成
        if history.get("daily"):
            lines.append(f"📆 最近每日计划: {history['daily']}")
        if history.get("weekly"):
            lines.append(f"📅 最近每周计划: {history['weekly']}")
        if history.get("monthly"):
            lines.append(f"📊 最近每月计划: {history['monthly']}")

        # 历史统计
        schedules = history.get("generated_schedules", [])
        if schedules:
            lines.append(f"\n总共自动生成: {len(schedules)} 个日程")

            daily_count = sum(1 for s in schedules if s["type"] == "daily")
            weekly_count = sum(1 for s in schedules if s["type"] == "weekly")
            monthly_count = sum(1 for s in schedules if s["type"] == "monthly")

            lines.append(f"  - 每日: {daily_count} 次")
            lines.append(f"  - 每周: {weekly_count} 次")
            lines.append(f"  - 每月: {monthly_count} 次")

            # 最近3次
            if len(schedules) > 0:
                lines.append("\n最近的生成:")
                for schedule in schedules[-3:]:
                    date_key = schedule.get("date") or schedule.get("week_start") or schedule.get("month")
                    lines.append(f"  - {schedule['type']}: {schedule['schedule_name']} ({date_key})")

        return "\n".join(lines)
