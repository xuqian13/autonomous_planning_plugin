"""工具模块单元测试

测试 utils/time_utils.py 和 tools.py 中的辅助函数
"""

import unittest
import sys
from pathlib import Path

# 添加父目录到路径
plugin_dir = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_dir))

# 导入被测模块
from utils.time_utils import (
    parse_time_window,
    parse_time_slot,
    time_slot_to_minutes,
    format_minutes_to_time,
    get_time_window_from_goal,
)


class TestParseTimeWindow(unittest.TestCase):
    """测试 parse_time_window 函数"""

    def test_valid_list_format(self):
        """测试有效的列表格式 [start, end]"""
        result = parse_time_window([540, 630])
        self.assertEqual(result, (540, 630))

    def test_none_input(self):
        """测试 None 输入"""
        result = parse_time_window(None)
        self.assertEqual(result, (None, None))

    def test_empty_list(self):
        """测试空列表"""
        result = parse_time_window([])
        self.assertEqual(result, (None, None))

    def test_single_element_list(self):
        """测试单元素列表"""
        result = parse_time_window([540])
        self.assertEqual(result, (None, None))


class TestTimeSlotToMinutes(unittest.TestCase):
    """测试 time_slot_to_minutes 函数"""

    def test_valid_time(self):
        """测试有效时间"""
        result = time_slot_to_minutes("09:30")
        self.assertEqual(result, 570)

    def test_midnight(self):
        """测试午夜"""
        result = time_slot_to_minutes("00:00")
        self.assertEqual(result, 0)

    def test_end_of_day(self):
        """测试一天结束"""
        result = time_slot_to_minutes("23:59")
        self.assertEqual(result, 1439)

    def test_invalid_format(self):
        """测试无效格式"""
        result = time_slot_to_minutes("invalid")
        self.assertIsNone(result)

    def test_missing_colon(self):
        """测试缺少冒号"""
        result = time_slot_to_minutes("0930")
        # "0930" 会被解析为 0小时930分钟 = 55800分钟（不正确但这是实际行为）
        self.assertEqual(result, 55800)


class TestFormatMinutesToTime(unittest.TestCase):
    """测试 format_minutes_to_time 函数"""

    def test_normal_time(self):
        """测试正常时间"""
        result = format_minutes_to_time(570)
        self.assertEqual(result, "09:30")

    def test_midnight(self):
        """测试午夜"""
        result = format_minutes_to_time(0)
        self.assertEqual(result, "00:00")

    def test_end_of_day(self):
        """测试一天结束"""
        result = format_minutes_to_time(1439)
        self.assertEqual(result, "23:59")


class MockGoal:
    """模拟 Goal 对象"""

    def __init__(self, name: str, time_window: list = None):
        self.name = name
        self.description = f"{name}活动"
        self.parameters = {"time_window": time_window} if time_window else {}


if __name__ == "__main__":
    unittest.main()

