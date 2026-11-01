---
name: eu4-modding
description: 《欧陆风云4》(Europa Universalis IV, EU4) 1.37.5.0 Mod 开发全流程技能：读游戏文件、新建 Mod、写事件/决议/任务树/理念/政府改革/地图/修正/on_actions、本地化与中文化、静态校验、安装启用、按 error.log 排错、发布创意工坊。Use whenever the user asks to create, edit, fix, validate, install, publish, or debug an EU4 mod or asks about EU4 scripting (scope, effects, triggers, variables, events, decisions, missions, ideas, reforms, map, localisation), even if they only say "做个事件"/"改地图"/"mod 没生效".
whenToUse: 用户要做/改/修/校验/安装/发布 EU4 Mod，或问 EU4 脚本写法（scope/effect/trigger/变量/事件/决议/任务/理念/改革/地图/本地化）时。
---

# EU4 1.37.5.0 Mod 制作全流程

面向 **v1.37.5.0** 实测整理。目标不是讲概念，而是**一次把能运行的 Mod 做出来**：
放对文件 → 写对语法 → 本地化齐全 → 校验通过 → 装进游戏 → error.log 干净。

> 本技能包**不含任何游戏素材**，也不含 `reference/`（那是 vanilla 用法统计，属于游戏内容衍生数据）。
> 首次运行 `build_reference.py` 会从**你自己机器上的游戏安装**生成，见 §4。

## 0. 开始前先确认 3 件事

1. **游戏路径**：默认自动探测 `steamapps/common/Europa Universalis IV`；用 `EU4_GAME` 覆盖。
2. **玩家目录**：`Documents/Paradox Interactive/Europa Universalis IV`（`mod/`、`logs/`、`dlc_load.json` 在此）；
   Linux Proton 必须显式设 `EU4_USER_DIR`。
3. **目标是「新增文件」还是「覆盖原版文件」** —— 两者语义完全不同，见铁律 2。

## 1. 铁律（违反任何一条都会踩坑）

### 1.0 核心原则：名字位由【容器类型】决定，绝不由【名字形态】推断 ★

**这一条比后面所有条目都重要，因为它是所有具体检查的生成器。**

先确定「这一层是效果位 / 触发器位 / 参数位 / 旗标位」，再判断「这个名字在该位是否合法」。
任何「看到 `xxx` 觉得像效果」的推断都是 bug 来源。

这不是巧合，是 Clausewitz 脚本的结构性特征：**同一个标识符在不同容器里可以是完全不同的东西**。

本项目在实测中连续踩到 9 次同一个母题，全部记在下面这张表里：

| # | 标识符 | 看起来是 | 实际是 | 判据（怎么才能确定） |
|---|---|---|---|---|
| 1 | `treasury` | 效果（能写数字） | **触发器** | 出现在 `limit = {}` / `trigger = {}` / `OR = {}` 内；cwt `alias[trigger:treasury]`；语料 100% 在触发器位 |
| 2 | `limit` / `ai_chance` | 效果名 | **容器名**（不是名字位） | 它是语境容器本身，其直接子项才是名字位 |
| 3 | `who` / `type` / `estate` | 效果名 | **参数字段** | 父 key 不是语境容器 → 当前项是参数 |
| 4 | `trigger_switch` 的 case key | 效果名 | **旗标名** | 父容器是 case 容器 → 值是数据 |
| 5 | `on_battle_won_country_effect` | on_action 钩子 | **scripted_effect** | 在 `common/scripted_effects/` 而非 `common/on_actions/` |
| 6 | `modifier` | 修正定义块 | **或 AI 权重块**（内部是触发器位） | **看块内是否含 `factor`**，不能看块名 |
| 7 | `force_potential` / `scale` / `potential` | 触发器 | **权重块的控制字段** | 只在权重容器内出现（容器独占度），且语义是控制而非判断 |
| 8 | `prestige` | 修正 | **同时也是触发器** | `prestige = 50` 是条件，`add_prestige = 50` 才是效果 |
| 9 | `zz_epi_chain1` | 合法 event id | **非法**（namespace 不能含数字） | vanilla 364 个 namespace 无一含数字 |

换成 HOI4 / CK3 / VIC3，这个陷阱一字不改地存在。

#### 1.0.1 判定的三层收窄（单独任何一层都会出错）

