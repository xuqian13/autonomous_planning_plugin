"""Conflict Resolver Module.

This module handles validation and conflict resolution for schedule items.
"""

from typing import Any, Dict, List

from src.common.logger import get_logger

from ...utils.time_utils import format_minutes_to_time, time_slot_to_minutes

logger = get_logger("autonomous_planning.conflict_resolver")


class ConflictResolver:
    """冲突解决器 - 处理时间重叠和验证"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化冲突解决器

        Args:
            config: 配置字典
        """
        self.config = config

    def validate_schedule_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

            # 验证duration_hours（如果提供）
            if "duration_hours" in item and item["duration_hours"]:
                try:
                    duration = float(item["duration_hours"])
                    if duration <= 0 or duration > 12:
                        item["duration_hours"] = 1.0  # 默认1小时
                except (ValueError, TypeError):
                    item["duration_hours"] = 1.0  # 默认1小时

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
        deduped_items = self.remove_time_conflicts(valid_items)

        return deduped_items

    def remove_time_conflicts(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理时间重叠的日程项（智能调整版：优先调整时间，必要时删除）

        策略：
        1. 按 time_slot 排序
        2. 计算每个活动的结束时间（使用duration_hours）
        3. 检测时间重叠：如果活动A的结束时间 > 活动B的开始时间，则重叠
        4. 冲突处理：
           - 如果重叠时间 < 活动时长的50%：缩短低优先级活动的持续时间
           - 如果重叠时间 >= 活动时长的50%：删除低优先级活动
        5. 优先级判断：priority高的 > 描述详细的 > 先出现的

        Args:
            items: 已验证的日程项列表

        Returns:
            无时间冲突的日程项列表（部分活动可能已调整持续时间）
        """
        if not items:
            return items

        # 解析时间并计算结束时间
        items_with_time = []
        for item in items:
            time_slot = item.get("time_slot")
            if not time_slot:
                # 没有时间的项放在最后
                items_with_time.append({
                    'start': 9999,
                    'end': 9999,
                    'item': item
                })
                continue

            # P1优化：使用统一的工具函数解析时间
            start_minutes = time_slot_to_minutes(time_slot)
            if start_minutes is None:
                logger.warning(f"解析时间失败: {time_slot}，将忽略该项")
                continue

            # 使用 duration_hours 计算结束时间
            duration_hours = item.get("duration_hours", 1.0)
            duration_minutes = int(duration_hours * 60)
            end_minutes = start_minutes + duration_minutes

            # 避免超过24小时
            if end_minutes > 24 * 60:
                end_minutes = 24 * 60

            items_with_time.append({
                'start': start_minutes,
                'end': end_minutes,
                'item': item
            })

        # 按开始时间排序
        items_with_time.sort(key=lambda x: x['start'])

        # 去重和冲突检测
        deduped_items = []
        duplicates_removed = 0
        overlaps_removed = 0
        overlaps_adjusted = 0

        for i, current in enumerate(items_with_time):
            # 检查是否与已保留的活动重叠
            has_conflict = False

            for kept in deduped_items:
                # 检测时间重叠：
                # 重叠条件：kept的结束时间 > current的开始时间 AND kept的开始时间 < current的结束时间
                if kept['end'] > current['start'] and kept['start'] < current['end']:
                    # 发现重叠
                    overlap_minutes = min(kept['end'], current['end']) - max(kept['start'], current['start'])

                    # 决定保留哪个
                    # 优先级：1. priority高的 2. 描述长的 3. 先出现的
                    current_priority_score = self.calculate_priority_score(current['item'])
                    kept_priority_score = self.calculate_priority_score(kept['item'])

                    # 计算活动原本的持续时间
                    current_duration = current['end'] - current['start']
                    kept_duration = kept['end'] - kept['start']

                    # 判断是否可以通过调整持续时间解决冲突
                    # 策略：如果重叠时间小于活动时长的50%，尝试缩短持续时间
                    can_adjust_current = overlap_minutes < current_duration * 0.5
                    can_adjust_kept = overlap_minutes < kept_duration * 0.5

                    if current_priority_score > kept_priority_score:
                        # 当前活动优先级更高
                        if can_adjust_kept:
                            # 缩短已保留活动的持续时间
                            old_end = kept['end']
                            kept['end'] = current['start']  # 调整结束时间到当前活动开始时间
                            new_duration = kept['end'] - kept['start']

                            # 更新活动的duration_hours
                            kept['item']['duration_hours'] = round(new_duration / 60, 2)

                            logger.info(
                                f"⏰ 调整时间：{kept['item']['name']} "
                                f"从 {self._format_time(kept['start'])}-{self._format_time(old_end)} "
                                f"调整为 {self._format_time(kept['start'])}-{self._format_time(kept['end'])} "
                                f"（缩短 {overlap_minutes} 分钟，避免与 {current['item']['name']} 冲突）"
                            )
                            overlaps_adjusted += 1
                        else:
                            # 重叠太多，移除已保留的
                            logger.warning(
                                f"时间重叠：{current['item']['name']} "
                                f"({self._format_time(current['start'])}-{self._format_time(current['end'])}) "
                                f"与 {kept['item']['name']} "
                                f"({self._format_time(kept['start'])}-{self._format_time(kept['end'])}) "
                                f"重叠 {overlap_minutes} 分钟（超过50%），移除 {kept['item']['name']}"
                            )
                            deduped_items.remove(kept)
                            overlaps_removed += 1
                    else:
                        # 已保留的活动优先级更高或相等
                        if can_adjust_current:
                            # 缩短当前活动的持续时间
                            old_end = current['end']
                            current['end'] = kept['start']  # 调整结束时间到已保留活动开始时间
                            new_duration = current['end'] - current['start']

                            # 如果调整后时间无效（结束时间小于等于开始时间），则跳过该活动
                            if new_duration <= 0:
                                logger.warning(
                                    f"时间重叠：{current['item']['name']} "
                                    f"({self._format_time(current['start'])}-{self._format_time(old_end)}) "
                                    f"与 {kept['item']['name']} 完全重叠，跳过 {current['item']['name']}"
                                )
                                has_conflict = True
                                overlaps_removed += 1
                                break

                            # 更新活动的duration_hours
                            current['item']['duration_hours'] = round(new_duration / 60, 2)

                            logger.info(
                                f"⏰ 调整时间：{current['item']['name']} "
                                f"从 {self._format_time(current['start'])}-{self._format_time(old_end)} "
                                f"调整为 {self._format_time(current['start'])}-{self._format_time(current['end'])} "
                                f"（缩短 {overlap_minutes} 分钟，避免与 {kept['item']['name']} 冲突）"
                            )
                            overlaps_adjusted += 1
                        else:
                            # 重叠太多，跳过当前活动
                            logger.warning(
                                f"时间重叠：{current['item']['name']} "
                                f"({self._format_time(current['start'])}-{self._format_time(current['end'])}) "
                                f"与 {kept['item']['name']} "
                                f"({self._format_time(kept['start'])}-{self._format_time(kept['end'])}) "
                                f"重叠 {overlap_minutes} 分钟（超过50%），跳过 {current['item']['name']}"
                            )
                            has_conflict = True
                            overlaps_removed += 1
                            break

            # 如果没有冲突，添加到结果
            if not has_conflict:
                deduped_items.append(current)

        if overlaps_adjusted > 0 or overlaps_removed > 0:
            logger.info(f"⚠️  时间冲突处理：调整了 {overlaps_adjusted} 个活动的持续时间，移除了 {overlaps_removed} 个活动")

        # 提取item对象
        result = [item['item'] for item in deduped_items]
        logger.info(f"✅ 日程验证完成：原始 {len(items)} 项 → 去重后 {len(result)} 项")

        return result

    def calculate_priority_score(self, item: Dict[str, Any]) -> float:
        """
        计算活动的优先级分数，用于冲突解决

        评分标准：
        - priority=high: +3
        - priority=medium: +2
        - priority=low: +1
        - 描述长度 > 50字: +1
        - 描述长度 > 80字: +2

        Returns:
            优先级分数（越高越优先）
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

    def calculate_quality_score(self, items: List[Dict], warnings: List[str]) -> float:
        """
        计算日程质量分数（0-1）

        评分标准：
        - 基础分：0.5
        - 活动数量合理：+0.2
        - 描述长度充分：+0.15
        - 时间覆盖全天：+0.15
        - 警告惩罚：每个警告-0.05（最多-0.3）

        Returns:
            质量分数（0.0-1.0）
        """
        if not items:
            return 0.0

        # 从配置读取参数
        min_activities = self.config.get('min_activities', 8)
        max_activities = self.config.get('max_activities', 15)
        min_desc_len = self.config.get('min_description_length', 15)
        max_desc_len = self.config.get('max_description_length', 50)
        target_desc_len = (min_desc_len + max_desc_len) // 2

        # 基础分
        score = 0.5

        # 奖励：活动数量合理
        if min_activities <= len(items) <= max_activities:
            score += 0.2
        elif len(items) >= min_activities - 2:
            score += 0.1

        # 奖励：描述长度充分
        avg_desc_len = sum(len(item.get('description', '')) for item in items) / len(items)
        if avg_desc_len >= target_desc_len:
            score += 0.15
        elif avg_desc_len >= min_desc_len:
            score += 0.08

        # 惩罚：警告数量
        warning_penalty = min(len(warnings) * 0.05, 0.3)
        score -= warning_penalty

        # 奖励：覆盖全天（0点到23点）
        time_coverage = self._calculate_time_coverage(items)
        score += time_coverage * 0.15

        return max(0.0, min(1.0, score))

    def _calculate_time_coverage(self, items: List[Dict]) -> float:
        """
        计算时间覆盖率（0-1）

        期望覆盖16小时（7:00-23:00）
        """
        covered_hours = set()
        for item in items:
            time_slot = item.get('time_slot', '')
            try:
                hour = int(time_slot.split(':')[0])
                covered_hours.add(hour)
            except (ValueError, IndexError, AttributeError):
                pass

        # 期望覆盖16小时（7:00-23:00）
        return len(covered_hours) / 16

    def _format_time(self, minutes: int) -> str:
        """将分钟数格式化为HH:MM（使用统一工具函数）"""
        return format_minutes_to_time(minutes)
