"""项目自检总览 —— 一条命令跑完所有可自动化的验收项。

为什么需要它
-----------
发布前要跑的东西散在 5 个地方（单测 / 冒烟 / 三守卫 / 文档 / 打包）。
人工逐个跑容易漏，漏掉的那一项恰恰可能是「在最需要时不生效」的约束。
这个脚本把它们汇总成一个 0/1 判定。

它不替代 CI —— CI 在双 OS 双版本上跑；它只给你本地一个明确的「现在能不能发」。

用法
----
    python tools/preflight.py
    python tools/preflight.py --quiet     # 只输出结论
    python tools/preflight.py --skip-env  # 跳过需要游戏目录的项（无游戏机器可用）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

__all__ = ["run_all"]

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _run(cmd: list[str], cwd: str, shell_extra: str | None = None) -> tuple[bool, str]:
    """跑子命令。★ 必须显式指定 encoding：

    中文 Windows 上 subprocess 默认用 GBK 解码子进程输出，而我们的脚本
    内部已经 reconfigure(encoding='utf-8') 输出 UTF-8，于是会报：
        UnicodeDecodeError: 'gbk' codec can't decode byte 0x94 ...
    这不是显示问题 —— 它会让 preflight 在中文机器上读不到输出、
    把「通过」误判成「失败」。所以统一用 utf-8 + errors='replace'。
    """
    try:
        if shell_extra:
            r = subprocess.run(f"{shell_extra} {' '.join(cmd)}", cwd=cwd,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", shell=True)
        else:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return False, str(exc)


def _last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def run_all(quiet: bool = False, skip_env: bool = False) -> int:
    py = sys.executable
    results: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, note: str = ""):
        results.append((name, ok, note))
        if not quiet:
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {name}" + (f"  — {note}" if note else ""))

    if not quiet:
        print("== 1. 语法 ==")
    ok, out = _run([py, "-m", "compileall", "-q",
                    "skills/eu4-modding/scripts", "tests", "tools"], ROOT)
    add("compileall", ok, _last_line(out) if not ok else "")

    if not quiet:
        print("== 2. Python 单测 ==")
    ok, out = _run([py, "-m", "unittest", "discover", "-s", "tests",
                    "-p", "test_*.py"], ROOT)
    tail = _last_line(out)
    add("unittest", ok, tail)

    if not quiet:
        print("== 3. Node 冒烟 ==")
    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    node_ok, node_out = True, "node 不可用，跳过"
    try:
        r = subprocess.run(["node", "--test", "tests/smoke.test.mjs"],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        node_ok = r.returncode == 0
        node_out = "pass 9" if node_ok else _last_line(r.stdout + r.stderr)
    except OSError:
        node_out = "node 不可用，跳过"
    add("smoke", node_ok, node_out)

    if not quiet:
        print("== 4. 守卫（先自测，再判定）==")
    for tool, extra in (
        ("tools/check_no_assets.py", "--selftest"),
        ("tools/check_no_reference.py", "--selftest"),
        ("tools/check_doc_links.py", "--selftest"),
        ("tools/check_no_assets.py", None),
        ("tools/check_no_reference.py", None),
        ("tools/check_doc_links.py", None),
    ):
        cmd = [py, tool] + ([extra] if extra else [])
        ok, out = _run(cmd, ROOT)
        add(f"{os.path.basename(tool)} {extra or ''}".strip(), ok, _last_line(out))

    if not quiet:
        print("== 5. 打包产物 ==")
    try:
        r = subprocess.run(f"{npm_cmd} pack --dry-run --json", cwd=ROOT,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", shell=True)
        if r.returncode != 0:
            add("npm pack", False, "npm 执行失败")
        else:
            data = json.loads(r.stdout)
            item = data[0] if isinstance(data, list) else data
            names = [f.get("path", "") for f in item.get("files", [])]
            bad_ref = [n for n in names if n.replace("\\", "/").startswith("reference/")]
            bad_assets = [n for n in names if n.lower().endswith(
                (".dds", ".tga", ".png", ".jpg", ".ogg", ".wav", ".mp3", ".dll", ".exe"))]
            add("tarball 无 reference/", not bad_ref, str(bad_ref[:3]))
            add("tarball 无素材", not bad_assets, str(bad_assets[:3]))
            add("tarball 文件数", len(names) > 20, f"{len(names)} files")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        add("npm pack", False, str(exc)[:60])

    if not quiet:
        print("== 6. 约定文件齐全 ==")
    must = [
        "package.json", "cordis.patch.yml", "lib/index.js", "icon.svg",
        "README.md", "README.zh-CN.md", "CHANGELOG.md", "LICENSE", ".gitignore",
        "skills/eu4-modding/SKILL.md", "skills/eu4-modding/INSTALL.md",
        "tests/smoke.test.mjs",
    ]
    missing = [m for m in must if not os.path.exists(os.path.join(ROOT, m))]
    add("约定文件", not missing, str(missing[:5]))

    if not skip_env:
        if not quiet:
            print("== 7. 环境（需游戏目录）==")
        try:
            sys.path.insert(0, os.path.join(ROOT, "skills", "eu4-modding", "scripts"))
            import _paths
            game = _paths.find_game_dir()
            add("游戏目录", bool(game), str(game) if game else "未找到（可用 --skip-env）")
            ref = os.path.join(ROOT, "reference")
            has_ref = os.path.isfile(os.path.join(ref, "effects.json"))
            add("reference/已生成", has_ref,
                "" if has_ref else "未生成（build_reference.py，不影响发布）")
        except Exception as exc:  # noqa: BLE001
            add("环境探测", False, str(exc)[:60])

    failed = [r for r in results if not r[1]]
    if not quiet:
        print()
    print(f"总计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    if failed:
        print("失败项：")
        for name, _ok, note in failed:
            print(f"   {name}  {note}")
        return 1
    print("PREFLIGHT: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发布前自检总览")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--skip-env", action="store_true",
                    help="跳过需要游戏目录的检查")
    args = ap.parse_args(argv)

    # ★ 自己也要用 UTF-8 输出：
    #   中文 Windows 默认 GBK，而我们把子进程输出按 utf-8 读进来
    #   （含 U+FFFD 替换字符），用 GBK 打印会再炸一次。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    return run_all(quiet=args.quiet, skip_env=args.skip_env)


if __name__ == "__main__":
    raise SystemExit(main())
