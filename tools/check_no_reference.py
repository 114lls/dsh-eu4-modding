"""「reference/ 不得进包」守卫 —— 与 assets 守卫同一思路：必须能反向验证。

为什么单独做成工具而不是只写在 CI 里
---------------------------------
`package.json` 的 `files` 是白名单，`reference/` 天然进不去。
但白名单一旦被改动（比如有人图省事写成 `"skills/**/*"` 或加了个 `"**/*.json"`），
约束就静默失效了。所以：
  1. 检查 files 白名单本身不含 reference
  2. **并用 npm pack --dry-run 检查实际产物**（若装了 npm）
  3. --selftest 往白名单里注入一个违规项，验证检查会失败

第 3 步是关键：证明「检查通过」不是因为检查根本没生效。

用法
----
    python tools/check_no_reference.py
    python tools/check_no_reference.py --selftest
    python tools/check_no_reference.py --npm        # 额外跑 npm pack --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

__all__ = ["is_reference_path", "check_files_whitelist", "check_packed"]


def is_reference_path(entry: str) -> bool:
    """★ 只按路径段判断，不能用子串。

    反例：`skills/eu4-modding/references/*.md` 含子串 "reference"，
    但它是技能自带文档，**必须**在包里。
    这条在早期实现里误判过一次。
    """
    norm = entry.replace("\\", "/").strip("./")
    segs = norm.split("/")
    return segs and (segs[0] == "reference")


def check_files_whitelist(pkg: dict) -> tuple[bool, list[str]]:
    files = pkg.get("files", [])
    bad = [f for f in files if is_reference_path(f)]
    return (not bad), bad


def check_packed(repo_root: str) -> tuple[bool, list[str], str]:
    """跑 npm pack --dry-run，检查实际产物里有没有 reference/。

    ★ npm 不可用/执行失败必须返回 ok=False —— **不能静默通过**。

    实测（2026-11-01）本机情况：Windows 上 `npm` 是 `npm.ps1`，
    PowerShell 执行策略禁止运行脚本；而 `subprocess.run(["npm", ...], shell=False)`
    在 Windows 需要 `.cmd`/`.exe` 后缀，找不到就 FileNotFoundError（WinError 2）。

    早期实现把这个异常吞成 `ok=False`，但 `main()` 只在 `--npm` 分支里
    检查 `if bad2:`，没有检查 `ok2` —— 结果 exit 0，**守卫静默通过 = 空断言**。
    这正是「约束写错只在最需要它时不生效」的同一条原则。

    解决办法：Windows 依次试 npm.cmd / npm.exe / npm；全部失败则明确失败。
    """
    candidates = ["npm.cmd", "npm.exe", "npm"] if os.name == "nt" else ["npm"]

    last_err = ""
    for exe in candidates:
        try:
            r = subprocess.run(
                [exe, "pack", "--dry-run", "--json"],
                cwd=repo_root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", shell=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            last_err = f"{exe}: {exc}"
            continue

        if r.returncode != 0:
            last_err = f"{exe} 退出码 {r.returncode}: {(r.stderr or '').strip()[:120]}"
            continue

        try:
            data = json.loads(r.stdout)
        except ValueError:
            last_err = f"{exe} 输出不是合法 JSON"
            continue

        names: list[str] = []
        for item in data if isinstance(data, list) else [data]:
            for f in item.get("files", []):
                names.append(f.get("path", ""))
        bad = [n for n in names if n.replace("\\", "/").startswith("reference/")]
        return (not bad), bad, f"{exe} pack 列出 {len(names)} 个文件"

    return False, [], f"npm 不可用或执行失败（无法检查实际产物）：{last_err}"


def selftest(repo_root: str) -> bool:
    """★ 反向验证：往白名单注入违规项，必须被判为不干净。"""
    pkg_path = os.path.join(repo_root, "package.json")
    with open(pkg_path, encoding="utf-8") as fh:
        pkg = json.load(fh)

    ok, bad = check_files_whitelist(pkg)
    print(f"自测 1：真实 package.json → clean={ok}, 违规={bad}")
    if not ok:
        print("★ 真实 package.json 已违规")
        return False

    poisoned = dict(pkg)
    poisoned["files"] = list(pkg.get("files", [])) + ["reference/effects.json"]
    ok2, bad2 = check_files_whitelist(poisoned)
    print(f"自测 2：注入 'reference/effects.json' → clean={ok2}, 违规={bad2}")
    if ok2:
        print("★ 守卫失效：注入违规项却仍报干净 —— 检查是空断言")
        return False

    # 顺带验证 references/ 不被误判
    ok3, bad3 = check_files_whitelist({"files": ["skills/eu4-modding/references/*.md"]})
    print(f"自测 3：'references/*.md' 不应被误判 → clean={ok3}, 违规={bad3}")
    if not ok3:
        print("★ 误判：把 references/ 当成了 reference/")
        return False

    print("守卫有效：能抓违规、且不误判 references/")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="reference/ 不得进包 守卫")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--npm", action="store_true", help="额外检查 npm pack 实际产物")
    args = ap.parse_args(argv)

    repo = args.repo or os.getcwd()

    if args.selftest:
        return 0 if selftest(repo) else 1

    pkg_path = os.path.join(repo, "package.json")
    with open(pkg_path, encoding="utf-8") as fh:
        pkg = json.load(fh)

    ok, bad = check_files_whitelist(pkg)
    print(f"files 白名单 {len(pkg.get('files', []))} 条")
    if bad:
        print("★ reference/ 出现在白名单里:")
        for b in bad:
            print(f"   {b}")
    else:
        print("OK：白名单不含 reference/")

    rc = 0 if ok else 1

    if args.npm:
        ok2, bad2, note = check_packed(repo)
        print(f"npm pack：{note}")
        if bad2:
            print("★ 实际产物含 reference/:")
            for b in bad2:
                print(f"   {b}")
            rc = 1
        elif not ok2:
            # ★ 关键修复：npm 不可用/执行失败时 ok2=False 且 bad2 为空，
            #   旧代码的 `elif ok2:` 分支会静默跳过 → exit 0（空断言）。
            #   这里必须显式失败，并指明「无法检查」而非「检查无问题」。
            print("★ 检查未能完成：无法确认产物是否含 reference/")
            print("  （npm 不可用 ≠ 产物干净；请先修好 npm 或改用其他方式核验）")
            rc = 1
        else:
            print("OK：实际产物不含 reference/")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
