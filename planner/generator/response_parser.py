"""LLM Response Parser Module.

This module provides utilities for parsing and cleaning LLM responses,
specifically for schedule generation JSON responses.

Classes:
    LLMResponseParser: Parser for LLM JSON responses with markdown cleanup

Example:
    >>> parser = LLMResponseParser()
    >>> data = parser.parse_json_response(llm_response)
    >>> items = parser.extract_schedule_items(data)
"""

import json
import re
from typing import Any, Dict, List, Optional

from src.common.logger import get_logger

from ...core.exceptions import LLMInvalidResponseError

logger = get_logger("autonomous_planning.response_parser")


class LLMResponseParser:
    """LLM响应解析器 - 专门处理日程生成的JSON响应

    职责：
    - 清理LLM响应中的markdown标记
    - 清理控制字符（修复JSON解析错误）
    - 解析JSON字符串为字典
    - 提取schedule_items字段
    - 统一错误处理

    优势：
    - DRY原则：消除重复的JSON清理代码
    - 单一职责：只负责响应解析
    - 易于测试：独立的工具类
    - 可扩展：未来可添加更多清理规则
    - 容错性强：自动处理控制字符问题
    """

    @staticmethod
    def clean_control_characters(text: str) -> str:
        """清理JSON字符串值中的非法控制字符

        JSON标准要求字符串值内的控制字符必须被转义，但LLM生成的响应可能包含：
        - 字符串值内未转义的换行符 \n
        - 字符串值内未转义的制表符 \t
        - 字符串值内未转义的回车符 \r
        - 其他控制字符（ASCII 0-31）

        注意：此方法只处理JSON字符串值内部的控制字符，
        不会影响JSON结构本身的格式化（如对象/数组的换行）。

        策略：使用正则表达式匹配双引号内的字符串，只在其中替换控制字符

        Args:
            text: 原始JSON字符串

        Returns:
            清理后的JSON字符串

        Examples:
            >>> text = '{"desc": "第一行\\n第二行"}'
            >>> LLMResponseParser.clean_control_characters(text)
            '{"desc": "第一行\\\\n第二行"}'
        """
        if not text:
            return text

        # 定义控制字符的转义映射
        control_char_map = {
            '\n': '\\n',
            '\r': '\\r',
            '\t': '\\t',
            '\b': '\\b',
            '\f': '\\f',
        }

        def replace_in_string(match):
            """替换匹配的字符串内部的控制字符"""
            string_content = match.group(0)

            # 跳过已经正确转义的内容
            if '\\' in string_content:
                # 如果字符串已经包含转义符，保守地不处理
                # 避免重复转义（如 \\n 变成 \\\\n）
                return string_content

            # 替换控制字符
            for char, escaped in control_char_map.items():
                string_content = string_content.replace(char, escaped)

            return string_content

        # 正则表达式：匹配双引号内的字符串内容（非贪婪模式）
        # 匹配 "..." 但不匹配已转义的引号 \"
        # 注意：这个正则不能处理所有边缘情况，但对大多数LLM响应足够了
        pattern = r'"[^"]*"'

        try:
            result = re.sub(pattern, replace_in_string, text)
            return result
        except Exception as e:
            # 如果正则替换失败，返回原文本
            logger.warning(f"控制字符清理失败，使用原文本: {e}")
            return text

    @staticmethod
    def clean_markdown_json(response: str) -> str:
        """清理LLM响应中的Markdown代码块标记

        支持的格式：
        - ```json\n{...}\n```
        - ```\n{...}\n```
        - {直接的JSON...}

        Args:
            response: 原始LLM响应字符串

        Returns:
            清理后的纯JSON字符串

        Examples:
            >>> LLMResponseParser.clean_markdown_json("```json\\n{\\\"key\\\": \\\"value\\\"}\\n```")
            '{"key": "value"}'

            >>> LLMResponseParser.clean_markdown_json("```\\n{\\\"key\\\": \\\"value\\\"}\\n```")
            '{"key": "value"}'
        """
        if not response:
            return response

        response = response.strip()

        # 移除开头的markdown标记
        if response.startswith("```json"):
            response = response[7:].lstrip()
        elif response.startswith("```"):
            response = response[3:].lstrip()

        # 移除结尾的markdown标记
        if response.endswith("```"):
            response = response[:-3].rstrip()

        return response.strip()

    @staticmethod
    def parse_json_response(response: str) -> Dict[str, Any]:
        """解析LLM返回的JSON响应（自动清理markdown和控制字符）

        处理流程：
        1. 清理markdown代码块标记
        2. 清理非法控制字符（修复"Invalid control character"错误）
        3. 解析JSON字符串

        Args:
            response: LLM原始响应字符串

        Returns:
            解析后的字典对象

        Raises:
            LLMInvalidResponseError: JSON解析失败时抛出

        Examples:
            >>> parser = LLMResponseParser()
            >>> data = parser.parse_json_response('```json\\n{"key": "value"}\\n```')
            >>> print(data)
            {'key': 'value'}
        """
        try:
            # 1. 清理markdown标记
            cleaned = LLMResponseParser.clean_markdown_json(response)

            # 2. 清理控制字符（修复JSON解析错误）
            cleaned = LLMResponseParser.clean_control_characters(cleaned)

            # 3. 解析JSON
            return json.loads(cleaned)

        except json.JSONDecodeError as e:
            # 记录详细错误信息以便调试
            error_msg = f"JSON解析失败: {e}"
            logger.error(error_msg)
            logger.debug(f"原始响应（前500字符）: {response[:500]}")
            logger.debug(f"清理后响应（前500字符）: {cleaned[:500] if 'cleaned' in locals() else 'N/A'}")

            raise LLMInvalidResponseError(
                f"无法解析LLM响应为JSON: {e}",
                response=response[:500]  # 只保存前500字符避免日志过大
            )
        except Exception as e:
            error_msg = f"响应解析异常: {type(e).__name__} - {e}"
            logger.error(error_msg, exc_info=True)

            raise LLMInvalidResponseError(
                f"响应解析失败: {e}",
                response=response[:500]
            )

    @staticmethod
    def extract_schedule_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从解析后的数据中提取schedule_items字段

        Args:
            data: 解析后的JSON字典

        Returns:
            schedule_items列表

        Raises:
            LLMInvalidResponseError: 缺少schedule_items字段时抛出

        Examples:
            >>> parser = LLMResponseParser()
            >>> data = {"schedule_items": [{"name": "早餐"}]}
            >>> items = parser.extract_schedule_items(data)
            >>> len(items)
            1
        """
        if "schedule_items" not in data:
            error_msg = "LLM响应缺少必需的 'schedule_items' 字段"
            logger.error(f"{error_msg}，实际字段: {list(data.keys())}")

            raise LLMInvalidResponseError(
                error_msg,
                response=json.dumps(data, ensure_ascii=False)[:500]
            )

        items = data["schedule_items"]

        # 验证是否为列表
        if not isinstance(items, list):
            error_msg = f"schedule_items必须是列表，实际类型: {type(items).__name__}"
            logger.error(error_msg)

            raise LLMInvalidResponseError(error_msg)

        logger.debug(f"成功提取 {len(items)} 个日程项")
        return items

    @staticmethod
    def parse_schedule_response(response: str) -> List[Dict[str, Any]]:
        """一站式解析：从原始响应到schedule_items

        这是最常用的方法，组合了清理、解析、提取三个步骤。

        Args:
            response: LLM原始响应字符串

        Returns:
            schedule_items列表

        Raises:
            LLMInvalidResponseError: 任何解析错误

        Examples:
            >>> parser = LLMResponseParser()
            >>> response = '''```json
            ... {
            ...   "schedule_items": [
            ...     {"name": "早餐", "time_slot": "08:00"}
            ...   ]
            ... }
            ... ```'''
            >>> items = parser.parse_schedule_response(response)
            >>> len(items)
            1
        """
        # Step 1: 解析JSON
        data = LLMResponseParser.parse_json_response(response)

        # Step 2: 提取schedule_items
        items = LLMResponseParser.extract_schedule_items(data)

        return items