对 `modifier` 这类**同名双义容器**，判定必须层层收窄：

```
第 1 层  块名不足以判断        →  「modifier 就是修正定义」是错的
第 2 层  看块内容（factor）     →  含 factor 才是权重块，内部是触发器位
第 3 层  再看键的容器独占度     →  只在权重块内出现的，可能是控制字段而非触发器
```

三层的来历：第 1 层是按块名推断造成的错；第 2 层修正了它；
第 3 层又发现第 2 层还不够（权重块里还有 `potential` / `scale` / `force_potential`
这类控制字段，它们形态与触发器完全一致）。**只做其中任何一层都会漏或误。**

三层落地的实现：

| 层 | 代码位置 | 手段 |
|---|---|---|
| 1 | `build_reference.name_scope()` | 按 path 逐层看祖先 key |
| 2 | `build_reference._block_is_weight()` | `block.get("factor") is not None` |
| 3 | `scripts/container_exclusivity.py` | 扫「名字 × 容器类型」矩阵找独占项 → **人工确认**后写进 `CONTROL_FIELDS` |

第 3 层**只做发现，不做裁决**。实测 58 个独占名字里只有 6 个是真控制字段，
其余 52 个是真触发器（`num_of_territories` / `dominant_culture` / `num_of_missionaries`…），
只是 vanilla 里恰好只被 AI 权重块用到。若按「独占 → 自动排除」执行，会把 52 个真触发器
从白名单删掉，**只能造成漏判的假阴性**。

#### 1.0.2 引擎原文索引（症状 ↔ error.log 原文）

上表是「名字位」的坑；下表是**它们在 error.log 里留下什么原文**。
写模组时对不上号，就按这张表直接搜日志。

| 症状 | 引擎原文（error.log） | 详解位置 |
|---|---|---|
| 效果位写了触发器（如 `treasury = -50`） | `[persistent.cpp:88]: Parsing Error. File: "events/xxx.txt", Error: Unknown effect type. Key: treasury` | `references/errorlog.md` §3 |
| namespace 含数字 | `[eventmanager.cpp:434]: Incorrect namespace event id: zz_epi_chain1.` | `references/deep-dive.md` §一 |
| 任务 ID 引用错 | `[effectimplementation.cpp:26715]: Script error! Script Object Token: "complete_mission", Error: Unknown ...` | `references/errorlog.md` §3 |
| 事件缺 title/desc/option | `[eventmanager.cpp:166]: Event #41 (personal_byz_rd.41) is missing a title in events/...` | `references/errorlog.md` §4 |
| 省份没归入区域 | `[colonialregion.cpp:63]: Adding province double N to colonial region xxx` | `references/map-editing.md` §四 |

**这五条的共同点**：日志里有原文，但**游戏界面没有任何提示**。
所以「看日志」不是可选项，是唯一手段 —— 详见 `references/deep-dive.md` 的「为什么静默」。


#### 1.0.3 分级铁律：漏判安全，误报伤信任

```
语料 + cwt 都有        → confirmed   不报
仅 cwt 有（语料没用）   → info        「引擎定义了但 vanilla 未使用」
仅语料有（cwt 没定义）  → warn        ★ cwt 滞后是常态，绝不能 error
两处都查不到           → warn        可能是拼写错，也可能是 cwt 滞后
mod 自己定义了          → 跳过        自定义 scripted_effect/trigger 在 mod 内合法
```

**假错（false positive）对工具信任的杀伤，远大于漏判（false negative）。**
报一次假错，用户就不再信工具 —— 所以「能证明会不生效」才报 error，其余一律 warn/info。

#### 1.0.4 文档写判据，工具输出状态（编写原则）

**易变的东西不要放进稳定产物里。**

- 58 个独占名字会随游戏版本变化 → 它是 `container_exclusivity.py` 的**输出**，不进本文档正文
- 快照哈希要绑不可变量 → `mod_digest.py` 只覆盖**游戏内容文件**，排除 README/工坊描述
- 文档写**判据与方法**，工具输出**当前状态**，两者永不混淆

把会过期的内容写进文档，等于给未来的读者埋地雷。

### 1.1 文件与覆盖语义

