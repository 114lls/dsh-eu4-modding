"""文档链接完整性守卫 —— 把「没有死链」从人工审查变成可测项。

为什么需要
-----------
SKILL.md §3 有一张「配方索引」表，整张表都指向 `references/cheatsheet.md` 的 §N。
缺任何一章，索引就是**死链**：用户按索引翻过去，什么都没有。
这属于「看起来完整、实际断裂」的发布缺陷 —— 与「dashboard 绿灯但功能不可用」同类。

它不会自己报错，所以必须用工具查。

检查项
------
  1. SKILL.md §3 表格里出现的每个 `§N`，cheatsheet.md 里必须有对应标题
  2. SKILL.md / 其他文档里引用的每个 `references/xxx.md`，文件必须存在
  3. cheatsheet.md 里的 `§N` 编号不得跳号重复（重复会让「指向第 N 章」有歧义）

用法
----
    python tools/check_doc_links.py
    python tools/check_doc_links.py --selftest   # 反向验证：注入死链必须报错
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# ★ 导入即把 stdout/stderr 切到 UTF-8。
#   必须写在模块级而非 __main__ 分支：测试会 import 本模块后直接调 selftest()，
#   那时不走 __main__，而 Windows runner 的默认编码是 cp1252，中文 print 会抛
#   UnicodeEncodeError: 'charmap' codec can't encode characters（CI 实测）。
import _utf8  # noqa: E402,F401

__all__ = ["extract_skill_index", "extract_cheatsheet_sections", "extract_file_refs"]

SKILL = "skills/eu4-modding/SKILL.md"
CHEATSHEET = "skills/eu4-modding/references/cheatsheet.md"
REF_DIR = "skills/eu4-modding/references"

# cheatsheet 里的章节标题：`## §3 修正` 或 `## 3. 修正`
_SECTION_RE = re.compile(r"^#{1,3}\s*(?:§\s*)?(\d{1,2})\s*[\.、:]?\s*(.*)$", re.MULTILINE)
# SKILL.md 索引表里对 cheatsheet 的引用：`§3` / `cheatsheet §3` / `§1~§15`
_INDEX_REF_RE = re.compile(r"§\s*(\d{1,2})")
# 文档里对 references 文件的引用
_FILE_REF_RE = re.compile(r"`?(references/[A-Za-z0-9_.\-]+\.md)`?")


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def extract_skill_index(skill_text: str) -> list[int]:
    """从 SKILL.md 的 §3 配方索引表里抽出所有 §N。

    只取「配方索引」那一节，避免把正文里「见 §1.0」这类也当成 cheatsheet 章节号。
    """
    m = re.search(r"##\s*3\..*?(?=^##\s|\Z)", skill_text, re.MULTILINE | re.DOTALL)
    section = m.group(0) if m else skill_text
    nums = []
    for hit in _INDEX_REF_RE.finditer(section):
        nums.append(int(hit.group(1)))
    return sorted(set(nums))


def extract_cheatsheet_sections(text: str) -> dict[int, str]:
    """返回 {章节号: 标题}。"""
    out: dict[int, str] = {}
    for m in _SECTION_RE.finditer(text):
        n = int(m.group(1))
        title = m.group(2).strip()
        # 只认有标题的（避免把 `## 1.xxx` 这类当章节）
        if title:
            out.setdefault(n, title)
    return out


def extract_file_refs(text: str) -> list[str]:
    return sorted({m.group(1) for m in _FILE_REF_RE.finditer(text)})


def check(repo: str, quiet: bool = False) -> tuple[bool, list[str]]:
    problems: list[str] = []
    skill_path = os.path.join(repo, SKILL)
    ref_dir = os.path.join(repo, REF_DIR)

    if not os.path.isfile(skill_path):
        return False, [f"找不到 {SKILL}"]

    skill_text = _read(skill_path)
    wanted = extract_skill_index(skill_text)

    if not quiet:
        print(f"SKILL.md §3 索引指向的章节：{wanted or '（未解析到，检查表格写法）'}")

    # 1) 文件引用是否存在
    #    文档里写的是 `references/xxx.md`（相对 references 目录），
    #    实际文件在 `skills/eu4-modding/references/xxx.md`，所以要补前缀。
    for rel in extract_file_refs(skill_text):
        p = os.path.join(repo, rel)
        if not os.path.isfile(p):
            alt = os.path.join(repo, REF_DIR, os.path.basename(rel))
            if os.path.isfile(alt):
                continue
            problems.append(f"引用了不存在的文档：{rel}")

    # 2) cheatsheet 章节覆盖
    cs_path = os.path.join(repo, CHEATSHEET)
    if wanted:
        if not os.path.isfile(cs_path):
            problems.append(f"索引指向 cheatsheet，但文件不存在：{CHEATSHEET}")
        else:
            cs_text = _read(cs_path)
            have = extract_cheatsheet_sections(cs_text)
            if not quiet:
                print(f"cheatsheet 实际章节：{sorted(have) or '（无）'}")
            missing = [n for n in wanted if n not in have]
            if missing:
                problems.append(
                    f"索引死链：cheatsheet.md 缺这些章节 §{missing}（索引指向但无内容）")
            # 3) 编号重复
            dup = _dup_section_numbers(cs_text)
            if dup:
                problems.append(f"cheatsheet 章节号重复：§{sorted(dup)}（会让「指向第 N 章」有歧义）")

    # 4) references 目录下每个 .md 都被 SKILL.md 或 INSTALL.md 提到（防"写了但没人知道"）
    if os.path.isdir(ref_dir):
        mentioned = set()
        for doc in (SKILL, "skills/eu4-modding/INSTALL.md"):
            p = os.path.join(repo, doc)
            if os.path.isfile(p):
                mentioned |= set(extract_file_refs(_read(p)))
        if not quiet:
            print(f"SKILL/INSTALL 引用到的文档：{sorted(mentioned) or '（无）'}")
        for f in sorted(os.listdir(ref_dir)):
            if not f.endswith(".md"):
                continue
            # ★ 统一成 `references/xxx.md` 形式比较：
            #   文档里写的是相对 references 目录的路径，而目录扫描拿到的是文件名。
            #   早期实现拿 `skills/eu4-modding/references/xxx.md` 去比，
            #   结果把所有已引用的文档都误报成「未引用」。
            rel = f"references/{f}"
            if rel not in mentioned:
                problems.append(f"文档存在但 SKILL/INSTALL 未引用：{rel}")

    return (not problems), problems


def _dup_section_numbers(text: str) -> set[int]:
    seen: dict[int, int] = {}
    for m in _SECTION_RE.finditer(text):
        n = int(m.group(1))
        seen[n] = seen.get(n, 0) + 1
    return {n for n, c in seen.items() if c > 1}


def selftest(repo: str) -> bool:
    """★ 反向验证：构造一份缺章的 cheatsheet，检查必须报死链。"""
    ok, problems = check(repo, quiet=True)
    print(f"自测 1：真实仓库 → clean={ok}, 问题={len(problems)}")
    for p in problems[:5]:
        print(f"    {p}")

    skill_text = _read(os.path.join(repo, SKILL))
    wanted = extract_skill_index(skill_text)
    if not wanted:
        print("★ 解析不到索引章节号 —— 检查函数本身有问题")
        return False

    # 构造：只留第一节的假 cheatsheet
    fake = "## §1 骨架\n内容\n"
    have = extract_cheatsheet_sections(fake)
    missing = [n for n in wanted if n not in have]
    print(f"自测 2：只给 §1 的假 cheatsheet → 应报缺 {missing}")
    if not missing:
        print("★ 守卫失效：缺章却不报")
        return False
    print("守卫有效：能检出索引死链")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="文档链接完整性守卫")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    repo = args.repo or os.getcwd()

    if args.selftest:
        return 0 if selftest(repo) else 1

    ok, problems = check(repo, quiet=args.quiet)
    if ok:
        print("\nOK：无死链")
        return 0
    print(f"\n★ 发现 {len(problems)} 个文档问题：")
    for p in problems:
        print(f"   {p}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
