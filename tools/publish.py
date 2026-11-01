"""发布前检查 + 一键发布到 npm。

为什么做成脚本
------------
`npm login` 是交互式的（需要浏览器/终端输入账号密码或 token），**无法代做**。
所以拆成两段：
  你：npm login（一次性）
  我/脚本：跑完所有检查 → npm publish

这个脚本只做「能自动化的部分」，且在发布前**必须**全部检查通过才允许发。

用法
----
    python tools/publish.py --check      # 只检查（默认）
    python tools/publish.py --publish    # 检查通过后真的发布
    python tools/publish.py --publish --dry   # 检查 + npm publish --dry-run

检查项（任一不过就拒绝发布）
--------------------------
  1. npm 已登录（npm whoami）—— 未登录直接停，提示先 npm login
  2. 包名在远端未被占用（`npm view <name>` 404 = 可用；已有版本则比对 version）
  3. `npm pack --dry-run` 产物不含 reference/ tests/ tools/ __pycache__ *.pyc
  4. 产物文件数 > 20 且 < 60（太多说明误打包，太少说明漏文件）
  5. 三个守卫 --selftest 全过
  6. Python 单测全绿
  7. Node 测试全绿
  8. digest 与基线一致（证明参照模组没被误改）
  9. 根目录无 tmp_*
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
NPM = "npm.cmd" if os.name == "nt" else "npm"

FORBIDDEN_PREFIXES = ("reference/", "tests/", "tools/", ".github/")
FORBIDDEN_PATTERNS = ("__pycache__", ".pyc", ".pyo")


def _run(cmd, cwd=ROOT, timeout=600) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=False,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)


def check_npm_auth() -> tuple[bool, str]:
    rc, out = _run([NPM, "whoami"])
    if rc == 0:
        return True, out.strip().splitlines()[-1] if out.strip() else "已登录"
    return False, "未登录（先跑: npm login）"


def check_name_available(name: str, version: str) -> tuple[bool, str]:
    rc, out = _run([NPM, "view", name, "version"])
    if rc != 0 and "404" in out:
        return True, f"{name} 在远端不存在，可直接发布"
    if rc == 0:
        remote = out.strip()
        if remote == version:
            return False, f"远端已有 {name}@{remote}，与本地 version 相同 —— 请先改 version"
        return True, f"远端已有 {remote}，本地 {version} 是新的，可发布"
    return False, f"查询失败：{out.strip()[:120]}"


def check_pack_contents() -> tuple[bool, str, list[str]]:
    rc, out = _run([NPM, "pack", "--dry-run", "--json"])
    if rc != 0:
        return False, f"npm pack 失败：{out[:150]}", []
    # npm 的输出前面可能有 warning，取第一个 { 到最后一个 }
    start, end = out.find("["), out.rfind("]")
    if start < 0:
        start, end = out.find("{"), out.rfind("}")
        if start < 0:
            return False, "pack --json 输出无法解析", []
        payload = out[start:end + 1]
    else:
        payload = out[start:end + 1]
    try:
        data = json.loads(payload)
    except ValueError as exc:
        return False, f"pack JSON 解析失败：{exc}", []

    item = data[0] if isinstance(data, list) else data
    files = [f.get("path", "") for f in item.get("files", [])]

    bad = []
    for f in files:
        if any(f.startswith(p) for p in FORBIDDEN_PREFIXES):
            bad.append(f)
        elif any(pat in f for pat in FORBIDDEN_PATTERNS):
            bad.append(f)

    if bad:
        return False, f"产物含不该进包的文件（{len(bad)} 个）：{bad[:5]}", files
    if not (20 <= len(files) <= 60):
        return False, f"文件数异常：{len(files)}（预期 20-60）", files
    size_kb = round(item.get("size", 0) / 1024, 1)
    return True, f"{len(files)} 个文件 / {size_kb} KB，内容干净", files


def check_guards() -> tuple[bool, str]:
    parts = []
    ok = True
    for tool, extra in (
        ("tools/check_no_assets.py", "--selftest"),
        ("tools/check_no_reference.py", "--selftest"),
        ("tools/check_doc_links.py", "--selftest"),
        ("tools/check_no_assets.py", None),
        ("tools/check_no_reference.py", None),
        ("tools/check_doc_links.py", None),
    ):
        cmd = [PY, tool] + ([extra] if extra else [])
        rc, out = _run(cmd)
        name = f"{os.path.basename(tool)}{' ' + extra if extra else ''}"
        parts.append(f"{name}={'PASS' if rc == 0 else 'FAIL'}")
        ok = ok and rc == 0
    return ok, "  ".join(parts)


def check_python_tests() -> tuple[bool, str]:
    rc, out = _run([PY, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])
    m = re.search(r"Ran (\d+) tests", out)
    skipped = re.search(r"skipped=(\d+)", out)
    n = m.group(1) if m else "?"
    s = skipped.group(1) if skipped else "0"
    return rc == 0, f"Ran={n} skipped={s} " + ("OK" if rc == 0 else "FAILED")


def check_node_tests() -> tuple[bool, str]:
    rc, out = _run(["node", "--test", "tests/smoke.test.mjs",
                    "tests/preset.test.mjs", "tests/preset-required.test.mjs"])
    passed = re.search(r"pass (\d+)", out)
    failed = re.search(r"fail (\d+)", out)
    return rc == 0, f"pass={passed.group(1) if passed else '?'} fail={failed.group(1) if failed else '?'}"


def check_digest() -> tuple[bool, str]:
    mod = os.path.expanduser(
        "~/Documents/Paradox Interactive/Europa Universalis IV/mod/posthumous_names")
    if not os.path.isdir(mod):
        return True, "参照模组不存在，跳过"
    rc, out = _run([PY, os.path.join(ROOT, "skills/eu4-modding/scripts/mod_digest.py"), mod])
    actual = out.strip().splitlines()[-1] if out.strip() else ""
    expected = "ed7ec40a45c2718e"
    return actual == expected, f"{actual} " + ("（与基线一致）" if actual == expected else f"（基线 {expected}）")


def check_no_tmp() -> tuple[bool, str]:
    tmps = [f for f in os.listdir(ROOT) if f.startswith("tmp_")]
    return not tmps, (f"{len(tmps)} 个：{tmps[:3]}" if tmps else "0 个")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="发布前检查 + npm publish")
    ap.add_argument("--check", action="store_true", help="只检查（默认）")
    ap.add_argument("--publish", action="store_true", help="检查通过后真的发布")
    ap.add_argument("--dry", action="store_true", help="用 npm publish --dry-run 演练")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as fh:
        pkg = json.load(fh)
    name, version = pkg["name"], pkg["version"]

    print(f"发布目标：{name}@{version}")
    print("=" * 64)

    results: list[tuple[str, bool, str]] = []

    def add(label, ok, note):
        results.append((label, ok, note))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:22s} {note}")

    add("npm 登录", *check_npm_auth())
    add("包名可用", *check_name_available(name, version))
    ok, note, files = check_pack_contents()
    add("产物内容", ok, note)
    add("三守卫", *check_guards())
    add("Python 单测", *check_python_tests())
    add("Node 测试", *check_node_tests())
    add("digest 基线", *check_digest())
    add("根目录无 tmp_", *check_no_tmp())

    failed = [r for r in results if not r[1]]
    print("=" * 64)
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")

    if failed:
        print("\n★ 有检查未通过，拒绝发布：")
        for label, _ok, note in failed:
            print(f"   {label}: {note}")
        if any(lb == "npm 登录" for lb, _o, _n in failed):
            print("\n下一步：在终端执行 npm login（交互式，我无法代做），然后重跑本脚本。")
        return 1

    print("\n全部检查通过。")

    if not args.publish and not args.dry:
        print("\n（仅检查模式。要发布：加 --publish；要演练：加 --dry）")
        return 0

    cmd = [NPM, "publish", "--access", "public"]
    if args.dry:
        cmd.append("--dry-run")
        print("\n执行 publish --dry-run（不会真的上传）…")
    else:
        print("\n执行 publish（真的上传到 npm registry）…")

    rc, out = _run(cmd, timeout=900)
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-12:]
    for ln in tail:
        print("  " + ln[:110])

    if rc == 0:
        print(f"\n{'演练' if args.dry else '发布'}成功：{name}@{version}")
        if not args.dry:
            print(f"  安装：npm i {name}")
            print(f"  查看：https://www.npmjs.com/package/{name}")
        return 0
    print(f"\n★ {'演练' if args.dry else '发布'}失败（exit={rc}）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
