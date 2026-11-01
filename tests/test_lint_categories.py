"""lint 六类检查的注错验证 —— 补齐「只验过 3 类」的缺口。

背景
----
2026-11-01 之前只对三类做过真实植入反跑：
  names（treasury 写在效果位）
  crossrefs（任务环 / 文本路径）
  textures（picture 指向不存在文件）

六类从未验过：syntax / events / loc / modifiers / variables / flags。
「没验过」不等于「能用」—— 这是本项目反复强调的那条：跳过的不能当通过。

做法
----
每类构造一个**最小 mod**，只植入该类的错误，然后断言：
  1. 目标 code 以预期 severity 出现
  2. 行号落在预期位置（跨文件的检查除外，那些没有单一行号）
  3. 不产生意料之外的 error（防止检查互相误触发）

★ 一个写测试时的真实教训
------------------------
我第一版用 `encoding="utf-8-sig"` 写「缺 BOM」的样本 —— 结果 Python 自动加了 BOM，
样本根本没有缺陷，测试显示「0 issue」让我以为 loc_parser 有 bug。
查下来是**测试代码的错，不是工具的错**。
所以这里把编码控制显式化：`bom` 是一个独立参数，不再靠 encoding 名字暗示。

跑法：
    python -m unittest tests.test_lint_categories -v
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
LINT = os.path.join(ROOT, "skills", "eu4-modding", "scripts", "eu4_lint.py")
REFERENCE = os.path.join(ROOT, "reference")

BOM_BYTES = b"\xef\xbb\xbf"


def write_file(path: str, text: str, *, bom: bool) -> None:
    """按需控制是否有 BOM。

    ★ 不能用 encoding 名字来控制：`utf-8-sig` 会自动加 BOM，
      `utf-8` 不加。写「缺 BOM」样本时必须用 utf-8，且文本里不能含 \\ufeff。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = text.encode("utf-8")
    if bom:
        raw = BOM_BYTES + raw
    with open(path, "wb") as fh:
        fh.write(raw)


def run_lint(mod_dir: str) -> tuple[int, dict]:
    cmd = [sys.executable, LINT, mod_dir, "--json"]
    if os.path.isdir(REFERENCE):
        cmd += ["--reference", REFERENCE]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        data = json.loads((r.stdout or "").strip())
    except ValueError:
        data = {"issues": [], "summary": {}, "_parse_error": (r.stdout or r.stderr)[:300]}
    return r.returncode, data


class _Base(unittest.TestCase):
    """每个用例自己建临时 mod，跑完清理。"""

    def build(self, files: dict) -> tuple[int, dict]:
        tmp = tempfile.mkdtemp(prefix="eu4cat_")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        mod = os.path.join(tmp, "M")
        os.makedirs(mod)
        for rel, spec in files.items():
            if isinstance(spec, tuple):
                text, bom = spec
            else:
                text, bom = spec, False
            write_file(os.path.join(mod, rel), text, bom=bom)
        return run_lint(mod)

    def assert_issue(self, data: dict, code: str, severity: str,
                     line: int | None = None, category: str | None = None):
        hits = [i for i in data["issues"] if i["code"] == code]
        self.assertTrue(hits, f"没抓到 {code}；实际 issues=" +
                        str([(i["code"], i["severity"]) for i in data["issues"]]))
        hit = hits[0]
        self.assertEqual(hit["severity"], severity,
                         f"{code} 应是 {severity}，实际 {hit['severity']}：{hit['message'][:80]}")
        if category:
            self.assertEqual(hit["category"], category)
        if line is not None:
            self.assertEqual(hit["line"], line,
                             f"{code} 行号应是 {line}，实际 {hit['line']}")
        return hit


