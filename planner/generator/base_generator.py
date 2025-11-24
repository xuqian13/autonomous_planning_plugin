"""Base Generator Module.

This module provides base configuration and utility methods for schedule generation.

REFACTORED: Separated concerns into specialized components:
- PromptBuilder: Prompt construction
- SchemaBuilder: JSON Schema definition
- ScheduleContextLoader: Historical context loading

This class now focuses on model configuration only.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.common.logger import get_logger
from src.plugin_system.apis import config_api, llm_api

from ..goal_manager import GoalManager
from ...utils.timezone_manager import TimezoneManager
from .prompt_builder import PromptBuilder
from .schema_builder import SchemaBuilder
from .context_loader import ScheduleContextLoader

logger = get_logger("autonomous_planning.base_generator")


class BaseScheduleGenerator:
    """基础日程生成器 - 提供配置和工具方法（重构版）

    职责（重构后）：
    - 模型配置管理
    - 组件协调（PromptBuilder、SchemaBuilder、ContextLoader）
    - 向后兼容的API

    已移除职责（迁移到专门组件）：
    - Prompt构建 → PromptBuilder
    - Schema构建 → SchemaBuilder
    - 上下文加载 → ScheduleContextLoader
    - 时区管理 → TimezoneManager
    """

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

        # 初始化时区管理器
        timezone_str = self.config.get("timezone", "Asia/Shanghai")
        self.tz_manager = TimezoneManager(timezone_str)

        # 初始化组件（依赖注入）
        self.prompt_builder = PromptBuilder(self.config, self.tz_manager)
        self.schema_builder = SchemaBuilder(self.config)
        self.context_loader = ScheduleContextLoader(goal_manager, self.tz_manager)

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

    # ========================================================================
    # 向后兼容的委托方法（调用新组件）
    # ========================================================================

    def build_json_schema(self) -> dict:
        """构建JSON Schema（委托给SchemaBuilder）

        Returns:
            JSON Schema字典
        """
        return self.schema_builder.build_json_schema()

    def load_yesterday_schedule_summary(self) -> Optional[str]:
        """加载昨日日程摘要（委托给ContextLoader）

        Returns:
            昨日日程摘要字符串
        """
        summary = self.context_loader.load_yesterday_schedule_summary()
        self.yesterday_schedule_summary = summary  # 保存到实例变量（向后兼容）
        return summary

    def build_schedule_prompt(
        self,
        schedule_type,
        preferences: Dict[str, Any],
        schema: Optional[Dict] = None
    ) -> str:
        """构建日程生成提示词（委托给PromptBuilder）

        Args:
            schedule_type: 日程类型
            preferences: 用户偏好
            schema: JSON Schema（可选）

        Returns:
            提示词字符串
        """
        return self.prompt_builder.build_schedule_prompt(
            schedule_type,
            preferences,
            schema,
            self.yesterday_schedule_summary
        )

    def build_retry_prompt(
        self,
        schedule_type,
        preferences: Dict[str, Any],
        schema: Dict,
        previous_issues: List[str]
    ) -> str:
        """构建第二轮prompt（委托给PromptBuilder）

        Args:
            schedule_type: 日程类型
            preferences: 用户偏好
            schema: JSON Schema
            previous_issues: 上一轮的问题列表

        Returns:
            改进后的提示词
        """
        return self.prompt_builder.build_retry_prompt(
            schedule_type,
            preferences,
            schema,
            previous_issues,
            self.yesterday_schedule_summary
        )
