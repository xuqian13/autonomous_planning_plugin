"""Core module - fundamental components

This module contains core constants, models, and exceptions.
    - constants: magic numbers and configuration
    - models: data models
    - exceptions: custom exceptions
"""

from .constants import (
    # Time constants
    MIN_ACTIVITY_DURATION_MINUTES,
    MAX_ACTIVITY_DURATION_MINUTES,
    MIN_ACTIVITY_DURATION_HOURS,
    MAX_ACTIVITY_DURATION_HOURS,
    MINUTES_PER_DAY,
    # Goal type
    GoalType,
    VALID_GOAL_TYPES,
    # Priority
    Priority,
    VALID_PRIORITIES,
    # Status
    GoalStatus,
    # Config defaults
    DEFAULT_MIN_ACTIVITIES,
    DEFAULT_MAX_ACTIVITIES,
    DEFAULT_MIN_DESCRIPTION_LENGTH,
    DEFAULT_MAX_DESCRIPTION_LENGTH,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_GENERATION_TIMEOUT,
    DEFAULT_USE_MULTI_ROUND,
    DEFAULT_MAX_ROUNDS,
    DEFAULT_QUALITY_THRESHOLD,
    # Time ranges
    MEAL_TIME_RANGES,
    DAILY_ROUTINE_TIME_RANGES,
    STUDY_TIME_RANGES,
    EXERCISE_TIME_RANGES,
    SOCIAL_TIME_RANGES,
    # Schedule type
    ScheduleType as ScheduleTypeEnum,
    # Format
    WEEKDAY_NAMES,
    TIME_FORMAT_HHMM,
    DATE_FORMAT,
)
from .models import Schedule, ScheduleItem, ScheduleType
from .exceptions import *  # Export all exceptions

__all__ = [
    # Constants
    "MIN_ACTIVITY_DURATION_MINUTES",
    "MAX_ACTIVITY_DURATION_MINUTES",
    "MIN_ACTIVITY_DURATION_HOURS",
    "MAX_ACTIVITY_DURATION_HOURS",
    "MINUTES_PER_DAY",
    "GoalType",
    "VALID_GOAL_TYPES",
    "Priority",
    "VALID_PRIORITIES",
    "GoalStatus",
    "DEFAULT_MIN_ACTIVITIES",
    "DEFAULT_MAX_ACTIVITIES",
    "DEFAULT_MIN_DESCRIPTION_LENGTH",
    "DEFAULT_MAX_DESCRIPTION_LENGTH",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_GENERATION_TIMEOUT",
    "DEFAULT_USE_MULTI_ROUND",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_QUALITY_THRESHOLD",
    "MEAL_TIME_RANGES",
    "DAILY_ROUTINE_TIME_RANGES",
    "STUDY_TIME_RANGES",
    "EXERCISE_TIME_RANGES",
    "SOCIAL_TIME_RANGES",
    "ScheduleTypeEnum",
    "WEEKDAY_NAMES",
    "TIME_FORMAT_HHMM",
    "DATE_FORMAT",
    # Models
    "Schedule",
    "ScheduleItem",
    "ScheduleType",
]
