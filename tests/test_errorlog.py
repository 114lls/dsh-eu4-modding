"""L5 验收：errorlog_triage 的归属与归类正确性。

通过标准（校验者给定 + 微调）
--------------------------
  a) 必须同时扫 error.log / error_old.log / error_old_old.log
  b) 必须能正确归类两条历史报错（基准写进 fixtures，不依赖实时日志）
  c) 按 [模块:行]: 消息 归类 + 按来源文件分组 + 与 dlc_load.json 交叉 + --only-mine
  d) 噪声占比要有量化输出
  e) 不依赖「哪个 mod 产生了这个错」的猜测 —— 只做文件/ID 归属的确定性判断
  f) 基准写进 fixtures（不绑定活的日志，避免随轮转漂移）

跑法：
    python -m unittest tests.test_errorlog -v
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "skills", "eu4-modding", "scripts"))

import errorlog_triage as E  # noqa: E402

SAMPLE = os.path.join(HERE, "fixtures", "errorlog_sample.log")
MOD = os.path.expanduser(
    "~/Documents/Paradox Interactive/Europa Universalis IV/mod/posthumous_names")


class TestParsing(unittest.TestCase):
    def test_parses_all_lines(self):
        rows = E.parse_log(SAMPLE)
        self.assertGreater(len(rows), 15, "样本行数异常")
        for r in rows:
            self.assertTrue(r.module.endswith(".cpp"), f"模块解析失败：{r.module}")

    def test_classification_covers_known_kinds(self):
        """分类优先级：更具体的错误压在更笼统的「Parsing Error」之上。

        实测样本里 `Parsing Error. File: ..., Error: Unknown effect type` 同时含两个特征，
        按 ERROR_CLASSES 顺序归到 **unknown-effect**（更具体、更有指导意义）。
        所以这里不要求 parse-parsing 一定出现，而是要求「至少命中具体的几类」。
        """
        rows = E.parse_log(SAMPLE)
        classes = {r.cls for r in rows}
        for expect in ("namespace", "unknown-effect", "map", "version", "missing-loc"):
            self.assertIn(expect, classes, f"分类缺失：{expect}")

    def test_parsing_error_goes_to_specific_class(self):
        """Parsing Error 行应被更具体的子类型吸收，而不是笼统的 parse-parsing。"""
        rows = E.parse_log(SAMPLE)
        pe = [r for r in rows if "Parsing Error" in r.message]
        self.assertGreater(len(pe), 0)
        for r in pe:
            self.assertNotEqual(r.cls, "other", f"Parsing Error 未被识别：{r.message[:60]}")


class TestBaselineReproducible(unittest.TestCase):
    """★ b) 两条历史报错必须被正确归类。

    基准来自 fixtures，不依赖用户机器上的实时日志 —— 后者会随轮转漂移。
    """

    def test_namespace_error_classified(self):
        rows = E.parse_log(SAMPLE)
        hits = [r for r in rows if "Incorrect namespace event id" in r.message]
        self.assertGreaterEqual(len(hits), 1, "没找到 namespace 非法基准行")
        for r in hits:
            self.assertEqual(r.cls, "namespace")
            ids = E.extract_ids(r.message)
            self.assertEqual(len(ids), 1, f"应只提取 1 个 id：{ids}")
        # 样本里刻意放了两条（一条是历史已改名的 zz_epi_chain1）
        all_ids = [i for r in hits for i in E.extract_ids(r.message)]
        self.assertIn("zz_epi_chain1", all_ids)

    def test_treasury_effect_error_classified(self):
        rows = E.parse_log(SAMPLE)
        hits = [r for r in rows if "Unknown effect type. Key: treasury" in r.message]
        self.assertEqual(len(hits), 1, "没找到 treasury 基准行")
        r = hits[0]
        self.assertEqual(r.cls, "unknown-effect")
        self.assertIn("events/zz_epi_chain_1.txt", r.files)


class TestAttribution(unittest.TestCase):
    """★ c/e) 归属只做确定性判断。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(MOD):
            raise unittest.SkipTest("[NEED-MOD] posthumous_names 不存在")
        cls.file_owner, cls.ns_owner = E.build_owner_maps([MOD], [])

    def test_namespace_map_built(self):
        self.assertGreater(len(self.ns_owner), 0, "namespace 归属表为空")
        self.assertIn("zz_epi_grain", self.ns_owner)

    def test_file_attribution(self):
        row = E.LogLine("x.log", 1, "persistent.cpp", 88,
                        'Parsing Error. File: "events/zz_epi_chain_1.txt", Error: Unknown effect type. Key: treasury')
        row.files = E.extract_files(row.message)
        owner = E.resolve_owner(row, self.file_owner, self.ns_owner)
        self.assertEqual(owner, "posthumous_names")

    def test_historical_namespace_attributed_by_prefix(self):
        """★ 已改名的 namespace（zz_epi_chain1）仍应归到该 mod。

        这条是真实场景：报错来自历史版本，当前 mod 里已无此 namespace，
        但它仍属于这个 mod —— 前缀推导是确定性判断，不是猜测。
        """
        row = E.LogLine("x.log", 1, "eventmanager.cpp", 434,
                        "Incorrect namespace event id: zz_epi_chain1.")
        row.ids = E.extract_ids(row.message)
        owner = E.resolve_owner(row, self.file_owner, self.ns_owner)
        self.assertEqual(owner, "posthumous_names")

    def test_vanilla_error_not_attributed(self):
        """vanilla 的错不能被误归到 mod（防误报）。"""
        row = E.LogLine("x.log", 1, "map.cpp", 2483, "Province 12 has no pixels in provinces.bmp")
        self.assertEqual(E.resolve_owner(row, self.file_owner, self.ns_owner), "unknown")

    def test_unknown_stays_unknown(self):
        row = E.LogLine("x.log", 1, "dlc.cpp", 254,
                        "Invalid supported_version in  file: mod/ugc_3232659721.mod line: 6")
        self.assertEqual(E.resolve_owner(row, self.file_owner, self.ns_owner), "unknown")


