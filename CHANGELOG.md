# Changelog

## 0.1.0 (2026-11-01)

首个版本。对标 `dsh-hoi4-modding` v1.2.0 的结构，但按 EU4 的失败模式
（静默不生效）重新安排了重心：**静态校验 + error.log 分诊**。

### Agent preset（模式下拉框）

- `lib/preset.js` —— 插件启动时调 `ctx.agentPresets.register()` 把 **「EU4 mod mode」**
  注册进模式下拉框
  - ★ 关键发现：宿主**不扫** `~/.dsh/.agent-presets/` 目录，必须由插件主动声明
    （依据 `dsh-purge` 注释原文：「Web 0.1.7+ 模式下拉只认 registry.register()」）。
    我先按目录扫描推断过一次，重启后下拉框里没有它 —— 推断被证伪。
  - 自带 YAML 子集解析器（块序列 / 标量 / 嵌套映射 / 块文本 `|`），
    遇 `!!js` 等不支持写法**直接抛错**，不静默降级；`lenientTags: true` 仅用于结构对照
  - 与宿主注册接口的契约：`{ id, name, description, order, plugins }`

- `presets/eu4-modding/agent.cordis.yml` —— 该模式的插件组合
  - 三处**必须是** `cordis:group` + `isolate`：`planning`（planMode）、
    `compaction` + `tool-result-pruner`（compaction / toolResultPruner）。
    写成扁平行会让服务落到 root realm 撞名，整份 preset 被标 broken
    —— 实测踩了三次，每次宿主都明确报出缺什么
  - 刻意与 redteam 不同的取舍：不带 anchor-gate、不开 workflow/ralph、
    `subagent` 收为 `maxDepth: 1`、`skill-filesystem` 保持 `includeDefaultRoots: true`
    （红队为控上下文设为 false；eu4 相反，怕漏掉用户放在 `.agents/skills` 的技能）

### 解析地基

- `pdx_parser.py` —— PDX 脚本容错解析器
  - 实测覆盖 vanilla **2630 个 .txt / 1,469,283 行 / 0 崩溃 / 0 error**
  - `walk_paths()` 提供上下文路径；`walk_full()` 额外提供祖先块（判定同名双义容器必需）
  - BOM / GB18030 / CP1252 一律 `warning`，绝不 `error`（vanilla 自身有 683 个非 UTF-8 文件）
- `loc_parser.py` —— `.yml` 本地化独立解析
  - 支持 **值后跟行尾注释**、**值内未转义引号**（实测 3307 行，德语占 88%）
  - `.yml` 缺 BOM 判 `error`（vanilla 578 个 .yml 全部带 BOM）
  - 实测 578 文件 / 519,508 key / 0 error

### 白名单

- `build_reference.py` —— 三源交叉：cwtools 配置定分类、vanilla 语料给用法、数据文件补 scripted 名与枚举
  - `treasury`：effects **无** / triggers **有**（复现真实踩过的坑）
  - 覆盖率 **99.64%**（分母 = 语料侧出现的名字），缺口全部归因到真实 cwt 缺口
  - `_meta.json` 带 `counts.by_source` / `overlap` / `source_split` / `cross_check` / `game_version` / `generated_at`
    - 原 `union_check` **改名**为 `source_split`：其判定（三源并集 == total）由定义恒真，不是交叉校验，原名会误导读者
    - 新增 `cross_check`（effects/triggers）：`total - corpus` 应等于 `(cwt ∪ vanilla_data) - corpus`，**可被外部重算**
- `cwt_reader.py` —— 只收 `type[on_action]` 块内的 subtype（全局扫会多收 54 个）
- `container_exclusivity.py` —— 发现「只在一种容器内出现」的候选，**结果需人工确认**

### 静态校验

- `eu4_lint.py` —— 八类：语法/编码、事件、**跨文件引用**、本地化、修正、变量、旗标、名字位
  - 名字位判定**按容器类型**：`modifier` 含 `factor` 才是权重块（内部触发器位）
  - `CONTROL_FIELDS` 排除 `potential`/`scale`/`force_potential`/MTTH 单位
  - mod 自定义 `scripted_effect`/`trigger` 不报（避免满屏假错）
  - 成品模组实测：**error 0 / warn 0 / info 80**
  - `--list-categories` 列出每个检查函数的类别归属；`--only` 帮助里写明归属

- **任务树「定义 vs 引用」检查**（新类别 `crossrefs`）
  - `complete_mission` / `required_missions` 引用 vs `missions/` 定义
  - 实测 vanilla `complete_mission` 分布：**decisions 33 / missions 11 / events 7**（合计 51）
    → 因此**不归 events**：归进去会让 `--only events` 漏掉三分之二的引用点
  - 消息措辞如实：「无法确认任务 ID 是否存在（本工具未索引 vanilla 任务）」
  - **环检测**：自环 + 多节点环（A→B→A、A→B→C→A）；
    只在本 mod 定义的任务间建图，外部 ID 视为叶子 —— 避免把 vanilla 的未知依赖算成环（假错）

### 日志与工具链

- `errorlog_triage.py` —— 默认扫全部 `error*.log`（EU4 会轮转）
  - 三种行数口径同时输出：物理行 / 非空行 / 日志条目（判定基准）
  - 归属只做确定性判断：文件名 → namespace → 前缀推导 → unknown
  - 实测真实日志 **95.8% 噪声**，19 个工坊 mod 可归属
- `mod_digest.py` —— 可复现快照哈希，spec 写死，`--spec`/`--print-digest`/`--verify`
- `backup.py` —— 备份/恢复，**用同一 digest 校验往返**，恢复前自动备份当前
- `new_mod.py` —— 脚手架，namespace 强制去数字，**生成即过 lint（error 0 / warn 0 / info 0）**
- `install_mod.py` —— 安装/卸载，写 `dlc_load.json` 前备份，中文目录名转 ASCII，卸载不留孤儿

### 打包

- `package.json` / `cordis.patch.yml` / `lib/index.js` / `icon.svg`
- `files` 白名单排除 `reference/`、`__pycache__`、`*.pyc`、`tests/`
- `.gitignore` 同步排除
- `tests/smoke.test.mjs` 校验打包契约（含「`reference/` 不得进包」断言）

### 测试

- Python：**137 个单测**（解析器 / 本地化 / 白名单 / lint / 任务树 / 日志 / 工具链 / 守卫）
- Node：**9 个冒烟测试**
- `tools/preflight.py` —— 一条命令跑完 15 项发布前检查
- fixtures 覆盖：4 类故意错的脚本、干净样本、自定义脚本效果、重复 on_action、
  任务树（正常链 / 自环 / 多节点环 / 重复定义 / 外部引用）、
  error.log 三类噪声混排（vanilla + 本 mod + 别的 mod）

### 守卫（每条都能反向验证）

- `tools/check_no_assets.py` —— 禁止游戏素材（12 种扩展名），`--selftest` 塞假 `.dds` 必须失败
- `tools/check_no_reference.py` —— `reference/` 不得进包；防退化断言「`references/` 不能被误判」
- `tools/check_doc_links.py` —— SKILL.md §3 索引无死链；`--selftest` 缺章必须失败

### 已知边界

- `.cwt` 只读，不写；上游配置滞后于游戏版本是常态，`corpus_only` 一律 `warn`
- `build_reference.py --min-coverage PCT` 让覆盖率下降 fail-loud（默认不启用）
- 任务 ID 检查**未索引 vanilla 任务**，只能报「无法确认」，不能断言「不存在」
- 不检查 `trigger` 条件可达性、任务长链完整闭包
