"""EU4 静态校验器 —— 把「静默不生效」变成「开工前就报错」。

设计前提（决定一切）
------------------
EU4 的失败模式是**静默的**：解析错了不报错、不闪退，只是不生效。
所以本工具的假错（false positive）比漏判（false negative）**危害大得多** ——
报一次假错，用户就不再信工具了。分级因此从严：

    error  只有「能证明会不生效」才报（括号不平衡、选项内套 effect、namespace 含数字…）
    warn   可疑但可能有正当理由（未知名、变量只设不读、旗标设了不清…）
    info   仅提示，不影响成败（cwt 有而 vanilla 未用的名字）

七类检查（逐类可单独开关）
------------------------
  1. 语法      括号平衡、引号闭合、BOM、编码、未闭合块
  2. 事件      namespace 合法性、id 前缀匹配、is_triggered_only、选项内套 effect、
               事件定义 vs 引用（`event xxx`/`country_event = { id = }`）
               附带：on_action 重复定义、任务树的定义 vs 引用
  3. 本地化    .yml BOM、key 缺失、语言根键、未加引号值
  4. 修正      定义 vs 引用、未引用（add_country_modifier 的 name/ID）
  5. 变量      set_variable vs 读写、未初始化、只设不读
  6. 旗标      set/clear/has 配对，「设了不清」
  7. 名字位    效果/触发器名对照白名单，**按容器类型判定**（见 SKILL.md §1.0）

用法
----
    python eu4_lint.py <Mod目录> --game <游戏目录>
    python eu4_lint.py <Mod目录> --no-reference     # 只查 1-6 类
    python eu4_lint.py <Mod目录> --only 1,2         # 只跑指定类别
    python eu4_lint.py <Mod目录> --json             # 机器可读输出
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _paths
import loc_parser
import pdx_parser

__all__ = ["Issue", "LintResult", "lint_mod", "CATEGORIES"]

CATEGORIES = {
    "syntax": "语法与编码",
    "events": "事件",
    "crossrefs": "跨文件引用（任务 ID / 前置任务）",
    "loc": "本地化",
    "modifiers": "修正",
    "variables": "变量",
    "flags": "旗标",
    "names": "名字位（效果/触发器白名单）",
}

# 类别的默认归属（--only 未指定时跑哪些）
DEFAULT_CATEGORIES = ("syntax", "events", "crossrefs", "loc",
                      "modifiers", "variables", "flags", "names")

# 每个检查函数属于哪个类别。★ 归类按「这类引用的覆盖面」定，不按「谁调用它」定。
#   实测 vanilla 的 `complete_mission =` 分布（结构化 key，不含注释）：
#     decisions 33 / missions 11 / events 7，合计 51
#   —— 主要出现在 decisions 里，归到 events 类会让 `--only events` 语义失真。
#   同理 `required_missions` 全在 missions/（4722 次），也是跨文件的引用关系。
CATEGORY_OF_CHECK = {
    "check_events": "events",
    "check_on_action_duplicates": "events",
    "check_missions": "crossrefs",
    "check_textures": "crossrefs",
    "check_loc": "loc",
    "check_loc_coverage": "loc",
    "check_modifiers": "modifiers",
    "check_variables": "variables",
    "check_flags": "flags",
    "check_names": "names",
}

# namespace 合法：只用字母与下划线，不能含数字（vanilla 364 个无一含数字）
NAMESPACE_RE = re.compile(r"^[A-Za-z_]+$")
SET_VAR_RE = re.compile(r"set_variable")
FLAG_SET = {"set_country_flag", "set_global_flag", "set_province_flag"}
FLAG_CLEAR = {"clr_country_flag", "clr_global_flag", "clr_province_flag"}
FLAG_CHECK = {
    "has_country_flag", "has_global_flag", "has_province_flag",
    "has_country_flag_value", "set_variable", "clr_country_flag",
}
# 事件文件里这些 key 的值是事件 ID 引用
EVENT_REF_KEYS = {"country_event", "province_event", "event", "news_event"}
# 选项内不允许再套一层 effect = { }
FORBIDDEN_IN_OPTION = {"effect"}
# EU4 用得到的图片扩展名（texture/picture 检查用）
IMAGE_EXTS = (".tga", ".dds", ".png", ".jpg", ".jpeg", ".bmp")


# 每个错误 code 对应的「怎么改」提示（供 --json 的 usability_hint 使用）。
# 只写可操作的动作，不写「请检查」这类空话。未知 code 时 hint 为 None。
USABILITY_HINT = {
    "bad-namespace": "namespace 只保留字母与下划线，去掉数字后重命名（事件 id 前缀要同步改）",
    "id-namespace-mismatch": "让事件 id 前缀与本文件 namespace 完全一致，或统一改 namespace",
    "effect-in-option": "删掉 option 里的 effect = { } 这层包裹，把效果直接写在 option 下",
    "no-mtth-no-triggered-only": "若由决议/脚本触发，加 is_triggered_only = yes；若随机触发，加 mean_time_to_happen",
    "unclosed-block": "在该行对应的块末尾补上缺失的 }",
    "stray-close": "删掉多余的 }，或检查上一处是否漏了开块",
    "bom": "用无 BOM 的 UTF-8 重存该文件（Python: open(..., encoding='utf-8')）",
    "encoding": "统一转成无 BOM 的 UTF-8 编码",
    "missing-bom": "用 UTF-8 with BOM 重存该 .yml（Python: encoding='utf-8-sig'）",
    "bad-loc-line": "本地化行格式应为 `KEY:0 \"value\"`（冒号不是等号，值必须有双引号）",
    "unquoted-loc-value": "给该键的值加上双引号",
    "missing-loc-key": "在 localisation/*_l_english.yml 里补上该键（或改用已存在的键）",
    "wrong-position": "把该名字换成正确类型的写法（触发器→判断条件，效果→add_xxx）",
    "unknown-name": "检查拼写；若确认合法，重新生成 reference/（cwt 可能滞后于版本）",
    "mission-ref": "确认该任务 ID 在 missions/ 里定义过；若引用的是 vanilla 任务则忽略",
    "mission-self-require": "从 required_missions 里删掉对自身的引用",
    "mission-cycle": "断开环中任意一条 required_missions 依赖",
    "mission-duplicate": "合并重复定义的任务，只保留一处",
    "on-action-duplicate": "把同一钩子的多处定义合并成一处",
    "var-uninitialized": "在使用前先 set_variable 初始化，或确认该变量由别的 mod 提供",
    "var-unused": "若无用则删除；若将来要用，保留并在注释里说明",
    "flag-not-set": "确认该旗标在别处被 set；若本 mod 内应有，补上 set_*_flag",
    "flag-never-cleared": "若是一次性标记可忽略；否则补 clr_*_flag",
    "modifier-ref": "确认修正名拼写；若来自 vanilla 则忽略",
    "event-ref": "确认事件 ID 拼写；若来自 vanilla 或其他 mod 则忽略",
    "parse-crash": "该文件可能编码损坏，用编辑器另存为 UTF-8 后重试",
    "unused-op": "该运算符在 EU4 脚本里无效，改用 = 或去掉",
    "texture-path": "确认图片文件确实放在 mod 目录下（路径用 gfx/ 下相对路径，不用绝对路径/反斜杠）",
    "sprite-ref": "若 sprite 来自 vanilla 可忽略；若自绘，需在 interface/*.gfx 里注册 name = \"<sprite>\"",
}


class Issue:
    __slots__ = ("severity", "category", "path", "line", "col", "code", "message")

    def __init__(self, severity, category, path, line, col, code, message):
        self.severity = severity
        self.category = category
        self.path = path
        self.line = line
        self.col = col
        self.code = code
        self.message = message

    def __str__(self):
        sev = {"error": "E", "warn": "W", "info": "I"}[self.severity]
        return f"{sev} [{self.category}] {self.path}:{self.line}: {self.message}"

    def usability_hint(self) -> str | None:
        """这条错误该怎么改（供 --json 输出与工具消费）。"""
        return USABILITY_HINT.get(self.code)

    def as_dict(self, reference: dict | None = None, mod_defined: set[str] | None = None):
        d = {
            "severity": self.severity, "category": self.category,
            "path": self.path, "line": self.line, "col": self.col,
            "code": self.code, "message": self.message,
            "usability_hint": self.usability_hint(),
        }
        if reference and self.code in ("unknown-name", "wrong-position"):
            d["did_you_mean"] = suggest_names(self.message, reference, mod_defined)
        return d


def suggest_names(message: str, reference: dict, mod_defined: set[str] | None = None,
                  limit: int = 3) -> list[str]:
    """从错误消息里抽出名字，给出拼写近似候选（did_you_mean）。

    为什么需要它：60% 的「未知名字」其实是拼写错（`treasry` / `add_prestge`）。
    直接给出候选比让用户去翻 3000 条 JSON 白名单高效得多。

    实现用 difflib（标准库），不做自定义编辑距离 —— 不重复造轮子。
    只在该名字确属「疑似拼错」的 code 上调用（unknown-name / wrong-position），
    避免给确定合法的名字也塞候选。

    ★ 候选来源 = 白名单（effects/triggers）+ 本 mod 自定义名。
      后者很重要：拼错自己刚定义的 scripted_effect 时，候选应指向它而不是 vanilla。
    """
    import difflib

    # 从消息里取第一个被引号包住的名字（各处的消息格式都是 'name' 或 "name"）
    m = re.search(r"['\"]([a-z_][a-z0-9_]*)['\"]", message)
    if not m:
        return []
    name = m.group(1)

    pool: list[str] = []
    for bucket in ("effects", "triggers"):
        pool.extend(reference.get(bucket, {}).keys())
    if mod_defined:
        pool.extend(mod_defined)

    # 去重并保留顺序
    seen: set[str] = set()
    uniq = [x for x in pool if not (x in seen or seen.add(x))]

    return difflib.get_close_matches(name, uniq, n=limit, cutoff=0.6)


class LintResult:
    def __init__(self):
        self.issues: list[Issue] = []
        self.files_scanned = 0
        self.loc_files = 0
        self.counts: Counter[str] = Counter()
        self.stats: dict = {}

    def add(self, *args):
        self.issues.append(Issue(*args))

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == "warn"]

    @property
    def infos(self):
        return [i for i in self.issues if i.severity == "info"]


# --------------------------------------------------------------------------- #
# 收集文件
# --------------------------------------------------------------------------- #
def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def collect_mod_files(mod_dir: str) -> tuple[list[str], list[str]]:
    """返回 (脚本 .txt, 本地化 .yml)。排除 descriptor/README 等非脚本 txt。"""
    scripts: list[str] = []
    locs: list[str] = []
    skip_names = {"descriptor.mod", "thumbnail.png"}
    for root, _sub, files in os.walk(mod_dir):
        for f in sorted(files):
            full = os.path.join(root, f)
            low = f.lower()
            if low.endswith(".txt"):
                # 根目录的说明性 txt 不是脚本
                if root == mod_dir and (
                    "readme" in low or "workshop" in low or low == "changelog.txt"
                ):
                    continue
                if low in skip_names:
                    continue
                scripts.append(full)
            elif low.endswith(".yml"):
                locs.append(full)
    return scripts, locs


def rel(path: str, base: str) -> str:
    return os.path.relpath(path, base).replace(os.sep, "/")


# --------------------------------------------------------------------------- #
# 1. 语法与编码
# --------------------------------------------------------------------------- #
def check_syntax(scripts, mod_dir, res: LintResult) -> dict:
    trees: dict[str, pdx_parser.Block] = {}
    infos = {}
    for p in scripts:
        try:
            tree, errs, info = pdx_parser.parse_file_info(p)
        except Exception as exc:  # noqa: BLE001
            res.add("error", "syntax", rel(p, mod_dir), 0, 0, "parse-crash",
                    f"解析器异常 {type(exc).__name__}: {exc}")
            continue
        trees[p] = tree
        infos[p] = info
        for e in errs:
            # 脚本 .txt 有 BOM 是 error（vanilla 词法器不处理；本机容错来自补丁，不能依赖）
            if e.code == "bom":
                res.add("error", "syntax", rel(p, mod_dir), e.line, e.col, "bom",
                        "脚本 .txt 以 BOM 开头 —— vanilla 词法器不处理 BOM，会导致首行解析异常")
            else:
                res.add(e.severity if e.severity in ("error", "warn") else "warn",
                        "syntax", rel(p, mod_dir), e.line, e.col, e.code, e.message)
        res.files_scanned += 1
    res.stats["syntax_trees"] = len(trees)
    return {"trees": trees, "infos": infos}


# --------------------------------------------------------------------------- #
# 2. 事件
# --------------------------------------------------------------------------- #
def check_events(ctx: dict, mod_dir: str, res: LintResult) -> None:
    trees = ctx["trees"]
    ns_by_file: dict[str, str] = {}
    for p, tree in trees.items():
        ns = None
        for e in tree.entries:
            if getattr(e, "key", "") == "namespace" and e.scalar:
                ns = e.scalar
                break
        if ns is None:
            continue
        ns_by_file[p] = ns
        if not NAMESPACE_RE.match(ns):
            res.add("error", "events", rel(p, mod_dir), 1, 1, "bad-namespace",
                    f"namespace {ns!r} 含数字或其他非法字符 —— 会报 Incorrect namespace event id，"
                    f"事件被丢弃且 buff 静默失效（vanilla 364 个 namespace 无一含数字）")

    for p, tree in trees.items():
        r = rel(p, mod_dir)
        ns = ns_by_file.get(p)
        for e in tree.entries:
            key = getattr(e, "key", "")
            if key not in ("country_event", "province_event", "news_event"):
                continue
            blk = e.block
            if blk is None:
                continue
            eid = blk.first_scalar("id")
            # id 前缀必须与 namespace 一致
            if eid and ns:
                prefix = eid.rsplit(".", 1)[0] if "." in eid else eid
                if prefix != ns:
                    res.add("error", "events", r, e.line, 1, "id-namespace-mismatch",
                            f"事件 id {eid!r} 的前缀 {prefix!r} 与本文件 namespace {ns!r} 不一致")
            # 选项内套 effect = {}
            for opt in blk.find_blocks("option"):
                for ent in opt.pairs():
                    if ent.key in FORBIDDEN_IN_OPTION:
                        res.add("error", "events", r, ent.line, ent.col, "effect-in-option",
                                "选项内不能再套 effect = { } —— 会报 Unknown effect type. Key: effect；直接写效果")
            # 决议触发的事件应 is_triggered_only
            if blk.get("is_triggered_only") is None and blk.get("mean_time_to_happen") is None:
                res.add("warn", "events", r, e.line, 1, "no-mtth-no-triggered-only",
                        f"事件 {eid or '<无 id>'} 既无 is_triggered_only 也无 mean_time_to_happen —— 会永不触发")

    # 事件引用 vs 定义
    defined: set[str] = set()
    for p, tree in trees.items():
        for e in tree.entries:
            if getattr(e, "key", "") in ("country_event", "province_event", "news_event"):
                blk = e.block
                if blk is not None:
                    eid = blk.first_scalar("id")
                    if eid:
                        defined.add(eid)
    for p, tree in trees.items():
        r = rel(p, mod_dir)
        for e in tree.walk():
            if e.key in EVENT_REF_KEYS and e.scalar and "." in e.scalar:
                if e.scalar not in defined:
                    res.add("info", "events", r, e.line, e.col, "event-ref",
                            f"引用了事件 {e.scalar!r}，本 Mod 内未定义（可能来自 vanilla 或其他 Mod）")


# --------------------------------------------------------------------------- #
# 3. 本地化
# --------------------------------------------------------------------------- #
def check_loc(locs, mod_dir, res: LintResult) -> dict[str, list]:
    keys: dict[str, list] = defaultdict(list)
    lang_files: dict[str, list[str]] = defaultdict(list)
    for p in locs:
        try:
            loc = loc_parser.parse_localisation_file_info(p)
        except Exception as exc:  # noqa: BLE001
            res.add("error", "loc", rel(p, mod_dir), 0, 0, "loc-crash", str(exc))
            continue
        res.loc_files += 1
        if loc.lang:
            lang_files[loc.lang].append(p)
        for e in loc.errors:
            if e.code == "missing-bom":
                res.add("error", "loc", rel(p, mod_dir), e.line, e.col, "missing-bom",
                        "本地化 .yml 缺少 UTF-8 BOM（vanilla 578 个 .yml 全部带 BOM）")
            else:
                res.add(e.severity if e.severity in ("error", "warn") else "warn",
                        "loc", rel(p, mod_dir), e.line, e.col, e.code, e.message)
        for entry in loc.entries:
            keys[entry.key].append(entry)
    res.stats["loc_langs"] = {k: len(v) for k, v in lang_files.items()}
    return keys


def check_loc_coverage(keys: dict[str, list], ctx: dict, mod_dir: str, res: LintResult,
                       vanilla_keys: set[str] | None = None) -> None:
    """脚本引用的 loc key 必须有定义。

    ★ 必须合并 vanilla 的 .yml 键表（约 13.7 万），否则所有引用原版文本的键
      都会被报成「缺失」—— 满屏假错。这正是我们要避开的那类失败。
    """
    trees = ctx["trees"]
    vanilla_keys = vanilla_keys or set()
    needed: set[str] = set()
    for p, tree in trees.items():
        for e in tree.walk():
            if e.key in ("title", "desc", "name") and e.scalar:
                needed.add(e.scalar)
    missing = sorted(k for k in needed if k not in keys and k not in vanilla_keys)

    res.stats["loc_keys_referenced"] = len(needed)
    res.stats["loc_keys_mod"] = len(keys)
    res.stats["loc_keys_missing"] = len(missing)

    for k in missing[:40]:
        res.add("warn", "loc", "<mod>", 0, 0, "missing-loc-key",
                f"脚本引用了本地化键 {k!r}，但 Mod 与 vanilla 的 .yml 里都没有定义")


def load_vanilla_loc_keys(game: str | None) -> set[str]:
    """加载 vanilla 全部 .yml 键。实测 578 文件 / 136,579 键 / 约 1 秒。"""
    out: set[str] = set()
    if not game:
        return out
    d = os.path.join(game, "localisation")
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".yml"):
            continue
        try:
            out |= loc_parser.parse_localisation_file_info(os.path.join(d, f)).keys()
        except Exception:  # noqa: BLE001
            continue
    return out


# --------------------------------------------------------------------------- #
# 4. 修正
# --------------------------------------------------------------------------- #
def check_modifiers(ctx: dict, mod_dir: str, res: LintResult) -> dict:
    trees = ctx["trees"]
    defined: dict[str, int] = {}
    for p, tree in trees.items():
        low = p.lower()
        if not low.endswith(".txt"):
            continue
        # 修正定义文件：common/event_modifiers 或 common/static_modifiers
        if "event_modifiers" in low or "static_modifiers" in low or "triggered_modifiers" in low:
            for e in tree.entries:
                if getattr(e, "op", "") and e.key:
                    defined[e.key] = e.line

    used: set[str] = set()
    for p, tree in trees.items():
        r = rel(p, mod_dir)
        for e in tree.walk():
            if e.key in ("add_country_modifier", "add_province_modifier",
                         "add_trade_modifier", "add_disaster_modifier"):
                blk = e.block
                if blk is not None:
                    n = blk.first_scalar("name")
                    if n:
                        used.add(n)
                        if n not in defined:
                            res.add("info", "modifiers", r, e.line, e.col, "modifier-ref",
                                    f"引用了修正 {n!r}，本 Mod 内未定义（可能来自 vanilla）")
    unused = sorted(set(defined) - used)
    res.stats["modifier_defined"] = len(defined)
    res.stats["modifier_used"] = len(used)
    res.stats["modifier_unused"] = len(unused)
    return {"defined": defined, "used": used, "unused": unused}


# ----------------------------------------------------------------------------- #
# 5. 变量
# ----------------------------------------------------------------------------- #
def check_variables(ctx: dict, mod_dir: str, res: LintResult) -> dict:
    trees = ctx["trees"]
    sets: dict[str, list[tuple[str, int]]] = defaultdict(list)
    reads: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for p, tree in trees.items():
        r = rel(p, mod_dir)
        for path, e in tree.walk_paths():
            if e.key in ("set_variable", "change_variable", "subtract_variable",
                         "multiply_variable", "divide_variable", "clamp_variable"):
                blk = e.block
                if blk is not None:
                    which = blk.first_scalar("which")
                    if which:
                        sets[which].append((r, e.line))
            elif e.key == "check_variable":
                blk = e.block
                if blk is not None:
                    which = blk.first_scalar("which")
                    if which:
                        reads[which].append((r, e.line))
            elif e.key in ("has_variable", "variable_arithmetic_trigger"):
                blk = e.block
                if blk is not None:
                    which = blk.first_scalar("which")
                    if which:
                        reads[which].append((r, e.line))

    only_read = sorted(set(reads) - set(sets))
    for name in only_read:
        f, ln = reads[name][0]
        res.add("warn", "variables", f, ln, 1, "var-uninitialized",
                f"变量 {name!r} 被读取但本 Mod 内从未 set_variable —— 值为 0 或未定义")

    only_set = sorted(set(sets) - set(reads))
    for name in only_set:
        f, ln = sets[name][0]
        res.add("info", "variables", f, ln, 1, "var-unused",
                f"变量 {name!r} 只被设置，从未被读取")

    res.stats["vars_set"] = len(sets)
    res.stats["vars_read"] = len(reads)
    return {"set": sets, "read": reads}


# --------------------------------------------------------------------------- #
# 6. 旗标
# --------------------------------------------------------------------------- #
def check_flags(ctx: dict, mod_dir: str, res: LintResult) -> dict:
    trees = ctx["trees"]
    sets: dict[str, list] = defaultdict(list)
    clears: dict[str, list] = defaultdict(list)
    checks: dict[str, list] = defaultdict(list)

    for p, tree in trees.items():
        r = rel(p, mod_dir)
        for e in tree.walk():
            if e.scalar is None:
                continue
            if e.key in FLAG_SET:
                sets[e.scalar].append((r, e.line))
            elif e.key in FLAG_CLEAR:
                clears[e.scalar].append((r, e.line))
            elif e.key in FLAG_CHECK and e.key != "set_variable":
                checks[e.scalar].append((r, e.line))

    never_cleared = sorted(set(sets) - set(clears))
    for name in never_cleared:
        f, ln = sets[name][0]
        res.add("info", "flags", f, ln, 1, "flag-never-cleared",
                f"旗标 {name!r} 被设置但从未清除 —— 若设计上是一次性标记可忽略")
    checked_but_not_set = sorted(set(checks) - set(sets))
    for name in checked_but_not_set:
        f, ln = checks[name][0]
        res.add("warn", "flags", f, ln, 1, "flag-not-set",
                f"旗标 {name!r} 被检查但本 Mod 内从未设置 —— 条件恒为 false")

    res.stats["flags_set"] = len(sets)
    res.stats["flags_cleared"] = len(clears)
    return {"set": sets, "clear": clears, "check": checks}


def check_on_action_duplicates(ctx: dict, mod_dir: str, res: LintResult) -> None:
    """on_action 重复定义检查。

    ★ 先查清引擎语义再定分级（本轮实测结论）：
      1. vanilla 的 `common/on_actions/00_on_actions.txt` 有 256 个顶层钩子，
         **零重复定义** —— 所以「重复定义」在 vanilla 里不是常态。
      2. `on_development_effect` / `on_advisor_removed_effct` 看着像重复，
         实际是顶层钩子块内**调用的 scripted_effect**（带 `type = adm` 参数），
         不是钩子名。用行首正则会把它们误算成钩子 —— 又一处「名字位由容器决定」。
      3. 铁律已知：on_action 在 EU4 里是**合并**的（实测 42 个工坊 mod 共用
         on_startup 且相安无事），所以「跨 mod 重复」不是问题。
         但**同一个 mod 内**重复定义同一钩子，语义取决于引擎的合并顺序，
         作者容易误以为后一份会覆盖前一份 —— 值得 warn。
    """
    seen: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for p, tree in ctx["trees"].items():
        low = p.lower().replace(os.sep, "/")
        if "on_actions" not in low:
            continue
        for e in tree.entries:
            if getattr(e, "op", "") and e.key and isinstance(e.value, pdx_parser.Block):
                seen[e.key].append((rel(p, mod_dir), e.line))
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    res.stats["on_action_hooks"] = len(seen)
    res.stats["on_action_duplicates"] = len(dup)
    for k, v in dup.items():
        f, ln = v[1]
        res.add("warn", "events", f, ln, 1, "on-action-duplicate",
                f"钩子 {k!r} 在本 Mod 内被定义了 {len(v)} 次（首次 {v[0][0]}:{v[0][1]}）。"
                f"EU4 的 on_action 是合并语义，重复定义的实际生效顺序取决于引擎，"
                f"建议合并成一处")


def check_missions(ctx: dict, mod_dir: str, res: LintResult) -> None:
    """任务树的「定义 vs 引用」检查。

    为什么它值得做（校验者判断）
    ---------------------------
    指向不存在的任务 ID 正是本工具存在的原因：**静默失效**。
    `complete_mission = xxx` 引用了不存在的任务 → 引擎不报错、不闪退，
    只是这一步效果什么都没发生。与事件引用检查完全同构，成本极低。

    实测 vanilla 结构（v1.37.5.0，`missions/*.txt` 228 个文件）
    --------------------------------------------------------
      · 顶层块 = 任务系列（series），1422 个；系列内每个块是一个任务，共 7343 个
      · 引用形式一：`complete_mission = <任务ID>`      全库 51 处
      · 引用形式二：`required_missions = { <任务ID> }`  全库 4724 处（**不是罕见特性**）
        所以「EU4 任务树没有显式前置」是错的 —— 有，而且用得很多。

    分级（沿用「误报伤信任」的铁律）
    ------------------------------
      · 引用的 ID 本 mod 未定义 → `info`。措辞必须**如实说出能力边界**：
        本工具**没有索引 vanilla 的任务 ID**，所以它分不清
        「指向 vanilla 里存在但你查不到的任务」与「指向谁都找不到的错 ID」。
        不能写「引用了不存在的任务」—— 那是断言了自己做不到的事。
      · `required_missions` 引用自身 → `warn`（自环必然不可完成）
      · `required_missions` 形成多节点环（A→B→A）→ `warn`（环上所有任务都不可完成）
      · 同一 mod 内重复定义同一任务 ID → `warn`（后者覆盖前者，容易误以为都生效）

    ★ 归类为 `crossrefs`，不是 `events`
    ------------------------------------
    实测 vanilla `complete_mission =` 的分布（结构化 key，不含注释）：
        decisions 33 / missions 11 / events 7   合计 51
    它主要出现在 **decisions** 里。归到 `events` 会让 `--only events` 的语义失真
    —— 用户以为在查「事件相关问题」，实际漏掉了三分之二的引用点。
    这是按「引用的覆盖面」归类，不是按「谁调用这个函数」归类。
    """
    # 1. 收集本 mod 定义的任务 ID（missions/ 下的顶层 series 的**直接子块**）
    defined: dict[str, list[tuple[str, int]]] = defaultdict(list)
    series_names: set[str] = set()
    mission_files: list[str] = []

    for p, tree in ctx["trees"].items():
        # ★ 只认「mod 目录内的相对路径」以 missions/ 开头。
        #   不能对整个绝对路径做 `/missions/` 子串判断 ——
        #   如果 mod 目录本身就叫 missions（例如 fixture `tests/fixtures/missions/`），
        #   那条判断会把 events/ 下的文件也当成任务定义。实测踩过。
        relpath = rel(p, mod_dir).lower().replace(os.sep, "/")
        if not relpath.startswith("missions/"):
            continue
        mission_files.append(p)
        r = rel(p, mod_dir)
        for e in tree.entries:
            if not (getattr(e, "op", "") and isinstance(e.value, pdx_parser.Block)):
                continue
            series_names.add(e.key)
            # 系列内的每个块 = 一个任务
            for sub in e.value.entries:
                if not (getattr(sub, "op", "") and isinstance(sub.value, pdx_parser.Block)):
                    continue
                # 跳过系列的元数据字段（slot / generic / ai / potential 等）
                if sub.key in MISSION_SERIES_META_KEYS:
                    continue
                defined[sub.key].append((r, sub.line))

    if not mission_files:
        res.stats["missions_defined"] = 0
        return

    res.stats["missions_defined"] = len(defined)
    res.stats["mission_series"] = len(series_names)

    # 2. 收集引用，以及「任务 → 前置任务」的有向边（用于环检测）
    refs: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    edges: dict[str, set[str]] = defaultdict(set)       # owner -> set(required)
    edge_lines: dict[tuple[str, str], tuple[str, int]] = {}

    for p, tree in ctx["trees"].items():
        r = rel(p, mod_dir)
        for path, e in tree.walk_paths():
            # complete_mission = xxx
            if e.key == "complete_mission" and e.scalar:
                refs[e.scalar].append((r, e.line, "complete_mission"))
            # required_missions = { a b c }
            elif e.key == "required_missions" and isinstance(e.value, pdx_parser.Block):
                owner = path[-1] if path else ""
                for sub in e.value.entries:
                    if getattr(sub, "is_bare", False) and sub.key:
                        refs[sub.key].append((r, sub.line, "required_missions"))
                        if owner:
                            edges[owner].add(sub.key)
                            edge_lines[(owner, sub.key)] = (r, sub.line)

    res.stats["mission_refs"] = len(refs)
    res.stats["mission_edges"] = sum(len(v) for v in edges.values())

    # 3. 引用了但本 mod 未定义 → info（措辞必须诚实：工具没索引 vanilla 任务）
    for name, hits in sorted(refs.items()):
        if name in defined:
            continue
        r, ln, kind = hits[0]
        res.add("info", "crossrefs", r, ln, 1, "mission-ref",
                f"无法确认任务 ID {name!r} 是否存在（本工具未索引 vanilla 任务）；"
                f"若这是自定义任务，请确认它已在本 Mod 的 missions/ 里定义"
                f"（引用形式：{kind}）")

    # 4. required_missions 自环 → warn
    for p, tree in ctx["trees"].items():
        r = rel(p, mod_dir)
        for path, e in tree.walk_paths():
            if e.key != "required_missions" or not isinstance(e.value, pdx_parser.Block):
                continue
            owner = path[-1] if path else ""
            for sub in e.value.entries:
                if getattr(sub, "is_bare", False) and sub.key == owner:
                    res.add("warn", "crossrefs", r, sub.line, 1, "mission-self-require",
                            f"任务 {owner!r} 在 required_missions 里引用了自身 —— "
                            f"自环会让它永远不可完成")

    # 5. 多节点环检测（A→B→A）→ warn
    #    只对「本 mod 内定义的任务」建图，外部 ID 视为叶子（无出边），
    #    避免把 vanilla 的未知依赖也算进环里（那是假错）。
    #    长度 1 的环（自环）已由第 4 步单独报告，这里跳过，否则会重复报。
    for cycle in find_cycles(edges, scope=set(defined)):
        if len(cycle) < 2:
            continue
        nodes = " → ".join(cycle + [cycle[0]])
        loc = edge_lines.get((cycle[0], cycle[1]))
        if loc:
            r, ln = loc
        else:
            first = next(iter(mission_files), "")
            r, ln = (rel(first, mod_dir) if first else "<mod>"), 1
        res.add("warn", "crossrefs", r, ln, 1, "mission-cycle",
                f"required_missions 形成环：{nodes} —— 环上的任务全部永远不可完成")

    # 6. 同 mod 内重复定义同一任务 ID → warn
    for name, hits in defined.items():
        if len(hits) > 1:
            r, ln = hits[1]
            res.add("warn", "crossrefs", r, ln, 1, "mission-duplicate",
                    f"任务 {name!r} 在本 Mod 内被定义了 {len(hits)} 次"
                    f"（首次 {hits[0][0]}:{hits[0][1]}）—— 后者覆盖前者，容易误以为都生效")


# 任务系列块里的元数据字段，不是任务本体。
# 实测（vanilla 00_Generic_missions.txt）：series 内有 slot / generic / ai / potential
# 这类字段，若一并算作任务，2 个任务会数成 4 个。
MISSION_SERIES_META_KEYS = {
    "slot", "generic", "ai", "potential", "has_country_shield", "is_repeatable",
    "is_mission_series", "ai_weight", "weight",
}


def find_cycles(edges: dict[str, set[str]], scope: set[str],
                max_cycles: int = 20) -> list[list[str]]:
    """在「任务 → 前置任务」有向图里找环，返回去重后的环列表。

    为什么只查 scope 内的节点
    ------------------------
    `required_missions` 可以引用 vanilla 的任务 ID，我们对那些 ID **没有出边信息**
    （不知道 vanilla 那个任务又依赖谁）。把它们纳入图会得出不可靠的环 ——
    那是典型的假错。

    实现：迭代 DFS，用栈记录当前路径。遇到已在栈中的节点即构成环，
    把环切出来。为控制输出量，最多返回 `max_cycles` 个环，并按
    「起点为环内字典序最小值」做规范化，避免 A→B→A 与 B→A→B 重复报告。
    """
    found: list[list[str]] = []
    seen_cycles: set[tuple[str, ...]] = set()

    def canonical(cyc: list[str]) -> tuple[str, ...]:
        """把环旋转到「以字典序最小的节点开头」，用于去重。"""
        if not cyc:
            return ()
        i = cyc.index(min(cyc))
        return tuple(cyc[i:] + cyc[:i])

    def dfs(node: str, path: list[str], on_path: set[str]) -> None:
        if len(found) >= max_cycles:
            return
        for nxt in sorted(edges.get(node, ())):
            if nxt not in scope:        # 外部任务：无出边，不可能成环
                continue
            if nxt in on_path:
                idx = path.index(nxt)
                cyc = path[idx:]
                key = canonical(cyc)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    found.append(list(key))
                continue
            if nxt in path:
                continue
            path.append(nxt)
            on_path.add(nxt)
            dfs(nxt, path, on_path)
            on_path.discard(nxt)
            path.pop()

    for start in sorted(scope):
        dfs(start, [start], {start})
        if len(found) >= max_cycles:
            break
    return found


def check_textures(ctx: dict, mod_dir: str, game: str | None, res: LintResult) -> None:
    """检查 icon / picture 等字段引用的图片文件是否真的存在。

    为什么值得做
    -----------
    EU4 里图标路径写错**不会报错**，只是图标空白 —— 又一个静默失败。
    典型三种错：
      · `icon = mission_trade` 但 `interface/*.gfx` 里没注册这个 sprite
      · `picture = "thumbnail.png"` 但 mod 目录里没有该文件
      · 路径写成绝对路径或用了反斜杠（vanilla 只认 gfx/ 下的相对路径）

    实现边界（如实说明，不外推）
    --------------------------
    只检查**能确定对应关系**的字段，不做 sprite 全链路解析：
      1. 事件/决议的 `picture = "xxx"` → 检查顶层扩展名（.tga/.dds/.png/.jpg/.bmp）
         是否存在，以及 mod/vanilla 下能否找到同名文件
      2. `.mod` / `descriptor.mod` 的 `picture = "xxx"` → 检查 mod 目录里有无该文件
      3. `icon = <sprite>` → 只做「本 mod 是否定义了该 sprite」的提示（info 级），
         **不去解析 vanilla 的 .gfx**（那是另一个规模的索引，且误报风险高）

    分级：路径明确指向文件但找不到 → warn；sprite 名本 mod 未定义 → info。
    """
    if not ctx["trees"]:
        return

    # 收集 mod 目录里实际存在的图片文件（基名，小写）
    mod_images: set[str] = set()
    for root, _sub, files in os.walk(mod_dir):
        for f in files:
            if f.lower().endswith(IMAGE_EXTS):
                mod_images.add(f.lower())

    # 收集本 mod 定义的 sprite 名（interface/*.gfx 里的 name = "xxx"）
    mod_sprites: set[str] = set()
    for p, tree in ctx["trees"].items():
        low = p.lower().replace(os.sep, "/")
        if "interface/" not in low and not low.endswith(".gfx"):
            continue
        for e in tree.walk():
            if e.key == "name" and e.scalar:
                mod_sprites.add(e.scalar)

    checked = 0
    for p, tree in ctx["trees"].items():
        r = rel(p, mod_dir)
        for e in tree.walk():
            # ① picture = "xxx.png"（事件/决议/`.mod`）
            if e.key == "picture" and e.scalar:
                val = e.scalar.strip()
                checked += 1
                if val.lower().endswith(IMAGE_EXTS):
                    base = os.path.basename(val).lower()
                    if base not in mod_images:
                        res.add("warn", "crossrefs", r, e.line, e.col, "texture-path",
                                f"picture 指向 {val!r}，但 Mod 目录里找不到该文件 "
                                f"（{', '.join(IMAGE_EXTS)}）—— 图标会显示空白")
                # 非图片扩展名的 picture 一般是 DLC 内建图，不检查
            # ② icon = <sprite>（任务/理念/决议图标）
            elif e.key == "icon" and e.scalar and not e.scalar.lower().endswith(IMAGE_EXTS):
                sprite = e.scalar.strip()
                checked += 1
                if sprite and sprite not in mod_sprites:
                    res.add("info", "crossrefs", r, e.line, e.col, "sprite-ref",
                            f"icon {sprite!r} 未在本 Mod 的 interface/*.gfx 中定义；"
                            f"若它来自 vanilla 则正常，否则图标会空白")

    res.stats["texture_fields_checked"] = checked
    res.stats["mod_images"] = len(mod_images)
    res.stats["mod_sprites"] = len(mod_sprites)


# --------------------------------------------------------------------------- #
# 7. 名字位（按容器类型判定）
# --------------------------------------------------------------------------- #
CONTAINER_TRIGGER = {
    "trigger", "limit", "potential", "allow", "can_take", "is_valid",
    "hidden_trigger", "trigger_switch", "allow_war", "on_trigger",
    "mean_time_to_happen", "ai_chance", "ai_will_do", "weight", "score",
}
CONTAINER_EFFECT = {
    "immediate", "option", "hidden_effect", "effect", "after",
    "on_startup", "on_monarch_death", "on_new_monarch",
}
TRANSPARENT = {"OR", "AND", "NOT", "NOR", "NAND", "XOR", "if", "else", "else_if", "while",
               "tooltip", "custom_tooltip", "hidden_tooltip", "modifier"}
CASE_CONTAINERS = {"trigger_switch", "switch", "tag_switch"}

# 结构字段：这些 key 是容器的参数/骨架，**永远不是名字位**，不能送去查白名单。
# 例：`option = { name = "xxx" }` 里的 name 是 loc key；
#     `option = { effect = { } }` 里的 effect 本身就是要报告的错误（由 events 类处理）。
STRUCTURAL_KEYS = {
    "name", "title", "desc", "picture", "highlight", "namespace", "id", "major",
    "minor", "sound", "duration", "scaled", "value", "factor", "modifier",
    "effect", "tooltip", "custom_tooltip", "hidden_tooltip", "localisation",
    "icon", "color", "size", "position", "tag", "country", "province",
    "date", "year", "month", "day", "OR", "AND", "NOT",
    # 语境容器本身在「被当作名字」时也常出现（option 里可以再放 trigger）：
    "trigger", "limit", "potential", "allow", "immediate", "option",
}

# 白名单条目状态 → lint 级别。★ 唯一的分级出处（与 build_reference 保持一致）
REF_SEVERITY: dict[str, str | None] = {
    "confirmed": None,
    "cwt_only": "info",
    "corpus_only": "warn",
    "conflict": "warn",
}


def position_kind(path: tuple[str, ...]) -> str | None:
    """★ 核心：名字位由容器类型决定（SKILL.md §1.0）。"""
    for key in reversed(path):
        if key in CASE_CONTAINERS:
            return None
        if key in TRANSPARENT:
            continue
        if key in CONTAINER_TRIGGER:
            return "trigger"
        if key in CONTAINER_EFFECT:
            return "effect"
        return None
    return None


def collect_mod_defined_names(ctx: dict) -> tuple[set[str], set[str]]:
    """★ 本 Mod 自己定义的 scripted_effect / scripted_trigger 名。

    它们不出现在 vanilla 白名单里（因为是自己写的），但**在 mod 里完全合法**。
    若不收集，lint 会对每个自定义脚本效果报「白名单查不到」—— 假错，
    正是我们要避免的。
    """
    effects: set[str] = set()
    triggers: set[str] = set()
    for p, tree in ctx["trees"].items():
        low = p.lower().replace(os.sep, "/")
        if "scripted_effects" in low:
            for e in tree.entries:
                if getattr(e, "op", "") and e.key:
                    effects.add(e.key)
        elif "scripted_triggers" in low:
            for e in tree.entries:
                if getattr(e, "op", "") and e.key:
                    triggers.add(e.key)
    return effects, triggers


def check_names(ctx: dict, reference: dict, mod_dir: str, res: LintResult) -> None:
    effects = reference.get("effects", {})
    triggers = reference.get("triggers", {})
    trees = ctx["trees"]
    mod_eff, mod_trg = collect_mod_defined_names(ctx)
    res.stats["mod_defined_effects"] = len(mod_eff)
    res.stats["mod_defined_triggers"] = len(mod_trg)

    for p, tree in trees.items():
        r = rel(p, mod_dir)
        for path, e in tree.walk_paths():
            kind = position_kind(path)
            if kind is None or e.is_bare:
                continue
            key = e.key
            if not key or key in TRANSPARENT or key in STRUCTURAL_KEYS:
                continue
            # 只在「疑似名字」的形态上检查（其余交给其他类别）
            if not re.match(r"^[a-z_][a-z0-9_]*$", key):
                continue

            if kind == "effect":
                row = effects.get(key)
                opposite = triggers.get(key)
                if row is None and key in mod_eff:
                    continue          # 本 Mod 自定义的 scripted_effect，合法
            else:
                row = triggers.get(key)
                opposite = effects.get(key)
                if row is None and key in mod_trg:
                    continue          # 本 Mod 自定义的 scripted_trigger，合法

            if row is not None:
                status = row.get("status", "confirmed")
                sev = REF_SEVERITY.get(status)
                if sev:
                    res.add(sev, "names", r, e.line, e.col, f"name-{status}",
                            f"{key!r} 在{'效果' if kind=='effect' else '触发器'}位，"
                            f"白名单状态={status}（{REF_STATUS_HINT.get(status, '')}）")
                continue

            # 不在正确白名单里
            if opposite is not None:
                res.add("error", "names", r, e.line, e.col, "wrong-position",
                        f"{key!r} 是{'触发器' if kind=='effect' else '效果'}，"
                        f"却写在了{'效果' if kind=='effect' else '触发器'}位 —— "
                        f"这是 treasury 那类坑：游戏会报 Unknown {'effect' if kind=='effect' else 'trigger'} type，"
                        f"整条链静默失效")
            else:
                # 两处都没有 → warn（cwt 可能滞后，不能报 error）
                res.add("warn", "names", r, e.line, e.col, "unknown-name",
                        f"{key!r} 在{'效果' if kind=='effect' else '触发器'}位，"
                        f"但白名单两处都查不到 —— 可能是拼写错误，也可能是 cwt 滞后于游戏版本")


REF_SEVERITY: dict[str, str | None] = {
    "confirmed": None,
    "cwt_only": "info",
    "corpus_only": "warn",
    "conflict": "warn",
}
REF_STATUS_HINT = {
    "cwt_only": "引擎定义了但 vanilla 未使用",
    "corpus_only": "语料用过但 cwt 未定义（cwt 滞后是常态）",
    "conflict": "两源结论不一，需人工核",
}


def load_reference(ref_dir: str) -> dict:
    out = {}
    for name in ("effects", "triggers", "modifiers", "on_actions"):
        p = os.path.join(ref_dir, f"{name}.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                out[name] = json.load(fh)
        else:
            out[name] = {}
    return out


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def lint_mod(mod_dir: str, game: str | None = None, ref_dir: str | None = None,
             only: set[str] | None = None) -> LintResult:
    res = LintResult()
    scripts, locs = collect_mod_files(mod_dir)

    ctx = check_syntax(scripts, mod_dir, res)

    if only is None or "events" in only:
        check_events(ctx, mod_dir, res)
        check_on_action_duplicates(ctx, mod_dir, res)

    # ★ 任务 ID 引用归在 crossrefs，不归 events。
    #   实测 vanilla `complete_mission =` 主要出现在 decisions（33/51），
    #   归到 events 会让 `--only events` 漏掉三分之二的引用点。
    if only is None or "crossrefs" in only:
        check_missions(ctx, mod_dir, res)
        check_textures(ctx, mod_dir, str(game) if game else None, res)

    keys = {}
    if only is None or "loc" in only:
        keys = check_loc(locs, mod_dir, res)
        vkeys = load_vanilla_loc_keys(game)
        res.stats["loc_keys_vanilla"] = len(vkeys)
        check_loc_coverage(keys, ctx, mod_dir, res, vkeys)

    if only is None or "modifiers" in only:
        check_modifiers(ctx, mod_dir, res)

    if only is None or "variables" in only:
        check_variables(ctx, mod_dir, res)

    if only is None or "flags" in only:
        check_flags(ctx, mod_dir, res)

    if (only is None or "names" in only) and ref_dir:
        check_names(ctx, load_reference(ref_dir), mod_dir, res)

    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EU4 Mod 静态校验")
    # ★ nargs="?"：--list-categories 是纯信息查询，不该强迫用户给一个 mod 路径。
    #   （研究侧实测：eu4_lint.py --list-categories → exit 2 "the following
    #    arguments are required: mod"，这条已修正。）
    ap.add_argument("mod", nargs="?", default=None, help="Mod 目录（--list-categories 时可省略）")
    ap.add_argument("--game", default=None, help="游戏安装目录")
    ap.add_argument("--reference", default=None, help="reference 目录（不传则查 . /reference）")
    ap.add_argument("--no-reference", action="store_true", help="跳过名字位检查")
    ap.add_argument("--only", default=None,
                    help="只跑指定类别，逗号分隔。可选：" + ",".join(DEFAULT_CATEGORIES) +
                         "。归属说明：syntax=括号/BOM/编码；events=namespace/事件ID/选项内套effect/"
                         "on_action重复；crossrefs=任务ID与前置任务引用（含所需环检测）；"
                         "loc=.yml BOM/键缺失/覆盖；modifiers=修正定义vs引用；"
                         "variables=变量读写；flags=旗标设清；names=效果/触发器名白名单"
                         "（需 reference/）")
    ap.add_argument("--list-categories", action="store_true",
                    help="列出所有类别与归属，然后退出")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--max-show", type=int, default=60, help="每类最多展示条数")
    args = ap.parse_args(argv)

    _paths.force_utf8_stdout()

    if args.list_categories:
        print("类别与归属：")
        for k in DEFAULT_CATEGORIES:
            checks = [c for c, cat in CATEGORY_OF_CHECK.items() if cat == k]
            print(f"  {k:10s} {CATEGORIES[k]}")
            for c in checks:
                print(f"             └─ {c}()")
        return 0

    if not args.mod:
        print("需要给出 Mod 目录（或用 --list-categories 查看类别归属）")
        return 2

    if not os.path.isdir(args.mod):
        print(f"Mod 目录不存在：{args.mod}")
        return 2

    only = {s.strip() for s in args.only.split(",")} if args.only else None
    unknown = (only or set()) - set(CATEGORIES)
    if unknown:
        print(f"未知类别：{sorted(unknown)}")
        print(f"可选：{list(DEFAULT_CATEGORIES)}")
        return 2
    game = args.game or _paths.find_game_dir()

    ref_dir = None
    ref_missing = False
    if not args.no_reference and (only is None or "names" in only):
        cand = args.reference or os.path.join(os.getcwd(), "reference")
        if os.path.isdir(cand):
            ref_dir = cand
        else:
            # ★ 白名单缺失不是「提示」，是「这一类检查没跑」。
            #   早期只打印一行，退出码仍按其他类别的结果算 → 用户看到 error 0
            #   就以为全查过了。实测：植入 treasury 到效果位，不带 --reference
            #   时 exit=0，带 --reference 时 exit=1。这是最危险的一类静默失败。
            ref_missing = True
            print("★ 名字位检查未启用：找不到 reference 目录")
            print(f"    查找位置：{cand}")
            print("    影响：效果/触发器名写错**不会被发现**（如 treasury 写在效果位）")
            print("    解决：先跑 build_reference.py，或用 --reference 指定目录；")
            print("          若只想查语法，显式加 --no-reference 表明这是有意跳过")

    res = lint_mod(str(args.mod), str(game) if game else None, ref_dir, only)

    if args.json:
        # did_you_mean 需要白名单；只在启用名字位检查时才传
        ref = load_reference(ref_dir) if ref_dir else None
        print(json.dumps({
            "mod": str(args.mod),
            "files": res.files_scanned,
            "loc_files": res.loc_files,
            "stats": res.stats,
            "issues": [i.as_dict(ref) for i in res.issues],
            "summary": {
                "error": len(res.errors), "warn": len(res.warnings), "info": len(res.infos),
            },
            # ★ 让消费者知道「名字位这一类没跑」，而不是拿一个 error 0 就以为全查了
            "skipped_checks": (["names(需 reference/)"] if ref_missing else []),
        }, ensure_ascii=False, indent=1))
        if res.errors:
            return 1
        return 2 if ref_missing else 0

    # 人类可读
    print(f"扫描 {res.files_scanned} 个脚本 / {res.loc_files} 个本地化文件")
    for k, v in res.stats.items():
        if k == "syntax_trees":
            continue
        print(f"  {k}: {v}")
    print()

    by_cat: dict[str, list[Issue]] = defaultdict(list)
    for i in res.issues:
        by_cat[i.category].append(i)

    for cat in CATEGORIES:
        items = by_cat.get(cat, [])
        if not items:
            continue
        e = sum(1 for i in items if i.severity == "error")
        w = sum(1 for i in items if i.severity == "warn")
        n = sum(1 for i in items if i.severity == "info")
        print(f"── {CATEGORIES[cat]}：{len(items)} 条（E{e} W{w} I{n}）")
        for i in items[:args.max_show]:
            print(f"   {i}")
        if len(items) > args.max_show:
            print(f"   … 还有 {len(items) - args.max_show} 条")
        print()

    print(f"合计：error {len(res.errors)} / warn {len(res.warnings)} / info {len(res.infos)}")
    if res.errors:
        print("判定：FAIL（存在 error，下面这些会导致静默失效）")
        for i in res.errors[:20]:
            print(f"   {i}")
        return 1
    if ref_missing:
        # ★ 不能报 PASS：名字位整类没跑，说 PASS 等于把「没查」当成「通过」
        print("判定：INCOMPLETE（无 error，但名字位检查未执行）")
        print("      error 0 只代表已执行的那几类干净，不代表全部查过")
        return 2
    print("判定：PASS（无 error）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
