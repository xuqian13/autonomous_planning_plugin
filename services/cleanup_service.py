"""目标清理 / 自动调度 后台任务的业务实现。

对应旧版 ``handlers/handlers.py:AutonomousPlannerEventHandler`` 与
``planner/auto_scheduler.py:ScheduleAutoScheduler``。

新版通过 ``on_load`` 中 ``asyncio.create_task`` 启动循环，``on_unload`` 中 cancel。
"""

from typing import TYPE_CHECKING, Optional
import asyncio
import logging

from ..planner.auto_scheduler import ScheduleAutoScheduler
from ..planner.goal_manager import get_goal_manager

if TYPE_CHECKING:
    from ..plugin import AutonomousPlanningPluginV4

logger = logging.getLogger(__name__)


class CleanupService:
    """目标清理与自动调度服务。"""

    def __init__(self, plugin: "AutonomousPlanningPluginV4") -> None:
        """初始化 CleanupService。

        Args:
            plugin: 当前插件实例
        """
        self._plugin = plugin
        self._stop_event: asyncio.Event = asyncio.Event()
        # 自动调度器实例（延迟到 run_scheduler_loop 创建，避免 __init__ 阶段事件循环未就绪）
        self._auto_scheduler: Optional[ScheduleAutoScheduler] = None
        logger.debug("CleanupService 初始化")

    # ------------------------------------------------------------
    # 后台任务 1：清理循环
    # ------------------------------------------------------------

    async def run_cleanup_loop(self) -> None:
        """定期清理过期日程与旧目标。

        循环间隔由 ``autonomous_planning.cleanup_interval``（秒）控制；
        每个间隔内调用 ``goal_manager.cleanup_expired_schedules()`` 与
        ``goal_manager.cleanup_old_goals(days=...)``。
        """
        logger.info("🧹 麦麦目标清理循环已启动")

        try:
            while not self._stop_event.is_set():
                try:
                    await self._cleanup_old_goals()
                except Exception as exc:
                    logger.error(f"清理目标异常: {exc}", exc_info=True)

                cleanup_interval = self._plugin.config.autonomous_planning.cleanup_interval
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=float(cleanup_interval))
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.debug("清理循环被取消")
            raise
        finally:
            logger.info("🛑 目标清理循环已退出")

    async def _cleanup_old_goals(self) -> None:
        """清理旧目标与过期日程（一次性操作）。"""
        goal_manager = get_goal_manager()

        # 1. 清理过期的日程（昨天及更早的 ACTIVE 日程）
        expired_schedules = goal_manager.cleanup_expired_schedules()
        if expired_schedules > 0:
            logger.info(f"🧹 清理了 {expired_schedules} 个过期日程（昨天及更早）")

        # 2. 清理已完成 / 已取消的旧目标（保留 cleanup_old_goals_days 天）
        cleanup_days = self._plugin.config.autonomous_planning.cleanup_old_goals_days
        cleaned_count = goal_manager.cleanup_old_goals(days=cleanup_days)
        if cleaned_count > 0:
            logger.info(f"🧹 清理了 {cleaned_count} 个旧目标（{cleanup_days} 天前）")

        # 3. 清理过期的 LLM 调用日志
        cfg = self._plugin.config.schedule
        if cfg.llm_log_enabled:
            try:
                from ..utils.llm_logger import cleanup_old_logs
                log_dir = self._plugin._data_dir / "llm_logs"
                deleted = cleanup_old_logs(log_dir, cfg.llm_log_retention_days)
                if deleted > 0:
                    logger.info(f"🧹 清理了 {deleted} 个过期 LLM 日志")
            except Exception as exc:
                logger.debug(f"LLM 日志清理失败: {exc}")

    # ------------------------------------------------------------
    # 后台任务 2：自动调度循环（每日定时生成日程）
    # ------------------------------------------------------------

    async def run_scheduler_loop(self) -> None:
        """启动并守护 ``ScheduleAutoScheduler``（每日定时自动生成日程）。

        调度器内部自带定时循环，本方法仅负责启动 + 等待停止信号 + 优雅关闭。
        延迟 5 秒启动，确保插件其他组件就绪。
        """
        logger.info("📅 自动调度循环已启动")
        try:
            # 延迟 5 秒启动，确保插件其他组件就绪
            await asyncio.sleep(5)
            self._auto_scheduler = ScheduleAutoScheduler(plugin=self._plugin)
            await self._auto_scheduler.start()

            # 等待停止信号
            await self._stop_event.wait()
        except asyncio.CancelledError:
            logger.debug("自动调度循环被取消")
            raise
        finally:
            # 优雅停止调度器
            if self._auto_scheduler is not None:
                try:
                    await self._auto_scheduler.stop()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"停止自动调度器异常: {exc}")
                self._auto_scheduler = None
            logger.info("🛑 自动调度循环已退出")

    # ------------------------------------------------------------
    # 停止控制
    # ------------------------------------------------------------

    async def stop(self) -> None:
        """通知所有循环停止。"""
        self._stop_event.set()
