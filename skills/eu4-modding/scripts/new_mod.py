"""新建 EU4 模组骨架 —— 生成的成果必须**一次就过 lint**。

设计约束（每条都是为了让「生成即合规」成立）
-----------------------------------------
1. 脚本 `.txt` 写 **UTF-8 无 BOM**；本地化 `.yml` 写 **UTF-8 with BOM**（vanilla 578 个全带 BOM）
2. namespace 只用字母（`--prefix` 会强制清洗掉数字，因为 namespace 含数字会让事件被丢弃）
3. 事件 id 前缀必须与 namespace 一致；事件带 `is_triggered_only = yes`
4. 骨架里的每个 loc 键都要在 .yml 里定义（title/desc/option name 全覆盖）
5. `.mod` 的 `path` 用正斜杠纯 ASCII；`picture=` 不是 `thumbnail=`；`supported_version=v1.37.5.0`
6. 生成的修正名、事件 id 全部带 mod 前缀，避免与 vanilla / 其他 mod 撞 ID
7. 生成后用 mod_digest 记录基线 digest，便于后续「骨架是否被改过」的判断

用法
----
    python new_mod.py <目标目录> --name "My Mod" --prefix MY
    python new_mod.py <目标目录> --name "My Mod" --prefix MY --no-event   # 只要骨架
    python new_mod.py <目标目录> --name "My Mod" --prefix MY --run-lint   # 生成后立刻校验
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _paths
import mod_digest

__all__ = ["create_mod", "sanitize_prefix", "SUPPORTED_VERSION"]

SUPPORTED_VERSION = "v1.37.5.0"


def sanitize_prefix(raw: str) -> str:
    """把用户给的 prefix 洗成「namespace 安全」的形式。

    ★ namespace 不能含数字 —— `zz_epi_chain1` 会报 Incorrect namespace event id，
    事件被丢弃且 buff 静默失效。vanilla 364 个 namespace 无一含数字。
    所以这里把数字与非字母字符全部去掉。

    行为（有测试钉死）：
      'zz_epi_chain1' → 'zz_epi_chain'   去掉尾随数字
      '中文前缀'       → 'mod'            全非 ASCII 时退回 'mod'
      ''             → 'mod'
      '123'          → 'mod'             **纯数字清洗后为空 → 退回 'mod'**
                        （不是 'm123'：数字在 namespace 里本来就不该出现，
                          保留 '123' 反而制造一个含数字的 namespace）
    """
    cleaned = re.sub(r"[^A-Za-z_]", "", raw or "")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "mod"
    return cleaned.lower()


def _write_txt(path: str, text: str) -> None:
    """脚本 .txt：UTF-8 **无 BOM**（vanilla 全无 BOM，词法器不处理 BOM）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _write_yml(path: str, text: str) -> None:
    """本地化 .yml：UTF-8 **with BOM**（vanilla 578 个 .yml 全部带 BOM）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(text)


def create_mod(dest: str, name: str, prefix: str, with_event: bool = True) -> dict:
    ns = sanitize_prefix(prefix)
    mod_name = name or "My Mod"
    created: list[str] = []

    if os.path.isdir(dest) and any(
            f for f in os.listdir(dest) if not f.startswith(".")):
        raise SystemExit(f"目标目录非空，拒绝覆盖：{dest}")
    os.makedirs(dest, exist_ok=True)

    # ---- descriptor.mod（mod 目录内） ----
    desc = (
        f'name = "{mod_name}"\n'
        f'path = "{os.path.abspath(dest).replace(chr(92), "/")}"\n'
        f'supported_version = "{SUPPORTED_VERSION}"\n'
        f'tags = {{ "Alternative History" }}\n'
        f'picture = "thumbnail.png"\n'
        f'# 骨架由 dsh-eu4-modding/new_mod.py 生成于 {time.strftime("%Y-%m-%d")}\n'
    )
    p = os.path.join(dest, "descriptor.mod")
    _write_txt(p, desc)
    created.append(p)

    # ---- 同级 .mod（玩家 mod 目录里那份，install_mod.py 会用） ----
    p = os.path.join(dest, f"{os.path.basename(dest)}.mod")
    _write_txt(p, desc)
    created.append(p)

    if with_event:
        # ---- 事件脚本（namespace 纯字母；id 前缀一致；is_triggered_only） ----
        ev = (
            f"namespace = {ns}\n"
            f"\n"
            f"# 骨架示例事件：控制台 `event {ns}.1` 触发\n"
            f"country_event = {{\n"
            f"\tid = {ns}.1\n"
            f'\ttitle = "{ns}.1.t"\n'
            f'\tdesc = "{ns}.1.d"\n'
            f"\tis_triggered_only = yes\n"
            f"\n"
            f"\toption = {{\n"
            f'\t\tname = "{ns}.1.a"\n'
            f"\t\tadd_prestige = 1\n"
            f"\t}}\n"
            f"}}\n"
        )
        p = os.path.join(dest, "events", f"{ns}_events.txt")
        _write_txt(p, ev)
        created.append(p)

        # ---- 本地化（BOM；覆盖 title / desc / option name） ----
        yml = (
            f"l_english:\n"
            f' {ns}.1.t:0 "[{ns}] 示例事件"\n'
            f' {ns}.1.d:0 "这是由 new_mod.py 生成的骨架事件。请在 localisation 里改这里的文本。"\n'
            f' {ns}.1.a:0 "确认"\n'
        )
        p = os.path.join(dest, "localisation", f"{ns}_l_english.yml")
        _write_yml(p, yml)
        created.append(p)

    # ---- 记录基线 digest ----
    detail = mod_digest.digest_detail(dest)
    return {
        "dest": dest,
        "namespace": ns,
        "mod_name": mod_name,
        "files": created,
        "digest": detail["hash"],
        "file_count": detail["file_count"],
        "byte_len": detail["total_bytes"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="新建 EU4 模组骨架")
    ap.add_argument("dest", help="目标目录")
    ap.add_argument("--name", default="My Mod")
    ap.add_argument("--prefix", default="my",
                    help="ID/namespace 前缀（会自动去掉数字，因为 namespace 不能含数字）")
    ap.add_argument("--no-event", action="store_true", help="只生成骨架，不含示例事件")
    ap.add_argument("--run-lint", action="store_true", help="生成后立刻跑 eu4_lint")
    args = ap.parse_args(argv)

    _paths.force_utf8_stdout()

    raw_prefix = args.prefix
    info = create_mod(args.dest, args.name, args.prefix, with_event=not args.no_event)

    print(f"已生成   = {info['dest']}")
    print(f"namespace= {info['namespace']}   (原始输入 {raw_prefix!r} 已清洗：namespace 不能含数字)")
    print(f"digest   = {info['digest']}")
    print(f"文件     =")
    for f in info["files"]:
        print(f"   {os.path.relpath(f, info['dest'])}")

    if args.run_lint:
        import eu4_lint
        ref = os.path.join(os.getcwd(), "reference")
        res = eu4_lint.lint_mod(info["dest"],
                               game=str(_paths.find_game_dir() or "") or None,
                               ref_dir=ref if os.path.isdir(ref) else None)
        print()
        print(f"lint     = error {len(res.errors)} / warn {len(res.warnings)} / info {len(res.infos)}")
        for i in res.issues:
            print(f"   {i}")
        if res.errors:
            print("★ 骨架未通过 lint —— 这是工具 bug，不是用户的错")
            return 1
        print("骨架一次通过 lint（error 0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