# --------------------------------------------------------------------------- #
# 1. syntax
# --------------------------------------------------------------------------- #
class TestSyntaxCategory(_Base):
    """语法与编码：括号不平衡、脚本 BOM。"""

    def test_unclosed_block_reports_open_line(self):
        rc, data = self.build({
            "events/bad.txt": 'namespace = zz_syn\n\ncountry_event = {\n\tid = zz_syn.1\n',
        })
        self.assert_issue(data, "unclosed-block", "error", line=3, category="syntax")
        self.assertEqual(rc, 1)

    def test_script_txt_with_bom_is_error(self):
        rc, data = self.build({
            "events/bom.txt": ('namespace = zz_bom\n', True),
        })
        self.assert_issue(data, "bom", "error", line=1, category="syntax")
        self.assertEqual(rc, 1)


# --------------------------------------------------------------------------- #
# 2. events
# --------------------------------------------------------------------------- #
class TestEventsCategory(_Base):
    """事件：namespace 含数字、选项内套 effect。"""

    def test_namespace_with_digit_is_error(self):
        rc, data = self.build({
            "events/ns.txt": (
                'namespace = zz_chain1\n\ncountry_event = {\n\tid = zz_chain1.1\n'
                '\tis_triggered_only = yes\n\toption = { name = "zz_chain1.1.a" }\n}\n'
            ),
        })
        hit = self.assert_issue(data, "bad-namespace", "error", line=1, category="events")
        self.assertIn("zz_chain1", hit["message"])
        self.assertEqual(rc, 1)

    def test_effect_inside_option_is_error(self):
        rc, data = self.build({
            "events/eio.txt": (
                'namespace = zz_eio\n\ncountry_event = {\n\tid = zz_eio.1\n'
                '\tis_triggered_only = yes\n\toption = {\n\t\tname = "zz_eio.1.a"\n'
                '\t\teffect = { add_prestige = 1 }\n\t}\n}\n'
            ),
        })
        self.assert_issue(data, "effect-in-option", "error", line=8, category="events")
        self.assertEqual(rc, 1)


# --------------------------------------------------------------------------- #
# 3. loc
# --------------------------------------------------------------------------- #
class TestLocCategory(_Base):
    """本地化：缺 BOM、被引用的键缺失。"""

    EV_OK = (
        'namespace = zz_loc\n\ncountry_event = {\n\tid = zz_loc.1\n'
        '\ttitle = "zz_loc.1.t"\n\tdesc = "zz_loc.1.d"\n\tis_triggered_only = yes\n'
        '\toption = { name = "zz_loc.1.a" }\n}\n'
    )

    def test_yml_without_bom_is_error(self):
        """★ 缺 BOM 必须是 error（vanilla 578 个 .yml 全部带 BOM）。"""
        rc, data = self.build({
            "events/ok.txt": self.EV_OK,
            "localisation/zz_loc_l_english.yml": (
                'l_english:\n zz_loc.1.t:0 "t"\n zz_loc.1.d:0 "d"\n zz_loc.1.a:0 "a"\n',
                False,   # ← 显式：不加 BOM
            ),
        })
        self.assert_issue(data, "missing-bom", "error", category="loc")
        self.assertEqual(rc, 1)

    def test_yml_with_bom_is_clean(self):
        """带 BOM 的同名文件不该报 missing-bom（防误报）。"""
        _, data = self.build({
            "events/ok.txt": self.EV_OK,
            "localisation/zz_loc_l_english.yml": (
                'l_english:\n zz_loc.1.t:0 "t"\n zz_loc.1.d:0 "d"\n zz_loc.1.a:0 "a"\n',
                True,
            ),
        })
        self.assertEqual([i for i in data["issues"] if i["code"] == "missing-bom"], [],
                         "带 BOM 的 .yml 被误报成缺 BOM")

    def test_missing_loc_key_is_warn(self):
        rc, data = self.build({
            "events/ok.txt": (
                'namespace = zz_lk\n\ncountry_event = {\n\tid = zz_lk.1\n'
                '\ttitle = "zz_lk.1.t"\n\tdesc = "zz_lk.1.d"\n\tis_triggered_only = yes\n'
                '\toption = { name = "zz_lk.NO_SUCH_KEY" }\n}\n'
            ),
            "localisation/zz_lk_l_english.yml": (
                'l_english:\n zz_lk.1.t:0 "t"\n zz_lk.1.d:0 "d"\n',
                True,
            ),
        })
        hit = self.assert_issue(data, "missing-loc-key", "warn", category="loc")
        self.assertIn("zz_lk.NO_SUCH_KEY", hit["message"])
        self.assertEqual(rc, 0, "缺 loc 键只是 warn，退出码应仍为 0")


