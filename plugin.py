"""麦麦自主规划插件 - 让麦麦能够自主管理日程和目标"""

import asyncio
import json
from typing import List, Tuple, Dict, Any, Optional, Union
from datetime import datetime, timedelta
from collections import OrderedDict

from src.plugin_system import (
    BasePlugin,
    BaseTool,
    BaseEventHandler,
    BaseCommand,
    register_plugin,
    ConfigField,
    EventType,
    MaiMessages,
    CustomEventHandlerResult,
)
from src.llm_models.payload_content.tool_option import ToolParamType
from src.common.logger import get_logger

from .planner.goal_manager import get_goal_manager, GoalPriority, GoalStatus
from .planner.schedule_generator import ScheduleGenerator, ScheduleType
from .planner.auto_scheduler import ScheduleAutoScheduler
from .utils.schedule_image_generator import ScheduleImageGenerator
from .utils.time_utils import migrate_time_window, parse_time_window, format_minutes_to_time, get_time_window_from_goal

logger = get_logger("autonomous_planning")


class LRUCache:
    """线程安全的LRU缓存实现"""

    def __init__(self, max_size=100):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.lock = asyncio.Lock()
        self._sync_lock = __import__('threading').Lock()  # P0: 同步方法的线程锁

    async def get(self, key):
        """获取缓存值（异步线程安全）"""
        async with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def get_sync(self, key):
        """获取缓存值（同步版本，线程安全）"""
        with self._sync_lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    async def set(self, key, value):
        """设置缓存值（异步线程安全）"""
        async with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def set_sync(self, key, value):
        """设置缓存值（同步版本，线程安全）"""
        with self._sync_lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            if len(self.cache) > self.max_size:
                self.cache.popitem(last=False)

    def clear(self):
        """清空缓存"""
        self.cache.clear()

    def items(self):
        """返回缓存的所有键值对"""
        return self.cache.items()

    def __delitem__(self, key):
        """删除缓存项"""
        if key in self.cache:
            del self.cache[key]

    def __contains__(self, key):
        """检查键是否存在"""
        return key in self.cache

    def __getitem__(self, key):
        """获取缓存值（同get_sync但不移动到末尾）"""
        return self.cache[key]

    def __setitem__(self, key, value):
        """设置缓存值（支持 cache[key] = value 语法）"""
        self.set_sync(key, value)


