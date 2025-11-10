# 麦麦自主规划插件

让麦麦拥有自己的生活日程，自动生成个性化的日常活动安排。

## 功能特性

- 🎯 **自动生成每日日程** - 使用 LLM 生成符合麦麦人格的日程（起床、吃饭、学习、娱乐等）
- 🔄 **自动管理** - 每天早上 6 点后自动生成新日程，自动清理过期记录
- 💬 **日程注入** - 麦麦会在对话中自然提到当前活动，例如"这会儿正吃饭呢"
- 📅 **便捷查看** - 通过命令快速查看和管理日程

## 快速开始

### 生成日程

对麦麦说：**"帮我生成今天的每日计划"**

或者等待自动生成（每天早上 6 点后）

### 查看日程

```bash
/plan status   # 简洁版（仅显示时间和活动）
/plan list     # 详细版（包含完整描述）
```

**示例输出：**
```
📅 今日日程

07:30-08:30 🏠 起床
08:30-09:30 🍽️ 早饭
10:00-11:00 📚 看番
12:30-13:30 🍽️ 午饭
...
```

### 删除日程

```bash
/plan delete 3        # 删除第3个日程项
/plan delete abc-123  # 删除指定ID的日程
```

## 配置说明

配置文件：`config.toml`

### 核心配置

```toml
[plugin]
enabled = true  # 启用插件

[autonomous_planning]
interval = 3600  # 检查间隔（秒）
max_actions_per_cycle = 0  # 0表示只生成日程不自动执行
```

### 自动生成配置

```toml
[autonomous_planning.auto_schedule]
auto_generate_daily = true  # 是否自动生成每日计划
daily_trigger_time = "06:00"  # 每天几点触发生成
cleanup_old_goals_days = 30  # 保留多少天的历史记录
```

### 日程注入配置（重要）

```toml
[autonomous_planning.schedule]
# 是否在对话时自动提到当前活动
inject_schedule = true  # 推荐开启
```

### 个性化偏好

```toml
[autonomous_planning.schedule.preferences]
# 作息时间
wake_up_time = "07:30"
sleep_time = "23:30"

# 三餐时间
breakfast_time = "08:00"
lunch_time = "12:00"
dinner_time = "18:00"

# 学习安排
has_classes = true
class_time_morning = "09:00"
study_time = "21:00"

# 娱乐爱好
entertainment_time = "19:00"
favorite_activities = ["刷贴吧", "看动漫", "玩游戏"]

# 兴趣学习
learning_topics = ["Python编程", "游戏开发", "动漫文化"]

# 其他
social_active = true
exercise_occasionally = true
```

## 工作原理

### 日程生成流程

```
触发生成（手动/自动）
    ↓
检查是否需要生成（避免重复）
    ↓
调用 LLM 生成个性化日程
    ↓
批量保存（性能优化）
    ↓
注入到对话系统
```

### 日程注入效果

当 `inject_schedule = true` 时，麦麦在回复时会自然提到当前活动：

- 用户："在干嘛？"
- 麦麦："这会儿正吃午饭（食堂随便吃点），等下要去上课"

## 文件结构

```
autonomous_planning_plugin/
├── README.md              # 本文档
├── config.toml            # 配置文件
├── plugin.py              # 插件主文件
├── planner/               # 核心模块
│   ├── goal_manager.py           # 目标管理
│   ├── schedule_generator.py     # 日程生成
│   └── auto_schedule_manager.py  # 自动调度
├── actions/
│   └── schedule_action.py        # 日程执行
└── data/
    ├── goals.json                # 当前日程数据
    └── schedule_history.json     # 历史记录
```

## 常见问题

**Q: 如何修改日程内容？**
A: 编辑 `config.toml` 中的 `preferences` 部分，然后对麦麦说"重新生成今日计划"

**Q: 日程会自动更新吗？**
A: 会。每天早上 6 点后，插件会自动删除旧日程并生成新的

**Q: 如何禁用自动生成？**
A: 在 `config.toml` 中设置 `auto_generate_daily = false`

**Q: 为什么麦麦不提到当前活动？**
A: 检查 `inject_schedule` 是否为 `true`，并确保已生成日程

## 命令列表

| 命令 | 说明 |
|------|------|
| `/plan status` | 查看简洁日程 |
| `/plan list` | 查看详细日程 |
| `/plan delete <序号>` | 删除指定日程 |
| `/plan help` | 查看帮助 |

## 版本信息

**当前版本：** 2.1.0

**主要特性：**
- ✅ LLM 个性化日程生成
- ✅ 自动日程管理
- ✅ 日程注入到对话
- ✅ 批量操作性能优化
- ✅ 人格风格适配

---

**享受麦麦的个性化日程！** 🎉
