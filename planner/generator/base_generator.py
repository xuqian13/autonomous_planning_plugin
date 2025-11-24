"""Base Generator Module.

This module provides base configuration and utility methods for schedule generation.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system.apis import config_api, llm_api

from ..goal_manager import GoalManager

logger = get_logger("autonomous_planning.base_generator")


class BaseScheduleGenerator:
    """基础日程生成器 - 提供配置和工具方法"""

    def __init__(self, goal_manager: GoalManager, config: Optional[Dict[str, Any]] = None):
        """
        初始化基础生成器

        Args:
            goal_manager: 目标管理器
            config: 配置字典（可选）
        """
        self.goal_manager = goal_manager
        self.yesterday_schedule_summary = None  # 昨日日程摘要（用于上下文）
        self.config = config or {}  # 保存配置

    def get_model_config(self) -> Tuple[Dict[str, Any], int, float]:
        """
        获取模型配置（优先使用自定义模型，否则使用主回复模型）

        Returns:
            (TaskConfig对象, max_tokens, temperature)
        """
        try:
            # 从插件配置读取 max_tokens（统一配置）
            max_tokens = self.config.get("max_tokens", 8192)

            # 检查是否启用自定义模型
            custom_model_config = self.config.get("custom_model", {})
            custom_enabled = custom_model_config.get("enabled", False)

            if custom_enabled:
                # 使用自定义模型
                model_name = custom_model_config.get("model_name", "").strip()
                api_base = custom_model_config.get("api_base", "").strip()
                api_key = custom_model_config.get("api_key", "").strip()
                provider = custom_model_config.get("provider", "openai").strip()
                temperature = custom_model_config.get("temperature", 0.7)

                if not model_name or not api_base or not api_key:
                    logger.warning("自定义模型配置不完整，回退到主回复模型")
                    return self._get_default_model_config()

                logger.info(f"使用自定义模型: {model_name} @ {api_base} (max_tokens={max_tokens}, temperature={temperature})")

                # 构建自定义模型配置 - 需要创建完整的配置对象
                from src.config.api_ada_configs import APIProvider, ModelInfo, TaskConfig
                from src.config.config import model_config as global_model_config

                # 创建临时的API提供商配置
                temp_provider_name = f"custom_schedule_provider"
                temp_provider = APIProvider(
                    name=temp_provider_name,
                    base_url=api_base,
                    api_key=api_key,
                    client_type=provider,
                    max_retry=2,
                    timeout=120,
                )

                # 创建临时的模型信息
                temp_model_name = f"custom_schedule_model"
                temp_model_info = ModelInfo(
                    model_identifier=model_name,
                    name=temp_model_name,
                    api_provider=temp_provider_name,
                )

                # 注册到全局配置
                global_model_config.api_providers_dict[temp_provider_name] = temp_provider
                global_model_config.models_dict[temp_model_name] = temp_model_info

                # 创建TaskConfig（不设置max_tokens和temperature，由调用时传入）
                task_config = TaskConfig(
                    model_list=[temp_model_name],
                )

                return task_config, max_tokens, temperature
            else:
                # 使用默认的主回复模型
                return self._get_default_model_config()

        except Exception as e:
            logger.warning(f"获取自定义模型配置失败: {e}，使用主回复模型", exc_info=True)
            return self._get_default_model_config()

    def _get_default_model_config(self) -> Tuple[Dict[str, Any], int, float]:
        """
        获取默认模型配置（主回复模型）

        Returns:
            (模型配置字典, max_tokens, temperature)
        """
        models = llm_api.get_available_models()
        model_config = models.get("replyer")

        if not model_config:
            raise RuntimeError("未找到 'replyer' 模型配置")

        # 从插件配置读取 max_tokens（统一配置）
        max_tokens = self.config.get("max_tokens", 8192)

        # 从主回复模型配置读取 temperature
        temperature = getattr(model_config, 'temperature', 0.7)

        logger.info(f"使用主回复模型 (max_tokens={max_tokens}, temperature={temperature})")

        return model_config, max_tokens, temperature

    def build_json_schema(self) -> dict:
        """
        构建JSON Schema，约束LLM输出格式

        优势：
        1. 强制类型检查（时间格式必须是HH:MM）
        2. 枚举约束（goal_type只能是预定义值）
        3. 必填字段检查
        4. 长度限制（防止过长或过短）

        Returns:
            JSON Schema字典
        """
        # 从配置读取参数
        min_activities = self.config.get('min_activities', 8)
        max_activities = self.config.get('max_activities', 15)
        min_desc_len = self.config.get('min_description_length', 15)
        max_desc_len = self.config.get('max_description_length', 50)

        return {
            "type": "object",
            "required": ["schedule_items"],
            "properties": {
                "schedule_items": {
                    "type": "array",
                    "minItems": min_activities,
                    "maxItems": max_activities,
                    "items": {
                        "type": "object",
                        "required": ["name", "description", "time_slot", "goal_type", "priority"],
                        "properties": {
                            "name": {
                                "type": "string",
                                "minLength": 2,
                                "maxLength": 20,
                                "description": "活动名称"
                            },
                            "description": {
                                "type": "string",
                                "minLength": min_desc_len,
                                "maxLength": max_desc_len,
                                "description": f"活动描述（叙述风格，{min_desc_len}-{max_desc_len}字）"
                            },
                            "time_slot": {
                                "type": "string",
                                "pattern": "^([01]?[0-9]|2[0-3]):[0-5][0-9]$",
                                "description": "时间点，HH:MM格式（如09:30）"
                            },
                            "goal_type": {
                                "type": "string",
                                "enum": [
                                    "daily_routine",      # 日常作息
                                    "meal",               # 吃饭
                                    "study",              # 学习
                                    "entertainment",      # 娱乐
                                    "social_maintenance", # 社交
                                    "exercise",           # 运动
                                    "learn_topic",        # 兴趣学习
                                    "rest",               # 休息
                                    "free_time",          # 自由时间
                                    "custom"              # 自定义
                                ],
                                "description": "活动类型"
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "优先级"
                            },
                            "duration_hours": {
                                "type": "number",
                                "minimum": 0.25,
                                "maximum": 12,
                                "description": "活动持续时长（小时）"
                            },
                            "parameters": {
                                "type": "object",
                                "description": "额外参数"
                            },
                            "conditions": {
                                "type": "object",
                                "description": "执行条件"
                            }
                        }
                    }
                }
            }
        }

    def load_yesterday_schedule_summary(self) -> Optional[str]:
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

    def build_schedule_prompt(self, schedule_type, preferences: Dict[str, Any], schema: Optional[Dict] = None) -> str:
        """构建日程生成提示词（精简版）"""
        # 获取配置
        personality = config_api.get_global_config("personality.personality", "是一个女大学生")
        reply_style = config_api.get_global_config("personality.reply_style", "")
        interest = config_api.get_global_config("personality.interest", "")
        bot_name = config_api.get_global_config("bot.nickname", "麦麦")

        # 从配置读取生成参数
        min_activities = self.config.get('min_activities', 8)
        max_activities = self.config.get('max_activities', 15)
        min_desc_len = self.config.get('min_description_length', 15)
        max_desc_len = self.config.get('max_description_length', 50)

        # 🆕 读取自定义prompt配置
        custom_prompt = self.config.get('custom_prompt', '').strip()

        # 时间信息
        today = datetime.now()
        date_str = today.strftime("%Y-%m-%d")
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekday_names[today.weekday()]
        is_weekend = today.weekday() >= 5

        # 状态生成
        mood_seed = abs(hash(date_str)) % 100
        energy_level = abs(hash(date_str + "energy")) % 100

        # 昨日上下文
        yesterday_context = self.yesterday_schedule_summary or "昨天普通的一天"

        # 核心提示词（精简版）
        prompt = f"""你是{bot_name}，{personality}

