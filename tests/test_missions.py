"""任务树「定义 vs 引用」检查的测试。

实测依据（vanilla v1.37.5.0）
--------------------------
  · missions/*.txt 228 个文件，1422 个 series，7343 个任务块
  · `complete_mission = <ID>`  全库 51 处
  · `required_missions = { <ID> }` 全库 4724 处（不是罕见特性）
    → 所以「EU4 任务树没有显式前置」是错的，有，而且用得很多。

检查分级（沿用「误报伤信任」原则）
-------------------------------
  引用了本 mod 未定义的 ID  → info（可能来自 vanilla）
  required_missions 自环     → warn（必然不可完成）
  同 mod 内重复定义同一 ID   → warn（后者覆盖前者）

跑法：
    python -m unittest tests.test_missions -v
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "skills", "eu4-modding", "scripts"))

import eu4_lint  # noqa: E402
import _paths  # noqa: E402

REFERENCE = os.path.join(ROOT, "reference")
GAME = str(_paths.find_game_dir() or "") or None

FIX_GOOD = os.path.join(HERE, "fixtures", "missions")
FIX_BAD = os.path.join(HERE, "fixtures", "missions_bad")


def _has_ref() -> bool:
    return os.path.isfile(os.path.join(REFERENCE, "effects.json"))


class TestMissionDefinitions(unittest.TestCase):
    """定义收集是否正确。"""

    @classmethod
    def setUpClass(cls):
        cls.res = eu4_lint.lint_mod(FIX_GOOD, game=GAME,
                                    ref_dir=REFERENCE if _has_ref() else None)

    def test_counts(self):
        s = self.res.stats
        self.assertEqual(s["missions_defined"], 2, "应只数出 2 个任务（不含 slot/generic）")
        self.assertEqual(s["mission_series"], 1)

    def test_series_metadata_not_counted_as_mission(self):
        """★ slot / generic 是系列元数据，不是任务本体。"""
        s = self.res.stats
        self.assertEqual(s["missions_defined"], 2)
        # 若把 slot/generic 也算进去，会变成 4
        self.assertNotEqual(s["missions_defined"], 4)


class TestMissionReferences(unittest.TestCase):
    """定义 vs 引用。"""

    @classmethod
    def setUpClass(cls):
        cls.res = eu4_lint.lint_mod(FIX_GOOD, game=GAME,
                                    ref_dir=REFERENCE if _has_ref() else None)

    def test_missing_mission_is_info(self):
        """★ 引用不存在的任务 → info，不能是 error（可能来自 vanilla）。"""
        hits = [i for i in self.res.issues if i.code == "mission-ref"]
        self.assertTrue(hits, "没抓到引用了不存在的任务")
        for i in hits:
            self.assertEqual(i.severity, "info",
                             f"应为 info，实际 {i.severity}：{i.message}")
        self.assertTrue(any("zz_mission_missing" in i.message for i in hits))

    def test_defined_mission_not_reported(self):
        """引用已定义的任务不应报。"""
        bad = [i for i in self.res.issues
               if i.code == "mission-ref" and "zz_mission_ok" in i.message]
        self.assertEqual(bad, [])

    def test_no_warn_on_clean_fixture(self):
        """干净 fixture 不该有 warn（防误报）。"""
        self.assertEqual(len(self.res.warnings), 0,
                         "干净 fixture 出现 warn：" +
                         "; ".join(str(i) for i in self.res.warnings[:5]))

    def test_all_issues_have_line(self):
        """每条都要带行号。"""
        for i in self.res.issues:
            if i.category == "loc" and i.path == "<mod>":
                continue
            self.assertGreater(i.line, 0, f"{i} 缺行号")


class TestSelfLoopAndDuplicate(unittest.TestCase):
    """自环与重复定义。"""

    @classmethod
    def setUpClass(cls):
        cls.res = eu4_lint.lint_mod(FIX_BAD, game=GAME,
                                    ref_dir=REFERENCE if _has_ref() else None)

    def test_self_require_warns(self):
        hits = [i for i in self.res.issues if i.code == "mission-self-require"]
        self.assertTrue(hits, "没抓到 required_missions 自环")
        for i in hits:
            self.assertEqual(i.severity, "warn")
            self.assertIn("zz_mission_selfloop", i.message)

    def test_duplicate_mission_warns(self):
        hits = [i for i in self.res.issues if i.code == "mission-duplicate"]
        self.assertTrue(hits, "没抓到重复定义的任务")
        for i in hits:
            self.assertEqual(i.severity, "warn")
            self.assertIn("zz_mission_dup", i.message)

    def test_no_error_in_bad_fixture(self):
        """这些是 warn 级，不是 error —— 重复定义不一定坏，只是容易误解。"""
        self.assertEqual(len(self.res.errors), 0,
                         "不该有 error：" + "; ".join(str(e) for e in self.res.errors[:3]))


class TestPathDetectionIsRelative(unittest.TestCase):
    """★ 路径判断必须基于「mod 目录内的相对路径」。

    实测踩过：如果 mod 目录本身叫 missions（fixture `tests/fixtures/missions/`），
    对绝对路径做 `/missions/` 子串判断会把 events/ 下的文件也算成任务定义，
    导致 tasks 数从 2 变成 4。
    """

    def test_mod_dir_named_missions_does_not_confuse(self):
        res = eu4_lint.lint_mod(FIX_GOOD, game=GAME,
                                ref_dir=REFERENCE if _has_ref() else None)
        self.assertEqual(res.stats["missions_defined"], 2,
                         "mod 目录名叫 missions 时，events/ 被误当成任务定义")


class TestCategoryAttribution(unittest.TestCase):
    """★ 归类必须与实际使用分布相符。

    实测 vanilla `complete_mission =` 的分布（结构化 key，不含注释）：
        decisions 33 / missions 11 / events 7   合计 51
    所以任务 ID 引用归在 `crossrefs`，不是 `events`。
    归到 events 会让 `--only events` 漏掉三分之二的引用点。
    """

    def test_crossrefs_category_exists(self):
        self.assertIn("crossrefs", eu4_lint.CATEGORIES)
        self.assertIn("crossrefs", eu4_lint.DEFAULT_CATEGORIES)

    def test_missions_check_mapped_to_crossrefs(self):
        self.assertEqual(eu4_lint.CATEGORY_OF_CHECK["check_missions"], "crossrefs")
        self.assertEqual(eu4_lint.CATEGORY_OF_CHECK["check_events"], "events")

    def test_only_events_skips_missions(self):
        """★ --only events 不应包含任何 tasks 检查的结果。"""
        res = eu4_lint.lint_mod(FIX_GOOD, game=GAME,
                                ref_dir=REFERENCE if _has_ref() else None,
                                only={"events"})
        mission_issues = [i for i in res.issues
                          if i.code and i.code.startswith("mission-")]
        self.assertEqual(mission_issues, [],
                         f"--only events 混入了任务检查：{mission_issues}")

    def test_only_crossrefs_includes_missions(self):
        res = eu4_lint.lint_mod(FIX_GOOD, game=GAME,
                                ref_dir=REFERENCE if _has_ref() else None,
                                only={"crossrefs"})
        self.assertEqual(res.stats.get("missions_defined"), 2)
        hits = [i for i in res.issues if i.code == "mission-ref"]
        self.assertTrue(hits, "--only crossrefs 没跑到任务检查")

    def test_every_check_has_a_category(self):
        """每个检查函数都必须有类别映射，否则 --only 会漏掉它。"""
        checks = [n for n in dir(eu4_lint) if n.startswith("check_")]
        unmapped = [c for c in checks
                    if c not in eu4_lint.CATEGORY_OF_CHECK
                    and c != "check_syntax"        # syntax 无条件运行
                    and c != "check_everything"]
        self.assertEqual(unmapped, [], f"这些检查没有类别归属：{unmapped}")


class TestHonestWording(unittest.TestCase):
    """★ 2) 消息措辞必须如实说出能力边界。

    工具**没有索引 vanilla 的任务 ID**，所以它分不清
    「指向 vanilla 里存在的任务」与「指向谁都找不到的错 ID」。
    不能写「引用了不存在的任务」—— 那是断言了自己做不到的事。
    """

    def test_wording_admits_no_vanilla_index(self):
        res = eu4_lint.lint_mod(FIX_GOOD, game=GAME,
                                ref_dir=REFERENCE if _has_ref() else None)
        hits = [i for i in res.issues if i.code == "mission-ref"]
        self.assertTrue(hits)
        msg = hits[0].message
        self.assertIn("无法确认", msg, "措辞应承认无法确认，而非断言不存在")
        self.assertIn("未索引 vanilla", msg, "应说明能力边界")
        self.assertNotIn("不存在的任务", msg, "不能断言工具做不到的事")


class TestMultiNodeCycle(unittest.TestCase):
    """★ 3) 多节点环检测（A→B→A、A→B→C→A）。"""

    FIX = os.path.join(HERE, "fixtures", "missions_cycle")

    @classmethod
    def setUpClass(cls):
        cls.res = eu4_lint.lint_mod(cls.FIX, game=GAME,
                                    ref_dir=REFERENCE if _has_ref() else None,
                                    only={"crossrefs"})

    def test_two_node_cycle_detected(self):
        hits = [i for i in self.res.issues if i.code == "mission-cycle"]
        self.assertTrue(hits, "没抓到多节点环")
        msgs = " ".join(i.message for i in hits)
        self.assertIn("zz_cyc_a", msgs)
        self.assertIn("zz_cyc_b", msgs)

    def test_three_node_cycle_detected(self):
        msgs = " ".join(i.message for i in self.res.issues
                        if i.code == "mission-cycle")
        self.assertIn("zz_cyc_x", msgs)
        self.assertIn("zz_cyc_z", msgs)

    def test_cycle_count(self):
        """恰好 2 个环（2 节点 + 3 节点），不能把同一条环重复报。"""
        hits = [i for i in self.res.issues if i.code == "mission-cycle"]
        self.assertEqual(len(hits), 2, f"环数不对：{[i.message for i in hits]}")

    def test_cycle_is_warn(self):
        for i in [i for i in self.res.issues if i.code == "mission-cycle"]:
            self.assertEqual(i.severity, "warn")

    def test_acyclic_chain_not_reported(self):
        """正常链 ok1→ok2→ok3 不能被报成环。"""
        msgs = " ".join(i.message for i in self.res.issues
                        if i.code == "mission-cycle")
        self.assertNotIn("zz_ok_", msgs)

    def test_external_task_not_counted_in_cycle(self):
        """引用 vanilla 任务（conquer_scotland）不应被算进环。

        我们对 vanilla 任务**没有出边信息**，把它纳入图会得出不可靠的环 —— 假错。
        """
        msgs = " ".join(i.message for i in self.res.issues
                        if i.code == "mission-cycle")
        self.assertNotIn("conquer_scotland", msgs,
                         "外部任务被算进了环（假错）")

    def test_find_cycles_unit(self):
        """直接测 find_cycles：A→B→A 应返回一个环。"""
        edges = {"A": {"B"}, "B": {"A"}}
        cycles = eu4_lint.find_cycles(edges, scope={"A", "B"})
        self.assertEqual(len(cycles), 1)
        self.assertEqual(sorted(cycles[0]), ["A", "B"])

    def test_find_cycles_ignores_out_of_scope(self):
        """scope 外的节点不出现在环里。"""
        edges = {"A": {"B"}, "B": {"A"}, "X": {"Y"}, "Y": {"X"}}
        cycles = eu4_lint.find_cycles(edges, scope={"A", "B"})
        self.assertEqual(len(cycles), 1)
        self.assertEqual(sorted(cycles[0]), ["A", "B"])

    def test_find_cycles_no_cycle(self):
        edges = {"A": {"B"}, "B": {"C"}, "C": set()}
        self.assertEqual(eu4_lint.find_cycles(edges, scope={"A", "B", "C"}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
