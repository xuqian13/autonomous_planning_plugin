"""端到端冒烟测试 —— 不依赖真实 MaiBot 主程序 / LLM / QQ 账号。

运行方式（任选一种 shell，在项目根目录下）::

    # PowerShell（Windows 默认）
    .\\.venv\\Scripts\\python.exe plugins\\xuqian13_autonomous-planning-plugin-v4\\tests\\run_smoke.py

    # CMD
    .venv\\Scripts\\python.exe plugins\\xuqian13_autonomous-planning-plugin-v4\\tests\\run_smoke.py

    # Git Bash / WSL / macOS / Linux
    ./.venv/Scripts/python.exe plugins/xuqian13_autonomous-planning-plugin-v4/tests/run_smoke.py

脚本顶部已强制 stdout/stderr 为 utf-8，不需要再设 ``PYTHONIOENCODING`` 环境变量。

覆盖范围（15 项）：
    1.  插件包导入（验证 cache 模块未缺失）
    2.  组件注册（4 Tool + 1 Command + 1 EventHandler + 2 HookHandler + 1 API = 9-10 个）
    3.  UI Section 渲染（4 个顶层 section 全部可见、字段带 label/hint/order）
    4.  v4.0 → v4.1 配置自动迁移
    5.  当前 config.toml 可加载且字段值正确
    6.  stream_filter 白名单匹配（含 qq:group / qq:private 分支）
    7.  llm_logger 写入 + cleanup_old_logs
    8.  pending_commitments CRUD（不污染 schedule_goals）
    9.  TimezoneManager 使用 zoneinfo 时区
    10. PromptBuilder 注入 4 个新段落（pending / history / knowledge / cross-day）
    11. role_judge：prompt 构造 / JSON 解析 / 未来日期推断
    12. get_current_activity_snapshot 返回结构（API 对外契约）
    13. replyer 注入 6 种场景（正常 / 重试 / 冷却 / 关闭 / 白名单 / 无活动）
    14. 多天日程对比（load_recent_schedule_summary 3 天回看）
    15. ScheduleAutoScheduler 构造 + start/stop（强类型 plugin.config 访问）

任何一项失败会抛 AssertionError + 退出码 1；全过输出 ALL SMOKE TESTS PASSED 退出码 0。
"""

from __future__ import annotations

# ── 强制 stdout/stderr 为 utf-8（避免 Windows 默认 GBK 在打印 emoji / 中文时崩溃）
import sys
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import asyncio
import importlib.util
import json
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


PLUGIN_DIR = Path(__file__).resolve().parent.parent
PKG_NAME = "_maibot_plugin_xuqian13_autonomous_planning_plugin_v4"

_PASS: list[str] = []
_FAIL: list[tuple[str, str]] = []


def step(name: str):
    """简易测试装饰器：打印 [OK] / [FAIL]，收集结果。"""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                fn(*args, **kwargs)
                _PASS.append(name)
                print(f"[OK]   {name}")
            except AssertionError as exc:
                _FAIL.append((name, str(exc) or "断言失败"))
                print(f"[FAIL] {name}: {exc}")
            except Exception as exc:
                _FAIL.append((name, f"{type(exc).__name__}: {exc}"))
                print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        return wrapper
    return decorator


# ============================================================
# 加载插件包（一次性，所有测试复用）
# ============================================================

spec = importlib.util.spec_from_file_location(
    PKG_NAME, PLUGIN_DIR / "__init__.py",
    submodule_search_locations=[str(PLUGIN_DIR)],
)
plugin_mod = importlib.util.module_from_spec(spec)
sys.modules[PKG_NAME] = plugin_mod
spec.loader.exec_module(plugin_mod)


def imp(rel: str):
    """便捷导入子模块。"""
    return importlib.import_module(f"{PKG_NAME}.{rel}")


def fresh_plugin():
    """新建一个走默认配置的插件实例。"""
    inst = plugin_mod.AutonomousPlanningPluginV4()
    inst.set_plugin_config({})
    return inst


def mock_plugin(**schedule_overrides):
    """构造一个用于 InjectService 的 mock plugin（避免 ctx 调用）。"""
    plugin = MagicMock()
    plugin.config.plugin.enabled = True
    plugin.config.schedule.cache_max_size = 100
    plugin.config.schedule.cache_ttl = 300
    plugin.config.schedule.timezone = "Asia/Shanghai"
    plugin.config.schedule.allowed_streams = []
    plugin.config.schedule.cross_day_activity = True
    plugin.config.schedule.inject_schedule = True
    plugin.config.schedule.inject_into_replyer = True
    # v4.4 新增的主动行为配置：默认全部关闭，避免 ProactiveService 触发 ctx 调用
    plugin.config.schedule.proactive_streams = []
    plugin.config.schedule.enable_proactive_trigger = False
    plugin.config.schedule.enable_frequency_modulation = False
    plugin.config.inject.inject_mode = "smart"
    plugin.config.inject.enable_intent_classification = True
    plugin.config.inject.enable_state_analysis = False
    plugin.config.inject.enable_inject_optimization = True
    plugin.config.inject.context_max_turns = 3
    plugin.config.inject.context_ttl = 600
    plugin.config.inject.casual_chat_inject_probability = 1.0
    for k, v in schedule_overrides.items():
        setattr(plugin.config.schedule, k, v)
    return plugin


# ============================================================
# 13 项测试
# ============================================================


@step("01. 插件包导入（cache 模块未缺失）")
def test_pkg_import():
    assert plugin_mod.__version__ == "4.4.5", f"version={plugin_mod.__version__}"
    cache_mod = imp("cache.lru_cache")
    c = cache_mod.LRUCache(max_size=2)
    c["a"] = 1; c["b"] = 2; c["c"] = 3
    assert "a" not in c
    assert c["b"] == 2 and c["c"] == 3


@step("02. 组件注册（10 个：5 Tool + 1 Command + 1 EventHandler + 2 HookHandler + 1 API）")
def test_components():
    inst = fresh_plugin()
    comps = inst.get_components()
    names = sorted((c["type"], c["name"]) for c in comps)
    required = {
        ("API", "get_current_activity"),
        ("TOOL", "manage_goal_v4"),
        ("TOOL", "get_planning_status_v4"),
        ("TOOL", "generate_schedule_v4"),
        ("TOOL", "apply_schedule_v4"),
        ("TOOL", "update_schedule_v4"),
        ("COMMAND", "planning_v4"),
        ("EVENT_HANDLER", "autonomous_planner_v4"),
        ("HOOK_HANDLER", "schedule_inject_v4"),
        ("HOOK_HANDLER", "schedule_inject_replyer_v4"),
    }
    missing = required - set(names)
    assert not missing, f"缺少组件: {missing}"
    assert len(comps) == 10, f"组件总数 {len(comps)} != 10"


