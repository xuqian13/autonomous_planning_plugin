# 麦麦自主规划插件

让 AI Bot 拥有自己的生活日程，自动生成符合人设的每日活动安排。

## 核心功能

- **智能日程生成** - 基于 Bot 人设通过 LLM 自动生成每日、每周、每月计划
- **定时自动生成** - 每天指定时间自动生成新日程，无需手动操作
- **自然对话融入** - 在对话中自然提到当前在做什么（如"这会儿正在吃午饭"）
- **多种展示格式** - 支持详细文字和精美图片两种日程查看方式
- **自动维护** - 定期清理过期日程，保持数据整洁

## 快速开始

### 1. 自动生成日程

对 Bot 说：
```
"帮我生成今天的日程"
```

Bot 会根据自己的人设（学生/打工人等）自动创建一整天的活动安排。

### 2. 查看日程

```bash
/plan status   # 详细文字格式（含活动描述）
/plan list     # 精美图片格式
```

**示例输出：**
```
📅 今日日程 2025-01-15 周三
共 8 项活动

1. ⏰ 07:30-08:30  🏠 起床洗漱
   📝 新的一天开始，洗漱整理，准备迎接美好的一天

2. ⏰ 08:30-09:00  🍽️ 吃早饭
   📝 享用营养丰富的早餐，补充能量

3. ⏰ 09:00-11:00  📚 上课
   📝 认真听讲，做好笔记，积极参与课堂讨论
...
```

### 3. 管理日程

```bash
/plan clear              # 清理旧日程
/plan delete <ID或序号>   # 删除指定日程
/plan help               # 查看帮助
```

## 配置说明

配置文件：`config.toml`

```toml
[plugin]
enabled = true  # 是否启用插件

[autonomous_planning]
cleanup_interval = 3600          # 清理检查间隔（秒）
cleanup_old_goals_days = 30      # 保留历史天数

[autonomous_planning.schedule]
inject_schedule = true           # 对话中自然提到当前活动
auto_generate = true             # 首次询问时自动生成日程
use_multi_round = true           # 启用多轮优化生成
max_rounds = 2                   # 最多尝试轮数
quality_threshold = 0.85         # 质量分数阈值

# 定时自动生成配置
auto_schedule_enabled = true     # 是否启用定时自动生成日程
auto_schedule_time = "00:30"     # 每天自动生成日程的时间（HH:MM格式）
timezone = "Asia/Shanghai"       # 时区设置
```

**日程注入效果：**
- 用户："在干嘛？"
- Bot："这会儿正吃午饭呢"

## 命令列表

| 命令 | 说明 |
|------|------|
| `/plan status` | 查看今日详细日程（文字） |
| `/plan list` | 查看今日日程（图片） |
| `/plan clear` | 清理昨天及更早的日程 |
| `/plan delete <ID>` | 删除指定日程 |
| `/plan help` | 显示帮助 |

## 工作原理

### 日程生成流程

```
方式1: 用户请求 → 检查是否已有日程 → LLM 基于人设生成 → 批量保存为目标 → 注入到对话
方式2: 定时触发 → 每天00:30自动执行 → LLM 基于人设生成 → 批量保存为目标 → 静默运行
```

### 定时自动生成

- **默认时间**：每天凌晨 00:30（可配置）
- **智能跳过**：如果当天已有日程，则自动跳过
- **静默运行**：完全后台执行，不打扰用户
- **时区支持**：支持配置时区（默认 Asia/Shanghai）
- **启动延迟**：插件启动10秒后自动开始定时任务

### LLM 生成机制

1. 读取 Bot 人设、兴趣、回复风格
2. 生成 15-20 个活动，覆盖全天
3. 每个活动包含：名称、描述（40-60字）、时间、类型、优先级
4. 多轮优化：质量不达标时附带反馈重新生成

### 数据管理

- **存储位置**：`data/goals.json`
- **清理策略**：自动清理 30 天前的已完成/已取消目标
- **原子保存**：使用文件锁和临时文件防止数据损坏

## 文件结构

```
autonomous_planning_plugin/
├── README.md                        # 本文档
├── plugin.py                        # 插件主文件（Tools、Commands、Event Handlers）
├── _manifest.json                   # 插件清单
├── config.toml                      # 配置文件
├── planner/                         # 核心规划模块
│   ├── goal_manager.py              # 目标管理器（创建、更新、删除目标）
│   ├── schedule_generator.py        # 日程生成器（LLM生成、验证、应用）
│   └── auto_scheduler.py            # 自动调度器（定时生成日程）
├── utils/                           # 工具模块
│   ├── schedule_image_generator.py  # 日程图片生成器
│   └── time_utils.py                # 时间窗口解析工具
├── assets/                          # 图片资源（bird.jpg, winter_char.jpg）
└── data/                            # 数据存储
    ├── goals.json                   # 日程数据
    └── images/                      # 生成的图片
```

## 常见问题

**Q: 如何修改已生成的日程？**
A: 使用 `/plan delete` 删除后重新生成，或对 Bot 说"重新生成今天的日程"

**Q: 为什么看不到日程？**
A: 运行 `/plan status` 检查是否已生成，或尝试手动生成

**Q: 如何禁用日程注入？**
A: 在 `config.toml` 中设置 `inject_schedule = false`

**Q: 如何修改定时生成的时间？**
A: 在 `config.toml` 中修改 `auto_schedule_time` 配置项，格式为 "HH:MM"

**Q: 如何禁用定时自动生成？**
A: 在 `config.toml` 中设置 `auto_schedule_enabled = false`

**Q: 定时生成会重复创建日程吗？**
A: 不会，调度器会先检查当天是否已有日程，如果有则自动跳过

**Q: 支持哪些活动类型？**
A: daily_routine（作息）、meal（吃饭）、study（学习）、entertainment（娱乐）、social_maintenance（社交）、exercise（运动）、learn_topic（兴趣学习）、rest（休息）、free_time（自由时间）、custom（自定义）

## 开发说明

### 主要组件

**Tools（供 LLM 调用）：**
- `ManageGoalTool` - 创建、查看、更新、删除目标
- `GetPlanningStatusTool` - 查看规划系统状态
- `GenerateScheduleTool` - 生成日程
- `ApplyScheduleTool` - 应用日程

**Event Handlers：**
- `AutonomousPlannerEventHandler` - 后台清理过期目标
- `ScheduleInjectEventHandler` - 在 LLM 调用前注入当前日程信息

**Commands：**
- `PlanningCommand` - 处理 `/plan` 命令

### 性能优化

- **批量保存**：创建多个目标时只保存一次，减少 I/O
- **延迟保存**：修改后延迟 1 秒保存，合并多个操作
- **LRU 缓存**：日程查询结果缓存 5 分钟，减少重复计算
- **图片缓存**：字体和图片资源缓存，避免重复加载

## 版本信息

**当前版本：** 2.1.0

**更新日志：**
- v2.1.0 - 精简代码注释，优化文档结构
- v2.0.0 - 代码重构，消除重复代码
- v1.5.0 - 新增 clear 命令，优化日程显示
- v1.0.0 - 初始版本发布

## 许可证

MIT License

---

**让 AI Bot 拥有真实的生活日程！** 🎉
