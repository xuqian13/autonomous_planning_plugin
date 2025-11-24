"""常量定义 - 自主规划插件

本模块定义了插件中使用的所有常量，避免魔法数字和硬编码值。

分类：
    - 时间常量
    - 活动类型和优先级
    - 质量评分参数
    - 数据库相关
    - 缓存相关
"""

from typing import Dict, List, Tuple

# ========== 时间常量 ==========

# 活动持续时间限制（分钟）
MIN_ACTIVITY_DURATION_MINUTES = 15
MAX_ACTIVITY_DURATION_MINUTES = 180  # 3小时

# 活动持续时间限制（小时）
MIN_ACTIVITY_DURATION_HOURS = 0.25
MAX_ACTIVITY_DURATION_HOURS = 12.0

# 一天的分钟数
MINUTES_PER_DAY = 24 * 60

# 缓存TTL（秒）
DEFAULT_CACHE_TTL = 300  # 5分钟
CONVERSATION_CACHE_EXPIRE = 1800  # 30分钟

# 清理间隔
DEFAULT_CLEANUP_INTERVAL = 3600  # 1小时
DEFAULT_CLEANUP_OLD_GOALS_DAYS = 30  # 30天

# ========== 活动类型 ==========

class GoalType:
    """目标类型枚举"""
    DAILY_ROUTINE = "daily_routine"      # 日常作息
    MEAL = "meal"                        # 吃饭
    STUDY = "study"                      # 学习
    ENTERTAINMENT = "entertainment"      # 娱乐
    SOCIAL = "social_maintenance"        # 社交
    EXERCISE = "exercise"                # 运动
    LEARN_TOPIC = "learn_topic"          # 兴趣学习
    HEALTH_CHECK = "health_check"        # 系统检查
    REST = "rest"                        # 休息
    FREE_TIME = "free_time"              # 自由时间
    CUSTOM = "custom"                    # 自定义

VALID_GOAL_TYPES: List[str] = [
    GoalType.DAILY_ROUTINE,
    GoalType.MEAL,
    GoalType.STUDY,
    GoalType.ENTERTAINMENT,
    GoalType.SOCIAL,
    GoalType.EXERCISE,
    GoalType.LEARN_TOPIC,
    GoalType.HEALTH_CHECK,
    GoalType.REST,
    GoalType.FREE_TIME,
    GoalType.CUSTOM,
]

# ========== 优先级 ==========

class Priority:
    """优先级枚举"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

VALID_PRIORITIES: List[str] = [
    Priority.HIGH,
    Priority.MEDIUM,
    Priority.LOW,
]

# ========== 目标状态 ==========

class GoalStatus:
    """目标状态枚举"""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

# ========== 日程生成参数 ==========

# 默认活动数量范围
DEFAULT_MIN_ACTIVITIES = 8
DEFAULT_MAX_ACTIVITIES = 15

# 描述长度限制（字符）
DEFAULT_MIN_DESCRIPTION_LENGTH = 15
DEFAULT_MAX_DESCRIPTION_LENGTH = 50

# LLM生成参数
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TEMPERATURE = 0.7
DEFAULT_GENERATION_TIMEOUT = 180.0  # 秒

# 多轮生成参数
DEFAULT_USE_MULTI_ROUND = True
DEFAULT_MAX_ROUNDS = 2
DEFAULT_QUALITY_THRESHOLD = 0.85

# ========== 质量评分参数 ==========

# 基础分
QUALITY_BASE_SCORE = 0.5

# 奖励分数
QUALITY_ACTIVITY_COUNT_BONUS = 0.2      # 活动数量合理
QUALITY_DESCRIPTION_LENGTH_BONUS = 0.15  # 描述长度充分
QUALITY_TIME_COVERAGE_BONUS = 0.15       # 时间覆盖全天

# 惩罚分数
QUALITY_WARNING_PENALTY_PER_ITEM = 0.05  # 每个警告
QUALITY_MAX_WARNING_PENALTY = 0.3        # 最大警告惩罚

# 时间覆盖期望（小时）
EXPECTED_TIME_COVERAGE_HOURS = 16  # 7:00-23:00

# ========== 时间合理性范围 ==========

# 用餐时间（小时）
MEAL_TIME_RANGES: Dict[str, Tuple[int, int]] = {
    "早餐": (6, 9),
    "午餐": (11, 14),
    "晚餐": (17, 20),
    "早饭": (6, 9),
    "午饭": (11, 14),
    "晚饭": (17, 20),
}

# 作息时间（小时或小时列表）
DAILY_ROUTINE_TIME_RANGES: Dict[str, List[Tuple[int, int]] | Tuple[int, int]] = {
    "睡觉": [(22, 24), (0, 6)],  # 跨午夜
    "睡前": [(21, 24), (0, 2)],
    "起床": (6, 10),
    "洗漱": (6, 23),
}

# 学习时间（小时）
STUDY_TIME_RANGES: Dict[str, Tuple[int, int]] = {
    "上课": (8, 18),
    "自习": (8, 23),
    "学习": (8, 23),
}

# 运动时间（小时）
EXERCISE_TIME_RANGES: Dict[str, List[Tuple[int, int]]] = {
    "运动": [(6, 9), (17, 22)],
    "健身": [(6, 9), (17, 22)],
}

# 社交活动时间（小时）
SOCIAL_TIME_RANGES: Dict[str, Tuple[int, int]] = {
    "夜聊": (20, 24),
    "晚安": (21, 24),
}

# ========== 冲突解决参数 ==========

# 重叠阈值（比例）
CONFLICT_OVERLAP_THRESHOLD = 0.5  # 50%

# 优先级分数
PRIORITY_SCORES: Dict[str, float] = {
    Priority.HIGH: 3.0,
    Priority.MEDIUM: 2.0,
    Priority.LOW: 1.0,
}

# 描述详细度分数阈值
DESCRIPTION_SCORE_THRESHOLD_HIGH = 80  # 字符数
DESCRIPTION_SCORE_THRESHOLD_MEDIUM = 50

DESCRIPTION_SCORE_BONUS_HIGH = 2.0
DESCRIPTION_SCORE_BONUS_MEDIUM = 1.0

# ========== 数据库相关 ==========

# 数据库模式版本
DATABASE_SCHEMA_VERSION = 1

# 连接超时（秒）
DATABASE_CONNECTION_TIMEOUT = 30.0

# ========== 缓存相关 ==========

# LRU缓存大小
DEFAULT_CACHE_MAX_SIZE = 100

# 注入阈值
DEFAULT_INJECTION_MESSAGE_THRESHOLD = 5
DEFAULT_INJECTION_TIME_THRESHOLD = 300  # 秒

# ========== 重试相关 ==========

# 默认重试次数
DEFAULT_MAX_RETRIES = 3

# 退避基数（秒）
DEFAULT_BACKOFF_BASE = 2  # 指数退避：1s, 2s, 4s

# ========== 日程类型 ==========

class ScheduleType:
    """日程类型"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

# ========== 文件路径 ==========

# 数据库默认路径
DEFAULT_DB_PATH = "data/goals.db"

# 配置文件名
CONFIG_FILE_NAME = "config.toml"

# ========== 其他常量 ==========

# 星期名称（中文）
WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 时间格式
TIME_FORMAT_HHMMSS = "%H:%M:%S"
TIME_FORMAT_HHMM = "%H:%M"
DATE_FORMAT = "%Y-%m-%d"
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
