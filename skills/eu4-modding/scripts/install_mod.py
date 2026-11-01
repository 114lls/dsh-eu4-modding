"""安装 / 卸载 EU4 模组 —— 只动玩家目录，永不碰游戏安装目录（铁律 1）。

关键约束（每条都对应一个真实坑）
-------------------------------
1. **永不改游戏安装目录**。内容写在 mod 目录，装到玩家目录。
2. 写 `dlc_load.json` 前**必须备份**，写入格式与现有文件保持一致
   （实测格式：`{"enabled_mods":["mod/xx.mod", ...],"disabled_dlcs":[]}`）。
3. 启用项形如 `mod/<stem>.mod` —— 注意是**相对路径**、正斜杠、带 `mod/` 前缀。
4. 卸载要**不留孤儿**：从 dlc_load 移除条目，并删掉玩家 mod 目录里的那份副本。
   但**不删**用户的源目录（那是他的资产）。
5. 中文路径必须处理：`.mod` 的 `path` 用正斜杠纯 ASCII 才稳，所以安装时是
   **复制**过去（复制到玩家目录后路径就是英文的了），而不是软链。
6. 幂等：重复安装不产生重复条目。

用法
----
    python install_mod.py <源模组目录> --yes --enable
    python install_mod.py <源模组目录> --yes           # 只复制，不改启用状态
    python install_mod.py <源模组目录> --uninstall --yes
    python install_mod.py --list                       # 列出已启用
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _paths

__all__ = ["install", "uninstall", "read_enabled", "write_enabled", "MOD_JSON_KEY"]


def read_enabled(user_dir: str) -> dict:
    """读 dlc_load.json，返回完整对象（保留未知字段，只改 enabled_mods）。"""
    p = os.path.join(user_dir, "dlc_load.json")
    if not os.path.isfile(p):
        return {"enabled_mods": [], "disabled_dlcs": []}
    with open(p, encoding="utf-8-sig", errors="replace") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            return {"enabled_mods": [], "disabled_dlcs": []}
    if not isinstance(data, dict):
        return {"enabled_mods": [], "disabled_dlcs": []}
    data.setdefault("enabled_mods", [])
    data.setdefault("disabled_dlcs", [])
    return data


def _backup_dlc_load(user_dir: str) -> str | None:
    """★ 写 dlc_load.json 前必须备份。返回备份路径（无原文件则 None）。"""
    src = os.path.join(user_dir, "dlc_load.json")
    if not os.path.isfile(src):
        return None
    dst = src + "." + time.strftime("%Y%m%d-%H%M%S") + ".bak"
    shutil.copy2(src, dst)
    return dst


def write_enabled(user_dir: str, data: dict, backup: bool = True) -> str | None:
    """写回 dlc_load.json。格式与现有一致（紧凑 JSON，无多余空格差异）。"""
    bak = _backup_dlc_load(user_dir) if backup else None
    p = os.path.join(user_dir, "dlc_load.json")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    return bak


def _is_ascii(s: str) -> bool:
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _safe_stem(src: str) -> tuple[str, str]:
    """源目录名 → 安装用的 ASCII stem。返回 (stem, 原始名)。

    ★ 铁律 16：`.mod` 的 `path` 要用正斜杠**纯 ASCII** —— 中文路径可能加载失败。
      所以安装时必须把**目录名本身**也转成 ASCII，否则 path 里必然带中文。
      做法：非 ASCII 字符逐字替换成 `_`（并去掉被替换后可能出现的连续下划线与首尾下划线），
      结果为空则退回 `eu4mod`。
      原始名保留在返回的第二个值里，供报告展示"从哪个中文目录装来的"。
    """
    raw = os.path.basename(os.path.abspath(src))
    if _is_ascii(raw):
        return raw, raw
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", raw)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "eu4mod"
    return cleaned, raw


def _mod_stem(src: str) -> str:
    """源目录名 → 安装后的 mod 文件名 stem（已保证 ASCII）。"""
    return _safe_stem(src)[0]


def _fix_path_field(text: str, new_path: str) -> str:
    """把 .mod 里的 path= 改成安装后的绝对路径（正斜杠纯 ASCII）。"""
    lines = text.splitlines()
    out = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("path") and "=" in stripped:
            out.append(f'path = "{new_path}"')
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.insert(0, f'path = "{new_path}"')
    return "\n".join(out) + "\n"


def install(src: str, user_dir: str, enable: bool = False, force: bool = False) -> dict:
    if not os.path.isdir(src):
        raise SystemExit(f"源目录不存在：{src}")
    mod_root = os.path.join(user_dir, "mod")
    os.makedirs(mod_root, exist_ok=True)
    stem, raw_name = _safe_stem(src)
    dest = os.path.join(mod_root, stem)

    if os.path.isdir(dest) and not force:
        raise SystemExit(f"目标已存在，拒绝覆盖（加 --force 强制）：{dest}")

    # 复制（不用软链：软链会让 .mod 的 path 带中文/非 ASCII）
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest)

    # 目标目录里的 .mod 文件名要稳定可预测，且 path 指向安装后的绝对路径
    mod_file = os.path.join(mod_root, f"{stem}.mod")
    abs_path = os.path.abspath(dest).replace(os.sep, "/")
    text = ""
    src_mod_path = os.path.join(dest, f"{stem}.mod")
    if os.path.isfile(src_mod_path):
        with open(src_mod_path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    else:
        # 源目录只有 descriptor.mod 的情况
        d = os.path.join(dest, "descriptor.mod")
        if os.path.isfile(d):
            with open(d, encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
    text = _fix_path_field(text, abs_path)
    with open(mod_file, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(text)
    # 目标目录里的 .mod 也同步一份（保持自洽）
    with open(os.path.join(dest, f"{stem}.mod"), "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(text)

    result = {
        "installed_to": dest,
        "mod_file": mod_file,
        "path_field": abs_path,
        "stem": stem,
        "source_name": raw_name,
        "renamed_to_ascii": raw_name != stem,
        "enabled": False,
        "dlc_load_backup": None,
    }

    if enable:
        data = read_enabled(user_dir)
        entry = f"mod/{stem}.mod"
        if entry not in data["enabled_mods"]:
            data["enabled_mods"].append(entry)
        result["dlc_load_backup"] = write_enabled(user_dir, data)
        result["enabled"] = True
        result["enabled_mods_count"] = len(data["enabled_mods"])

    return result


def uninstall(src_or_stem: str, user_dir: str, remove_copy: bool = True) -> dict:
    """卸载：从 dlc_load 移除 + 删玩家目录副本。**不动**用户源目录。

    ★ 必须覆盖两种 stem：ASCII stem（新装法）与**原始目录名**（早期装法/手工安装）。
      实测教训：旧版按原始中文名装过一次，改进 ASCII 转换后，卸载只清 ASCII 那份，
      中文名的 `.mod` 与目录成了孤儿留在 dlc_load 与 mod/ 里。
      所以这里两套名字都清，并把清掉的孤儿单独列出来。
    """
    stem, raw_name = _safe_stem(src_or_stem)
    candidates = sorted({stem, raw_name})
    mod_root = os.path.join(user_dir, "mod")

    data = read_enabled(user_dir)
    entries = [f"mod/{c}.mod" for c in candidates]
    hits = [e for e in entries if e in data["enabled_mods"]]
    was = bool(hits)
    if hits:
        data["enabled_mods"] = [m for m in data["enabled_mods"] if m not in entries]
    bak = write_enabled(user_dir, data) if was else None

    removed: list[str] = []
    if remove_copy:
        for c in candidates:
            for p in (os.path.join(mod_root, c), os.path.join(mod_root, f"{c}.mod")):
                if os.path.isdir(p):
                    shutil.rmtree(p)
                    removed.append(p)
                elif os.path.isfile(p):
                    os.remove(p)
                    removed.append(p)

    # 孤儿检查：dlc_load 里是否还残留这两种 stem
    data2 = read_enabled(user_dir)
    orphan = [m for m in data2["enabled_mods"] if m in entries]

    return {
        "stem": stem,
        "candidates": candidates,
        "was_enabled": was,
        "removed_entries": hits,
        "dlc_load_backup": bak,
        "removed": removed,
        "orphan_left": orphan,
        "enabled_mods_count": len(data2["enabled_mods"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EU4 模组安装 / 卸载")
    ap.add_argument("src", nargs="?", default=None, help="源模组目录")
    ap.add_argument("--yes", action="store_true", help="跳过确认（自动化）")
    ap.add_argument("--enable", action="store_true", help="安装后写进 dlc_load.json")
    ap.add_argument("--uninstall", action="store_true", help="卸载")
    ap.add_argument("--force", action="store_true", help="目标已存在时覆盖")
    ap.add_argument("--list", action="store_true", help="列出已启用的 mod")
    ap.add_argument("--user-dir", default=None, help="玩家目录（默认自动探测）")
    args = ap.parse_args(argv)

    _paths.force_utf8_stdout()

    user = args.user_dir or str(_paths.find_user_dir() or "")
    if not user or not os.path.isdir(user):
        print("找不到玩家目录；用 --user-dir 指定，或设 EU4_USER_DIR")
        return 2

    if args.list:
        data = read_enabled(user)
        mods = data["enabled_mods"]
        print(f"已启用 {len(mods)} 个（{os.path.join(user, 'dlc_load.json')}）")
        for m in mods:
            print(f"   {m}")
        return 0

    if not args.src:
        print("需要给出源模组目录（或用 --list）")
        return 2

    if not args.yes:
        print(f"将把 {args.src} 复制到 {os.path.join(user, 'mod')}")
        print("（自动化环境请加 --yes）")
        return 2

    if args.uninstall:
        r = uninstall(args.src, user)
        print(f"已卸载    = {r['stem']}  (候选名 {r['candidates']})")
        print(f"曾启用    = {r['was_enabled']}")
        print(f"移除条目  = {r['removed_entries'] or '无'}")
        print(f"dlc备份   = {r['dlc_load_backup']}")
        print(f"删除项    = {r['removed']}")
        print(f"孤儿残留  = {r['orphan_left'] or '无'}")
        print(f"剩余启用  = {r['enabled_mods_count']}")
        return 0 if not r["orphan_left"] else 1

    r = install(args.src, user, enable=args.enable, force=args.force)
    print(f"已安装到  = {r['installed_to']}")
    print(f"mod 文件  = {r['mod_file']}")
    print(f"path 字段 = {r['path_field']}   (正斜杠纯 ASCII)")
    if r["renamed_to_ascii"]:
        print(f"★ 目录名改动 = {r['source_name']!r} → {r['stem']!r} "
              f"（path 必须纯 ASCII，中文路径可能加载失败）")
    print(f"已启用    = {r['enabled']}")
    if r["dlc_load_backup"]:
        print(f"dlc备份   = {r['dlc_load_backup']}")
    if r.get("enabled_mods_count"):
        print(f"启用总数  = {r['enabled_mods_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
