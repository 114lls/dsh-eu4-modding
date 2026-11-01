"""L2/L4 验收：白名单分类正确性 + lint 分级断言。

L2 通过标准（校验者给定）
------------------------
  a) treasury 分类：effects.json 里【无】，triggers.json 里【有】
  b) cwt <-> 语料覆盖率 >= 95%（分母 = 语料侧出现过的效果/触发器名）
  c) 未覆盖的 5% 必须逐类归因，不能只给「其他」总数
  d) 反跑：植入「语料有、cwt 无」的名字 → 断言 warn/info，不是 error

本文件把这些标准写成可执行断言，供 CI 与本地复现。

跑法：
    # 需要先把 cwtools-eu4-config 检出到某目录，再指定
    python tests/test_reference.py --cwt <cwt 目录>
    # 不指定则从 EU4_CWT_DIR 环境变量取
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "eu4-modding", "scripts"))
sys.path.insert(0, HERE)

import _paths  # noqa: E402
import build_reference as B  # noqa: E402
import cwt_reader  # noqa: E402

CWT_DIR = os.environ.get("EU4_CWT_DIR", "")
GAME = str(_paths.find_game_dir() or "")

_CACHE: dict = {}


def _skip_slow():
    """破坏性自测要完整重建 3 次（实测约 105 秒）。

    默认跳过以保持 CI/日常回归快；要验证守卫有效时用：
        $env:EU4_RUN_SABOTAGE = "1"
        python -m unittest tests.test_reference -v
    """
    if not os.environ.get("EU4_RUN_SABOTAGE"):
        raise unittest.SkipTest("[SLOW] 慢测试（实测约 105s）；设 EU4_RUN_SABOTAGE=1 启用")


def _build_once():
    """★ skip 原因带 [NEED-CWT] 前缀，便于统计「哪些断言因缺环境没执行」。

    实测差异（2026-11-01）：同一套测试，无 EU4_CWT_DIR 时 skipped=14，
    有它时 skipped=3。差出的 11 个全是白名单覆盖相关断言
    （treasury 分类、覆盖率、cross_check、corpus_only 分级…），
    它们**没有执行**，不是「通过」。

    所以报数时必须区分：`150 tests / 执行 X / skip Y（NEED-CWT a + SLOW b）`。
    「150 OK」这种写法会让人误以为 150 个都验过了。
    """
    if "data" not in _CACHE:
        if not CWT_DIR or not os.path.isdir(CWT_DIR):
            raise unittest.SkipTest("[NEED-CWT] 未提供 EU4_CWT_DIR 或 cwt 配置目录不存在")
        _CACHE["data"] = B.build(CWT_DIR, GAME or None, use_corpus=bool(GAME), verbose=False)
    return _CACHE["data"]


class TestWhitelistClassification(unittest.TestCase):
    """L2-a：treasury 的分类是白名单可信度的唯一硬指标。"""

    def test_treasury_is_trigger_not_effect(self):
        data = _build_once()
        self.assertNotIn("treasury", data["effects"], "treasury 必须【不】在 effects 里（它是触发器）")
        self.assertIn("treasury", data["triggers"], "treasury 必须在 triggers 里")

    def test_add_years_of_income_is_effect(self):
        """推荐写法的正样本：它是效果，不该出现在触发器表。"""
        data = _build_once()
        self.assertIn("add_years_of_income", data["effects"])
        self.assertNotIn("add_years_of_income", data["triggers"])

    def test_add_treasury_is_effect(self):
        data = _build_once()
        self.assertIn("add_treasury", data["effects"])


class TestCoverage(unittest.TestCase):
    """L2-b/c：覆盖率与缺口归因。"""

    def test_coverage_at_least_95(self):
        data = _build_once()
        s = data["stats"]
        self.assertGreaterEqual(
            s["overall"]["coverage_pct"], 95.0,
            f"覆盖率 {s['overall']['coverage_pct']}% < 95%",
        )

    def test_effect_coverage(self):
        data = _build_once()
        self.assertGreaterEqual(data["stats"]["effect"]["coverage_pct"], 95.0)

    def test_trigger_coverage(self):
        data = _build_once()
        self.assertGreaterEqual(data["stats"]["trigger"]["coverage_pct"], 95.0)

    def test_gap_is_attributed(self):
        """缺口必须逐类归因，不能只给一个「其他」总数。"""
        data = _build_once()
        ga = data["stats"]["gap_attribution"]
        self.assertIn("in_exe_but_not_cwt", ga)
        self.assertIn("unknown_anywhere", ga)

        gap_total = data["stats"]["effect"]["gap"] + data["stats"]["trigger"]["gap"]
        attributed = sum(v["count"] for v in ga.values())
        self.assertEqual(attributed, gap_total,
                         "归因总数必须等于缺口总数（不允许有未归类的残留）")

    def test_gap_small(self):
        data = _build_once()
        gap = data["stats"]["effect"]["gap"] + data["stats"]["trigger"]["gap"]
        self.assertLess(gap, 30, f"缺口 {gap} 个过大，需逐类归因说明")


class TestLintSeverity(unittest.TestCase):
    """L2-d：语料有、cwt 无的名字必须是 warn，不能是 error。

    这是**比覆盖率本身更重要**的断言：cwt 滞后于游戏版本是常态，
    若 lint 对合法名字报 error，就是对合法名字报假错 —— 误报对信任的杀伤。
    """

    def test_corpus_only_name_is_warn_not_error(self):
        data = _build_once()
        # 找一个确证的 corpus_only 项
        candidates = [n for n, row in data["triggers"].items() if row["status"] == "corpus_only"]
        candidates += [n for n, row in data["effects"].items() if row["status"] == "corpus_only"]
        if not candidates:
            self.skipTest("本机 cwt 与语料已完全对齐，无 corpus_only 样本；改用合成样本验证")

        name = candidates[0]
        severity = B.lint_severity("triggers" if name in data["triggers"] else "effects", "corpus_only")
        self.assertEqual(severity, "warn", f"{name} 的状态应是 warn，实际 {severity}")

    def test_cwt_only_is_info(self):
        self.assertEqual(B.lint_severity("effects", "cwt_only"), "info")

    def test_confirmed_is_none(self):
        self.assertIsNone(B.lint_severity("effects", "confirmed"))


class TestSabotage(unittest.TestCase):
    """★ P2-1 破坏性自测：注入破坏后构建必须变坏。

    与三个守卫的 --selftest 同一条原则：**先证明检查会红，再用检查判定**。
    如果构建在任何输入下都给 99.64%，这个数字就没有信息量。

    三路注入：
      S1 空 cwt 目录          → 覆盖率 < 50%
      S2 删 effects.cwt 的 alias[effect:...] → effect 覆盖率下降
      S3 不存在的 cwt 路径     → 必须报 cwt_errors

    成本考虑：S1/S2 要完整重建两次，耗时较长，所以只做「关键判据」，
    不重复 S3（它几乎零成本，已在 test 内联验证）。
    """

    def test_sabotage_detects_empty_cwt(self):
        """空 cwt 目录必须让覆盖率显著下降。"""
        import shutil, tempfile
        _skip_slow()
        if not (CWT_DIR and os.path.isdir(CWT_DIR)):
            raise unittest.SkipTest("[NEED-CWT] 需要 EU4_CWT_DIR")
        tmp = tempfile.mkdtemp(prefix="sab_empty_")
        try:
            empty = os.path.join(tmp, "empty")
            os.makedirs(empty, exist_ok=True)
            r = B.build(empty, GAME or None, verbose=False)
            cov = r["stats"]["overall"]["coverage_pct"]
            self.assertLess(cov, 50.0,
                            f"空 cwt 下覆盖率仍为 {cov:.2f}% —— 构建没随输入变坏（空断言）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sabotage_detects_missing_effect_aliases(self):
        """删掉 effects.cwt 的 alias 后 effect 侧覆盖率必须下降。"""
        import shutil, tempfile
        _skip_slow()
        if not (CWT_DIR and os.path.isdir(CWT_DIR)):
            raise unittest.SkipTest("[NEED-CWT] 需要 EU4_CWT_DIR")
        base = _build_once()
        base_eff = base["stats"]["effect"]["coverage_pct"]

        tmp = tempfile.mkdtemp(prefix="sab_alias_")
        try:
            broken = os.path.join(tmp, "broken")
            shutil.copytree(CWT_DIR, broken)
            target = None
            for root, _s, files in os.walk(broken):
                for f in files:
                    if f == "effects.cwt":
                        target = os.path.join(root, f)
            if target is None:
                self.skipTest("cwt 目录里没有 effects.cwt")

            with open(target, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().split("\n")
            kept = [ln for ln in lines if not ln.strip().startswith("alias[effect:")]
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(kept))

            r = B.build(broken, GAME or None, verbose=False)
            cov = r["stats"]["effect"]["coverage_pct"]
            self.assertLess(cov, base_eff,
                            f"删 alias 后 effect 覆盖率未下降（{base_eff:.2f}% → {cov:.2f}%）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_sabotage_detects_bad_cwt_path(self):
        """不存在的 cwt 路径必须报错，不能假装成功。"""
        r = B.build(os.path.join(os.path.expanduser("~"), "no_such_cwt_xyz"),
                    GAME or None, verbose=False)
        self.assertTrue(r["meta"].get("cwt_errors"),
                        "不存在的 cwt 路径没有报错 —— 会静默给出低质量白名单")

    def test_sabotage_cli_entry(self):
        """--sabotage 必须存在且返回 0（三路都变坏）。"""
        _skip_slow()
        self.assertTrue(hasattr(B, "sabotage_selftest"))
        if not (CWT_DIR and os.path.isdir(CWT_DIR)):
            raise unittest.SkipTest("[NEED-CWT] 需要 EU4_CWT_DIR")
        rc = B.sabotage_selftest(CWT_DIR, GAME or None)
        self.assertEqual(rc, 0, "破坏性自测判定为『守卫失效』")


class TestStatsVisibility(unittest.TestCase):
    """★ P1-2：cross_check 必须能一眼看到，不能只埋在 JSON 里。

    复查者原话：「能一眼看到的可复算量，才有人会去算」。
    """

    def test_meta_has_cross_check(self):
        data = _build_once()
        for kind in ("effects", "triggers"):
            cc = data["meta"]["counts"].get(kind, {}).get("cross_check")
            self.assertIsNotNone(cc, f"{kind} 缺 cross_check")
            self.assertEqual(cc["total_minus_corpus"], cc["cwt_or_vanilla_only"],
                             f"{kind} 的 cross_check 两侧不等")

    def test_union_check_renamed(self):
        """union_check 已改名 source_split，不得残留旧名。"""
        data = _build_once()
        for kind, v in data["meta"]["counts"].items():
            self.assertNotIn("union_check", v, f"{kind} 还残留 union_check")
            self.assertIn("source_split", v, f"{kind} 缺 source_split")


if __name__ == "__main__":
    unittest.main(verbosity=2)