1. **永不改游戏安装目录**；内容写在 mod 目录，装到玩家目录。
2. **同名文件 = 整体替换**（不是合并）。优先新增文件，用 `zz_` 前缀避免冲突。
3. **同 ID 后者覆盖前者**；用自己的 `NS_` 前缀。
4. **namespace 不能含数字** —— `zz_epi_chain1` 会导致 `Incorrect namespace event id`，
   **事件被丢弃、buff 静默失效**。只用字母：`zz_epi_grain`。（vanilla 364 个 namespace 无一含数字，一查便知）
5. **on_action 是合并的**（不是覆盖）—— 实测 42 个工坊 mod 共用 `on_startup` 且相安无事，可放心共存。

### 1.2 编码

6. **脚本 `.txt` 不能有 BOM**（vanilla 全无 BOM；实测 2630 个 .txt 里 0 个带 BOM）
   —— vanilla 词法器不处理 BOM。本机若有 Unicode Patch 会加容错，**但模组要发给别人，不能依赖补丁**。
7. **本地化 `.yml` 必须 UTF-8 with BOM**（vanilla 578 个 .yml **全部**带 BOM）。

### 1.3 效果 / 触发器

8. **`treasury` 是触发器不是效果** —— 效果里写 `treasury = -50` 会 `Unknown effect type`，
   **整条事件链失效且不闪退**。用 `add_treasury`（定额）或 `add_years_of_income`（按岁入比例，推荐）。
9. **事件选项内不能再套 `effect = {}`** —— 会报 `Unknown effect type. Key: effect`，直接写效果。
10. **`every_country` 在决议里会静默失败**（决议点不动、无报错）；放 on_actions 里就正常。

### 1.4 事件与变量

11. **决议触发的事件必须 `is_triggered_only = yes`**，否则会按 MTTH 乱触发。
12. **双变量比较语法**：`check_variable = { which = A which = B }`（判定 A ≥ B）。
13. **文本显示变量值**：`[Root.变量名.GetValue]`（tooltip 确认可用；事件 desc 需实测）。
14. **同名修正会合并** —— 重复 `add_country_modifier` 同一 ID 不新增条目。
15. **`duration` 写在 `add_country_modifier` 里**，不是写在修正定义里；`duration = -1` 表示永久。

### 1.5 流程与工具

16. **脚本改动后必须完全退出游戏重进**，EU4 无热重载。
17. **`.mod` 文件字段**：`path` 用正斜杠纯 ASCII；`picture=` 指向图片文件名；`supported_version` 写 `v1.37.5.0`。
18. **生成文件用 Python，不要用 PowerShell 拼接字符串** —— 会串行首（`1prestige = ...`）、丢缩进，
   一次错误的批量替换能写坏整个文件。
19. **任何时候都要先备份**，尤其是批量修改之前。

## 2. 标准工作流

```
0) 侦察    读游戏文件确认目标系统/字段/原版参考（grep vanilla 找参考用法）
1) 骨架    scripts/new_mod.py 生成 descriptor.mod + .mod + 本地化 + 示例内容
2) 内容    按 references/cheatsheet.md 的配方逐类添加
3) 本地化  每个 ID 补 l_english（UTF-8 with BOM）
4) 校验    python scripts/eu4_lint.py <Mod目录>
5) 安装    python scripts/install_mod.py <Mod目录> --enable
6) 实测    启动游戏 → 控制台 event 触发 → 看 error.log
7) 发布    descriptor.mod 齐全；干净 mod 集复测
```

**每次只加一类内容、加完就校验；不要一次写 2000 行再排错。**

## 3. 配方索引（代码在 `references/cheatsheet.md`）

| 任务 | 章节 |
|---|---|
| 新建 Mod 骨架 / `.mod` 与 `descriptor.mod` | cheatsheet §1 |
| 本地化规则、key 命名、颜色/变量/图标、中文字体 | §2 |
| 事件（MTTH / is_triggered_only / 选项 / 事件链） | §3 |
| 决议（category / potential / allow / effect / 任务型） | §4 |
| 任务树（对应 HOI4 国策树） | `references/missions.md` |
| 理念组 / 政府改革 / 政策 / 特权 | §5 |
| 修正（static / triggered / event / province） | §6 |
| scripted_effects / scripted_triggers | §7 |
| on_actions（钩子清单与合并语义） | §8 |
| 变量与工具（set_variable / check_variable / 事件目标） | §9 |
| 地图编辑（省份 / 地形 / 区域） | `references/map-editing.md` |
| 触发式提示（`hints/hints.txt`，bonus 内容类型） | §10 |
| 调试命令与 error.log 对照表 | `references/errorlog.md` |
| **效果/触发器易混对照表（treasury 类坑专章）** | `references/effects-triggers.md` |

