"""pdx_parser 单元测试 —— 纯标准库，无 pytest 依赖。

跑法：
    python -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "eu4-modding", "scripts")
sys.path.insert(0, SCRIPTS)

import pdx_parser  # noqa: E402
from pdx_parser import parse_file_info, parse_text  # noqa: E402


class TestLexer(unittest.TestCase):
    def test_comment_not_inside_quotes(self):
        """vanilla 实证：`"Friedrich #0" = 100` 里的 # 不是注释。"""
        src = 'name = "Friedrich #0"'
        tree, errs = parse_text(src)
        self.assertEqual(len(errs), 0)
        self.assertEqual(tree.first_scalar("name"), "Friedrich #0")

    def test_key_outside_quotes_is_comment(self):
        src = "a = 1 # b = 2\nc = 3"
        tree, errs = parse_text(src)
        self.assertEqual(tree.first_scalar("a"), "1")
        self.assertEqual(tree.first_scalar("c"), "3")
        self.assertIsNone(tree.first_scalar("b"))

    def test_param_placeholder_is_plain_token(self):
        """scripted_effects 里 $PARAM$ 有 2226 处，不能当语法错误。"""
        src = "has_province_modifier = $MODIFIER$\nextend_province_modifier = { name = $MODIFIER$ duration = $DURATION$ }"
        tree, errs = parse_text(src)
        self.assertEqual(len(errs), 0)
        self.assertEqual(tree.first_scalar("has_province_modifier"), "$MODIFIER$")
        blk = tree.first_block("extend_province_modifier")
        self.assertEqual(blk.first_scalar("name"), "$MODIFIER$")
        self.assertEqual(blk.first_scalar("duration"), "$DURATION$")

    def test_date_is_single_token(self):
        tree, errs = parse_text("birth_date = 1444.1.24")
        self.assertEqual(len(errs), 0)
        self.assertEqual(tree.first_scalar("birth_date"), "1444.1.24")

    def test_bare_list_values(self):
        src = "group = {\n\tcatholic\n\treformed\n}"
        tree, errs = parse_text(src)
        blk = tree.first_block("group")
        bare = [e.key for e in blk.entries if getattr(e, "is_bare", False)]
        self.assertEqual(bare, ["catholic", "reformed"])

    def test_hoi4_only_operators_warn(self):
        """EU4 无 != / <= / ?= 的有效用法，解析要能吞下但给 warning。"""
        tree, errs = parse_text("a != b")
        self.assertEqual(len(tree.entries), 1)
        self.assertTrue(any(e.code == "unused-op" for e in errs))
        self.assertTrue(all(e.severity == "warning" for e in errs))


class TestStructure(unittest.TestCase):
    def test_unclosed_block_reports_open_line(self):
        """括号不平衡要报在**开块那一行**，否则用户没法定位。"""
        src = "namespace = a\n\ncountry_event = {\n\tid = a.1\n"
        tree, errs = parse_text(src)
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0].code, "unclosed-block")
        self.assertEqual(errs[0].line, 3)

    def test_stray_close_is_error(self):
        tree, errs = parse_text("a = 1\n}")
        self.assertTrue(any(e.code == "stray-close" for e in errs))

    def test_walk_paths_branches_treasury(self):
        """★ 核心：同一个 key 出现在不同语境，路径必须不同。"""
        src = (
            "country_event = {\n"
            "\ttrigger = {\n"
            "\t\tOR = { treasury = 500 }\n"
            "\t}\n"
            "\timmediate = { treasury = -50 }\n"
            "}\n"
        )
        tree, _ = parse_text(src)
        paths = [(p, e.scalar) for p, e in tree.find_paths("treasury")]
        self.assertEqual(paths[0][0], ("country_event", "trigger", "OR"))
        self.assertEqual(paths[0][1], "500")
        self.assertEqual(paths[1][0], ("country_event", "immediate"))
        self.assertEqual(paths[1][1], "-50")

    def test_walk_paths_line_numbers(self):
        src = "a = {\n  b = {\n    c = 1\n  }\n}\n"
        tree, _ = parse_text(src)
        hits = [(p, e.line) for p, e in tree.find_paths("c")]
        self.assertEqual(hits[0][0], ("a", "b"))
        self.assertEqual(hits[0][1], 3)


class TestEncoding(unittest.TestCase):
    def _tmp(self, data: bytes):
        f = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".txt")
        f.write(data)
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.remove(f.name))
        return f.name

    def test_bom_is_warning_not_error(self):
        p = self._tmp("\ufeffnamespace = zz_epi_grain\n".encode("utf-8"))
        tree, errs, info = parse_file_info(p)
        self.assertTrue(info.bom)
        self.assertEqual(tree.first_scalar("namespace"), "zz_epi_grain")
        self.assertTrue(any(e.code == "bom" and e.severity == "warning" for e in errs))
        self.assertFalse(any(e.severity == "error" for e in errs))

    def test_cp1252_is_warning_not_error(self):
        """vanilla 自身就有 `Sal\\xe9` 这类字节，不能判 error。"""
        p = self._tmp(b"# Sal\xe9\nname = x\n")
        tree, errs, info = parse_file_info(p)
        self.assertEqual(info.encoding, "latin-1")
        self.assertFalse(any(e.severity == "error" for e in errs))
        self.assertTrue(any(e.code == "encoding" for e in errs))

    def test_gb18030_chinese_accepted(self):
        p = self._tmp("desc = \"中文描述内容足够多以触发汉字密度判定\"\n".encode("gb18030"))
        tree, errs, info = parse_file_info(p)
        self.assertEqual(info.encoding, "gb18030")
        self.assertFalse(any(e.severity == "error" for e in errs))

    def test_utf8_chinese_is_clean(self):
        p = self._tmp('desc = "中文描述"\n'.encode("utf-8"))
        tree, errs, info = parse_file_info(p)
        self.assertEqual(info.encoding, "utf-8")
        self.assertEqual(len(errs), 0)


class TestRobustness(unittest.TestCase):
    def test_never_crashes_on_garbage(self):
        for src in ["", "{{{", "}}}", "a =", "a = {", "= b", '"unterminated', "a == = b"]:
            tree, errs = parse_text(src)   # 不抛异常即通过
            self.assertIsNotNone(tree)

    def test_fixtures_parse(self):
        for name in ("bad_all.txt", "bad_paren.txt"):
            p = os.path.join(HERE, "fixtures", name)
            tree, errs, info = parse_file_info(p)
            self.assertIsNotNone(tree)


if __name__ == "__main__":
    unittest.main(verbosity=2)
