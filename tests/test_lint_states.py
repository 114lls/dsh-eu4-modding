"""lint 的三种判定状态 —— error 0 不等于「全部查过」。

为什么需要这个测试
----------------
实测（2026-11-01，从插件安装后的真实使用场景）：
  在无 reference/ 的工作目录下跑 eu4_lint.py <mod>，退出码是 0，
  输出里只有一行「[提示] 未找到 reference 目录，跳过名字位检查」。
  此时把 treasury 写在效果位，**不会被发现**，而用户看到 error 0 会以为全查过了。

这是最危险的一类静默失败：工具在跑、有输出、判定 PASS，但有一整类检查没执行。

修法（三态判定）：
  FAIL       有 error                     → exit 1
  INCOMPLETE 无 error 但名字位没跑          → exit 2
  PASS       无 error 且全部检查都跑过       → exit 0

exit 2 而不是 0，是为了让脚本/CI 能区分「干净」和「没查全」。

跑法：
    python -m unittest tests.test_lint_states -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
LINT = os.path.join(ROOT, "skills", "eu4-modding", "scripts", "eu4_lint.py")
REFERENCE = os.path.join(ROOT, "reference")

# 一个必然会被名字位检查抓到的最小 mod：treasury 写在效果位
BAD_MOD = """namespace = zz_state

country_event = {
\tid = zz_state.1
\ttitle = "zz_state.1.t"
\tdesc = "zz_state.1.d"
\tis_triggered_only = yes
\timmediate = {
\t\ttreasury = -50
\t}
\toption = {
\t\tname = "zz_state.1.a"
\t}
}
"""

LOC = """l_english:
 zz_state.1.t:0 "t"
 zz_state.1.d:0 "d"
 zz_state.1.a:0 "a"
"""


def _make_mod(tmp: str) -> str:
    d = os.path.join(tmp, "BadMod")
    os.makedirs(os.path.join(d, "events"))
    os.makedirs(os.path.join(d, "localisation"))
    with open(os.path.join(d, "events", "zz_state.txt"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(BAD_MOD)
    with open(os.path.join(d, "localisation", "zz_state_l_english.yml"), "w",
              encoding="utf-8-sig", newline="\n") as fh:
        fh.write(LOC)
    return d


def _run(mod: str, cwd: str, extra: list[str] | None = None) -> tuple[int, str]:
    cmd = [sys.executable, LINT, mod] + (extra or [])
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class TestThreeStates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eu4state_")
        self.mod = _make_mod(self.tmp)
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_fail_when_reference_present(self):
        """带 reference → 抓到 treasury，exit 1。"""
        if not os.path.isdir(REFERENCE):
            raise unittest.SkipTest("[NEED-REF] reference/ 不存在")
        rc, out = _run(self.mod, self.tmp, ["--reference", REFERENCE])
        self.assertEqual(rc, 1, f"应 exit 1（抓到 error），实际 {rc}")
        self.assertIn("FAIL", out)
        self.assertIn("treasury", out)

    def test_incomplete_when_reference_missing(self):
        """★ 无 reference → error 0，但必须报 INCOMPLETE 且 exit 2，不能报 PASS。"""
        rc, out = _run(self.mod, self.tmp)          # cwd 是临时目录，无 reference/
        self.assertEqual(rc, 2, f"应 exit 2（INCOMPLETE），实际 {rc}")
        self.assertIn("INCOMPLETE", out)
        self.assertNotIn("判定：PASS", out,
                         "缺 reference 时不该报 PASS —— 那是把「没查」当成「通过」")
        self.assertIn("不会被发现", out)

    def test_pass_with_no_reference_flag(self):
        """显式 --no-reference → 用户表明有意跳过，此时才允许 PASS。"""
        # 用一个干净的 mod（不含 treasury）
        clean = os.path.join(self.tmp, "CleanMod")
        os.makedirs(os.path.join(clean, "events"))
        os.makedirs(os.path.join(clean, "localisation"))
        with open(os.path.join(clean, "events", "zz_ok.txt"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write('namespace = zz_ok\n\ncountry_event = {\n\tid = zz_ok.1\n'
                     '\ttitle = "zz_ok.1.t"\n\tdesc = "zz_ok.1.d"\n'
                     '\tis_triggered_only = yes\n\toption = {\n'
                     '\t\tname = "zz_ok.1.a"\n\t\tadd_prestige = 1\n\t}\n}\n')
        with open(os.path.join(clean, "localisation", "zz_ok_l_english.yml"), "w",
                  encoding="utf-8-sig", newline="\n") as fh:
            fh.write('l_english:\n zz_ok.1.t:0 "t"\n zz_ok.1.d:0 "d"\n'
                     ' zz_ok.1.a:0 "a"\n')
        rc, out = _run(clean, self.tmp, ["--no-reference"])
        self.assertEqual(rc, 0, f"--no-reference 下干净 mod 应 exit 0，实际 {rc}")
        self.assertIn("PASS", out)

    def test_json_reports_skipped_checks(self):
        """--json 必须告诉消费者「哪一类没跑」。"""
        rc, out = _run(self.mod, self.tmp, ["--json"])
        self.assertEqual(rc, 2)
        self.assertIn("skipped_checks", out)
        self.assertIn("names", out)


class TestExitCodeContract(unittest.TestCase):
    """三态退出码必须是 0/1/2，不能有第四个值（否则脚本无法可靠分支）。"""

    def test_codes_are_distinct(self):
        # 这是文档化契约，不是运行时断言：0=干净 1=有 error 2=没查全
        self.assertEqual({0, 1, 2}, {0, 1, 2})


if __name__ == "__main__":
    unittest.main(verbosity=2)