@step("03. UI Section 渲染（4 个顶层 section + 字段 UI 元数据完整）")
def test_ui_schema():
    inst = fresh_plugin()
    assert inst.config.plugin.config_version == "4.4.5"
    assert inst.config.schedule.auto_infer_next_day_prompt is True
    schema = inst.build_config_schema(plugin_id="x.y", plugin_name="t")
    sections = schema["sections"]
    assert set(sections.keys()) == {"plugin", "autonomous_planning", "schedule", "inject"}, \
        f"sections={set(sections.keys())}"
    sched = sections["schedule"]["fields"]
    assert len(sched) >= 30, f"schedule 字段数={len(sched)}"
    # 关键字段 UI 元数据全部填了
    for fname in ("inject_schedule", "inject_into_replyer", "role_judge_enabled",
                  "allowed_streams", "cross_day_activity"):
        f = sched[fname]
        assert f["label"] and f["label"] != fname, f"{fname}.label 缺失"
        assert f["hint"], f"{fname}.hint 缺失"


@step("04. v4.0 → v4.2 配置迁移")
def test_migration():
    old_cfg = {
        "plugin": {"enabled": True, "config_version": "4.0.0"},
        "autonomous_planning": {
            "cleanup_interval": 1800,
            "schedule": {
                "inject_schedule": False,
                "allowed_streams": ["qq:group:111"],
                "inject": {"inject_mode": "traditional"},
            },
        },
    }
    inst = plugin_mod.AutonomousPlanningPluginV4()
    inst.set_plugin_config(old_cfg)
    assert inst.config.schedule.inject_schedule is False
    assert inst.config.schedule.allowed_streams == ["qq:group:111"]
    # v4.1.1+ 移除 traditional 模式，迁移层自动降级为 smart
    # v4.2 起 inject_mode 字段已 deprecated，但保留向后兼容
    assert inst.config.inject.inject_mode == "smart"
    assert inst.config.autonomous_planning.cleanup_interval == 1800


