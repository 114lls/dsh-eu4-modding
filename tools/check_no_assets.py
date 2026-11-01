"""「不含游戏素材」守卫 —— 可作为可执行检查，也供 CI 调用。

为什么它必须存在，且必须能被反向验证
---------------------------------
「不含任何游戏素材」是本项目的发布约束。约束类实现有个通病：
**写错了不会报错，只在最需要它的时候不生效**。
含素材的包一旦发出去，事后发现就来不及了。

所以这个守卫做两件事：
  1. 正常模式：扫仓库里有没有疑似素材文件，有则**非 0 退出**
  2. --selftest：往临时目录塞一个假 .dds，验证自己**确实会报错**

只有第 2 步通过，第 1 步的「全部干净」才有意义。

扫描范围
--------
只看被 git 跟踪的文件（`git ls-files`），因为那才是会进包的东西。
未被跟踪的本地文件（如用户自己的贴图）不该导致失败。

用法
----
    python tools/check_no_assets.py              # 正常检查
    python tools/check_no_assets.py --selftest   # 验证守卫自身有效
    python tools/check_no_assets.py --list       # 列出所有被跟踪文件
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

# ★ 导入即把 stdout/stderr 切到 UTF-8。
#   必须写在模块级而非 __main__ 分支：测试会 import 本模块后直接调 selftest()，
#   那时不走 __main__，而 Windows runner 的默认编码是 cp1252，中文 print 会抛
#   UnicodeEncodeError: 'charmap' codec can't encode characters（CI 实测）。
import _utf8  # noqa: E402,F401

__all__ = ["ASSET_EXTS", "ALLOWED_EXCEPTIONS", "check", "selftest"]

# 疑似游戏素材的扩展名
ASSET_EXTS = (
    ".dds", ".tga", ".png", ".jpg", ".jpeg", ".bmp", ".gif",
    ".ogg", ".wav", ".mp3", ".dll", ".exe",
)

# 允许存在的例外（相对路径精确匹配）：
#   tests/fixtures/asset_guard/NOT_A_REAL_ASSET.dds
#   —— 它是**故意**放的守卫自测样本，内容是空文本，不是真 DDS。
ALLOWED_EXCEPTIONS = {
    "tests/fixtures/asset_guard/NOT_A_REAL_ASSET.dds",
}


def git_files(repo_root: str) -> list[str]:
    """返回被 git 跟踪的文件（相对路径，正斜杠）。"""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root, capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        # 不是 git 仓库（比如解压的 zip）→ 退化为扫目录，但排除常见生成物
        result = []
        for root, _sub, files in os.walk(repo_root):
            rel_root = os.path.relpath(root, repo_root).replace(os.sep, "/")
            if any(p in rel_root.split("/") for p in
                   ("node_modules", "__pycache__", "reference", ".git")):
                continue
            for f in files:
                result.append(f if rel_root == "." else f"{rel_root}/{f}")
        return result
    return [ln for ln in out.splitlines() if ln.strip()]


def check(repo_root: str, quiet: bool = False) -> tuple[bool, list[str]]:
    """返回 (是否干净, 违规文件列表)。"""
    files = git_files(repo_root)
    bad: list[str] = []
    for rel in files:
        low = rel.lower()
        if not low.endswith(ASSET_EXTS):
            continue
        if rel in ALLOWED_EXCEPTIONS:
            continue
        bad.append(rel)
    ok = not bad
    if not quiet:
        print(f"扫描 {len(files)} 个被跟踪文件")
        print(f"素材扩展名 = {', '.join(ASSET_EXTS)}")
        print(f"允许例外   = {sorted(ALLOWED_EXCEPTIONS) or '无'}")
        if bad:
            print("\n发现疑似游戏素材（禁止提交）:")
            for b in bad:
                print(f"   {b}")
        else:
            print("\nOK：未发现素材文件")
    return ok, bad


def selftest() -> bool:
    """★ 反向验证：故意塞一个假素材，确认守卫确实会报错。

    这是「约束必须能被反向验证」的落地 —— 否则守卫可能是个空断言。
    """
    tmp = tempfile.mkdtemp(prefix="eu4guard_")
    try:
        fake = os.path.join(tmp, "FAKE_ASSET.dds")
        with open(fake, "wb") as fh:
            fh.write(b"not a real dds")     # 任意字节，只看扩展名
        ok, bad = check(tmp, quiet=True)
        print(f"自测：临时目录塞入 FAKE_ASSET.dds")
        print(f"      check() 返回 ok={ok} 违规={bad}")
        if ok:
            print("★ 守卫失效：塞了 .dds 却报干净 —— 约束是空断言")
            return False
        print("守卫有效：能抓到 .dds")
        return True
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="不含游戏素材 守卫")
    ap.add_argument("--repo", default=None, help="仓库根目录（默认当前目录）")
    ap.add_argument("--selftest", action="store_true", help="验证守卫自身有效")
    ap.add_argument("--list", action="store_true", help="列出被跟踪文件")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    repo = args.repo or os.getcwd()

    if args.selftest:
        return 0 if selftest() else 1

    if args.list:
        for f in git_files(repo):
            print(f)
        return 0

    ok, _bad = check(repo, quiet=args.quiet)
    if not ok:
        print("\n★ 发布约束被破坏：包内不得含任何游戏素材")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
