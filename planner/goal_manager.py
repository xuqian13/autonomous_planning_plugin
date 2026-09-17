"""Goal Manager Module (SQLite Version).

This module provides goal management functionality using SQLite database
for improved performance and concurrency handling.

Classes:
    GoalStatus: Enumeration of possible goal statuses
    GoalPriority: Enumeration of goal priority levels
    Goal: Represents a single goal with tracking information
    GoalManager: Manages all goals with SQLite persistence

Improvements over JSON version:
    - Better concurrency (SQLite built-in locking)
    - ACID transactions (no data corruption)
    - Faster queries
    - No manual file locking needed
    - Automatic cleanup and optimization

Example:
    >>> from goal_manager import get_goal_manager, GoalPriority
    >>> manager = get_goal_manager()
    >>> goal = manager.create_goal(
    ...     name="Daily Exercise",
    ...     description="Exercise for 30 minutes",
    ...     goal_type="health",
    ...     creator_id="user123",
    ...     chat_id="chat456",
    ...     priority="high"
    ... )
"""

from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import logging
import uuid

from ..database import GoalDatabase
from ..utils.time_utils import strip_tz
from ..utils.timezone_manager import TimezoneManager

logger = logging.getLogger(__name__)


# 支持的日期格式（按优先级尝试）：
#   "2026-05-26" / "2026/05/26" / "20260526" / "2026-05-26T10:30:00" / "2026-05-26 10:30:00"
_COMMITMENT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _normalize_commitment_date(raw: str, today: datetime) -> str:
    """把任意输入归一化为 ``YYYY-MM-DD``。

    解析失败时记 warning 并回退为 ``today``，保证调用方拿到的总是合法日期字符串
    （而不是写库存歧义格式，导致 get/consume 漏匹配）。

    Args:
        raw: 调用方传入的日期字符串（可能是 ISO / 含时间 / 斜杠分隔等）。
        today: 解析失败时的兜底日期（仅取日期部分）。

    Returns:
        ``YYYY-MM-DD`` 格式的字符串。
    """
    text = str(raw or "").strip()
    if not text:
        return today.strftime("%Y-%m-%d")
    # 已经是规范格式，直接返回
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        pass
    # ISO 8601 含 tz 后缀（``2026-05-26T10:30:00+08:00``）
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d")
    except ValueError:
        pass
    # 其它备选格式
    for fmt in _COMMITMENT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.warning(f"commitment_date 无法解析: {raw!r}，回退为今天 {today.strftime('%Y-%m-%d')}")
    return today.strftime("%Y-%m-%d")


