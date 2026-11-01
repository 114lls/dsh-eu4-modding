"""把源码仓库同步到已安装的插件副本（两处），并给出重启提示。

为什么需要它
-----------
DSH 的插件安装不是软链：实测 `~/.dsh/profiles/desktop/node_modules/dsh-eu4-modding`
是一个**实际副本**（`dsh-purge` / `dsh-cyber-red-ui` 同样如此，说明宿主不跟随软链）。
而插件在运行时读的是副本里的 `SKILL.md` 与 `scripts/`。

所以：改了源码仓库 → 副本还是旧的 → 重启后加载的是旧内容。
这个工具把「同步」和「校验」合成一条命令，避免漏同步。

两处目标
-------
  1. ~/.dsh/plugins/dsh-eu4-modding                    插件本体（file: 依赖指向它）
  2. ~/.dsh/profiles/desktop/node_modules/dsh-eu4-modding  实际被 require 的那份

只同步「会进包」的文件（与 package.json 的 files 白名单一致），
同步后逐文件比对哈希，并列出**没有同步**的（如果两边都缺，说明源码里也没有）。

用法
----
    python tools/sync_plugin.py                  # 同步并校验
    python tools/sync_plugin.py --dry-run        # 只看会改哪些，不动文件
    python tools/sync_plugin.py --verify-only    # 只校验，不同步（CI 用）
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PLUGIN_DIR = os.path.expanduser("~/.dsh/plugins/dsh-eu4-modding")
NM_DIR = os.path.expanduser("~/.dsh/profiles/desktop/node_modules/dsh-eu4-modding")

# 与 package.json files 白名单保持一致的相对路径集合
INCLUDE_EXTS_SKILL = (".md", ".py")
SYNC_TOP = [
    "lib/index.js",
    "lib/preset.js",
    "package.json",
    "cordis.patch.yml",
    "icon.svg",
    "README.md",
    "README.zh-CN.md",
    "CHANGELOG.md",
    "LICENSE",
]
# presets/ 是 agent preset 的源文件：插件启动时读它并 ctx.agentPresets.register()
# 漏同步这一项 → 模式下拉框里没有「EU4 mod mode」
SYNC_PRESET_DIR = "presets"


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def collect() -> list[str]:
    """返回需要同步的相对路径（源码仓库内）。"""
    rels: list[str] = []
    for rel in SYNC_TOP:
        p = os.path.join(ROOT, rel)
        if os.path.isfile(p):
            rels.append(rel)

    skill_root = os.path.join(ROOT, "skills", "eu4-modding")
    for sub in ("", "references", "scripts"):
        d = os.path.join(skill_root, sub) if sub else skill_root
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if not os.path.isfile(p):
                continue
            if not f.endswith(INCLUDE_EXTS_SKILL):
                continue
            rels.append(os.path.relpath(p, ROOT).replace(os.sep, "/"))

    # presets/：agent preset 源文件（必须同步，否则下拉框里没有这一项）
    preset_root = os.path.join(ROOT, SYNC_PRESET_DIR)
    if os.path.isdir(preset_root):
        for root, _dirs, files in os.walk(preset_root):
            for f in sorted(files):
                if f.endswith((".yml", ".yaml")):
                    p = os.path.join(root, f)
                    rels.append(os.path.relpath(p, ROOT).replace(os.sep, "/"))

    return rels


def sync_one(target: str, rels: list[str], dry: bool) -> dict:
    changed, same, missing_src = [], [], []
    for rel in rels:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(target, rel)
        if not os.path.isfile(src):
            missing_src.append(rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False):
            same.append(rel)
            continue
        changed.append(rel)
        if not dry:
            shutil.copy2(src, dst)
    return {"changed": changed, "same": same, "missing_src": missing_src}


def verify(target: str, rels: list[str]) -> list[str]:
    """返回不一致的相对路径。"""
    bad = []
    for rel in rels:
        src = os.path.join(ROOT, rel)
        dst = os.path.join(target, rel)
        if not os.path.isfile(src):
            continue
        if not os.path.isfile(dst) or _sha(src) != _sha(dst):
            bad.append(rel)
    return bad


def find_stale(target: str, rels: list[str]) -> list[str]:
    """目标里存在、但源码里已没有的文件（删除项未同步造成）。

    排除运行时产物：`__pycache__/` 与 `*.pyc` 是插件跑脚本时 Python 自己生成的，
    不算「源码删除后残留」——把它们报成多余会造成每次校验都有一堆假阳性。
    """
    keep = set(rels)
    stale = []
    for root, dirs, files in os.walk(target):
        # 直接剪掉 __pycache__ 目录，不再往下走
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".pyc"):
                continue
            rel = os.path.relpath(os.path.join(root, f), target).replace(os.sep, "/")
            if rel in keep:
                continue
            if any(rel.startswith(p) for p in ("skills/eu4-modding/", "lib/", "presets/")) \
                    or rel in SYNC_TOP:
                stale.append(rel)
    return sorted(stale)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同步源码 → 已安装插件副本")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    rels = collect()
    print(f"源码待同步文件: {len(rels)}")

    targets = [t for t in (PLUGIN_DIR, NM_DIR) if os.path.isdir(t)]
    if not targets:
        print("★ 未找到已安装的插件副本；先按 INSTALL.md 方式 A 安装")
        print(f"  找过：{PLUGIN_DIR}")
        print(f"        {NM_DIR}")
        return 2

    rc = 0
    for t in targets:
        label = "plugins" if t == PLUGIN_DIR else "node_modules"
        if args.verify_only:
            bad = verify(t, rels)
            stale = find_stale(t, rels)
            print(f"\n[{label}] 校验")
            print(f"  不一致: {len(bad)}")
            for b in bad[:8]:
                print(f"    {b}")
            print(f"  目标多余(源码无): {len(stale)}")
            for s in stale[:5]:
                print(f"    {s}")
            if bad:
                rc = 1
        else:
            r = sync_one(t, rels, args.dry_run)
            print(f"\n[{label}] {'(dry-run) ' if args.dry_run else ''}同步")
            print(f"  已{'将' if args.dry_run else ''}更新 : {len(r['changed'])}")
            for c in r["changed"][:8]:
                print(f"    {c}")
            print(f"  已相同 : {len(r['same'])}")
            if r["missing_src"]:
                print(f"  ★ 源码缺: {r['missing_src']}")

    if not args.dry_run and not args.verify_only:
        print("\n" + "=" * 60)
        print("同步完成。★ 需要完全退出 DSH 并重启，改动才会生效。")
        print("（调试用内部重启窗口在桌面整合版里无头，必须整进程重启。）")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