class TestTriageOutput(unittest.TestCase):
    """★ a/d) 多日志扫描 + 噪声量化。"""

    def setUp(self):
        if not os.path.isdir(MOD):
            raise unittest.SkipTest("[NEED-MOD] posthumous_names 不存在")
        self.data = E.triage([SAMPLE], [MOD], [])

    def test_noise_ratio_present(self):
        self.assertIn("noise_ratio", self.data)
        self.assertTrue(0 <= self.data["noise_ratio"] <= 1)

    def test_noise_ratio_meaningful(self):
        """样本是人工构造的，大部分应是 vanilla 噪声 —— 验证不是 0 也不是 1。"""
        self.assertGreater(self.data["noise_ratio"], 0.5)
        self.assertLess(self.data["noise_ratio"], 1.0)

    def test_mine_contains_baselines(self):
        mine = self.data["mine"]
        msgs = " ".join(r.message for r in mine)
        self.assertIn("treasury", msgs)
        self.assertIn("zz_epi_chain1", msgs)

    def test_by_class_and_by_module(self):
        self.assertIn("namespace", self.data["by_class"])
        self.assertIn("eventmanager.cpp:434", self.data["by_module"])

    def test_discovers_all_error_logs(self):
        """a) 自动发现应扫全部 error*.log 而不是只有当前那个。"""
        import _paths
        logs = _paths.error_logs()
        if not logs:
            raise unittest.SkipTest("[NEED-LOG] 本机无 error*.log")
        names = {os.path.basename(p) for p in logs}
        # 若本机确实存在轮转文件，必须都被发现
        user = _paths.find_user_dir()
        import glob
        on_disk = {os.path.basename(p) for p in glob.glob(os.path.join(str(user), "logs", "error*.log"))}
        self.assertEqual(names, on_disk, "有 error*.log 没被发现")


if __name__ == "__main__":
    unittest.main(verbosity=2)
