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

import uuid
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.common.logger import get_logger

from ..database import GoalDatabase

logger = get_logger("autonomous_planning.goal_manager")


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
        interval_seconds: Execution interval
        conditions: Execution conditions
        parameters: Goal parameters
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
        interval_seconds: Optional[int] = None,
        conditions: Optional[Dict[str, Any]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        progress: int = 0,
        last_executed_at: Optional[datetime] = None,
        execution_count: int = 0,
    ):
        self.goal_id = goal_id
        self.name = name
        self.description = description
        self.goal_type = goal_type
        self.priority = priority if isinstance(priority, GoalPriority) else GoalPriority(priority)
        self.creator_id = creator_id
        self.chat_id = chat_id
        self.status = status if isinstance(status, GoalStatus) else GoalStatus(status)
        self.created_at = created_at or datetime.now()
        self.deadline = deadline
        self.interval_seconds = interval_seconds
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
            "interval_seconds": self.interval_seconds,
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
            interval_seconds=data.get("interval_seconds"),
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

        # Check execution interval
        if self.interval_seconds and self.last_executed_at:
            next_execution = self.last_executed_at + timedelta(seconds=self.interval_seconds)
            if datetime.now() < next_execution:
                return False

        # Check deadline
        if self.deadline and datetime.now() > self.deadline:
            return False

        return True

    def mark_executed(self):
        """Mark goal as executed."""
        self.last_executed_at = datetime.now()
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
            time_left = self.deadline - datetime.now()
            if time_left.total_seconds() > 0:
                days = time_left.days
                hours = time_left.seconds // 3600
                lines.append(f"   剩余时间: {days}天{hours}小时")
            else:
                lines.append(f"   ⚠️ 已超期")

        if self.interval_seconds:
            hours = self.interval_seconds // 3600
            minutes = (self.interval_seconds % 3600) // 60
            if hours > 0:
                lines.append(f"   周期: 每{hours}小时{minutes}分钟")
            else:
                lines.append(f"   周期: 每{minutes}分钟")

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

        logger.info(f"GoalManager initialized with database: {db_path}")

    def create_goal(
        self,
        name: str,
        description: str,
        goal_type: str,
        creator_id: str,
        chat_id: str,
        priority: str = "medium",
        deadline: Optional[datetime] = None,
        interval_seconds: Optional[int] = None,
        conditions: Optional[Dict[str, Any]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        auto_save: bool = True,  # Kept for compatibility, always saves immediately
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
            interval_seconds: Execution interval in seconds
            conditions: Execution conditions
            parameters: Goal parameters
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
            interval_seconds=interval_seconds,
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
            interval_seconds=interval_seconds,
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

    def get_schedule_goals(
        self,
        chat_id: str = "global",
        date_str: Optional[str] = None
    ) -> List[Goal]:
        """Get schedule goals (goals with time_window).

        Args:
            chat_id: Chat identifier (default: "global")
            date_str: Date string (YYYY-MM-DD, default: today)

        Returns:
            List of schedule Goal objects
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        goals = self.get_all_goals(chat_id=chat_id)
        schedule_goals = []

        for goal in goals:
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

                # Only return goals for specified date
                if goal_date == date_str:
                    schedule_goals.append(goal)

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
        cutoff_date = datetime.now() - timedelta(days=days)

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
            for goal in sorted(active, key=lambda g: g.priority.value):
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


def get_goal_manager() -> GoalManager:
    """Get global goal manager instance.

    Returns:
        GoalManager singleton instance
    """
    global _goal_manager
    if _goal_manager is None:
        _goal_manager = GoalManager()
    return _goal_manager
