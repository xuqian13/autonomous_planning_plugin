"""自主规划插件 - 命令模块"""

import json
from typing import Dict, Any, List, Tuple
from datetime import datetime

from src.plugin_system import BaseCommand
from src.common.logger import get_logger

from ..planner.goal_manager import get_goal_manager, GoalStatus
from ..planner.schedule_generator import ScheduleGenerator, ScheduleType
from ..utils.schedule_image_generator import ScheduleImageGenerator
from ..utils.time_utils import format_minutes_to_time, get_time_window_from_goal

logger = get_logger("autonomous_planning.commands")

class PlanningCommand(BaseCommand):
    """规划管理命令"""

    command_name = "planning"
    command_description = "麦麦自主规划系统管理命令"
    command_pattern = r"(?P<planning_cmd>^/(plan|规划).*$)"

    def _get_today_schedule_goals(self, goal_manager) -> List:
        """
        获取今天的日程目标（P2优化：使用统一方法）

        Args:
            goal_manager: 目标管理器实例

        Returns:
            今天创建的日程目标列表
        """
        return goal_manager.get_schedule_goals(chat_id="global")

    def _sort_schedule_goals(self, goals: List) -> List:
        """
        按时间排序日程目标

        Args:
            goals: 日程目标列表

        Returns:
            排序后的日程目标列表
        """
        def get_time_window(g):
            tw = (g.parameters.get("time_window") if g.parameters else None) or \
                 (g.conditions.get("time_window") if g.conditions else None) or [0]
            return tw[0] if tw else 0

        return sorted(goals, key=get_time_window)

    def _format_time_from_minutes(self, minutes: int) -> str:
        """将分钟数转换为时间字符串"""
        return format_minutes_to_time(minutes)

    def _get_time_window_from_goal(self, goal) -> tuple:
        """从目标中提取时间窗口（统一使用工具函数）"""
        return get_time_window_from_goal(goal)

    def _check_permission(self) -> bool:
        """检查用户权限"""
        try:
            admin_users = self.get_config("autonomous_planning.schedule.admin_users", [])
            # 如果没有配置管理员（空列表），则所有人都有权限
            if not admin_users:
                return True

            user_id = str(self.message.message_info.user_info.user_id)
            return user_id in admin_users
        except Exception as e:
            logger.warning(f"检查权限失败: {e}")
            # 出错时默认有权限（保持向后兼容）
            return True

    async def execute(self) -> Tuple[bool, str, bool]:
        """执行命令"""
        command_text = self.matched_groups.get("planning_cmd", "").strip()
        parts = command_text.split()

        # 检查权限（所有命令都需要管理员权限）
        has_permission = self._check_permission()
        if not has_permission:
            await self.send_text("🚫 你不是管理员哦~只有管理员才能查看和管理日程呢")
            return True, "没有权限", True

        if len(parts) == 1:
            await self._show_help()
            return True, "显示帮助", True

        subcommand = parts[1] if len(parts) > 1 else ""

        if subcommand == "status":
            # 显示状态 - 详细文字格式
            goal_manager = get_goal_manager()
            schedule_goals = self._get_today_schedule_goals(goal_manager)

            if not schedule_goals:
                await self.send_text("📋 今天还没有日程安排\n\n💡 提示：对我说\"帮我生成今天的日程\"来自动创建")
            else:
                # 按时间排序
                schedule_goals = self._sort_schedule_goals(schedule_goals)

                # 获取今天的日期和星期
                today = datetime.now().strftime("%Y-%m-%d")
                weekday_cn = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
                weekday = weekday_cn[datetime.now().weekday()]

                messages = [f"📅 今日日程 {today} {weekday}\n"]
                messages.append(f"共 {len(schedule_goals)} 项活动\n")

                for idx, goal in enumerate(schedule_goals, 1):
                    # 获取时间窗口
                    start_minutes, end_minutes = self._get_time_window_from_goal(goal)

                    # 转换为时间字符串
                    start_time = self._format_time_from_minutes(start_minutes)
                    end_time = self._format_time_from_minutes(end_minutes)

                    # 目标类型emoji
                    type_emoji = {
                        "meal": "🍽️",
                        "study": "📚",
                        "entertainment": "🎮",
                        "daily_routine": "🏠",
                        "social_maintenance": "💬",
                        "learn_topic": "📖",
                        "exercise": "🏃",
                        "rest": "💤",
                        "free_time": "🌟",
                    }.get(goal.goal_type, "📌")

                    # 详细格式：序号、时间、emoji、名称
                    messages.append(f"{idx}. ⏰ {start_time}-{end_time}  {type_emoji} {goal.name}")

                    # 添加描述
                    if goal.description:
                        messages.append(f"   📝 {goal.description}")

                    messages.append("")  # 空行分隔

                await self.send_text("\n".join(messages))

        elif subcommand == "list":
            # 列出目标 - 图片格式
            goal_manager = get_goal_manager()
            schedule_goals = self._get_today_schedule_goals(goal_manager)

            if not schedule_goals:
                await self.send_text("📋 今天还没有日程安排\n\n💡 提示：对我说\"帮我生成今天的日程\"来自动创建")
            else:
                # 按时间排序
                schedule_goals = self._sort_schedule_goals(schedule_goals)

                # 准备图片数据
                schedule_items = []
                for goal in schedule_goals:
                    # 获取时间窗口
                    start_minutes, end_minutes = self._get_time_window_from_goal(goal)

                    # 转换为时间字符串
                    time_str = f"{self._format_time_from_minutes(start_minutes)}-{self._format_time_from_minutes(end_minutes)}"

                    schedule_items.append({
                        "time": time_str,
                        "name": goal.name,
                        "description": goal.description,
                        "goal_type": goal.goal_type
                    })

                # 生成图片
                img_path = None
                img_base64 = None
                try:
                    # 简化标题：只显示日期，不显示emoji
                    today = datetime.now().strftime("%Y-%m-%d")
                    weekday_cn = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
                    weekday = weekday_cn[datetime.now().weekday()]
                    title = f"今日日程 {today} {weekday}"

                    img_path, img_base64 = ScheduleImageGenerator.generate_schedule_image(
                        title=title,
                        schedule_items=schedule_items
                    )

                    # 使用imageurl发送文件路径（适合本地文件）
                    await self.send_custom("imageurl", f"file://{img_path}")
                    logger.info(f"✅ 日程图片已发送（imageurl，路径: {img_path}）")

                except Exception as e:
                    logger.error(f"发送图片失败: {e}, 使用文本输出")
                    # 降级方案：文本输出
                    try:
                        messages = ["📅 今日日程详情\n"]
                        for item in schedule_items:
                            messages.append(f"  ⏰ {item['time']}  {item['name']}")
                            messages.append(f"     {item['description']}")
                            messages.append("")
                        await self.send_text("\n".join(messages))
                    except Exception as e2:
                        logger.error(f"文本输出也失败: {e2}")

        elif subcommand == "delete":
            # 删除目标
            goal_manager = get_goal_manager()

            if len(parts) < 3:
                await self.send_text("❌ 请提供要删除的目标ID或序号\n\n用法: /plan delete <goal_id或序号>\n\n使用 /plan list 查看所有目标")
                return True, "缺少参数", True

            identifier = parts[2]

            # 尝试作为索引处理
            if identifier.isdigit():
                idx = int(identifier) - 1
                goals = goal_manager.get_all_goals()

                if 0 <= idx < len(goals):
                    goal = goals[idx]
                    goal_id = goal.goal_id
                    goal_name = goal.name
                else:
                    await self.send_text(f"❌ 序号 {identifier} 超出范围\n使用 /plan list 查看所有目标")
                    return True, "序号无效", True
            else:
                # 作为 goal_id 处理
                goal_id = identifier
                goal = goal_manager.get_goal(goal_id)

                if not goal:
                    await self.send_text(f"❌ 目标不存在: {goal_id}")
                    return True, "目标不存在", True

                goal_name = goal.name

            # 执行删除
            success = goal_manager.delete_goal(goal_id)

            if success:
                await self.send_text(f"🗑️ 已删除目标: {goal_name}\n\nID: {goal_id}")
            else:
                await self.send_text(f"❌ 删除失败")

        elif subcommand == "clear":
            # 清理旧日程
            goal_manager = get_goal_manager()

            # 获取要清理的天数（默认清理昨天及更早的日程）
            days_to_keep = 0  # 只保留今天的
            if len(parts) >= 3 and parts[2].isdigit():
                days_to_keep = int(parts[2])

            # 计算截止日期
            from datetime import timedelta
            cutoff_date = datetime.now() - timedelta(days=days_to_keep)
            today_str = datetime.now().strftime("%Y-%m-%d")

            # 找出要清理的日程目标
            goals = goal_manager.get_all_goals()
            to_delete = []

            for g in goals:
                # 检查是否是日程类型（有time_window）
                has_time_window = False
                if g.parameters and "time_window" in g.parameters:
                    has_time_window = True
                elif g.conditions and "time_window" in g.conditions:
                    has_time_window = True

                if not has_time_window:
                    continue  # 跳过非日程类型

                if g.created_at:
                    try:
                        if isinstance(g.created_at, str):
                            goal_date_str = g.created_at.split("T")[0]
                            goal_datetime = datetime.strptime(goal_date_str, "%Y-%m-%d")
                        else:
                            # datetime 对象
                            goal_datetime = g.created_at.replace(hour=0, minute=0, second=0, microsecond=0)

                        # 使用datetime对象比较
                        cutoff_datetime = cutoff_date.replace(hour=0, minute=0, second=0, microsecond=0)
                        if goal_datetime < cutoff_datetime:
                            to_delete.append(g)
                    except Exception as e:
                        logger.warning(f"解析目标创建时间失败: {g.created_at} - {e}")
                        continue

            if not to_delete:
                await self.send_text(f"✨ 没有需要清理的旧日程")
            else:
                # 执行删除
                deleted_count = 0
                for goal in to_delete:
                    if goal_manager.delete_goal(goal.goal_id):
                        deleted_count += 1

                if deleted_count > 0:
                    today_schedule_count = len(self._get_today_schedule_goals(goal_manager))
                    await self.send_text(f"🧹 已清理 {deleted_count} 个旧日程目标\n\n保留了今天的 {today_schedule_count} 个日程")
                else:
                    await self.send_text(f"❌ 清理失败")

        elif subcommand == "help":
            await self._show_help()

        else:
            await self.send_text(f"未知命令: {subcommand}\n使用 /plan help 查看帮助")

        return True, "命令执行完成", True

    async def _show_help(self):
        """显示帮助"""
        help_text = """🤖 麦麦自主规划系统

📋 命令列表:
/plan status - 查看今日日程（详细文字格式，含描述）
/plan list - 查看今日日程（美观图片格式）
/plan delete <goal_id或序号> - 删除指定目标
/plan clear - 清理昨天及更早的旧日程
/plan help - 显示此帮助

💡 使用方式:
1. 对我说 "帮我生成今天的日程" 我会自动创建
2. 对我说 "今天有什么安排" 我会查看并告诉你
3. 使用 status 查看详细文字信息，list 查看美观图片
4. 使用 clear 清理旧日程，保持目标列表整洁

✨ 示例对话:
"帮我生成今天的日程"
"今天有什么安排"
"现在应该做什么"
"提醒我每天早上9点问候大家"

🗑️ 清理示例:
/plan clear          # 清理昨天及更早的日程
/plan delete 1       # 删除第1个目标
/plan delete abc-123 # 删除指定ID的目标

📌 注意:
- 日程每天自动生成，无需手动创建
- status/list 命令只显示今天的日程
- clear 命令会自动保留今天的日程
"""
        await self.send_text(help_text)


# ===== Plugin =====