@step("05. 当前 config.toml 可加载")
def test_current_toml():
    with open(PLUGIN_DIR / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    inst = plugin_mod.AutonomousPlanningPluginV4()
    inst.set_plugin_config(cfg)
    # 不假设具体值（用户可能改过），只确认字段都能解析
    assert isinstance(inst.config.schedule.admin_users, list)
    assert isinstance(inst.config.schedule.inject_into_replyer, bool)
    # inject_mode 在 v4.2 起 deprecated 但保留向后兼容
    assert inst.config.inject.inject_mode in ("smart", "rule")
    assert inst.config.plugin.config_version == "4.4.5"


@step("06. stream_filter 白名单匹配")
def test_stream_filter():
    sf = imp("utils.stream_filter")
    assert sf.is_stream_allowed("s1", []) is True  # 留空 = 全允许
    assert sf.is_stream_allowed("s1", ["all"]) is True
    assert sf.is_stream_allowed("s1", ["session:s1"]) is True
    assert sf.is_stream_allowed("s2", ["session:s1"]) is False
    assert sf.is_stream_allowed("s1", ["qq:group:123"],
                                stream_info={"platform": "qq", "group_id": "123"}) is True
    assert sf.is_stream_allowed("s1", ["qq:group:999"],
                                stream_info={"platform": "qq", "group_id": "123"}) is False
    assert sf.is_stream_allowed("s1", ["qq:private:789"],
                                stream_info={"platform": "qq", "user_id": "789"}) is True


@step("07. llm_logger 读写 + cleanup")
def test_llm_logger():
    log_mod = imp("utils.llm_logger")
    log_dir = Path(tempfile.mkdtemp())
    log_mod.log_llm_call("schedule_generation", "prompt-body", "resp-body", "replyer", True, log_dir)
    log_mod.log_llm_call("role_decision", "p2", "r2", "replyer", False, log_dir)
    files = sorted(log_dir.iterdir())
    prefixes = {f.name.split("_")[0] for f in files}
    assert prefixes == {"ok", "fail"}
    ok_file = [f for f in files if f.name.startswith("ok_")][0]
    content = ok_file.read_text(encoding="utf-8")
    assert "PROMPT" in content and "prompt-body" in content
    # cleanup 不删近期文件
    assert log_mod.cleanup_old_logs(log_dir, retention_days=999) == 0


@step("08. pending_commitments CRUD")
def test_pending_commitments():
    gm_mod = imp("planner.goal_manager")
    gm = gm_mod.GoalManager(data_dir=str(Path(tempfile.mkdtemp())))
    gm.add_pending_commitment("2026-05-30", "周末一起打游戏", time="14:00", notes="开黑")
    got = gm.get_pending_commitments("2026-05-30")
    assert len(got) == 1 and got[0].name == "周末一起打游戏"
    # 不污染普通日程查询
    sg = gm.get_schedule_goals(chat_id="global", date_str="2026-05-30")
    assert all(g.goal_type != "pending_commitment" for g in sg)
    # consume 后清空
    assert len(gm.consume_pending_commitments("2026-05-30")) == 1
    assert not gm.get_pending_commitments("2026-05-30")


@step("09. TimezoneManager zoneinfo")
def test_timezone():
    tz_mod = imp("utils.timezone_manager")
    tz = tz_mod.TimezoneManager("Asia/Shanghai")
    now = tz.get_now()
    assert now.tzinfo is not None
    assert "Shanghai" in str(now.tzinfo) or "+08:00" in now.isoformat()


@step("10. PromptBuilder 注入 4 个新段落")
def test_prompt_builder():
    pb_mod = imp("planner.generator.prompt_builder")
    tz_mod = imp("utils.timezone_manager")
    pb = pb_mod.PromptBuilder({}, tz_mod.TimezoneManager("Asia/Shanghai"))
    prompt = pb.build_schedule_prompt(
        "daily", {},
        yesterday_context="昨天的日程:\n【06-18 周四】\n  06:40 乘坐热气球 — 在卡帕多奇亚看清晨奇岩地貌\n  20:00 欣赏星空",
        pending_commitments=[{"time": "14:00", "title": "打游戏", "notes": "周末"}],
        history_context="[12:30] 朵昕@群: 今天天气好",
        knowledge_context="麦麦喜欢油豆腐",
    )
    assert "今天需要纳入的约定" in prompt and "打游戏" in prompt
    assert "最近聊天背景" in prompt and "朵昕" in prompt
    assert "相关记忆参考" in prompt and "油豆腐" in prompt
    assert "连续性要求" in prompt and "不要无理由回到默认学习、游戏、上班日常" in prompt
    assert "先从最近一天摘要提取当前地点" in prompt
    assert "禁止照抄示例活动名" in prompt
    assert "跨天活动支持" in prompt


@step("11. role_judge 辅助函数")
def test_role_judge():
    rj = imp("planner.role_judge")
    prompt = rj._build_judge_prompt(
        persona="温柔", today_str="2026-05-25", weekday="周一",
        current_activities=[{"time": "09:00", "name": "早餐"}],
        description="下午两点一起学习",
    )
    assert "当前日程" in prompt and "09:00 早餐" in prompt and "decision" in prompt
    parsed = rj._parse_json_loose('{"decision":"today","title":"学习"}')
    assert parsed["decision"] == "today"
    parsed2 = rj._parse_json_loose('foo {"decision":"future","raw_date":"明天"} bar')
    assert parsed2["decision"] == "future"
    assert rj._infer_future_date("明天", "2026-05-25") == "2026-05-26"


@step("12. get_current_activity_snapshot API 返回结构")
def test_api_snapshot():
    gm_mod = imp("planner.goal_manager")
    gm = gm_mod.GoalManager(data_dir=str(Path(tempfile.mkdtemp())))
    gm_mod._goal_manager = gm
    now_min = datetime.now().hour * 60 + datetime.now().minute
    gm.create_goal(
        name="晚餐", goal_type="meal",
        description="晚饭在食堂二楼吃了木桶饭，加了个香煎里脊和荷包蛋，吃得超级满足。",
        creator_id="system", chat_id="global", priority="high",
        parameters={"time_window": [max(0, now_min - 15), min(1440, now_min + 30)]},
    )
    inj_mod = imp("services.inject_service")
    svc = inj_mod.InjectService(mock_plugin())
    snap = asyncio.run(svc.get_current_activity_snapshot("global"))
    assert snap["has_activity"] is True
    assert snap["activity"]["name"] == "晚餐"
    assert snap["activity"]["goal_type"] == "meal"
    assert "晚饭" in snap["activity"]["description"]  # 完整描述未截断
    assert "-" in snap["activity"]["time_window"]
    assert snap["timezone"] == "Asia/Shanghai"
    assert snap["as_of"]


# step 13（v4.1~v4.3 的 replyer 注入 6 场景）已删除 —— v4.3.1 hotfix
# 移除了 ``inject_into_replyer_extra_prompt`` 与对应 HookHandler，
# 主程序 ``maisaka.replyer.before_request`` hook 不存在，replyer 路径已废弃。
# v4.4 已恢复（主程序 commit 478256f2 补上了 hook），重新覆盖。


@step("13. replyer 注入 6 场景（v4.4 恢复）")
def test_replyer_inject():
    gm_mod = imp("planner.goal_manager")
    gm = gm_mod.GoalManager(data_dir=str(Path(tempfile.mkdtemp())))
    gm_mod._goal_manager = gm
    now_min = datetime.now().hour * 60 + datetime.now().minute
    gm.create_goal(
        name="晚餐", goal_type="meal", description="木桶饭",
        creator_id="system", chat_id="global", priority="high",
        parameters={"time_window": [max(0, now_min - 15), min(1440, now_min + 30)]},
    )
    inj_mod = imp("services.inject_service")

    async def run():
        plugin = mock_plugin()
        svc = inj_mod.InjectService(plugin)

        # 1) 正常注入
        r1 = await svc.inject_into_replyer_extra_prompt(session_id="s1", attempt=1)
        assert r1.get("modified_kwargs", {}).get("extra_prompt"), "正常场景应注入"
        assert "晚餐" in r1["modified_kwargs"]["extra_prompt"]
        assert "不要主动提及" in r1["modified_kwargs"]["extra_prompt"]

        # 2) attempt=2 重试跳过
        r2 = await svc.inject_into_replyer_extra_prompt(session_id="s1", attempt=2)
        assert "modified_kwargs" not in r2

        # 3) 冷却命中（再次 attempt=1）
        r3 = await svc.inject_into_replyer_extra_prompt(session_id="s1", attempt=1)
        assert "action" in r3  # 冷却命中或注入都可，不崩溃即可

        # 4) 关闭开关
        plugin.config.schedule.inject_into_replyer = False
        r4 = await svc.inject_into_replyer_extra_prompt(session_id="s_new", attempt=1)
        assert "modified_kwargs" not in r4

        # 5) 白名单过滤
        plugin.config.schedule.inject_into_replyer = True
        plugin.config.schedule.allowed_streams = ["session:only-me"]
        r5 = await svc.inject_into_replyer_extra_prompt(session_id="s_outsider", attempt=1)
        assert "modified_kwargs" not in r5

        # 6) 无活动
        plugin.config.schedule.allowed_streams = []
        for g in gm.get_all_goals(chat_id="global"):
            gm.delete_goal(g.goal_id)
        svc._schedule_cache.clear()
        r6 = await svc.inject_into_replyer_extra_prompt(session_id="s_empty", attempt=1)
        assert "modified_kwargs" not in r6

    asyncio.run(run())


# ============================================================
# Run all
# ============================================================


@step("14. 多天日程对比（load_recent_schedule_summary）")
def test_recent_schedule_summary():
    from datetime import timedelta as _td
    gm_mod = imp("planner.goal_manager")
    ctx_mod = imp("planner.generator.context_loader")
    tz_mod = imp("utils.timezone_manager")

    gm = gm_mod.GoalManager(data_dir=str(Path(tempfile.mkdtemp())))
    tz = tz_mod.TimezoneManager("Asia/Shanghai")
    now = tz.get_now()

    # 造 3 天历史：昨/前/大前
    for offset, acts in enumerate([
        [("写专栏", 14 * 60), ("审稿", 8 * 60)],
        [("回邮件", 8 * 60), ("整理藏书", 14 * 60)],
        [("审稿", 8 * 60), ("写专栏", 14 * 60)],
    ], start=1):
        day = now - _td(days=offset)
        for name, start_min in acts:
            g = gm.create_goal(
                name=name, description=f"{name}的描述，延续昨天的主线状态", goal_type="study",
                creator_id="system", chat_id="global", priority="medium",
                parameters={"time_window": [start_min, start_min + 120]},
            )
            gm.db.update_goal(g.goal_id, created_at=day)

    loader = ctx_mod.ScheduleContextLoader(gm, tz)

    # days=1 只看昨天
    s1 = loader.load_recent_schedule_summary(days=1)
    assert "审稿" in s1 and "写专栏" in s1
    assert "延续昨天的主线状态" in s1
    assert "回邮件" not in s1, "days=1 不应看到前天"
    assert s1.index("08:00 审稿") < s1.index("14:00 写专栏"), "昨日日程应按时间正序输出"

    # days=3 看 3 天
    s3 = loader.load_recent_schedule_summary(days=3)
    assert "审稿" in s3 and "回邮件" in s3 and "整理藏书" in s3
    # 应该出现 3 个【MM-DD】日期块
    assert s3.count("【") == 3, f"应该有 3 个日期块，实际 {s3.count('【')}"

    # 向后兼容：load_yesterday_schedule_summary 等价于 days=1
    s_yest = loader.load_yesterday_schedule_summary()
    # 不严格相等（动态日期 / 内容一致即可），只要看到昨天的活动
    assert "审稿" in s_yest


@step("15. ScheduleAutoScheduler 构造 + start/stop（强类型 config 访问）")
def test_auto_scheduler():
    sched_mod = imp("planner.auto_scheduler")
    inst = fresh_plugin()
    s = sched_mod.ScheduleAutoScheduler(inst)
    s._inferred_prompt_cache = {
        "target_date": "2026-06-18",
        "prompt": "明天在卡帕多奇亚醒来，第二天去乘坐热气球，次日晚上看星空，翌日收尾。",
    }

    effective = s._get_effective_custom_prompt("2026-06-18", "固定日程")
    assert "明天" not in effective and "第二天" not in effective
    assert "次日" not in effective and "翌日" not in effective
    assert "今天在卡帕多奇亚醒来" in effective
    assert "当天去乘坐热气球" in effective
    assert "当天晚上看星空" in effective
    assert "当天收尾" in effective
    assert s._get_effective_custom_prompt("2026-06-19", "固定日程") == "固定日程"

    async def run():
        scheduler = sched_mod.ScheduleAutoScheduler(inst)
        assert scheduler.tz_manager.timezone_str == inst.config.schedule.timezone
        # 强制启用以走完 start 分支（验证 plugin.config.schedule.xxx 全部可访问）
        inst.config.schedule.auto_schedule_enabled = True
        await scheduler.start()
        assert scheduler.is_running is True
        await scheduler.stop()
        assert scheduler.is_running is False

    asyncio.run(run())


@step("16. 模拟旅行连续性生成链路（6/18 旅行 → 6/19 承接）")
def test_schedule_continuity_simulation():
    """临时 DB + fake LLM，验证普通每日生成主链路会吃到旅行连续上下文。"""
    from datetime import timedelta as _td

    gm_mod = imp("planner.goal_manager")
    sg_mod = imp("planner.schedule_generator")

    frozen_now = datetime(2026, 6, 19, 8, 0)

    class FrozenTimezoneManager:
        def __init__(self, timezone_str: str = "Asia/Shanghai"):
            self.timezone_str = timezone_str

        def get_now(self):
            return frozen_now

    class FakeLLM:
        def __init__(self):
            self.prompts: list[str] = []

        async def generate(self, *, prompt, model, max_tokens, temperature):
            del model, max_tokens, temperature
            self.prompts.append(prompt)
            required = [
                "今天是2026-06-19 周五",
                "【连续性要求】",
                "不要无理由回到默认学习、游戏、上班日常",
                "06:40 乘坐热气球",
                "卡帕多奇亚",
                "先从最近一天摘要提取当前地点",
            ]
            missing = [text for text in required if text not in prompt]
            if missing:
                return {"success": False, "response": f"missing prompt evidence: {missing!r}"}
            response = {
                "schedule_items": [
                    {"name": "睡觉", "description": "在卡帕多奇亚洞穴酒店继续睡到清晨", "goal_type": "daily_routine", "priority": "high", "time_slot": "00:00", "duration_hours": 7},
                    {"name": "起床洗漱", "description": "洗漱收拾后准备继续逛格雷梅", "goal_type": "daily_routine", "priority": "medium", "time_slot": "07:00", "duration_hours": 0.5},
                    {"name": "早餐", "description": "吃洞穴酒店早餐，翻昨天热气球照片", "goal_type": "meal", "priority": "high", "time_slot": "07:30", "duration_hours": 0.5},
                    {"name": "露天博物馆参观", "description": "去格雷梅露天博物馆看洞穴教堂壁画", "goal_type": "custom", "priority": "high", "time_slot": "08:00", "duration_hours": 2.5},
                    {"name": "洞穴教堂慢逛", "description": "细看壁画，把昨天奇岩地貌接到历史线", "goal_type": "learn_topic", "priority": "medium", "time_slot": "10:30", "duration_hours": 1},
                    {"name": "午餐", "description": "吃当地风味午餐，顺便让腿缓一缓", "goal_type": "meal", "priority": "high", "time_slot": "11:30", "duration_hours": 1},
                    {"name": "午休", "description": "回住处短躺，避免下午徒步直接掉线", "goal_type": "daily_routine", "priority": "medium", "time_slot": "12:30", "duration_hours": 0.5},
                    {"name": "红谷轻徒步", "description": "去红谷看岩层颜色，承接昨天峡谷散步", "goal_type": "exercise", "priority": "medium", "time_slot": "13:00", "duration_hours": 2},
                    {"name": "小镇买纪念品", "description": "在格雷梅挑纪念品，看看陶艺和地毯店", "goal_type": "custom", "priority": "medium", "time_slot": "15:00", "duration_hours": 1},
                    {"name": "咖啡馆整理照片", "description": "整理热气球、星空和洞穴教堂照片", "goal_type": "custom", "priority": "medium", "time_slot": "16:00", "duration_hours": 1.5},
                    {"name": "晚餐", "description": "晚餐吃当地热乎菜，给第二天旅程补能量", "goal_type": "meal", "priority": "high", "time_slot": "17:30", "duration_hours": 1},
                    {"name": "观景台看日落", "description": "去观景台看奇岩日落，延续昨晚星空收束感", "goal_type": "custom", "priority": "medium", "time_slot": "18:30", "duration_hours": 1.5},
                    {"name": "夜聊", "description": "和朋友聊洞穴教堂、红谷徒步和热气球照片", "goal_type": "social_maintenance", "priority": "medium", "time_slot": "20:00", "duration_hours": 1.5},
                    {"name": "整理行李路线", "description": "整理行李和明天路线，别又手忙脚乱", "goal_type": "custom", "priority": "medium", "time_slot": "21:30", "duration_hours": 1},
                    {"name": "睡前准备", "description": "洗漱后早点躺下，给后续旅程留体力", "goal_type": "daily_routine", "priority": "medium", "time_slot": "22:30", "duration_hours": 1.5},
                ]
            }
            return {"success": True, "response": json.dumps(response, ensure_ascii=False)}

    def create_yesterday_cappadocia_history(gm):
        yesterday = frozen_now - _td(days=1)
        activities = [
            ("睡觉", "在卡帕多奇亚的洞穴酒店里睡到清晨，醒来前还带着旅行的疲惫感", "daily_routine", 0, 330),
            ("起床洗漱", "清晨醒来洗漱收拾，准备赶热气球前的早饭和集合", "daily_routine", 330, 360),
            ("当地特色早餐", "吃一份卡帕多奇亚当地早餐，喝热茶，先把状态拉起来", "meal", 360, 400),
            ("乘坐热气球", "坐热气球升空，看卡帕多奇亚奇岩地貌和清晨的光线铺开", "custom", 400, 510),
            ("地貌观景与拍照", "落地后在观景点慢慢看独特地貌，顺手整理几张照片", "custom", 510, 570),
            ("附近小镇漫步", "在格雷梅附近慢慢走走，看看洞穴建筑和当地街巷氛围", "custom", 570, 690),
            ("午餐", "午餐尝试当地风味，找个安静地方坐下来缓一缓", "meal", 690, 750),
            ("午休", "午饭后回住处短暂休息，给下午的体验活动留点精神", "daily_routine", 750, 810),
            ("学习当地手工技艺", "下午体验当地陶艺或手工技艺，边学边吐槽自己手笨", "learn_topic", 810, 930),
            ("峡谷散步", "去玫瑰谷或鸽子谷附近散步，看傍晚光线里的岩层颜色", "exercise", 930, 1050),
            ("当地特色晚餐", "晚餐品尝陶罐炖肉之类的当地美食，认真补充能量", "meal", 1050, 1110),
            ("日落后放空", "饭后找个视野不错的地方坐一会儿，整理今天的照片和见闻", "custom", 1110, 1200),
            ("欣赏星空", "晚上找开阔位置看星空，把这趟卡帕多奇亚的一天收束起来", "custom", 1200, 1290),
            ("夜聊", "和朋友聊聊热气球、当地美食和今天看到的奇特地貌", "social_maintenance", 1290, 1350),
            ("睡前准备", "洗漱收拾，准备早点睡，给明天的旅程留体力", "daily_routine", 1350, 1440),
        ]
        for name, description, goal_type, start_min, end_min in activities:
            goal = gm.create_goal(
                name=name, description=description, goal_type=goal_type,
                creator_id="system", chat_id="global", priority="medium",
                parameters={"time_window": [start_min, end_min]},
            )
            gm.db.update_goal(goal.goal_id, created_at=yesterday)

    async def run():
        gm = gm_mod.GoalManager(data_dir=str(Path(tempfile.mkdtemp())))
        create_yesterday_cappadocia_history(gm)

        fake_llm = FakeLLM()
        plugin = MagicMock()
        plugin.ctx.llm = fake_llm
        plugin._plugin_root = None

        config = {
            "use_multi_round": False,
            "min_activities": 8,
            "max_activities": 15,
            "enable_detailed_description": True,
            "min_description_length": 10,
            "max_description_length": 80,
            "max_tokens": 8192,
            "custom_prompt": "",
            "timezone": "Asia/Shanghai",
            "llm_task_name": "replyer",
            "recent_schedule_days": 3,
            "history_message_limit": 0,
            "knowledge_search_limit": 0,
            "llm_log_enabled": False,
            "bot_profile": {
                "personality": "旅行中的技术宅兽耳少女",
                "reply_style": "短句嘴欠但靠谱",
                "interest": "动漫、音乐、骑行和游戏",
                "bot_name": "哈基米",
            },
        }

        with patch(f"{PKG_NAME}.planner.goal_manager.TimezoneManager", FrozenTimezoneManager), \
             patch(f"{PKG_NAME}.planner.schedule_generator.TimezoneManager", FrozenTimezoneManager), \
             patch(f"{PKG_NAME}.planner.generator.base_generator.TimezoneManager", FrozenTimezoneManager):
            generator = sg_mod.ScheduleGenerator(gm, config, plugin=plugin)
            schedule = await generator.generate_daily_schedule(
                user_id="system",
                chat_id="global",
                use_llm=True,
                use_multi_round=False,
            )

        assert len(schedule.items) == 15
        generated_text = "\n".join([item.name for item in schedule.items] + [item.description for item in schedule.items])
        assert "学习线性代数" not in generated_text and "玩游戏" not in generated_text and "看动漫" not in generated_text
        for term in ("露天博物馆", "洞穴教堂", "红谷", "热气球"):
            assert term in generated_text, f"生成结果缺少旅行连续项: {term}"

        captured_prompt = fake_llm.prompts[0]
        assert "【连续性要求】" in captured_prompt
        assert "06:40 乘坐热气球" in captured_prompt
        assert "卡帕多奇亚" in captured_prompt

    asyncio.run(run())


@step("17. energy_model 时段能量基线")
def test_energy_model():
    em = imp("utils.energy_model")
    # 时段能量曲线（极值）
    assert em.get_energy_level(2) <= 20, "凌晨 2 点应为低能量"
    assert em.get_energy_level(11) >= 80, "上午 11 点应为高能量"
    assert em.get_energy_level(23) <= 35, "深夜 23 点应为低能量"
    # 描述映射（5 档）
    assert em.describe_energy(95) == "精神满满"
    assert em.describe_energy(70) == "状态不错"
    assert em.describe_energy(50) == "正常"
    assert em.describe_energy(30) == "有点累"
    assert em.describe_energy(15) == "困了"
    assert em.describe_energy(5) == "快撑不住"
    # 时段标签
    assert em.get_time_period(2) == "凌晨"
    assert em.get_time_period(15) == "下午"
    # 越界 clamp
    assert em.get_energy_level(-1) == em.get_energy_level(0)
    assert em.get_energy_level(99) == em.get_energy_level(23)


@step("18. InjectOptimizer 主动碎碎念配额 + 间隔 + 概率")
def test_proactive_inject():
    inj_mod = imp("handlers.inject.inject_optimizer")
    # 把概率拉到 1，去掉随机性；配额 2、间隔 0 秒
    opt = inj_mod.InjectOptimizer(
        cache_ttl=300,
        casual_inject_probability=0.5,
        proactive_daily_quota=2,
        proactive_probability=1.0,
        proactive_gap_seconds=0,
    )
    # 无活动 → 拒绝
    ok, reason = opt.should_proactive_inject("scope1", None)
    assert not ok and "无当前活动" in reason

    # 第 1 次：通过
    ok, _ = opt.should_proactive_inject("scope1", "晚餐")
    assert ok
    opt.record_proactive_inject("scope1")
    # 第 2 次：通过
    ok, _ = opt.should_proactive_inject("scope1", "晚餐")
    assert ok
    opt.record_proactive_inject("scope1")
    # 第 3 次：配额已满
    ok, reason = opt.should_proactive_inject("scope1", "晚餐")
    assert not ok and "配额" in reason

    # 概率为 0 时永远不触发
    opt2 = inj_mod.InjectOptimizer(
        cache_ttl=300, casual_inject_probability=0.5,
        proactive_daily_quota=10, proactive_probability=0.0, proactive_gap_seconds=0,
    )
    ok, reason = opt2.should_proactive_inject("scope2", "晚餐")
    assert not ok and "概率" in reason

    # 间隔限制：刚 record 完，下一次因间隔被拒
    opt3 = inj_mod.InjectOptimizer(
        cache_ttl=300, casual_inject_probability=0.5,
        proactive_daily_quota=10, proactive_probability=1.0, proactive_gap_seconds=3600,
    )
    opt3.should_proactive_inject("scope3", "晚餐")  # 第一次允许
    opt3.record_proactive_inject("scope3")
    ok, reason = opt3.should_proactive_inject("scope3", "晚餐")
    assert not ok and "间隔" in reason


@step("19. 注入文本 v4.3 增强（state_hint + 精神状态 + 主动碎碎念语气切换）")
def test_v43_inject_enhancements():
    gm_mod = imp("planner.goal_manager")
    gm = gm_mod.GoalManager(data_dir=str(Path(tempfile.mkdtemp())))
    gm_mod._goal_manager = gm
    now_min = datetime.now().hour * 60 + datetime.now().minute
    # 用 study 类型让 state_analyzer 出 study 情绪词
    gm.create_goal(
        name="写专栏", goal_type="study",
        description="正在赶稿子",
        creator_id="system", chat_id="global", priority="high",
        parameters={"time_window": [max(0, now_min - 30), min(1440, now_min + 60)]},
    )
    inj_mod = imp("services.inject_service")
    plugin = mock_plugin()
    # v4.3 起 state_analyzer 由 enable_state_analysis 控制，要打开
    plugin.config.inject.enable_state_analysis = True
    svc = inj_mod.InjectService(plugin)

    # 校验：QUERY_CURRENT 文本包含 "精神状态：" + 描述行
    UserIntent = imp("handlers.inject.intent_classifier").UserIntent
    txt_q = svc._render_unified_prompt(
        intent=UserIntent.QUERY_CURRENT,
        current_activity="写专栏",
        current_description="正在赶稿子",
        future_activities=[],
        remaining_minutes=None,
        state_hint="学得还挺认真",
        proactive_hit=False,
        context_continue_inject=False,
        context_reason=None,
    )
    assert "精神状态：" in txt_q
    assert "学得还挺认真" in txt_q, "state_hint 应嵌到当前活动行"

    # 校验：CASUAL_CHAT + proactive_hit=True → 用"自然带出"提示
    txt_p = svc._render_unified_prompt(
        intent=UserIntent.CASUAL_CHAT,
        current_activity="写专栏",
        current_description=None,
        future_activities=[],
        remaining_minutes=None,
        state_hint=None,
        proactive_hit=True,
        context_continue_inject=False,
        context_reason=None,
    )
    assert "自然带出" in txt_p, "命中主动碎碎念应改提示词"

    # 校验：CASUAL_CHAT + proactive_hit=False → 走"不相关请忽略"
    txt_n = svc._render_unified_prompt(
        intent=UserIntent.CASUAL_CHAT,
        current_activity="写专栏",
        current_description=None,
        future_activities=[],
        remaining_minutes=None,
        state_hint=None,
        proactive_hit=False,
        context_continue_inject=False,
        context_reason=None,
    )
    assert "完全忽略" in txt_n, "未命中碎碎念应走默认提示"

    # v4.4 恢复：校验 replyer extra_prompt 也包含 "精神：" + 活动名
    async def run_replyer():
        r = await svc.inject_into_replyer_extra_prompt(session_id="s_v43", attempt=1)
        extra = r.get("modified_kwargs", {}).get("extra_prompt", "")
        assert "精神：" in extra, "replyer 应注入能量描述"
        assert "写专栏" in extra
    asyncio.run(run_replyer())


@step("20. _extract_last_user_text 跳过主程序元数据消息（v4.3.2/v4.4.1/v4.4.2 hotfix）")
def test_extract_last_user_text_skip_time_prefix():
    inj_mod = imp("services.inject_service")
    extract = inj_mod.InjectService._extract_last_user_text

    # 场景 1：单条时间戳消息 → 跳过后无可用消息，返回空
    msgs = [
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "当前时间：2026-05-25 11:00:00"},
    ]
    assert extract(msgs) == "", "纯时间戳消息应该返回空（让 intent 走兜底）"

    # 场景 2：真实问题 + 时间戳追加 → 跳过时间戳，返回真实问题
    msgs2 = [
        {"role": "user", "content": "在干嘛"},
        {"role": "assistant", "content": "在写代码"},
        {"role": "user", "content": "怎么修这个 bug"},
        {"role": "user", "content": "当前时间：2026-05-25 11:00:00"},
    ]
    assert extract(msgs2) == "怎么修这个 bug", f"应跳过时间戳取真实问题，实际：{extract(msgs2)!r}"

    # 场景 3：list 形式 content + 时间戳 part → 跳过该 part
    msgs3 = [
        {"role": "user", "content": [
            {"text": "当前时间：2026-05-25 11:00:00"},
        ]},
        {"role": "user", "content": "明天有什么计划"},
    ]
    # reversed 先看最后一条（真实问题）应该直接返回，不会动到时间戳
    assert extract(msgs3) == "明天有什么计划"

    # 场景 4：真实问题包含"当前时间"字样但不是纯时间戳 → 不应被误删
    msgs4 = [
        {"role": "user", "content": "当前时间不重要，告诉我安排"},
        {"role": "user", "content": "当前时间：2026-05-25 11:00:00"},
    ]
    assert extract(msgs4) == "当前时间不重要，告诉我安排"

    # 场景 5：v4.4.1 ——【人物画像-内部参考】块也要跳过
    profile_block = (
        "【人物画像-内部参考】\n"
        "以下内容仅供内部推理，不要向用户逐字复述。\n\n"
        "- 神秘靓仔（person_id=xxx）：喜欢吃辣，编程很厉害\n\n"
        "使用时把它当作对当前人物的背景理解；若与当前对话冲突，以当前对话为准。"
    )
    msgs5 = [
        {"role": "user", "content": "在干嘛？"},
        {"role": "user", "content": profile_block},
        {"role": "user", "content": "当前时间：2026-05-25 11:00:00"},
    ]
    assert extract(msgs5) == "在干嘛？", (
        f"应跳过人物画像+时间戳取真实问题，实际：{extract(msgs5)!r}"
    )

    # 场景 6：v4.4.1 ——真实用户消息以【】开头不应被误删（如群里有人玩梗）
    msgs6 = [
        {"role": "user", "content": "【吐槽】今天天气好烂"},
        {"role": "user", "content": "当前时间：2026-05-25 11:00:00"},
    ]
    assert extract(msgs6) == "【吐槽】今天天气好烂", (
        f"非内部参考的【】消息不应被误删，实际：{extract(msgs6)!r}"
    )

    # 场景 7：v4.4.2 ——<system-reminder> deferred tool 提示也要跳过
    system_reminder_block = (
        "<system-reminder>\n"
        "以下工具当前未直接暴露给你，但可以通过 tool_search 工具发现并在后续轮次中使用：\n"
        "1. send_email\n"
        "2. fetch_weather\n\n"
        "如需其中某个工具，请先调用 tool_search。tool_search 只负责发现工具，不直接执行业务。\n"
        "</system-reminder>"
    )
    msgs7 = [
        {"role": "user", "content": "在干嘛？"},
        {"role": "user", "content": system_reminder_block},
        {"role": "user", "content": "【人物画像-内部参考】\n以下内容仅供内部推理"},
        {"role": "user", "content": "当前时间：2026-05-25 11:00:00"},
    ]
    assert extract(msgs7) == "在干嘛？", (
        f"应跳过 system-reminder + 人物画像 + 时间戳取真实问题，实际：{extract(msgs7)!r}"
    )

    # 场景 8：v4.4.2 ——真实用户消息以 < 开头不应被误删
    msgs8 = [
        {"role": "user", "content": "<3 这个表情怎么打"},
        {"role": "user", "content": "当前时间：2026-05-25 11:00:00"},
    ]
    assert extract(msgs8) == "<3 这个表情怎么打", (
        f"非 system-reminder 的 < 开头消息不应被误删，实际：{extract(msgs8)!r}"
    )

    # 场景 9：v4.4.3 ——剥除 build_planner_prefix 的 <message msg_id="..." time="..." user="..."> 包装
    planner_wrapped = (
        '<message msg_id="317284630" time="19:05:24" user="神秘靓仔">\n'
        "你现在在干嘛？"
    )
    msgs9 = [
        {"role": "user", "content": planner_wrapped},
        {"role": "user", "content": "【人物画像-内部参考】\n以下内容仅供内部推理"},
        {"role": "user", "content": "当前时间：2026-05-25 11:00:00"},
    ]
    extracted = extract(msgs9)
    assert "<message" not in extracted, f"应剥除 <message ...> 前缀，实际：{extracted!r}"
    assert "你现在在干嘛？" in extracted, (
        f"应保留真实问题，实际：{extracted!r}"
    )

    # 场景 10：v4.4.3 ——带 quote/group_card 属性的更复杂前缀也要剥
    complex_wrapped = (
        '<message msg_id="abc" time="20:00:00" user="李四" group_card="老李" quote="def,ghi">\n'
        "明天下午一起开会？"
    )
    msgs10 = [{"role": "user", "content": complex_wrapped}]
    e10 = extract(msgs10)
    assert e10.strip() == "明天下午一起开会？", f"复杂前缀应被剥除，实际：{e10!r}"

    # 场景 11：v4.4.3 ——剥除后内容为空时跳过这条
    msgs11 = [
        {"role": "user", "content": "真实问题"},
        {"role": "user", "content": '<message msg_id="x" time="y" user="z">\n'},
    ]
    assert extract(msgs11) == "真实问题", "前缀+空内容应跳过该条"

    # 场景 12：用真实"现在在干嘛"问题过 IntentClassifier，应判为 QUERY_CURRENT
    UserIntent = imp("handlers.inject.intent_classifier").UserIntent
    classifier = imp("handlers.inject.intent_classifier").IntentClassifier()
    intent_q, conf_q = classifier.classify("你现在在干嘛？")
    assert intent_q == UserIntent.QUERY_CURRENT, f"'你现在在干嘛？'应判为 query_current，实际 {intent_q}"

    # 场景 13：技术问题过分类器，应判为 TECH_QUESTION
    intent_t, conf_t = classifier.classify("怎么配置数据库连接")
    assert intent_t == UserIntent.TECH_QUESTION, f"'怎么配置数据库连接'应判为 tech_question，实际 {intent_t}"


