"""回归统计器 —— 把「通过 / 跳过 / 未执行」分开报，不混成一个数字。

为什么需要它
-----------
2026-11-01 实测：同一套测试，无 `EU4_CWT_DIR` 时 skipped=14，有它时 skipped=3。
差出的 11 个是白名单覆盖相关断言（treasury 分类、覆盖率、cross_check、corpus_only 分级）——
它们**没有执行**，不是「通过」。

但「150 tests OK」这种写法会让人误以为 150 个都验过了。
复查者原话：「skip 意味着没执行，而『没执行』和『通过』在发布判定里不是一回事。」

正确报法：
    Ran=154  executed=151  skipped=3   {[NEED-CWT]: 0, [SLOW]: 3}

用法
----
    python tools/regression_report.py                    # 当前环境
    python tools/regression_report.py --no-cwt           # 遮掉 EU4_CWT_DIR，看最保守口径
    python tools/regression_report.py --with-slow        # 顺带跑 sabotage 慢测（约 +105s）
    python tools/regression_report.py --json             # 机器可读
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

__all__ = ["run_report"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TAGS = ("[NEED-CWT]", "[NEED-MOD]", "[NEED-REF]", "[NEED-LOG]", "[SLOW]")


def run_report(env_override: dict | None = None, timeout: int = 900) -> dict:
    env = os.environ.copy()
    for k in ("EU4_CWT_DIR", "EU4_RUN_SABOTAGE", "EU4_GAME"):
        env.pop(k, None)
    if env_override:
        env.update(env_override)

    r = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests",
         "-p", "test_*.py", "-v"],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=timeout,
    )
    out = (r.stdout or "") + (r.stderr or "")

    ran = re.search(r"Ran (\d+) tests", out)
    skipped_total = re.search(r"skipped=(\d+)", out)
    failures = re.search(r"FAILED .*", out)

    n = int(ran.group(1)) if ran else 0
    s = int(skipped_total.group(1)) if skipped_total else 0

    by_tag = {t: 0 for t in TAGS}
    untagged: list[str] = []
    for ln in out.split("\n"):
        m = re.search(r"skipped '(\[?[A-Z\-]*\]?)", ln)
        if not m or "skipped=" in ln:
            continue
        tag = m.group(1)
        if tag in by_tag:
            by_tag[tag] += 1
        else:
            untagged.append(ln.strip()[:90])

    return {
        "ran": n,
        "skipped": s,
        "executed": n - s,
        "ok": r.returncode == 0,
        "failures": failures.group(0) if failures else None,
        "by_tag": by_tag,
        "untagged_skips": untagged,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回归统计：通过/跳过/未执行 分开报")
    ap.add_argument("--no-cwt", action="store_true", help="遮掉 EU4_CWT_DIR（最保守口径）")
    ap.add_argument("--with-slow", action="store_true", help="跑 sabotage 慢测（约 +105s）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    # ★ run_report 会先 pop 掉 EU4_CWT_DIR 等变量再 update(override)，
    #   所以「保留当前环境的 CWT_DIR」必须显式写进 override，不能指望它自动继承。
    #   早期版本没做这一步 → CLI 跑 --no-cwt 与不跑都一样（都 14），看起来像选项失效。
    override: dict = {}
    if not args.no_cwt:
        cur = os.environ.get("EU4_CWT_DIR")
        if cur:
            override["EU4_CWT_DIR"] = cur
    else:
        override["EU4_CWT_DIR"] = ""
    if args.with_slow:
        override["EU4_RUN_SABOTAGE"] = "1"

    rep = run_report(override)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if rep["ok"] else 1

    print(f"Ran={rep['ran']}  executed={rep['executed']}  skipped={rep['skipped']}")
    print(f"分类: {rep['by_tag']}")
    if rep["untagged_skips"]:
        print("★ 有未标注前缀的 skip（统计口径失效）：")
        for ln in rep["untagged_skips"][:5]:
            print(f"   {ln}")
    if rep["failures"]:
        print(f"结果: {rep['failures']}")
    else:
        print("结果: OK")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
