"""麦麦自主规划插件 v4 - 主入口

完整 7 个组件外壳，业务逻辑下沉到 ``services/``。

组件总览：
- 4 个 ``@Tool``      ：manage_goal_v4 / get_planning_status_v4 / generate_schedule_v4 / apply_schedule_v4
- 1 个 ``@Command``   ：planning_v4 (``/plan`` 或 ``/规划``)
- 1 个 ``@EventHandler`` ：autonomous_planner_v4 (ON_START)
- 1 个 ``@HookHandler``  ：schedule_inject_v4 (maisaka.planner.before_request) ⭐ 注入入口
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Tuple
import asyncio
import logging
import shutil

from maibot_sdk import API, Command, EventHandler, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import EventType, HookMode, HookOrder, ToolParameterInfo, ToolParamType

from .config_models import AutonomousPlanningV4Config
from .services import CleanupService, CommandService, InjectService, ProactiveService, ToolsService


logger = logging.getLogger(__name__)


# ============================================================
# v4.0 → v4.1 配置迁移：把 [autonomous_planning.schedule.*] 搬到顶层 [schedule.*]
# ============================================================

_SCHEDULE_SUBKEY = "schedule"
_INJECT_SUBKEY = "inject"


def _migrate_v40_to_v41(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """把旧 ``[autonomous_planning.schedule.xxx]`` 字段搬到顶层 ``[schedule.xxx]``。

    SDK 只展开顶层 PluginConfigBase 子段为 UI section，旧版的三级嵌套结构
    会让 schedule / inject 段在 WebUI 中不可见。本迁移把它们提到顶层，
    并保留 ``[autonomous_planning]`` 段下的清理参数原位。

    Args:
        raw: 用户提供的原始配置字典。

    Returns:
        (新配置字典, 是否产生了迁移)。
    """
    if not isinstance(raw, dict):
        return raw, False

    ap = raw.get("autonomous_planning")
    if not isinstance(ap, dict):
        return raw, False

    legacy_schedule = ap.get(_SCHEDULE_SUBKEY)
    if not isinstance(legacy_schedule, dict):
        return raw, False

    cfg = dict(raw)
    new_ap = dict(ap)
    new_ap.pop(_SCHEDULE_SUBKEY, None)
    cfg["autonomous_planning"] = new_ap

    # 把 schedule.inject 拆出来作为顶层 [inject]
    legacy_schedule = dict(legacy_schedule)
    legacy_inject = legacy_schedule.pop(_INJECT_SUBKEY, None)

    # 顶层 [schedule] 合并（保留用户在新位置自定义的字段，冲突时优先新位置）
    top_schedule = dict(cfg.get(_SCHEDULE_SUBKEY) or {})
    schedule_conflicts: List[str] = []
    for k, v in legacy_schedule.items():
        if k in top_schedule and top_schedule[k] != v:
            schedule_conflicts.append(k)
        else:
            top_schedule.setdefault(k, v)
    cfg[_SCHEDULE_SUBKEY] = top_schedule

    inject_conflicts: List[str] = []
    if isinstance(legacy_inject, dict):
        top_inject = dict(cfg.get(_INJECT_SUBKEY) or {})
        for k, v in legacy_inject.items():
            if k in top_inject and top_inject[k] != v:
                inject_conflicts.append(k)
            else:
                top_inject.setdefault(k, v)
        cfg[_INJECT_SUBKEY] = top_inject

    # 当新旧位置同字段值不一致时，明确告诉用户保留了新位置值（避免静默丢失旧设置）
    if schedule_conflicts:
        logger.warning(
            "v4.0→4.1 配置迁移：schedule 段下列字段在新旧位置都存在但值不同，"
            "已保留新位置 [schedule.X] 的值，旧位置 [autonomous_planning.schedule.X] 被丢弃：%s",
            schedule_conflicts,
        )
    if inject_conflicts:
        logger.warning(
            "v4.0→4.1 配置迁移：inject 段下列字段在新旧位置都存在但值不同，"
            "已保留新位置 [inject.X] 的值，旧位置 [autonomous_planning.schedule.inject.X] 被丢弃：%s",
            inject_conflicts,
        )

    return cfg, True


def _downgrade_traditional_inject_mode(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """v4.1.1+：``inject_mode='traditional'`` 已移除，自动降级为 ``smart``。

    避免 SDK 的 ``Literal["smart", "rule"]`` 校验拒绝旧用户的 config.toml。
    traditional 模式的语义（固定模板）已被 smart 模式的关键词预判 + LLM
    自判断覆盖，无功能损失。

    Args:
        raw: 用户提供的配置字典（可能已经过 ``_migrate_v40_to_v41``）。

    Returns:
        (新配置字典, 是否产生了降级)。
    """
    if not isinstance(raw, dict):
        return raw, False
    inject_section = raw.get(_INJECT_SUBKEY)
    if not isinstance(inject_section, dict):
        return raw, False
    if str(inject_section.get("inject_mode") or "").strip() != "traditional":
        return raw, False
    new_inject = dict(inject_section)
    new_inject["inject_mode"] = "smart"
    cfg = dict(raw)
    cfg[_INJECT_SUBKEY] = new_inject
    logger.warning(
        "inject_mode='traditional' 在 v4.1.1+ 已移除（语义被 smart 模式覆盖），"
        "已自动降级为 'smart'"
    )
    return cfg, True


class AutonomousPlanningPluginV4(MaiBotPlugin):
    """麦麦自主规划插件 v4"""

    config_model: ClassVar[type[PluginConfigBase]] = AutonomousPlanningV4Config

    def __init__(self) -> None:
        """初始化插件基础字段，service 实例延迟到 ``on_load`` 创建。"""
        super().__init__()
        self._plugin_root: Path = Path(__file__).resolve().parent
        # 持久化根目录：on_load 时会按 Host 分配的 ctx.paths.data_dir 覆盖；
        # 此处先给旧式源码目录兜底，保证未进入 Runner（如单测）时路径仍可用。
        self._data_dir: Path = self._plugin_root / "data"
        self._tools_svc: ToolsService | None = None
        self._cmd_svc: CommandService | None = None
        self._inject_svc: InjectService | None = None
        self._cleanup_svc: CleanupService | None = None
        self._proactive_svc: ProactiveService | None = None
        self._bg_tasks: List[asyncio.Task] = []
        # v4 新增：bot 全局配置缓存（on_load 时一次性拉取）
        self._bot_profile: Dict[str, str] = {}

    # ============================================================
    # 配置迁移 Hook（SDK 在校验前调用）
    # ============================================================

    def normalize_plugin_config(
        self,
        config_data: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """v4.0 → v4.1 配置迁移 + traditional 模式降级 + 委托 SDK 默认归一化。

        SDK 会在 config_data 与默认配置之间 merge，且 extra="ignore" 会丢弃未声明字段。
        所以这里必须**在 super 之前**把旧 ``[autonomous_planning.schedule.*]`` 搬到顶层，
        并把 v4.1.1 前已移除的 ``inject_mode='traditional'`` 降级到 ``smart``。
        """
        raw = dict(config_data) if isinstance(config_data, Mapping) else {}
        migrated, did_migrate = _migrate_v40_to_v41(raw) if raw else (raw, False)
        if did_migrate:
            logger.info("检测到 v4.0 旧配置，已迁移 [autonomous_planning.schedule.*] 到顶层 [schedule.*] / [inject.*]")

        # 单独的内部归一化（不依赖 v4.0→4.1 迁移触发）
        migrated, did_downgrade = _downgrade_traditional_inject_mode(migrated)

        normalized, default_changed = super().normalize_plugin_config(migrated)
        return normalized, did_migrate or did_downgrade or default_changed

    # ============================================================
    # 持久化目录解析与迁移
    # ============================================================

    def _resolve_data_dir(self) -> Path:
        """解析插件持久化根目录。

        MaiBot 1.2 起由 Host 通过 ``ctx.paths.data_dir`` 分配统一持久化目录
        （``<项目根>/data/plugins/<plugin_id>/``），把数据写进插件源码目录已被
        Host 标记为旧式用法——源码目录会被 ``git pull`` / 重装 / 重新 clone 覆盖，
        数据随之丢失。

        旧版 Host 或单测环境没有注入 ``paths`` 时，回退到源码目录下的 ``data/``。

        Returns:
            持久化根目录（不保证已存在）。
        """
        paths = getattr(getattr(self, "_ctx", None), "paths", None)
        data_dir = getattr(paths, "data_dir", None)
        if data_dir:
            return Path(data_dir)
        return self._plugin_root / "data"

    def _migrate_legacy_data(self, data_dir: Path) -> None:
        """把旧式源码目录 ``data/`` 的数据一次性搬到统一持久化目录。

        仅在目标目录尚未建立 ``goals.db`` 时执行，且用**复制**而非移动：旧文件
        原样保留作为回退，迁移失败也不影响以新目录启动。整个迁移失败不阻断加载。

        Args:
            data_dir: 目标持久化根目录。
        """
        legacy_dir = self._plugin_root / "data"
        try:
            if legacy_dir.resolve() == Path(data_dir).resolve():
                return
            if not legacy_dir.is_dir():
                return

            # WAL 模式下 -wal/-shm 与主库同源，必须整体复制才能得到一致快照
            if (data_dir / "goals.db").exists():
                return

            data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(legacy_dir, data_dir, dirs_exist_ok=True)
            logger.info(
                "[v4] 已将旧数据目录迁移到统一持久化目录：%s -> %s（旧文件保留作回退）",
                legacy_dir,
                data_dir,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[v4] 旧数据目录迁移失败，继续使用新目录启动: %s", exc)

    # ============================================================
    # 生命周期
    # ============================================================

    async def on_load(self) -> None:
        """插件加载完成：建立数据目录、初始化 services、启动后台任务。"""
        # 仅当插件被启用时才初始化 service 与启动后台任务
        if not self.config.plugin.enabled:
            logger.warning("[v4] 插件已禁用（plugin.enabled=False），跳过初始化")
            return

        data_dir = self._resolve_data_dir()
        self._migrate_legacy_data(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = data_dir
        db_path = str(data_dir / "goals.db")

        # 目标库全局单例按统一目录建立：GoalManager() 的默认值仍指向源码 data/，
        # 必须在此显式传入，否则各 service 走单例时会写回旧目录。
        from .planner.goal_manager import get_goal_manager
        get_goal_manager(data_dir=str(data_dir))

        # v4 新增：预拉取 bot 全局配置（personality / bot.nickname 等）一次性缓存
        # PromptBuilder 不再运行时调用 config_api.get_global_config
        self._bot_profile = await self._prefetch_bot_profile()

        # 初始化 services（依赖注入：plugin 自身 + 数据库路径）
        self._tools_svc = ToolsService(self, db_path=db_path)
        self._cmd_svc = CommandService(self)
        self._inject_svc = InjectService(self)
        self._cleanup_svc = CleanupService(self)
        self._proactive_svc = ProactiveService(self)

        # 启动后台任务（清理循环 + 自动调度循环 + 注入缓存预热 + v4.4 主动行为循环）
        self._bg_tasks.append(asyncio.create_task(self._cleanup_svc.run_cleanup_loop()))
        self._bg_tasks.append(asyncio.create_task(self._cleanup_svc.run_scheduler_loop()))
        self._bg_tasks.append(asyncio.create_task(self._inject_svc.preheat_cache()))
        self._bg_tasks.append(asyncio.create_task(self._proactive_svc.run_loop()))

        logger.info("[v4] 自主规划插件 v4 已加载，data_dir=%s", data_dir)

    async def _prefetch_bot_profile(self) -> Dict[str, str]:
        """预拉取 bot 全局配置（人设、回复风格、兴趣、昵称）。

        失败时返回空字典，PromptBuilder 会使用内置默认值。

        Returns:
            ``{"personality": str, "reply_style": str, "interest": str, "bot_name": str}``
        """
        profile: Dict[str, str] = {}
        try:
            personality = await self.ctx.config.get("personality.personality", "")
            reply_style = await self.ctx.config.get("personality.reply_style", "")
            interest = await self.ctx.config.get("personality.interest", "")
            bot_name = await self.ctx.config.get("bot.nickname", "")
            profile = {
                "personality": str(personality or ""),
                "reply_style": str(reply_style or ""),
                "interest": str(interest or ""),
                "bot_name": str(bot_name or ""),
            }
            logger.debug("[v4] bot_profile 已预拉取: %s", profile)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[v4] 预拉取 bot_profile 失败，使用空值: %s", exc)
        return profile

    # ============================================================
    # 对外暴露：ScheduleGenerator 配置构建（单一来源）
    # ============================================================

    def build_schedule_config(self) -> Dict[str, Any]:
        """构建供 ``ScheduleGenerator`` 使用的 dict 配置。

        统一所有 ScheduleGenerator 构造入口（``ToolsService`` /
        ``ScheduleAutoScheduler`` / ``CommandService``）的配置来源，避免出现
        "部分入口漏带 bot_profile"等路径不一致问题（v4.4.4 修复的 bug 即源于此：
        ``auto_scheduler`` 原本用 ``self.plugin.config.schedule.model_dump()``
        构造 config，而 pydantic 模型里不存在 ``bot_profile`` 字段，导致 prompt
        生成时人设缺失）。

        Returns:
            包含 schedule 段全部强类型字段，以及插件层缓存的 ``bot_profile`` 的
            可变 dict。调用方拿到后可临时覆盖 ``custom_prompt`` 等字段而不影响下次调用。
        """
        cfg = self.config.schedule
        return {
            "use_multi_round": cfg.use_multi_round,
            "max_rounds": cfg.max_rounds,
            "quality_threshold": cfg.quality_threshold,
            "min_activities": cfg.min_activities,
            "max_activities": cfg.max_activities,
            "enable_detailed_description": cfg.enable_detailed_description,
            "min_description_length": cfg.min_description_length,
            "max_description_length": cfg.max_description_length,
            "max_tokens": cfg.max_tokens,
            "custom_prompt": cfg.custom_prompt,
            "timezone": cfg.timezone,
            "llm_task_name": cfg.llm_task_name,
            "recent_schedule_days": cfg.recent_schedule_days,
            "bot_profile": dict(self._bot_profile) if self._bot_profile else {},
        }

    async def on_unload(self) -> None:
        """插件卸载：通知所有循环停止 → cancel 后台任务 → 等待退出 → 关闭数据库。"""
        if self._cleanup_svc is not None:
            await self._cleanup_svc.stop()
        if self._proactive_svc is not None:
            await self._proactive_svc.stop()

        for task in self._bg_tasks:
            if not task.done():
                task.cancel()

        # 等待全部任务退出（异常吞掉，避免阻塞卸载）
        for task in self._bg_tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                logger.debug("后台任务退出: %s", exc)

        self._bg_tasks.clear()

        # 释放 GoalManager 持有的 SQLite 线程本地连接池
        from .planner.goal_manager import close_goal_manager
        close_goal_manager()

        logger.info("[v4] 自主规划插件 v4 已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """处理配置热重载事件。

        Args:
            scope: 变更范围 (``self`` / ``bot`` / ``model``)
            config_data: 当前范围最新配置数据
            version: 配置版本号
        """
        del config_data
        logger.info("[v4] 配置热更新 scope=%s version=%s", scope, version)
        # services 持有 plugin 引用，配置通过 self.config.xxx 动态读取，无需特殊处理

    # ============================================================
    # Tool 组件（4 个，对应 v3 的 4 个 BaseTool 子类）
    # ============================================================

    @Tool(
        "manage_goal_v4",
        description="管理长期目标，支持创建、查看、更新、暂停、恢复、完成、取消、删除目标",
        parameters=[
            ToolParameterInfo(
                name="action",
                param_type=ToolParamType.STRING,
                description="操作类型: create / list / get / update / pause / resume / complete / cancel / delete",
                required=True,
            ),
            ToolParameterInfo(
                name="goal_id",
                param_type=ToolParamType.STRING,
                description="目标 ID（除 create 和 list 外都需要）",
                required=False,
            ),
            ToolParameterInfo(
                name="name",
                param_type=ToolParamType.STRING,
                description="目标名称（create 时必需）",
                required=False,
            ),
            ToolParameterInfo(
                name="description",
                param_type=ToolParamType.STRING,
                description="目标描述（create 时必需）",
                required=False,
            ),
            ToolParameterInfo(
                name="goal_type",
                param_type=ToolParamType.STRING,
                description="目标类型: health_check / social_maintenance / learn_topic / custom",
                required=False,
            ),
            ToolParameterInfo(
                name="priority",
                param_type=ToolParamType.STRING,
                description="优先级: high / medium / low",
                required=False,
            ),
            ToolParameterInfo(
                name="time_window",
                param_type=ToolParamType.STRING,
                description="时间窗口，格式 'HH:MM-HH:MM'（如 '09:00-10:30'）",
                required=False,
            ),
            ToolParameterInfo(
                name="deadline_hours",
                param_type=ToolParamType.FLOAT,
                description="截止时间（从现在开始的小时数）",
                required=False,
            ),
            ToolParameterInfo(
                name="parameters",
                param_type=ToolParamType.STRING,
                description="目标参数（JSON 字符串）",
                required=False,
            ),
        ],
    )
    async def handle_manage_goal(self, **kwargs: Any) -> Dict[str, Any]:
        """目标管理工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.manage_goal(**kwargs)

    @Tool(
        "get_planning_status_v4",
        description="查看今日日程安排，按时间顺序显示正在进行和即将到来的活动",
        parameters=[
            ToolParameterInfo(
                name="detailed",
                param_type=ToolParamType.BOOLEAN,
                description="是否显示详细信息",
                required=False,
            ),
        ],
    )
    async def handle_get_planning_status(self, **kwargs: Any) -> Dict[str, Any]:
        """规划状态查询工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.get_planning_status(**kwargs)

    @Tool(
        "generate_schedule_v4",
        description="自动生成并应用全局每日/每周/每月计划，使用 LLM 根据 bot 人设智能生成",
        parameters=[
            ToolParameterInfo(
                name="schedule_type",
                param_type=ToolParamType.STRING,
                description="日程类型: daily / weekly / monthly",
                required=True,
            ),
            ToolParameterInfo(
                name="auto_apply",
                param_type=ToolParamType.BOOLEAN,
                description="是否立即应用日程（默认 true）",
                required=False,
            ),
        ],
    )
    async def handle_generate_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        """日程生成工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.generate_schedule(**kwargs)

    @Tool(
        "apply_schedule_v4",
        description="应用之前生成的日程，将日程项转换为全局可执行的目标",
        parameters=[
            ToolParameterInfo(
                name="schedule_data",
                param_type=ToolParamType.STRING,
                description="日程数据（从 generate_schedule 获取，JSON 字符串）",
                required=True,
            ),
        ],
    )
    async def handle_apply_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        """日程应用工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.apply_schedule(**kwargs)

    @Tool(
        "update_schedule_v4",
        description="根据自然语言请求维护日程：角色裁判决定接受/未来约定/拒绝；接受时调整今日日程，未来约定写入候选清单",
        parameters=[
            ToolParameterInfo(
                name="description",
                param_type=ToolParamType.STRING,
                description="日程变更的自然语言描述（如\"下午两点一起学习\"、\"周末打游戏\"）",
                required=True,
            ),
        ],
    )
    async def handle_update_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        """角色裁判式日程更新工具入口。"""
        if self._tools_svc is None:
            return {"type": "error", "content": "插件未启用"}
        return await self._tools_svc.update_schedule(**kwargs)

    # ============================================================
    # Command 组件
    # ============================================================

    @Command(
        "planning_v4",
        description="日程规划系统管理命令（支持 /plan 与 /规划）",
        pattern=r"(?P<planning_cmd>^/(plan|规划).*$)",
    )
    async def handle_planning_command(
        self,
        text: str = "",
        stream_id: str = "",
        user_id: str = "",
        platform: str = "",
        group_id: str = "",
        matched_groups: Any = None,
        **kwargs: Any,
    ) -> Tuple[bool, str, bool]:
        """``/plan`` 命令入口，转发给 CommandService。"""
        del kwargs
        if self._cmd_svc is None:
            if stream_id:
                await self.ctx.send.text("插件未启用", stream_id)
            return False, "插件未启用", True
        return await self._cmd_svc.execute(
            text=text,
            stream_id=stream_id,
            user_id=user_id,
            platform=platform,
            group_id=group_id,
            matched_groups=matched_groups if isinstance(matched_groups, dict) else {},
        )

    # ============================================================
    # EventHandler 组件（ON_START 用作启动信号；POST_LLM 不再使用，见 POC_RESULT.md）
    # ============================================================

    @EventHandler(
        "autonomous_planner_v4",
        description="启动事件处理器：用于配合主程序生命周期发出启动日志",
        event_type=EventType.ON_START,
        intercept_message=False,
        weight=10,
    )
    async def handle_on_start(self, **kwargs: Any) -> Tuple[bool, bool, Any, Any, Any]:
        """主程序 ON_START 事件回调。

        所有后台任务已在 ``on_load`` 中启动，这里仅做日志确认。
        """
        del kwargs
        logger.info("[v4] 收到 ON_START 事件，后台任务运行中")
        return True, True, None, None, None

    # ============================================================
    # API 组件（对外暴露，可被其他插件通过 ctx.api.call 调用）
    # ============================================================

    @API(
        "get_current_activity",
        description="返回当前时间段最新的日程活动快照（含描述、时间窗口、即将到来活动）",
        version="1",
        public=True,
    )
    async def api_get_current_activity(
        self,
        chat_id: str = "global",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """对外 API：当前时间段最新的日程活动快照。

        其他插件调用方式::

            snapshot = await self.ctx.api.call(
                "xuqian13.autonomous-planning-plugin-v4.get_current_activity",
                chat_id="global",
            )
            if snapshot["has_activity"]:
                print(snapshot["activity"]["name"])  # 例如 "睡前刷手机"

        Args:
            chat_id: 可选的会话 ID 过滤；默认 ``global``（与日程注入一致）。
            **kwargs: 预留扩展，当前未使用。

        Returns:
            dict: 见 ``InjectService.get_current_activity_snapshot`` 的返回结构。
        """
        del kwargs
        if self._inject_svc is None:
            return {
                "has_activity": False,
                "activity": None,
                "next_activities": [],
                "as_of": "",
                "timezone": "",
                "error": "plugin_not_initialized",
            }
        return await self._inject_svc.get_current_activity_snapshot(chat_id or "global")

    # ============================================================
    # HookHandler 组件（v4 注入入口，替代 v3 的 POST_LLM）
    # ============================================================

    @HookHandler(
        "maisaka.planner.before_request",
        name="schedule_inject_v4",
        description="在 Maisaka 向 LLM 发起规划请求前注入当前日程信息",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
    )
    async def handle_inject_schedule(
        self,
        messages: List[Dict[str, Any]] | None = None,
        session_id: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """日程注入 Hook 入口，转发给 InjectService。"""
        if self._inject_svc is None or not self.config.schedule.inject_schedule:
            return {"action": "continue"}
        return await self._inject_svc.inject_into_planner_messages(
            messages=messages or [],
            session_id=session_id,
            **kwargs,
        )

    # v4.4.0 恢复：v4.3.1 误删此 HookHandler，但主程序 5/21 已通过 commit 478256f2
    # （feat: 支持回复器 Hook 指定模型）补上 ``maisaka.replyer.before_request`` hook。
    # 现在该 hook 实际可用，恢复 replyer 阶段注入。
    @HookHandler(
        "maisaka.replyer.before_request",
        name="schedule_inject_replyer_v4",
        description="在 Maisaka replyer 调 LLM 前把当前活动注入到 extra_prompt（突出活人感，不要主动提及）",
        mode=HookMode.BLOCKING,
        order=HookOrder.NORMAL,
    )
    async def handle_inject_replyer(
        self,
        session_id: str = "",
        attempt: int = 1,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """replyer 注入 Hook 入口，转发给 InjectService。"""
        if self._inject_svc is None or not self.config.schedule.inject_into_replyer:
            return {"action": "continue"}
        return await self._inject_svc.inject_into_replyer_extra_prompt(
            session_id=session_id,
            attempt=attempt,
            **kwargs,
        )


def create_plugin() -> AutonomousPlanningPluginV4:
    """创建 v4 插件实例（Runner 加载入口）。

    Returns:
        AutonomousPlanningPluginV4: 新的 v4 插件实例
    """
    return AutonomousPlanningPluginV4()
