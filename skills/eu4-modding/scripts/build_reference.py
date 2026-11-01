"""从三源交叉构建名称白名单 —— 抓「用错名字」的唯一手段。

三源与分工
----------
  1. **cwtools-eu4-config（MIT）** → 定**分类**。`alias[effect:xxx]` / `alias[trigger:xxx]`
     把类型显式写进模式定义，这是唯一可靠的分类来源。
  2. **vanilla 语料** → 定**用法**。给出 `文件:行` 的实证，让「这个名字长什么样」可核查。
  3. **eu4.exe 字符串表（可选，--exe）** → 置信度加成。**默认关闭**：
     exe 随版本变化会失效，且「字符串存在 ≠ 分类」，只作参考。

为什么不用 exe 做主源
--------------------
  `treasury` 在 exe 里出现多次，次数分不出它在效果表还是触发器值表。
  真正需要的分类是「按语境判定」：出现在 `limit = {}` / `trigger = {}` 内的是触发器，
  出现在效果位的是效果。cwtools 配置把这些类型显式写好了，比数偏移可靠，也更好移植。

分级（★ 误报对信任的杀伤远大于漏判）
---------------------------------
  corpus + cwt 都有        → confirmed     （语料实证 + 类型已知）
  仅 cwt 有（语料没用过）  → cwt_only      → `info`「引擎定义了但 vanilla 未使用」
  仅语料有（cwt 没定义）   → corpus_only   → **`warn`，绝不能 error**（cwt 滞后于版本是常态）
  cwt 说 A，语料语境说 B   → conflict      → `warn`，需人工核

覆盖率指标（分母必须定义清楚，否则指标会自证）
--------------------------------------------
  覆盖率 = |语料侧候选名 ∩ cwt 已分类名| / |语料侧候选名|
  分母取「语料侧」，因为取 cwt ∪ 语料的话 cwt 独有条目天然 100%，
  指标会失去意义。未覆盖的缺口才有真实含义：= 语料有、cwt 没定义。

用法
----
    python build_reference.py --cwt <cwtools-eu4-config 目录> --game <游戏目录> --out reference
    python build_reference.py --stats                 # 只打印覆盖率与归因，不写文件
    python build_reference.py --no-corpus             # 只用 cwt（出包时快）
    python build_reference.py --exe <eu4.exe>         # 附加 exe 置信度（可选）
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
import cwt_reader
import pdx_parser

__all__ = ["build", "TXT_DIRS", "TRIGGER_MARKERS", "EFFECT_MARKERS", "TRANSPARENT_KEYS"]

# 与 tests/bench_vanilla.py 保持同一口径（4 个顶目录 + customizable_localization）
TXT_DIRS = ["events", "common", "decisions", "missions", "customizable_localization"]

# ---- 语境判定 ----
# 进入「触发器位」的父 key
TRIGGER_MARKERS = {
    "trigger", "limit", "potential", "allow", "can_take", "is_valid", "hidden_trigger",
    "trigger_switch", "allow_war", "can_do", "possible", "valid", "ai_chance",
    "mean_time_to_happen", "ai_will_do", "weight", "score", "on_trigger",
}
# 进入「效果位」的父 key
EFFECT_MARKERS = {
    "immediate", "option", "hidden_effect", "effect", "after", "on_startup",
    "on_monarch_death", "on_new_monarch", "on_war", "on_peace", "on_reform",
    "events",          # on_action 里的 `events = { ns.1 ns.2 }` 是事件列表，其内是事件 ID
    "random_events",   # 同上，且子键是权重数字
}
# 透明 key：继承父语境（OR/AND/NOT 只是组合符，if/else 两种语境都用）
TRANSPARENT_KEYS = {
    "OR", "AND", "NOT", "NOR", "NAND", "XOR",
    "if", "else", "else_if", "while", "tooltip", "custom_tooltip",
    "hidden_tooltip",
}

# ★ AI 权重容器：内部是**触发器位**。
#   实测父容器分布（vanilla v1.37.5.0，2630 个 .txt 全扫）：
#     mean_time_to_happen 2494 / ai_chance 2437 / ai_will_do 1925 / chance 1046 /
#     selection_weight 645 / spawn_chance 482 / movement_evaluation 348 / progress 332 /
#     target_province_weights 207 / embracement_speed 107 / ai 92 /
#     province_independence_weight 82 / ai_weight 49 / ai_priority 19 /
#     down_progress 16 / ai_war_score 10 / war_priority 9 / ai_trade_score 6 / …
#   这些块里的 key **全部**带 factor（实测 0 例外），所以它们是权重块。
WEIGHT_CONTAINERS = {
    "mean_time_to_happen", "ai_chance", "ai_will_do", "chance", "selection_weight",
    "spawn_chance", "movement_evaluation", "progress", "target_province_weights",
    "embracement_speed", "ai", "province_independence_weight", "ai_weight",
    "ai_priority", "down_progress", "ai_war_score", "war_priority", "ai_trade_score",
    "ai_pick_ancestor", "ai_strategy", "weight", "score",
}

# ★ 修正定义容器：内部是**修正字段位**（不是效果/触发器名字位）。
#   实测：conditional_modifier 772 / skill_scaled_modifier 192 /
#         influence_scaled_conditional_modifier 42 / scaled_modifier 39 /
#         loyalty_scaled_conditional_modifier 35 / range_modifier 13 / bonus 11 /
#         reverse_scaled_modifier 6 —— 合计 1117 个，**全部不含 factor**。
MODIFIER_DEF_CONTAINERS = {
    "conditional_modifier", "skill_scaled_modifier", "scaled_modifier",
    "influence_scaled_conditional_modifier", "loyalty_scaled_conditional_modifier",
    "range_modifier", "bonus", "reverse_scaled_modifier",
}


def _block_is_weight(block) -> bool:
    """权重块的判据：块内含 `factor`。

    实测依据：所有 WEIGHT_CONTAINERS 下的 modifier 块**全部**含 factor（0 例外），
    而所有 MODIFIER_DEF_CONTAINERS 下的 modifier 块**全部**不含（0 例外）。
    所以「含 factor」是精确判据，比按父容器名硬编码更稳 ——
    它还能兜住块内混排（同一个父块里既有权重子块又有修正子块）。
    """
    return block is not None and block.get("factor") is not None


def name_scope(path: tuple[str, ...], ancestors: tuple = ()) -> str | None:
    """判定「这个位置能不能放效果/触发器名」。返回 'effect' / 'trigger' / None。

    ★ 关键：只有**语境容器**的直接子项才是名字位，否则一律跳过。
      `option/limit/OR/treasury`  → limit 是语境容器 → 名字位 → trigger
      `option/add_opinion/who`    → add_opinion 不是语境容器 → who 是参数 → None
      `.../trigger_switch/adm_advisor` → trigger_switch 是 case 容器 → 值是数据 → None

    ★★ `modifier` 容器**有两种语义**，必须看内容而不是名字：
      `modifier = { factor = 1.1  treasury = 100 }`  → 是权重块 → 内部 trigger 位（合法）
      `modifier = { ... }`（无 factor，父为 conditional_modifier 等）→ 修正字段位
    实测共有 1117 个「无 factor」的 modifier 块，都是修正定义。

    ★★★ ancestors 与 path 的对齐关系（这里改动过两次，必须写死）：
      验证方法：`common/institutions/00_Core.txt:164` 的 `potential`
        path     = ('feudalism', 'embracement_speed', 'modifier')   len=3
        ancestors = [ 顶层块, embracement_speed 块, modifier 块 ]    len=3
      即 **ancestors 与 path 一一对应、长度相同**：
        ancestors[i] 就是 path[i] 这个 key 所指向的那个块。
      所以「path[-1]=`modifier` 自身的块」= `ancestors[-1]` ——
      判定 modifier 是不是权重块，必须查 ancestors[-1]，不是 ancestors[-2]。
    """
    anc = ancestors
    for i, key in enumerate(reversed(path)):
        # path 的第 (len-1-i) 个 key 对应 ancestors 的同下标块
        idx = len(path) - 1 - i
        block = anc[idx] if idx < len(anc) else None

        if key in CASE_CONTAINERS:
            return None                      # case 容器内的 key 是数据值
        if key in TRANSPARENT_KEYS:
            continue
        if key in WEIGHT_CONTAINERS:
            return "trigger"                 # AI 权重容器 → 内部是触发器位
        if key == "modifier":
            return "trigger" if _block_is_weight(block) else None
        if key in MODIFIER_DEF_CONTAINERS:
            return None                      # 修正定义 → 修正字段位
        if key in TRIGGER_MARKERS:
            return "trigger"
        if key in EFFECT_MARKERS:
            return "effect"
        # 遇到第一个非语境容器的 key：说明当前项是它的参数 → 不是名字位
        return None
    return None

# ★ 权重块/MTTH 块的**控制字段**（不是触发器名）。
#   它们的出现位置与真触发器不可区分（都在权重块内），只能靠语义识别。
#   发现手段：scripts/container_exclusivity.py 找出「只在一种容器内出现」的名字，
#   再人工逐个确认。实测（vanilla v1.37.5.0）：
#     potential         94 次，全部在 modifier 权重块 → 「potential 为假则不参与」
#     scale             85 次，同上
#     force_potential   12 次，只有 institutions/00_Core.txt → 「potential 为假时隐藏此项」
#     months / days / years  2581 / 1684 / 29 次，全部在 mean_time_to_happen → MTTH 单位
#   注意：这不是完整清单的「自动推导」，而是**发现 + 人工确认**的产物。
#   独占度只能提示候选（58 个独占里 52 个其实是真触发器，例如 dominant_culture
#   在 vanilla 只被权重块用到，但它确实是触发器）。
#   所以宁可漏收（漏 = false negative，安全）也不要误收进排除表（误 = false positive，伤信任）。
CONTROL_FIELDS = {
    "potential", "scale", "force_potential",
    "custom_trigger_tooltip", "months", "days", "years",
}

# 语境容器本身不是「名字」：它们是结构，出现在语料里不能算效果/触发器名。
# 上一版把 limit(18465) / ai_chance(4646) / trigger(1843) 计成了效果名，是覆盖率虚低的第二个原因。
CONTAINER_KEYS = TRIGGER_MARKERS | EFFECT_MARKERS | WEIGHT_CONTAINERS | {
    "hidden", "random", "random_list", "random_owned_province", "random_owned_area",
    "property_list", "switch", "ignore", "capital_scope", "goto", "tag_switch",
    "modifier", "factor", "scale", "scaled",
}

# ★ case 容器：块内的 key 是**数据值**（如 `trigger_switch = { on_trigger = ...; adm_advisor = {...} }`
#   里的 adm_advisor 是旗标名，不是效果名），不能计入名字位。
CASE_CONTAINERS = {"trigger_switch", "switch", "tag_switch", "on_trigger"}

SCOPE = ("effect", "trigger", "both", "unknown")


def _join_path(path: tuple[str, ...]) -> str:
    return "/".join(path) if path else "<root>"


_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# scope 链关键字：它们是指代（root/from/prev…），不是效果/触发器名
SCOPE_KEYWORDS = {
    "root", "from", "prev", "this", "overlord", "emperor", "capital_scope",
    "capital", "owner", "controller", "ruler", "heir", "consort", "target",
    "event_target", "saved", "country", "province", "days", "months", "years",
}
# 小写逻辑词：vanilla 里偶尔写成小写（or / not / and），与 OR/NOT 同义
LOWERCASE_LOGIC = {"or", "and", "not", "nor", "nand", "xor"}


def scan_vanilla_definitions(game: str) -> tuple[set[str], set[str]]:
    """★ 第 4 源：vanilla 用**数据文件**定义的名字。

    cwt 用 `<scripted_effect>` / `<scripted_trigger>` 通配符覆盖这一类，
    所以它们永远不会出现在 `idx.effects` / `idx.triggers` 里 ——
    这不是覆盖率缺口，是**数据定义的合法名字**。
    实测：缺口 effect 988 个里有 262 个是 vanilla 的 scripted_effect 名。
    """
    se, st = set(), set()
    for pattern, target in (("common/scripted_effects/*.txt", se),
                            ("common/scripted_triggers/*.txt", st)):
        for f in _glob(os.path.join(game, pattern)):
            try:
                tree, _e, _i = pdx_parser.parse_file_info(f)
            except Exception:  # noqa: BLE001
                continue
            for e in tree.entries:
                if getattr(e, "op", "") and e.key:
                    target.add(e.key)
    return se, st


def scan_enum_values(game: str) -> dict[str, set[str]]:
    """★ 第 5 源：vanilla 常见的枚举值集合。

    `aegean_archipelago_area` / `anatolia_region` / `POL` 这类是**标量值**，
    出现在效果位却永远不该被当成效果名。按目录收集即可，无需全量。

    ★ 必须只取「定义层的 key」，不能递归 walk：
      上一版用 tree.walk() 抓全部递归 key，结果 building 定义体内的
      `treasury` / `build_trigger` / `hostile_disembark_speed`（其实是修正名与触发器名）
      全被当成枚举值，把合法的效果名/触发器名从候选集里吞掉了。
      判定：地图类文件是扁平列表（取顶层 key）；定义类文件（ideas/institutions/
      buildings）只取顶层 key（每个理念组/建筑一个顶层块名），块内字段一律不取。
    """
    out: dict[str, set[str]] = {}
    specs = [
        ("areas", "map/area.txt"),
        ("regions", "map/region.txt"),
        ("continents", "map/continent.txt"),
        ("superregions", "map/superregion.txt"),
        ("trade_nodes", "common/tradenodes/*.txt"),
        ("religions", "common/religions/*.txt"),
        ("culture_groups", "common/cultures/*.txt"),
        ("countries", "common/country_tags/*.txt"),
        ("goods", "common/tradegoods/*.txt"),
        ("advisortypes", "common/advisortypes/*.txt"),
        ("buildings", "common/buildings/*.txt"),
        ("idea_groups", "common/ideas/*.txt"),
        ("institutions", "common/institutions/*.txt"),
        ("colonial_regions", "common/colonial_regions/*.txt"),
        ("trade_companies", "common/trade_companies/*.txt"),
        ("rebel_types", "common/rebel_types/*.txt"),
        ("subject_types", "common/subject_types/*.txt"),
    ]
    for label, pattern in specs:
        names: set[str] = set()
        for f in _glob(os.path.join(game, pattern)):
            try:
                tree, _e, _i = pdx_parser.parse_file_info(f)
            except Exception:  # noqa: BLE001
                continue
            # ★ 只取顶层 key（定义层），不递归
            for e in tree.entries:
                if getattr(e, "op", "") and e.key:
                    names.add(e.key)
        if names:
            out[label] = names
    return out


def _glob(pattern: str) -> list[str]:
    import glob as _g
    return sorted(_g.glob(pattern))


def scan_vanilla_on_actions(game: str) -> set[str]:
    """★ 第 6 源：vanilla `common/on_actions/*.txt` 的**顶层 key** = 引擎钩子真集。

    实测（v1.37.5.0）：256 个。与 cwt 的 `type[on_action]` subtype 交集 224 个。
      · vanilla 有、cwt 无 32 个（如 on_hussars_raised / on_harmonized_christian）→ cwt 滞后
      · cwt 有、vanilla 无  5 个（on_russian_oprichnina 等）→ cwt 更全，按 info 分级

    ★ 命名陷阱（本项目的第 4 例「同形不同类」）：
      引擎钩子   on_battle_won_country          ← 定义在 common/on_actions/
      它调用的脚本效果 on_battle_won_country_effect   ← 定义在 common/scripted_effects/
      同一个 on_ 前缀，两种完全不同的类别。绝不能靠前缀推断。
    """
    names: set[str] = set()
    for f in _glob(os.path.join(game, "common/on_actions/*.txt")):
        try:
            tree, _e, _i = pdx_parser.parse_file_info(f)
        except Exception:  # noqa: BLE001
            continue
        for e in tree.entries:
            if getattr(e, "op", "") and e.key:
                names.add(e.key)
    return names


# 结构字段黑名单：这些是容器的参数，不是效果/触发器
STRUCTURAL = {
    "name", "title", "desc", "picture", "highlight", "namespace", "id", "major", "minor",
    "sound", "duration", "scaled", "value", "factor", "tooltip",
    "custom_tooltip", "hidden_tooltip", "localisation", "icon", "color", "size",
    "position", "tag", "country", "province", "date", "year", "month", "day",
}


def scan_corpus(game: str, verbose: bool = False,
                vanilla_effects: set[str] | None = None,
                vanilla_triggers: set[str] | None = None,
                enum_values: dict[str, set[str]] | None = None,
                modifier_names: set[str] | None = None,
                ) -> tuple[dict, dict, dict]:
    """扫描 vanilla 语料，返回 (effects, triggers, unknown)。

    过滤链（每一条都在实测里救过一次指标）：
      1. 容器名（limit / ai_chance / trigger …）不是名字        → unknown
      2. 结构字段（name / title / id …）不是名字                → unknown
      3. 不是标识符形态（`1` / `$p$` / 日期）不是名字            → unknown
      4. 枚举值（xxx_area / xxx_region / POL …）不是名字         → unknown
      5. 语境判定：只有语境容器的直接子项才是名字位              → 其余 unknown
    """
    vanilla_effects = vanilla_effects or set()
    vanilla_triggers = vanilla_triggers or set()
    enum_values = enum_values or {}
    modifier_names = modifier_names or set()
    enum_all: set[str] = set()
    for v in enum_values.values():
        enum_all |= v
    # 常见枚举后缀，兜住脚本里出现但没在 map/ 文件里读到的（如新版本区域）
    enum_suffix = ("_area", "_region", "_superregion", "_province", "_trade_node")

    effects: dict[str, dict] = defaultdict(lambda: {"count": 0, "examples": [], "_scopes": set()})
    triggers: dict[str, dict] = defaultdict(lambda: {"count": 0, "examples": [], "_scopes": set()})
    unknown = Counter()

    n_files = 0
    for d in TXT_DIRS:
        root_dir = os.path.join(game, d)
        if not os.path.isdir(root_dir):
            continue
        for root, _sub, files in os.walk(root_dir):
            for f in sorted(files):
                if not f.endswith(".txt") or f.startswith("."):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, game).replace(os.sep, "/")
                n_files += 1
                try:
                    tree, _errs, _info = pdx_parser.parse_file_info(full)
                except Exception:  # noqa: BLE001
                    continue

                for path, entry, ancestors in tree.walk_full():
                    key = entry.key
                    if not key or entry.is_bare:
                        continue
                    if key in TRANSPARENT_KEYS or key in STRUCTURAL or key in CONTAINER_KEYS:
                        unknown[key] += 1
                        continue
                    if not _IDENT_RE.match(key):
                        unknown[key] += 1
                        continue
                    if key in enum_all or key.endswith(enum_suffix):
                        unknown[key] += 1
                        continue
                    if key in SCOPE_KEYWORDS or key in LOWERCASE_LOGIC:
                        unknown[key] += 1
                        continue
                    # ★ 权重块的控制字段：potential / scale / force_potential / MTTH 单位。
                    #   它们只在权重块里出现，形态与触发器完全一致，
                    #   靠名字无法区分，只能按已确认清单排除。
                    if key in CONTROL_FIELDS:
                        unknown[key] += 1
                        continue
                    # ★ 修正名不是效果/触发器名：`development_cost = -0.1` 是修正字段。
                    #   但**权重块内的修正名也是合法触发器**（`modifier={factor=1.1 treasury=100}`
                    #   里的 treasury 是触发器），所以先判定语境，再按语境决定是否过滤。
                    scope = name_scope(path, ancestors)
                    if scope is None:
                        unknown[key] += 1
                        continue
                    # 现在确定为名字位：修正名只在「非权重块」里才排除
                    if key in modifier_names and scope != "trigger":
                        unknown[key] += 1
                        continue
                    bucket = effects if scope == "effect" else triggers
                    rec = bucket[key]
                    rec["count"] += 1
                    rec["_scopes"].add(path[-1] if path else "")
                    if len(rec["examples"]) < 8:
                        rec["examples"].append(f"{rel}:{entry.line}")

    for bucket in (effects, triggers):
        for rec in bucket.values():
            rec["_scopes"] = sorted(rec["_scopes"])
    if verbose:
        print(f"  语料扫描 {n_files} 文件 | effect 名 {len(effects)} | trigger 名 {len(triggers)} "
              f"| unknown 名 {len(unknown)}")
    return effects, triggers, dict(unknown)


def scan_exe(exe: str) -> set[str]:
    """可选源：抽 exe 里的 snake_case 关键字。只作置信度参考，不做分类。"""
    try:
        with open(exe, "rb") as fh:
            data = fh.read()
    except OSError:
        return set()
    text = data.decode("latin-1", "ignore")
    import re
    return {m for m in re.findall(r"[a-z][a-z0-9_]{2,40}", text)}


def lint_severity(bucket: str, status: str) -> str | None:
    """把白名单条目状态映射成 lint 级别。★ 唯一的分级出处，lint 直接调它。

    分级理由（误报对信任的杀伤远大于漏判）：
      confirmed      → None   语料实证 + cwt 已分类，是合法名，不报
      cwt_only       → info   「引擎定义了但 vanilla 未使用」，不是错
      corpus_only    → warn   ★ cwt 滞后于版本是常态；绝不能 error
      conflict       → warn   两边结论不一，需人工核
    """
    return SEVERITY_MAP.get(status)


SEVERITY_MAP: dict[str, str | None] = {
    "confirmed": None,
    "cwt_only": "info",
    "corpus_only": "warn",
    "conflict": "warn",
}


def build(cwt_dir: str, game: str | None, use_corpus: bool = True,
          exe: str | None = None, verbose: bool = True) -> dict:
    idx = cwt_reader.load_config(cwt_dir) if cwt_dir else cwt_reader.CwtIndex()
    if verbose:
        print(f"  cwt: {idx.stats()}")

    corpus_eff, corpus_trg, unknown = ({}, {}, {})
    vanilla_eff = vanilla_trg = set()
    enum_values: dict[str, set[str]] = {}
    source_stats: dict[str, dict] = {}
    if use_corpus and game:
        if verbose:
            print("  扫描语料…")
        vanilla_eff, vanilla_trg = scan_vanilla_definitions(game)
        enum_values = scan_enum_values(game)
        corpus_eff, corpus_trg, unknown = scan_corpus(
            game, verbose, vanilla_eff, vanilla_trg, enum_values,
            set(idx.modifiers),
        )

    exe_keys = scan_exe(exe) if exe else set()

    # ---- 三源交叉 ----
    # 把 vanilla 数据定义的名字并入 cwt 侧：它们是被 `alias[effect:<scripted_effect>]`
    # 通配符覆盖的一类，若不并入，覆盖率会被这类合法名字拉低（实测 262 + 294 个）。
    for name in vanilla_eff:
        idx.effects.setdefault(
            name,
            [cwt_reader.CwtEntry(name=name, kind="effect",
                                 source_file="vanilla:common/scripted_effects", line=0)],
        )
    for name in vanilla_trg:
        idx.triggers.setdefault(
            name,
            [cwt_reader.CwtEntry(name=name, kind="trigger",
                                 source_file="vanilla:common/scripted_triggers", line=0)],
        )

    # ---- 来源分解：让总数可审计（★ 校验者要求）----
    # 每条记录在构建时就打上来源标记，最后按来源计数；
    # 这样 1402 这个数能被拆开看：哪些是引擎定义、哪些是语料发现、哪些是数据枚举。
    for kind, cwt_map, corpus_map, vanilla_set in (
        ("effects", idx.effects, corpus_eff, vanilla_eff),
        ("triggers", idx.triggers, corpus_trg, vanilla_trg),
    ):
        label = "effects" if kind == "effects" else "triggers"
        by_source = {"cwt": set(), "vanilla_data": set(), "corpus": set()}
        for name, entries in cwt_map.items():
            for e in entries:
                if e.source_file.startswith("vanilla:"):
                    by_source["vanilla_data"].add(name)
                else:
                    by_source["cwt"].add(name)
        by_source["corpus"] = set(corpus_map)
        total = set(cwt_map) | set(corpus_map)
        stats = source_stats.setdefault(label, {})
        stats["total"] = len(total)
        stats["by_source"] = {k: len(v) for k, v in by_source.items()}
        stats["overlap"] = {
            "cwt_and_corpus": len(by_source["cwt"] & by_source["corpus"]),
            "vanilla_and_corpus": len(by_source["vanilla_data"] & by_source["corpus"]),
            "cwt_only": len(by_source["cwt"] - by_source["corpus"]),
            "vanilla_only": len(by_source["vanilla_data"] - by_source["corpus"]),
            "corpus_only": len(by_source["corpus"] - set(cwt_map)),
        }
        # ★ 改名：这里原来叫 union_check，但它的判定是
        #     |cwt ∪ vanilla_data ∪ corpus| == |cwt ∪ corpus|
        #   左边三项里 vanilla_data ⊆ cwt_map（vanilla 名是并入 cwt 侧的），
        #   所以该等式**由定义恒真**，不是独立验证。原名会误导读者以为做了交叉校验。
        #   诚实命名：source_split —— 它描述的是「按来源拆分的结果」，不是「校验通过」。
        stats["source_split"] = {
            "cwt": len(by_source["cwt"]),
            "vanilla_data": len(by_source["vanilla_data"]),
            "corpus": len(by_source["corpus"]),
            "total": len(total),
            "note": "三项之和可能有重叠，不等于 total（overlap 里给了交集大小）",
        }
        # ★ 真正可独立验证的等式：total 必须等于「三源并集」，
        #   且 by_source 里任一项都必须 ⊆ total 的来源之一。这里只保留
        #   一个**可被外部重算**的量：total 与 corpus 大小差值应等于 cwt 独有数。
        stats["cross_check"] = {
            "total_minus_corpus": len(total) - len(by_source["corpus"]),
            "cwt_or_vanilla_only": len(
                (by_source["cwt"] | by_source["vanilla_data"]) - by_source["corpus"]),
            "note": "两者应相等；不等说明 by_source 与 total 构造不一致",
        }

    for label, d in (("modifiers", idx.modifiers),):
        source_stats[label] = {
            "total": len(d),
            "by_source": {"cwt": len(d), "corpus": 0, "vanilla_data": 0},
            "overlap": {},
            # modifiers 只有 cwt 一个来源，没有可分拆的东西
            "source_split": {"cwt": len(d), "vanilla_data": 0, "corpus": 0, "total": len(d)},
        }

    out: dict[str, dict] = {"effects": {}, "triggers": {}, "modifiers": {}, "on_actions": {}}

    def merge(kind: str, cwt_map: dict, corpus_map: dict) -> dict:
        rows: dict[str, dict] = {}
        for name, entries in cwt_map.items():
            rows[name] = {
                "name": name,
                "kind": kind,
                "status": "cwt_only",
                "cwt": [e.as_dict() for e in entries],
                "corpus": None,
            }
        for name, rec in corpus_map.items():
            if name in rows:
                rows[name]["status"] = "confirmed"
                rows[name]["corpus"] = {"count": rec["count"], "examples": rec["examples"]}
            else:
                rows[name] = {
                    "name": name,
                    "kind": kind,
                    "status": "corpus_only",     # ★ lint 里必须是 warn
                    "cwt": [],
                    "corpus": {"count": rec["count"], "examples": rec["examples"]},
                }
        if exe_keys:
            for name, row in rows.items():
                row["in_exe"] = name in exe_keys
        return rows

    out["effects"] = merge("effect", idx.effects, corpus_eff)
    out["triggers"] = merge("trigger", idx.triggers, corpus_trg)
    out["modifiers"] = merge("modifier", idx.modifiers, {})

    # on_actions：以 vanilla 真实钩子为**主集**，cwt 独有的降为 info
    vanilla_on_actions = scan_vanilla_on_actions(game) if game else set()
    oa_rows: dict[str, dict] = {}
    for name in sorted(vanilla_on_actions):
        in_cwt = name in idx.on_actions
        oa_rows[name] = {
            "name": name,
            "kind": "on_action",
            "status": "confirmed" if in_cwt else "corpus_only",
            "cwt": [e.as_dict() for e in idx.on_actions.get(name, [])],
            "corpus": None,
            "vanilla": True,
        }
    for name in sorted(set(idx.on_actions) - vanilla_on_actions):
        oa_rows[name] = {
            "name": name,
            "kind": "on_action",
            "status": "cwt_only",           # 引擎定义了但 vanilla 未使用 → info
            "cwt": [e.as_dict() for e in idx.on_actions[name]],
            "corpus": None,
            "vanilla": False,
        }
    out["on_actions"] = oa_rows

    # ---- on_actions 来源计数（须在 out 生成之后）----
    _hooks = {n for n, r in oa_rows.items() if r.get("vanilla")}
    _extra = set(oa_rows) - _hooks
    source_stats["on_actions"] = {
        "total": len(oa_rows),
        "by_source": {"vanilla_hooks": len(_hooks), "cwt_only": len(_extra)},
        "overlap": {
            "vanilla_and_cwt": len(_hooks & set(idx.on_actions)),
            "vanilla_only": len(_hooks - set(idx.on_actions)),
            "cwt_only": len(_extra),
        },
        # ★ 原名 union_check 是 partition 的恒真判断（_hooks ∪ _extra 按定义就是 oa_rows），
        #   改名为 source_split，只描述拆分结果，不再假装是校验。
        "source_split": {
            "vanilla_hooks": len(_hooks),
            "cwt_only": len(_extra),
            "total": len(oa_rows),
        },
        "note": "主集 = vanilla common/on_actions/*.txt 顶层 key；cwt 独有降为 info",
    }

    # ---- 冲突：cwt 说 effect，语料语境说 trigger（或反过来）----
    conflicts = sorted(set(corpus_eff) & set(idx.triggers) - set(idx.effects))
    conflicts += sorted(set(corpus_trg) & set(idx.effects) - set(idx.triggers))
    for name in conflicts:
        row = out["effects"].get(name) or out["triggers"].get(name)
        if row:
            row["status"] = "conflict"

    # ---- 覆盖率（分母 = 语料侧）----
    stats: dict[str, dict] = {}
    for kind, corpus_map, cwt_map in (
        ("effect", corpus_eff, idx.effects),
        ("trigger", corpus_trg, idx.triggers),
    ):
        corpus_names = set(corpus_map)
        covered = corpus_names & set(cwt_map)
        gap = sorted(corpus_names - set(cwt_map))
        stats[kind] = {
            "corpus_names": len(corpus_names),
            "covered": len(covered),
            "coverage_pct": round(len(covered) / max(len(corpus_names), 1) * 100, 2),
            "gap": len(gap),
            "gap_names": gap,
        }

    total_corpus = stats["effect"]["corpus_names"] + stats["trigger"]["corpus_names"]
    total_cov = stats["effect"]["covered"] + stats["trigger"]["covered"]
    stats["overall"] = {
        "corpus_names": total_corpus,
        "covered": total_cov,
        "coverage_pct": round(total_cov / max(total_corpus, 1) * 100, 2),
    }
    stats["by_source"] = source_stats

    # ---- 缺口归因（必须逐类，不能只给「其他」）----
    gap_names = set(stats["effect"]["gap_names"]) | set(stats["trigger"]["gap_names"])
    attribution = {
        "in_exe_but_not_cwt": [],        # exe 有、cwt 无 → cwt 滞后于版本
        "unknown_anywhere": [],          # 两处都没有 → 可能是结构字段误入，或解析遗漏
    }
    for n in sorted(gap_names):
        (attribution["in_exe_but_not_cwt"] if n in exe_keys else attribution["unknown_anywhere"]).append(n)
    stats["gap_attribution"] = {k: {"count": len(v), "names": v[:40]}
                                for k, v in attribution.items()}

    import datetime
    return {
        "meta": {
            "cwt_source": cwt_reader.CFG_REPO,
            "cwt_license": cwt_reader.CFG_LICENSE,
            "cwt_files": len(idx.files),
            "game": game,
            "corpus_dirs": TXT_DIRS,
            "exe_used": bool(exe_keys),
            "cwt_errors": idx.errors[:10],
            # ★ 换版本时一眼看出该重生成（reference 是游戏内容衍生数据，不进安装包）
            "game_version": _paths.read_version(__import__("pathlib").Path(game)) if game else None,
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "generator": "dsh-eu4-modding/build_reference.py",
            "counts": {
                k: {
                    "total": v["total"],
                    "by_source": v["by_source"],
                    "overlap": v["overlap"],
                    # ★ 从 union_check 改名：前者是恒真判断，后者只是拆分结果描述
                    "source_split": v.get("source_split"),
                    # ★ 真正可被外部重算的交叉校验（仅 effects/triggers 有）
                    "cross_check": v.get("cross_check"),
                }
                for k, v in source_stats.items()
            },
        },
        "stats": stats,
        "unknown_keys_sample": dict(sorted(unknown.items(), key=lambda kv: -kv[1])[:40]),
        **out,
    }


def sabotage_selftest(cwt_dir: str, game: str | None) -> int:
    """★ 破坏性自测：注入破坏后，构建必须**变坏**而不是照常成功。

    为什么必须做（复查者判据）
    ------------------------
    「先证明检查会红，再用检查判定」—— 与三个守卫的 --selftest 同一条原则。
    如果构建在任何输入下都给出 99.64%，那这个数字就没有信息量。

    三路注入（每路独立，任一未变坏即整体失败）
    ----------------------------------------
      S1 把 cwt 目录置空（无任何 alias）→ 覆盖率必须显著下降（< 50%）
      S2 删掉 effects.cwt 里所有 `alias[effect:...]` 行 → effect 侧覆盖率必须下降
      S3 传一个不存在的 cwt 路径 → 必须给出错误而不能假装成功

    实现只用标准库，临时目录用完即删，**不碰用户的真实 cwt 检出**。

    返回 0 = 三路都变坏（守卫有效）；1 = 至少一路没变坏（守卫是空断言）。
    """
    import shutil
    import tempfile

    if not cwt_dir or not os.path.isdir(cwt_dir):
        print("sabotage：需要有效的 --cwt 目录作为基线")
        return 1

    base = build(cwt_dir, game, verbose=False)
    base_cov = base["stats"]["overall"]["coverage_pct"]
    print(f"\n[sabotage] 基线覆盖率 = {base_cov:.2f}%")

    results: list[tuple[str, bool, str]] = []

    # ---- S1：空 cwt 目录 ----
    tmp1 = tempfile.mkdtemp(prefix="sab1_")
    try:
        empty = os.path.join(tmp1, "empty_cwt")
        os.makedirs(empty, exist_ok=True)
        r1 = build(empty, game, verbose=False)
        cov1 = r1["stats"]["overall"]["coverage_pct"]
        ok1 = cov1 < 50.0
        results.append(("S1 空 cwt 目录", ok1,
                        f"覆盖率 {base_cov:.2f}% → {cov1:.2f}%（要求 <50%）"))
    finally:
        shutil.rmtree(tmp1, ignore_errors=True)

    # ---- S2：删掉 effects.cwt 里的 alias[effect:...] ----
    tmp2 = tempfile.mkdtemp(prefix="sab2_")
    try:
        broken = os.path.join(tmp2, "broken_cwt")
        shutil.copytree(cwt_dir, broken)
        target = None
        for root, _s, files in os.walk(broken):
            for f in files:
                if f == "effects.cwt":
                    target = os.path.join(root, f)
                    break
        if target is None:
            results.append(("S2 删 effect alias", False, "找不到 effects.cwt"))
        else:
            with open(target, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            lines = text.split("\n")
            kept = [ln for ln in lines if not re.match(r"^alias\[effect:", ln.strip())]
            removed = len(lines) - len(kept)
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(kept))
            r2 = build(broken, game, verbose=False)
            cov2 = r2["stats"]["effect"]["coverage_pct"]
            base_eff = base["stats"]["effect"]["coverage_pct"]
            ok2 = cov2 < base_eff
            results.append(("S2 删 effect alias", ok2,
                            f"删 {removed} 行；effect 覆盖率 "
                            f"{base_eff:.2f}% → {cov2:.2f}%（要求下降）"))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # ---- S3：不存在的 cwt 路径 ----
    r3 = build(os.path.join(tempfile.gettempdir(), "no_such_cwt_dir_xyz"), game, verbose=False)
    errs = r3["meta"].get("cwt_errors", [])
    ok3 = bool(errs)
    results.append(("S3 不存在的 cwt 路径", ok3,
                    f"cwt_errors = {errs[:1] if errs else '空（未报错）'}"))

    print("[sabotage] 三路注入结果：")
    all_ok = True
    for name, ok, note in results:
        print(f"  {'PASS' if ok else '★ FAIL'}  {name:22s} {note}")
        all_ok = all_ok and ok
    print(f"[sabotage] 判定：{'守卫有效（注入破坏后构建确实变坏）' if all_ok else '守卫失效（存在不随输入变化的指标）'}")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EU4 名称白名单构建（三源交叉）")
    ap.add_argument("--cwt", default=None, help="cwtools-eu4-config 检出目录")
    ap.add_argument("--game", default=None, help="游戏安装目录（默认自动探测）")
    ap.add_argument("--out", default=None, help="输出目录（默认 ./reference）")
    ap.add_argument("--no-corpus", action="store_true", help="只用 cwt，不扫语料")
    ap.add_argument("--exe", default=None, help="可选：eu4.exe 路径，附加置信度")
    ap.add_argument("--stats", action="store_true", help="只打印统计，不写文件")
    ap.add_argument("--min-coverage", type=float, default=None, metavar="PCT",
                    help="覆盖率下限（百分比）。低于此值时非 0 退出（fail-loud）。"
                         "cwt 配置被弃维护时覆盖率会静默下降，此参数把它变成报错")
    ap.add_argument("--sabotage", action="store_true",
                    help="破坏性自测：注入三种破坏，验证构建确实会变坏（不是恒成功的空断言）")
    args = ap.parse_args(argv)

    _paths.force_utf8_stdout()
    game = args.game or _paths.find_game_dir()
    game = str(game) if game else None

    if args.sabotage:
        return sabotage_selftest(args.cwt, game)

    print("构建白名单…")
    data = build(args.cwt, game, use_corpus=not args.no_corpus, exe=args.exe)

    s = data["stats"]
    print(f"\n覆盖率（分母=语料侧）")
    for k in ("effect", "trigger", "overall"):
        v = s[k]
        print(f"  {k:8s} 语料名 {v['corpus_names']:5d}  已分类 {v.get('covered', 0):5d}  "
              f"覆盖率 {v['coverage_pct']:5.2f}%")
    ga = s["gap_attribution"]
    print(f"\n缺口归因（共 {s['effect']['gap'] + s['trigger']['gap']} 个）")
    for k, v in ga.items():
        print(f"  {k:24s} {v['count']:5d}  {v['names'][:8]}")

    out = data["effects"].get("treasury"), data["triggers"].get("treasury")
    print(f"\n★ treasury 验收：effects.json 中 {'存在' if out[0] else '不存在'} | "
          f"triggers.json 中 {'存在' if out[1] else '不存在'}")

    # ★ 把 cross_check 打到 stdout，不要只埋在 2 MB 的 JSON 里。
    #   理由（复查者原话）：能一眼看到的可复算量，才有人会去算。
    #   埋进 JSON 等于要求每个核验者先完整重建一次才能核对。
    print("\ncross_check（两侧应相等；不等说明 by_source 与 total 构造不一致）")
    cc_failed = False
    gate_failed = False        # 覆盖率门槛结果，默认未启用=未失败
    for k in ("effects", "triggers"):
        cc = data["meta"]["counts"].get(k, {}).get("cross_check")
        if not cc:
            continue
        a, b = cc["total_minus_corpus"], cc["cwt_or_vanilla_only"]
        ok = "OK" if a == b else "★ 不一致"
        print(f"  {k:8s} {a:5d} / {b:5d}  {ok}")
        if a != b:
            cc_failed = True
    print(f"  口径：total - corpus  vs  (cwt ∪ vanilla_data) - corpus")
    print(f"  复算方式：直接读 _meta.json 的 meta.counts.<kind>.cross_check")

    # ★ fail-loud：覆盖率低于阈值要报错，不能静默降级。
    #   动机：分类主源是第三方的 cwtools-eu4-config，它若被弃维护/滞后于游戏版本，
    #   覆盖率会缓慢下降，而工具照常运行 —— 用户不会察觉判据变旧。
    #   （注意：gate_failed 已在 cross_check 段之前初始化为 False，
    #     这里不能再赋一次，否则会把 cc_failed 的结果覆盖掉。）
    if args.min_coverage is not None:
        actual = s["overall"]["coverage_pct"]
        print(f"\n覆盖率门槛：要求 >= {args.min_coverage:.2f}%，实际 {actual:.2f}%")
        if actual < args.min_coverage:
            gate_failed = True
            print(f"★ 未达门槛 —— 判据可能已过时（cwt 配置滞后或游戏版本变化）")
            print(f"  缺口归因见上方；常见原因：cwt 未收录新版本新增的名字")
            print(f"  若要保留输出，去掉 --min-coverage；若要排查，看 gap_attribution")
        else:
            print(f"达标（余量 {actual - args.min_coverage:.2f} 个百分点）")

    if args.stats:
        return 1 if (gate_failed or cc_failed) else 0

    out_dir = args.out or "reference"
    os.makedirs(out_dir, exist_ok=True)
    for name in ("effects", "triggers", "modifiers", "on_actions"):
        rows = {k: v for k, v in data[name].items()}
        path = os.path.join(out_dir, f"{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"  写出 {path}  ({len(rows)} 条)")
    with open(os.path.join(out_dir, "_meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": data["meta"], "stats": data["stats"],
                   "unknown_keys_sample": data["unknown_keys_sample"]},
                  fh, ensure_ascii=False, indent=1)
    print(f"  写出 {out_dir}/_meta.json")

    return 1 if gate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