今天是{date_str} {weekday}{"（周末）" if is_weekend else ""}
昨天: {yesterday_context}
状态: 心情{mood_seed}/100，活力{energy_level}/100
"""

        # 🆕 添加自定义prompt（如果配置了）
        if custom_prompt:
            prompt += f"""
【特殊要求】
{custom_prompt}
"""

        prompt += f"""
【任务】生成今天的详细日程JSON：
1. {min_activities}-{max_activities}个活动，覆盖全天（00:00起床到睡觉）
2. 每个description {min_desc_len}-{max_desc_len}字，用自然叙述风格（像日记）
3. 体现人设：{personality[:50]}...
4. 兴趣相关：{interest if interest else "日常生活"}
5. 表达风格：{reply_style[:30] if reply_style else "自然随意"}
"""

        # 如果有自定义prompt，强调一下
        if custom_prompt:
            prompt += f"6. ⚠️ 优先满足上述【特殊要求】的内容\n"

        prompt += """
【活动类型】
daily_routine(作息)|meal(吃饭)|study(学习)|entertainment(娱乐)|social_maintenance(社交)|exercise(运动)|learn_topic(兴趣)|custom(其他)

【JSON格式示例】
{
  "schedule_items": [
    {"name":"睡觉","description":"蜷在被窝里睡得很香","goal_type":"daily_routine","priority":"high","time_slot":"00:00","duration_hours":7.5},
    {"name":"起床","description":"迷迷糊糊爬起来","goal_type":"daily_routine","priority":"medium","time_slot":"07:30","duration_hours":0.25},
    {"name":"早餐","description":"简单吃了点东西","goal_type":"meal","priority":"medium","time_slot":"08:00","duration_hours":0.5},
    ..."""

        prompt += f"""（继续{min_activities}-{max_activities}个活动）
  ]
}}

