"""
麦麦自主规划插件

让麦麦具备自主规划和执行目标的能力
"""

import asyncio
import json
from typing import List, Tuple, Type, Dict, Any, Optional
from datetime import datetime, timedelta

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
from .planner.auto_schedule_manager import AutoScheduleManager
from .actions.schedule_action import ScheduleAction
from .utils.schedule_image_generator import ScheduleImageGenerator

logger = get_logger("autonomous_planning")


# ===== Tools =====

class ManageGoalTool(BaseTool):
    """目标管理工具"""

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
        """执行工具"""
        try:
            action = function_args.get("action")
            goal_manager = get_goal_manager()

            # 获取当前聊天信息
            # 优先从 function_args 中的 _chat_id 获取（ToolExecutor 自动注入）
            chat_id = function_args.get("_chat_id", "default")
            user_id = function_args.get("_user_id", "system")

            if action == "create":
                # 创建目标
                name = function_args.get("name")
                description = function_args.get("description")

                if not name or not description:
                    return {"type": "error", "content": "创建目标需要提供name和description"}

                goal_type = function_args.get("goal_type", "custom")
                priority = function_args.get("priority", "medium")
                interval_minutes = function_args.get("interval_minutes")
                deadline_hours = function_args.get("deadline_hours")

                # 处理 parameters：可能是字符串（JSON）或字典
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

                # 计算时间（分钟转秒，精确计算）
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
                # 列出目标
                summary = goal_manager.get_goals_summary(chat_id=chat_id)
                return {"type": "goal_list", "content": summary}

            elif action == "get":
                # 查看目标详情
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                goal = goal_manager.get_goal(goal_id)
                if not goal:
                    return {"type": "error", "content": f"目标不存在: {goal_id}"}

                return {"type": "goal_info", "content": goal.get_summary()}

            elif action == "update":
                # 更新目标
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                # 构建更新参数
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
                    return {"type": "goal_updated", "content": f"✅ 目标已更新\n\n{goal.get_summary()}"}
                else:
                    return {"type": "error", "content": "更新失败"}

            elif action == "pause":
                # 暂停目标
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                success = goal_manager.pause_goal(goal_id)
                return {
                    "type": "goal_paused" if success else "error",
                    "content": "⏸️ 目标已暂停" if success else "暂停失败"
                }

            elif action == "resume":
                # 恢复目标
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                success = goal_manager.resume_goal(goal_id)
                return {
                    "type": "goal_resumed" if success else "error",
                    "content": "▶️ 目标已恢复" if success else "恢复失败"
                }

            elif action == "complete":
                # 完成目标
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                success = goal_manager.complete_goal(goal_id)
                return {
                    "type": "goal_completed" if success else "error",
                    "content": "✅ 目标已完成！" if success else "完成失败"
                }

            elif action == "cancel":
                # 取消目标
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                success = goal_manager.cancel_goal(goal_id)
                return {
                    "type": "goal_cancelled" if success else "error",
                    "content": "❌ 目标已取消" if success else "取消失败"
                }

            elif action == "delete":
                # 删除目标
                goal_id = function_args.get("goal_id")
                if not goal_id:
                    return {"type": "error", "content": "需要提供goal_id"}

                # 获取目标信息用于显示
                goal = goal_manager.get_goal(goal_id)
                if not goal:
                    return {"type": "error", "content": f"目标不存在: {goal_id}"}

                goal_name = goal.name

                # 删除目标
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
    """获取规划状态工具"""

    name = "get_planning_status"
    description = "查看麦麦的自主规划系统状态，包括活跃目标、执行历史等"
    parameters = [
        ("detailed", ToolParamType.BOOLEAN, "是否显示详细信息", False, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具"""
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
    """生成日程工具"""

    name = "generate_schedule"
    description = "自动生成并应用全局每日/每周/每月计划（所有聊天共享），使用LLM智能生成个性化计划，并自动保存为可执行目标"
    parameters = [
        ("schedule_type", ToolParamType.STRING, "日程类型: daily(每日)/weekly(每周)/monthly(每月)", True, None),
        ("preferences", ToolParamType.STRING, "用户偏好设置（JSON字符串）", False, None),
        ("auto_apply", ToolParamType.BOOLEAN, "是否立即应用日程（默认true）", False, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具"""
        try:
            schedule_type_str = function_args.get("schedule_type", "daily")
            preferences_raw = function_args.get("preferences", {})
            auto_apply = function_args.get("auto_apply", True)  # 默认自动应用日程

            # 解析preferences（可能是JSON字符串）
            if isinstance(preferences_raw, str):
                try:
                    preferences = json.loads(preferences_raw) if preferences_raw else {}
                except json.JSONDecodeError:
                    logger.warning(f"preferences解析失败，使用空字典: {preferences_raw}")
                    preferences = {}
            else:
                preferences = preferences_raw if preferences_raw else {}

            # 强制使用全局chat_id
            chat_id = "global"
            user_id = function_args.get("_user_id", "system")

            goal_manager = get_goal_manager()
            schedule_generator = ScheduleGenerator(goal_manager)

            # 使用LLM生成日程
            schedule_type = ScheduleType(schedule_type_str)

            if schedule_type == ScheduleType.DAILY:
                schedule = await schedule_generator.generate_daily_schedule(
                    user_id=user_id,
                    chat_id=chat_id,
                    preferences=preferences,
                    use_llm=True
                )
            elif schedule_type == ScheduleType.WEEKLY:
                schedule = await schedule_generator.generate_weekly_schedule(
                    user_id=user_id,
                    chat_id=chat_id,
                    preferences=preferences,
                    use_llm=True
                )
            elif schedule_type == ScheduleType.MONTHLY:
                schedule = await schedule_generator.generate_monthly_schedule(
                    user_id=user_id,
                    chat_id=chat_id,
                    preferences=preferences,
                    use_llm=True
                )
            else:
                return {"type": "error", "content": f"未知的日程类型: {schedule_type_str}"}

            # 获取日程摘要
            summary = schedule_generator.get_schedule_summary(schedule)

            # 如果需要自动应用
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
    """应用日程工具"""

    name = "apply_schedule"
    description = "应用之前生成的日程，将日程项转换为全局可执行的目标（所有聊天共享）"
    parameters = [
        ("schedule_data", ToolParamType.STRING, "日程数据（从generate_schedule获取，JSON字符串）", True, None),
    ]
    available_for_llm = True

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具"""
        try:
            schedule_data = function_args.get("schedule_data")

            if not schedule_data:
                return {"type": "error", "content": "需要提供schedule_data"}

            # 强制使用全局chat_id
            chat_id = "global"
            user_id = function_args.get("_user_id", "system")

            goal_manager = get_goal_manager()
            schedule_generator = ScheduleGenerator(goal_manager)

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


# ===== Event Handlers =====

class AutonomousPlannerEventHandler(BaseEventHandler):
    """自主规划事件处理器 - 负责定期清理过期目标"""

    event_type = EventType.ON_START
    handler_name = "autonomous_planner"
    handler_description = "定期清理过期的日程目标"
    weight = 10
    intercept_message = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.goal_manager = get_goal_manager()

        # 检查循环任务
        self.check_task: Optional[asyncio.Task] = None
        self.is_running = False

        # 配置
        self.enabled = self.get_config("plugin.enabled", True)
        # 每小时清理一次过期目标
        self.cleanup_interval = self.get_config("autonomous_planning.cleanup_interval", 3600)

        logger.info(f"自主规划维护任务初始化完成 (清理间隔: {self.cleanup_interval}秒)")

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """处理启动事件"""
        if not self.enabled:
            return True, True, None, None, None

        # 启动后台清理循环
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

            # 等待下一个清理周期
            await asyncio.sleep(self.cleanup_interval)

    async def _cleanup_old_goals(self):
        """清理旧目标"""
        try:
            # 清理已完成/已取消的旧目标（保留30天）
            cleanup_days = self.get_config("autonomous_planning.cleanup_old_goals_days", 30)
            cleaned_count = self.goal_manager.cleanup_old_goals(days=cleanup_days)

            if cleaned_count > 0:
                logger.info(f"🧹 清理了 {cleaned_count} 个旧目标（{cleanup_days}天前）")

        except Exception as e:
            logger.error(f"清理旧目标失败: {e}", exc_info=True)


class ScheduleInjectEventHandler(BaseEventHandler):
    """日程注入事件处理器 - 在LLM调用前注入当前日程"""

    event_type = EventType.POST_LLM  # POST_LLM实际上在LLM调用之前触发
    handler_name = "schedule_inject_handler"
    handler_description = "在LLM调用前注入当前日程信息到prompt"
    weight = 10
    intercept_message = True  # 必须为True才能返回modified_message

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # 加载配置
        self.enabled = self.get_config("plugin.enabled", True)
        self.inject_schedule = self.get_config("autonomous_planning.schedule.inject_schedule", True)

        # 日程缓存（减少重复查询）
        # 修改：使用dict存储不同chat_id和时间的缓存
        self._schedule_cache = {}  # key: f"{chat_id}_{time_window}", value: (result, timestamp)
        self._schedule_cache_ttl = 30  # 缓存30秒
        self._cache_cleanup_interval = 300  # 每5分钟清理一次过期缓存
        self._last_cache_cleanup = 0  # 上次清理时间

        if self.enabled and self.inject_schedule:
            logger.info("日程注入功能已启用")

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """执行事件处理"""
        if not self.enabled or not self.inject_schedule:
            return True, True, None, None, None

        # POST_LLM 事件时 message 不会是 None
        if not message or not message.llm_prompt:
            return True, True, None, None, None

        try:
            # 获取chat_id
            chat_id = message.stream_id if hasattr(message, 'stream_id') else None

            if not chat_id:
                return True, True, None, None, None

            # 获取当前日程
            current_activity, current_description, next_activity, next_time = self._get_current_schedule(chat_id)

            # 构建日程提示
            schedule_prompt = ""
            if current_activity:
                # 使用自然、符合麦麦风格的提示语气
                schedule_prompt = f"\n【当前状态】\n"
                schedule_prompt += f"这会儿正{current_activity}"

                if current_description:
                    schedule_prompt += f"（{current_description}）"

                schedule_prompt += f"\n回复时可以自然提到当前在做什么，不要刻意强调。"

                if next_activity and next_time:
                    schedule_prompt += f"\n等下{next_time}要{next_activity}。"

                schedule_prompt += "\n"

            # 如果有日程信息，注入到prompt
            if schedule_prompt:
                original_prompt = str(message.llm_prompt)
                # 在prompt开头注入日程信息
                new_prompt = schedule_prompt + "\n" + original_prompt
                message.modify_llm_prompt(new_prompt, suppress_warning=True)
                logger.debug(f"已注入日程状态: {current_activity}")

            return True, True, None, None, message

        except Exception as e:
            logger.error(f"注入日程信息失败: {e}", exc_info=True)
            return True, True, None, None, None

    def _cleanup_expired_cache(self, current_time: float):
        """清理过期的缓存项"""
        expired_keys = []
        for key, (_, cached_time) in self._schedule_cache.items():
            if current_time - cached_time > self._schedule_cache_ttl:
                expired_keys.append(key)

        for key in expired_keys:
            del self._schedule_cache[key]

        if expired_keys:
            logger.debug(f"清理了 {len(expired_keys)} 个过期缓存项")

    def _get_current_schedule(self, chat_id: Optional[str] = None) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        获取当前日程信息（带缓存）

        Returns:
            (当前活动, 活动描述, 下一个活动, 下一个活动时间)
        """
        import time

        # 获取当前时间
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        current_time = time.time()

        # 构建缓存键：包含chat_id和时间窗口（5分钟精度）
        # 使用5分钟窗口而不是小时，减少跨窗口缓存失效问题
        time_window = (current_hour * 60 + current_minute) // 5  # 每5分钟一个窗口
        cache_key = f"{chat_id or 'global'}_{time_window}"

        # 定期清理过期缓存（避免内存无限增长）
        if current_time - self._last_cache_cleanup > self._cache_cleanup_interval:
            self._cleanup_expired_cache(current_time)
            self._last_cache_cleanup = current_time

        # 检查缓存是否有效
        if cache_key in self._schedule_cache:
            cached_result, cached_time = self._schedule_cache[cache_key]
            if current_time - cached_time < self._schedule_cache_ttl:
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

            current_minute = now.minute
            current_time_minutes = current_hour * 60 + current_minute

            # 找到有时间窗口的目标
            scheduled_goals = []
            for goal in goals:
                # 向后兼容：优先从parameters读取time_window，其次从conditions读取
                time_window = None
                if goal.parameters and "time_window" in goal.parameters:
                    time_window = goal.parameters.get("time_window")
                elif goal.conditions:
                    time_window = goal.conditions.get("time_window")

                if time_window:
                    scheduled_goals.append((goal, time_window))

            if not scheduled_goals:
                result = (None, None, None, None)
                self._schedule_cache[cache_key] = (result, current_time)
                return result

            # 排序：按开始时间（兼容新旧格式）
            def get_start_minutes(item):
                goal, time_window = item
                start_val = time_window[0] if time_window else 0
                # 判断格式
                if len(time_window) > 1 and time_window[1] > 24:
                    # 新格式：已经是分钟
                    return start_val
                else:
                    # 旧格式：小时，转为分钟
                    return start_val * 60

            scheduled_goals.sort(key=get_start_minutes)

            # 查找当前活动
            current_activity = None
            current_description = None
            for goal, time_window in scheduled_goals:
                start_val = time_window[0] if len(time_window) > 0 else 0
                end_val = time_window[1] if len(time_window) > 1 else start_val + 60

                # 判断格式并转换
                if end_val <= 24:
                    # 旧格式
                    start_minutes = start_val * 60
                    end_minutes = end_val * 60
                else:
                    # 新格式
                    start_minutes = start_val
                    end_minutes = end_val

                if start_minutes <= current_time_minutes < end_minutes:
                    current_activity = goal.name
                    current_description = goal.description
                    break

            # 查找下一个活动
            next_activity = None
            next_time = None
            for goal, time_window in scheduled_goals:
                start_val = time_window[0] if len(time_window) > 0 else 0
                end_val = time_window[1] if len(time_window) > 1 else start_val + 60

                # 判断格式并转换
                if end_val <= 24:
                    start_minutes = start_val * 60
                else:
                    start_minutes = start_val

                if start_minutes > current_time_minutes:
                    next_activity = goal.name
                    # 转换为时:分格式
                    hour = start_minutes // 60
                    minute = start_minutes % 60
                    next_time = f"{hour:02d}:{minute:02d}"
                    break

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

    async def execute(self) -> Tuple[bool, str, bool]:
        """执行命令"""
        command_text = self.matched_groups.get("planning_cmd", "").strip()
        parts = command_text.split()

        if len(parts) == 1:
            await self._show_help()
            return True, "显示帮助", True

        subcommand = parts[1] if len(parts) > 1 else ""

        if subcommand == "status":
            # 显示状态 - 简洁的时间线格式
            goal_manager = get_goal_manager()
            goals = goal_manager.get_all_goals()

            if not goals:
                await self.send_text("📋 当前没有任何目标")
            else:
                # 检测日程类型的目标（向后兼容）
                schedule_goals = []
                for g in goals:
                    # 优先从parameters读取time_window，其次从conditions读取
                    has_time_window = False
                    if g.parameters and "time_window" in g.parameters:
                        has_time_window = True
                    elif g.conditions and "time_window" in g.conditions:
                        has_time_window = True

                    if has_time_window:
                        schedule_goals.append(g)

                if schedule_goals:
                    # 按时间排序
                    def get_time_window(g):
                        tw = (g.parameters.get("time_window") if g.parameters else None) or \
                             (g.conditions.get("time_window") if g.conditions else None) or [0]
                        return tw[0] if tw else 0

                    schedule_goals.sort(key=get_time_window)

                    messages = ["📅 今日日程\n"]

                    for goal in schedule_goals:
                        # 向后兼容地获取time_window
                        time_window = None
                        if goal.parameters and "time_window" in goal.parameters:
                            time_window = goal.parameters.get("time_window", [0, 0])
                        elif goal.conditions and "time_window" in goal.conditions:
                            time_window = goal.conditions.get("time_window", [0, 0])

                        if time_window:
                            start_val = time_window[0] if len(time_window) > 0 else 0
                            end_val = time_window[1] if len(time_window) > 1 else start_val + 60

                            # 判断格式并转换为分钟
                            if end_val <= 24:
                                # 旧格式：小时
                                start_minutes = start_val * 60
                                end_minutes = end_val * 60
                            else:
                                # 新格式：分钟
                                start_minutes = start_val
                                end_minutes = end_val

                            # 转换为时:分
                            start_hour = start_minutes // 60
                            start_min = start_minutes % 60
                            end_hour = end_minutes // 60
                            end_min = end_minutes % 60

                            # 目标类型emoji
                            type_emoji = {
                                "meal": "🍽️",
                                "study": "📚",
                                "entertainment": "🎮",
                                "daily_routine": "🏠",
                                "social_maintenance": "💬",
                                "learn_topic": "📖",
                            }.get(goal.goal_type, "📌")

                            # 简洁格式：时间 + emoji + 名称
                            messages.append(f"{start_hour:02d}:{start_min:02d}-{end_hour:02d}:{end_min:02d} {type_emoji} {goal.name}")

                    await self.send_text("\n".join(messages))
                else:
                    # 如果没有日程目标，显示原有的统计摘要
                    summary = goal_manager.get_goals_summary()
                    await self.send_text(summary)

        elif subcommand == "list":
            # 列出目标 - 图片格式
            goal_manager = get_goal_manager()
            goals = goal_manager.get_all_goals()

            if not goals:
                await self.send_text("📋 当前没有任何目标")
            else:
                # 检测日程类型的目标（向后兼容）
                schedule_goals = []
                for g in goals:
                    # 优先从parameters读取time_window，其次从conditions读取
                    has_time_window = False
                    if g.parameters and "time_window" in g.parameters:
                        has_time_window = True
                    elif g.conditions and "time_window" in g.conditions:
                        has_time_window = True

                    if has_time_window:
                        schedule_goals.append(g)

                if schedule_goals:
                    # 按时间排序
                    def get_time_window(g):
                        tw = (g.parameters.get("time_window") if g.parameters else None) or \
                             (g.conditions.get("time_window") if g.conditions else None) or [0]
                        return tw[0] if tw else 0

                    schedule_goals.sort(key=get_time_window)

                    # 准备图片数据
                    schedule_items = []
                    for goal in schedule_goals:
                        # 向后兼容地获取time_window
                        time_window = None
                        if goal.parameters and "time_window" in goal.parameters:
                            time_window = goal.parameters.get("time_window", [0, 0])
                        elif goal.conditions and "time_window" in goal.conditions:
                            time_window = goal.conditions.get("time_window", [0, 0])

                        if time_window:
                            start_val = time_window[0] if len(time_window) > 0 else 0
                            end_val = time_window[1] if len(time_window) > 1 else start_val + 60

                            # 判断格式并转换为分钟
                            if end_val <= 24:
                                # 旧格式：小时
                                start_minutes = start_val * 60
                                end_minutes = end_val * 60
                            else:
                                # 新格式：分钟
                                start_minutes = start_val
                                end_minutes = end_val

                            # 转换为时:分
                            start_hour = start_minutes // 60
                            start_min = start_minutes % 60
                            end_hour = end_minutes // 60
                            end_min = end_minutes % 60

                            time_str = f"{start_hour:02d}:{start_min:02d}-{end_hour:02d}:{end_min:02d}"

                            schedule_items.append({
                                "time": time_str,
                                "name": goal.name,
                                "description": goal.description,
                                "goal_type": goal.goal_type
                            })

                    # 生成图片
                    try:
                        today = datetime.now().strftime("%Y-%m-%d %A")
                        img_bytes, img_base64 = ScheduleImageGenerator.generate_schedule_image(
                            title=f"📅 今日日程 {today}",
                            schedule_items=schedule_items
                        )
                        await self.send_image(img_base64)
                    except Exception as e:
                        logger.error(f"生成日程图片失败: {e}", exc_info=True)
                        # 降级到文本输出
                        messages = ["📅 今日日程详情\n"]
                        for item in schedule_items:
                            messages.append(f"  ⏰ {item['time']}  {item['name']}")
                            messages.append(f"     {item['description']}")
                            messages.append("")
                        await self.send_text("\n".join(messages))

                else:
                    # 没有日程目标，显示普通列表
                    messages = ["📋 所有目标:\n"]
                    for idx, goal in enumerate(goals, 1):
                        messages.append(f"[{idx}] {goal.get_summary()}")
                        messages.append("")
                    await self.send_text("\n".join(messages))

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

        elif subcommand == "help":
            await self._show_help()

        else:
            await self.send_text(f"未知命令: {subcommand}\n使用 /plan help 查看帮助")

        return True, "命令执行完成", True

    async def _show_help(self):
        """显示帮助"""
        help_text = """🤖 麦麦自主规划系统

命令列表:
/plan status - 查看日程概览（简洁格式）
/plan list - 查看日程详情（包含完整描述）
/plan delete <goal_id或序号> - 删除目标
/plan help - 显示此帮助

💡 使用方式:
1. 对我说 "帮我创建一个目标..." 我会调用工具创建
2. 我会自动执行已创建的目标
3. 使用 status 查看简洁日程，list 查看详细信息
4. 使用 delete 命令删除不需要的目标

示例:
"帮我每小时检查一下系统状况"
"提醒我每天早上9点问候大家"
"每天帮我学习一个新知识"

删除示例:
/plan delete 1        # 删除第1个目标
/plan delete abc-123  # 删除指定ID的目标
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
                description="是否启用自主规划插件"
            ),
        },
        "autonomous_planning": {
            "interval": ConfigField(
                type=int,
                default=300,
                description="规划循环间隔（秒），默认5分钟"
            ),
            "max_actions_per_cycle": ConfigField(
                type=int,
                default=3,
                description="每个周期最多执行的行动数量"
            ),
            "enable_llm_planning": ConfigField(
                type=bool,
                default=False,
                description="是否启用LLM智能规划（实验性）"
            ),
        },
    }

    def get_plugin_components(self) -> List[Tuple]:
        """获取插件组件"""
        return [
            # Actions - 通过 Planner 主动执行的动作
            (ScheduleAction.get_action_info(), ScheduleAction),
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
