# EU4 脚本速查（cheatsheet）

> **本文只写判据与配方，不写易变清单。**
> 名字是否合法，查 `build_reference.py` 生成的 `reference/*.json`；
> 本文只教「怎么写」和「为什么这么写」。

章节编号与 `SKILL.md` §3 的配方索引一一对应，由 `tools/check_doc_links.py` 做死链检查。

---

## §1 骨架：Mod 目录与 `.mod` 字段

### 最小可用结构

```
MyMod/
├── descriptor.mod            mod 目录里的那份（游戏读它）
├── MyMod.mod                 同级的那份（启动器读它，装到玩家目录）
├── events/xxx_events.txt
├── common/
├── decisions/
└── localisation/xxx_l_english.yml
```

一条命令生成（**生成即过 lint**）：

```powershell
python "$S\new_mod.py" "D:\mods\MyMod" --name "My Mod" --prefix MY --run-lint
# 期望输出：lint = error 0 / warn 0 / info 0
```

### `.mod` 字段（实测有效）

```
name = "My Mod"
path = "C:/Users/you/Documents/Paradox Interactive/Europa Universalis IV/mod/MyMod"
supported_version = "v1.37.5.0"
tags = { "Alternative History" }
picture = "thumbnail.png"
```

| 字段 | 要求 | 常见错 |
|---|---|---|
| `path` | **正斜杠、纯 ASCII** | 写反斜杠；路径含中文（可能加载失败） |
| `picture` | 图片**文件名** | 写成 `thumbnail=`（错误的字段名） |
| `supported_version` | `v1.37.5.0` 或 `"1.37.*"` | 写 `*` / `1.*` 会报 `Invalid supported_version` |

### 三条覆盖语义（必须记住）

1. **同名文件 = 整体替换**，不是合并。优先**新增**文件，用 `zz_` 前缀。
2. **同 ID 后者覆盖前者**。事件用独立 `add_namespace`，ID 带自己的前缀。
3. **on_action 是合并的**（实测 42 个工坊 mod 共用 `on_startup` 相安无事）。

---

## §2 本地化：`.yml` 规则与中文

### 语法（与脚本 `.txt` 完全不同）

```
l_english:
 MY_EVENT.1.t:0 "标题"
 MY_EVENT.1.d:0 "描述，可以带 $VAR$ 与 [Root.GetName]"
 MY_EVENT.1.a:0 "选项文字"
```

- 行首 `l_english:` 是**语言根键**，与文件名后缀一致（`_l_english.yml` → `l_english:`）
- 键值用**冒号**不是等号：`KEY:0 "value"`
- 值必须在**双引号**内，否则该条被忽略
- 值后面**可以跟行尾注释**：`KEY:0 "值" # [LocEditor:AwaitingBatchUpdate]`
- 值内部**可以含未转义的引号**（PDX 以该行**最后一个**引号作终止符）

### 编码铁律（两个方向，别弄反）

| 文件类型 | BOM | 依据 |
|---|---|---|
| 脚本 `.txt` | **不能有** | vanilla 2630 个 .txt 实测 0 个带 BOM；词法器不处理 BOM |
| 本地化 `.yml` | **必须有** | vanilla 578 个 .yml **全部**带 BOM |

本机若装了 EU4 Unicode Patch，脚本词法器会加 BOM 容错 —— **但模组要发给别人，不能依赖补丁**。

### 中文显示（两层，缺一不可）

