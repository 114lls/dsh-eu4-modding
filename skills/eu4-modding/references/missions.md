# 任务树（EU4 特有，对应 HOI4 的国策树）

> 只写结构与判据；具体的任务 ID 与条件请查 vanilla `missions/*.txt`（228 个文件）。
> 实测规模：**1422 个 series / 7343 个任务块**。

## 一、任务树的结构

```
missions/
└── MY_country_missions.txt

MY_country_missions = {        # ← 这是「任务系列 series」，不是单个任务
    slot = 1                   # 界面槽位
    generic = yes
    potential = { ... }        # 整个系列的触发条件

    mymod_mission_1 = {        # ← 这才是任务本体
        icon = mission_trade
        required_missions = { ... }   # 前置任务
        trigger = { ... }             # 完成条件
        effect = { ... }              # 完成奖励
    }
}
```

**易混点**：`slot` / `generic` / `ai` / `potential` 是**系列元数据**，
不是任务本体。数任务数时必须跳过它们（否则 2 个任务会数成 4 个）。

## 二、与 HOI4 国策树的差异

| 项 | HOI4 国策树 | EU4 任务树 |
|---|---|---|
| 前置依赖 | `prerequisite = { ... }` | **`required_missions = { ... }`** |
| 一国多树 | 1.19 每国同时只能一棵激活树 | 可按条件切换不同系列 |
| 布局 | 手动坐标 `x` / `y` | `slot = N` 定义槽位 |
| 完成条件 | `completion_trigger` | 任务自身的 `trigger` |
| 完成奖励 | `completion_reward` | `effect` |

**一处更正**（本文早期版本写错过）：

我曾写「EU4 任务树没有显式前置，只能靠旗标」。这是**错的**。
实测 `required_missions` 在全库出现 **4724 次**，是常规机制，不是罕见特性。

正确做法是两者并用：

```
mymod_mission_2 = {
    required_missions = { mymod_mission_1 }             # 显式前置（界面会画箭头）
    trigger = { has_country_flag = mymod_step1_done }   # 若还需额外条件
    effect = { ... }
}
```

只靠 `required_missions` 时界面会画前置箭头；只靠旗标则不画。

## 三、常见坑

| 坑 | 症状 | 修法 |
|---|---|---|
| 任务永不出现 | `trigger` 恒为 false | 逐项拆开测；`NOT = { ... }` 是取反 |
| 任务永远不可完成 | `required_missions` 引用了**自身** | lint 报 `mission-self-require`（warn） |
| 改了任务没生效 | 同 ID 被定义两次（后者覆盖前者） | lint 报 `mission-duplicate`（warn） |
| `complete_mission` 什么都没发生 | 引用了**不存在**的任务 ID | lint 报 `mission-ref`（info） |
| `icon` 用了未注册的 GFX | 图标空白 | 查 `interface/*.gfx` |
| 任务完成但奖励没生效 | `effect` 里有 `every_country` 等 | 见 SKILL.md 铁律 10 |

## 四、调试

```
# 控制台：直接完成某个任务（任务 ID 即块名）
complete_mission mymod_mission_1
```

## 五、lint 覆盖了什么

`eu4_lint.py` 的 `check_missions()` 做三层检查，归在 `events` 类别：

| 检查 | code | 分级 | 为什么这个级别 |
|---|---|---|---|
| 引用不存在的任务 | `mission-ref` | **info** | 可能来自 vanilla 或其他 mod；报 error 就是假错 |
| `required_missions` 自环 | `mission-self-require` | warn | 自环必然不可完成 |
| 同 mod 内重复定义任务 | `mission-duplicate` | warn | 后者覆盖前者，容易误以为都生效 |

「引用不存在的任务」为什么只是 info：
一个 mod 完全可以引用 vanilla 的任务 ID —— 例如自己写的事件里
`complete_mission = conquer_scotland`。报 error 就是假错，
而假错对工具信任的杀伤远大于漏判。

## 六、未覆盖的部分

以下**不做**自动检查（需要更完整的语义分析）：

- `trigger` 里的条件是否可达（无法静态判定，`always = no` 除外）
- 系列 `potential` 与实际国家是否匹配
- 任务在界面上的实际排布（`slot` 冲突）
- `required_missions` 形成的**长链**是否可完成（只查自环，不查多节点环）

这些靠进游戏实测 + `errorlog_triage.py --only-mine`。

## 七、实现细节：为什么路径判断必须用相对路径

`check_missions()` 只把「**mod 目录内相对路径**以 `missions/` 开头」的文件当任务定义。

实测踩过：如果对**绝对路径**做 `/missions/` 子串判断，当 mod 目录本身叫
`missions`（例如 fixture `tests/fixtures/missions/`）时，
`events/` 下的文件也会命中，任务数从 2 变成 4。

这是「按容器/位置判定」原则在**文件路径**上的同一条教训。
