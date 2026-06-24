"""Context Loader Module.

This module provides historical context loading functionality for schedule generation.
Separated from BaseScheduleGenerator to follow Single Responsibility Principle.
"""

import logging
from datetime import datetime, time, timedelta
from typing import Any, Dict, List, Optional


# 类型提示导入
from ...utils.stream_filter import is_stream_allowed
from ...utils.timezone_manager import TimezoneManager
from ..goal_manager import GoalManager

logger = logging.getLogger(__name__)

# 常量定义
MAX_YESTERDAY_ACTIVITIES = 15  # 昨日日程显示的最大活动数，与单日日程上限对齐
MAX_YESTERDAY_DESC_CHARS = 48  # 昨日日程描述最大字符数，保留地点/主线但控制 prompt 长度
MAX_HISTORY_LINE_CHARS = 80    # 历史消息单行最大字符数


class ScheduleContextLoader:
    """日程上下文加载器 - 单一职责：加载历史上下文

    该类负责：
    1. 加载昨日日程摘要
    2. 格式化上下文信息
    3. 提供给Prompt使用

    与BaseScheduleGenerator的区别：
    - 只负责上下文加载，不涉及Prompt构建或Schema定义
    - 通过构造函数接收所有依赖（依赖注入）
    """

    def __init__(self, goal_manager: GoalManager, tz_manager: TimezoneManager):
        """初始化上下文加载器

        Args:
            goal_manager: 目标管理器
            tz_manager: 时区管理器
        """
        self.goal_manager = goal_manager
        self.tz_manager = tz_manager

    def _extract_time_window(self, goal) -> Optional[List[int]]:
        """从目标中提取time_window

        Args:
            goal: 目标对象

        Returns:
            time_window列表，如果不存在则返回None
        """
        if goal.parameters and "time_window" in goal.parameters:
            return goal.parameters["time_window"]
        elif goal.conditions and "time_window" in goal.conditions:
            return goal.conditions["time_window"]
        return None

    def load_yesterday_schedule_summary(self) -> Optional[str]:
        """加载昨日日程摘要（向后兼容，等价于 load_recent_schedule_summary(days=1)）。

        Returns:
            昨日日程摘要字符串，如果加载失败则返回默认文本
        """
        return self.load_recent_schedule_summary(days=1)

    def load_recent_schedule_summary(self, days: int = 3) -> Optional[str]:
        """加载最近 N 天的日程摘要，用于让 LLM 连续演化新日程。

        覆盖范围：``yesterday - (days - 1)`` 到 ``yesterday``。
        过期日程（status=COMPLETED）依然能拉到 —— ``cleanup_expired_schedules``
        只是改状态不删数据，``cleanup_old_goals(days=30)`` 才真正删除。

        Args:
            days: 回看天数（1=只看昨天；3-5 推荐；防止交替式重复）

        Returns:
            多天日程摘要字符串；全部为空时返回兜底文案。
        """
        try:
            days = max(1, int(days))
            now = self.tz_manager.get_now()
            all_lines: List[str] = []

            for offset in range(1, days + 1):  # 1=昨天, 2=前天, ...
                day = now - timedelta(days=offset)
                day_str = day.strftime("%Y-%m-%d")
                day_label = day.strftime("%m-%d")
                weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                day_weekday = weekday_names[day.weekday()]

                # 拉这一天的 schedule_goals（不限状态，包含已 cleanup 为 COMPLETED 的）
                goals = self.goal_manager.get_schedule_goals(chat_id="global", date_str=day_str)

                timed_activities: List[tuple[int, str]] = []
                for goal in goals:
                    time_window = self._extract_time_window(goal)
                    if time_window:
                        try:
                            start_minutes = int(time_window[0])
                        except (TypeError, ValueError, IndexError):
                            continue
                        hour = start_minutes // 60
                        minute = start_minutes % 60
                        time_str = f"{hour:02d}:{minute:02d}"
                        line = f"  {time_str} {goal.name}"
                        desc = " ".join(str(getattr(goal, "description", "") or "").split())
                        if desc and desc != str(goal.name):
                            if len(desc) > MAX_YESTERDAY_DESC_CHARS:
                                desc = desc[:MAX_YESTERDAY_DESC_CHARS].rstrip() + "…"
                            line += f" — {desc}"
                        timed_activities.append((start_minutes, line))

                if timed_activities:
                    day_activities = [line for _, line in sorted(timed_activities, key=lambda item: item[0])]
                    all_lines.append(f"【{day_label} {day_weekday}】")
                    all_lines.extend(day_activities[:MAX_YESTERDAY_ACTIVITIES])

            if all_lines:
                label = "昨天的日程" if days == 1 else f"最近 {days} 天的日程"
                summary = f"{label}:\n" + "\n".join(all_lines)
                logger.debug(f"加载 {days} 天日程摘要: {len(all_lines)} 行")
                return summary

            logger.debug(f"最近 {days} 天没有日程记录")
            return f"最近{'一天' if days == 1 else f'{days} 天'}普通的日子" if days == 1 else f"最近 {days} 天没有具体日程记录"

        except Exception as e:
            logger.warning(f"加载最近 {days} 天日程失败: {e}")
            return f"最近{'一天' if days == 1 else f'{days} 天'}的事情记不太清了"

    # ============================================================
    # 跨群动态上下文（D 阶段新增）
    # ============================================================

    async def load_recent_history_across_streams(
        self,
        plugin: Any,
        allowed_streams: List[str],
        limit: int,
    ) -> str:
        """跨白名单聊天流拉取当天最近消息，拼成可读字符串。

        Args:
            plugin: 当前插件实例（提供 ``ctx.chat`` 与 ``ctx.message``）。
            allowed_streams: 群白名单；为空 = 不限制，但仍会取所有已知 stream。
            limit: 累计最多拉取多少条消息；<=0 直接返回空串（节省 RPC）。

        Returns:
            形如 ``[HH:MM] {nick}@{stream}: {text}`` 的多行字符串；无内容时返回空串。
        """
        if limit <= 0 or plugin is None:
            return ""

        try:
            streams_raw = await plugin.ctx.chat.get_all_streams(platform="qq")
        except Exception as exc:
            logger.debug(f"获取聊天流列表失败: {exc}")
            return ""
        if not isinstance(streams_raw, list):
            return ""

        # 按白名单过滤
        streams = [s for s in streams_raw if isinstance(s, dict)]
        streams = [
            s for s in streams
            if is_stream_allowed(
                str(s.get("session_id") or s.get("stream_id") or ""),
                allowed_streams,
                stream_info=s,
            )
        ]
        if not streams:
            return ""

        per_stream = max(1, limit // len(streams))
        now = datetime.now()
        start_today = datetime.combine(now.date(), time.min)
        start_ts = str(start_today.timestamp())
        end_ts = str(now.timestamp())

        all_lines: List[str] = []
        for stream in streams:
            sid = str(stream.get("session_id") or stream.get("stream_id") or "").strip()
            if not sid:
                continue
            try:
                msgs = await plugin.ctx.message.get_by_time_in_chat(
                    sid, start_ts, end_ts, limit=per_stream,
                )
            except Exception as exc:
                logger.debug(f"读取 {sid} 历史失败: {exc}")
                continue
            if not isinstance(msgs, list):
                continue
            stream_label = str(stream.get("group_id") or stream.get("user_id") or sid[:8])
            for msg in msgs:
                line = self._format_history_line(msg, stream_label)
                if line:
                    all_lines.append(line)

        # 截顶并合并
        if not all_lines:
            return ""
        return "\n".join(all_lines[-limit:])

    async def load_relevant_knowledge(
        self,
        plugin: Any,
        query_hint: str,
        limit: int,
    ) -> str:
        """从知识库拉取角色 / 日常相关记忆。

        Args:
            plugin: 当前插件实例。
            query_hint: 额外查询线索（通常是日期字符串）。
            limit: 最大结果条数；<=0 直接返回空串。

        Returns:
            知识库返回的可读字符串；失败返回空串。
        """
        if limit <= 0 or plugin is None:
            return ""
        query = f"日常生活|角色习惯|{query_hint}".strip("|")
        try:
            result = await plugin.ctx.knowledge.search(query, limit=limit)
        except Exception as exc:
            logger.debug(f"知识库检索失败: {exc}")
            return ""
        return str(result or "").strip()

    @staticmethod
    def _format_history_line(msg: Dict[str, Any], stream_label: str) -> str:
        """把单条消息 dict 格式化成可读行；无效消息返回空串。"""
        if not isinstance(msg, dict):
            return ""
        text = str(msg.get("processed_plain_text") or "").strip()
        if not text:
            raw = msg.get("raw_message")
            if isinstance(raw, dict):
                parts: List[str] = []
                comps = raw.get("components")
                if isinstance(comps, list):
                    for c in comps:
                        if isinstance(c, dict):
                            piece = str(c.get("text") or c.get("content") or "").strip()
                            if piece:
                                parts.append(piece)
                text = " ".join(parts)
        if not text:
            return ""
        if len(text) > MAX_HISTORY_LINE_CHARS:
            text = text[:MAX_HISTORY_LINE_CHARS] + "…"

        time_str = ""
        ts = msg.get("timestamp")
        try:
            time_str = datetime.fromtimestamp(float(ts)).strftime("%H:%M")
        except (TypeError, ValueError, OSError):
            time_str = ""

        info = msg.get("message_info") if isinstance(msg.get("message_info"), dict) else {}
        user_info = info.get("user_info") if isinstance(info.get("user_info"), dict) else {}
        nick = (
            str(user_info.get("user_cardname") or "").strip()
            or str(user_info.get("user_nickname") or "").strip()
            or str(user_info.get("user_id") or "").strip()
            or "用户"
        )

        prefix = f"[{time_str}] " if time_str else ""
        return f"{prefix}{nick}@{stream_label}: {text}"
