"""守卫类检查的测试 —— 每条都必须能被反向验证。

为什么单列一个文件
----------------
「约束类」实现有个通病：**写错了不会报错，只在最需要它的时候不生效**。
含游戏素材的包一旦发出去、文档死链一旦发布，事后发现就来不及了。

所以这里每条守卫都测两件事：
  1. 在真实仓库上通过
  2. **注入违规后必须失败**（--selftest 逻辑的单元版）

只测第 1 条的守卫，可能是个空断言。

跑法：
    python -m unittest tests.test_guards -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import check_doc_links  # noqa: E402
import check_no_assets  # noqa: E402
import check_no_reference  # noqa: E402


class TestAssetGuard(unittest.TestCase):
    """不含游戏素材 守卫。"""

    def test_repo_is_clean(self):
        ok, bad = check_no_assets.check(ROOT, quiet=True)
        self.assertTrue(ok, f"仓库含疑似素材：{bad}")

    def test_guard_actually_fires(self):
        """★ 反向验证：塞一个假 .dds，必须报违规。"""
        self.assertTrue(check_no_assets.selftest(),
                        "守卫失效：塞了 .dds 却报干净")

    def test_allowed_exception_is_respected(self):
        """自测样本 NOT_A_REAL_ASSET.dds 必须被例外放行，否则仓库永远不干净。"""
        files = check_no_assets.git_files(ROOT)
        self.assertIn("tests/fixtures/asset_guard/NOT_A_REAL_ASSET.dds", files,
                      "自测样本不在 git 跟踪列表里（CI 上会失效）")
        ok, bad = check_no_assets.check(ROOT, quiet=True)
        self.assertNotIn("tests/fixtures/asset_guard/NOT_A_REAL_ASSET.dds", bad)

    def test_exception_list_is_not_wildcard(self):
        """例外必须是精确路径，不能退化成前缀/通配，否则守卫形同虚设。"""
        for exc in check_no_assets.ALLOWED_EXCEPTIONS:
            self.assertTrue(exc.endswith(".dds"))
            self.assertNotIn("*", exc, f"例外不该用通配：{exc}")


class TestReferenceGuard(unittest.TestCase):
    """reference/ 不得进包 守卫。"""

    def test_is_reference_path_is_segment_based(self):
        """★ 必须按路径段判断：`references/` 含子串 'reference' 但应放行。"""
        self.assertTrue(check_no_reference.is_reference_path("reference"))
        self.assertTrue(check_no_reference.is_reference_path("reference/effects.json"))
        self.assertTrue(check_no_reference.is_reference_path("./reference/x.json"))
        self.assertFalse(
            check_no_reference.is_reference_path("skills/eu4-modding/references/*.md"),
            "把 references/ 误判成 reference/")
        self.assertFalse(check_no_reference.is_reference_path("README.md"))

    def test_whitelist_is_clean(self):
        with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as fh:
            pkg = json.load(fh)
        ok, bad = check_no_reference.check_files_whitelist(pkg)
        self.assertTrue(ok, f"白名单含 reference/: {bad}")

    def test_guard_actually_fires(self):
        """★ 反向验证：注入违规项必须被检出。"""
        self.assertTrue(check_no_reference.selftest(ROOT),
                        "守卫失效：注入 reference/ 却报干净")

    def test_uses_whitelist_not_blacklist(self):
        """files 必须是白名单（显式列出该进的），不是靠排除。"""
        with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as fh:
            pkg = json.load(fh)
        files = pkg["files"]
        self.assertGreater(len(files), 5, "files 太少，可能不是白名单")
        self.assertIn("lib/index.js", files)
        self.assertIn("skills/eu4-modding/SKILL.md", files)

    def test_dev_only_dirs_not_packaged(self):
        """★ 白名单的实证价值：tools/ 与 tests/ 天然不进包，无需逐条排除。"""
        with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as fh:
            files = json.load(fh)["files"]
        for dev_dir in ("tools/", "tests/", "reference/", ".github/"):
            self.assertFalse(
                any(f.startswith(dev_dir) for f in files),
                f"{dev_dir} 不该出现在 files 白名单里")


class TestDocLinkGuard(unittest.TestCase):
    """文档死链 守卫。"""

    def test_repo_docs_ok(self):
        ok, problems = check_doc_links.check(ROOT, quiet=True)
        self.assertTrue(ok, "文档问题：" + "; ".join(problems))

    def test_guard_actually_fires(self):
        """★ 反向验证：缺章的 cheatsheet 必须被判为死链。"""
        self.assertTrue(check_doc_links.selftest(ROOT),
                        "守卫失效：缺章却不报")

    def test_index_numbers_parsed(self):
        """SKILL.md §3 索引表里的 §N 必须能被解析出来。"""
        with open(os.path.join(ROOT, check_doc_links.SKILL), encoding="utf-8") as fh:
            text = fh.read()
        nums = check_doc_links.extract_skill_index(text)
        self.assertGreaterEqual(len(nums), 8, f"索引章节解析太少：{nums}")
        self.assertIn(1, nums)
        self.assertIn(10, nums)

    def test_cheatsheet_covers_every_index_number(self):
        """★ 核心：每个索引号在 cheatsheet 里都有标题（= 无死链）。"""
        with open(os.path.join(ROOT, check_doc_links.SKILL), encoding="utf-8") as fh:
            wanted = check_doc_links.extract_skill_index(fh.read())
        cs = os.path.join(ROOT, check_doc_links.CHEATSHEET)
        self.assertTrue(os.path.isfile(cs), "cheatsheet.md 不存在")
        with open(cs, encoding="utf-8") as fh:
            have = check_doc_links.extract_cheatsheet_sections(fh.read())
        missing = [n for n in wanted if n not in have]
        self.assertEqual(missing, [], f"索引死链：缺 §{missing}")

    def test_no_duplicate_sections(self):
        cs = os.path.join(ROOT, check_doc_links.CHEATSHEET)
        with open(cs, encoding="utf-8") as fh:
            dup = check_doc_links._dup_section_numbers(fh.read())
        self.assertEqual(dup, set(), f"章节号重复：{sorted(dup)}")

    def test_all_reference_docs_are_linked(self):
        """references/ 下每个 .md 都必须被 SKILL 或 INSTALL 引用（防"写了没人知道"）。"""
        ok, problems = check_doc_links.check(ROOT, quiet=True)
        unlinked = [p for p in problems if "未引用" in p]
        self.assertEqual(unlinked, [], f"未被引用的文档：{unlinked}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