## 4. 工具（`$S = scripts 目录`, `$GAME = 游戏目录`）

仅标准库，Python 3.10+。

```powershell
# ★ $S 以「加载本技能时返回的 Base directory」为准，不要照抄下面的示例路径。
#   三种安装方式的实际位置不同：
#     插件安装（DSH）  node_modules/dsh-eu4-modding/skills/eu4-modding/scripts
#     纯技能目录       ~/.agents/skills/eu4-modding/scripts
#     Claude Code      ~/.claude/skills/eu4-modding/scripts
$S = "<本技能 Base directory>\scripts"
$GAME = "C:\Program Files (x86)\Steam\steamapps\common\Europa Universalis IV"

# 环境体检（版本/数据完整性/exe 可信度/Unicode 补丁/日志规模）
python "$S\_paths.py"

# ★ 首次必做：从你自己的游戏安装生成白名单（技能包内不含此数据）
#   需要先克隆 cwtools-eu4-config（MIT）到本地
python "$S\build_reference.py" --cwt "D:\cwtools-eu4-config" --game "$GAME" --out ".\reference"

# 静态校验（八类检查；首次约 30s 建索引）
python "$S\eu4_lint.py" "D:\mods\MyMod" --game "$GAME"

# 查看每个检查函数归在哪个类别（--only 的取值就是这些）
python "$S\eu4_lint.py" --list-categories

# 机器可读输出（每条 issue 带 usability_hint + did_you_mean 拼写候选）
python "$S\eu4_lint.py" "D:\mods\MyMod" --reference ".\reference" --json

# 只查语法，不建白名单（快）
python "$S\eu4_lint.py" "D:\mods\MyMod" --no-reference

# error.log 分诊（默认扫全部 error*.log，EU4 会轮转）
python "$S\errorlog_triage.py" --only-mine

# 新建 / 安装 / 备份
python "$S\new_mod.py" "D:\mods\MyMod" --name "My Mod" --prefix MY --run-lint
python "$S\install_mod.py" "D:\mods\MyMod" --yes --enable
python "$S\install_mod.py" "D:\mods\MyMod" --yes --uninstall
python "$S\backup.py" "D:\mods\MyMod" --tag before-edit
python "$S\backup.py" "D:\mods\MyMod" --restore <备份名>      # 恢复前会自动备份当前

# 快照哈希（可复现）：给断言/备份做客观判据
python "$S\mod_digest.py" "D:\mods\MyMod" --print-digest     # 清单 + 字节数 + 哈希
python "$S\mod_digest.py" "D:\mods\MyMod" --verify <hash>
python "$S\mod_digest.py" --spec                            # 打印算法 spec

# 容器独占度分析：发现新控制字段（结果需人工确认，别自动采纳）
python "$S\container_exclusivity.py" --limit 40
python "$S\container_exclusivity.py" --name prestige        # 看它跨哪些容器
```

`build_reference.py` 三源交叉：cwtools 配置定**分类**、vanilla 语料给**用法**（`文件:行`）、
vanilla 数据文件补 scripted 名与枚举。`eu4.exe` 只作可选置信度（`--exe`），默认关闭。

**备份完整性 = 用 `mod_digest.py` 校验往返**：备份时记 digest，恢复后重算，
相等才算完整。两个工具必须用同一套 spec，否则「一致」无法复核。

