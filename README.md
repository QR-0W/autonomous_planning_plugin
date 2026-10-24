# 麦麦自主规划插件 v4

> 让麦麦具备自主规划能力的 MaiBot 插件，通过 LLM 智能生成符合人设的日常生活日程，像真实生活着的人 —— 有自己的日程、有当下在做的事、回复时贴合生活节奏。

---

## 这个插件做什么

- 🌅 **有作息**：每天 LLM 自动生成 8-15 个活动，从睡觉、三餐到学习娱乐覆盖全天
- 🧠 **知道现在在干嘛**：planner 决策和 replyer 回复都能感知"当前活动"
- 🗣️ **回复贴合状态**：晚上 18 点你问"忙吗"，她会自然带出"刚吃完饭"
- 📅 **能记下未来的事**：你说"周六一起打游戏"，那天的日程会自动加上这件事
- ⚖️ **能拒绝离谱请求**：LLM 扮演角色判断 today / future / reject，不是无脑接受
- 🌙 **懂跨天活动**：23:00 开始的睡眠，凌晨 1 点注入也能识别"还在睡"

---

## 装上看到的第一件事

启动 MaiBot，日志里会冒出来：

```
插件 xuqian13.autonomous-planning-plugin-v4 v4.4.3 加载成功
[v4] bot_profile 已预拉取: {'personality': '...', 'reply_style': '...', 'bot_name': '...'}
✅ 智能注入组件已加载 (intent=True, optimizer=True, context=3/600s)
🧹 麦麦目标清理循环已启动
📅 自动调度循环已启动
🌟 活动驱动主动行为循环已启动（v4.4）
日程定时生成已启动 - 执行时间: 00:30
```

打开 WebUI 插件配置页，看到 4 个 section：

| Section | 干啥用 |
|---|---|
| 插件 | 开关、版本 |
| 后台清理 | 多久清一次过期目标和日志 |
| 日程管理 | 注入开关、生成参数、定时、白名单、时区、LLM 日志…（主战场） |
| 智能注入策略 | 注入模式、意图分类、冷却 |

每个字段都有中文 label + 提示。

---

## 三个典型对话

### 📌 让她生成今天的日程

```
你：帮我生成今天的日程
麦麦：[generate_schedule_v4 → LLM] 生成了 12 个活动，已经记下了
你：/plan status
麦麦：
  📅 今日日程 2026-05-25 周一  共 12 项活动
  1. ⏰ 00:00-07:00  🏠 睡觉
     📝 在温暖的被窝里睡个好觉…
  2. ⏰ 07:00-07:45  🏠 起床洗漱
     📝 闹钟一响就爬起来…
  ...
```

### 📌 自然贴合当前状态

```
（18:00 晚餐时段）
你：你忙吗？
麦麦：嗯，刚好吃完，要做点什么吗？
              ↑ 没说"我正在吃饭"，因为 prompt 写了"不要主动提及"
              只在你问起时自然带出来
```

### 📌 未来约定

```
（周三）
你：周六我们一起去吃章鱼烧
麦麦：嗯，周六记下了
       ↑ update_schedule_v4 → 角色裁判 → decision=future
       → 写入 goals 表 pending_commitment

（周六 00:30）
[自动调度] 拉到今日 pending → 生成时融入 →
  14:00 和朋友吃章鱼烧
[完成后 consume → goal status=completed]
```

---

## 用户命令

| 命令 | 作用 |
|---|---|
| `/plan` 或 `/规划` | 显示帮助 |
| `/plan status` | 今日日程（文字详细） |
| `/plan list` | 今日日程（图片） |
| `/plan regenerate [额外要求]` | 重新生成今日日程（先删今天再重生，可附加临时要求） |
| `/plan delete <id或序号>` | 删除指定目标 |
| `/plan clear [days]` | 清理旧日程，默认仅保留今天 |

`admin_users` 留空 = 所有人可用；
`allowed_streams` 留空 = 所有群可用。

---

## 给其他插件用

如果你也在写插件、想知道"麦麦现在在做什么"，调一下：

```python
snapshot = await self.ctx.api.call(
    "xuqian13.autonomous-planning-plugin-v4.get_current_activity",
)

if snapshot["has_activity"]:
    print(snapshot["activity"]["name"])         # "晚餐"
    print(snapshot["activity"]["time_window"])  # "17:30-18:15"
    print(snapshot["next_activities"][:1])      # [{"time": "18:15", "name": "操场散步"}]
```

完整返回：

```json
{
  "has_activity": true,
  "activity": {
    "name": "晚餐",
    "description": "晚饭在食堂二楼吃了木桶饭…",
    "goal_type": "meal",
    "time_window": "17:30-18:15"
  },
  "next_activities": [{"time": "18:15", "name": "操场散步"}],
  "as_of": "2026-05-25T18:00:00+08:00",
  "timezone": "Asia/Shanghai"
}
```

没有当前活动时 `has_activity=false`，`activity=null`。

---

## 安装

1. 把整个目录放到 `MaiBot/plugins/xuqian13_autonomous-planning-plugin-v4/`
2. 确认主程序 `config/model_config.toml` 里 `[model_task_config.planner]` 段已配好（MaiBot 默认就带这段，无需新建），`model_list` 至少一个能用的模型：

   ```toml
   [model_task_config.planner]   # 规划模型配置
   model_list = [
       "你的模型名",
   ]
   max_tokens = 2048
   temperature = 0.3
   ```

   想换别的任务名，把插件 `config.toml` 的 `[schedule] llm_task_name = "..."` 改成对应的任务名即可（例如 `replyer` / `utils` 等主程序已配置的任务）。
3. 启动 MaiBot

完事。`data/goals.db` 会在第一次启动时自动建。

---

## 出问题了？

| 现象 | 看这里 |
|---|---|
| 日程定时生成"未启用" | `auto_schedule_enabled = true` 改了没 |
| LLM 生成日程一直失败 | 看 `data/llm_logs/fail_schedule_generation_*.txt`，里面有完整 prompt 和响应 |
| 麦麦回复时不提当前活动 | 正常 —— prompt 写了"不要主动提及"；只在用户问起 / 强相关时才带出来 |
| 麦麦每天日程都差不多 | 提高 inject 的 temperature（0.85）+ 看 prompt 里的"原则" |

---

## 跑冒烟测试

不动主程序也能验证代码层面没回归：

```powershell
cd "F:\下载\Maibot 插件开发\MaiM-with-u\MaiBot"
.\.venv\Scripts\python.exe plugins\xuqian13_autonomous-planning-plugin-v4\tests\run_smoke.py
```

13 项全过 ✅。

---

## 项目结构（一眼看完）

```
plugin.py             ← 入口 + 7 个组件装饰器 + v4.0→4.1 配置迁移
config_models.py      ← 4 个顶层 PluginConfigBase（plugin / autonomous_planning / schedule / inject）
config.toml           ← 默认配置

services/             ← 4 个 service：tools / command / inject / cleanup
planner/              ← goal_manager + schedule_generator + auto_scheduler + role_judge + generator/
handlers/inject/      ← 智能注入子模块：意图分类、状态分析、注入优化器、上下文缓存
utils/                ← 时区 / 时间 / 流过滤 / LLM 日志 / 图片生成
cache/                ← 线程安全 LRU
database/             ← SQLite 数据访问
core/                 ← 数据模型 / 异常 / 常量
tests/run_smoke.py    ← 13 项端到端冒烟

data/                 ← 运行时（goals.db / llm_logs/）
```

---

## License

AGPL-3.0
