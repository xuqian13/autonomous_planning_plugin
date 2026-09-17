"""``/plan`` 命令的业务实现。

通过 ``self._plugin.ctx.send.text(...) / ctx.send.image(...)`` 发送消息，
通过 host 注入的 ``user_id`` / ``stream_id`` 解析上下文，无须再访问 ``self.message``。
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Tuple
import asyncio
import logging

from ..planner.goal_manager import get_goal_manager
from ..utils.schedule_image_generator import ScheduleImageGenerator
from ..utils.stream_filter import is_stream_allowed
from ..utils.time_utils import format_minutes_to_time, get_time_window_from_goal, strip_tz
from ..utils.timezone_manager import TimezoneManager

if TYPE_CHECKING:
    from ..plugin import AutonomousPlanningPluginV4

logger = logging.getLogger(__name__)


# 目标类型 → emoji 映射
_GOAL_TYPE_EMOJI: Dict[str, str] = {
    "meal": "🍽️",
    "study": "📚",
    "entertainment": "🎮",
    "daily_routine": "🏠",
    "social_maintenance": "💬",
    "learn_topic": "📖",
    "exercise": "🏃",
    "rest": "💤",
    "free_time": "🌟",
}

_WEEKDAY_NAMES: Dict[int, str] = {
    0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日",
}


class CommandService:
    """规划命令处理服务。"""

    def __init__(self, plugin: "AutonomousPlanningPluginV4") -> None:
        """初始化 CommandService。

        Args:
            plugin: 当前插件实例
        """
        self._plugin = plugin
        logger.debug("CommandService 初始化")

    # ------------------------------------------------------------
    # 配置访问辅助
    # ------------------------------------------------------------

    @property
    def _enable_detailed_description(self) -> bool:
        return self._plugin.config.schedule.enable_detailed_description

    def _make_tz_manager(self) -> TimezoneManager:
        return TimezoneManager(self._plugin.config.schedule.timezone)

    # ------------------------------------------------------------
    # 命令分发入口
    # ------------------------------------------------------------

    async def execute(
        self,
        text: str = "",
        stream_id: str = "",
        user_id: str = "",
        platform: str = "",
        group_id: str = "",
        matched_groups: Dict[str, str] | None = None,
    ) -> Tuple[bool, str, bool]:
        """命令执行入口，按子命令分发。

        Args:
            text: 完整命令文本（含 ``/plan`` 前缀）
            stream_id: 消息流 ID（用于回发消息）
            user_id: 触发命令的用户 ID（用于权限校验）
            platform: 平台标识
            group_id: 群组 ID
            matched_groups: 命令正则命名捕获

        Returns:
            (success, response_text, intercept_message) 三元组
        """
        del platform, group_id  # 当前命令未使用，保留参数防止未来扩展
        groups = matched_groups or {}
        command_text = str(groups.get("planning_cmd", text) or "").strip()
        parts = command_text.split()

        # 权限检查：所有命令都需要管理员权限（admin_users 留空时所有人可用）
        if not self._check_permission(user_id):
            await self._send(stream_id, "🚫 你不是管理员哦~只有管理员才能查看和管理日程呢")
            return True, "没有权限", True

        # 白名单过滤（留空 = 全部允许）
        allowed_streams = self._plugin.config.schedule.allowed_streams
        if not is_stream_allowed(stream_id, allowed_streams):
            await self._send(stream_id, "💤 当前会话未启用日程功能")
            return True, "会话未启用", True

        if len(parts) == 1:
            await self._show_help(stream_id)
            return True, "显示帮助", True

        subcommand = parts[1] if len(parts) > 1 else ""

        if subcommand == "status":
            await self._handle_status(stream_id)
        elif subcommand == "list":
            await self._handle_list(stream_id)
        elif subcommand == "regenerate":
            await self._handle_regenerate(stream_id, parts)
        elif subcommand == "delete":
            return await self._handle_delete(stream_id, parts)
        elif subcommand == "clear":
            await self._handle_clear(stream_id, parts)
        elif subcommand == "help":
            await self._show_help(stream_id)
        else:
            await self._send(stream_id, f"未知命令: {subcommand}\n使用 /plan help 查看帮助")

        return True, "命令执行完成", True

    # ------------------------------------------------------------
    # 权限检查
    # ------------------------------------------------------------

    def _check_permission(self, user_id: str) -> bool:
        """检查用户权限。

        Args:
            user_id: 当前触发命令的用户 ID

        Returns:
            是否有权限
        """
        admin_users = self._plugin.config.schedule.admin_users
        # 留空时所有人都有权限
        if not admin_users:
            return True
        return str(user_id) in admin_users

    # ------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------

    async def _send(self, stream_id: str, text: str) -> None:
        """发送文本消息（封装 ctx.send.text）。"""
        if not stream_id:
            logger.warning("无 stream_id，跳过发送: %s", text[:30])
            return
        await self._plugin.ctx.send.text(text, stream_id)

    def _get_today_schedule_goals(self, goal_manager: Any) -> List[Any]:
        """获取今天的日程目标（沿用 v3 统一方法）。"""
        return goal_manager.get_schedule_goals(chat_id="global")

    def _sort_schedule_goals(self, goals: List[Any]) -> List[Any]:
        """按时间窗口排序日程目标。"""

        def _get_start(g: Any) -> int:
            tw = (g.parameters.get("time_window") if g.parameters else None) or (
                g.conditions.get("time_window") if g.conditions else None
            ) or [0]
            return tw[0] if tw else 0

        return sorted(goals, key=_get_start)

    # ------------------------------------------------------------
    # 子命令实现
    # ------------------------------------------------------------

    async def _handle_status(self, stream_id: str) -> None:
        """``/plan status``：详细文字格式显示今日日程。"""
        goal_manager = get_goal_manager()
        schedule_goals = self._get_today_schedule_goals(goal_manager)

        if not schedule_goals:
            await self._send(stream_id, "📋 今天还没有日程安排\n\n💡 提示：对我说\"帮我生成今天的日程\"来自动创建")
            return

        schedule_goals = self._sort_schedule_goals(schedule_goals)
        tz_manager = self._make_tz_manager()
        now = tz_manager.get_now()
        today = now.strftime("%Y-%m-%d")
        weekday = _WEEKDAY_NAMES[now.weekday()]

        messages = [f"📅 今日日程 {today} {weekday}\n", f"共 {len(schedule_goals)} 项活动\n"]

        for idx, goal in enumerate(schedule_goals, 1):
            start_minutes, end_minutes = get_time_window_from_goal(goal)
            start_time = format_minutes_to_time(start_minutes)
            end_time = format_minutes_to_time(end_minutes)
            type_emoji = _GOAL_TYPE_EMOJI.get(goal.goal_type, "📌")

            messages.append(f"{idx}. ⏰ {start_time}-{end_time}  {type_emoji} {goal.name}")
            if self._enable_detailed_description and goal.description:
                messages.append(f"   📝 {goal.description}")
            messages.append("")  # 空行分隔

        await self._send(stream_id, "\n".join(messages))

    async def _handle_list(self, stream_id: str) -> None:
        """``/plan list``：图片格式显示今日日程。"""
        goal_manager = get_goal_manager()
        schedule_goals = self._get_today_schedule_goals(goal_manager)

        if not schedule_goals:
            await self._send(stream_id, "📋 今天还没有日程安排\n\n💡 提示：对我说\"帮我生成今天的日程\"来自动创建")
            return

        schedule_goals = self._sort_schedule_goals(schedule_goals)

        # 准备图片数据
        schedule_items: List[Dict[str, Any]] = []
        for goal in schedule_goals:
            start_minutes, end_minutes = get_time_window_from_goal(goal)
            time_str = f"{format_minutes_to_time(start_minutes)}-{format_minutes_to_time(end_minutes)}"
            schedule_items.append({
                "time": time_str,
                "name": goal.name,
                "description": goal.description if self._enable_detailed_description else "",
                "goal_type": goal.goal_type,
            })

        tz_manager = self._make_tz_manager()
        now = tz_manager.get_now()
        today = now.strftime("%Y-%m-%d")
        weekday = _WEEKDAY_NAMES[now.weekday()]
        title = f"今日日程 {today} {weekday}"

        # 生成图片：PIL 是同步阻塞操作（渲染 + 字体加载可能耗时 100ms~1s），
        # 必须丢给默认线程池，避免阻塞事件循环上的其它协程
        try:
            _img_path, img_base64 = await asyncio.to_thread(
                ScheduleImageGenerator.generate_schedule_image,
                title=title,
                schedule_items=schedule_items,
                output_dir=self._plugin._data_dir / "images",
            )
            if not stream_id:
                logger.warning("无 stream_id，跳过图片发送")
                return
            await self._plugin.ctx.send.image(img_base64, stream_id)
            logger.info("✅ 日程图片已发送（base64）")
        except Exception as exc:
            logger.error(f"发送图片失败: {exc}，降级为文本输出", exc_info=True)
            # 降级方案：文本输出
            try:
                fallback = ["📅 今日日程详情\n"]
                for item in schedule_items:
                    fallback.append(f"  ⏰ {item['time']}  {item['name']}")
                    if self._enable_detailed_description and item["description"]:
                        fallback.append(f"     {item['description']}")
                    fallback.append("")
                await self._send(stream_id, "\n".join(fallback))
            except Exception as exc2:
                logger.error(f"文本输出也失败: {exc2}", exc_info=True)

    async def _handle_regenerate(self, stream_id: str, parts: List[str]) -> None:
        """``/plan regenerate [额外要求...]``：立即重新生成今日日程。

        会先删掉今天已有的 schedule_goals，再走 ``ScheduleGenerator`` 全量重生成并
        自动 apply。``parts[2:]`` 拼成的剩余字符串将作为 ``extra_prompt`` 临时叠加
        到 ``custom_prompt`` 上（生成完成后自动还原）。

        Args:
            stream_id: 来源会话 ID。
            parts: 已 split 的命令片段，``parts[0]=/plan``，``parts[1]=regenerate``，
                ``parts[2:]`` 为可选的额外要求文本。
        """
        tools_svc = self._plugin._tools_svc
        if tools_svc is None:
            await self._send(stream_id, "❌ 插件未完成初始化，无法重新生成日程")
            return

        # 剩余参数拼为 extra_prompt（允许带空格的自然描述）
        extra_prompt = " ".join(parts[2:]).strip()

        hint = "♻️ 正在重新生成今日日程，请稍候（约 30s ~ 2min）..."
        if extra_prompt:
            hint += f"\n📝 额外要求：{extra_prompt}"
        await self._send(stream_id, hint)

        try:
            schedule = await tools_svc.regenerate_today_schedule_now(extra_prompt=extra_prompt)
        except Exception as exc:
            logger.error(f"重新生成日程失败: {exc}", exc_info=True)
            await self._send(stream_id, f"❌ 重新生成失败: {exc}")
            return

        msg = f"✅ 已重新生成今日日程，共 {len(schedule.items)} 项活动\n\n💡 使用 /plan list 查看图片，或 /plan status 查看文字详情"
        await self._send(stream_id, msg)

    async def _handle_delete(self, stream_id: str, parts: List[str]) -> Tuple[bool, str, bool]:
        """``/plan delete <id|序号>``：删除目标。"""
        goal_manager = get_goal_manager()

        if len(parts) < 3:
            await self._send(
                stream_id,
                "❌ 请提供要删除的目标 ID 或序号\n\n用法: /plan delete <goal_id或序号>\n\n使用 /plan list 查看所有目标",
            )
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
                await self._send(stream_id, f"❌ 序号 {identifier} 超出范围\n使用 /plan list 查看所有目标")
                return True, "序号无效", True
        else:
            # 作为 goal_id 处理
            goal_id = identifier
            goal = goal_manager.get_goal(goal_id)
            if not goal:
                await self._send(stream_id, f"❌ 目标不存在: {goal_id}")
                return True, "目标不存在", True
            goal_name = goal.name

        success = goal_manager.delete_goal(goal_id)
        if success:
            await self._send(stream_id, f"🗑️ 已删除目标: {goal_name}\n\nID: {goal_id}")
        else:
            await self._send(stream_id, "❌ 删除失败")
        return True, "命令执行完成", True

    async def _handle_clear(self, stream_id: str, parts: List[str]) -> None:
        """``/plan clear [days]``：清理旧日程。"""
        goal_manager = get_goal_manager()

        # days_to_keep=0 表示仅保留今天
        days_to_keep = 0
        if len(parts) >= 3 and parts[2].isdigit():
            days_to_keep = int(parts[2])

        tz_manager = self._make_tz_manager()
        cutoff_date = tz_manager.get_now() - timedelta(days=days_to_keep)

        goals = goal_manager.get_all_goals()
        to_delete: List[Any] = []
        for g in goals:
            # 仅清理日程类型（有 time_window）
            has_time_window = bool(
                (g.parameters and "time_window" in g.parameters)
                or (g.conditions and "time_window" in g.conditions)
            )
            if not has_time_window:
                continue

            if not g.created_at:
                continue

            try:
                # Goal.from_dict 保证 created_at 已是 tz-aware datetime
                goal_datetime = g.created_at.replace(hour=0, minute=0, second=0, microsecond=0)
                cutoff_datetime = cutoff_date.replace(hour=0, minute=0, second=0, microsecond=0)

                # 防 tz-aware/naive 混比报错（历史数据库可能是 tz-naive，v4.1 后是 tz-aware）
                if strip_tz(goal_datetime) < strip_tz(cutoff_datetime):
                    to_delete.append(g)
            except Exception as exc:
                logger.warning(f"解析目标创建时间失败: {g.created_at} - {exc}")
                continue

        if not to_delete:
            await self._send(stream_id, "✨ 没有需要清理的旧日程")
            return

        deleted_count = 0
        for goal in to_delete:
            if goal_manager.delete_goal(goal.goal_id):
                deleted_count += 1

        if deleted_count > 0:
            today_schedule_count = len(self._get_today_schedule_goals(goal_manager))
            await self._send(
                stream_id,
                f"🧹 已清理 {deleted_count} 个旧日程目标\n\n保留了今天的 {today_schedule_count} 个日程",
            )
        else:
            await self._send(stream_id, "❌ 清理失败")

    async def _show_help(self, stream_id: str) -> None:
        """显示帮助。"""
        help_text = """🤖 日程规划系统

