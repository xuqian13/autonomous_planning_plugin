"""麦麦自主规划插件 - 主文件"""

import asyncio
from typing import List, Tuple

from src.plugin_system import BasePlugin, register_plugin, ConfigField
from src.common.logger import get_logger

from .tools import ManageGoalTool, GetPlanningStatusTool, GenerateScheduleTool, ApplyScheduleTool
from .handlers import AutonomousPlannerEventHandler, ScheduleInjectEventHandler
from .commands import PlanningCommand
from .planner.auto_scheduler import ScheduleAutoScheduler

logger = get_logger("autonomous_planning")

@register_plugin
class AutonomousPlanningPlugin(BasePlugin):
    """麦麦自主规划插件"""

    plugin_name: str = "autonomous_planning_plugin"
    enable_plugin: bool = True
    dependencies: List[str] = []  # perception_plugin 是可选依赖
    python_dependencies: List[str] = []
    config_file_name: str = "config.toml"

    config_section_descriptions = {
        "plugin": "插件基本配置",
        "autonomous_planning": "自主规划配置"
    }

    config_schema: dict = {
        "plugin": {
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用插件"
            ),
        },
        "autonomous_planning": {
            "cleanup_interval": ConfigField(
                type=int,
                default=3600,
                description="清理间隔（秒）"
            ),
            "cleanup_old_goals_days": ConfigField(
                type=int,
                default=30,
                description="保留历史记录天数"
            ),
            "schedule": {
                # 日程注入功能
                "inject_schedule": ConfigField(
                    type=bool,
                    default=True,
                    description="在对话时自然提到当前活动"
                ),
                "auto_generate": ConfigField(
                    type=bool,
                    default=True,
                    description="询问日程时自动检查并生成"
                ),
                # 多轮生成配置
                "use_multi_round": ConfigField(
                    type=bool,
                    default=True,
                    description="启用多轮生成机制"
                ),
                "max_rounds": ConfigField(
                    type=int,
                    default=2,
                    description="最多尝试轮数（1-3）"
                ),
                "quality_threshold": ConfigField(
                    type=float,
                    default=0.85,
                    description="质量阈值（0.80-0.90）"
                ),
                # 生成参数
                "min_activities": ConfigField(
                    type=int,
                    default=8,
                    description="最少活动数量"
                ),
                "max_activities": ConfigField(
                    type=int,
                    default=15,
                    description="最多活动数量"
                ),
                "min_description_length": ConfigField(
                    type=int,
                    default=15,
                    description="描述最小长度"
                ),
                "max_description_length": ConfigField(
                    type=int,
                    default=50,
                    description="描述最大长度"
                ),
                "max_tokens": ConfigField(
                    type=int,
                    default=8192,
                    description="日程生成的最大token数"
                ),
                "generation_timeout": ConfigField(
                    type=float,
                    default=180.0,
                    description="日程生成超时时间（秒）"
                ),
                # 缓存配置
                "cache_ttl": ConfigField(
                    type=int,
                    default=300,
                    description="缓存TTL（秒）"
                ),
                "cache_max_size": ConfigField(
                    type=int,
                    default=100,
                    description="缓存最大条目数"
                ),
                # 定时自动生成配置
                "auto_schedule_enabled": ConfigField(
                    type=bool,
                    default=True,
                    description="是否启用定时自动生成日程"
                ),
                "auto_schedule_time": ConfigField(
                    type=str,
                    default="00:30",
                    description="每天自动生成日程的时间（HH:MM格式）"
                ),
                "timezone": ConfigField(
                    type=str,
                    default="Asia/Shanghai",
                    description="时区设置"
                ),
                # 权限配置
                "admin_users": ConfigField(
                    type=list,
                    default=[],
                    description="管理员QQ号列表，格式: [\"12345\", \"67890\"]，留空则所有人可用"
                ),
                "custom_model": {
                    "enabled": ConfigField(
                        type=bool,
                        default=False,
                        description="是否启用自定义模型"
                    ),
                    "model_name": ConfigField(
                        type=str,
                        default="",
                        description="模型名称"
                    ),
                    "api_base": ConfigField(
                        type=str,
                        default="",
                        description="API地址"
                    ),
                    "api_key": ConfigField(
                        type=str,
                        default="",
                        description="API密钥"
                    ),
                    "provider": ConfigField(
                        type=str,
                        default="",
                        description="提供商类型"
                    ),
                    "temperature": ConfigField(
                        type=float,
                        default=0.7,
                        description="温度参数（0.0-1.0）"
                    ),
                },
            },
        },
    }

    def __init__(self, *args, **kwargs):
        """初始化插件"""
        super().__init__(*args, **kwargs)
        self.scheduler = None
        logger.info("自主规划插件初始化完成")
        # 延迟启动调度器，确保插件系统完全初始化
        asyncio.create_task(self._start_scheduler_after_delay())

    async def _start_scheduler_after_delay(self):
        """延迟启动调度器（10秒后）"""
        await asyncio.sleep(10)
        self.scheduler = ScheduleAutoScheduler(self)
        await self.scheduler.start()

    def get_plugin_components(self) -> List[Tuple]:
        """获取插件组件"""
        return [
            # Tools - 供 LLM 直接调用的工具
            (ManageGoalTool.get_tool_info(), ManageGoalTool),
            (GetPlanningStatusTool.get_tool_info(), GetPlanningStatusTool),
            (GenerateScheduleTool.get_tool_info(), GenerateScheduleTool),
            (ApplyScheduleTool.get_tool_info(), ApplyScheduleTool),
            # Event Handlers - 事件处理器
            (AutonomousPlannerEventHandler.get_handler_info(), AutonomousPlannerEventHandler),
            (ScheduleInjectEventHandler.get_handler_info(), ScheduleInjectEventHandler),
            # Commands - 命令处理
            (PlanningCommand.get_command_info(), PlanningCommand),
        ]