# --------------------------------------------------------------------------- #
# 4. modifiers
# --------------------------------------------------------------------------- #
class TestModifiersCategory(_Base):
    """修正：引用了未定义的修正名。"""

    def test_undefined_modifier_is_info(self):
        """★ 只是 info：可能来自 vanilla，报 warn 会对合法引用报假错。"""
        rc, data = self.build({
            "events/ok.txt": (
                'namespace = zz_mod\n\ncountry_event = {\n\tid = zz_mod.1\n'
                '\tis_triggered_only = yes\n\timmediate = {\n'
                '\t\tadd_country_modifier = { name = zz_mod_ghost duration = 365 }\n\t}\n'
                '\toption = { name = "zz_mod.1.a" }\n}\n'
            ),
            "localisation/zz_mod_l_english.yml": (
                'l_english:\n zz_mod.1.a:0 "a"\n', True,
            ),
        })
        hit = self.assert_issue(data, "modifier-ref", "info", category="modifiers")
        self.assertIn("zz_mod_ghost", hit["message"])
        self.assertGreater(hit["line"], 0, "modifier-ref 应带行号")
        self.assertEqual(rc, 0)

    def test_defined_modifier_is_clean(self):
        """定义了就不再报（防误报）。"""
        _, data = self.build({
            "common/event_modifiers/zz_mod_m.txt":
                'zz_mod_ghost = { prestige = 0.5 }\n',
            "events/ok.txt": (
                'namespace = zz_mod\n\ncountry_event = {\n\tid = zz_mod.1\n'
                '\tis_triggered_only = yes\n\timmediate = {\n'
                '\t\tadd_country_modifier = { name = zz_mod_ghost duration = 365 }\n\t}\n'
                '\toption = { name = "zz_mod.1.a" }\n}\n'
            ),
            "localisation/zz_mod_l_english.yml": (
                'l_english:\n zz_mod.1.a:0 "a"\n zz_mod_ghost:0 "g"\n', True,
            ),
        })
        self.assertEqual([i for i in data["issues"] if i["code"] == "modifier-ref"], [],
                         "已定义的修正被误报")


# --------------------------------------------------------------------------- #
# 5. variables
# --------------------------------------------------------------------------- #
class TestVariablesCategory(_Base):
    """变量：读取了从未 set 的变量。"""

    def test_uninitialized_variable_is_warn(self):
        rc, data = self.build({
            "events/ok.txt": (
                'namespace = zz_var\n\ncountry_event = {\n\tid = zz_var.1\n'
                '\tis_triggered_only = yes\n\ttrigger = {\n'
                '\t\tcheck_variable = { which = zz_var_never_set value = 1 }\n\t}\n'
                '\toption = { name = "zz_var.1.a" }\n}\n'
            ),
            "localisation/zz_var_l_english.yml": (
                'l_english:\n zz_var.1.a:0 "a"\n', True,
            ),
        })
        hit = self.assert_issue(data, "var-uninitialized", "warn", line=7, category="variables")
        self.assertIn("zz_var_never_set", hit["message"])
        self.assertEqual(rc, 0)

    def test_set_then_read_is_clean(self):
        _, data = self.build({
            "events/ok.txt": (
                'namespace = zz_var2\n\ncountry_event = {\n\tid = zz_var2.1\n'
                '\tis_triggered_only = yes\n\timmediate = {\n'
                '\t\tset_variable = { which = zz_var_ok value = 1 }\n\t}\n'
                '\ttrigger = {\n\t\tcheck_variable = { which = zz_var_ok value = 1 }\n\t}\n'
                '\toption = { name = "zz_var2.1.a" }\n}\n'
            ),
            "localisation/zz_var2_l_english.yml": (
                'l_english:\n zz_var2.1.a:0 "a"\n', True,
            ),
        })
        self.assertEqual([i for i in data["issues"] if i["code"] == "var-uninitialized"], [],
                         "先 set 后读被误报")