⚠️ 重要：duration_hours 表示活动的持续时长（小时），不是重复间隔！
- 睡觉 00:00 持续7.5小时 → 结束于 07:30
- 起床 07:30 持续0.25小时（15分钟） → 结束于 07:45
- 早餐 08:00 持续0.5小时（30分钟） → 结束于 08:30

【时间合理性要求 - 重要！】
⚠️ 必须同时满足以下两点：
1. 无缝覆盖全天：每个活动结束时间 = 下个活动开始时间
2. 遵守常识性时间安排，参考以下顺序：
   • 00:00-07:30  睡觉 (7-8小时)
   • 07:30-08:00  起床/洗漱
   • 08:00-08:30  早餐 ← 必须在 06:00-09:00
   • 08:30-12:00  上午活动（学习/娱乐/社交）
   • 12:00-12:30  午餐 ← 必须在 11:00-14:00
   • 12:30-18:00  下午活动
   • 18:00-18:30  晚餐 ← 必须在 17:00-20:00
   • 18:30-23:00  晚间活动（娱乐/社交/夜聊）
   • 23:00-00:00  睡前准备 → 回到 00:00

【要求】
- 严格JSON格式，无注释
- time_slot按时间递增（HH:MM格式）
- ⚠️ 必须无缝覆盖全天：每个活动结束时间 = 下个活动开始时间，不能有空档
- ⚠️ 关键活动时间必须合理：早餐6-9点、午餐11-14点、晚餐17-20点、睡觉从22-2点开始
- description简洁自然，{min_desc_len}-{max_desc_len}字
- 体现{weekday}特色（{"周末睡懒觉" if is_weekend else "工作日早起"}）
- 符合心情{mood_seed}和活力{energy_level}
"""

        # 添加Schema约束（精简版）
        if schema:
            prompt += f"""
【Schema要求】
- {min_activities}-{max_activities}个活动（必须）
- 必填：name(2-20字), description({min_desc_len}-{max_desc_len}字), time_slot, goal_type, priority
- priority: high/medium/low
- duration_hours: 0.25-12（活动持续时长，小时）

Schema: {json.dumps(schema.get('properties', {}).get('schedule_items', {}), ensure_ascii=False)}
"""

        return prompt

    def build_retry_prompt(
        self,
        schedule_type,
        preferences: Dict[str, Any],
        schema: Dict,
        previous_issues: List[str]
    ) -> str:
        """
        构建第二轮prompt（附带反馈）

        Args:
            schedule_type: 日程类型
            preferences: 用户偏好
            schema: JSON Schema
            previous_issues: 上一轮的问题列表

        Returns:
            改进后的提示词
        """
        base_prompt = self.build_schedule_prompt(schedule_type, preferences, schema)

        feedback = "\n\n⚠️ **上一次生成存在以下问题，请改进：**\n\n"
        for idx, issue in enumerate(previous_issues[:5], 1):  # 只列出前5个
            feedback += f"{idx}. {issue}\n"

        feedback += "\n**请重新生成一个更合理的日程，特别注意以上问题！**\n"

        return base_prompt + feedback
