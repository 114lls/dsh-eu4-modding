# error.log 详解与分诊

> 核心事实：**EU4 的 error.log 里 95%+ 是别人的噪声。**
> 实测本机三份日志 7.89 MB / 50,513 条，可归属到自己 mod 的只有 1 行。

## 一、日志在哪、有几份

```
Documents/Paradox Interactive/Europa Universalis IV/logs/
├── error.log            当前
├── error_old.log        上一份
├── error_old_old.log    再上一份
├── text.log             本地化相关（哈希冲突详情）
└── system.log / game.log / setup.log ...
```

**EU4 会轮转**：`error.log` 满了（或每次启动）会滚动，旧的被覆盖。
实测见过一份 11 MB 的日志在几次启动后就被覆盖掉了。

所以 `errorlog_triage.py` **默认扫全部 `error*.log`**，只扫当前那份会漏掉 95% 的语料。

## 二、三种「行数」必须分清

这是最容易出报告错的地方。同一批文件可以数出三个不同的数字：

| 口径 | 定义 | 本机实测 |
|---|---|---|
| 物理行 | `raw.count(b"\n")` | **93,433** |
| 非空行 | strip 后非空的行数 | **53,537** |
| 日志条目 | 匹配 `[module.cpp:N]: msg` 的行 | **50,513** |

差距来自**续行**：EU4 有些解析错误会把上下文续在下一行，那些行不以 `[` 开头，
不属于任何条目的**开头**。

`errorlog_triage.py` 的判定基准是**条目数**，并同时输出三个数。
写进报告时务必标明口径。

## 三、日志格式

```
[module.cpp:1234]: 消息正文
```

部分消息里带定位信息：

```
[persistent.cpp:88]: Parsing Error. File: "events/xxx.txt", Error: Unknown effect type. Key: treasury
[eventmanager.cpp:434]: Incorrect namespace event id: zz_epi_chain1.
[eventmanager.cpp:166]: Event #41 (personal_byz_rd.41) is missing a title in events/personal_byz_rom
[effectimplementation.cpp:26715]: Script error! Script Object Token: "complete_mission", Error: Unknown effect type
```

## 四、错误分类（工具内置 13 类）

| 类别 | 特征串 | 严重程度 |
|---|---|---|
| `namespace` | `Incorrect namespace event id` | ★★★ 事件被丢弃 |
| `unknown-effect` | `Unknown effect type` | ★★★ 整条链失效 |
| `unknown-trigger` | `Unknown trigger` / `Trigger conditions invalid` | ★★★ |
| `parse-parsing` | `Parsing Error` | ★★★ 文件可能整体不加载 |
| `missing-loc` | `missing a title/desc` / `Missing province adjective` | ★★ 显示为 key 名 |
| `no-options` | `has no options` | ★★ 事件无法选择 |
| `missing-picture` | `is missing picture` | ★ 占位图 |
| `unknown-country` | `Unknown country 'XXX'` | ★★ tag 不存在 |
| `unknown-db` | `not found in DB` / `Undefined event target` | ★★ |
| `map` | `provinces.bmp` / `hasn't been assigned` | ★★ 地图相关 |
| `version` | `Invalid supported_version` | ★ 不影响运行 |
| `loc-collision` | `Localization hash collision` | ★ 看不出差异 |
| `other` | 未匹配 | — |

**分类优先级**：更具体的压在更笼统的之上。例如
`Parsing Error. File: ..., Error: Unknown effect type` 一行同时含两个特征，
它会归到 `unknown-effect`（更具体、更有指导意义）而非 `parse-parsing`。

## 五、归属判定：只做确定性判断

工具**不猜**「哪个 mod 产生了这个错」。它按三条确定性规则：

1. **文件名精确**：报错里带 `File: "events/xxx.txt"` → 查这个相对路径属于哪个 mod
2. **namespace 精确**：`Incorrect namespace event id: zz_epi_grain.` → 查该 namespace 属于哪个 mod
3. **前缀推导**（针对历史报错）：若某 id 与已知 namespace 有足够长的公共前缀
   （≥5 字符且 ≥较短一方 60%），归到该 mod

第 3 条是必需的：`Incorrect namespace event id: zz_epi_chain1.` 里的 `zz_epi_chain1`
在当前模组里**已经不存在**（改名为 `zz_epi_grain`），但它仍应归到该 mod。

都不中 → `unknown`，不硬猜。

## 六、用法

```powershell
$S = "$env:USERPROFILE\.agents\skills\eu4-modding\scripts"

# 全量分诊（自动发现全部 error*.log）
python "$S\errorlog_triage.py"

# 只看自己的
python "$S\errorlog_triage.py" --only-mine

# JSON 输出（给别的工具用）
python "$S\errorlog_triage.py" --json

# 指定日志 / 指定 mod
python "$S\errorlog_triage.py" --file "D:\some.log" --mod "D:\mods\MyMod"
```

## 七、排错顺序（重要）

1. **先确认「只启用原版 + 你的 Mod」** —— 几十个 mod 并存时噪声无法排除
2. `--only-mine` 看自己那几行
3. 按错误分类逐类修，**一次只改一类，改完重跑**
4. 脚本改动后**完全退出游戏重进**（EU4 无热重载）

## 八、基准测试不要绑实时日志

日志会轮转，写死的「本机有 45,501 行」明天就不成立了。

正确做法：**把基准写进 fixtures**。

```
tests/fixtures/errorlog_sample.log
```

它混了三类噪声源（vanilla / 本 mod / 别的 mod），归属逻辑才真的被测到 ——
否则会出现「归谁都对」的假通过。
