"""Schedule Generator Configuration Module.

This module provides centralized configuration management for schedule generation,
following the DRY principle by avoiding repeated config.get() calls.

Classes:
    ScheduleGeneratorConfig: Configuration manager with validation and defaults

Example:
    >>> config_dict = {'min_activities': 8, 'max_activities': 15}
    >>> config = ScheduleGeneratorConfig(config_dict)
    >>> config.min_activities
    8
"""

from typing import Any, Dict, Optional

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.config")


class ScheduleGeneratorConfig:
    """日程生成器配置类

    职责：
    - 集中管理所有配置参数
    - 提供默认值
    - 配置验证
    - 计算属性（如target_description_length）

    优势：
    - DRY原则：避免重复的config.get()调用
    - 类型安全：通过属性访问，IDE可以提示
    - 验证集中：所有验证逻辑在一处
    - 易于测试：可以轻松创建测试配置

    Args:
        config_dict: 配置字典（通常从config.toml读取）

    Examples:
        >>> config = ScheduleGeneratorConfig({
        ...     'min_activities': 10,
        ...     'max_activities': 20
        ... })
        >>> config.min_activities
        10
        >>> config.max_activities
        20
    """

    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        """初始化配置

        Args:
            config_dict: 配置字典（可选），如果为None则使用默认值
        """
        config_dict = config_dict or {}

        # === 活动数量配置 ===
        self.min_activities = config_dict.get('min_activities', 8)
        self.max_activities = config_dict.get('max_activities', 15)

        # === 描述长度配置 ===
        self.min_description_length = config_dict.get('min_description_length', 15)
        self.max_description_length = config_dict.get('max_description_length', 50)

        # === 多轮生成配置 ===
        self.use_multi_round = config_dict.get('use_multi_round', True)
        self.max_rounds = config_dict.get('max_rounds', 2)
        self.quality_threshold = config_dict.get('quality_threshold', 0.85)

        # === 模型配置 ===
        self.max_tokens = config_dict.get('max_tokens', 8192)
        self.generation_timeout = config_dict.get('generation_timeout', 180.0)

        # === 自定义Prompt ===
        self.custom_prompt = config_dict.get('custom_prompt', '').strip()

        # === 缓存配置 ===
        self.cache_ttl = config_dict.get('cache_ttl', 300)
        self.cache_max_size = config_dict.get('cache_max_size', 100)

        # === 自定义模型配置 ===
        self.custom_model = config_dict.get('custom_model', {})

        # 保存原始配置字典（用于传递给子组件）
        self._raw_config = config_dict

        # 验证配置
        self._validate()

        logger.debug(f"配置已加载: 活动数[{self.min_activities}-{self.max_activities}], "
                    f"描述长度[{self.min_description_length}-{self.max_description_length}], "
                    f"多轮生成={self.use_multi_round}")

    def _validate(self):
        """验证配置的合法性

        Raises:
            ValueError: 配置不合法时抛出

        Examples:
            >>> try:
            ...     config = ScheduleGeneratorConfig({'min_activities': 20, 'max_activities': 10})
            ... except ValueError as e:
            ...     print("配置错误")
            配置错误
        """
        # 验证活动数量
        if self.min_activities > self.max_activities:
            raise ValueError(
                f"min_activities ({self.min_activities}) 不能大于 "
                f"max_activities ({self.max_activities})"
            )

        if self.min_activities < 1:
            raise ValueError(f"min_activities 必须≥1，当前值: {self.min_activities}")

        if self.max_activities > 50:
            logger.warning(f"max_activities ({self.max_activities}) 过大，可能影响生成质量")

        # 验证描述长度
        if self.min_description_length > self.max_description_length:
            raise ValueError(
                f"min_description_length ({self.min_description_length}) 不能大于 "
                f"max_description_length ({self.max_description_length})"
            )

        if self.min_description_length < 5:
            raise ValueError(
                f"min_description_length 必须≥5，当前值: {self.min_description_length}"
            )

        # 验证多轮生成参数
        if self.max_rounds < 1 or self.max_rounds > 5:
            raise ValueError(f"max_rounds 必须在1-5之间，当前值: {self.max_rounds}")

        if not 0.0 <= self.quality_threshold <= 1.0:
            raise ValueError(
                f"quality_threshold 必须在0.0-1.0之间，当前值: {self.quality_threshold}"
            )

        # 验证模型参数
        if self.max_tokens < 1000:
            logger.warning(f"max_tokens ({self.max_tokens}) 过小，可能导致响应截断")

        if self.generation_timeout < 10:
            raise ValueError(
                f"generation_timeout 必须≥10秒，当前值: {self.generation_timeout}"
            )

    @property
    def target_description_length(self) -> int:
        """目标描述长度（中位数）

        Returns:
            目标长度（min和max的中位数）

        Examples:
            >>> config = ScheduleGeneratorConfig({
            ...     'min_description_length': 10,
            ...     'max_description_length': 50
            ... })
            >>> config.target_description_length
            30
        """
        return (self.min_description_length + self.max_description_length) // 2

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于传递给子组件）

        Returns:
            配置字典

        Examples:
            >>> config = ScheduleGeneratorConfig({'min_activities': 10})
            >>> config_dict = config.to_dict()
            >>> config_dict['min_activities']
            10
        """
        return {
            'min_activities': self.min_activities,
            'max_activities': self.max_activities,
            'min_description_length': self.min_description_length,
            'max_description_length': self.max_description_length,
            'use_multi_round': self.use_multi_round,
            'max_rounds': self.max_rounds,
            'quality_threshold': self.quality_threshold,
            'max_tokens': self.max_tokens,
            'generation_timeout': self.generation_timeout,
            'custom_prompt': self.custom_prompt,
            'cache_ttl': self.cache_ttl,
            'cache_max_size': self.cache_max_size,
            'custom_model': self.custom_model,
        }

    def __repr__(self) -> str:
        """字符串表示

        Returns:
            配置摘要

        Examples:
            >>> config = ScheduleGeneratorConfig()
            >>> 'ScheduleGeneratorConfig' in repr(config)
            True
        """
        return (
            f"ScheduleGeneratorConfig("
            f"activities={self.min_activities}-{self.max_activities}, "
            f"desc_len={self.min_description_length}-{self.max_description_length}, "
            f"multi_round={self.use_multi_round})"
        )
