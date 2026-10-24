# 日程响应对象契约修复核验

## 结论与边界

截至 2026-10-24 本次核验，官方主分支与 QR-0W 的 6 个公开分支均未修复此问题。官方最新提交已经包含在本地 `dfdf1a2801db201e0543e436375e9873cbe23510` 中，无需通过回退或整分支覆盖获取更新。

修复仅保存在本隔离副本。未写入运行插件目录或 Host，未加载插件入口，未读取实际配置或数据库，未请求 LLM，未生成、应用或删除真实日程。回归采用合成对象与数组响应复现；本次没有读取真实运行日志，因此不推断模型变更或「今天才引入代码回归」。

## 官方提交与源文件证据

[GitHub API 主分支快照](https://api.github.com/repos/xuqian13/autonomous_planning_plugin/commits/main) 返回 `0f3877785157370c88ca8ff5be8abfdf8a3f0130`，时间为 `2026-09-14T07:05:06Z`。该提交合并 [PR #16](https://github.com/xuqian13/autonomous_planning_plugin/pull/16)，内容为 MaiBot 1.x 的 host 兼容范围和版本声明调整。

在本隔离仓库执行下列命令，退出码为 `0`，证实官方固定提交是本地 HEAD 的祖先：

```sh
git merge-base --is-ancestor \
  0f3877785157370c88ca8ff5be8abfdf8a3f0130 \
  dfdf1a2801db201e0543e436375e9873cbe23510
```

从 GitHub 按官方固定 SHA 下载以下三个源文件，逐字节与隔离仓库同 SHA 的 git 对象比对，全部相同：

| 官方固定源码 | Git blob SHA | 现有问题 |
| --- | --- | --- |
| [response_parser.py](https://github.com/xuqian13/autonomous_planning_plugin/blob/0f3877785157370c88ca8ff5be8abfdf8a3f0130/planner/generator/response_parser.py#L223-L239) | `2c11f1e3fd310b1b3ba76f0fd7ef3ec39fc196b7` | 未验证根类型，缺字段分支对 `data` 调用 `keys()` |
| [schema_builder.py](https://github.com/xuqian13/autonomous_planning_plugin/blob/0f3877785157370c88ca8ff5be8abfdf8a3f0130/planner/generator/schema_builder.py#L81-L90) | `82298f63c6f39187550be8f6f6f6ae36623f41f3` | 正式契约为根 `object`，必需 `schedule_items` 数组字段 |
| [prompt_builder.py](https://github.com/xuqian13/autonomous_planning_plugin/blob/0f3877785157370c88ca8ff5be8abfdf8a3f0130/planner/generator/prompt_builder.py#L307-L320) | `12d39ae3d873e5bfad1fa876376a60479ef17c11` | `Schema:` 只序列化 `schedule_items` 子 Schema，呈现根 `array` |

本地 HEAD 与官方提交相比，[响应解析器](<planner/generator/response_parser.py>)和 [Schema 构建器](<planner/generator/schema_builder.py>)在修复前完全相同。本地[提示词构建器](<planner/generator/prompt_builder.py>)已有连续性、旅行与生活阶段改动，但保留同一处子 Schema 输出。官方更新无法消除此矛盾。

## Fork 分支核验

使用 [QR-0W 分支 API](https://api.github.com/repos/QR-0W/autonomous_planning_plugin/branches?per_page=100) 获取全部 6 个公开分支，对每个返回的固定 SHA 在线读取上述三个源文件。全部保留 `data.keys()`、缺少根对象检查、只输出数组子 Schema，并要求对象根。

| 分支 | 核验 SHA | 响应解析器 blob 前缀 | 提示词 blob 前缀 |
| --- | --- | --- | --- |
| `backup/identity-affection-20261015-205012` | `dfdf1a2801db201e0543e436375e9873cbe23510` | `2c11f1e3` | `814d4501` |
| `feat/infer-next-day-schedule` | `fe46c2880f438190ca52d1731eb11da8aaeb2778` | `085e3a1a` | `e4f5d078` |
| `fix/next-day-schedule-update` | `dfdf1a2801db201e0543e436375e9873cbe23510` | `2c11f1e3` | `814d4501` |
| `fix/schedule-continuity-clean` | `b07c0da581887fd2055e363261d8de3469eae920` | `2c11f1e3` | `b8b60a4b` |
| `fix/sdk-2.8-task-name` | `15717aab26a1b45e14c61ccfd22ed31cc8243703` | `2c11f1e3` | `12d39ae3` |
| `main` | `93b122a67505268d1d3626eb60af24b0e33e4c10` | `085e3a1a` | `e4f5d078` |

隔离副本的 `origin/feat/infer-next-day-schedule` 为旧的 `04158e6…`，与线上分支头不同。此分支结论使用 API 返回的 `fe46c288…` 在线源码，不采用过期本地 ref。

[旧解析器固定源码](https://github.com/QR-0W/autonomous_planning_plugin/blob/fe46c2880f438190ca52d1731eb11da8aaeb2778/planner/generator/response_parser.py)与[旧提示词固定源码](https://github.com/QR-0W/autonomous_planning_plugin/blob/fe46c2880f438190ca52d1731eb11da8aaeb2778/planner/generator/prompt_builder.py)也包含同一问题；未引入整 fork。

## 最小实现

- [prompt_builder.py:312–334](<planner/generator/prompt_builder.py#L312-L334>)：明确根节点必须为对象，禁止裸数组；序列化完整根 Schema。首次生成与重试共用同一构建方法，无 Schema 时仍声明对象根。
- [response_parser.py:203–257](<planner/generator/response_parser.py#L203-L257>)：JSON 解析后验证对象根；直接调用字段提取方法时也先检查根类型；保留数组字段检查，并验证数组项为对象。非法响应抛出明确的 `LLMInvalidResponseError`，不将裸数组自动包装为对象。
- [test_response_contract.py](<tests/test_response_contract.py>)：新增标准库 `unittest` 回归。使用假的包 namespace，加载隔离副本中的确切模块，不执行任何包初始化文件或插件入口。

保留原有 Markdown 清理、控制字符清理和有效对象提取行为。空的 `schedule_items` 数组仍可提取，最小活动数继续由现有后续校验负责。

[插件入口](<plugin.py>)、[Hook 注入服务](<services/inject_service.py>)、[Hook 契约测试](<tests/test_hook_contract.py>)、[连续性模块](<planner/generator/continuity_state.py>)、[Schema 构建器](<planner/generator/schema_builder.py>)、[基础生成器](<planner/generator/base_generator.py>)及[日程生成器](<planner/schedule_generator.py>)均与 `dfdf1a2` 的原始字节一致。未重跑会加载 SDK 插件入口的旧测试。

## 离线验证

在本隔离副本根目录执行：

```sh
python3 -I -B tests/test_response_contract.py
git diff --check
```

测试不需要安装第三方依赖。`-I` 隔离 Python 环境，`-B` 禁止写入字节码。

同一测试在修复前运行 14 项，产生 17 个子测试断言失败和 9 个子测试异常，退出码 `1`，其中明确复现 `AttributeError: 'list' object has no attribute 'keys'`。详见[修复前日志](<tests/response-contract-before.log#L48-L70>)。

修复后 14 项全部通过，退出码 `0`；`git diff --check` 退出码 `0`。覆盖对象成功、Markdown、控制字符清理、裸数组和其他非对象根拒绝、缺字段、字段非数组、元素非对象、空数组、非法 JSON、初始及重试完整 Schema、描述开关和连续性保留。详见[修复后日志](<tests/response-contract-after.log#L38-L47>)。

额外以 Python audit hook 包围同一测试，禁止 socket、子进程、SQLite 连接、所有文件写入，以及插件入口、配置文件和数据库文件读取。14 项全部通过，退出码 `0`，详见[隔离审计日志](<tests/response-contract-isolation.log>)。日志重定向由 shell 在隔离目录中完成。隔离副本仍没有运行数据目录或实际插件配置。
