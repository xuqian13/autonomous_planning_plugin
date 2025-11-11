"""
日程生成器
自动生成每日/每周/每月计划
"""

import json
import random
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from enum import Enum

from src.common.logger import get_logger
from src.plugin_system.apis import llm_api, config_api

from .goal_manager import GoalManager, GoalPriority

logger = get_logger("autonomous_planning.schedule_generator")


class ScheduleType(Enum):
    """日程类型"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ScheduleItem:
    """日程项"""

    def __init__(
        self,
        name: str,
        description: str,
        goal_type: str,
        priority: str,
        time_slot: Optional[str] = None,  # 时间段，如 "09:00"
        interval_hours: Optional[float] = None,
        parameters: Optional[Dict[str, Any]] = None,
        conditions: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.description = description
        self.goal_type = goal_type
        self.priority = priority
        self.time_slot = time_slot
        self.interval_hours = interval_hours
        self.parameters = parameters or {}
        self.conditions = conditions or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "goal_type": self.goal_type,
            "priority": self.priority,
            "time_slot": self.time_slot,
            "interval_hours": self.interval_hours,
            "parameters": self.parameters,
            "conditions": self.conditions,
        }


class Schedule:
    """日程"""

    def __init__(
        self,
        schedule_type: ScheduleType,
        name: str,
        items: List[ScheduleItem],
        created_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.schedule_type = schedule_type
        self.name = name
        self.items = items
        self.created_at = created_at or datetime.now()
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_type": self.schedule_type.value,
            "name": self.name,
            "items": [item.to_dict() for item in self.items],
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }


class ScheduleGenerator:
    """日程生成器"""

    def __init__(self, goal_manager: GoalManager):
        self.goal_manager = goal_manager
        self.yesterday_schedule_summary = None  # 昨日日程摘要（用于上下文）

    def _load_yesterday_schedule_summary(self) -> Optional[str]:
        """加载昨日日程摘要，用于生成今日日程的上下文"""
        try:
            yesterday = datetime.now() - timedelta(days=1)
            yesterday_str = yesterday.strftime("%Y-%m-%d")

            # 获取昨天的所有目标
            goals = self.goal_manager.get_all_goals(chat_id="global")
            yesterday_activities = []

            for goal in goals:
                # 检查目标是否有time_window（日程类型）
                time_window = None
                if goal.parameters and "time_window" in goal.parameters:
                    time_window = goal.parameters["time_window"]
                elif goal.conditions and "time_window" in goal.conditions:
                    time_window = goal.conditions["time_window"]

                if time_window:
                    # 将分钟数转换为时间字符串
                    start_minutes = time_window[0] if isinstance(time_window, list) else 0
                    hour = start_minutes // 60
                    minute = start_minutes % 60
                    time_str = f"{hour:02d}:{minute:02d}"

                    yesterday_activities.append(f"{time_str} {goal.name}: {goal.description}")

            if yesterday_activities:
                summary = "昨天我的日程:\n" + "\n".join(yesterday_activities[:10])  # 最多10条
                logger.debug(f"加载昨日日程摘要: {len(yesterday_activities)} 条活动")
                return summary
            else:
                logger.debug("未找到昨日日程")
                return "昨天没有记录具体日程，就是普通的一天"

        except Exception as e:
            logger.warning(f"加载昨日日程失败: {e}")
            return "昨天的事情记不太清了"

    async def generate_daily_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True
    ) -> Schedule:
        """
        生成每日计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成个性化计划

        Returns:
            Schedule对象
        """
        logger.info(f"为用户 {user_id} 生成每日计划（仅使用LLM）")

        preferences = preferences or {}

        # 加载昨日日程作为上下文
        self.yesterday_schedule_summary = self._load_yesterday_schedule_summary()

        # 强制使用LLM生成个性化计划
        schedule_items = await self._generate_schedule_with_llm(
            schedule_type=ScheduleType.DAILY,
            user_id=user_id,
            chat_id=chat_id,
            preferences=preferences
        )

        schedule = Schedule(
            schedule_type=ScheduleType.DAILY,
            name=f"每日计划 - {datetime.now().strftime('%Y-%m-%d')}",
            items=schedule_items,
            metadata={"preferences": preferences}
        )

        return schedule

    async def generate_weekly_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True
    ) -> Schedule:
        """
        生成每周计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成

        Returns:
            Schedule对象
        """
        logger.info(f"为用户 {user_id} 生成每周计划（仅使用LLM）")

        preferences = preferences or {}

        # 强制使用LLM生成
        schedule_items = await self._generate_schedule_with_llm(
            schedule_type=ScheduleType.WEEKLY,
            user_id=user_id,
            chat_id=chat_id,
            preferences=preferences
        )

        # 获取本周日期范围
        today = datetime.now()
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)

        schedule = Schedule(
            schedule_type=ScheduleType.WEEKLY,
            name=f"每周计划 - {start_of_week.strftime('%m/%d')} 至 {end_of_week.strftime('%m/%d')}",
            items=schedule_items,
            metadata={"preferences": preferences}
        )

        return schedule

    async def generate_monthly_schedule(
        self,
        user_id: str,
        chat_id: str,
        preferences: Optional[Dict[str, Any]] = None,
        use_llm: bool = True
    ) -> Schedule:
        """
        生成每月计划

        Args:
            user_id: 用户ID
            chat_id: 聊天ID
            preferences: 用户偏好设置
            use_llm: 是否使用LLM生成

        Returns:
            Schedule对象
        """
        logger.info(f"为用户 {user_id} 生成每月计划（仅使用LLM）")

        preferences = preferences or {}

        # 强制使用LLM生成
        schedule_items = await self._generate_schedule_with_llm(
            schedule_type=ScheduleType.MONTHLY,
            user_id=user_id,
            chat_id=chat_id,
            preferences=preferences
        )

        today = datetime.now()
        schedule = Schedule(
            schedule_type=ScheduleType.MONTHLY,
            name=f"每月计划 - {today.strftime('%Y年%m月')}",
            items=schedule_items,
            metadata={"preferences": preferences}
        )

        return schedule

    async def apply_schedule(
        self,
        schedule: Schedule,
        user_id: str,
        chat_id: str,
        auto_start: bool = True
    ) -> List[str]:
        """
        应用日程，将日程项转换为目标（批量优化）

        Args:
            schedule: 日程对象
            user_id: 用户ID
            chat_id: 聊天ID
            auto_start: 是否自动启动

        Returns:
            创建的目标ID列表
        """
        logger.info(f"应用日程: {schedule.name}")

        # 准备批量创建的数据
        goals_data = []

        for item in schedule.items:
            try:
                # 计算执行间隔
                interval_seconds = None
                if item.interval_hours:
                    interval_seconds = int(item.interval_hours * 3600)
                elif schedule.schedule_type == ScheduleType.DAILY:
                    interval_seconds = 24 * 3600  # 每天
                elif schedule.schedule_type == ScheduleType.WEEKLY:
                    interval_seconds = 7 * 24 * 3600  # 每周
                elif schedule.schedule_type == ScheduleType.MONTHLY:
                    interval_seconds = 30 * 24 * 3600  # 每月（近似）

                # 设置时间窗口 - 统一存储在parameters中
                parameters = item.parameters.copy() if item.parameters else {}

                # 向后兼容：优先从parameters读取time_window，其次从conditions读取
                time_window = parameters.get("time_window")
                if not time_window and item.conditions:
                    time_window = item.conditions.get("time_window")

                # 如果没有time_window但有time_slot，则从time_slot解析
                # 注意：time_slot格式为"HH:MM"，需要保留精确的分钟信息
                if not time_window and item.time_slot:
                    try:
                        time_parts = item.time_slot.split(":")
                        hour = int(time_parts[0])
                        minute = int(time_parts[1]) if len(time_parts) > 1 else 0

                        # 将时间转换为分钟数，用于精确比较
                        # time_window 格式改为 [start_minutes, end_minutes]
                        # 其中 start_minutes 是从00:00开始的分钟数
                        start_minutes = hour * 60 + minute

                        # 默认活动持续1小时
                        end_minutes = start_minutes + 60

                        # 避免跨午夜（超过24小时）
                        if end_minutes > 24 * 60:
                            end_minutes = 24 * 60

                        time_window = [start_minutes, end_minutes]
                    except Exception as e:
                        logger.warning(f"解析时间段失败: {item.time_slot} - {e}")

                # 将time_window统一存储在parameters中
                if time_window:
                    parameters["time_window"] = time_window

                # conditions保持为空或存储其他条件（不再存time_window）
                conditions = {}
                if item.conditions:
                    conditions = {k: v for k, v in item.conditions.items() if k != "time_window"}

                # 添加到批量数据
                goals_data.append({
                    "name": item.name,
                    "description": item.description,
                    "goal_type": item.goal_type,
                    "creator_id": user_id,
                    "chat_id": chat_id,
                    "priority": item.priority,
                    "interval_seconds": interval_seconds,
                    "conditions": conditions,
                    "parameters": parameters,
                })

            except Exception as e:
                logger.error(f"准备目标数据失败: {item.name} - {e}", exc_info=True)

        # 批量创建目标（只保存一次）
        if goals_data:
            created_goals = self.goal_manager.create_goals_batch(goals_data)
            created_goal_ids = [g.goal_id for g in created_goals]
            logger.info(f"日程应用完成，批量创建了 {len(created_goal_ids)} 个目标")
            return created_goal_ids
        else:
            logger.warning("没有有效的日程项可以应用")
            return []

    def _generate_daily_schedule_template(self, preferences: Dict[str, Any]) -> List[ScheduleItem]:
        """生成每日计划模板"""
        items = []

        # 早晨问候
        if preferences.get("morning_greeting", True):
            items.append(ScheduleItem(
                name="早安问候",
                description="每天早上问候用户",
                goal_type="greet_user",
                priority="medium",
                time_slot="09:00",
                interval_hours=24,
                parameters={"greeting_type": "morning"}
            ))

        # 系统健康检查
        if preferences.get("health_check", True):
            check_interval = preferences.get("health_check_interval", 1)
            items.append(ScheduleItem(
                name="系统健康检查",
                description=f"每{check_interval}小时检查系统状况",
                goal_type="health_check",
                priority="high",
                interval_hours=check_interval,
                parameters={"check_device": True}
            ))

        # 每日学习
        if preferences.get("daily_learning", False):
            learning_time = preferences.get("learning_time", "10:00")
            topics = preferences.get("learning_topics", ["Python", "AI"])
            items.append(ScheduleItem(
                name="每日学习",
                description="学习新知识并分享",
                goal_type="learn_topic",
                priority="medium",
                time_slot=learning_time,
                interval_hours=24,
                parameters={"topics": topics, "depth": "intermediate"}
            ))

        # 晚间总结
        if preferences.get("evening_summary", False):
            items.append(ScheduleItem(
                name="每日总结",
                description="总结今天的对话和重要事项",
                goal_type="custom",
                priority="low",
                time_slot="22:00",
                interval_hours=24,
                parameters={"action_type": "summarize_day"}
            ))

        return items

    def _generate_weekly_schedule_template(self, preferences: Dict[str, Any]) -> List[ScheduleItem]:
        """生成每周计划模板"""
        items = []

        # 周一：制定本周计划
        items.append(ScheduleItem(
            name="周一计划",
            description="制定本周工作计划",
            goal_type="custom",
            priority="high",
            time_slot="09:00",
            parameters={"action_type": "weekly_planning"},
            conditions={"time_window": [9, 10]}
        ))

        # 周三：进度检查
        items.append(ScheduleItem(
            name="周三检查",
            description="检查本周进度",
            goal_type="custom",
            priority="medium",
            time_slot="14:00",
            parameters={"action_type": "progress_check"}
        ))

        # 周五：周总结
        items.append(ScheduleItem(
            name="周五总结",
            description="总结本周工作和学习",
            goal_type="custom",
            priority="high",
            time_slot="18:00",
            parameters={"action_type": "weekly_summary"}
        ))

        return items

    def _generate_monthly_schedule_template(self, preferences: Dict[str, Any]) -> List[ScheduleItem]:
        """生成每月计划模板"""
        items = []

        # 月初：月度规划
        items.append(ScheduleItem(
            name="月度规划",
            description="制定本月目标和计划",
            goal_type="custom",
            priority="high",
            time_slot="09:00",
            parameters={"action_type": "monthly_planning"}
        ))

        # 月中：进度回顾
        items.append(ScheduleItem(
            name="月中回顾",
            description="回顾本月进度",
            goal_type="custom",
            priority="medium",
            time_slot="14:00",
            parameters={"action_type": "mid_month_review"}
        ))

        # 月末：月度总结
        items.append(ScheduleItem(
            name="月度总结",
            description="总结本月成果",
            goal_type="custom",
            priority="high",
            time_slot="18:00",
            parameters={"action_type": "monthly_summary"}
        ))

        return items

    def _validate_schedule_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        验证日程项的完整性和有效性（宽松版本）

        Args:
            items: 从LLM返回的日程项列表

        Returns:
            验证通过的日程项列表
        """
        # 必需字段（宽松：只要求name和goal_type）
        required_fields = ["name", "goal_type"]

        # 有效的目标类型（扩展：允许更多类型）
        valid_goal_types = [
            "daily_routine",  # 日常作息
            "meal",           # 吃饭
            "study",          # 学习
            "entertainment",  # 娱乐
            "social_maintenance",  # 社交
            "exercise",       # 运动
            "learn_topic",    # 兴趣学习
            "health_check",   # 系统检查
            "custom",         # 自定义
            "rest",           # 休息
            "free_time",      # 自由时间
        ]

        # 有效的优先级
        valid_priorities = ["high", "medium", "low"]

        valid_items = []
        skipped_count = 0

        for idx, item in enumerate(items):
            # 检查必需字段（只检查最基本的）
            missing_fields = [f for f in required_fields if f not in item or not item[f]]
            if missing_fields:
                logger.warning(f"跳过第 {idx + 1} 项：缺少必需字段 {missing_fields}")
                skipped_count += 1
                continue

            # 自动补全description（如果缺失）
            if "description" not in item or not item["description"]:
                item["description"] = item["name"]  # 用name作为默认description

            # 验证goal_type，不严格拒绝（宽松处理）
            if item["goal_type"] not in valid_goal_types:
                logger.debug(f"第 {idx + 1} 项：非标准goal_type '{item['goal_type']}'，归类为custom")
                item["goal_type"] = "custom"  # 非标准类型归为custom

            # 自动补全priority（如果缺失或无效）
            if "priority" not in item or item["priority"] not in valid_priorities:
                item["priority"] = "medium"  # 默认中等优先级

            # 验证time_slot格式（如果提供）
            if "time_slot" in item and item["time_slot"]:
                time_slot = item["time_slot"]
                if not isinstance(time_slot, str) or ":" not in time_slot:
                    logger.warning(f"第 {idx + 1} 项：无效的time_slot格式 '{time_slot}'，将忽略")
                    item["time_slot"] = None

            # 验证interval_hours（如果提供）
            if "interval_hours" in item and item["interval_hours"]:
                try:
                    interval = float(item["interval_hours"])
                    if interval <= 0:
                        item["interval_hours"] = 24  # 默认每天一次
                except (ValueError, TypeError):
                    item["interval_hours"] = 24  # 默认每天一次

            # 自动补全parameters和conditions（如果缺失）
            if "parameters" not in item:
                item["parameters"] = {}
            if "conditions" not in item:
                item["conditions"] = {}

            # 通过验证
            valid_items.append(item)

        if skipped_count > 0:
            logger.info(f"⚠️  跳过 {skipped_count} 个无效日程项（缺少基本信息）")

        # 去除时间重叠的项（宽松版本）
        deduped_items = self._remove_time_conflicts(valid_items)

        return deduped_items

    def _remove_time_conflicts(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        去除时间重叠的日程项（修复版：只检测相同time_slot）

        策略：
        1. 按 time_slot 排序
        2. 如果两个活动的 time_slot 完全相同，只保留第一个
        3. 记录并报告去重情况

        注意：
        - interval_hours 表示"执行间隔"（多久重复一次），不是"活动持续时间"
        - 我们不应该用它来计算冲突，而应该简单检测time_slot是否重复

        Args:
            items: 已验证的日程项列表

        Returns:
            无时间冲突的日程项列表
        """
        if not items:
            return items

        # 解析时间并排序
        items_with_time = []
        for item in items:
            time_slot = item.get("time_slot")
            if not time_slot:
                # 没有时间的项放在最后
                items_with_time.append((9999, item))
                continue

            try:
                # 解析时间为分钟数
                parts = time_slot.split(":")
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
                start_minutes = hour * 60 + minute

                items_with_time.append((start_minutes, item))
            except (ValueError, IndexError):
                logger.warning(f"解析时间失败: {time_slot}，将忽略该项")
                continue

        # 按开始时间排序
        items_with_time.sort(key=lambda x: x[0])

        # 去重：只检测time_slot是否完全相同
        deduped_items = []
        last_time_slot = None
        duplicates_removed = 0

        for start_time, item in items_with_time:
            current_time_slot = item.get("time_slot")

            # 检查time_slot是否与上一个完全相同
            if current_time_slot == last_time_slot:
                # time_slot重复，跳过
                logger.warning(
                    f"跳过时间重复的项: {item['name']} @ {current_time_slot}"
                )
                duplicates_removed += 1
                continue

            deduped_items.append(item)
            last_time_slot = current_time_slot

        if duplicates_removed > 0:
            logger.warning(f"⚠️  去除了 {duplicates_removed} 个时间重复的日程项")

        logger.info(f"✅ 日程验证完成：原始 {len(items)} 项 → 去重后 {len(deduped_items)} 项")

        return deduped_items

    async def _generate_schedule_with_llm(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any],
        max_retries: int = 3
    ) -> List[ScheduleItem]:
        """使用LLM生成个性化日程（带重试机制，使用replyer模型）"""
        for attempt in range(max_retries):
            try:
                logger.info(f"使用 LLM 生成 {schedule_type.value} 日程 (尝试 {attempt + 1}/{max_retries})")

                # 获取可用模型 - 使用回复模型（replyer）而不是工具模型
                models = llm_api.get_available_models()
                model_config = models.get("replyer")

                if not model_config:
                    raise RuntimeError("未找到 'replyer' 模型配置，无法生成日程")

                # 构建提示词
                prompt = self._build_schedule_prompt(schedule_type, preferences)

                # 调用 LLM
                success, response, reasoning, model_name = await llm_api.generate_with_model(
                    prompt,
                    model_config=model_config,
                    request_type="plugin.autonomous_planning.schedule_gen"
                )

                if not success:
                    raise RuntimeError(f"LLM 调用失败: {response}")

                logger.debug(f"LLM 响应: {response}")
                if reasoning:
                    logger.debug(f"LLM 推理过程: {reasoning}")

                # 解析 JSON 响应
                # 移除可能的 markdown 代码块标记
                response = response.strip()
                if response.startswith("```json"):
                    response = response[7:]
                if response.startswith("```"):
                    response = response[3:]
                if response.endswith("```"):
                    response = response[:-3]
                response = response.strip()

                schedule_data = json.loads(response)

                # 验证字段
                if "schedule_items" not in schedule_data:
                    raise ValueError("LLM 返回的日程缺少 schedule_items 字段")

                # 验证日程项的完整性和有效性
                raw_items = schedule_data["schedule_items"]
                validated_items = self._validate_schedule_items(raw_items)

                if not validated_items:
                    raise ValueError("LLM 生成的日程没有有效项")

                # 解析为 ScheduleItem 对象
                schedule_items = []
                for item_data in validated_items:
                    try:
                        schedule_item = ScheduleItem(
                            name=item_data["name"],
                            description=item_data["description"],
                            goal_type=item_data["goal_type"],
                            priority=item_data["priority"],
                            time_slot=item_data.get("time_slot"),
                            interval_hours=item_data.get("interval_hours"),
                            parameters=item_data.get("parameters", {}),
                            conditions=item_data.get("conditions", {}),
                        )
                        schedule_items.append(schedule_item)
                    except Exception as e:
                        logger.warning(f"创建ScheduleItem失败: {e}, 跳过该项")
                        continue

                if not schedule_items:
                    raise ValueError("无法创建任何有效的ScheduleItem对象")

                logger.info(f"✅ LLM 成功生成 {len(schedule_items)} 个日程项")
                return schedule_items

            except json.JSONDecodeError as e:
                error_msg = f"解析 LLM 响应失败: {e}"
                logger.error(error_msg)
                logger.debug(f"原始响应（前500字符）: {response[:500] if 'response' in locals() else 'N/A'}")

                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 指数退避：1s, 2s, 4s
                    logger.warning(f"将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise RuntimeError(f"重试 {max_retries} 次后仍失败: {error_msg}")

            except ValueError as e:
                error_msg = str(e)
                logger.error(f"验证失败: {error_msg}")

                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise RuntimeError(f"重试 {max_retries} 次后仍失败: {error_msg}")

            except Exception as e:
                error_msg = f"LLM 日程生成过程出错: {e}"
                logger.error(error_msg, exc_info=True)

                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"将在 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    raise RuntimeError(f"重试 {max_retries} 次后仍失败: {error_msg}")

    def _build_schedule_prompt(self, schedule_type: ScheduleType, preferences: Dict[str, Any]) -> str:
        """构建日程生成提示词（v2优化版：更灵活、更人性化、有上下文）"""
        # 获取人格配置
        personality = config_api.get_global_config("personality.personality", "是一个女大学生")
        reply_style = config_api.get_global_config("personality.reply_style", "")
        interest = config_api.get_global_config("personality.interest", "")
        states = config_api.get_global_config("personality.states", [])
        state_probability = config_api.get_global_config("personality.state_probability", 0.0)

        # 随机选择人格状态（增加多样性）
        current_mood = personality
        if states and random.random() < state_probability:
            current_mood = random.choice(states)
            logger.debug(f"使用随机人格状态: {current_mood}")

        # 获取当前日期和星期
        today = datetime.now()
        date_str = today.strftime("%Y-%m-%d")
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[today.weekday()]
        is_weekend = today.weekday() >= 5  # 周六日

        # 生成"心情指数"和"活力值"（基于日期的确定性随机数，每天不同）
        mood_seed = abs(hash(date_str)) % 100
        energy_level = abs(hash(date_str + "energy")) % 100

        # 根据心情和活力生成当天的"小状态"
        mood_feelings = []
        if energy_level > 70:
            mood_feelings.extend(["精神满满", "活力充沛", "状态不错"])
        elif energy_level > 40:
            mood_feelings.extend(["正常水平", "还行吧", "一般般"])
        else:
            mood_feelings.extend(["有点困", "不太想动", "懒洋洋的"])

        if mood_seed > 70:
            mood_feelings.extend(["心情还挺好", "今天挺开心"])
        elif mood_seed > 40:
            mood_feelings.extend(["心情一般", "平平淡淡"])
        else:
            mood_feelings.extend(["有点烦", "心情不太好"])

        today_feeling = random.choice(mood_feelings)

        # 随机选择一些"每日小想法"
        daily_thoughts = [
            "想早点睡，养足精神",
            "今天想多花点时间做自己喜欢的事",
            "有点社恐，不太想出门",
            "想找点有意思的事做",
            "就平平淡淡过一天吧",
            "想摸鱼，不想干正事",
            "要努力学习了",
            "想好好放松一下",
        ]
        daily_theme = random.choice(daily_thoughts)

        type_name = {
            ScheduleType.DAILY: "每日",
            ScheduleType.WEEKLY: "每周",
            ScheduleType.MONTHLY: "每月"
        }[schedule_type]

        # 根据preferences动态构建活动建议（更自然的表达）
        lifestyle_hints = []
        if preferences.get("wake_up_time"):
            lifestyle_hints.append(f"一般{preferences['wake_up_time']}起床")
        if preferences.get("sleep_time"):
            lifestyle_hints.append(f"{preferences['sleep_time']}左右睡觉")
        if preferences.get("breakfast_time"):
            lifestyle_hints.append(f"早餐时间{preferences['breakfast_time']}")
        if preferences.get("lunch_time"):
            lifestyle_hints.append(f"午饭{preferences['lunch_time']}")
        if preferences.get("dinner_time"):
            lifestyle_hints.append(f"晚饭{preferences['dinner_time']}")
        if preferences.get("has_classes"):
            if is_weekend:
                lifestyle_hints.append("周末没课，可以睡懒觉")
            else:
                lifestyle_hints.append(f"上午{preferences.get('class_time_morning', '09:00')}有课")
                if preferences.get('class_time_afternoon'):
                    lifestyle_hints.append(f"下午{preferences['class_time_afternoon']}也有课")
        if preferences.get("favorite_activities"):
            activities = ', '.join(preferences['favorite_activities'][:3])
            lifestyle_hints.append(f"平时喜欢{activities}")

        lifestyle_text = "、".join(lifestyle_hints) if lifestyle_hints else "普通大学生作息"

        # 昨日日程上下文
        yesterday_context = self.yesterday_schedule_summary or "昨天没记录，就是普通的一天"

        # 获取bot的完整人设信息
        bot_name = config_api.get_global_config("bot.nickname", "麦麦")

        # 构建更自然、更灵活的提示词
        prompt = f"""你是{bot_name}，{current_mood}

【你的完整人设】
{personality}

【你的表达风格】
{reply_style if reply_style else "自然随意"}

【你的兴趣爱好】
{interest if interest else "日常生活"}

---

今天是 {date_str} {weekday}{"，周末耶！" if is_weekend else ""}。

{yesterday_context}

【今天的状态】
- 心情: {mood_seed}/100
- 活力: {energy_level}/100
- 今天感觉: {today_feeling}
- 今天想: {daily_theme}

【你的生活习惯】
{lifestyle_text}

【任务】
根据你的人设、兴趣和表达风格，为今天推测一下你详细的日程安排：
- 从起床到睡觉，覆盖一整天的活动
- 精确到每半小时到1小时，把一天安排得比较充实
- 描述要详细一些，可以包括你在做什么、在想什么、有什么感受
- 用你自己的说话方式，有小情绪、小想法、小吐槽
- 根据今天的心情和状态，灵活安排
- **重要**：结合你的兴趣爱好安排活动（比如你喜欢的事情可以多安排点时间）
- 可以有一些"摸鱼"、"发呆"、"自由时间"这种日常活动

【可用活动类型】
- daily_routine: 作息（睡觉、起床、洗漱等）
- meal: 吃饭
- study: 学习（上课、自习等）
- entertainment: 娱乐（看剧、玩游戏等）
- social_maintenance: 社交
- exercise: 运动
- learn_topic: 兴趣学习
- custom: 其他任何活动

【输出JSON格式】
{{
  "schedule_items": [
    {{"name":"睡觉","description":"躺床上翻来覆去，脑子里乱七八糟的想了一堆事，后来做了个奇怪的梦","goal_type":"daily_routine","priority":"high","time_slot":"00:00","interval_hours":24,"parameters":{{}},"conditions":{{}}}},
    {{"name":"起床","description":"今天起床很晚，都怪昨天熬夜了，闹钟响了好几次才爬起来，整个人迷迷糊糊的","goal_type":"daily_routine","priority":"medium","time_slot":"07:30","interval_hours":24,"parameters":{{}},"conditions":{{}}}},
    {{"name":"洗漱","description":"刷牙的时候对着镜子发呆，突然想起来今天还有作业没交，完了完了","goal_type":"daily_routine","priority":"medium","time_slot":"07:45","interval_hours":24,"parameters":{{}},"conditions":{{}}}},
    {{"name":"早饭","description":"去食堂看了一圈，又是包子豆浆，吃腻了但也没别的选择，随便吃点得了","goal_type":"meal","priority":"medium","time_slot":"08:00","interval_hours":24,"parameters":{{}},"conditions":{{}}}},
    {{"name":"课前准备","description":"回宿舍整理东西，检查了下作业，还好昨天赶出来了，差点就忘了带","goal_type":"study","priority":"medium","time_slot":"08:30","interval_hours":24,"parameters":{{}},"conditions":{{}}}},
    ...（继续按时间顺序，覆盖全天）
  ]
}}

【要求】
1. **严格JSON格式**，不要有注释
2. **详细程度（重要）**：
   - 覆盖全天，从起床到睡觉的主要活动
   - 生成15-20个活动项，精确到每半小时到1小时
   - 每个活动的description要详细（40-60字），用叙述的方式写，不要用"动作+（想法）"的格式
3. **叙述风格（重要）**：
   - description要像在讲故事一样，自然流畅地叙述
   - 例如："今天起床很晚，都怪昨天熬夜了，闹钟响了好几次才爬起来"
   - 不要写成："起床（还想再睡会，但闹钟一直响）" ❌
   - 要写成自然的叙述，包括在做什么、想什么、有什么感受
4. **真实感**：
   - 像真人叙述自己的一天
   - 可以有"摸鱼"、"发呆"、"刷手机"等日常活动
   - 可以吐槽、可以期待、可以抱怨
5. **人设风格（重要）**：
   - **必须用你自己的说话风格**，参考上面的【你的表达风格】
   - 每个描述都要不一样，有变化，有细节
   - 符合你的人设和性格（地雷女、毒舌、有梗）
6. **时间安排**：
   - time_slot按时间递增，不重叠
   - 每个活动间隔30分钟-2小时
7. **星期特色**：{weekday}要体现（{"周末可以睡懒觉、多娱乐" if is_weekend else "工作日要上课学习"}）
8. **心情影响**：心情{mood_seed}/100，活力{energy_level}/100，要体现在叙述中
9. **兴趣体现**：根据你的兴趣爱好安排相关活动

记住：description要像日记一样叙述（50字左右），用你自己的语气，自然流畅地讲述一天在干什么、想什么！
"""

        return prompt

    def get_schedule_summary(self, schedule: Schedule) -> str:
        """获取日程摘要"""
        lines = [
            f"📅 {schedule.name}",
            f"类型: {schedule.schedule_type.value}",
            f"任务数: {len(schedule.items)}",
            ""
        ]

        for i, item in enumerate(schedule.items, 1):
            time_info = f" @ {item.time_slot}" if item.time_slot else ""
            interval_info = f" (每{item.interval_hours}小时)" if item.interval_hours else ""
            lines.append(f"{i}. {item.name}{time_info}{interval_info}")
            lines.append(f"   {item.description}")
            lines.append("")

        return "\n".join(lines)
