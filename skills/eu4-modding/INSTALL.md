# EU4 模组开发技能包 —— 安装与自检

## 一、这是什么

一个 **Agent Skill**：`SKILL.md` + `references/`（文档）+ `scripts/`（Python 工具）。
它帮你在 DeepSeek Harness / Claude Code 等兼容客户端里做 EU4 模组：
新建、写内容、**静态校验**、安装启用、**按 error.log 排错**。

**核心判断**：EU4 的失败模式是**静默的** —— 解析错了不报错、不闪退，只是不生效。
所以本技能把重心放在「静态校验 + error.log 分诊」，工具价值大于文档。

## 二、运行前提

| 项 | 要求 |
|---|---|
| Python | 3.10+（只用标准库，无需 pip 安装） |
| 游戏 | Europa Universalis IV **v1.37.5.0** |
| 网络 | 可选。首次生成白名单需要克隆 `cwtools/cwtools-eu4-config`（MIT）；无网络时只做语法检查 |

本技能**不含任何游戏素材**，也不含 `reference/`（那是 vanilla 用法统计，属于游戏内容衍生数据）。

## 三、安装

### 方式 A：作为 DSH 插件（推荐，本地试用走这一条）

> **本节的步骤是实测得出的**（2026-11-01，依据本机已装的 `dsh-cyber-red-ui` /
> `dsh-purge` 两个插件在 `~/.dsh/profiles/desktop/package.json` 里的实际声明形式）。
> **只把目录 Copy-Item 到 `plugins/` 是不够的** —— 宿主不会自动加载它，
> 必须同时登记 `file:` 依赖与 `bundles` 两项，见下面第 4、5 步。

```powershell
# 1) 源目录（在源码仓库根执行）
$SRC = (Get-Location).Path            # 应含 package.json / cordis.patch.yml / lib / skills
# 2) 插件目录
$DST = "$env:USERPROFILE\.dsh\plugins\dsh-eu4-modding"

# 3) 复制插件本体（目录不存在才复制）
if (!(Test-Path $DST)) { Copy-Item -Recurse $SRC $DST }
else { "目标已存在，先卸载或备份：$DST" }

# 4) 登记 file: 依赖（★ 缺这一步宿主不会加载）
$PROFILE_PKG = "$env:USERPROFILE\.dsh\profiles\desktop\package.json"
Copy-Item $PROFILE_PKG "$PROFILE_PKG.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
# 用 Python 改 JSON，不要用 PowerShell 拼接（会写坏文件）
python -c @"
import json, io, os
p = os.path.expandvars(r'$PROFILE_PKG')
d = json.load(io.open(p, encoding='utf-8'))
d.setdefault('dependencies', {})['dsh-eu4-modding'] = 'file:../../plugins/dsh-eu4-modding'
b = d.setdefault('dsh', {}).setdefault('profile', {}).setdefault('bundles', [])
if 'dsh-eu4-modding' not in b:
    b.append('dsh-eu4-modding')
io.open(p, 'w', encoding='utf-8', newline='\n').write(
    json.dumps(d, ensure_ascii=False, indent=2))
print('registered')
"@

# 5) 安装依赖（把 file: 依赖链接进 node_modules）
$PNPM = "$env:LOCALAPPDATA\Programs\DeepSeek Harness.new-{...}\resources\runtime\primary-runtime\dependencies\pnpm\pnpm.cjs"
# ↑ 版本化深路径，需按实际目录填；也可用宿主自带的 pnpm
node $PNPM install --dir "$env:USERPROFILE\.dsh\profiles\desktop"

# 6) 校验：三项全部为 True 才算装好
@('package.json','cordis.patch.yml','lib\index.js','skills\eu4-modding\SKILL.md') |
  ForEach-Object { "$_ : $(Test-Path (Join-Path $DST $_))" }
# 以及：profile 里已登记
python -c "import json,io,os;d=json.load(io.open(os.path.expandvars(r'$PROFILE_PKG'),encoding='utf-8'));print('dep:', 'dsh-eu4-modding' in d.get('dependencies',{}));print('bundle:', 'dsh-eu4-modding' in d['dsh']['profile']['bundles'])"
```

**然后完全退出 DSH 并重启**，在会话里确认技能表出现 `eu4-modding`。

> **为什么需要 `file:` + `pnpm install`**：宿主按 profile 的 `dependencies` 解析插件，
> 本地路径用 `file:` 协议；`peerDependencies`（`@deepseek-ai/cordis` / `dsh-skill`）
> 由宿主自身提供（实测它们在 `app.asar` 内，出现 1061 / 224 次），**插件不需要自带**。
> 因此 `package.json` 里 `dependencies: {}` 是正常的，不需要为插件加依赖。