# --------------------------------------------------------------------------- #
# 6. flags
# --------------------------------------------------------------------------- #
class TestFlagsCategory(_Base):
    """旗标：检查了从未 set 的旗标。"""

    def test_flag_not_set_is_warn(self):
        rc, data = self.build({
            "events/ok.txt": (
                'namespace = zz_flg\n\ncountry_event = {\n\tid = zz_flg.1\n'
                '\tis_triggered_only = yes\n\ttrigger = {\n'
                '\t\thas_country_flag = zz_flg_ghost\n\t}\n'
                '\toption = { name = "zz_flg.1.a" }\n}\n'
            ),
            "localisation/zz_flg_l_english.yml": (
                'l_english:\n zz_flg.1.a:0 "a"\n', True,
            ),
        })
        hit = self.assert_issue(data, "flag-not-set", "warn", line=7, category="flags")
        self.assertIn("zz_flg_ghost", hit["message"])
        self.assertEqual(rc, 0)

    def test_set_then_check_is_clean(self):
        _, data = self.build({
            "events/ok.txt": (
                'namespace = zz_flg2\n\ncountry_event = {\n\tid = zz_flg2.1\n'
                '\tis_triggered_only = yes\n\timmediate = {\n'
                '\t\tset_country_flag = zz_flg_ok\n\t}\n'
                '\ttrigger = {\n\t\thas_country_flag = zz_flg_ok\n\t}\n'
                '\toption = { name = "zz_flg2.1.a" }\n}\n'
            ),
            "localisation/zz_flg2_l_english.yml": (
                'l_english:\n zz_flg2.1.a:0 "a"\n', True,
            ),
        })
        self.assertEqual([i for i in data["issues"] if i["code"] == "flag-not-set"], [],
                         "先 set 后检查被误报")


# --------------------------------------------------------------------------- #
# 跨类别：干净样本必须 0 error
# --------------------------------------------------------------------------- #
class TestNoFalsePositives(_Base):
    """六个类别合起来，一个完全合规的 mod 必须 0 error。"""

    def test_clean_mod_has_no_error(self):
        _, data = self.build({
            "events/ok.txt": (
                'namespace = zz_clean\n\ncountry_event = {\n\tid = zz_clean.1\n'
                '\ttitle = "zz_clean.1.t"\n\tdesc = "zz_clean.1.d"\n'
                '\tis_triggered_only = yes\n'
                '\ttrigger = {\n\t\thas_country_flag = zz_clean_done\n\t}\n'
                '\timmediate = {\n\t\tset_country_flag = zz_clean_done\n'
                '\t\tset_variable = { which = zz_clean_x value = 1 }\n\t}\n'
                '\toption = {\n\t\tname = "zz_clean.1.a"\n'
                '\t\tadd_prestige = 1\n\t}\n}\n'
            ),
            "common/event_modifiers/zz_clean_m.txt":
                'zz_clean_mod = { prestige = 0.5 }\n',
            "localisation/zz_clean_l_english.yml": (
                'l_english:\n'
                ' zz_clean.1.t:0 "t"\n zz_clean.1.d:0 "d"\n zz_clean.1.a:0 "a"\n'
                ' zz_clean_mod:0 "m"\n', True,
            ),
        })
        errors = [i for i in data["issues"] if i["severity"] == "error"]
        self.assertEqual(errors, [],
                         "干净 mod 出现 error（误报）："
                         + "; ".join(f"{e['code']}@{e['line']}" for e in errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