class ManageGoalTool(BaseTool):
    """目标管理工具 - 创建、查看、更新和删除目标"""

    name = "manage_goal"
    description = "管理麦麦的长期目标，支持创建、查看、更新、暂停、恢复、完成、取消、删除目标"
    parameters = [
        ("action", ToolParamType.STRING, "操作类型: create(创建)/list(列出)/get(查看)/update(更新)/pause(暂停)/resume(恢复)/complete(完成)/cancel(取消)/delete(删除)", True, None),
        ("goal_id", ToolParamType.STRING, "目标ID (除create和list外都需要)", False, None),
        ("name", ToolParamType.STRING, "目标名称 (create时必需)", False, None),
        ("description", ToolParamType.STRING, "目标描述 (create时必需)", False, None),
        ("goal_type", ToolParamType.STRING, "目标类型: health_check(系统检查/监控/健康检查), social_maintenance(问候/社交), learn_topic(学习/研究主题), custom(其他自定义目标). 根据目标名称和描述智能选择合适的类型", False, None),
        ("priority", ToolParamType.STRING, "优先级: high/medium/low", False, None),
        ("interval_minutes", ToolParamType.FLOAT, "执行间隔（分钟）。例如：2表示每2分钟执行一次，60表示每小时执行一次", False, None),
        ("deadline_hours", ToolParamType.FLOAT, "截止时间（从现在开始的小时数）", False, None),
        ("parameters", ToolParamType.STRING, "目标参数（JSON字符串）。health_check类型建议: {\"check_plugins\": true}; social_maintenance类型建议: {\"greeting_type\": \"morning\"}; learn_topic类型必需: {\"topics\": [\"主题1\", \"主题2\"], \"depth\": \"intermediate\"}", False, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行目标管理操作"""
        try:
            action = function_args.get("action")
            goal_manager = get_goal_manager()
            chat_id = function_args.get("_chat_id", "default")
            user_id = function_args.get("_user_id", "system")

            if action == "create":
                name = function_args.get("name")
                description = function_args.get("description")

                if not name or not description:
                    return {"type": "error", "content": "创建目标需要提供name和description"}

                # P0修复：输入验证 - 长度限制
                if len(name) > 100:
                    return {"type": "error", "content": "目标名称过长（最多100字符）"}
                if len(description) > 500:
                    return {"type": "error", "content": "目标描述过长（最多500字符）"}

                # P0修复：输入验证 - 特殊字符过滤（防注入）
                dangerous_patterns = ["<script>", "{{", "}}", "${", "$(", "`"]
                for pattern in dangerous_patterns:
                    if pattern in name or pattern in description:
                        return {"type": "error", "content": f"输入包含非法字符: {pattern}"}

                goal_type = function_args.get("goal_type", "custom")
                priority = function_args.get("priority", "medium")
                interval_minutes = function_args.get("interval_minutes")
                deadline_hours = function_args.get("deadline_hours")

                # 参数验证
                if interval_minutes is not None:
                    if interval_minutes <= 0:
                        return {"type": "error", "content": "间隔时间必须大于0分钟"}
                    if interval_minutes > 525600:  # 1年
                        return {"type": "error", "content": "间隔时间不能超过1年"}

                if deadline_hours is not None:
                    if deadline_hours <= 0:
                        return {"type": "error", "content": "截止时间必须大于0小时"}
                    if deadline_hours > 87600:  # 10年
                        return {"type": "error", "content": "截止时间不能超过10年"}

                # 解析parameters参数
                parameters_raw = function_args.get("parameters", {})
                if isinstance(parameters_raw, str):
                    try:
                        parameters = json.loads(parameters_raw)
                    except json.JSONDecodeError:
                        logger.warning(f"无法解析参数 JSON: {parameters_raw}")
                        parameters = {}
                elif isinstance(parameters_raw, dict):
                    parameters = parameters_raw
                else:
                    parameters = {}

                # 计算时间
                interval_seconds = int(interval_minutes * 60) if interval_minutes else None
                deadline = datetime.now() + timedelta(hours=deadline_hours) if deadline_hours else None

                goal = goal_manager.create_goal(
                    name=name,
                    description=description,
                    goal_type=goal_type,
                    creator_id=user_id,
                    chat_id=chat_id,
                    priority=priority,
                    deadline=deadline,
                    interval_seconds=interval_seconds,
                    parameters=parameters,
                )

                content = f"""✅ 目标创建成功！

{goal.get_summary()}

麦麦会自动执行这个目标~"""

                return {"type": "goal_created", "id": goal.goal_id, "content": content}

            elif action == "list":
                summary = goal_manager.get_goals_summary(chat_id=chat_id)
                return {"type": "goal_list", "content": summary}

            elif action == "get":
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                goal = goal_manager.get_goal(goal_id)
                if not goal:
                    return {"type": "error", "content": f"目标不存在: {goal_id}"}

                return {"type": "goal_info", "content": goal.get_summary()}

            elif action == "update":
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                update_params = {}
                if "name" in function_args:
                    update_params["name"] = function_args["name"]
                if "description" in function_args:
                    update_params["description"] = function_args["description"]
                if "priority" in function_args:
                    update_params["priority"] = GoalPriority(function_args["priority"])
                if "interval_minutes" in function_args:
                    update_params["interval_seconds"] = int(function_args["interval_minutes"] * 60)
                if "parameters" in function_args:
                    # 处理 parameters：可能是字符串（JSON）或字典
                    parameters_raw = function_args["parameters"]
                    if isinstance(parameters_raw, str):
                        try:
                            update_params["parameters"] = json.loads(parameters_raw)
                        except json.JSONDecodeError:
                            logger.warning(f"无法解析参数 JSON: {parameters_raw}")
                            update_params["parameters"] = {}
                    else:
                        update_params["parameters"] = parameters_raw

                success = goal_manager.update_goal(goal_id, **update_params)

                if success:
                    goal = goal_manager.get_goal(goal_id)
                    if goal:
                        return {"type": "goal_updated", "content": f"✅ 目标已更新\n\n{goal.get_summary()}"}
                    else:
                        return {"type": "error", "content": "目标已被删除"}
                else:
                    return {"type": "error", "content": "更新失败"}

            elif action == "pause":
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}
                success = goal_manager.pause_goal(goal_id)
                return {
                    "type": "goal_paused" if success else "error",
                    "content": "⏸️ 目标已暂停" if success else "暂停失败"
                }

            elif action == "resume":
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}
                success = goal_manager.resume_goal(goal_id)
                return {
                    "type": "goal_resumed" if success else "error",
                    "content": "▶️ 目标已恢复" if success else "恢复失败"
                }

            elif action == "complete":
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}
                success = goal_manager.complete_goal(goal_id)
                return {
                    "type": "goal_completed" if success else "error",
                    "content": "✅ 目标已完成！" if success else "完成失败"
                }

            elif action == "cancel":
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}
                success = goal_manager.cancel_goal(goal_id)
                return {
                    "type": "goal_cancelled" if success else "error",
                    "content": "❌ 目标已取消" if success else "取消失败"
                }

            elif action == "delete":
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}
                goal = goal_manager.get_goal(goal_id)
                if not goal:
                    return {"type": "error", "content": f"目标不存在: {goal_id}"}
                goal_name = goal.name
                success = goal_manager.delete_goal(goal_id)
                return {
                    "type": "goal_deleted" if success else "error",
                    "content": f"🗑️ 已删除目标: {goal_name}" if success else "删除失败"
                }

            else:
                return {"type": "error", "content": f"未知操作: {action}"}

        except Exception as e:
            logger.error(f"目标管理失败: {e}", exc_info=True)
            return {"type": "error", "content": f"操作失败: {str(e)}"}


class GetPlanningStatusTool(BaseTool):
    """获取规划状态工具 - 查看活跃目标和执行历史"""

    name = "get_planning_status"
    description = "查看麦麦的自主规划系统状态，包括活跃目标、执行历史等"
    parameters = [
        ("detailed", ToolParamType.BOOLEAN, "是否显示详细信息", False, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """查询并返回规划系统状态"""
        try:
            goal_manager = get_goal_manager()

            # 获取统计信息
            all_goals = goal_manager.get_all_goals()
            active_goals = goal_manager.get_active_goals()

            status_counts = {}
            for goal in all_goals:
                status = goal.status.value
                status_counts[status] = status_counts.get(status, 0) + 1

            # 构建状态报告
            content = f"""🤖 麦麦自主规划系统状态

📊 目标统计:
   总目标数: {len(all_goals)}
   活跃: {status_counts.get('active', 0)}
   暂停: {status_counts.get('paused', 0)}
   完成: {status_counts.get('completed', 0)}
   取消: {status_counts.get('cancelled', 0)}

🎯 当前活跃目标:"""

            if active_goals:
                for goal in active_goals[:5]:  # 只显示前5个
                    content += f"\n\n{goal.get_summary()}"
            else:
                content += "\n   暂无活跃目标"

            content += "\n\n💡 提示: 使用 manage_goal 工具可以创建新目标"

            return {"type": "planning_status", "content": content}

        except Exception as e:
            logger.error(f"获取规划状态失败: {e}", exc_info=True)
            return {"type": "error", "content": f"获取状态失败: {str(e)}"}


class GenerateScheduleTool(BaseTool):
    """生成日程工具 - 自动生成每日/每周/每月计划"""

    name = "generate_schedule"
    description = "自动生成并应用全局每日/每周/每月计划（所有聊天共享），使用LLM根据bot人设智能生成个性化计划，并自动保存为可执行目标"
    parameters = [
        ("schedule_type", ToolParamType.STRING, "日程类型: daily(每日)/weekly(每周)/monthly(每月)", True, None),
        ("auto_apply", ToolParamType.BOOLEAN, "是否立即应用日程（默认true）", False, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """生成并应用日程"""
        try:
            schedule_type_str = function_args.get("schedule_type", "daily")
            auto_apply = function_args.get("auto_apply", True)
            chat_id = "global"  # 全局日程
            user_id = function_args.get("_user_id", "system")

            goal_manager = get_goal_manager()

            # 读取配置并传给ScheduleGenerator
            schedule_config = {
                "use_multi_round": self.get_config("autonomous_planning.schedule.use_multi_round", True),
                "max_rounds": self.get_config("autonomous_planning.schedule.max_rounds", 2),
                "quality_threshold": self.get_config("autonomous_planning.schedule.quality_threshold", 0.85),
                "min_activities": self.get_config("autonomous_planning.schedule.min_activities", 6),
                "max_activities": self.get_config("autonomous_planning.schedule.max_activities", 12),
                "min_description_length": self.get_config("autonomous_planning.schedule.min_description_length", 15),
                "max_description_length": self.get_config("autonomous_planning.schedule.max_description_length", 30),
                "max_tokens": self.get_config("autonomous_planning.schedule.max_tokens", 8192),
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
            schedule_type = ScheduleType(schedule_type_str)

            if schedule_type == ScheduleType.DAILY:
                schedule = await schedule_generator.generate_daily_schedule(
                    user_id=user_id,
                    chat_id=chat_id,
                    use_llm=True
                )
            elif schedule_type == ScheduleType.WEEKLY:
                schedule = await schedule_generator.generate_weekly_schedule(
                    user_id=user_id,
                    chat_id=chat_id,
                    use_llm=True
                )
            elif schedule_type == ScheduleType.MONTHLY:
                schedule = await schedule_generator.generate_monthly_schedule(
                    user_id=user_id,
                    chat_id=chat_id,
                    use_llm=True
                )
            else:
                return {"type": "error", "content": f"未知的日程类型: {schedule_type_str}"}

            # 获取日程摘要
            summary = schedule_generator.get_schedule_summary(schedule)

            # 自动应用日程
            if auto_apply:
                created_ids = await schedule_generator.apply_schedule(
                    schedule=schedule,
                    user_id=user_id,
                    chat_id=chat_id
                )
                summary += f"\n\n✅ 日程已应用为全局目标，创建了 {len(created_ids)} 个目标（所有聊天共享）"

            return {"type": "schedule_generated", "content": summary}

        except Exception as e:
            logger.error(f"生成日程失败: {e}", exc_info=True)
            return {"type": "error", "content": f"生成日程失败: {str(e)}"}


class ApplyScheduleTool(BaseTool):
    """应用日程工具 - 将日程项转换为可执行目标"""

    name = "apply_schedule"
    description = "应用之前生成的日程，将日程项转换为全局可执行的目标（所有聊天共享）"
    parameters = [
        ("schedule_data", ToolParamType.STRING, "日程数据（从generate_schedule获取，JSON字符串）", True, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """应用日程并创建目标"""
        try:
            schedule_data = function_args.get("schedule_data")
            if not schedule_data:
                return {"type": "error", "content": "需要提供schedule_data"}

            chat_id = "global"  # 全局日程
            user_id = function_args.get("_user_id", "system")

            goal_manager = get_goal_manager()

            # 读取配置并传给ScheduleGenerator
            schedule_config = {
                "use_multi_round": self.get_config("autonomous_planning.schedule.use_multi_round", True),
                "max_rounds": self.get_config("autonomous_planning.schedule.max_rounds", 2),
                "quality_threshold": self.get_config("autonomous_planning.schedule.quality_threshold", 0.85),
                "min_activities": self.get_config("autonomous_planning.schedule.min_activities", 6),
                "max_activities": self.get_config("autonomous_planning.schedule.max_activities", 12),
                "min_description_length": self.get_config("autonomous_planning.schedule.min_description_length", 15),
                "max_description_length": self.get_config("autonomous_planning.schedule.max_description_length", 30),
                "max_tokens": self.get_config("autonomous_planning.schedule.max_tokens", 8192),
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

            # 重建Schedule对象
            from .planner.schedule_generator import ScheduleItem, Schedule
            items = []
            for item_data in schedule_data.get("items", []):
                items.append(ScheduleItem(
                    name=item_data["name"],
                    description=item_data["description"],
                    goal_type=item_data["goal_type"],
                    priority=item_data["priority"],
                    time_slot=item_data.get("time_slot"),
                    interval_hours=item_data.get("interval_hours"),
                    parameters=item_data.get("parameters", {}),
                    conditions=item_data.get("conditions", {}),
                ))

            schedule = Schedule(
                schedule_type=ScheduleType(schedule_data["schedule_type"]),
                name=schedule_data["name"],
                items=items
            )

            # 应用日程
            created_ids = await schedule_generator.apply_schedule(
                schedule=schedule,
                user_id=user_id,
                chat_id=chat_id
            )

            content = f"""✅ 日程应用成功！

创建了 {len(created_ids)} 个全局目标（所有聊天共享）
日程名称: {schedule.name}

这些目标已经激活，麦麦会自动执行它们~

使用 /plan status 查看所有目标"""

            return {"type": "schedule_applied", "content": content}

        except Exception as e:
            logger.error(f"应用日程失败: {e}", exc_info=True)
            return {"type": "error", "content": f"应用日程失败: {str(e)}"}


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
        """清理已完成/已取消的旧目标（保留30天）"""
        try:
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
                            try:
                                generation_success = await asyncio.wait_for(
                                    self._auto_generate_today_schedule(user_id, chat_id="global"),
                                    timeout=generation_timeout
                                )
                            except asyncio.TimeoutError:
                                logger.error(f"⏰ 日程生成超时（{generation_timeout}秒），跳过本次生成")
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
        with self._schedule_cache._sync_lock:
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
                # 检查是否是日程类型
                has_time_window = False
                if g.parameters and "time_window" in g.parameters:
                    has_time_window = True
                elif g.conditions and "time_window" in g.conditions:
                    has_time_window = True

                if has_time_window:
                    goal_date_str = None
                    goal_datetime = None

                    if g.created_at:
                        try:
                            if isinstance(g.created_at, str):
                                goal_date_str = g.created_at.split("T")[0]
                                goal_datetime = datetime.strptime(goal_date_str, "%Y-%m-%d")
                            else:
                                # datetime 对象
                                goal_datetime = g.created_at.replace(hour=0, minute=0, second=0, microsecond=0)
                        except Exception as e:
                            logger.warning(f"解析目标创建时间失败: {g.created_at} - {e}")
                            continue

                    # 使用datetime对象比较（更健壮）
                    if goal_datetime and goal_datetime < cutoff_date.replace(hour=0, minute=0, second=0, microsecond=0):
                        to_delete.append(g)

            if not to_delete:
                await self.send_text(f"✨ 没有需要清理的旧日程")
            else:
                # 执行删除
                deleted_count = 0
                for goal in to_delete:
                    if goal_manager.delete_goal(goal.goal_id):
                        deleted_count += 1

                if deleted_count > 0:
                    await self.send_text(f"🧹 已清理 {deleted_count} 个旧日程目标\n\n保留了今天的 {len(self._get_today_schedule_goals(goal_manager))} 个日程")
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

@register_plugin
class AutonomousPlanningPlugin(BasePlugin):
    """麦麦自主规划插件"""

    plugin_name: str = "autonomous_planning_plugin"
    enable_plugin: bool = True
    dependencies: List[str] = []  # perception_plugin 是可选依赖
    python_dependencies: List[str] = []
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "plugin": "插件基本配置",
        "autonomous_planning": "自主规划配置"
    }

    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用插件"
            ),
        },
        "autonomous_planning": {
            "cleanup_interval": ConfigField(
                type=int,
                default=3600,
                description="清理间隔（秒）"
            ),
            "cleanup_old_goals_days": ConfigField(
                type=int,
                default=30,
                description="保留历史记录天数"
            ),
            "schedule": {
                "inject_schedule": ConfigField(
                    type=bool,
                    default=True,
                    description="在对话时自然提到当前活动"
                ),
                "auto_generate": ConfigField(
                    type=bool,
                    default=True,
                    description="询问日程时自动检查并生成"
                ),
                "use_multi_round": ConfigField(
                    type=bool,
                    default=True,
                    description="启用多轮生成机制"
                ),
                "max_rounds": ConfigField(
                    type=int,
                    default=2,
                    description="最多尝试轮数"
                ),
                "quality_threshold": ConfigField(
                    type=float,
                    default=0.85,
                    description="质量阈值"
                ),
                "auto_schedule_enabled": ConfigField(
                    type=bool,
                    default=True,
                    description="是否启用定时自动生成日程"
                ),
                "auto_schedule_time": ConfigField(
                    type=str,
                    default="00:30",
                    description="每天自动生成日程的时间（HH:MM格式）"
                ),
                "timezone": ConfigField(
                    type=str,
                    default="Asia/Shanghai",
                    description="时区设置"
                ),
                "admin_users": ConfigField(
                    type=list,
                    default=[],
                    description="有权限使用命令的管理员QQ号列表，格式: [\"12345\", \"67890\"]"
                ),
                "max_tokens": ConfigField(
                    type=int,
                    default=8192,
                    description="日程生成的最大token数"
                ),
                "generation_timeout": ConfigField(
                    type=float,
                    default=180.0,
                    description="日程生成超时时间（秒）"
                ),
                "custom_model": {
                    "enabled": ConfigField(
                        type=bool,
                        default=False,
                        description="是否启用自定义模型"
                    ),
                    "model_name": ConfigField(
                        type=str,
                        default="",
                        description="模型名称"
                    ),
                    "api_base": ConfigField(
                        type=str,
                        default="",
                        description="API地址"
                    ),
                    "api_key": ConfigField(
                        type=str,
                        default="",
                        description="API密钥"
                    ),
                    "provider": ConfigField(
                        type=str,
                        default="",
                        description="提供商类型"
                    ),
                    "temperature": ConfigField(
                        type=float,
                        default=0.7,
                        description="温度参数（0.0-1.0）"
                    ),
                },
            },
        },
    }

    def __init__(self, *args, **kwargs):
        """初始化插件"""
        super().__init__(*args, **kwargs)
        self.scheduler = None
        logger.info("自主规划插件初始化完成")
        # 延迟启动调度器，确保插件系统完全初始化
        asyncio.create_task(self._start_scheduler_after_delay())

    async def _start_scheduler_after_delay(self):
        """延迟启动调度器（10秒后）"""
        await asyncio.sleep(10)
        self.scheduler = ScheduleAutoScheduler(self)
        await self.scheduler.start()

    def get_plugin_components(self) -> List[Tuple]:
        """获取插件组件"""
        return [
            # Tools - 供 LLM 直接调用的工具
            (ManageGoalTool.get_tool_info(), ManageGoalTool),
            (GetPlanningStatusTool.get_tool_info(), GetPlanningStatusTool),
            (GenerateScheduleTool.get_tool_info(), GenerateScheduleTool),
            (ApplyScheduleTool.get_tool_info(), ApplyScheduleTool),
            # Event Handlers - 事件处理器
            (AutonomousPlannerEventHandler.get_handler_info(), AutonomousPlannerEventHandler),
            (ScheduleInjectEventHandler.get_handler_info(), ScheduleInjectEventHandler),
            # Commands - 命令处理
            (PlanningCommand.get_command_info(), PlanningCommand),
        ]
