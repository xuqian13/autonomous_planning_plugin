"""Base Generator Module.

职责：
    - 模型任务名解析（供 ScheduleGenerator 在调 ``ctx.llm.generate`` 时使用）
    - 组件协调（PromptBuilder / SchemaBuilder / ScheduleContextLoader / TimezoneManager）

子组件细化职责：
    - Prompt 构建 → PromptBuilder
    - Schema 构建 → SchemaBuilder
    - 上下文加载 → ScheduleContextLoader
    - 时区管理 → TimezoneManager
"""

from typing import Any, Dict, List, Optional, Tuple
import logging

from ...utils.timezone_manager import TimezoneManager
from ..goal_manager import GoalManager
from .context_loader import ScheduleContextLoader
from .prompt_builder import PromptBuilder
from .schema_builder import SchemaBuilder

logger = logging.getLogger(__name__)


# 默认模型任务名（需在主程序 model_config.toml 中预先配置）
DEFAULT_LLM_TASK_NAME = "replyer"
DEFAULT_TEMPERATURE = 0.85  # v4.1：从 0.7 提到 0.85，让日程描述更有变化（schema 校验兜底防跑偏）


class BaseScheduleGenerator:
    """基础日程生成器：模型任务名 + 组件协调。

    各子组件通过依赖注入装配，``ScheduleGenerator`` 继承此类获得 Prompt / Schema /
    Context 构建能力。
    """

    def __init__(self, goal_manager: GoalManager, config: Optional[Dict[str, Any]] = None) -> None:
        """初始化基础生成器。

        Args:
            goal_manager: 目标管理器
            config: 配置字典（可选）
        """
        self.goal_manager = goal_manager
        self.yesterday_schedule_summary: Optional[str] = None  # 昨日日程摘要（用于上下文）
        # 动态上下文（由 ScheduleGenerator 在生成前异步注入）
        self.pending_commitments: Optional[List[Dict[str, Any]]] = None
        self.history_context: str = ""
        self.knowledge_context: str = ""
        self.config: Dict[str, Any] = config or {}  # 保存配置

        # 初始化时区管理器
        timezone_str = self.config.get("timezone", "Asia/Shanghai")
        self.tz_manager = TimezoneManager(timezone_str)

        # 初始化子组件（依赖注入）
        self.prompt_builder = PromptBuilder(self.config, self.tz_manager)
        self.schema_builder = SchemaBuilder(self.config)
        self.context_loader = ScheduleContextLoader(goal_manager, self.tz_manager)

    # ========================================================================
    # 模型配置
    # ========================================================================

    def get_model_config(self) -> Tuple[str, int, float]:
        """获取 LLM 调用参数。

        返回主程序 ``model_config.toml`` 中预先配置的任务名字符串，由
        ScheduleGenerator 在调用 ``ctx.llm.generate(task_name=task_name, ...)`` 时使用。

        Returns:
            ``(task_name, max_tokens, temperature)`` 三元组
        """
        task_name = str(self.config.get("llm_task_name", DEFAULT_LLM_TASK_NAME)).strip() or DEFAULT_LLM_TASK_NAME
        max_tokens = int(self.config.get("max_tokens", 8192))
        temperature = float(self.config.get("temperature", DEFAULT_TEMPERATURE))

        logger.debug(f"LLM 任务名: {task_name} (max_tokens={max_tokens}, temperature={temperature})")
        return task_name, max_tokens, temperature

    # ========================================================================
    # 委托方法（薄包装，子组件承担实际逻辑）
    # ========================================================================

    def build_json_schema(self) -> dict:
        """构建 JSON Schema（委托给 SchemaBuilder）。

        Returns:
            JSON Schema 字典
        """
        return self.schema_builder.build_json_schema()

    def load_yesterday_schedule_summary(self) -> Optional[str]:
        """加载昨日日程摘要（委托给 ContextLoader）。

        Returns:
            昨日日程摘要字符串
        """
        summary = self.context_loader.load_yesterday_schedule_summary()
        self.yesterday_schedule_summary = summary  # 缓存到实例
        return summary

    def build_schedule_prompt(
        self,
        schedule_type: Any,
        preferences: Dict[str, Any],
        schema: Optional[Dict] = None,
    ) -> str:
        """构建日程生成提示词（委托给 PromptBuilder）。

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
            self.yesterday_schedule_summary,
            pending_commitments=self.pending_commitments,
            history_context=self.history_context,
            knowledge_context=self.knowledge_context,
        )

    def build_retry_prompt(
        self,
        schedule_type: Any,
        preferences: Dict[str, Any],
        schema: Dict,
        previous_issues: List[str],
    ) -> str:
        """构建第二轮 prompt（委托给 PromptBuilder）。

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
            self.yesterday_schedule_summary,
            pending_commitments=self.pending_commitments,
            history_context=self.history_context,
            knowledge_context=self.knowledge_context,
        )
