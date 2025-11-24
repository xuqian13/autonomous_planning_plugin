"""自主规划插件 - 工具模块

提供LLM可调用的工具，用于管理目标和生成日程。

工具列表：
    - ManageGoalTool: 目标管理（创建、查看、更新、删除等）
    - GetPlanningStatusTool: 获取规划状态
    - GenerateScheduleTool: 生成日程
    - ApplyScheduleTool: 应用日程
"""

import json
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta

from src.plugin_system import BaseTool
from src.llm_models.payload_content.tool_option import ToolParamType
from src.common.logger import get_logger

from ..planner.goal_manager import get_goal_manager, GoalPriority, GoalStatus
from ..planner.schedule_generator import ScheduleGenerator, ScheduleType
from ..core.exceptions import InvalidParametersError, InvalidTimeWindowError

logger = get_logger("autonomous_planning.tools")


def _parse_json_parameters(raw_params: Any) -> Dict[str, Any]:
    """解析JSON参数（字符串或字典）。

    Args:
        raw_params: 原始参数，可能是JSON字符串或字典

    Returns:
        解析后的字典
    """
    if isinstance(raw_params, str):
        try:
            return json.loads(raw_params)
        except json.JSONDecodeError:
            logger.warning(f"无法解析参数JSON: {raw_params}")
            return {}
    elif isinstance(raw_params, dict):
        return raw_params
    return {}


def _parse_time_window_str(time_window_str: str) -> Optional[List[int]]:
    """解析时间窗口字符串为分钟数列表。

    Args:
        time_window_str: 时间窗口字符串，格式 "HH:MM-HH:MM"

    Returns:
        [start_minutes, end_minutes] 或 None（解析失败）
    """
    try:
        parts = time_window_str.split("-")
        if len(parts) != 2:
            return None
        start_parts = parts[0].strip().split(":")
        end_parts = parts[1].strip().split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])
        return [start_minutes, end_minutes]
    except (ValueError, IndexError):
        return None


