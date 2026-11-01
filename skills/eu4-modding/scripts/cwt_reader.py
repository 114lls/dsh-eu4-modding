"""CWTools 模式定义（.cwt）读取器 —— 白名单的**分类主源**。

为什么用它做主源（而不是 exe 字符串表）
--------------------------------------
1. 可移植：技能包要发给不同用户，其安装可能是 repack / 破解 / 换过 exe / 别的版本；
   exe 偏移随每次游戏更新失效，维护成本高。
2. **关键**：字符串存在 ≠ 分类。`treasury` 在 exe 里出现 19 次，
   光看次数分不出它在「效果表」还是「触发器值表」——必须按**语境**判定：
   出现在 `limit = { }` / `trigger = { }` 内的是触发器，出现在效果位的是效果。
   cwttools 的配置把类型直接写在模式定义里：`alias[effect:xxx]` / `alias[trigger:xxx]`。
3. 覆盖率更高：语料只能证明「vanilla 用过这个名字」，
   cwt 还能覆盖「引擎定义了但 vanilla 脚本里没用过」的名字 → 减少 false negative。

上游来源
--------
    cwtools/cwtools-eu4-config   MIT License   https://github.com/cwtools/cwtools-eu4-config
    数据文件按 MIT 引用，注明出处即可；本模块**只读不写**，也不分发上游文件本体。

.cwt 语法要点（实测 v1.x）
--------------------------
    alias[effect:multiply_variable] = { ... }        → 效果名 multiply_variable
    alias[trigger:treasury] = int                    → 触发器名 treasury
    alias[effect:<scripted_effect>] = bool           → 带尖括号的是通配占位符，不是真名
    alias[trigger:<advisor_type>] = int[1..5]        → 同上，跳过
    ## scope = country                               → 上一行的 scope 注释，可采集
    modifiers = { accept_vassalization_reasons = country ... }   → 修正名表
    types = { type[on_action] = { subtype[on_startup] = { } } }  → on_action 名表

分类结论落在 `CwtIndex.effects / triggers / modifiers / on_actions / scope_links`。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import pdx_parser

__all__ = ["CwtIndex", "CwtEntry", "load_config", "CFG_REPO", "CFG_LICENSE"]

CFG_REPO = "cwtools/cwtools-eu4-config"
CFG_LICENSE = "MIT"

# alias[effect:xxx] = ...   /   alias[trigger:xxx] = ...
_ALIAS_RE = re.compile(r"^alias\[\s*(effect|trigger|effect_or_trigger)\s*:\s*([^\]]+?)\s*\]$")
# modifiers = { name = scope }   /  types = { type[on_action] = { subtype[x] = {} } }
_MODIFIER_ROW_RE = re.compile(r"^([a-z_!][a-z0-9_!]*)\s*=\s*(country|province|global|army|navy|trade|true|yes|no)$")
_SUBTYPE_RE = re.compile(r"^subtype\[\s*([^\]]+?)\s*\]$")
_TYPE_RE = re.compile(r"^type\[\s*([^\]]+?)\s*\]$")


@dataclass(slots=True)
class CwtEntry:
    """一条模式定义。"""

    name: str
    kind: str                    # 'effect' | 'trigger' | 'modifier' | 'on_action'
    scope: str = ""              # 注释里的 `## scope = country`
    value_type: str = ""         # `= int` / `= { }` / `= bool` 之类
    source_file: str = ""
    line: int = 0
    container: str = ""          # on_action 时记录所属 type 块（'on_action' / 'building' …）

    def as_dict(self) -> dict:
        d = {"kind": self.kind}
        if self.scope:
            d["scope"] = self.scope
        if self.value_type:
            d["value_type"] = self.value_type
        if self.source_file:
            d["source"] = f"{self.source_file}:{self.line}"
        return d


@dataclass
class CwtIndex:
    effects: dict[str, list[CwtEntry]] = field(default_factory=dict)
    triggers: dict[str, list[CwtEntry]] = field(default_factory=dict)
    modifiers: dict[str, list[CwtEntry]] = field(default_factory=dict)
    on_actions: dict[str, list[CwtEntry]] = field(default_factory=dict)
    scope_links: dict[str, list[CwtEntry]] = field(default_factory=dict)
    enums: dict[str, list[CwtEntry]] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ---- 查询 ----
    def has_effect(self, name: str) -> bool:
        return name in self.effects

    def has_trigger(self, name: str) -> bool:
        return name in self.triggers

    def has_modifier(self, name: str) -> bool:
        return name in self.modifiers

    def stats(self) -> dict:
        return {
            "files": len(self.files),
            "effects": len(self.effects),
            "triggers": len(self.triggers),
            "modifiers": len(self.modifiers),
            "on_actions": len(self.on_actions),
            "scope_links": len(self.scope_links),
            "enums": len(self.enums),
        }


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def _strip_placeholder(name: str) -> str | None:
    """`<scripted_effect>` / `<advisor_type>` 这类通配符不是真名，返回 None。"""
    name = name.strip()
    if not name or name.startswith("<") or name.startswith("enum[") or name.startswith("scope["):
        return None
    if "$" in name or "?" in name:
        return None
    return name


def _scope_from_prev_comment(lines: list[str], idx: int) -> str:
    """向上找最近的 `## scope = xxx` 注释。"""
    for j in range(idx - 1, max(idx - 4, -1), -1):
        s = lines[j].strip()
        if not s:
            continue
        if s.startswith("##") or s.startswith("#"):
            m = re.search(r"scope\s*=\s*([A-Za-z_|]+|any)", s)
            if m:
                return m.group(1)
            continue
        break
    return ""


def _read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", "replace")


def _collect_alias(text: str, fname: str, idx: CwtIndex) -> None:
    lines = text.splitlines()
    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("##"):
            continue

        m = _ALIAS_RE.match(line.split("=")[0].strip())
        if not m:
            continue
        kind_raw, name_raw = m.group(1), m.group(2)
        name = _strip_placeholder(name_raw)
        if name is None:
            continue

        rhs = line.split("=", 1)[1].strip() if "=" in line else ""
        value_type = "block" if rhs.startswith("{") else rhs[:24]
        scope = _scope_from_prev_comment(lines, lineno - 1)

        entry = CwtEntry(name=name, kind=kind_raw, scope=scope, value_type=value_type,
                         source_file=fname, line=lineno)

        target = None
        if kind_raw in ("effect", "effect_or_trigger"):
            target = idx.effects
            if kind_raw == "effect_or_trigger":
                idx.triggers.setdefault(name, []).append(entry)
        elif kind_raw == "trigger":
            target = idx.triggers
        if target is not None:
            target.setdefault(name, []).append(entry)


def _collect_block_rows(text: str, fname: str, idx: CwtIndex) -> None:
    """`modifiers = { name = country }` 这类扁平块，以及 `type[x] = { subtype[y] }`。

    ★ on_action 只能收「type[on_action] 块内」的 subtype。
      上一版用 `_SUBTYPE_RE` 全局扫 subtype，把
      `type[building]` / `type[event]` / `type[government]` 里的 subtype
      （如 `!cost` / `default` / `hidden` / `positive`）全算成了 on_action，
      导致总数 283 而非真实的 229 —— 同一类「同形不同类」错误。
    """
    cur_type: str | None = None   # 当前所在的 type[...] 块名
    depth = 0

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # type[...] = {   —— 进入某个类型块
        m = _TYPE_RE.match(line.split("=")[0].strip())
        if m:
            cur_type = m.group(1)
            depth = 0
            continue

        # subtype[...] = {  —— 只有在 type[on_action] 块内才算钩子
        m = _SUBTYPE_RE.match(line.split("=")[0].strip())
        if m:
            name = _strip_placeholder(m.group(1))
            if name and cur_type == "on_action":
                idx.on_actions.setdefault(name, []).append(
                    CwtEntry(name=name, kind="on_action", container=cur_type,
                             source_file=fname, line=lineno)
                )
            continue

        # 修正名扁平表（只在 modifiers = { } 块内）
        m = _MODIFIER_ROW_RE.match(line)
        if m and "modifier" in fname.lower():
            name, scope = m.group(1), m.group(2)
            idx.modifiers.setdefault(name, []).append(
                CwtEntry(name=name, kind="modifier", scope=scope,
                         source_file=fname, line=lineno)
            )


def _collect_scope_links(text: str, fname: str, idx: CwtIndex) -> None:
    """scope_links.cwt 里是 `alias[scope_link:xxx]` 或 `xxx = { ... }` 形式。"""
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^alias\[\s*scope_link\s*:\s*([^\]]+?)\s*\]", line)
        if m:
            name = _strip_placeholder(m.group(1))
            if name:
                idx.scope_links.setdefault(name, []).append(
                    CwtEntry(name=name, kind="scope_link", source_file=fname, line=lineno)
                )


def load_config(config_dir: str) -> CwtIndex:
    """读取一个 cwtools-eu4-config 检出目录，返回分类索引。

    只读；任何单文件失败都记进 `errors` 而不是抛异常（白名单不完整也比崩掉好）。
    """
    idx = CwtIndex()
    if not os.path.isdir(config_dir):
        idx.errors.append(f"配置目录不存在：{config_dir}")
        return idx

    for root, _sub, files in os.walk(config_dir):
        for fname in sorted(files):
            if not fname.endswith(".cwt"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, config_dir).replace(os.sep, "/")
            idx.files.append(rel)
            try:
                text = _read_text(full)
            except OSError as exc:
                idx.errors.append(f"{rel}: 读取失败 {exc}")
                continue
            try:
                _collect_alias(text, rel, idx)
                _collect_block_rows(text, rel, idx)
                if "scope_link" in rel:
                    _collect_scope_links(text, rel, idx)
            except Exception as exc:  # noqa: BLE001
                idx.errors.append(f"{rel}: 解析异常 {type(exc).__name__}: {exc}")

    return idx


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _paths import force_utf8_stdout

    force_utf8_stdout()
    if len(sys.argv) < 2:
        print("用法: python cwt_reader.py <cwtools-eu4-config 目录>")
        raise SystemExit(2)
    idx = load_config(sys.argv[1])
    print(idx.stats())
    if idx.errors:
        print("errors:", idx.errors[:5])
    for probe in ("treasury", "add_years_of_income", "add_prestige"):
        print(f"  {probe:24s} effect={idx.has_effect(probe)} trigger={idx.has_trigger(probe)}")
