"""Schedule Quality Scorer Module.

This module provides quality scoring for generated schedules,
evaluating factors like activity count, description length, and time coverage.

Classes:
    ScheduleQualityScorer: Calculates quality scores for schedules

Example:
    >>> scorer = ScheduleQualityScorer(config)
    >>> score = scorer.calculate_score(items, warnings)
    >>> print(f"Quality: {score:.2f}")
"""

from typing import Any, Dict, List

from src.common.logger import get_logger

from ...utils.time_utils import time_slot_to_minutes

logger = get_logger("autonomous_planning.quality_scorer")


class ScheduleQualityScorer:
    """日程质量评分器

    职责：
    - 评估生成日程的质量（0.0-1.0）
    - 考虑多个维度：活动数量、描述长度、时间覆盖、警告数量
    - 支持可配置的评分权重

    评分维度：
    1. 基础分（base）
    2. 活动数量合理性（activity_count）
    3. 描述长度充分性（description_length）
    4. 时间覆盖全天性（time_coverage）
    5. 警告惩罚（warnings）

    Args:
        config: 配置字典，包含评分相关参数
            - min_activities: 最少活动数
            - max_activities: 最多活动数
            - min_description_length: 最短描述
            - max_description_length: 最长描述

    Examples:
        >>> config = {
        ...     'min_activities': 8,
        ...     'max_activities': 15,
        ...     'min_description_length': 15,
        ...     'max_description_length': 50
        ... }
        >>> scorer = ScheduleQualityScorer(config)
        >>> items = [{"name": "早餐", "description": "吃早餐吃早餐吃早餐", "time_slot": "08:00"}]
        >>> score = scorer.calculate_score(items, [])
        >>> score > 0.0
        True
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化评分器

        Args:
            config: 配置字典
        """
        self.config = config

        # 评分权重（可通过配置覆盖）
        self.weights = {
            'base': 0.5,
            'activity_count': 0.2,
            'description_length': 0.15,
            'time_coverage': 0.15,
            'warnings': 0.3  # 警告惩罚上限
        }

    def calculate_score(
        self,
        items: List[Dict[str, Any]],
        warnings: List[str]
    ) -> float:
        """计算日程质量分数

        评分公式：
        ```
        score = base
              + (活动数量合理 ? activity_count_weight : 0)
              + (描述长度充分 ? description_length_weight : 0)
              + (时间覆盖率 * time_coverage_weight)
              - min(警告数 * 0.05, warnings_weight)
        ```

        Args:
            items: 日程项列表
            warnings: 警告列表

        Returns:
            质量分数（0.0-1.0之间）

        Examples:
            >>> scorer = ScheduleQualityScorer({
            ...     'min_activities': 5,
            ...     'max_activities': 10,
            ...     'min_description_length': 10,
            ...     'max_description_length': 50
            ... })
            >>> items = [
            ...     {"description": "这是一个足够长的描述文本", "time_slot": "08:00"},
            ...     {"description": "另一个活动的描述", "time_slot": "12:00"}
            ... ]
            >>> score = scorer.calculate_score(items, [])
            >>> 0.0 <= score <= 1.0
            True
        """
        if not items:
            logger.warning("日程项为空，质量分数为0")
            return 0.0

        # 读取配置参数
        min_activities = self.config.get('min_activities', 8)
        max_activities = self.config.get('max_activities', 15)
        min_desc_len = self.config.get('min_description_length', 15)
        max_desc_len = self.config.get('max_description_length', 50)
        target_desc_len = (min_desc_len + max_desc_len) // 2

        # 初始化分数
        score = self.weights['base']

        # 1. 活动数量评分
        activity_count_score = self._score_activity_count(
            len(items),
            min_activities,
            max_activities
        )
        score += activity_count_score * self.weights['activity_count']

        # 2. 描述长度评分
        description_score = self._score_description_length(
            items,
            min_desc_len,
            target_desc_len
        )
        score += description_score * self.weights['description_length']

        # 3. 时间覆盖率评分
        coverage_score = self._score_time_coverage(items)
        score += coverage_score * self.weights['time_coverage']

        # 4. 警告惩罚
        warning_penalty = min(len(warnings) * 0.05, self.weights['warnings'])
        score -= warning_penalty

        # 确保分数在[0, 1]范围内
        final_score = max(0.0, min(1.0, score))

        logger.debug(
            f"质量评分: {final_score:.2f} "
            f"(活动数:{activity_count_score:.2f}, "
            f"描述:{description_score:.2f}, "
            f"覆盖:{coverage_score:.2f}, "
            f"警告:-{warning_penalty:.2f})"
        )

        return final_score

    def _score_activity_count(
        self,
        count: int,
        min_count: int,
        max_count: int
    ) -> float:
        """评估活动数量

        Args:
            count: 实际活动数
            min_count: 最少要求
            max_count: 最多要求

        Returns:
            分数（0.0-1.0）
        """
        if min_count <= count <= max_count:
            return 1.0  # 完美
        elif count >= min_count - 2:
            return 0.5  # 接近要求
        else:
            return 0.0  # 不达标

    def _score_description_length(
        self,
        items: List[Dict[str, Any]],
        min_length: int,
        target_length: int
    ) -> float:
        """评估描述长度

        Args:
            items: 日程项列表
            min_length: 最短要求
            target_length: 目标长度

        Returns:
            分数（0.0-1.0）
        """
        if not items:
            return 0.0

        # 计算平均描述长度
        total_length = sum(len(item.get('description', '')) for item in items)
        avg_length = total_length / len(items)

        if avg_length >= target_length:
            return 1.0  # 达到目标
        elif avg_length >= min_length:
            return 0.5  # 达到最低要求
        else:
            return 0.0  # 不达标

    def _score_time_coverage(self, items: List[Dict[str, Any]]) -> float:
        """评估时间覆盖率

        期望覆盖全天的主要时段（7:00-23:00，共16小时）

        Args:
            items: 日程项列表

        Returns:
            覆盖率（0.0-1.0）

        Examples:
            >>> scorer = ScheduleQualityScorer({})
            >>> items = [
            ...     {"time_slot": "08:00"},
            ...     {"time_slot": "12:00"},
            ...     {"time_slot": "18:00"}
            ... ]
            >>> coverage = scorer._score_time_coverage(items)
            >>> 0.0 <= coverage <= 1.0
            True
        """
        if not items:
            return 0.0

        # 统计覆盖的小时数
        covered_hours = set()

        for item in items:
            time_slot = item.get('time_slot')
            if not time_slot:
                continue

            # 解析时间
            minutes = time_slot_to_minutes(time_slot)
            if minutes is None:
                continue

            hour = minutes // 60
            covered_hours.add(hour)

        # 期望覆盖16小时（7:00-23:00）
        expected_hours = 16
        coverage_ratio = len(covered_hours) / expected_hours

        return min(1.0, coverage_ratio)

    def calculate_priority_score(self, item: Dict[str, Any]) -> float:
        """计算单个活动的优先级分数（用于冲突解决）

        评分标准：
        - priority=high: +3
        - priority=medium: +2
        - priority=low: +1
        - 描述长度 > 50字: +1
        - 描述长度 > 80字: +2

        Args:
            item: 活动字典

        Returns:
            优先级分数（越高越优先）

        Examples:
            >>> scorer = ScheduleQualityScorer({})
            >>> item = {"priority": "high", "description": "这是一个很长的描述" * 10}
            >>> score = scorer.calculate_priority_score(item)
            >>> score >= 3.0
            True
        """
        score = 0.0

        # 优先级分数
        priority = item.get("priority", "medium")
        if priority == "high":
            score += 3
        elif priority == "medium":
            score += 2
        else:  # low
            score += 1

        # 描述详细度分数
        desc_len = len(item.get("description", ""))
        if desc_len > 80:
            score += 2
        elif desc_len > 50:
            score += 1

        return score
