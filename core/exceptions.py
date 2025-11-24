"""自定义异常类 - 自主规划插件

本模块定义插件特定的异常类，用于更精确的错误处理和恢复。

异常分类：
    - 可恢复异常：网络超时、临时故障等（应重试）
    - 不可恢复异常：配额超限、权限错误等（不应重试）

使用示例：
    >>> try:
    ...     await llm_api.generate(...)
    ... except LLMQuotaExceededError:
    ...     logger.error("配额超限，停止重试")
    ...     return None
    ... except LLMTimeoutError:
    ...     logger.warning("超时，将重试")
    ...     # 继续重试逻辑
"""


class AutonomousPlanningError(Exception):
    """自主规划插件基础异常类

    所有插件特定异常的父类。
    """
    pass


# ============================================================================
# LLM相关异常（用于日程生成）
# ============================================================================

class LLMError(AutonomousPlanningError):
    """LLM调用异常的基类"""
    pass


class LLMQuotaExceededError(LLMError):
    """LLM配额超限异常

    当达到API调用配额限制时抛出。
    这是不可恢复错误，不应该重试。

    属性：
        message: 错误消息
        quota_type: 配额类型（如 'daily', 'monthly', 'tokens'）
    """

    def __init__(self, message: str, quota_type: str = "unknown"):
        super().__init__(message)
        self.quota_type = quota_type


class LLMTimeoutError(LLMError):
    """LLM调用超时异常

    当API调用超时时抛出。
    这是可恢复错误，可以重试。

    属性：
        message: 错误消息
        timeout_seconds: 超时时长（秒）
    """

    def __init__(self, message: str, timeout_seconds: float = None):
        super().__init__(message)
        self.timeout_seconds = timeout_seconds


class LLMInvalidResponseError(LLMError):
    """LLM响应格式无效异常

    当LLM返回的JSON格式无法解析或不符合预期schema时抛出。
    这可能是可恢复错误（可以用不同的prompt重试）。

    属性：
        message: 错误消息
        response: 原始响应内容
    """

    def __init__(self, message: str, response: str = None):
        super().__init__(message)
        self.response = response


class LLMRateLimitError(LLMError):
    """LLM速率限制异常

    当触发API速率限制时抛出。
    这是可恢复错误，应该等待后重试。

    属性：
        message: 错误消息
        retry_after_seconds: 建议等待时间（秒）
    """

    def __init__(self, message: str, retry_after_seconds: float = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


# ============================================================================
# 数据库相关异常
# ============================================================================

class DatabaseError(AutonomousPlanningError):
    """数据库操作异常的基类"""
    pass


class GoalNotFoundError(DatabaseError):
    """目标不存在异常

    当尝试访问不存在的目标ID时抛出。
    """

    def __init__(self, goal_id: str):
        super().__init__(f"Goal not found: {goal_id}")
        self.goal_id = goal_id


class GoalAlreadyExistsError(DatabaseError):
    """目标已存在异常

    当尝试创建已存在的goal_id时抛出。
    """

    def __init__(self, goal_id: str):
        super().__init__(f"Goal already exists: {goal_id}")
        self.goal_id = goal_id


# ============================================================================
# 验证相关异常
# ============================================================================

class ValidationError(AutonomousPlanningError):
    """输入验证异常的基类"""
    pass


class InvalidParametersError(ValidationError):
    """参数验证失败异常

    当goal的parameters字段不符合预期schema时抛出。

    属性：
        message: 错误消息
        field_name: 出错的字段名
        invalid_value: 无效的值
    """

    def __init__(self, message: str, field_name: str = None, invalid_value=None):
        super().__init__(message)
        self.field_name = field_name
        self.invalid_value = invalid_value


class InvalidTimeWindowError(ValidationError):
    """时间窗口无效异常

    当time_window格式不正确或范围无效时抛出。

    属性：
        message: 错误消息
        time_window: 无效的时间窗口值
    """

    def __init__(self, message: str, time_window=None):
        super().__init__(message)
        self.time_window = time_window


# ============================================================================
# 权限相关异常
# ============================================================================

class PermissionError(AutonomousPlanningError):
    """权限异常的基类"""
    pass


class UnauthorizedAccessError(PermissionError):
    """未授权访问异常

    当用户尝试访问无权访问的资源时抛出。

    属性：
        user_id: 用户ID
        resource_type: 资源类型（如 'goal', 'schedule'）
        resource_id: 资源ID
    """

    def __init__(self, user_id: str, resource_type: str, resource_id: str):
        super().__init__(
            f"User {user_id} not authorized to access {resource_type}:{resource_id}"
        )
        self.user_id = user_id
        self.resource_type = resource_type
        self.resource_id = resource_id


# ============================================================================
# 调度相关异常
# ============================================================================

class ScheduleError(AutonomousPlanningError):
    """日程生成异常的基类"""
    pass


class ScheduleGenerationError(ScheduleError):
    """日程生成失败异常

    当日程生成过程失败时抛出（在重试后仍然失败）。

    属性：
        message: 错误消息
        attempt_count: 尝试次数
    """

    def __init__(self, message: str, attempt_count: int = 0):
        super().__init__(message)
        self.attempt_count = attempt_count


class ScheduleConflictError(ScheduleError):
    """日程冲突异常

    当检测到时间冲突或资源冲突时抛出。

    属性：
        message: 错误消息
        conflicting_items: 冲突的项目列表
    """

    def __init__(self, message: str, conflicting_items: list = None):
        super().__init__(message)
        self.conflicting_items = conflicting_items or []
