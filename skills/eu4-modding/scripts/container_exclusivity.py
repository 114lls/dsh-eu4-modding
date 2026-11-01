"""容器独占度分析 —— 找出「只在某一类容器里出现」的可疑名字。

为什么需要它
-----------
手写排除清单（`scale` / `potential` / `custom_trigger_tooltip` …）必然不完整。
实测就漏了 `force_potential`：它在 vanilla 出现 6 次，**全部**在
`common/institutions/00_Core.txt` 的权重 `modifier` 块内，语义是
「potential 为假时隐藏此权重项」—— 是权重块的**控制字段**，不是触发器。

可实现的通用判据
---------------
  1. 扫全量，对每个名字记录「它出现过的容器类型集合」（容器 = path 里最近的语境 key）
  2. 集合大小 == 1 且该容器属于权重/控制类 → 标记为控制字段候选
  3. 人工确认一次，写进 build_reference 的 CONTROL_FIELDS 常量

  对比：真触发器会横跨多种容器（prestige 在 mean_time_to_happen / ai_chance /
  limit / trigger … 都出现）；控制字段只在权重块里。

用法
----
    python container_exclusivity.py --game <游戏目录>          # 只看权重类容器
    python container_exclusivity.py --game <游戏目录> --all    # 全部容器类型
    python container_exclusivity.py --game <游戏目录> --name prestige
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _paths
import build_reference as B
import pdx_parser

__all__ = ["analyze", "scan_containers"]

# 语境容器 → 该容器内出现的名字
def scan_containers(game: str, dirs: list[str] | None = None) -> dict[str, Counter]:
    """返回 {容器名: Counter(名字 -> 次数)}。

    「容器」定义为 path 里**最近的语境 key**（与 name_scope 同一套判定），
    这样得到的分类和实际判定逻辑一致，不会出现两套口径。
    """
    dirs = dirs or B.TXT_DIRS
    out: dict[str, Counter] = defaultdict(Counter)

    for d in dirs:
        root_d = os.path.join(game, d)
        if not os.path.isdir(root_d):
            continue
        for root, _s, fs in os.walk(root_d):
            for f in sorted(fs):
                if not f.endswith(".txt") or f.startswith("."):
                    continue
                full = os.path.join(root, f)
                try:
                    tree, _e, _i = pdx_parser.parse_file_info(full)
                except Exception:  # noqa: BLE001
                    continue
                for path, entry, ancestors in tree.walk_full():
                    key = entry.key
                    if not key or entry.is_bare:
                        continue
                    scope = B.name_scope(path, ancestors)
                    if scope is None:
                        continue
                    # 最近的语境容器
                    container = "<none>"
                    for k in reversed(path):
                        if k in B.WEIGHT_CONTAINERS or k == "modifier" \
                           or k in B.TRIGGER_MARKERS or k in B.EFFECT_MARKERS:
                            container = k
                            break
                    out[container][key] += 1
    return out


def analyze(game: str, weight_only: bool = True) -> list[dict]:
    """返回独占名字清单（每个名字只在一种容器里出现过）。"""
    matrix = scan_containers(game)
    name_containers: dict[str, list[str]] = defaultdict(list)
    name_counts: dict[str, int] = defaultdict(int)
    for container, counter in matrix.items():
        for name, n in counter.items():
            name_containers[name].append(container)
            name_counts[name] += n

    rows = []
    for name, conts in name_containers.items():
        if len(conts) != 1:
            continue
        c = conts[0]
        if weight_only and c not in B.WEIGHT_CONTAINERS and c != "modifier":
            continue
        rows.append({
            "name": name,
            "container": c,
            "count": name_counts[name],
            "exclusive": True,
        })
    rows.sort(key=lambda r: (-r["count"], r["name"]))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EU4 名字的容器独占度分析")
    ap.add_argument("--game", default=None)
    ap.add_argument("--all", action="store_true", help="列出全部容器类型，不只权重类")
    ap.add_argument("--name", default=None, help="只看某个名字跨哪些容器")
    ap.add_argument("--limit", type=int, default=80)
    args = ap.parse_args(argv)

    _paths.force_utf8_stdout()
    game = args.game or _paths.find_game_dir()
    if not game:
        print("找不到游戏目录，用 --game 指定")
        return 2
    game = str(game)

    matrix = scan_containers(game)

    if args.name:
        hits = [(c, cnt[args.name]) for c, cnt in matrix.items() if cnt[args.name]]
        print(f"{args.name} 出现在 {len(hits)} 种容器里（真名字通常跨多种，控制字段只有一种）:")
        for c, n in sorted(hits, key=lambda kv: -kv[1]):
            print(f"   {c:30s} {n:6d}")
        return 0

    rows = analyze(game, weight_only=not args.all)
    print(f"容器类型总数: {len(matrix)}")
    print(f"独占名字（只在 1 种容器内出现）: {len(rows)}")
    print()
    print(f"{'名字':38s} {'容器':26s} {'次数':>6s}")
    for r in rows[:args.limit]:
        print(f"{r['name']:38s} {r['container']:26s} {r['count']:6d}")
    if len(rows) > args.limit:
        print(f"… 还有 {len(rows) - args.limit} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
