"""麦麦自主规划插件 v4

基于 maibot_sdk v2.4.0 重写，从旧版 ``src.plugin_system`` API 迁移而来。
"""

from .plugin import AutonomousPlanningPluginV4, create_plugin

__all__ = ["AutonomousPlanningPluginV4", "create_plugin"]
__version__ = "4.4.6"
