# dsh-eu4-modding

**《欧陆风云 IV》(EU4) 1.37.5.0 模组开发技能包**，面向 DeepSeek Harness 及兼容的 Agent Skills 客户端。

EU4 的失败模式是**静默的**：语法写错不崩溃、不闪退，内容只是不生效。
所以本技能包把重心放在**静态校验**与 **error.log 分诊**，而不是文档篇幅。

## 目录结构

```
lib/
├── index.js                 插件入口：注册 eu4-modding 技能
└── preset.js                agent preset 注册（含 YAML 子集解析）

presets/eu4-modding/
├── preset.yml               模式名 / 描述 / order
└── agent.cordis.yml         该模式的插件组合（工具行 + 人设）

skills/eu4-modding/
├── SKILL.md                 主文件：核心原则 + 19 条铁律 + 工作流 + 工具 + 排错表
├── INSTALL.md               安装与自检
├── references/              cheatsheet / deep-dive / errorlog / missions / map-editing / effects-triggers
└── scripts/
    ├── _paths.py               环境探测（游戏目录 / 玩家目录 / 日志 / Unicode 补丁）
    ├── pdx_parser.py           PDX 脚本解析器（全部工具的地基）
    ├── loc_parser.py           .yml 本地化解析（与 .txt 是两套语法）
    ├── cwt_reader.py           读 cwtools-eu4-config 的模式定义（MIT）
    ├── build_reference.py      三源交叉 → reference/*.json 白名单
    ├── container_exclusivity.py 容器独占度分析（发现控制字段，需人工确认）
    ├── eu4_lint.py             八类静态校验（三态退出码：0=PASS 1=FAIL 2=INCOMPLETE）
    ├── errorlog_triage.py      error.log 分诊（默认扫全部 error*.log）
    ├── mod_digest.py           可复现的快照哈希
    ├── backup.py               备份 / 恢复 / 完整性校验
    ├── new_mod.py              脚手架（生成即过 lint）
    └── install_mod.py          安装 / 卸载 / dlc_load.json 管理
```

装好后，模式下拉框里会多一项 **「EU4 mod mode」**（`order: 11`），
带 EU4 专用人设：三态退出码、名字位由容器决定、漏判安全误报伤信任等判据。

## 快速开始

```powershell
# ★ $S 以「加载本技能时返回的 Base directory」为准，不要照抄下面的示例。
#   三种安装方式的路径不同：
#     插件安装（DSH）  ~/.dsh/profiles/desktop/node_modules/dsh-eu4-modding/skills/eu4-modding/scripts
#     纯技能目录       ~/.agents/skills/eu4-modding/scripts
#     Claude Code      ~/.claude/skills/eu4-modding/scripts
$S = "<本技能 Base directory>\scripts"
$GAME = "C:\Program Files (x86)\Steam\steamapps\common\Europa Universalis IV"

# 1) 生成白名单（首次必做；需要 cwtools-eu4-config 的本地检出）
python "$S\build_reference.py" --cwt "$env:TEMP\cwtools-eu4-config" --game "$GAME" --out ".\reference"

# 2) 新建模组（生成后立刻校验）
python "$S\new_mod.py" "D:\mods\MyMod" --name "My Mod" --prefix MY --run-lint

# 3) 校验
python "$S\eu4_lint.py" "D:\mods\MyMod" --game "$GAME"

# 4) 安装启用
python "$S\install_mod.py" "D:\mods\MyMod" --yes --enable

# 5) 排错（只看自己的）
python "$S\errorlog_triage.py" --only-mine
```

## 设计原则

1. **名字位由容器类型决定，绝不由名字形态推断。**
   `treasury` 是触发器不是效果；`modifier` 在修正定义里与 AI 权重块里是两种东西；
   `force_potential` 是权重块的控制字段而非触发器。详见 SKILL.md §1.0。

2. **漏判安全，误报伤信任。**
   只有「能证明会不生效」才报 `error`；其余一律 `warn`/`info`。
   一个对合法写法报假错的 linter，用户第一天就不信了。

3. **易变的东西不进稳定产物。**
   白名单、独占清单、快照哈希都是**工具输出**，不是文档内容；`reference/` 不随包分发。

4. **基准必须可复现。**
   快照哈希有 `--spec` / `--print-digest` / `--verify`；
   error.log 的基准写进 fixtures，不依赖用户机器上会轮转的实时日志。

## 范围与许可

- **不含任何游戏素材**：没有 vanilla 文件、贴图、音频、本地化文本。
- `reference/`（约 2 MB 的 vanilla 用法统计）**不进 npm 包**，由用户在本地生成。
- `build_reference.py` 可读取 [cwtools/cwtools-eu4-config](https://github.com/cwtools/cwtools-eu4-config)
  （MIT）的模式定义，**只读不分发**；用户自备检出。
- 本项目 MIT。

## 已知限制

工具会**如实说出自己的边界** —— 完整清单见 `SKILL.md` §9。要点：

| 限制 | 后果 |
|---|---|
| 中文显示依赖第三方 [Unicode Patch](https://github.com/YozoraTempest/EU4-Unicode-Patch)，非 vanilla 能力 | 未装补丁时中文显示为方框 |
| 白名单分类主源是第三方 `cwtools-eu4-config` | 它若滞后/弃维护，覆盖率会降；用 `--min-coverage` 让它 fail-loud |
| 全部验证基于 **1.37.5.0** | 换版本需重生成 `reference/`；`_meta.json` 的 `game_version` 可对照 |
| 任务 ID 检查**未索引 vanilla 任务** | 只能报「无法确认该 ID 是否存在」，不能断言「不存在」 |
| 不检查 `trigger` 条件可达性、任务长链闭包 | 需进游戏实测 + `errorlog_triage.py --only-mine` |

## 测试

```powershell
python -m unittest discover -s tests -p "test_*.py"
node --test tests/smoke.test.mjs
python tools/preflight.py          # 一条命令跑完所有发布前检查
```
