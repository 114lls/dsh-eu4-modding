"""L1 验收：解析器在 vanilla 语料上的健壮性与覆盖率基准。

这是**验收用**的开发工具，不是技能包的运行脚本。它只读游戏目录，不改任何文件。

扫描口径（★ 基准数字必须可复现，所以口径写死在这里）
------------------------------------------------------
  * 顶层目录仅 4 个：events / common / decisions / missions（子目录自动递归，
    **不要**再把 common/scripted_effects 之类单列，否则会重复计入）
  * 去重键 = os.path.normcase(os.path.normpath(绝对路径))，**不是原始字符串**
    —— 否则 `common\\x` 与 `common/x` 被当成两个文件。
    上一轮 2654 vs 2624 的 30 之差 = 15 scripted_effects + 1 on_actions
    + 3 event_modifiers + 11 scripted_triggers，正是这个 bug。
  * 不含隐藏文件；localisation/ 的 .yml 单独跑 --yml 回归

用法：
    python tests/bench_vanilla.py --dirs        # 只打印口径（复现用）
    python tests/bench_vanilla.py               # .txt 抽样 200
    python tests/bench_vanilla.py --all         # .txt 全量
    python tests/bench_vanilla.py --yml         # .yml 全量
    python tests/bench_vanilla.py --all --yml   # 两者都跑

判定：
    .txt : 0 崩溃、0 error（vanilla 是引擎认可的语法）
    .yml : 全部 UTF-8 + BOM、0 error
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "eu4-modding", "scripts"))
sys.path.insert(0, HERE)

import _paths  # noqa: E402
import pdx_parser  # noqa: E402
import loc_parser  # noqa: E402

TXT_DIRS = ["events", "common", "decisions", "missions", "customizable_localization"]
YML_DIRS = ["localisation"]

# ★ 排除清单：语料范围直接决定白名单召回率，所以排除项必须逐条给理由。
#   实测数据见每行注释（vanilla v1.37.5.0，全盘 .txt 8200 个）。
EXCLUDED_TXT_DIRS = {
    "history": (
        "5287 个 / 327,169 行。**含 9273 处 add_core 等效果用法**（如 1-Uppland.txt:3 `add_core = SWE`），"
        "所以它并非纯数据。但其中的 key 绝大多数是省份归属/君主属性等历史数据字段，"
        "统计效果/触发器用法会引入大量同形不同义的噪声。"
        "→ 现阶段排除；lint 需要查「某效果是否用过」时再按需抽样，不纳入白名单主统计。"
    ),
    "map": (
        "145 个 / 84,012 行 / 26,337 个 key= 。是地图拓扑与区域定义（areas/regions/default.map 等），"
        "无效果/触发器语义 → 排除。"
    ),
    "gfx": (
        "9 个 / 1,429 行。图形资源定义（.gfx 的 .txt 形式），无脚本语义 → 排除。"
    ),
    "interface": (
        "4 个 / 15,443 行 / 6,625 个 key= 。GUI 布局定义（.gui 的 .txt 形式），"
        "key 是 GUI 控件而非效果 → 排除。"
    ),
    "hints": (
        "1 个 / 1,275 行。触发式提示定义（window/title/text/trigger），结构上像脚本但用途单一，"
        "对效果/触发器白名单无增量 → 排除（作为 bonus 内容类型单独写进 references）。"
    ),
    "tutorial": (
        "10 个 / 5,617 行。教程脚本，与 hints 同构，无增量 → 排除。"
    ),
    "tests": (
        "4 个 / 42 行。AI 测试脚本（ai_test_*.txt），key 极少且非脚本语法 → 排除。"
    ),
    "patchnotes": (
        "32 个。更新日志纯文本，非脚本 → 排除。"
    ),
    "builtin_dlc / dlc_metadata / licenses / plugins / music / sound / soundtrack": (
        "各 0-14 个。元数据、许可证文本、插件配置 → 排除。"
    ),
    "<游戏根目录>": (
        "11 个 .txt（checksum_manifest / trigger_profile / ThirdPartyLicenses 等）。"
        "trigger_profile.txt 是运行时产出的性能剖析，**不是脚本**；"
        "含 Unicode Patch 的 README 与字体清单 → 全部排除。"
    ),
}


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def collect(game: str, dirs: list[str], suffix: str = ".txt") -> list[str]:
    """递归收集。★ 去重用 normcase/normpath，避免分隔符差异导致重复计数。"""
    out: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        p = os.path.join(game, d)
        if not os.path.isdir(p):
            continue
        for root, _sub, files in os.walk(p):
            for f in sorted(files):
                if not f.endswith(suffix) or f.startswith("."):
                    continue
                full = os.path.join(root, f)
                key = _norm(full)
                if key in seen:
                    continue
                seen.add(key)
                out.append(full)
    return out


def _rel(game: str):
    g = game + os.sep
    return lambda s: s[len(g):] if s.startswith(g) else s


# --------------------------------------------------------------------------- #
# .txt 基准
# --------------------------------------------------------------------------- #
def bench_txt(game: str, args) -> bool:
    files = collect(game, TXT_DIRS)
    print(f"[.txt] 口径 {TXT_DIRS} → {len(files)} 个文件")
    if not files:
        return False

    sample = files if args.all else random.Random(args.seed).sample(files, min(args.sample, len(files)))
    rel = _rel(game)

    t0 = time.time()
    entries = lines = bytes_total = 0
    bom_files = 0
    error_files: list[tuple[str, list]] = []
    warn_codes: Counter[str] = Counter()
    crash: list[tuple[str, str]] = []
    encodings: Counter[str] = Counter()

    for p in sample:
        try:
            tree, errs, info = pdx_parser.parse_file_info(p)
        except Exception as exc:  # noqa: BLE001 —— 基准就是要抓崩溃
            crash.append((p, f"{type(exc).__name__}: {exc}"))
            continue
        entries += len(tree.entries)
        lines += info.lines
        bytes_total += info.bytes
        encodings[info.encoding] += 1
        if info.bom:
            bom_files += 1
        real = [e for e in errs if e.severity == "error"]
        warns = [e for e in errs if e.severity == "warning"]
        for w in warns:
            warn_codes[w.code or "?"] += 1
        if real:
            error_files.append((p, real))

    dt = time.time() - t0
    print(f"[.txt] {len(sample)} 文件 / {lines:,} 行 / {bytes_total/1048576:.2f} MB / {dt:.2f}s "
          f"({len(sample)/max(dt,1e-9):.0f} 文件/秒)")
    print(f"[.txt] 顶层 entry {entries:,} | 崩溃 {len(crash)} | 含 ERROR 的文件 {len(error_files)} (应为 0)")
    print(f"[.txt] warning {sum(warn_codes.values())} {dict(warn_codes)}")
    print(f"[.txt] 编码 {dict(encodings)} | BOM 文件 {bom_files}")

    for p, msg in crash[:args.show_errors]:
        print(f"   CRASH {rel(p)}  {msg}")
    for p, es in error_files[:args.show_errors]:
        print(f"   ERROR {rel(p)}")
        for e in es[:5]:
            print(f"         {e.line}:{e.col} {e.code} {e.message}")

    ok = not crash and not error_files
    print(f"[.txt] 判定 {'PASS' if ok else 'FAIL'}")
    return ok


# --------------------------------------------------------------------------- #
# .yml 基准（578 个正样本全在这一类文件里）
# --------------------------------------------------------------------------- #
def bench_yml(game: str, args) -> bool:
    files = collect(game, YML_DIRS, ".yml")
    print(f"[.yml] 口径 {YML_DIRS} → {len(files)} 个文件")
    if not files:
        return False

    rel = _rel(game)
    t0 = time.time()
    no_bom: list[str] = []
    bad_enc: list[str] = []
    err_files: list[tuple[str, list]] = []
    entries = 0
    langs: Counter[str] = Counter()
    roots: Counter[str] = Counter()
    warn_codes: Counter[str] = Counter()
    crash: list[tuple[str, str]] = []

    for p in files:
        try:
            loc = loc_parser.parse_localisation_file_info(p)
        except Exception as exc:  # noqa: BLE001
            crash.append((p, f"{type(exc).__name__}: {exc}"))
            continue
        entries += len(loc.entries)
        if loc.info:
            if not loc.info.bom:
                no_bom.append(p)
            if loc.info.encoding != "utf-8":
                bad_enc.append(p)
        langs[loc.lang or "<none>"] += 1
        roots[loc.root_key or "<none>"] += 1
        real = [e for e in loc.errors if e.severity == "error"]
        for e in loc.errors:
            if e.severity == "warning":
                warn_codes[e.code or "?"] += 1
        if real:
            err_files.append((p, real))

    dt = time.time() - t0
    print(f"[.yml] {len(files)} 文件 / {entries:,} 条 key / {dt:.2f}s")
    print(f"[.yml] 语言分布 {dict(langs)}")
    print(f"[.yml] 根键分布 {dict(roots)}")
    print(f"[.yml] 缺 BOM {len(no_bom)} (应为 0) | 非 UTF-8 {len(bad_enc)} (应为 0) | 崩溃 {len(crash)} | 含 ERROR {len(err_files)} (应为 0)")
    print(f"[.yml] warning {sum(warn_codes.values())} {dict(warn_codes)}")

    for p in no_bom[:args.show_errors]:
        print(f"   NO-BOM {rel(p)}")
    for p in bad_enc[:args.show_errors]:
        print(f"   BAD-ENC {rel(p)}")
    for p, es in err_files[:args.show_errors]:
        print(f"   ERROR {rel(p)}")
        for e in es[:4]:
            print(f"         {e.line} {e.code} {e.message}")

    ok = not no_bom and not bad_enc and not crash and not err_files
    print(f"[.yml] 判定 {'PASS' if ok else 'FAIL'}")
    return ok


def show_dirs(game: str) -> int:
    """打印口径明细，供别人复现基准数字。"""
    print(f"game = {game}")
    print(f"TXT_DIRS = {TXT_DIRS}")
    print(f"YML_DIRS = {YML_DIRS}")
    print("去重键   = normcase(normpath(abspath(path)))")
    total_t = total_y = 0
    for d in TXT_DIRS:
        n = len(collect(game, [d]))
        total_t += n
        print(f"  {d:28s} .txt {n}")
    print(f"  {'合计':28s} .txt {total_t}")
    for d in YML_DIRS:
        n = len(collect(game, [d], ".yml"))
        total_y += n
        print(f"  {d:28s} .yml {n}")

    print("\n【排除目录清单 + 理由】")
    for k, v in EXCLUDED_TXT_DIRS.items():
        print(f"  {k}")
        print(f"      {v}")

    grand = sum(1 for _ in _walk_all_txt(game))
    print(f"\n参考：全盘 .txt = {grand}，纳入语料 = {total_t} "
          f"（{total_t/max(grand,1)*100:.0f}%）")
    return 0


def _walk_all_txt(game: str):
    for root, _sub, files in os.walk(game):
        for f in sorted(files):
            if f.endswith(".txt") and not f.startswith("."):
                yield os.path.join(root, f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EU4 解析器 vanilla 基准")
    ap.add_argument("--game", default=None)
    ap.add_argument("--all", action="store_true", help=".txt 全量")
    ap.add_argument("--yml", action="store_true", help="跑 .yml 回归")
    ap.add_argument("--dirs", action="store_true", help="只打印扫描口径")
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--show-errors", type=int, default=8)
    args = ap.parse_args(argv)

    _paths.force_utf8_stdout()
    game = str(args.game or _paths.find_game_dir() or "")
    if not game or not os.path.isdir(game):
        print("找不到游戏目录；用 --game 指定或设 EU4_GAME")
        return 2

    if args.dirs:
        return show_dirs(game)

    results: list[tuple[str, bool]] = []
    if args.yml:
        results.append((".yml", bench_yml(game, args)))
        if args.all:
            results.append((".txt", bench_txt(game, args)))
    else:
        results.append((".txt", bench_txt(game, args)))

    print("\n" + "  ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results))
    return 0 if all(v for _, v in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
