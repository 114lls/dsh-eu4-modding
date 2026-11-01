"""error.log 分诊 —— 把 18 万行噪声压成「我的问题」那几行。

为什么需要它
-----------
EU4 的 error.log 里 **95%+ 是别人的错**（原版 + 几十个工坊 mod 并存）。
本机实测三份日志合计约 7-18 MB / 5-18 万行，其中真正属于自己的只有个位数。
手工 grep 找不到，因为不知道该 grep 什么。

设计要点（每条都对应一次踩坑）
---------------------------
1. **必须扫全部 `error*.log`** —— EU4 会轮转（error.log / error_old.log /
   error_old_old.log）。实测那份 11 MB 的就被轮转覆盖过。只扫当前那个会漏 95% 语料。
2. **不硬编码任何行数/字节数基准** —— 日志是活的，文件大小随时间变化。
3. **只做确定性归属判断**，不猜「哪个 mod 产生了这个错」：
   - 报错里带文件名 → 用**路径前缀/文件名**匹配已启用 mod
   - 报错里带 namespace/ID → 用**已启用 mod 的 namespace 清单**匹配
   - 两者都没有 → 归为 unknown，不硬猜
4. 归属依据 `dlc_load.json` 的 enabled_mods（**当前启用状态**），
   而不是「mod 目录里存在」。

日志格式（实测）
--------------
    [模块.cpp:行号]: 消息
部分消息里带 `File: "相对路径"` 或 `Incorrect namespace event id: xxx.`

用法
----
    python errorlog_triage.py                          # 扫全部 error*.log
    python errorlog_triage.py --only-mine              # 只留本项目
    python errorlog_triage.py --mod <模组目录>          # 用指定 mod 的 namespace 归属
    python errorlog_triage.py --file <日志路径>         # 指定日志（可不传则自动发现）
    python errorlog_triage.py --json
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
import pdx_parser

__all__ = ["LogLine", "triage", "classify_message", "ERROR_CLASSES"]

# 日志行：[module.cpp:123]: message
_LINE_RE = re.compile(r"^\[([a-zA-Z0-9_]+\.(?:cpp|h)):([0-9]+)\]:\s*(.*)$")
# 消息里的文件名：File: "events/xxx.txt"  或  in events/xxx.txt  或 in  file: mod/xxx.mod
_FILE_HINTS = [
    re.compile(r'File:\s*"([^"]+)"'),
    re.compile(r'file:\s*([^\s]+\.(?:txt|mod|gui|yml))'),
    re.compile(r'in\s+(?:file:\s*)?mod/([^\s]+\.mod)'),
    re.compile(r'in\s+([^\s]+\.(?:txt|gui|yml))\s'),
]
# namespace 报错：Incorrect namespace event id: xxx.
_NS_RE = re.compile(r"namespace event id:\s*([^\s.]+)")
# 事件 ID：event #N (ns.1) / Event xx.1
_EID_RE = re.compile(r"\(([a-zA-Z_][a-zA-Z0-9_]*\.\d+)\)")
# 已知 mod 相关路径前缀
_MOD_PATH_RE = re.compile(r"mod/([^\s]+?)(?:\.mod|/)")

# 错误分类（按消息特征，用于聚合）
ERROR_CLASSES = [
    ("namespace", "namespace 非法", re.compile(r"Incorrect namespace event id")),
    ("unknown-effect", "未知效果名", re.compile(r"Unknown effect type|Unknown effect-type")),
    ("unknown-trigger", "未知触发器", re.compile(r"Unknown trigger|Trigger conditions invalid")),
    ("parse-parsing", "解析错误", re.compile(r"Parsing Error|Parsing Errors")),
    ("missing-loc", "本地化缺失", re.compile(r"missing a title|missing a desc|Missing province adjective")),
    ("no-options", "事件缺选项", re.compile(r"has no options")),
    ("missing-picture", "缺事件图片", re.compile(r"is missing picture")),
    ("unknown-country", "未知国家 tag", re.compile(r"Unknown country")),
    ("unknown-db", "数据库项目缺失", re.compile(r"not found in DB|Undefined event target")),
    ("map", "地图数据", re.compile(r"provinces\.bmp|hasn't been assigned|no pixels")),
    ("version", "版本字段非法", re.compile(r"Invalid supported_version")),
    ("loc-collision", "本地化哈希冲突", re.compile(r"Localization hash collision")),
    ("ai-flag", "AI/旗标条件", re.compile(r"AI|has_country_flag")),
]


class LogLine:
    __slots__ = ("source", "line_no", "module", "module_line", "message",
                 "cls", "cls_label", "files", "ids", "owner")

    def __init__(self, source, line_no, module, module_line, message):
        self.source = source
        self.line_no = line_no
        self.module = module
        self.module_line = module_line
        self.message = message
        self.cls = "other"
        self.cls_label = "其他"
        self.files: list[str] = []
        self.ids: list[str] = []
        self.owner = "unknown"

    def as_dict(self):
        return {
            "source": self.source, "line": self.line_no,
            "module": f"{self.module}:{self.module_line}",
            "class": self.cls, "class_label": self.cls_label,
            "owner": self.owner, "files": self.files, "ids": self.ids,
            "message": self.message,
        }

    def __str__(self):
        loc = f"{self.source}:{self.line_no}"
        return f"[{self.owner}] {loc} [{self.cls_label}] {self.message[:140]}"


def classify_message(msg: str) -> tuple[str, str]:
    for key, label, pat in ERROR_CLASSES:
        if pat.search(msg):
            return key, label
    return "other", "其他"


def extract_files(msg: str) -> list[str]:
    out = []
    for pat in _FILE_HINTS:
        for m in pat.finditer(msg):
            v = m.group(1).strip()
            if v and v not in out and not v.startswith("ugc_"):
                out.append(v)
    return out


def extract_ids(msg: str) -> list[str]:
    out = []
    m = _NS_RE.search(msg)
    if m:
        out.append(m.group(1))
    for m in _EID_RE.finditer(msg):
        out.append(m.group(1))
    return out


def parse_log(path: str) -> list[LogLine]:
    """解析一份日志，返回**日志条目**列表（不是物理行）。

    ★ 三种「行数」必须分清，否则报告里的数字别人按别的口径重算不出来：
        物理行   raw.count(b"\\n")            93,433   ← 文件真实长度
        非空行   非空 strip 后的行数           53,537
        条目数   匹配 `[module.cpp:N]: msg`    50,513   ← 本函数返回值

      实测（本机 3 份日志 7,890,816 B）：
        物理行 93,433 / 非空 53,537 / 条目 50,513。
      差距来自大量不以 `[` 开头的续行（多行消息）与空行 —— EU4 的解析错误
      有时会把上下文续在下一行，那些行不属于任何条目的**开头**。
      `triage()` 返回的 total_lines 是**条目数**，不是物理行数。
    """
    rows: list[LogLine] = []
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    for i, line in enumerate(text.splitlines(), 1):
        if not line.startswith("["):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        row = LogLine(os.path.basename(path), i, m.group(1), int(m.group(2)), m.group(3))
        # 构造时就填好分类/文件/ID，让 parse_log 的输出自洽
        # （之前只在 triage() 里填，导致直接调 parse_log 的调用方拿到全 other）
        row.cls, row.cls_label = classify_message(row.message)
        row.files = extract_files(row.message)
        row.ids = extract_ids(row.message)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# 归属判定：只做确定性判断
# --------------------------------------------------------------------------- #
def build_owner_maps(mod_dirs: list[str], enabled_names: list[str]) -> tuple:
    """返回 (文件归属表, namespace 归属表)。

    文件归属：mod 目录下的相对路径 → mod 名。
    namespace：mod 的 events/*.txt 里的 namespace → mod 名。
    """
    file_owner: dict[str, str] = {}
    ns_owner: dict[str, str] = {}

    for mod_dir in mod_dirs:
        if not os.path.isdir(mod_dir):
            continue
        name = os.path.basename(mod_dir)
        for root, _sub, fs in os.walk(mod_dir):
            for f in fs:
                low = f.lower()
                if not low.endswith((".txt", ".yml")):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, mod_dir).replace(os.sep, "/")
                file_owner[rel] = name
                file_owner[f] = name        # 也允许只按基名匹配
                if low.endswith(".txt") and rel.startswith("events"):
                    try:
                        tree, _e, _i = pdx_parser.parse_file_info(full)
                    except Exception:
                        continue
                    for e in tree.entries:
                        if getattr(e, "key", "") == "namespace" and e.scalar:
                            ns_owner[e.scalar] = name

    return file_owner, ns_owner


def resolve_owner(row: LogLine, file_owner: dict, ns_owner: dict) -> str:
    """确定性归属：文件名 → namespace 精确 → 推导前缀 → 二级前缀。都不中 → unknown。

    ★ 「推导前缀」这一步是为**历史报错**准备的：
      `Incorrect namespace event id: zz_epi_chain1.` 里的 zz_epi_chain1
      在当前模组里已经不存在了（改名为 zz_epi_grain），但它仍应归到该 mod。
      做法：取已知 namespace 的**最长公共前缀**，若报错 id 以它开头，就归属过去。
      实测 zz_epi_chain1 与 zz_epi_grain / zz_epi_border 等的公共前缀是 `zz_epi_`。

    为什么不用「模糊相似度」：那是猜测。这里用的是**确定性的字符串前缀关系**，
    并且只在已知 mod 的命名空间集合内部推导 —— 不会把 vanilla 的错归到你的 mod。
    """
    # 1. 文件名精确
    for f in row.files:
        base = os.path.basename(f)
        if f in file_owner:
            return file_owner[f]
        if base in file_owner:
            return file_owner[base]

    # 2. namespace 精确
    for ident in row.ids:
        if ident in ns_owner:
            return ns_owner[ident]
        prefix = ident.rsplit(".", 1)[0] if "." in ident else ident
        if prefix in ns_owner:
            return ns_owner[prefix]

    # 3. 推导前缀（针对历史/已改名的 namespace）
    for ident in row.ids:
        token = ident.rsplit(".", 1)[0] if "." in ident else ident
        best_mod, best_len = None, 0
        for ns, mod in ns_owner.items():
            common = _common_prefix(token, ns)
            # 要求公共前缀足够长（≥5）且达到较短一方的 60%，避免 `zz_` 这种泛匹配
            shortest = min(len(token), len(ns))
            if common >= 5 and common >= shortest * 0.6 and common > best_len:
                best_mod, best_len = mod, common
        if best_mod:
            return best_mod

    return "unknown"


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def triage(log_files: list[str], mod_dirs: list[str], enabled_names: list[str]) -> dict:
    file_owner, ns_owner = build_owner_maps(mod_dirs, enabled_names)

    rows: list[LogLine] = []
    sizes = {}
    phys_lines = 0
    nonblank = 0
    for p in log_files:
        try:
            size = os.path.getsize(p)
            sizes[os.path.basename(p)] = size
            with open(p, "rb") as fh:
                raw = fh.read()
            phys_lines += raw.count(b"\n")
            text = raw.decode("utf-8", "replace")
            nonblank += sum(1 for ln in text.splitlines() if ln.strip())
        except OSError:
            sizes[os.path.basename(p)] = 0
        part = parse_log(p)
        rows.extend(part)

    for r in rows:
        # parse_log 已填好分类/文件/ID；这里只补归属
        r.owner = resolve_owner(r, file_owner, ns_owner)

    by_owner = Counter(r.owner for r in rows)
    by_class = Counter(r.cls for r in rows)
    by_module = Counter(f"{r.module}:{r.module_line}" for r in rows)
    known_mods = set(ns_owner.values()) | set(file_owner.values())
    mine = [r for r in rows if r.owner != "unknown" and r.owner in known_mods]

    return {
        "rows": rows,
        "sizes": sizes,
        "total_lines": len(rows),          # = 日志条目数（判定基准）
        "physical_lines": phys_lines,      # = LF 计数
        "nonblank_lines": nonblank,
        "total_bytes": sum(sizes.values()),
        "by_owner": dict(by_owner),
        "by_class": dict(by_class),
        "by_module": dict(by_module.most_common(30)),
        "mine": mine,
        "mod_names": sorted(known_mods),
        "noise_ratio": round(1 - len(mine) / max(len(rows), 1), 4),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EU4 error.log 分诊")
    ap.add_argument("--file", action="append", default=None,
                    help="指定日志（可多次；默认自动发现全部 error*.log）")
    ap.add_argument("--mod", action="append", default=None,
                    help="指定 mod 目录（可多次；默认读 dlc_load.json 已启用的）")
    ap.add_argument("--only-mine", action="store_true", help="只输出能归属到已知 mod 的行")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)

    _paths.force_utf8_stdout()

    if args.file:
        logs = [p for p in args.file if os.path.isfile(p)]
    else:
        logs = _paths.error_logs()
    if not logs:
        print("找不到 error*.log；用 --file 指定，或确认 EU4_USER_DIR")
        return 2

    # 确定要归属的 mod 目录
    mod_dirs: list[str] = []
    if args.mod:
        mod_dirs = [p for p in args.mod if os.path.isdir(p)]
    else:
        user = _paths.find_user_dir()
        mod_root = _paths.mod_dir()
        if mod_root:
            for name in _paths.enabled_mod_names(user):
                cand = mod_root / name
                if cand.is_dir():
                    mod_dirs.append(str(cand))
            # .mod 指向的外部目录也纳入
            for m in _paths.enabled_mods(user):
                p = mod_root / os.path.basename(m)
                if p.is_file():
                    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.strip().startswith("path"):
                            val = line.split("=", 1)[1].strip().strip('"').replace("/", os.sep)
                            if os.path.isdir(val):
                                mod_dirs.append(val)

    data = triage(logs, mod_dirs, [])

    if args.json:
        print(json.dumps({
            "logs": data["sizes"],
            "physical_lines": data["physical_lines"],
            "nonblank_lines": data["nonblank_lines"],
            "log_entries": data["total_lines"],
            "total_bytes": data["total_bytes"],
            "by_owner": data["by_owner"],
            "by_class": data["by_class"],
            "by_module": data["by_module"],
            "mod_names": data["mod_names"],
            "noise_ratio": data["noise_ratio"],
            "issues": [r.as_dict() for r in (data["mine"] if args.only_mine else data["rows"])],
        }, ensure_ascii=False, indent=1))
        return 0

    print(f"扫描 {len(logs)} 份日志 / {data['total_bytes']:,} B")
    print(f"   物理行 {data['physical_lines']:,} | 非空行 {data['nonblank_lines']:,} "
          f"| 日志条目 {data['total_lines']:,}  ← 判定基准是【条目数】")
    for name, size in data["sizes"].items():
        print(f"   {name:24s} {size:>10,} B")
    print()
    print("按归属：")
    for owner, n in sorted(data["by_owner"].items(), key=lambda kv: -kv[1]):
        pct = n / max(data["total_lines"], 1) * 100
        print(f"   {owner:28s} {n:8d}  ({pct:.1f}%)")
    print(f"\n噪声占比（无法归属到已知 mod 的行）：{data['noise_ratio']*100:.1f}%")
    print()
    print("按错误类型：")
    for cls, n in sorted(data["by_class"].items(), key=lambda kv: -kv[1]):
        label = next((l for k, l, _ in ERROR_CLASSES if k == cls), "其他")
        print(f"   {label:22s} {n:8d}")
    print()
    print(f"高频模块（Top {args.top}）：")
    for mod, n in list(data["by_module"].items())[:args.top]:
        print(f"   {mod:34s} {n:6d}")

    rows = data["mine"] if args.only_mine else data["rows"]
    if args.only_mine:
        print(f"\n★ 本项目相关（{len(rows)} 行）：")
        if not rows:
            print("   （无 —— error.log 里没有你自己的报错）")
        for r in rows[:60]:
            print(f"   {r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
