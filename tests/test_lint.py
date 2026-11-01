"""L3 验收：eu4_lint 的分级正确性。

通过标准（校验者给定）
--------------------
  a) 七类检查逐类可跑
  b) fixtures 反跑：4 类故意错 + 语料独有名字 → 全部按预期分级
  c) 干净样本 0 error（防误报，与 b 同等重要）
  d) 每条报错带 文件:行号

跑法：
    python -m unittest tests.test_lint -v
    EU4_CWT_DIR 与 reference/ 存在时，名字位检查才会启用。
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "skills", "eu4-modding", "scripts"))
sys.path.insert(0, HERE)

import eu4_lint  # noqa: E402
import mod_digest  # noqa: E402
import _paths  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
REFERENCE = os.path.join(ROOT, "reference")
GAME = str(_paths.find_game_dir() or "") or None

# ★ 已知且已确认的 warn 规则白名单（必须逐条写理由）。
# 空 = 目前认为「该模组不该有任何 warn」。
# 若将来某条规则确实会对合法写法报警，把它加进来并在此写明为什么不是假错。
KNOWN_WARN_RULES: set[str] = set()


def _has_ref() -> bool:
    return os.path.isfile(os.path.join(REFERENCE, "effects.json"))


class TestFixturesCatchRealErrors(unittest.TestCase):
    """b) 反跑：必须抓得到故意植入的错，且行号准确。"""

    @classmethod
    def setUpClass(cls):
        cls.res = eu4_lint.lint_mod(FIXTURES, ref_dir=REFERENCE if _has_ref() else None)

    def _find(self, code):
        return [i for i in self.res.issues if i.code == code]

    def test_bad_namespace_is_error(self):
        """含数字的 namespace → error，行号 1。"""
        items = self._find("bad-namespace")
        self.assertTrue(items, "没抓到含数字的 namespace")
        self.assertEqual(items[0].severity, "error")
        self.assertEqual(items[0].line, 1)
        self.assertIn("zz_epi_chain1", items[0].message)

    def test_effect_inside_option_is_error(self):
        """选项内套 effect = {} → error，行号指向 effect 那一行。"""
        items = self._find("effect-in-option")
        self.assertTrue(items, "没抓到选项内套 effect")
        for i in items:
            self.assertEqual(i.severity, "error")
            self.assertGreater(i.line, 0)

    def test_unbalanced_paren_is_error(self):
        items = [i for i in self.res.issues if i.code == "unclosed-block"]
        self.assertTrue(items, "没抓到括号不平衡")
        self.assertEqual(items[0].severity, "error")
        self.assertEqual(items[0].line, 4, "应报在开块那一行，不是文件末行")

    @unittest.skipUnless(_has_ref(), "需要 reference/")
    def test_treasury_in_effect_position_is_error(self):
        """★ 核心：效果位写 treasury → error（treasury 那类坑）。"""
        items = self._find("wrong-position")
        self.assertTrue(items, "没抓到效果位用了触发器名")
        hits = [i for i in items if "treasury" in i.message]
        self.assertTrue(hits, "没抓到 treasury")
        self.assertEqual(hits[0].severity, "error")
        self.assertGreater(hits[0].line, 0)

    @unittest.skipUnless(_has_ref(), "需要 reference/")
    def test_corpus_only_name_is_warn_not_error(self):
        """★ 校验者要求的 d 项：语料有、cwt 无 → warn，绝不能 error。"""
        items = [i for i in self.res.issues if "corpus_only" in (i.code or "")]
        self.assertTrue(items, "没抓到 corpus_only 状态的条目")
        for i in items:
            self.assertEqual(i.severity, "warn",
                             f"{i.message} 被报成 {i.severity}，必须是 warn")

    def test_every_issue_has_line(self):
        """d) 每条报错带 文件:行号。"""
        for i in self.res.issues:
            if i.category == "loc" and i.path == "<mod>":
                continue        # 跨文件的 loc 缺失没有单一行号
            self.assertGreaterEqual(i.line, 0, f"{i} 缺行号")
            self.assertTrue(i.path, f"{i} 缺文件路径")


class TestNoFalsePositives(unittest.TestCase):
    """c) 干净样本必须 0 error —— 与「能抓到真错」同等重要。"""

    def test_clean_sample_has_no_error(self):
        res = eu4_lint.lint_mod(os.path.join(FIXTURES, "clean"),
                                ref_dir=REFERENCE if _has_ref() else None)
        self.assertEqual(len(res.errors), 0,
                         "干净样本出现 error（误报）：" + "; ".join(str(e) for e in res.errors))


class TestModDefinedNamesNotWarned(unittest.TestCase):
    """★ L4-b 回归保护：mod 自定义 scripted_effect/trigger 不得报 warn。

    这是「误报对信任的杀伤」落到测试里。一个 mod 若每个自定义脚本效果都被报
    warn，用户第一天就不信工具了。实测 posthumous_names 曾有 13 条这类假错。
    """

    def setUp(self):
        self.res = eu4_lint.lint_mod(os.path.join(FIXTURES, "mod_defined"),
                                     ref_dir=REFERENCE if _has_ref() else None)

    def test_custom_scripted_effect_not_warned(self):
        bad = [i for i in self.res.issues
               if i.category == "names" and "zz_my_custom_effect" in i.message
               and i.severity in ("warn", "error")]
        self.assertEqual(bad, [], f"自定义 scripted_effect 被报了：{bad}")

    def test_custom_scripted_trigger_not_warned(self):
        bad = [i for i in self.res.issues
               if i.category == "names" and "zz_my_custom_trigger" in i.message
               and i.severity in ("warn", "error")]
        self.assertEqual(bad, [], f"自定义 scripted_trigger 被报了：{bad}")

    def test_names_stats_reflect_mod_definitions(self):
        self.assertGreater(self.res.stats.get("mod_defined_effects", 0), 0,
                           "mod_defined_effects 计数为 0，说明收集逻辑没生效")


class TestOnActionDuplicates(unittest.TestCase):
    """★ L4-c：同 mod 内重复定义 on_action → warn。

    引擎语义已在 vanilla 查清：256 个顶层钩子零重复；
    `on_development_effect` 那个「重复」其实是块内调用的 scripted_effect（带 type 参数），
    不是钩子名。所以重复定义在本 mod 内出现时才 warn。
    """

    def test_duplicate_on_action_warns(self):
        res = eu4_lint.lint_mod(os.path.join(FIXTURES, "dup_on_action"),
                                ref_dir=REFERENCE if _has_ref() else None)
        items = [i for i in res.issues if i.code == "on-action-duplicate"]
        self.assertTrue(items, "没抓到重复定义的 on_action")
        self.assertEqual(items[0].severity, "warn")


class TestCategories(unittest.TestCase):
    """a) 七类可单独开关。"""

    def test_only_syntax(self):
        res = eu4_lint.lint_mod(FIXTURES, only={"syntax"})
        self.assertTrue(all(i.category == "syntax" for i in res.issues))

    def test_category_names_cover_all(self):
        """类别集合必须与 CATEGORIES 声明一致（新增类别时这条会红，是防退化）。"""
        expected = {"syntax", "events", "crossrefs", "loc",
                    "modifiers", "variables", "flags", "names"}
        self.assertEqual(set(eu4_lint.CATEGORIES), expected)
        # DEFAULT_CATEGORIES 必须覆盖全部类别，否则 --only 的默认集会漏检查
        self.assertEqual(set(eu4_lint.DEFAULT_CATEGORIES), expected)

    def test_collect_skips_readme(self):
        scripts, locs = eu4_lint.collect_mod_files(FIXTURES)
        names = {os.path.basename(p).lower() for p in scripts}
        self.assertNotIn("readme.md", names)


class TestPosthumousNames(unittest.TestCase):
    """★ L4-e：对真实成品模组跑一遍，与审计逐条对齐。

    已知差异（已查实，不是口径问题）：
      namespace 9（审计早先记 8，漏了 zz_epi_rep，来自 10/31 新增的 zz_epi_republic.txt）
      modifiers 314（审计早先记 313，zz_epi_event_modifiers 实测 62 而非 61）
      loc 键 716（含 debug 时为 722；debug 的 6 键随发布删除，722-6=716）

    ★ 快照断言必须绑定不可变量：
      这个参照模组【自己在项目期间变了】—— 共和国功能簇的加入让 8/313/712
      三个数同时过时。所以本类先用哈希比对：模组一变，测试明确报
      「快照失效，需重新确认」，而不是给一个看起来失败但其实无辜的红灯。
    """

    MOD = os.path.expanduser(
        "~/Documents/Paradox Interactive/Europa Universalis IV/mod/posthumous_names")

    # 断言基线：由 scripts/mod_digest.py 计算的快照哈希。
    # 该工具把算法 spec 写死并可自检（--print-digest / --verify），第三方可复现。
    # 校准于 2026-11-01，对应「共和国功能簇」已加入后的状态
    # （9 namespace / 314 修正 / 716 loc 键；debug 决议已按发布计划删除，722-6=716）。
    EXPECTED_DIGEST = "ed7ec40a45c2718e"

    @classmethod
    def _digest(cls) -> str:
        return mod_digest.mod_digest(cls.MOD)

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(cls.MOD):
            raise unittest.SkipTest("[NEED-MOD] posthumous_names 不存在")
        if not _has_ref():
            raise unittest.SkipTest("[NEED-REF] 需要 reference/（先跑 build_reference.py）")
        cls.digest = cls._digest()
        cls.res = eu4_lint.lint_mod(cls.MOD, game=GAME, ref_dir=REFERENCE)

    def test_snapshot_is_current(self):
        """快照失效检测：模组变了就明确提示重新确认，而不是红一个无辜的灯。"""
        if self.digest != self.EXPECTED_DIGEST:
            self.skipTest(
                f"模组已变动（当前 {self.digest}，基线 {self.EXPECTED_DIGEST}）。"
                f"请重新确认下列数字后更新 EXPECTED_DIGEST。")

    def test_no_error(self):
        self.assertEqual(len(self.res.errors), 0,
                         "成品模组出现 error：" + "; ".join(str(e) for e in self.res.errors))

    def test_no_warn(self):
        """★ 关键：成品模组不应有 warn。有 warn 说明工具在报假错。

        分级策略（校验者建议）：
          warn == 0  【或】 全部 warn 的规则 id 属于具名白名单 KNOWN_WARN_RULES。
        这样既不逼人默默压制，又要求做显式决定。
        """
        unexpected = [i for i in self.res.warnings if i.code not in KNOWN_WARN_RULES]
        self.assertEqual(
            unexpected, [],
            "出现未预期的 warn 级问题（要么修工具，要么加进 KNOWN_WARN_RULES 并写理由）："
            + "; ".join(str(i) for i in unexpected[:10]))

    def test_modifier_counts(self):
        s = self.res.stats
        self.assertEqual(s["modifier_defined"], 314)
        self.assertEqual(s["modifier_used"], 314)
        self.assertEqual(s["modifier_unused"], 0)

    def test_loc_coverage_complete(self):
        """★ L4-a：loc 覆盖接上 vanilla 键表后，缺失必须为 0。"""
        s = self.res.stats
        self.assertEqual(s["loc_keys_missing"], 0)
        self.assertEqual(s["loc_keys_mod"], 716)
        self.assertGreater(s["loc_keys_vanilla"], 100000, "vanilla 键表没加载")

    def test_no_uninitialized_vars(self):
        self.assertEqual(self.res.stats["vars_set"] - self.res.stats["vars_read"], 1,
                         "预期只有 1 个变量只设不读（info 级）")

    def test_has_info_but_no_warn(self):
        """仅有 info 是被允许的；warn 必须为 0。"""
        self.assertEqual(len(self.res.warnings), 0)
        self.assertGreater(len(self.res.infos), 0, "info 计数异常")


if __name__ == "__main__":
    unittest.main(verbosity=2)