**卸载**（三处都要清，否则会残留加载失败）：

```powershell
# 1) 从 profile 移除
python -c @"
import json, io, os
p = os.path.expanduser('~/.dsh/profiles/desktop/package.json')
d = json.load(io.open(p, encoding='utf-8'))
d.get('dependencies', {}).pop('dsh-eu4-modding', None)
b = d.get('dsh', {}).get('profile', {}).get('bundles', [])
if 'dsh-eu4-modding' in b: b.remove('dsh-eu4-modding')
io.open(p, 'w', encoding='utf-8', newline='\n').write(json.dumps(d, ensure_ascii=False, indent=2))
print('unregistered')
"@
# 2) 删插件目录
Remove-Item -Recurse -Force "$env:USERPROFILE\.dsh\plugins\dsh-eu4-modding"
# 3) 让 pnpm 收拾 node_modules
node $PNPM install --dir "$env:USERPROFILE\.dsh\profiles\desktop"
```

> 注：不要用 `dsh plugin ...` 之类的 CLI。本机实测该命令不在 PATH，
> 实际可执行文件埋在版本化深路径（`%LOCALAPPDATA%\Programs\DeepSeek Harness.new-{...}\resources\runtime\cli\bin\dsh.cmd`），
> 未经验证。**没给全路径就不是可执行指令**。

### 方式 B：作为纯技能目录（不装插件，最轻）

```powershell
# DeepSeek Harness
Copy-Item -Recurse .\skills\eu4-modding "$env:USERPROFILE\.agents\skills\eu4-modding"
# Claude Code
Copy-Item -Recurse .\skills\eu4-modding "$env:USERPROFILE\.claude\skills\eu4-modding"
```

zip 分发时**根目录必须**是 `eu4-modding/`。

## 四、首次必做：生成白名单

lint 的名字位检查需要 `reference/*.json`。它由**你自己的游戏安装**现场生成：

```powershell
$S = "$env:USERPROFILE\.agents\skills\eu4-modding\scripts"
$GAME = "C:\Program Files (x86)\Steam\steamapps\common\Europa Universalis IV"

# 1) 取 cwtools 模式定义（MIT，只读引用，不随包分发）
git clone --depth 1 https://github.com/cwtools/cwtools-eu4-config.git "$env:TEMP\cwtools-eu4-config"

# 2) 生成
python "$S\build_reference.py" --cwt "$env:TEMP\cwtools-eu4-config" --game "$GAME" --out ".\reference"
```

产出：`effects.json` / `triggers.json` / `modifiers.json` / `on_actions.json` / `_meta.json`，
每条都带 `status`（confirmed / cwt_only / corpus_only）与 `corpus.examples`（`文件:行`）。

`_meta.json` 里带 `game_version` 与 `generated_at` —— **换版本时能看出该重生成**。

## 五、自检

```powershell
$S = "$env:USERPROFILE\.agents\skills\eu4-modding\scripts"

# 环境体检
python "$S\_paths.py"

# 用干净骨架验证工具链自身
python "$S\new_mod.py" "$env:TEMP\SelfTest" --name "SelfTest" --prefix self --run-lint
# 期望：lint = error 0 / warn 0 / info 0
```

若 `--run-lint` 报 error，那是**工具 bug**，不是你的模组问题。

### 跑测试（源码目录内）

```powershell
cd C:\Users\alice\Documents\dsh-eu4-modding
$env:EU4_CWT_DIR = "$env:TEMP\cwtools-eu4-config"
python -m unittest discover -s tests -p "test_*.py"   # Python 单测
node --test tests/smoke.test.mjs                      # 打包结构冒烟
```

## 六、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `找不到游戏目录` | 装在非标准路径 | 设 `EU4_GAME`（Linux Proton 还要设 `EU4_USER_DIR`） |
| lint 说「未找到 reference 目录」 | 没跑第四节 | 跑 `build_reference.py`，或加 `--no-reference` 跳过名字位检查 |
| 名字位检查报大量 `unknown-name` | 白名单与游戏版本不匹配 | 重新生成 reference；`_meta.json` 里的 `game_version` 可对照 |
| 中文输出乱码（控制台） | PowerShell 5.1 用 cp936 | 工具内部已 `reconfigure(encoding='utf-8')`；若仍乱码，`chcp 65001` |
| 想跳过某类检查 | 调试需要 | `--only syntax,events` 或 `--no-reference` |

## 七、卸载

```powershell
python "$S\install_mod.py" "D:\mods\MyMod" --yes --uninstall
```

卸载会移除 `dlc_load.json` 条目与玩家目录副本，**不动你的源目录**。
若曾按中文目录名装过，卸载会连那份一起清理（不留孤儿）。
