"""
目标管理器
管理麦麦的长期目标、任务和计划
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from enum import Enum
from pathlib import Path

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.goal_manager")


class GoalStatus(Enum):
    """目标状态"""
    ACTIVE = "active"        # 活跃中
    PAUSED = "paused"        # 已暂停
    COMPLETED = "completed"  # 已完成
    CANCELLED = "cancelled"  # 已取消
    FAILED = "failed"        # 已失败


class GoalPriority(Enum):
    """目标优先级"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Goal:
    """目标类"""

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
        """转换为字典"""
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
        """从字典创建"""
        # 转换时间字符串
        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
        deadline = datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None
        last_executed_at = datetime.fromisoformat(data["last_executed_at"]) if data.get("last_executed_at") else None

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

    def should_execute_now(self) -> bool:
        """判断是否应该执行"""
        if self.status != GoalStatus.ACTIVE:
            return False

        # 如果有执行间隔，检查是否到时间
        if self.interval_seconds and self.last_executed_at:
            next_execution = self.last_executed_at + timedelta(seconds=self.interval_seconds)
            if datetime.now() < next_execution:
                return False

        # 检查截止时间
        if self.deadline and datetime.now() > self.deadline:
            return False

        return True

    def mark_executed(self):
        """标记为已执行"""
        self.last_executed_at = datetime.now()
        self.execution_count += 1

    def get_summary(self) -> str:
        """获取目标摘要"""
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
    """目标管理器"""

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.goals_file = self.data_dir / "goals.json"
        self.goals: Dict[str, Goal] = {}

        self._load_goals()

    def _load_goals(self):
        """从文件加载目标"""
        if self.goals_file.exists():
            try:
                with open(self.goals_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for goal_data in data:
                        goal = Goal.from_dict(goal_data)
                        self.goals[goal.goal_id] = goal
                logger.info(f"加载了 {len(self.goals)} 个目标")
            except Exception as e:
                logger.error(f"加载目标失败: {e}", exc_info=True)

    def _save_goals(self):
        """保存目标到文件"""
        try:
            data = [goal.to_dict() for goal in self.goals.values()]
            with open(self.goals_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"保存了 {len(self.goals)} 个目标")
        except Exception as e:
            logger.error(f"保存目标失败: {e}", exc_info=True)

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
        auto_save: bool = True,  # 新增参数：是否自动保存
    ) -> Goal:
        """创建新目标"""
        goal_id = str(uuid.uuid4())

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

        self.goals[goal_id] = goal

        if auto_save:
            self._save_goals()
            logger.info(f"创建了新目标: {name} (ID: {goal_id})")
        else:
            logger.debug(f"创建了新目标（未保存）: {name} (ID: {goal_id})")

        return goal

    def create_goals_batch(
        self,
        goals_data: List[Dict[str, Any]]
    ) -> List[Goal]:
        """
        批量创建目标（只保存一次）

        Args:
            goals_data: 目标数据列表，每个字典包含create_goal的参数

        Returns:
            创建的Goal对象列表
        """
        created_goals = []

        for data in goals_data:
            # 强制不自动保存
            data['auto_save'] = False
            goal = self.create_goal(**data)
            created_goals.append(goal)

        # 统一保存一次
        self._save_goals()
        logger.info(f"批量创建了 {len(created_goals)} 个目标")

        return created_goals

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """获取目标"""
        return self.goals.get(goal_id)

    def get_all_goals(self, chat_id: Optional[str] = None, status: Optional[GoalStatus] = None) -> List[Goal]:
        """获取所有目标"""
        goals = list(self.goals.values())

        if chat_id:
            goals = [g for g in goals if g.chat_id == chat_id]

        if status:
            goals = [g for g in goals if g.status == status]

        return goals

    def get_active_goals(self, chat_id: Optional[str] = None) -> List[Goal]:
        """获取活跃的目标"""
        return self.get_all_goals(chat_id=chat_id, status=GoalStatus.ACTIVE)

    def get_executable_goals(self) -> List[Goal]:
        """获取可以执行的目标"""
        active_goals = self.get_active_goals()
        return [g for g in active_goals if g.should_execute_now()]

    def update_goal(
        self,
        goal_id: str,
        **kwargs
    ) -> bool:
        """更新目标"""
        goal = self.goals.get(goal_id)
        if not goal:
            return False

        # 更新字段
        for key, value in kwargs.items():
            if hasattr(goal, key):
                setattr(goal, key, value)

        self._save_goals()
        logger.info(f"更新了目标: {goal_id}")
        return True

    def update_goal_status(self, goal_id: str, status: GoalStatus) -> bool:
        """更新目标状态"""
        return self.update_goal(goal_id, status=status)

    def update_goal_progress(self, goal_id: str, progress: int) -> bool:
        """更新目标进度"""
        progress = max(0, min(100, progress))  # 限制在 0-100
        return self.update_goal(goal_id, progress=progress)

    def complete_goal(self, goal_id: str) -> bool:
        """完成目标"""
        return self.update_goal(goal_id, status=GoalStatus.COMPLETED, progress=100)

    def pause_goal(self, goal_id: str) -> bool:
        """暂停目标"""
        return self.update_goal_status(goal_id, GoalStatus.PAUSED)

    def resume_goal(self, goal_id: str) -> bool:
        """恢复目标"""
        return self.update_goal_status(goal_id, GoalStatus.ACTIVE)

    def cancel_goal(self, goal_id: str) -> bool:
        """取消目标"""
        return self.update_goal_status(goal_id, GoalStatus.CANCELLED)

    def delete_goal(self, goal_id: str) -> bool:
        """删除目标"""
        if goal_id in self.goals:
            del self.goals[goal_id]
            self._save_goals()
            logger.info(f"删除了目标: {goal_id}")
            return True
        return False

    def cleanup_old_goals(self, days: int = 30) -> int:
        """
        清理旧的已完成/已取消目标

        Args:
            days: 保留最近N天的目标，默认30天

        Returns:
            清理的目标数量
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        to_delete = []

        for goal_id, goal in self.goals.items():
            # 只清理已完成或已取消的目标
            if goal.status in [GoalStatus.COMPLETED, GoalStatus.CANCELLED]:
                # 检查创建时间是否超过保留期限
                if goal.created_at and goal.created_at < cutoff_date:
                    to_delete.append(goal_id)

        # 执行删除
        for goal_id in to_delete:
            del self.goals[goal_id]

        if to_delete:
            self._save_goals()
            logger.info(f"🧹 清理了 {len(to_delete)} 个旧目标（{days}天前）")

        return len(to_delete)

    def mark_goal_executed(self, goal_id: str):
        """标记目标已执行"""
        goal = self.goals.get(goal_id)
        if goal:
            goal.mark_executed()
            self._save_goals()

    def get_goals_summary(self, chat_id: Optional[str] = None) -> str:
        """获取目标摘要"""
        goals = self.get_all_goals(chat_id=chat_id)

        if not goals:
            return "📋 当前没有任何目标"

        # 按状态分组
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
            for goal in paused[:3]:  # 只显示前3个
                lines.append(f"   - {goal.name}")

        if completed:
            lines.append(f"\n✅ 已完成 ({len(completed)}个)")

        return "\n".join(lines)


# 全局单例
_goal_manager: Optional[GoalManager] = None


def get_goal_manager() -> GoalManager:
    """获取全局目标管理器实例"""
    global _goal_manager
    if _goal_manager is None:
        _goal_manager = GoalManager()
    return _goal_manager