def _validate_parameters_schema(params: Dict[str, Any], goal_type: str = None) -> Tuple[bool, Optional[str]]:
    """验证目标参数的schema结构。

    Args:
        params: 要验证的参数字典
        goal_type: 目标类型（用于特定验证）

    Returns:
        (is_valid, error_message): 验证结果和错误消息

    Raises:
        InvalidParametersError: 参数验证失败时

    Schema规范:
        - time_window: 必须是包含2个整数的列表 [start_minutes, end_minutes]
        - topics: 必须是字符串列表（learn_topic类型必需）
        - depth: 必须是字符串（learn_topic类型必需）
        - check_plugins: 必须是布尔值（health_check类型建议）
        - greeting_type: 必须是字符串（social_maintenance类型建议）
    """
    if not isinstance(params, dict):
        raise InvalidParametersError("参数必须是字典类型", invalid_value=type(params).__name__)

    # 验证 time_window
    if "time_window" in params:
        time_window = params["time_window"]
        if not isinstance(time_window, list):
            raise InvalidTimeWindowError(
                f"time_window必须是列表，当前类型: {type(time_window).__name__}",
                time_window=time_window
            )
        if len(time_window) != 2:
            raise InvalidTimeWindowError(
                f"time_window必须包含2个元素，当前: {len(time_window)}个",
                time_window=time_window
            )
        if not all(isinstance(x, int) for x in time_window):
            raise InvalidTimeWindowError(
                f"time_window的元素必须是整数，当前: {[type(x).__name__ for x in time_window]}",
                time_window=time_window
            )
        # 验证取值范围 (0-1440分钟 = 24小时)
        start, end = time_window
        if not (0 <= start < 1440 and 0 < end <= 1440):
            raise InvalidTimeWindowError(
                f"time_window的值必须在0-1440范围内，当前: {time_window}",
                time_window=time_window
            )
        if start >= end:
            raise InvalidTimeWindowError(
                f"time_window的起始时间必须小于结束时间，当前: {time_window}",
                time_window=time_window
            )

    # 验证 topics（learn_topic类型）
    if goal_type == "learn_topic":
        if "topics" not in params:
            raise InvalidParametersError(
                "learn_topic类型的目标必须包含topics参数",
                field_name="topics"
            )
        topics = params["topics"]
        if not isinstance(topics, list):
            raise InvalidParametersError(
                f"topics必须是列表，当前类型: {type(topics).__name__}",
                field_name="topics",
                invalid_value=topics
            )
        if not all(isinstance(t, str) for t in topics):
            raise InvalidParametersError(
                "topics的元素必须都是字符串",
                field_name="topics",
                invalid_value=topics
            )
        if len(topics) == 0:
            raise InvalidParametersError(
                "topics列表不能为空",
                field_name="topics",
                invalid_value=topics
            )

        # 验证 depth
        if "depth" not in params:
            raise InvalidParametersError(
                "learn_topic类型的目标必须包含depth参数",
                field_name="depth"
            )
        depth = params["depth"]
        if not isinstance(depth, str):
            raise InvalidParametersError(
                f"depth必须是字符串，当前类型: {type(depth).__name__}",
                field_name="depth",
                invalid_value=depth
            )
        valid_depths = ["basic", "intermediate", "advanced"]
        if depth not in valid_depths:
            raise InvalidParametersError(
                f"depth必须是以下之一: {valid_depths}，当前: {depth}",
                field_name="depth",
                invalid_value=depth
            )

    # 验证 check_plugins（health_check类型）
    if "check_plugins" in params:
        check_plugins = params["check_plugins"]
        if not isinstance(check_plugins, bool):
            raise InvalidParametersError(
                f"check_plugins必须是布尔值，当前类型: {type(check_plugins).__name__}",
                field_name="check_plugins",
                invalid_value=check_plugins
            )

    # 验证 greeting_type（social_maintenance类型）
    if "greeting_type" in params:
        greeting_type = params["greeting_type"]
        if not isinstance(greeting_type, str):
            raise InvalidParametersError(
                f"greeting_type必须是字符串，当前类型: {type(greeting_type).__name__}",
                field_name="greeting_type",
                invalid_value=greeting_type
            )

    return True, None


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
        ("time_window", ToolParamType.STRING, "时间窗口，格式为'HH:MM-HH:MM'。例如：'09:00-10:30'表示9点到10点半", False, None),
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
                time_window_str = function_args.get("time_window")
                deadline_hours = function_args.get("deadline_hours")

                # 解析时间窗口
                time_window = None
                if time_window_str:
                    time_window = _parse_time_window_str(time_window_str)
                    if time_window is None:
                        return {
                            "type": "error",
                            "content": "时间窗口格式错误，应为'HH:MM-HH:MM'"
                        }

                if deadline_hours is not None:
                    if deadline_hours <= 0:
                        return {"type": "error", "content": "截止时间必须大于0小时"}
                    if deadline_hours > 87600:  # 10年
                        return {"type": "error", "content": "截止时间不能超过10年"}

                # 解析parameters参数
                parameters = _parse_json_parameters(function_args.get("parameters", {}))

                # 计算时间
                deadline = datetime.now() + timedelta(hours=deadline_hours) if deadline_hours else None

                # 将time_window存入parameters
                if time_window:
                    parameters["time_window"] = time_window

                # 🆕 P0级：验证parameters的schema
                try:
                    _validate_parameters_schema(parameters, goal_type)
                except (InvalidParametersError, InvalidTimeWindowError) as e:
                    logger.warning(f"参数验证失败: {e}")
                    return {
                        "type": "error",
                        "content": f"参数验证失败: {str(e)}"
                    }

                goal = goal_manager.create_goal(
                    name=name,
                    description=description,
                    goal_type=goal_type,
                    creator_id=user_id,
                    chat_id=chat_id,
                    priority=priority,
                    deadline=deadline,
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
                if "time_window" in function_args:
                    tw = _parse_time_window_str(function_args["time_window"])
                    if tw is None:
                        return {
                            "type": "error",
                            "content": "时间窗口格式错误，应为'HH:MM-HH:MM'"
                        }
                    goal = goal_manager.get_goal(goal_id)
                    if goal:
                        params = goal.parameters.copy() if goal.parameters else {}
                        params["time_window"] = tw
                        update_params["parameters"] = params
                if "parameters" in function_args:
                    update_params["parameters"] = _parse_json_parameters(
                        function_args["parameters"]
                    )

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
                "min_activities": self.get_config("autonomous_planning.schedule.min_activities", 8),
                "max_activities": self.get_config("autonomous_planning.schedule.max_activities", 15),
                "min_description_length": self.get_config("autonomous_planning.schedule.min_description_length", 15),
                "max_description_length": self.get_config("autonomous_planning.schedule.max_description_length", 50),
                "max_tokens": self.get_config("autonomous_planning.schedule.max_tokens", 8192),
                "custom_prompt": self.get_config("autonomous_planning.schedule.custom_prompt", ""),
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
                "min_activities": self.get_config("autonomous_planning.schedule.min_activities", 8),
                "max_activities": self.get_config("autonomous_planning.schedule.max_activities", 15),
                "min_description_length": self.get_config("autonomous_planning.schedule.min_description_length", 15),
                "max_description_length": self.get_config("autonomous_planning.schedule.max_description_length", 50),
                "max_tokens": self.get_config("autonomous_planning.schedule.max_tokens", 8192),
                "custom_prompt": self.get_config("autonomous_planning.schedule.custom_prompt", ""),
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
                    duration_hours=item_data.get("duration_hours"),
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