1. **vanilla 层**：中文写进 `l_english` 会显示成方框/问号（字体无中文覆盖）
2. **补丁层**：装 [EU4 Unicode Patch](https://github.com/YozoraTempest/EU4-Unicode-Patch)（MIT）后正常

判断补丁是否加载：看 `<游戏目录>\plugins\eu4_unicode_patch.log` 的时间戳有没有更新。

### 常用标记

| 写法 | 效果 |
|---|---|
| `§Y文字§!` | 黄色高亮（`§R` 红 / `§G` 绿 / `§B` 蓝 / `§!` 结束） |
| `$VAR$` | 脚本传进来的变量 |
| `[Root.GetName]` | 作用域取值（**不是所有位置都支持**，tooltip 确认） |
| `\n` | 换行 |

---

## §3 事件

### 最小模板（用 `new_mod.py` 生成的就是这个）

```
namespace = myns          # 只用字母，不能含数字！

country_event = {
    id = myns.1
    title = "myns.1.t"
    desc = "myns.1.d"
    is_triggered_only = yes

    trigger = {
        treasury = 100          # 触发器位：treasury 在这里合法
    }

    immediate = {
        add_prestige = 5        # 效果位：必须是效果名
    }

    option = {
        name = "myns.1.a"
        add_years_of_income = 0.5
    }
}
```

### 三个必踩的坑

| 坑 | 症状 | 正确做法 |
|---|---|---|
| namespace 含数字 | `Incorrect namespace event id`，**事件被丢弃、buff 静默失效** | 只用字母：`myns` 而非 `myns1` |
| 选项内套 `effect = {}` | `Unknown effect type. Key: effect` | 直接把效果写在 `option` 里 |
| 效果位写触发器 | `Unknown effect type`，**整条链失效且不闪退** | 查 `reference/effects.json` |

### 两种触发方式

```
# A. 按 MTTH 自动触发（有随机性）
country_event = {
    id = myns.2
    mean_time_to_happen = { months = 120 }
    ...
}

# B. 只被 scripted / 决议 / 控制台触发（推荐）
is_triggered_only = yes
```

**决议触发的事件必须用 B**，否则会按 MTTH 乱触发。

### 事件链

```
# 第一段
country_event = {
    id = myns.10
    is_triggered_only = yes
    option = {
        name = "myns.10.a"
        add_country_modifier = { name = myns_prepared duration = 3650 }
    }
}

# 第二段：用「有没有那个修正」当 gate
country_event = {
    id = myns.11
    is_triggered_only = yes
    trigger = { has_country_modifier = myns_prepared }
    ...
}
```

---

## §4 决议

```
country_decisions = {

    myns_decision = {
        potential = {
            country_exists = yes
            is_at_war = no
        }
        allow = {
            treasury = 100
        }
        effect = {
            add_years_of_income = -0.5
            add_prestige = 10
        }
        ai_will_do = { factor = 1 }
    }
}
```

| key | 含义 | 注意 |
|---|---|---|
| `potential` | 是否**显示**该决议 | 与 `allow` 不同 |
| `allow` | 是否**可点** | 不满足时按钮灰 |
| `effect` | 点了之后做什么 | 这里有 `every_country` 会**静默失败**（点不动、无报错），要放 on_actions |

---

## §5 理念组 / 政府改革 / 政策 / 特权

### 理念组

```
myideas = {
    myideas_start = {
        myideas_start_modifier = { global_tax_modifier = 0.10 }
    }
    myideas_1 = { ... }      # 依次到 myideas_7
    myideas_bonus = { ... }
    myideas_trigger = { ... }
}
```

### 政府改革

```
myreform = {
    potential = { ... }
    allow = { ... }
    effect = { add_stability = 1 }
    # 修正直接写在改革块里
    global_tax_modifier = 0.05
}
```

### 修正的两种放置位置（易混）

| 位置 | 形态 |
|---|---|
| **修正定义**（`common/event_modifiers/`） | `mymod = { global_tax_modifier = 0.10 }` |
| **效果引用** | `add_country_modifier = { name = mymod duration = 3650 }` |

`duration` 写在**引用处**，不是写在修正定义里。`duration = -1` 表示永久。

---

## §6 修正

### 四种修正类型

| 类型 | 文件位置 | 怎么用 |
|---|---|---|
| `static_modifier` | `common/static_modifiers/` | 固定引用 |
| `event_modifier` | `common/event_modifiers/` | `add_country_modifier = { name = X duration = N }` |
| `triggered_modifier` | `common/triggered_modifiers/` | 条件满足自动生效 |
| `province_triggered_modifier` | `common/province_triggered_modifiers/` | 省份版 |

### 同名修正会合并

重复 `add_country_modifier` **同一 ID** 不新增条目，只刷新时长。
需要记录多代同名效果时，必须准备**多个不同 ID 的变体**。

---

## §7 scripted_effects / scripted_triggers

```
# common/scripted_effects/zz_mymod_effects.txt
mymod_reward = {
    add_prestige = 10
    set_country_flag = mymod_rewarded
}

# common/scripted_triggers/zz_mymod_triggers.txt
mymod_has_reward = {
    has_country_flag = mymod_rewarded
}
```

引用时**不带括号参数**（除非用了 `$PARAM$`）：

```
immediate = { mymod_reward = yes }
trigger = { mymod_has_reward = yes }
```

### 带参数

```
mymod_give_modifier = {
    add_country_modifier = { name = $MODIFIER$ duration = $DURATION$ }
}
# 调用
immediate = { mymod_give_modifier = { MODIFIER = mymod_a DURATION = 3650 } }
```

`$...$` 是普通 token，解析器按原样保留。

### lint 的识别边界

lint 会先收集本 mod 的 `common/scripted_effects/` 与 `common/scripted_triggers/`
**顶层 key**，命中就跳过白名单检查。所以自定义名**必须定义在这两处**才被识别。

---

## §8 on_actions

```
# common/on_actions/zz_mymod_on_actions.txt
on_startup = {
    mymod_init = yes
}

on_new_monarch = {
    if = {
        limit = { has_regency = no }
        add_prestige = 2
    }
}
```

| 事实 | 依据 |
|---|---|
| on_action **合并**，不覆盖 | 实测 42 个工坊 mod 共用 `on_startup` 相安无事 |
| 钩子名不带 `on…effect` 后缀 | `on_battle_won_country` 是钩子；`on_battle_won_country_effect` 是 scripted_effect |
| 同 mod 内重复定义同一钩子 | 语义取决于引擎合并顺序，lint 报 `warn` |

钩子全集由 `build_reference.py` 生成到 `reference/on_actions.json`
（主集 = vanilla `common/on_actions/*.txt` 的顶层 key）。

---

## §9 变量与事件目标

```
# 设置
set_variable = { which = mymod_score value = 0 }
change_variable = { which = mymod_score value = 1 }

# 读取（判 ≥ 某值）
check_variable = { which = mymod_score value = 10 }

# 双变量比较（判 A ≥ B）
check_variable = { which = mymod_a which = mymod_b }
```

### 在文本里显示变量值

```
desc = "分数：[Root.mymod_score.GetValue]"
```

**注意**：这个写法在 tooltip 里确认可用；事件 `desc` 里需实测。

### 事件目标

```
immediate = { save_event_target_as = mymod_target }
# 之后
mymod_target = { add_prestige = 5 }
```

跨事件用 `save_global_event_target_as`（本事件用 `save_event_target_as`）。

### 作用域链

`ROOT` / `FROM` / `PREV` / `THIS` / `OWNER` / `CONTROLLER` / `CAPITAL_SCOPE` …
这些是**指代**，不是效果名 —— lint 会跳过它们。

---

## §10 触发式提示（`hints/hints.txt`）

一个经常被忽略的可模组化系统：

```
hints = {
    mymod_hint = {
        window = "..."
        title = "..."
        text = "..."
        trigger = { ... }
    }
}
```

适合做新手引导 / 版本提示。属于 bonus 内容类型，不是核心玩法。

---

## §11 调试命令与日志

### 控制台

```
event myns.1            触发事件
kill                    杀君主（测继承相关）
debug_mode              显示事件详情
cash 5000               给钱
tag SWE                 切国家
yesman / no              AI 全同意 / 全拒绝
```

### 日志

```
Documents/Paradox Interactive/Europa Universalis IV/logs/error.log
```

**会轮转**（`error_old.log` / `error_old_old.log`）。用工具扫全部：

```powershell
python "$S\errorlog_triage.py" --only-mine
```

排错细节见 `references/errorlog.md`。

**脚本改动后必须完全退出游戏重进** —— EU4 没有热重载。

---

## §12 版本差异与兼容

| 项 | 1.37.5.0 实测事实 |
|---|---|
| namespace | 364 个，**无一含数字** |
| `treasury` | 只作**触发器**；效果用 `add_treasury` / `add_years_of_income` |
| `.txt` BOM | 2630 个抽样 0 个带 BOM |
| `.yml` BOM | 578 个**全部**带 BOM |
| 非 UTF-8 脚本 | 683 个（CP1252/西文重音），所以编码问题只能算 `warning` |
| 效果/触发器名字 | 用 `build_reference.py` 现生，别抄旧教程 |

**不确定语法时去游戏目录查 vanilla**，不要凭印象：

```powershell
Select-String -Path "$GAME\events\*.txt" -Pattern 'namespace\s*=' | Select-Object -First 5
Select-String -Path "$GAME\common\scripted_effects\*.txt" -Pattern 'add_prestige' | Select-Object -First 3
```
