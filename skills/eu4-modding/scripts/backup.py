"""改动前备份 + 回滚 —— 完整性用 digest 做客观判据。

为什么它必须和 mod_digest.py 协同
-------------------------------
「备份成功」靠什么证明？只有「恢复后与原状一致」才算。
而「一致」不能靠肉眼比文件列表，要靠**同一个哈希函数**：
    备份时记录 digest → 恢复后重算 digest → 相等才算完整。

这正是 mod_digest.py 存在的第二重价值：它不只给 lint 的快照断言用，
也给备份的完整性做客观判据。两者用同一套 spec，所以「一致」是可复核的。

安全约束（每条都对应真实风险）
---------------------------
1. **恢复前先备份当前状态**：否则一次误恢复就永久丢失现场。
2. **恢复用 digest 校验**：不相等就明确报出差异，而不是静默覆盖。
3. **永不改游戏安装目录**（铁律 1），只操作 mod 目录。
4. 备份目录用时间戳 + 可选 tag，绝不覆盖已有备份。

用法
----
    python backup.py <模组目录> --tag before-edit      # 打备份
    python backup.py <模组目录> --list                 # 列出备份
    python backup.py <模组目录> --restore <备份名>      # 恢复（会先自动备份当前）
    python backup.py <模组目录> --verify <备份名>       # 只校验 digest，不动文件
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _paths
import mod_digest

__all__ = ["BACKUP_ROOT", "make_backup", "restore_backup", "list_backups", "verify_backup"]

# 备份放模组目录同级，避免被当成 mod 内容扫描
BACKUP_ROOT = "_eu4_mod_backups"


def _backup_root(mod_dir: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(mod_dir)), BACKUP_ROOT)


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def make_backup(mod_dir: str, tag: str = "", note: str = "") -> str:
    """打一份备份，返回备份目录路径。备份里含 manifest.json（记录 digest 与文件清单）。"""
    if not os.path.isdir(mod_dir):
        raise SystemExit(f"模组目录不存在：{mod_dir}")

    name = _stamp() + (f"-{tag}" if tag else "")
    dest = os.path.join(_backup_root(mod_dir), name)
    if os.path.exists(dest):
        raise SystemExit(f"备份已存在，拒绝覆盖：{dest}")
    os.makedirs(dest, exist_ok=False)

    detail = mod_digest.digest_detail(mod_dir)
    shutil.copytree(mod_dir, os.path.join(dest, "files"))

    manifest = {
        "mod": os.path.abspath(mod_dir),
        "mod_name": os.path.basename(os.path.abspath(mod_dir)),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tag": tag,
        "note": note,
        "digest": detail["hash"],
        "digest_spec": {
            "ext": list(mod_digest.INCLUDE_EXTS),
            "dirs": list(mod_digest.INCLUDE_DIRS),
            "excluded": sorted(mod_digest.EXCLUDE_NAMES),
            "sep": mod_digest.SEP.decode("utf-8", "replace"),
            "tool": "eu4-modding/mod_digest.py",
        },
        "file_count": detail["file_count"],
        "byte_len": detail["total_bytes"],
        "files": detail["files"],
    }
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    return dest


def list_backups(mod_dir: str) -> list[dict]:
    root = _backup_root(mod_dir)
    out: list[dict] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        mf = os.path.join(root, name, "manifest.json")
        if not os.path.isfile(mf):
            continue
        try:
            with open(mf, encoding="utf-8") as fh:
                m = json.load(fh)
        except (OSError, ValueError):
            continue
        m["_dir"] = name
        out.append(m)
    return out


def _find_backup(mod_dir: str, name: str) -> str:
    root = _backup_root(mod_dir)
    cand = os.path.join(root, name)
    if os.path.isdir(cand):
        return cand
    # 允许用后缀/tag 模糊匹配唯一项
    hits = [n for n in sorted(os.listdir(root)) if name in n] if os.path.isdir(root) else []
    if len(hits) == 1:
        return os.path.join(root, hits[0])
    if not hits:
        raise SystemExit(f"找不到备份：{name}")
    raise SystemExit(f"备份名有歧义，匹配到 {len(hits)} 个：{hits}")


def verify_backup(mod_dir: str, name: str) -> dict:
    """校验备份完整性：重算备份内 files/ 的 digest，与 manifest 比对。不动原模组。"""
    dest = _find_backup(mod_dir, name)
    mf = os.path.join(dest, "manifest.json")
    if not os.path.isfile(mf):
        raise SystemExit(f"备份缺 manifest：{dest}")
    with open(mf, encoding="utf-8") as fh:
        m = json.load(fh)
    inner = os.path.join(dest, "files")
    if not os.path.isdir(inner):
        raise SystemExit(f"备份缺 files/：{dest}")
    actual = mod_digest.digest_detail(inner)["hash"]
    return {
        "backup": os.path.basename(dest),
        "expected": m.get("digest"),
        "actual": actual,
        "match": actual == m.get("digest"),
        "file_count": m.get("file_count"),
    }


def restore_backup(mod_dir: str, name: str, force: bool = False) -> dict:
    """恢复备份。★ 恢复前先把当前状态自动备份一次，避免误覆盖。"""
    dest = _find_backup(mod_dir, name)
    v = verify_backup(mod_dir, name)
    if not v["match"] and not force:
        raise SystemExit(
            f"备份自身完整性校验失败（expected {v['expected']} vs actual {v['actual']}），"
            f"拒绝恢复。若确认要强制恢复加 --force")

    pre = make_backup(mod_dir, tag="pre-restore", note=f"恢复 {os.path.basename(dest)} 之前的自动备份")
    inner = os.path.join(dest, "files")

    # 清空当前 mod 目录的**内容**（保留目录本身），再整体复制
    for entry in os.listdir(mod_dir):
        p = os.path.join(mod_dir, entry)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    for entry in os.listdir(inner):
        s = os.path.join(inner, entry)
        d = os.path.join(mod_dir, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)

    after = mod_digest.digest_detail(mod_dir)["hash"]
    return {
        "restored_from": os.path.basename(dest),
        "pre_restore_backup": os.path.basename(pre),
        "digest_before": v["expected"],
        "digest_after": after,
        "roundtrip_ok": after == v["expected"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EU4 模组备份 / 恢复 / 完整性校验")
    ap.add_argument("mod", help="模组目录")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="列出备份")
    g.add_argument("--restore", metavar="NAME", help="恢复指定备份（会先自动备份当前）")
    g.add_argument("--verify", metavar="NAME", help="只校验备份 digest")
    ap.add_argument("--tag", default="", help="备份标签（如 before-edit）")
    ap.add_argument("--note", default="", help="备注")
    ap.add_argument("--force", action="store_true", help="备份校验失败时仍强制恢复")
    args = ap.parse_args(argv)

    _paths.force_utf8_stdout()

    if args.list:
        rows = list_backups(args.mod)
        if not rows:
            print("没有备份")
            return 0
        print(f"{'备份名':34s} {'digest':18s} {'文件':>5s}  说明")
        for m in rows:
            print(f"{m['_dir']:34s} {m.get('digest','-'):18s} {m.get('file_count',0):5d}  "
                  f"{m.get('tag','')} {m.get('note','')}")
        return 0

    if args.verify:
        v = verify_backup(args.mod, args.verify)
        print(f"备份      = {v['backup']}")
        print(f"expected  = {v['expected']}")
        print(f"actual    = {v['actual']}")
        print(f"判定      = {'MATCH' if v['match'] else 'MISMATCH'}")
        return 0 if v["match"] else 1

    if args.restore:
        r = restore_backup(args.mod, args.restore, force=args.force)
        print(f"已恢复自  = {r['restored_from']}")
        print(f"恢复前备份= {r['pre_restore_backup']}")
        print(f"digest    = {r['digest_before']} → {r['digest_after']}")
        print(f"往返一致  = {'YES' if r['roundtrip_ok'] else 'NO'}")
        return 0 if r["roundtrip_ok"] else 1

    dest = make_backup(args.mod, tag=args.tag, note=args.note)
    d = mod_digest.digest_detail(args.mod)
    print(f"备份目录  = {dest}")
    print(f"digest    = {d['hash']}")
    print(f"文件数    = {d['file_count']}")
    print(f"字节数    = {d['total_bytes']}")
    print(f"校验      = python backup.py \"{args.mod}\" --verify {os.path.basename(dest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
