# 效果 / 触发器易混对照表（`treasury` 类坑专章）

> **本文只写判据，不写易变清单。** 具体的名字表由 `build_reference.py` 生成到
> `reference/effects.json` / `triggers.json`；本文教你**怎么判断**，而不是告诉你答案。

## 一、这个坑为什么致命

EU4 的失败模式是**静默的**：

```
immediate = { treasury = -50 }
```

这行写在效果位。`treasury` 是**触发器**（判「国库是否 ≥ X」），不是效果。
游戏不会崩、不弹窗、不闪退 —— 它报一条 error.log，然后**整条事件链失效**。

实测过的真实症状：

| 现象 | 真相 |
|---|---|
| 事件不弹 | namespace 非法 / 缺 `is_triggered_only` |
| 事件弹了但 buff 没加 | 效果名写错，静默失效 |
| 决议点不动、无报错 | 效果里有 `every_country` |
| 整条链失效且无痕 | 效果位写了触发器名 |

## 二、判定三步法（顺序不能颠倒）

### 第 1 步：确定「这里是效果位还是触发器位」

**由容器决定，不由名字形态决定。**

| 语境容器 | 内部是什么位 |
|---|---|
| `trigger = {}` / `limit = {}` / `potential = {}` / `allow = {}` | **触发器位** |
| `immediate = {}` / `option = {}` / `effect = {}` / `hidden_effect = {}` | **效果位** |
| `mean_time_to_happen = {}` / `ai_chance = {}` / `ai_will_do = {}` / `chance = {}` | **触发器位**（权重块） |
| `modifier = {}` **含 `factor`** | **触发器位**（AI 权重块） |
| `modifier = {}` **不含 `factor`** | **修正字段位**（不是名字位） |
| `conditional_modifier` / `scaled_modifier` / `bonus` 下的 `modifier` | **修正字段位** |
| `trigger_switch = {}` 的 case key | **数据值**（旗标名） |

`OR` / `AND` / `NOT` / `if` / `else` 是**透明**的：继承父语境。

### 第 2 步：查白名单（不是查你的记忆）

```powershell
python skills\eu4-modding\scripts\build_reference.py --cwt <cwt目录> --game <游戏目录> --out reference
```

然后：

```powershell
# 效果名表里有没有它
Select-String -Path reference\effects.json -Pattern '"treasury"'
# 触发器名表里有没有它
Select-String -Path reference\triggers.json -Pattern '"treasury"'
```

**正确结果**：`effects.json` 里**没有**，`triggers.json` 里**有**。

### 第 3 步：看 status 再决定严重程度

`reference/*.json` 每条都有 `status`：

| status | 含义 | lint 级别 |
|---|---|---|
| `confirmed` | 语料用过 + cwt 已分类 | 不报 |
| `cwt_only` | 引擎定义了但 vanilla 未使用 | `info` |
| `corpus_only` | 语料用过但 cwt 未定义（cwt 滞后于版本） | `warn` |
| 两处都没有 | 可能是拼写错 | `warn` |
| 在**对面**表里 | 写错位了 | **`error`** |

## 三、本项目的 9 个实证案例

| # | 标识符 | 看起来是 | 实际是 | 怎么确定的 |
|---|---|---|---|---|
| 1 | `treasury` | 效果 | **触发器** | 语料 169 次全在触发器位；cwt `alias[trigger:treasury] = int` |
| 2 | `limit` / `ai_chance` | 效果名 | **容器名** | 其直接子项才是名字位 |
| 3 | `who` / `type` / `estate` | 效果名 | **参数字段** | 父 key 不是语境容器 |
| 4 | `trigger_switch` 的 case key | 效果名 | **旗标名** | 父容器是 case 容器 |
| 5 | `on_battle_won_country_effect` | on_action 钩子 | **scripted_effect** | 定义在 `common/scripted_effects/` |
| 6 | `modifier` | 修正定义块 | **或 AI 权重块** | 看块内是否含 `factor` |
| 7 | `force_potential` / `scale` / `potential` | 触发器 | **权重块控制字段** | 只在权重容器内出现，语义是控制 |
| 8 | `prestige` | 修正 | **同时也是触发器** | `prestige = 50` 是条件 |
| 9 | `zz_epi_chain1` | 合法 event id | **非法** | vanilla 364 个 namespace 无一含数字 |

## 四、正确写法对照

```eur4
# ❌ 错：效果位写了触发器
immediate = { treasury = -50 }

# ✅ 对：定额
immediate = { add_treasury = -50 }

# ✅ 更推荐：按岁入比例（不依赖绝对金额，平衡性好）
immediate = { add_years_of_income = -0.5 }   # 扣半年岁入

# ❌ 错：选项内再套一层 effect
option = {
    name = "x.a"
    effect = { add_prestige = 1 }   # 报 Unknown effect type. Key: effect
}

# ✅ 对：直接写
option = {
    name = "x.a"
    add_prestige = 1
}

# ❌ 错：namespace 含数字
namespace = zz_epi_chain1

# ✅ 对
namespace = zz_epi_chain
```

## 五、怎么自己发现新的坑

规则会随版本变化，**方法不会**。两个工具：

```powershell
# 1) 容器独占度：找出「只在一种容器里出现」的名字
python skills\eu4-modding\scripts\container_exclusivity.py --limit 40

# 2) 某个名字跨哪些容器（真名字通常跨多种）
python skills\eu4-modding\scripts\container_exclusivity.py --name prestige
```

**重要**：独占度**只做发现，不做裁决**。实测 58 个独占名字里只有 6 个是真控制字段，
其余 52 个是真触发器（只是在 vanilla 里恰好只被 AI 权重块用到）。

确认新控制字段的流程：

1. 跑工具拿到候选
2. **读原文**确认语义（是「判断条件」还是「控制开关」）
3. 确认后写进 `build_reference.py` 的 `CONTROL_FIELDS`
4. 重跑 `build_reference.py`，看覆盖率与缺口归因变化

宁可漏收（false negative，安全），也不要误收（false positive，伤信任）。