class GoalStatus(Enum):
    """Goal status enumeration."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class GoalPriority(Enum):
    """Goal priority enumeration."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Goal:
    """Goal class representing a single goal.

    Attributes:
        goal_id: Unique identifier
        name: Goal name
        description: Detailed description
        goal_type: Type of goal
        priority: Priority level
        creator_id: User who created the goal
        chat_id: Chat where goal was created
        status: Current status
        created_at: Creation timestamp
        deadline: Optional deadline
        conditions: Execution conditions
        parameters: Goal parameters (includes time_window for schedule goals)
        progress: Progress percentage (0-100)
        last_executed_at: Last execution timestamp
        execution_count: Number of executions
    """

    def __init__(
        self,
        goal_id: str,
        name: str,
        description: str,
        goal_type: str,
        priority: GoalPriority,
        creator_id: str,
        chat_id: str,
        status: GoalStatus = GoalStatus.ACTIVE,
        created_at: Optional[datetime] = None,
        deadline: Optional[datetime] = None,
        conditions: Optional[Dict[str, Any]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        progress: int = 0,
        last_executed_at: Optional[datetime] = None,
        execution_count: int = 0,
        **kwargs,  # 忽略旧字段如 interval_seconds
    ):
        self.goal_id = goal_id
        self.name = name
        self.description = description
        self.goal_type = goal_type
        self.priority = priority if isinstance(priority, GoalPriority) else GoalPriority(priority)
        self.creator_id = creator_id
        self.chat_id = chat_id
        self.status = status if isinstance(status, GoalStatus) else GoalStatus(status)
        # 使用时区感知时间（向后兼容）
        if created_at is None:
            tz_manager = TimezoneManager()  # 使用默认时区
            created_at = tz_manager.get_now()
        self.created_at = created_at
        self.deadline = deadline
        self.conditions = conditions or {}
        self.parameters = parameters or {}
        self.progress = progress
        self.last_executed_at = last_executed_at
        self.execution_count = execution_count

    def to_dict(self) -> Dict[str, Any]:
        """Convert goal to dictionary.

        Returns:
            Dictionary representation of goal
        """
        return {
            "goal_id": self.goal_id,
            "name": self.name,
            "description": self.description,
            "goal_type": self.goal_type,
            "priority": self.priority.value,
            "creator_id": self.creator_id,
            "chat_id": self.chat_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "conditions": self.conditions,
            "parameters": self.parameters,
            "progress": self.progress,
            "last_executed_at": self.last_executed_at.isoformat() if self.last_executed_at else None,
            "execution_count": self.execution_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        """Create goal from dictionary.

        Args:
            data: Dictionary containing goal data

        Returns:
            Goal instance
        """
        # Parse datetime fields
        created_at = cls._parse_datetime(data.get("created_at"))
        deadline = cls._parse_datetime(data.get("deadline"))
        last_executed_at = cls._parse_datetime(data.get("last_executed_at"))

        return cls(
            goal_id=data["goal_id"],
            name=data["name"],
            description=data["description"],
            goal_type=data["goal_type"],
            priority=data["priority"],
            creator_id=data["creator_id"],
            chat_id=data["chat_id"],
            status=data.get("status", "active"),
            created_at=created_at,
            deadline=deadline,
            conditions=data.get("conditions", {}),
            parameters=data.get("parameters", {}),
            progress=data.get("progress", 0),
            last_executed_at=last_executed_at,
            execution_count=data.get("execution_count", 0),
        )

    @staticmethod
    def _parse_datetime(dt_str):
        """Parse datetime string.

        Args:
            dt_str: ISO format datetime string

        Returns:
            datetime object or None
        """
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None

    def should_execute_now(self) -> bool:
        """Check if goal should be executed now.

        Returns:
            True if goal should be executed, False otherwise
        """
        if self.status != GoalStatus.ACTIVE:
            return False

        # Check time_window if present
        time_window = self.parameters.get("time_window") if self.parameters else None
        if time_window and isinstance(time_window, list) and len(time_window) == 2:
            tz_manager = TimezoneManager()
            now = tz_manager.get_now()
            current_minutes = now.hour * 60 + now.minute
            if not (time_window[0] <= current_minutes <= time_window[1]):
                return False

        # Check deadline
        tz_manager = TimezoneManager()
        if self.deadline and strip_tz(tz_manager.get_now()) > strip_tz(self.deadline):
            return False

        return True

    def mark_executed(self):
        """Mark goal as executed."""
        tz_manager = TimezoneManager()
        self.last_executed_at = tz_manager.get_now()
        self.execution_count += 1

    def get_summary(self) -> str:
        """Get goal summary.

        Returns:
            Formatted summary string
        """
        status_emoji = {
            GoalStatus.ACTIVE: "🟢",
            GoalStatus.PAUSED: "⏸️",
            GoalStatus.COMPLETED: "✅",
            GoalStatus.CANCELLED: "❌",
            GoalStatus.FAILED: "💔",
        }

        priority_emoji = {
            GoalPriority.HIGH: "🔴",
            GoalPriority.MEDIUM: "🟡",
            GoalPriority.LOW: "🟢",
        }

        lines = [
            f"{status_emoji[self.status]} 目标: {self.name}",
            f"   ID: {self.goal_id[:8]}...",
            f"   聊天流: {self.chat_id}",
            f"   优先级: {priority_emoji[self.priority]} {self.priority.value}",
            f"   进度: {self.progress}%",
            f"   执行次数: {self.execution_count}",
        ]

        if self.deadline:
            tz_manager = TimezoneManager()
            time_left = strip_tz(self.deadline) - strip_tz(tz_manager.get_now())
            if time_left.total_seconds() > 0:
                days = time_left.days
                hours = time_left.seconds // 3600
                lines.append(f"   剩余时间: {days}天{hours}小时")
            else:
                lines.append(f"   ⚠️ 已超期")

        # 显示时间窗口
        time_window = self.parameters.get("time_window") if self.parameters else None
        if time_window and isinstance(time_window, list) and len(time_window) == 2:
            start_h, start_m = divmod(time_window[0], 60)
            end_h, end_m = divmod(time_window[1], 60)
            lines.append(f"   时间: {start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}")

        return "\n".join(lines)


class GoalManager:
    """Goal manager using SQLite database.

    Simplified version that delegates all persistence to GoalDatabase.
    No need for manual file locking, delayed saves, or backup management
    as SQLite handles all of this natively.

    Args:
        data_dir: Directory for database file (default: plugin_dir/data)
        db_name: Database file name (default: goals.db)
    """

    def __init__(self, data_dir: str = None, db_name: str = "goals.db"):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        db_path = self.data_dir / db_name
        self.db = GoalDatabase(db_path=str(db_path), backup_on_init=True)

        # Initialize timezone manager
        self.tz_manager = TimezoneManager()

        logger.debug(f"GoalManager initialized with database: {db_path}")

    def create_goal(
        self,
        name: str,
        description: str,
        goal_type: str,
        creator_id: str,
        chat_id: str,
        priority: str = "medium",
        deadline: Optional[datetime] = None,
        conditions: Optional[Dict[str, Any]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        auto_save: bool = True,  # Kept for compatibility, always saves immediately
        **kwargs,  # 忽略旧字段如 interval_seconds
    ) -> Goal:
        """Create a new goal.

        Args:
            name: Goal name
            description: Goal description
            goal_type: Type of goal
            creator_id: User who creates the goal
            chat_id: Chat identifier
            priority: Priority level (high/medium/low)
            deadline: Optional deadline
            conditions: Execution conditions
            parameters: Goal parameters (use time_window for schedule)
            auto_save: Compatibility parameter (ignored, always saves)

        Returns:
            Created Goal object
        """
        goal_id = str(uuid.uuid4())

        # Create goal in database
        self.db.create_goal(
            goal_id=goal_id,
            name=name,
            description=description,
            goal_type=goal_type,
            priority=priority,
            creator_id=creator_id,
            chat_id=chat_id,
            deadline=deadline,
            conditions=conditions,
            parameters=parameters,
        )

        # Return Goal object
        goal = Goal(
            goal_id=goal_id,
            name=name,
            description=description,
            goal_type=goal_type,
            priority=GoalPriority(priority),
            creator_id=creator_id,
            chat_id=chat_id,
            deadline=deadline,
            conditions=conditions,
            parameters=parameters,
        )

        logger.debug(f"Created goal: {name} (ID: {goal_id})")
        return goal

    def create_goals_batch(self, goals_data: List[Dict[str, Any]]) -> List[Goal]:
        """Batch create goals.

        Args:
            goals_data: List of goal data dictionaries

        Returns:
            List of created Goal objects

        Raises:
            Exception: If batch creation fails
        """
        created_goals = []

        try:
            for data in goals_data:
                # Remove auto_save parameter if present
                data.pop('auto_save', None)

                goal = self.create_goal(**data)
                created_goals.append(goal)

            logger.info(f"Batch created {len(created_goals)} goals")
            return created_goals

        except Exception as e:
            logger.error(f"Batch creation failed: {e}", exc_info=True)
            raise

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """Get goal by ID.

        Args:
            goal_id: Goal identifier

        Returns:
            Goal object or None if not found
        """
        data = self.db.get_goal(goal_id)
        if data:
            return Goal.from_dict(data)
        return None

    def get_all_goals(
        self,
        chat_id: Optional[str] = None,
        status: Optional[GoalStatus] = None
    ) -> List[Goal]:
        """Get all goals with optional filtering.

        Args:
            chat_id: Filter by chat ID
            status: Filter by status

        Returns:
            List of Goal objects
        """
        status_str = status.value if status else None
        goals_data = self.db.get_all_goals(chat_id=chat_id, status=status_str)

        return [Goal.from_dict(data) for data in goals_data]

    def get_active_goals(self, chat_id: Optional[str] = None) -> List[Goal]:
        """Get active goals.

        Args:
            chat_id: Optional chat ID filter

        Returns:
            List of active Goal objects
        """
        return self.get_all_goals(chat_id=chat_id, status=GoalStatus.ACTIVE)

    def get_executable_goals(self) -> List[Goal]:
        """Get goals that should be executed now.

        Returns:
            List of executable Goal objects
        """
        active_goals = self.get_active_goals()
        return [g for g in active_goals if g.should_execute_now()]

    # ============================================================
    # pending_commitments：未来约定（goal_type="pending_commitment"）
    # ============================================================
    #
    # 复用 goals 表存储，用 goal_type 区分；parameters JSON 中含：
    #   commitment_date: "YYYY-MM-DD"
    #   time: "HH:MM" 或 ""
    #   notes: 描述备注
    #   reason: 角色裁判给出的接受理由
    #   source: "user_request" / "auto" 等
    # 消费时 status 改为 COMPLETED，不删除（保留审计）。

    def add_pending_commitment(
        self,
        commitment_date: str,
        title: str,
        *,
        time: str = "",
        notes: str = "",
        reason: str = "",
        creator_id: str = "system",
    ) -> Goal:
        """添加一条未来约定。

        Args:
            commitment_date: 约定日期，``YYYY-MM-DD`` 格式。
            title: 约定标题（对应 Goal.name）。
            time: 可选的时间（HH:MM）。
            notes: 可选的备注。
            reason: 可选的角色裁判理由。
            creator_id: 创建者 ID。

        Returns:
            新创建的 Goal 对象。
        """
        # 防御性规范化：调用方传入的格式可能不统一（"2026/05/26"、ISO datetime、
        # 仅含时间分量等），与 get/consume 保持同一比较口径
        normalized_date = _normalize_commitment_date(commitment_date, self.tz_manager.get_now())
        parameters = {
            "commitment_date": normalized_date,
            "time": time,
            "notes": notes,
            "reason": reason,
            "source": "user_request",
        }
        return self.create_goal(
            name=title,
            description=notes or title,
            goal_type="pending_commitment",
            creator_id=creator_id,
            chat_id="global",
            priority="medium",
            parameters=parameters,
        )

    def get_pending_commitments(
        self,
        commitment_date: Optional[str] = None,
    ) -> List[Goal]:
        """查询未来约定。

        Args:
            commitment_date: 指定日期；留空 = 今天。

        Returns:
            匹配的 pending_commitment 列表（仅返回 status=active 的）。
        """
        if commitment_date is None:
            commitment_date = self.tz_manager.get_now().strftime("%Y-%m-%d")
        else:
            # 输入也走规范化，避免和写入时格式不一致漏匹配
            commitment_date = _normalize_commitment_date(commitment_date, self.tz_manager.get_now())

        candidates = self.get_all_goals(chat_id="global", status=GoalStatus.ACTIVE)
        return [
            g for g in candidates
            if g.goal_type == "pending_commitment"
            and (g.parameters or {}).get("commitment_date") == commitment_date
        ]

    def consume_pending_commitments(
        self,
        commitment_date: str,
    ) -> List[Goal]:
        """消费指定日期的全部未消费约定（标记为 COMPLETED）。

        Args:
            commitment_date: 目标日期。

        Returns:
            被消费的约定列表。
        """
        consumed = self.get_pending_commitments(commitment_date)
        for goal in consumed:
            self.db.update_goal(goal.goal_id, status=GoalStatus.COMPLETED.value)
        if consumed:
            logger.info(f"已消费 {len(consumed)} 条 {commitment_date} 的未来约定")
        return consumed

    def get_schedule_goals(
        self,
        chat_id: str = "global",
        date_str: Optional[str] = None,
        include_yesterday: bool = False,
    ) -> List[Goal]:
        """Get schedule goals (goals with time_window).

        Args:
            chat_id: Chat identifier (default: "global")
            date_str: Date string (YYYY-MM-DD, default: today)
            include_yesterday: 同时返回昨日创建的 schedule goals（供 inject_service
                识别跨夜活动）。

        Returns:
            List of schedule Goal objects
        """
        if date_str is None:
            date_str = self.tz_manager.get_now().strftime("%Y-%m-%d")

        target_dates = {date_str}
        if include_yesterday:
            try:
                base = datetime.strptime(date_str, "%Y-%m-%d")
                target_dates.add((base - timedelta(days=1)).strftime("%Y-%m-%d"))
            except (TypeError, ValueError):
                pass

        goals = self.get_all_goals(chat_id=chat_id)
        schedule_goals = []

        for goal in goals:
            # 跳过 pending_commitment 类型，避免污染日程列表
            if goal.goal_type == "pending_commitment":
                continue
            # Check for time_window in parameters or conditions
            has_time_window = False
            if goal.parameters and "time_window" in goal.parameters:
                has_time_window = True
            elif goal.conditions and "time_window" in goal.conditions:
                has_time_window = True

            if has_time_window:
                # Check creation date
                goal_date = None
                if goal.created_at:
                    try:
                        goal_date = goal.created_at.strftime("%Y-%m-%d")
                    except Exception:
                        pass

                # Only return goals for specified dates
                if goal_date in target_dates:
                    schedule_goals.append(goal)

        # 去重：按 (name, time_window) 去重，防止重复日程显示
        # 如果有多个相同名称和时间的日程，只保留第一个（通常是最先创建的）
        if schedule_goals:
            seen = set()
            unique_goals = []

            for goal in schedule_goals:
                # 提取time_window作为唯一性标识的一部分
                time_window = None
                if goal.parameters and "time_window" in goal.parameters:
                    time_window = tuple(goal.parameters["time_window"])
                elif goal.conditions and "time_window" in goal.conditions:
                    time_window = tuple(goal.conditions["time_window"])

                # 使用 (name, time_window) 作为唯一键
                key = (goal.name, time_window)

                if key not in seen:
                    seen.add(key)
                    unique_goals.append(goal)
                else:
                    logger.debug(f"跳过重复日程: {goal.name} @ {time_window}")

            if len(unique_goals) < len(schedule_goals):
                logger.debug(f"去重：{len(schedule_goals)} -> {len(unique_goals)} 个日程")

            return unique_goals

        return schedule_goals

    def update_goal(self, goal_id: str, **kwargs) -> bool:
        """Update goal fields.

        Args:
            goal_id: Goal identifier
            **kwargs: Fields to update

        Returns:
            True if updated, False if not found
        """
        return self.db.update_goal(goal_id, **kwargs)

    def update_goal_status(self, goal_id: str, status: GoalStatus) -> bool:
        """Update goal status.

        Args:
            goal_id: Goal identifier
            status: New status

        Returns:
            True if updated, False if not found
        """
        return self.update_goal(goal_id, status=status.value)

    def update_goal_progress(self, goal_id: str, progress: int) -> bool:
        """Update goal progress.

        Args:
            goal_id: Goal identifier
            progress: Progress percentage (0-100)

        Returns:
            True if updated, False if not found
        """
        progress = max(0, min(100, progress))
        return self.update_goal(goal_id, progress=progress)

    def complete_goal(self, goal_id: str) -> bool:
        """Mark goal as completed.

        Args:
            goal_id: Goal identifier

        Returns:
            True if completed, False if not found
        """
        return self.update_goal(goal_id, status=GoalStatus.COMPLETED.value, progress=100)

    def pause_goal(self, goal_id: str) -> bool:
        """Pause goal.

        Args:
            goal_id: Goal identifier

        Returns:
            True if paused, False if not found
        """
        return self.update_goal_status(goal_id, GoalStatus.PAUSED)

    def resume_goal(self, goal_id: str) -> bool:
        """Resume paused goal.

        Args:
            goal_id: Goal identifier

        Returns:
            True if resumed, False if not found
        """
        return self.update_goal_status(goal_id, GoalStatus.ACTIVE)

    def cancel_goal(self, goal_id: str) -> bool:
        """Cancel goal.

        Args:
            goal_id: Goal identifier

        Returns:
            True if cancelled, False if not found
        """
        return self.update_goal_status(goal_id, GoalStatus.CANCELLED)

    def delete_goal(self, goal_id: str) -> bool:
        """Delete goal.

        Args:
            goal_id: Goal identifier

        Returns:
            True if deleted, False if not found
        """
        deleted = self.db.delete_goal(goal_id)
        if deleted:
            logger.debug(f"Deleted goal: {goal_id}")
        return deleted

    def cleanup_old_goals(self, days: int = 30) -> int:
        """Clean up old completed/cancelled goals.

        Args:
            days: Keep goals from last N days (default: 30)

        Returns:
            Number of goals cleaned up
        """
        cutoff_date = self.tz_manager.get_now() - timedelta(days=days)

        # Delete old completed goals
        completed_count = self.db.delete_goals_by_status(
            status=GoalStatus.COMPLETED.value,
            older_than=cutoff_date
        )

        # Delete old cancelled goals
        cancelled_count = self.db.delete_goals_by_status(
            status=GoalStatus.CANCELLED.value,
            older_than=cutoff_date
        )

        total = completed_count + cancelled_count

        if total > 0:
            logger.info(f"Cleaned up {total} old goals (older than {days} days)")

        return total

    def cleanup_expired_schedules(self) -> int:
        """Clean up expired schedule goals (yesterday and older).

        自动清理昨天及更早的日程目标，防止过期日程累积。

        策略：
        - 只清理有time_window的目标（日程类型）
        - 只清理创建日期在今天之前的
        - 将它们标记为COMPLETED而非直接删除

        Returns:
            Number of schedule goals cleaned up
        """
        today_str = self.tz_manager.get_now().strftime("%Y-%m-%d")

        # 获取所有ACTIVE状态的目标
        active_goals = self.get_all_goals(status=GoalStatus.ACTIVE)

        expired_count = 0
        for goal in active_goals:
            # 检查是否为日程类型（有time_window）
            has_time_window = False
            if goal.parameters and "time_window" in goal.parameters:
                has_time_window = True
            elif goal.conditions and "time_window" in goal.conditions:
                has_time_window = True

            if not has_time_window:
                continue  # 跳过非日程类型的目标

            # 检查创建日期
            if goal.created_at:
                goal_date = goal.created_at.strftime("%Y-%m-%d")
                if goal_date < today_str:
                    # 这是昨天或更早的日程，标记为完成
                    self.update_goal_status(goal.goal_id, GoalStatus.COMPLETED)
                    expired_count += 1
                    logger.debug(f"Marked expired schedule as completed: {goal.name} (created: {goal_date})")

        if expired_count > 0:
            logger.info(f"🧹 清理了 {expired_count} 个过期日程（昨天及更早）")

        return expired_count

    def mark_goal_executed(self, goal_id: str):
        """Mark goal as executed.

        Args:
            goal_id: Goal identifier
        """
        goal = self.get_goal(goal_id)
        if goal:
            goal.mark_executed()
            self.update_goal(
                goal_id,
                last_executed_at=goal.last_executed_at,
                execution_count=goal.execution_count
            )

    def get_goals_summary(self, chat_id: Optional[str] = None) -> str:
        """Get goals summary.

        Args:
            chat_id: Optional chat ID filter

        Returns:
            Formatted summary string
        """
        goals = self.get_all_goals(chat_id=chat_id)

        if not goals:
            return "📋 当前没有任何目标"

        # Group by status
        active = [g for g in goals if g.status == GoalStatus.ACTIVE]
        paused = [g for g in goals if g.status == GoalStatus.PAUSED]
        completed = [g for g in goals if g.status == GoalStatus.COMPLETED]

        lines = [f"📋 目标总览 (共 {len(goals)} 个)\n"]

        if active:
            lines.append(f"🟢 活跃目标 ({len(active)}个):")
            # 🐛 修复：按优先级排序（high > medium > low）
            priority_order = {"high": 0, "medium": 1, "low": 2}
            for goal in sorted(active, key=lambda g: priority_order.get(g.priority.value, 1)):
                lines.append(goal.get_summary())
                lines.append("")

        if paused:
            lines.append(f"\n⏸️ 暂停目标 ({len(paused)}个):")
            for goal in paused[:3]:  # Show first 3
                lines.append(f"   - {goal.name}")

        if completed:
            lines.append(f"\n✅ 已完成 ({len(completed)}个)")

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics.

        Returns:
            Dictionary with statistics
        """
        return self.db.get_stats()

    def vacuum(self):
        """Optimize database (should be run periodically)."""
        self.db.vacuum()
        logger.info("Database optimized")

    def close(self):
        """Close database connection."""
        self.db.close()


# Global singleton
_goal_manager: Optional[GoalManager] = None


def get_goal_manager(data_dir: Optional[Union[str, Path]] = None) -> GoalManager:
    """Get global goal manager instance.

    Args:
        data_dir: 持久化目录。仅在**首次**创建单例时生效；传入 ``None`` 时沿用
            ``GoalManager`` 的默认值（插件源码目录下的 ``data/``）。插件
            ``on_load`` 会用 Host 分配的统一目录调用一次，以固定单例的数据位置。

    Returns:
        GoalManager singleton instance
    """
    global _goal_manager
    if _goal_manager is None:
        _goal_manager = GoalManager(data_dir=str(data_dir) if data_dir else None)
    return _goal_manager


def close_goal_manager() -> None:
    """关闭并释放全局 GoalManager 单例所持有的数据库连接。

    供插件 ``on_unload`` 调用，避免长时间运行后线程本地连接累积。
    再次调用 ``get_goal_manager()`` 会重建实例。
    """
    global _goal_manager
    if _goal_manager is not None:
        try:
            _goal_manager.db.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"关闭目标数据库连接异常: {exc}")
        _goal_manager = None