> 许可：`build_reference.py` 引用 [cwtools/cwtools-eu4-config](https://github.com/cwtools/cwtools-eu4-config)（MIT）的数据，
> 只读不分发。若你的环境无网络，`--no-corpus --no-cwt` 仍可跑纯语法检查。

## 5. 排错：从 error.log 反推

先确认「只启用原版 + 你的 Mod」，再 grep 自己的文件名/ID —— **EU4 的 error.log 里 95%+ 是别人的噪声**。

| error.log 关键字 | 常见原因 | 修法 |
|---|---|---|
| `Unknown effect type. Key: xxx` | 把触发器当效果用（如 `treasury`） | 查 `reference/effects.json` |
| `Unknown trigger-type` / `Not a valid value` | trigger 名或参数值错 | 查 `reference/triggers.json` |
| `Unknown modifier-type: xxx` | 修正名拼错或未定义 | 查 `reference/modifiers.json` |
| `Incorrect namespace event id: xxx.` | **namespace 含数字** | 改成纯字母 |
| `Unknown effect type. Key: effect` | 选项里套了 `effect = {}` | 去掉那层 |
| `Parsing Error ... near line: N` | 括号不平衡 / 字段拼错 | 按行号查；跑 lint |
| `Parsing Errors. File: "", Error: ...` | 文件名为空 → 常见于 mod 的 `.mod` 字段错 | 检查 `path=` / `picture=` |
| 决议**点不动**、无报错 | 效果里有 `every_country` | 移到 on_actions |
| 事件**不弹** | namespace 非法 / 缺 `is_triggered_only` | 见铁律 4 / 11 |
| **buff 没加**但数值变了 | 效果名错，静默失效 | **必须查 error.log** |
| 文字显示 `[Root.xxx]` 原文 | loc 命令不被该处支持 | 换支持的写法 |
| 中文显示成方框/问号 | 未装 Unicode Patch | 见 cheatsheet §2 |
| **mod 自定义 scripted_effect/trigger 被报 warn** | 收集 mod 自身定义的逻辑没生效（`collect_mod_defined_names` 只扫 `common/scripted_effects/` 与 `scripted_triggers/` 的顶层 key） | 确认这两个目录的文件被 lint 扫到；自定义名必须定义在这两处才被识别 |
| `Unsupported ESM URL scheme ... protocol 'c:'` | Windows 上 `import()` 传了裸绝对路径 | 用 `pathToFileURL(path).href`，别手拼字符串 |
| `.mod` 的 `path` 含中文 | 由中文目录名安装而来 | `install_mod.py` 会把目录名转 ASCII；卸载会连旧中文名一起清理 |
| error.log 行数对不上 | 三种口径混用：**物理行 / 非空行 / 日志条目** | 用 `errorlog_triage.py` 输出的三个数，判定基准是**条目数**（只算 `[module:N]:` 开头） |

## 6. 验收清单

- [ ] `eu4_lint.py` 0 error（warning 尽量清零）
- [ ] 所有新增 ID 有本地化；`.yml` 带 BOM，脚本 `.txt` 无 BOM
- [ ] 所有效果/触发器名在 `reference/*.json` 里存在或分级为 info/warn
- [ ] 没有覆盖原版整文件（除非有意）；`.mod` 的 `path` 为 ASCII 正斜杠
- [ ] 干净 mod 集进游戏：触发全部内容，`error.log` 无自己文件的报错
- [ ] `errorlog_triage.py --only-mine` 输出为空

## 7. 深度资料

- `references/cheatsheet.md` —— 主参考，编号章节覆盖全部内容类型（自足，不依赖本机知识库）
- `references/deep-dive.md` —— 坑的成因与机理（静默失败为什么静默）
- `references/effects-triggers.md` —— 同形不同类对照表
- `references/missions.md` / `references/map-editing.md` / `references/errorlog.md`

## 8. 跨平台与共享

标准 Agent Skills 目录（`SKILL.md` + `scripts/` + `references/`），复制即迁移：

| 平台 | 安装位置 |
|---|---|
| DeepSeek Harness（插件） | `~/.dsh/profiles/desktop/node_modules/dsh-eu4-modding/skills/eu4-modding/` |
| DeepSeek Harness（纯技能） | `~/.agents/skills/eu4-modding/` |
| Claude Code | `~/.claude/skills/eu4-modding/` 或项目内 `.claude/skills/eu4-modding/` |
| 其他兼容客户端 | 其 Skills 设置；zip 根目录必须是 `eu4-modding/` |

> 插件安装后，技能内容里的相对路径（`references/...`、`scripts/...`）
> 一律相对于**加载时返回的 Base directory** 解析，不要写死上面的路径。

- 游戏/玩家目录自动探测；失败时设 `EU4_GAME` / `EU4_USER_DIR`。
- 依赖：Python 3.10+（标准库）+ EU4 1.37.5.0。
- **共享边界**：不含游戏本体资源，不含 `reference/`（vanilla 衍生数据，由用户在本地生成）。
- 详细安装/自检见同目录 `INSTALL.md`。

## 9. 已知限制（诚实边界）

**工具的诚实边界也是产品的一部分。** 用户看到「这个工具在什么条件下会不可靠」，
比看到 100 个通过测试更信任它。以下五条是**实测的**环境限制，不是推测。

### 9.1 中文显示依赖第三方补丁，不是 vanilla 能力

vanilla 的字体没有中文覆盖，中文写进 `l_english` 会显示成方框/问号。
要正常显示必须装 [EU4 Unicode Patch](https://github.com/YozoraTempest/EU4-Unicode-Patch)（MIT）。

该补丁的机制是 **DLL 劫持**（`eu4.exe` 导入表含 `version.dll`，
Windows 会优先加载 exe 同目录下的同名 DLL），**不修改 exe**，
所以不受 Steam 校验完整性影响。

对 lint 的含义：检查 `l_english` 里的中文时，**不能假设「中文 = 错误」或「中文 = 正常」**，
应提示「检测到非 ASCII 字符，需要 Unicode Patch 才能显示」。

### 9.2 白名单依赖第三方 cwtools-eu4-config

`reference/*.json` 的分类主源是 [cwtools/cwtools-eu4-config](https://github.com/cwtools/cwtools-eu4-config)（MIT）。
**它若被弃维护，覆盖率会下降**，而下降是静默的 —— 工具照常运行，只是判据变旧。

对策：`build_reference.py` 输出覆盖率，**低于阈值应 fail-loud**：

```powershell
python "$S\build_reference.py" --cwt <cwt目录> --game "$GAME" --out ".\reference" --min-coverage 90
```

覆盖率低于 90% 时非 0 退出并明确指出缺口归因，而不是静默降级。
（当前实测 99.64%，阈值 90% 留了大余量。）

### 9.3 版本脆弱性：全部验证基于 1.37.5.0

本技能包的所有数字（namespace 364 个、`.yml` 578 个、`treasury` 169 次…）
都来自 **v1.37.5.0** 的实测。

换版本后：

| 后果 | 应对 |
|---|---|
| `reference/` 里的名字表可能过时 | 重跑 `build_reference.py` |
| cwt 配置可能滞后于新版本 | 覆盖率会下降 → 9.2 的 fail-loud 会报出来 |
| 个别语法变化 | 以游戏文件为准，不以本文档为准 |

`reference/_meta.json` 里有 `game_version` 与 `generated_at`，可对照是否该重生成。

### 9.4 引用站点不可达（本机实测）

查资料时**不要只指向不可达的站**：

| 站点 | 状态 |
|---|---|
| `eu4.paradoxwikis.com` | ⚠️ Cloudflare 挑战 |
| `forum.paradoxplaza.com` | ⚠️ 返回 "Validating browser…" |
| Reddit / Wikipedia / DuckDuckGo / Brave | ⚠️ 域不通 |
| GitHub API / `wiki.cmdz.top` / `steamcommunity.com` / Mojeek | ✅ 可用 |

替代方案：**直接读游戏安装目录里的 vanilla 文件** —— 那是一手资料，且不受网络影响。
这本来也是本技能的第一原则（「不确定就去游戏目录查」）。

### 9.5 通用搜索引擎对精确工程资料检索能力有限

实测：四个不同的 EU4 开发类查询，搜索引擎返回**同一组通用结果**（降级行为）。

**结论**：找这类资料，**直接抓已知目标**比搜索更可靠：

| 目标 | 方式 |
|---|---|
| vanilla 的一手用法 | `Select-String -Path "$GAME\..."` |
| 白名单与分类 | `reference/*.json`（本地生成） |
| 同类项目 | GitHub API 直查仓库（不靠搜索） |
| 中文资料 | `wiki.cmdz.top` 的中文条目 |

### 9.6 其余未覆盖的部分

- `eu4_lint.py` 不检查 `trigger` 条件是否可达（无法静态判定）
- 任务树的**长链可完成性**只查环，不做完整闭包分析
- 文档里说的事实性内容（除数字外）没有自动检查（`check_doc_links.py` 只查死链）
- 文档从未被真实模组作者读过 —— 这是唯一无法由我们自己完善的一条