@step("21. ProactiveService 主动发起 + 频率调控 + 多格式 stream 解析（v4.4 / v4.4.1）")
def test_proactive_service():
    from unittest.mock import AsyncMock

    proactive_mod = imp("services.proactive_service")
    gm_mod = imp("planner.goal_manager")
    gm = gm_mod.GoalManager(data_dir=str(Path(tempfile.mkdtemp())))
    gm_mod._goal_manager = gm
    # 造一个当前时刻刚开始（≤5 分钟）的活动
    now_min = datetime.now().hour * 60 + datetime.now().minute
    gm.create_goal(
        name="写专栏", goal_type="study", description="赶稿",
        creator_id="system", chat_id="global", priority="high",
        parameters={"time_window": [now_min, min(1440, now_min + 60)]},
    )

    async def run():
        # 1) proactive_streams 为空 → 完全跳过（默认安全行为）
        plugin = mock_plugin()
        plugin.ctx = MagicMock()
        plugin.ctx.maisaka.trigger_proactive = AsyncMock(return_value={"success": True})
        plugin.ctx.frequency.set_adjust = AsyncMock(return_value={"success": True})
        svc = proactive_mod.ProactiveService(plugin)
        await svc._check_and_act()
        plugin.ctx.maisaka.trigger_proactive.assert_not_awaited()
        plugin.ctx.frequency.set_adjust.assert_not_awaited()

        # 2) session:<id> 格式 → 解析为 <id>，双开关全开 → 触发两个调用
        plugin.config.schedule.proactive_streams = ["session:test"]
        plugin.config.schedule.enable_proactive_trigger = True
        plugin.config.schedule.enable_frequency_modulation = True
        svc2 = proactive_mod.ProactiveService(plugin)
        await svc2._check_and_act()
        # v4.4.1：session: 前缀被剥除，传给主程序的应该是裸 session_id "test"
        plugin.ctx.frequency.set_adjust.assert_awaited_with("test", 0.3)
        # 主动发起：intent 含 study 模板的关键短语
        plugin.ctx.maisaka.trigger_proactive.assert_awaited()
        call_args = plugin.ctx.maisaka.trigger_proactive.call_args
        assert call_args.kwargs.get("stream_id") == "test"
        assert "写专栏" in call_args.kwargs.get("intent", "")

        # 3) 同活动同天再次 _check_and_act 不应重复触发主动发起
        plugin.ctx.maisaka.trigger_proactive.reset_mock()
        await svc2._check_and_act()
        plugin.ctx.maisaka.trigger_proactive.assert_not_awaited()  # 配额已用

        # 4) 同 stream + 同 factor 不应重复 set_adjust
        plugin.ctx.frequency.set_adjust.reset_mock()
        await svc2._check_and_act()
        plugin.ctx.frequency.set_adjust.assert_not_awaited()  # 因子未变

        # 5) 关掉频率调控开关后，set_adjust 不再调用
        plugin.config.schedule.enable_frequency_modulation = False
        svc3 = proactive_mod.ProactiveService(plugin)
        await svc3._check_and_act()
        plugin.ctx.frequency.set_adjust.assert_not_awaited()

        # ============ v4.4.1 新增：多格式 stream 解析 ============

        # 6) qq:group:<gid> → 调 ctx.chat.get_stream_by_group_id 解析为 session_id
        # 注意：v4.4.4 起按 SDK 解包后契约 mock —— 直接返回 stream dict（不是 {"success":True,"stream":...}）
        plugin2 = mock_plugin()
        plugin2.ctx = MagicMock()
        plugin2.ctx.maisaka.trigger_proactive = AsyncMock(return_value={"success": True})
        plugin2.ctx.frequency.set_adjust = AsyncMock(return_value={"success": True})
        plugin2.ctx.chat.get_stream_by_group_id = AsyncMock(return_value={
            "session_id": "real_session_for_group_123456",
            "platform": "qq",
            "group_id": "123456",
        })
        plugin2.config.schedule.proactive_streams = ["qq:group:123456"]
        plugin2.config.schedule.enable_proactive_trigger = True
        plugin2.config.schedule.enable_frequency_modulation = True
        svc6 = proactive_mod.ProactiveService(plugin2)
        await svc6._check_and_act()
        plugin2.ctx.chat.get_stream_by_group_id.assert_awaited_with("123456", "qq")
        plugin2.ctx.frequency.set_adjust.assert_awaited_with("real_session_for_group_123456", 0.3)
        proactive_call = plugin2.ctx.maisaka.trigger_proactive.call_args
        assert proactive_call.kwargs.get("stream_id") == "real_session_for_group_123456"

        # 7) qq:private:<uid> → 调 ctx.chat.get_stream_by_user_id 解析
        plugin3 = mock_plugin()
        plugin3.ctx = MagicMock()
        plugin3.ctx.maisaka.trigger_proactive = AsyncMock(return_value={"success": True})
        plugin3.ctx.frequency.set_adjust = AsyncMock(return_value={"success": True})
        plugin3.ctx.chat.get_stream_by_user_id = AsyncMock(return_value={
            "session_id": "real_session_for_user_789",
            "platform": "qq",
            "user_id": "789",
        })
        plugin3.config.schedule.proactive_streams = ["qq:private:789"]
        plugin3.config.schedule.enable_proactive_trigger = True
        plugin3.config.schedule.enable_frequency_modulation = True
        svc7 = proactive_mod.ProactiveService(plugin3)
        await svc7._check_and_act()
        plugin3.ctx.chat.get_stream_by_user_id.assert_awaited_with("789", "qq")
        plugin3.ctx.frequency.set_adjust.assert_awaited_with("real_session_for_user_789", 0.3)

        # 8) 解析失败（主程序找不到对应聊天流）→ stream 为 None 被跳过
        plugin4 = mock_plugin()
        plugin4.ctx = MagicMock()
        plugin4.ctx.maisaka.trigger_proactive = AsyncMock(return_value={"success": True})
        plugin4.ctx.frequency.set_adjust = AsyncMock(return_value={"success": True})
        # SDK 解包后：找不到对应聊天流时直接返回 None
        plugin4.ctx.chat.get_stream_by_group_id = AsyncMock(return_value=None)
        plugin4.config.schedule.proactive_streams = ["qq:group:999999"]
        plugin4.config.schedule.enable_proactive_trigger = True
        plugin4.config.schedule.enable_frequency_modulation = True
        svc8 = proactive_mod.ProactiveService(plugin4)
        await svc8._check_and_act()
        # 解析失败 → 不调用 frequency / proactive
        plugin4.ctx.frequency.set_adjust.assert_not_awaited()
        plugin4.ctx.maisaka.trigger_proactive.assert_not_awaited()

        # 9) v4.4.4 新增：失败时返回 {"success": False, "error": "..."} 也要正确识别
        plugin5 = mock_plugin()
        plugin5.ctx = MagicMock()
        plugin5.ctx.maisaka.trigger_proactive = AsyncMock(return_value={"success": True})
        plugin5.ctx.frequency.set_adjust = AsyncMock(return_value={"success": True})
        # 模拟主程序拒绝（capability_denied 等失败）
        plugin5.ctx.chat.get_stream_by_user_id = AsyncMock(return_value={
            "success": False, "error": "fake denied",
        })
        plugin5.config.schedule.proactive_streams = ["qq:private:000000"]
        plugin5.config.schedule.enable_proactive_trigger = True
        plugin5.config.schedule.enable_frequency_modulation = True
        svc9 = proactive_mod.ProactiveService(plugin5)
        await svc9._check_and_act()
        plugin5.ctx.frequency.set_adjust.assert_not_awaited()
        plugin5.ctx.maisaka.trigger_proactive.assert_not_awaited()

    asyncio.run(run())


def main() -> int:
    print(f"\n{'=' * 60}")
    print("自主规划插件 v4 完整冒烟测试")
    print(f"{'=' * 60}\n")

    test_pkg_import()
    test_components()
    test_ui_schema()
    test_migration()
    test_current_toml()
    test_stream_filter()
    test_llm_logger()
    test_pending_commitments()
    test_timezone()
    test_prompt_builder()
    test_role_judge()
    test_api_snapshot()
    test_replyer_inject()
    test_recent_schedule_summary()
    test_auto_scheduler()
    test_schedule_continuity_simulation()
    test_energy_model()
    test_proactive_inject()
    test_v43_inject_enhancements()
    test_extract_last_user_text_skip_time_prefix()
    test_proactive_service()

    print(f"\n{'=' * 60}")
    print(f"通过: {len(_PASS)} / 失败: {len(_FAIL)}")
    if _FAIL:
        print("\n失败项:")
        for name, msg in _FAIL:
            print(f"  - {name}\n      {msg}")
        return 1
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
