"""自主规划插件 v4 - 配置模型。

**v4.1 结构扁平化**：SDK 只展开"顶层 PluginConfigBase 字段"为 WebUI section，
因此把原嵌套的 ``schedule`` / ``inject`` 段提到顶层，让 UI 能直接渲染。
旧 ``[autonomous_planning.schedule.xxx]`` 路径通过 ``plugin.py`` 中的
``normalize_plugin_config`` 迁移自动搬到新位置。

每个字段都通过 ``json_schema_extra`` 提供 UI 元数据（label / hint / order /
placeholder / rows / item_type / input_type 等），WebUI 据此渲染表单；
``description`` 保留长说明用于 schema 文档场景。

字段编排按 ``order`` 数字分组（schedule 段）：
    1-9    日程注入开关
    10-29  日程生成参数
    30-49  定时 / 次日推断
    50-59  跨群上下文 + 跨天活动
    60-69  LLM 调用 / 日志归档
    70-79  角色裁判
    80-89  缓存
    90-99  权限 / 白名单
    100+   时区
"""

from __future__ import annotations

from typing import ClassVar, List, Literal

from maibot_sdk import Field, PluginConfigBase


# ============================================================
# [plugin]
# ============================================================


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置段"""

    __ui_label__: ClassVar[str] = "插件"
    __ui_icon__: ClassVar[str] = "package"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description="是否启用插件。关闭后所有日程生成 / 注入 / 命令响应停止。",
        json_schema_extra={
            "label": "启用插件",
            "hint": "关闭后日程生成、注入和命令响应都停止",
            "order": 1,
        },
    )
    config_version: str = Field(
        default="4.4.5",
        description="配置文件版本号",
        json_schema_extra={
            "label": "配置版本",
            "hint": "请勿手动改",
            "disabled": True,
            "order": 2,
        },
    )


# ============================================================
# [autonomous_planning] —— 后台清理参数
# ============================================================


class AutonomousPlanningConfig(PluginConfigBase):
    """自主规划全局后台参数段"""

    __ui_label__: ClassVar[str] = "后台清理"
    __ui_icon__: ClassVar[str] = "trash-2"
    __ui_order__: ClassVar[int] = 1

    cleanup_interval: int = Field(
        default=3600,
        ge=60,
        description="后台清理循环间隔（秒，默认 1 小时）。",
        json_schema_extra={
            "label": "清理间隔",
            "hint": "秒；后台清理过期日程 / 旧目标 / LLM 日志的间隔",
            "order": 1,
        },
    )
    cleanup_old_goals_days: int = Field(
        default=30,
        ge=1,
        description="保留多少天前的已完成 / 取消目标。",
        json_schema_extra={
            "label": "保留天数",
            "hint": "天；超过此天数的旧目标（完成/取消）自动删除",
            "order": 2,
        },
    )


# ============================================================
# [schedule] —— 日程管理（核心段，原 [autonomous_planning.schedule]）
# ============================================================


class ScheduleConfig(PluginConfigBase):
    """日程管理配置段"""

    __ui_label__: ClassVar[str] = "日程管理"
    __ui_icon__: ClassVar[str] = "calendar"
    __ui_order__: ClassVar[int] = 2

    # ── 1-9 注入开关 ──────────────────────────────────────────

    inject_schedule: bool = Field(
        default=True,
        description="在 planner 决策时把当前活动注入 messages 列表（影响是否回复 / 用哪个工具）。",
        json_schema_extra={
            "label": "注入到 planner",
            "hint": "决策阶段注入；让模型知道当前活动",
            "order": 1,
        },
    )
    inject_into_replyer: bool = Field(
        default=True,
        description="在 replyer 调 LLM 前把当前活动作为 extra_prompt 注入；让回复语气贴合当前状态。"
                    "与 planner 注入共享冷却，不会两阶段连刷。"
                    "v4.4 起恢复（v4.3.1 误删，主程序已补上 maisaka.replyer.before_request hook）。",
        json_schema_extra={
            "label": "注入到 replyer",
            "hint": "回复阶段注入；让回复语气贴合当前活动状态",
            "order": 2,
        },
    )
    auto_generate: bool = Field(
        default=True,
        description="用户询问日程时若当天无日程则自动调 generate_schedule 生成。",
        json_schema_extra={
            "label": "自动生成日程",
            "hint": "用户问起当天日程但还没有时自动调生成",
            "order": 3,
        },
    )
    max_future_activities: int = Field(
        default=3,
        ge=0,
        description="智能注入时最多显示的未来活动数量。",
        json_schema_extra={
            "label": "未来活动条数",
            "hint": "0 = 不显示；注入文本中'接下来'的活动数量",
            "order": 4,
        },
    )

    # ── 10-29 生成参数 ────────────────────────────────────────

    custom_prompt: str = Field(
        default="",
        description="自定义日程生成提示词（如\"今天想多运动\"、\"专注学习\"等，留空使用默认风格）。",
        json_schema_extra={
            "label": "自定义生成 prompt",
            "hint": "留空使用默认；如\"今天想多运动\"\"专注学习\"",
            "rows": 3,
            "placeholder": "（留空使用默认风格）",
            "order": 10,
        },
    )
    use_multi_round: bool = Field(
        default=True,
        description="启用多轮生成（首轮质量不达标时按反馈重试，提升日程质量）。",
        json_schema_extra={
            "label": "多轮生成",
            "hint": "首轮质量不达标时按反馈重试，提升质量",
            "order": 11,
        },
    )
    max_rounds: int = Field(
        default=2,
        ge=1,
        le=5,
        description="多轮生成最大轮数（1-3 推荐 2）。",
        json_schema_extra={
            "label": "最大轮数",
            "hint": "1-5；推荐 2；越多越费 token",
            "order": 12,
        },
    )
    quality_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="多轮生成质量阈值（达到此分即停止），0.80-0.90 推荐 0.85。",
        json_schema_extra={
            "label": "质量阈值",
            "hint": "0-1；达到此分提前结束多轮重试，推荐 0.85",
            "order": 13,
        },
    )
    min_activities: int = Field(
        default=8,
        ge=1,
        description="单日最少活动数（建议 8-10）。",
        json_schema_extra={
            "label": "最少活动数",
            "hint": "单日最少；建议 8-10",
            "order": 14,
        },
    )
    max_activities: int = Field(
        default=15,
        ge=1,
        description="单日最多活动数（建议 12-15）。",
        json_schema_extra={
            "label": "最多活动数",
            "hint": "单日最多；建议 12-15",
            "order": 15,
        },
    )
    enable_detailed_description: bool = Field(
        default=True,
        description="启用详细活动描述（关闭后生成、注入、命令展示都不显示长描述）。",
        json_schema_extra={
            "label": "详细描述",
            "hint": "关闭后只显示活动名，不显示长描述",
            "order": 16,
        },
    )
    min_description_length: int = Field(
        default=20,
        ge=5,
        description="活动描述最小字符数。",
        json_schema_extra={
            "label": "描述最少字数",
            "hint": "20-30 较合理；过短易模糊",
            "order": 17,
        },
    )
    max_description_length: int = Field(
        default=50,
        ge=5,
        description="活动描述最大字符数。",
        json_schema_extra={
            "label": "描述最多字数",
            "hint": "50-100 较合理；过长占 token",
            "order": 18,
        },
    )
    max_tokens: int = Field(
        default=8192,
        ge=1000,
        description="LLM 生成日程的最大 token 数。",
        json_schema_extra={
            "label": "最大 token",
            "hint": "建议 8192；过小可能截断日程",
            "order": 19,
        },
    )
    generation_timeout: float = Field(
        default=180.0,
        ge=10.0,
        description="单次生成超时时间（秒），推荐 120-300。",
        json_schema_extra={
            "label": "生成超时",
            "hint": "秒；推荐 120-300；过短可能失败",
            "order": 20,
        },
    )
    recent_schedule_days: int = Field(
        default=3,
        ge=1,
        le=14,
        description="生成新日程时回看最近 N 天的日程作为去重参考。N=1 = 只看昨天（旧行为）；N=3-5 推荐。",
        json_schema_extra={
            "label": "回看天数",
            "hint": "1-14；防止交替式重复（周一审稿/周二写专栏/周三又审稿）；推荐 3",
            "order": 21,
        },
    )

    # ── 30-49 定时 / 次日推断 ─────────────────────────────────

    auto_schedule_enabled: bool = Field(
        default=True,
        description="每天定时自动生成下一天日程。",
        json_schema_extra={
            "label": "定时自动生成",
            "hint": "每天到点自动调 LLM 生成第二天日程",
            "order": 30,
        },
    )
    auto_schedule_time: str = Field(
        default="00:30",
        description="定时自动生成时间（24 小时制 HH:MM）。",
        json_schema_extra={
            "label": "生成时间",
            "hint": "HH:MM；推荐 00:30 凌晨低峰期",
            "placeholder": "00:30",
            "order": 31,
        },
    )
    auto_infer_next_day_prompt: bool = Field(
        default=True,
        description="是否在晚间根据近期活动自动推断次日策略提示词。",
        json_schema_extra={
            "label": "次日策略推断",
            "hint": "晚间自动总结近期活动写入次日，保持日程连续演化",
            "order": 32,
        },
    )
    infer_time: str = Field(
        default="22:30",
        description="次日策略推断时间（HH:MM）。",
        json_schema_extra={
            "label": "推断时间",
            "hint": "HH:MM；通常晚间执行",
            "placeholder": "22:30",
            "order": 33,
        },
    )
    infer_lookback_days: int = Field(
        default=3,
        ge=1,
        le=7,
        description="推断时回看历史天数（1-7）。",
        json_schema_extra={
            "label": "回看天数",
            "hint": "1-7；统计最近几天活动模式",
            "order": 34,
        },
    )
    infer_max_prompt_chars: int = Field(
        default=300,
        ge=50,
        description="推断结果最大字符数。",
        json_schema_extra={
            "label": "推断 prompt 字数",
            "hint": "限制推断出的 custom_prompt 长度",
            "order": 35,
        },
    )
    infer_use_completion_signal: bool = Field(
        default=True,
        description="推断时是否参考活动状态和进度。",
        json_schema_extra={
            "label": "参考完成度",
            "hint": "推断时考虑各活动的执行状态",
            "order": 36,
        },
    )

    # ── 50-59 跨群上下文 + 跨天活动 ────────────────────────────

    history_message_limit: int = Field(
        default=30,
        ge=0,
        description="生成日程时从白名单群里回读最近 N 条聊天消息作为上下文。0 = 禁用。",
        json_schema_extra={
            "label": "历史消息条数",
            "hint": "0 = 禁用；建议 20-50；让日程贴近近期发生的事",
            "order": 50,
        },
    )
    knowledge_search_limit: int = Field(
        default=5,
        ge=0,
        description="生成日程时检索知识库的最大条数。0 = 禁用。",
        json_schema_extra={
            "label": "知识库条数",
            "hint": "0 = 禁用；建议 3-8；让日程参考长期记忆",
            "order": 51,
        },
    )
    cross_day_activity: bool = Field(
        default=True,
        description="启用跨天活动（time_window 跨夜的写法 + 注入时回读昨日跨夜活动）。",
        json_schema_extra={
            "label": "跨天活动",
            "hint": "支持 23:00-01:30 这种跨夜写法；凌晨注入也能识别延续活动",
            "order": 52,
        },
    )

    # ── 60-69 LLM 调用 / 日志归档 ─────────────────────────────

    llm_task_name: str = Field(
        default="replyer",
        description="日程生成使用的 LLM 任务名，需在主程序 model_config.toml 中预先配置。",
        json_schema_extra={
            "label": "LLM 任务名",
            "hint": "对应主程序 model_config 的 task 名（replyer / planner ...）",
            "placeholder": "replyer",
            "order": 60,
        },
    )
    llm_log_enabled: bool = Field(
        default=True,
        description="是否把 LLM 调用归档到 data/llm_logs/，方便排查 prompt / 响应。",
        json_schema_extra={
            "label": "LLM 调用归档",
            "hint": "写入 data/llm_logs/ 便于事后排查",
            "order": 61,
        },
    )
    llm_log_retention_days: int = Field(
        default=7,
        ge=1,
        description="LLM 日志保留天数（cleanup_loop 自动清理过期文件）。",
        json_schema_extra={
            "label": "日志保留天数",
            "hint": "超过此天数的日志自动清理",
            "order": 62,
        },
    )

    # ── 70-79 角色裁判 ────────────────────────────────────────

    role_judge_enabled: bool = Field(
        default=True,
        description="update_schedule_v4 是否走 LLM 角色裁判模式（today/future/reject）。关闭后直接落 pending_commitments。",
        json_schema_extra={
            "label": "角色裁判模式",
            "hint": "开启：LLM 扮演角色判断 today/future/reject；关闭：直落 pending",
            "order": 70,
        },
    )
    role_judge_temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="角色裁判 LLM 温度。越低越保守（更倾向拒绝离谱请求）。",
        json_schema_extra={
            "label": "裁判温度",
            "hint": "0-2；建议 0.3；越低越保守、越倾向严格判断",
            "order": 71,
        },
    )

    # ── 80-89 缓存 ────────────────────────────────────────────

    cache_ttl: int = Field(
        default=300,
        ge=10,
        description="日程缓存有效期（秒，默认 5 分钟）；过期后重查 goals 表。",
        json_schema_extra={
            "label": "缓存有效期",
            "hint": "秒；默认 300（5 分钟）",
            "order": 80,
        },
    )
    cache_max_size: int = Field(
        default=100,
        ge=10,
        description="缓存最大条目数（LRU 策略，超出按最近最少使用淘汰）。",
        json_schema_extra={
            "label": "缓存最大条数",
            "hint": "LRU 淘汰；多群可适当调大",
            "order": 81,
        },
    )

    # ── 90-99 权限 / 白名单 ────────────────────────────────────

    admin_users: List[str] = Field(
        default_factory=list,
        description="管理员 QQ 号列表，控制谁能执行 /plan 命令；留空 = 所有人可用。",
        json_schema_extra={
            "label": "管理员 QQ",
            "hint": '纯数字 QQ 号，例 ["123456"]；留空 = 所有人可用',
            "item_type": "string",
            "placeholder": '["123456"]',
            "order": 90,
        },
    )
    allowed_streams: List[str] = Field(
        default_factory=list,
        description="启用日程注入 / 命令响应的聊天流白名单；留空 = 全部允许。支持 all / session:<id> / qq:group:<gid> / qq:private:<uid>。",
        json_schema_extra={
            "label": "群白名单",
            "hint": '留空=全部允许；支持 all / qq:group:123456 / qq:private:789',
            "item_type": "string",
            "placeholder": '[] 或 ["qq:group:123456"]',
            "order": 91,
        },
    )

    # ── 92-99 主动行为（v4.4 新增） ─────────────────────────────

    proactive_streams: List[str] = Field(
        default_factory=list,
        description="允许触发『主动发起对话 / 频率调控』的聊天流白名单（v4.4 新增）。"
                    "默认空 = 完全禁用，避免误打扰；必须显式列出才生效。"
                    "支持 3 种格式（v4.4.1 起）："
                    "(1) qq:group:<群号>（推荐，自动解析为 session_id）；"
                    "(2) qq:private:<QQ 号>（私聊）；"
                    "(3) session:<session_id>（直接 session_id）。"
                    "解析失败的条目会被自动跳过 + 警告，不影响其他条目。",
        json_schema_extra={
            "label": "主动行为白名单",
            "hint": '支持 qq:group:123456 / qq:private:789 / session:xxx；留空=禁用',
            "item_type": "string",
            "placeholder": '[] 或 ["qq:group:123456"]',
            "order": 92,
        },
    )
    enable_proactive_trigger: bool = Field(
        default=True,
        description="在活动切换瞬间（前 5 分钟）让 bot 主动开口（v4.4 新增）。"
                    "通过 ctx.maisaka.trigger_proactive 触发；每天每群每个活动只触发一次。"
                    "需要 proactive_streams 白名单显式列出才生效。",
        json_schema_extra={
            "label": "活动切换主动发起",
            "hint": "v4.4；活动开始 5 分钟内让 bot 主动开口（每群每活动每天 1 次）",
            "order": 93,
        },
    )
    enable_frequency_modulation: bool = Field(
        default=True,
        description="按当前活动类型动态调节聊天频率（v4.4 新增）。"
                    "通过 ctx.frequency.set_adjust 推给 heartflow：学习/工作时频率 30%，"
                    "休息/娱乐时 130%~150%；让 bot 在该忙的时候少说话、该闲的时候多说话。"
                    "需要 proactive_streams 白名单显式列出才生效。",
        json_schema_extra={
            "label": "活动频率调控",
            "hint": "v4.4；学习/工作时降频；休息/娱乐时升频",
            "order": 94,
        },
    )

    # ── 100+ 时区 ─────────────────────────────────────────────

    timezone: str = Field(
        default="Asia/Shanghai",
        description="时区设置（IANA 命名，如 Asia/Shanghai、UTC、America/New_York）。",
        json_schema_extra={
            "label": "时区",
            "hint": "IANA 命名；服务器跨时区时务必显式设置",
            "placeholder": "Asia/Shanghai",
            "order": 100,
        },
    )


# ============================================================
# [inject] —— 智能注入策略（原 [autonomous_planning.schedule.inject]）
# ============================================================


class InjectConfig(PluginConfigBase):
    """智能注入配置段：``[inject]``。

    控制 planner / replyer 注入时的策略（智能模式 / 意图分类 / 优化器 / 上下文缓存）。
    """

    __ui_label__: ClassVar[str] = "智能注入策略"
    __ui_icon__: ClassVar[str] = "zap"
    __ui_order__: ClassVar[int] = 3

    inject_mode: Literal["smart", "rule"] = Field(
        default="smart",
        description="（v4.2 起 deprecated）旧 smart/rule 双模式合并为统一管道；此字段保留仅为向后兼容，运行时已忽略。",
        json_schema_extra={
            "label": "注入模式（已弃用）",
            "hint": "v4.2 起合并为统一管道，此选项已无效，将在下个大版本删除",
            "disabled": True,
            "order": 1,
        },
    )
    enable_intent_classification: bool = Field(
        default=True,
        description="启用意图分类（识别用户问句类型，rule 模式必需；smart 模式仍会用于跳过技术问答场景）。",
        json_schema_extra={
            "label": "启用意图分类",
            "hint": "rule 模式必需；smart 模式用于过滤技术问答场景",
            "order": 2,
        },
    )
    enable_state_analysis: bool = Field(
        default=True,
        description="启用活动状态分析（v4.3 重激活）：按活动进度生成情绪化短语，"
                    "如\"学了一会儿了，还算专注\"，并注入到 planner/replyer 提示词。",
        json_schema_extra={
            "label": "启用状态分析",
            "hint": "v4.3 重激活；生成情绪化活动描述，让回复语气更贴合阶段",
            "order": 3,
        },
    )
    enable_inject_optimization: bool = Field(
        default=True,
        description="启用注入优化器（防止重复注入和无效打扰，planner / replyer 共用冷却）。",
        json_schema_extra={
            "label": "启用注入优化",
            "hint": "冷却控制；planner 与 replyer 共享，避免双阶段连刷",
            "order": 4,
        },
    )
    casual_chat_inject_probability: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="闲聊场景（非直接询问 / 非技术问答）的注入概率（0-1）。",
        json_schema_extra={
            "label": "闲聊注入概率",
            "hint": "0-1；闲聊场景的随机注入概率，0.5 = 一半概率注入",
            "order": 5,
        },
    )
    context_max_turns: int = Field(
        default=3,
        ge=1,
        description="对话上下文保留轮数（用于判断是否在连续讨论日程话题）。",
        json_schema_extra={
            "label": "上下文轮数",
            "hint": "连续 N 轮对话仍判定为日程话题时持续注入",
            "order": 6,
        },
    )
    context_ttl: int = Field(
        default=600,
        ge=60,
        description="对话上下文过期时间（秒），超时后认为是新会话。",
        json_schema_extra={
            "label": "上下文过期",
            "hint": "秒；超过此时间无新消息则重置上下文",
            "order": 7,
        },
    )


# ============================================================
# 顶层 —— 4 个 section 全部扁平挂载，SDK 才能展开为 UI section
# ============================================================


class AutonomousPlanningV4Config(PluginConfigBase):
    """v4 顶层配置模型。

    **注意**：4 个 PluginConfigBase 子段都必须作为**顶层字段**挂载，
    SDK 才会展开为独立 UI section（嵌套子段会被当成普通对象字段，UI 不渲染）。
    """

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    autonomous_planning: AutonomousPlanningConfig = Field(default_factory=AutonomousPlanningConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    inject: InjectConfig = Field(default_factory=InjectConfig)
