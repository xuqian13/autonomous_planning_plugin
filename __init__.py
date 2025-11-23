"""Autonomous Planning Plugin for MaiBot.

This plugin provides autonomous planning and goal management capabilities,
enabling MaiBot to create, manage, and execute scheduled tasks and goals.

Features:
    - Goal management (create, update, delete, pause, resume)
    - Daily/weekly/monthly schedule generation with LLM
    - Automatic schedule injection into conversations
    - Auto-cleanup of old goals
    - Schedule visualization with images

Examples:
    >>> from autonomous_planning_plugin import AutonomousPlanningPlugin
    >>> plugin = AutonomousPlanningPlugin()
"""

from .plugin import AutonomousPlanningPlugin

__all__ = ["AutonomousPlanningPlugin"]
__version__ = "1.0.0"
