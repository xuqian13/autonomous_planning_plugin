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
                if not time_window and item.time_slot:
                    try:
                        hour = int(item.time_slot.split(":")[0])
                        time_window = [hour, hour + 1]
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
        验证日程项的完整性和有效性

        Args:
            items: 从LLM返回的日程项列表

        Returns:
            验证通过的日程项列表
        """
        # 必需字段
        required_fields = ["name", "description", "goal_type", "priority"]

        # 有效的目标类型
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
        ]

        # 有效的优先级
        valid_priorities = ["high", "medium", "low"]

        valid_items = []
        skipped_count = 0

        for idx, item in enumerate(items):
            # 检查必需字段
            missing_fields = [f for f in required_fields if f not in item or not item[f]]
            if missing_fields:
                logger.warning(f"跳过第 {idx + 1} 项：缺少必需字段 {missing_fields}")
                skipped_count += 1
                continue

            # 验证goal_type是否有效
            if item["goal_type"] not in valid_goal_types:
                logger.warning(f"跳过第 {idx + 1} 项：无效的goal_type '{item['goal_type']}'")
                skipped_count += 1
                continue

            # 验证priority是否有效
            if item["priority"] not in valid_priorities:
                logger.warning(f"第 {idx + 1} 项：无效的priority '{item['priority']}'，使用默认值 'medium'")
                item["priority"] = "medium"

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
                        logger.warning(f"第 {idx + 1} 项：interval_hours必须大于0，将忽略")
                        item["interval_hours"] = None
                except (ValueError, TypeError):
                    logger.warning(f"第 {idx + 1} 项：无效的interval_hours '{item['interval_hours']}'，将忽略")
                    item["interval_hours"] = None

            # 通过验证
            valid_items.append(item)

        if skipped_count > 0:
            logger.warning(f"共跳过 {skipped_count} 个无效日程项")

        return valid_items

    async def _generate_schedule_with_llm(
        self,
        schedule_type: ScheduleType,
        user_id: str,
        chat_id: str,
        preferences: Dict[str, Any],
        max_retries: int = 3
    ) -> List[ScheduleItem]:
        """使用LLM生成个性化日程（带重试机制）"""
        for attempt in range(max_retries):
            try:
                logger.info(f"使用 LLM 生成 {schedule_type.value} 日程 (尝试 {attempt + 1}/{max_retries})")

                # 获取可用模型
                models = llm_api.get_available_models()
                model_config = models.get("utils")

                if not model_config:
                    raise RuntimeError("未找到 'utils' 模型配置，无法生成日程")

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
        """构建日程生成提示词（优化版，融合人格配置和每日多样性）"""
        # 获取人格配置
        personality = config_api.get_global_config("personality.personality", "是一个女大学生")
        reply_style = config_api.get_global_config("personality.reply_style", "")
        interest = config_api.get_global_config("personality.interest", "")
        states = config_api.get_global_config("personality.states", [])
        state_probability = config_api.get_global_config("personality.state_probability", 0.0)

        # 随机选择人格状态（增加多样性）
        if states and random.random() < state_probability:
            personality = random.choice(states)
            logger.debug(f"使用随机人格状态: {personality}")

        # 获取当前日期和星期（用于每日多样性）
        today = datetime.now()
        date_str = today.strftime("%Y-%m-%d")
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[today.weekday()]

        # 生成"心情指数"（基于日期的确定性随机数，用于增加日程变化）
        mood_index = abs(hash(date_str)) % 100

        type_name = {
            ScheduleType.DAILY: "每日",
            ScheduleType.WEEKLY: "每周",
            ScheduleType.MONTHLY: "每月"
        }[schedule_type]

        # 根据preferences动态构建活动建议
        activity_suggestions = []
        if preferences.get("wake_up_time"):
            activity_suggestions.append(f"起床时间: {preferences['wake_up_time']}")
        if preferences.get("sleep_time"):
            activity_suggestions.append(f"睡觉时间: {preferences['sleep_time']}")
        if preferences.get("breakfast_time"):
            activity_suggestions.append(f"早餐: {preferences['breakfast_time']}")
        if preferences.get("lunch_time"):
            activity_suggestions.append(f"午餐: {preferences['lunch_time']}")
        if preferences.get("dinner_time"):
            activity_suggestions.append(f"晚餐: {preferences['dinner_time']}")
        if preferences.get("has_classes"):
            activity_suggestions.append(f"上课: 上午{preferences.get('class_time_morning', '09:00')}, 下午{preferences.get('class_time_afternoon', '14:00')}")
        if preferences.get("study_time"):
            activity_suggestions.append(f"学习: {preferences['study_time']}")
        if preferences.get("entertainment_time"):
            activity_suggestions.append(f"娱乐: {preferences['entertainment_time']}")
        if preferences.get("favorite_activities"):
            activity_suggestions.append(f"喜欢: {', '.join(preferences['favorite_activities'][:3])}")

        suggestions_text = "\n".join([f"  {s}" for s in activity_suggestions]) if activity_suggestions else "  参考正常大学生作息"

        prompt = f"""你{personality}

今天是 {date_str} {weekday}，心情指数 {mood_index}/100。

【你的特点】
- 表达方式：{reply_style if reply_style else "随意自然，像普通大学生"}
- 兴趣爱好：{interest if interest else "日常生活"}

【任务】
为今天生成{type_name}生活日程。日程描述要用你自己的说话风格，不要太正式，像是自己给自己做的计划。

【用户偏好】
{suggestions_text}

【目标类型】
- daily_routine(作息) meal(吃饭) study(学习) entertainment(娱乐)
- social_maintenance(社交) exercise(运动) learn_topic(兴趣学习) custom(其他)

【输出格式】返回JSON：
{{
  "schedule_items": [
    {{"name":"起床","description":"新的一天开始啦","goal_type":"daily_routine","priority":"medium","time_slot":"07:30","interval_hours":24,"parameters":{{}},"conditions":{{}}}}
  ]
}}

【要求】
1. 严格JSON格式，无注释
2. 真实大学生生活，不是机器人式任务列表
3. 包含：起床、三餐、学习、娱乐、睡觉等日常活动
4. 每项指定time_slot（HH:MM格式）
5. 生成8-12个活动
6. priority: high/medium/low
7. **重要**：根据今天的日期({date_str})和星期({weekday})调整活动安排
8. **重要**：描述要用你自己的风格，平淡简短，像贴吧/知乎/微博的语气
9. **重要**：每天的日程应该有所不同，活动时间、顺序、描述都要有变化
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
