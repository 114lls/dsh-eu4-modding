"""模组快照哈希 —— 让「这份结果对应哪一版模组」可被第三方复现。

为什么需要它
-----------
lint 的断言是**快照断言**（"这个模组 0 warn / 314 修正 / 722 loc 键"），
而模组是活的：共和国功能簇一加入，8 / 313 / 712 三个数同时过时。
若只硬编码数字，将来模组一变就只报「模组已变动」，分不清是
「模组内容变了」还是「我的哈希实现变了」—— 都是看似失败、实际无关的红灯。

所以：先把哈希本身做成**可复现、可自检**的，再拿它当地基。

精确 spec（字节流的构成，逐条写死，不接受"大致如此"）
---------------------------------------------------
  1. 文件集：只取**会影响 lint 结果**的脚本与本地化文件，扩展名白名单
        .txt  .yml  .yaml
     白名单目录（相对路径首段）：
        common/ events/ decisions/ missions/ localisation/
        customizable_localization/ history/ map/ interface/ gfx/
     显式排除（即使扩展名匹配也不收）：
        workshop_description.txt   工坊描述，不是游戏内容
        descriptor.mod             .mod 描述文件，不影响 lint
        README* / *.md / 缩略图 / 任何文档类
     理由：快照要「宁窄勿宽」。宽了会用假信号稀释真信号 ——
     改一句工坊描述就让快照报「模组已变动」，但 lint 结果根本不变。

  2. 排序：按**相对路径的 UTF-8 字节序**（Python 默认 str 排序即该序）。
     路径统一用 `/` 分隔（`os.relpath` 后把 `\\` 换成 `/`），
     这样 Windows 与 Linux 算出同一个值。**不做大小写归一**（见下方注意）。

  3. 每个文件贡献的字节：`相对路径 + b"\\n" + 文件原始字节（不做换行归一）`
     - 路径后必须跟一个 `\\n` 作为分隔符，否则 `ab` + `c` 与 `a` + `bc` 会撞哈希
     - 文件内容按**原始字节**读入，不转码、不 strip、不统一 CRLF
       （内容归一会让「只改了行尾」这种真实变动被吞掉）

  4. 摘要：sha256，取前 16 位十六进制（小写）。

自检入口
--------
    python mod_digest.py <模组目录>                # 只打印哈希
    python mod_digest.py <模组目录> --print-digest # 打印清单 + 字节数 + 哈希
    python mod_digest.py <模组目录> --verify <hash>  # 比对，退出码 0/1

    --print-digest 输出（a）参与计算的文件清单（排序后）（b）字节流总长度（c）最终哈希，
    这样任何人跑一次就能比对，也便于将来定位「为什么哈希变了」。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys

__all__ = ["mod_digest", "digest_detail", "INCLUDE_DIRS", "INCLUDE_EXTS", "EXCLUDE_NAMES"]

INCLUDE_EXTS = (".txt", ".yml", ".yaml")
INCLUDE_DIRS = (
    "common", "events", "decisions", "missions", "localisation",
    "customizable_localization", "history", "map", "interface", "gfx",
)
EXCLUDE_NAMES = {
    "workshop_description.txt",
    "descriptor.mod",
}
DIGEST_LEN = 16
SEP = b"\n"


def _norm_rel(path: str, base: str) -> str:
    return os.path.relpath(path, base).replace(os.sep, "/")


def collect_files(mod_dir: str) -> list[tuple[str, str]]:
    """按 spec 收文件集，返回 [(相对路径, 绝对路径)]，已按相对路径字节序排序。"""
    out: list[tuple[str, str]] = []
    for root, _sub, files in os.walk(mod_dir):
        for f in files:
            low = f.lower()
            if low in EXCLUDE_NAMES:
                continue
            if not low.endswith(INCLUDE_EXTS):
                continue
            full = os.path.join(root, f)
            rel = _norm_rel(full, mod_dir)
            top = rel.split("/", 1)[0] if "/" in rel else ""
            if top not in INCLUDE_DIRS:
                continue
            out.append((rel, full))
    out.sort(key=lambda t: t[0])
    return out


def digest_detail(mod_dir: str) -> dict:
    """返回 {hash, files, total_bytes, file_count}，可用于自检输出与调试。"""
    h = hashlib.sha256()
    total = 0
    files: list[str] = []
    for rel, full in collect_files(mod_dir):
        payload = rel.encode("utf-8") + SEP
        h.update(payload)
        total += len(payload)
        try:
            with open(full, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        h.update(raw)
        total += len(raw)
        files.append(rel)
    return {
        "hash": h.hexdigest()[:DIGEST_LEN],
        "files": files,
        "file_count": len(files),
        "total_bytes": total,
    }


def mod_digest(mod_dir: str) -> str:
    return digest_detail(mod_dir)["hash"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EU4 模组快照哈希（可复现）")
    ap.add_argument("mod", help="模组目录")
    ap.add_argument("--print-digest", action="store_true",
                    help="打印文件清单 / 字节流总长 / 哈希")
    ap.add_argument("--verify", default=None, metavar="HASH",
                    help="与给定哈希比对")
    ap.add_argument("--spec", action="store_true", help="打印算法 spec")
    args = ap.parse_args(argv)

    if args.spec:
        print(__doc__)
        return 0

    if not os.path.isdir(args.mod):
        print(f"模组目录不存在：{args.mod}")
        return 2

    d = digest_detail(args.mod)
    if args.print_digest:
        print(f"mod       = {args.mod}")
        print(f"hash      = {d['hash']}")
        print(f"files     = {d['file_count']}")
        print(f"byte_len  = {d['total_bytes']}")
        print(f"ext       = {', '.join(INCLUDE_EXTS)}")
        print(f"dirs      = {', '.join(INCLUDE_DIRS)}")
        print(f"excluded  = {', '.join(sorted(EXCLUDE_NAMES))}")
        print(f"sep       = {SEP!r}")
        print("清单（按相对路径 UTF-8 字节序）:")
        for i, f in enumerate(d["files"], 1):
            print(f"  {i:3d}. {f}")
    else:
        print(d["hash"])

    if args.verify:
        ok = d["hash"] == args.verify.strip()
        print(f"verify    = {'MATCH' if ok else 'MISMATCH'} ({args.verify.strip()})")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