📋 命令列表:
/plan status - 查看今日日程（详细文字格式，含描述）
/plan list - 查看今日日程（美观图片格式）
/plan regenerate [额外要求] - 重新生成今日日程（先删今天再重生，可附加临时要求）
/plan delete <goal_id或序号> - 删除指定目标
/plan clear - 清理昨天及更早的旧日程
/plan help - 显示此帮助

💡 使用方式:
1. 对我说 "帮我生成今天的日程" 我会自动创建
2. 对我说 "今天有什么安排" 我会查看并告诉你
3. 使用 status 查看详细文字信息，list 查看美观图片
4. 使用 regenerate 在不满意当前日程时立即重新生成
5. 使用 clear 清理旧日程，保持目标列表整洁

✨ 示例对话:
"帮我生成今天的日程"
"今天有什么安排"
"现在应该做什么"
"提醒我每天早上9点问候大家"

♻️ 重新生成示例:
/plan regenerate                # 直接重新生成今日日程
/plan regenerate 加入下午跑步     # 重新生成并临时叠加要求
/plan regenerate 今天生日要庆祝   # 重新生成并融入特殊事件

🗑️ 清理示例:
/plan clear          # 清理昨天及更早的日程
/plan delete 1       # 删除第 1 个目标
/plan delete abc-123 # 删除指定 ID 的目标

📌 注意:
- 日程每天自动生成，无需手动创建
- status/list 命令只显示今天的日程
- regenerate 会丢弃今天已有日程并重新生成，操作不可逆
- clear 命令会自动保留今天的日程
"""
        await self._send(stream_id, help_text)
