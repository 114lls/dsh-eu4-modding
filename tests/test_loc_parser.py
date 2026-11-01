"""loc_parser 单元测试 —— .yml 本地化语法。

跑法：
    python -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "eu4-modding", "scripts"))

from loc_parser import parse_localisation, parse_localisation_file_info, _split_value_and_comment  # noqa: E402


class TestValueSplitting(unittest.TestCase):
    def test_plain_value(self):
        v, after, quoted = _split_value_and_comment('"中文"')
        self.assertEqual((v, after, quoted), ("中文", "", True))

    def test_trailing_comment(self):
        """vanilla 实证：`KEY:0 "值" # [LocEditor:AwaitingBatchUpdate]`"""
        v, after, quoted = _split_value_and_comment('"El Buda" # [LocEditor:AwaitingBatchUpdate]')
        self.assertEqual(v, "El Buda")
        self.assertTrue(after.startswith("#"))
        self.assertTrue(quoted)

    def test_embedded_quotes_use_last_quote(self):
        """★ vanilla 实证（00_lanfang_l_english.yml:332）：
        值内部含未转义引号，PDX 以**最后一个**引号作终止符。"""
        rest = '"I had a dream," [Root.Monarch.GetSheHe] announces, "that he saved us.""'
        v, after, quoted = _split_value_and_comment(rest)
        self.assertTrue(quoted)
        self.assertEqual(after, "")
        # 外层起始引号与最后那个终止引号被剥离，中间的 3 个引号原样保留
        self.assertEqual(v, 'I had a dream," [Root.Monarch.GetSheHe] announces, "that he saved us."')
        self.assertEqual(v.count('"'), 3)

    def test_unquoted(self):
        v, _, quoted = _split_value_and_comment("裸值")
        self.assertEqual((v, quoted), ("裸值", False))

    def test_unclosed_quote(self):
        _, _, quoted = _split_value_and_comment('"没有结尾')
        self.assertFalse(quoted)


class TestLocParse(unittest.TestCase):
    SRC = (
        "\ufeffl_english:\n"
        ' a:0 "A"\n'
        ' b:0 "B" # note\n'
        ' c:0 "带 \\" 转义"\n'
    )

    def test_basic(self):
        loc = parse_localisation(self.SRC, "<t>")
        self.assertEqual(loc.lang, "english")
        self.assertEqual(loc.root_key, "l_english")
        self.assertEqual(len(loc.entries), 3)
        self.assertEqual(loc.errors, [])

    def test_missing_bom_is_error(self):
        loc = parse_localisation("l_english:\n a:0 \"A\"\n", "<t>")
        self.assertTrue(any(e.code == "missing-bom" and e.severity == "error" for e in loc.errors))

    def test_unknown_lang_warns(self):
        loc = parse_localisation("\ufeffl_klingon:\n a:0 \"A\"\n", "<t>")
        self.assertTrue(any(e.code == "unknown-lang" for e in loc.errors))

    def test_bad_line_is_error_with_line_number(self):
        loc = parse_localisation("\ufeffl_english:\n a:0 \"A\"\n这行是垃圾\n", "<t>")
        errs = [e for e in loc.errors if e.code == "bad-loc-line"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0].line, 3)

    def test_all_comment_file_is_warning_only(self):
        """vanilla 有两个通篇注释的空占位文件，不能判 error。"""
        src = "\ufeff# [LocEditor:RedundantFile] ...\n# l_german:\n# 注释\n"
        loc = parse_localisation(src, "<t>")
        self.assertEqual(len(loc.entries), 0)
        self.assertFalse(any(e.severity == "error" for e in loc.errors))
        self.assertTrue(any(e.code == "missing-root-key" and e.severity == "warning" for e in loc.errors))

    def test_file_info_bom_roundtrip(self):
        f = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".yml")
        f.write('\ufeffl_english:\n a:0 "A"\n'.encode("utf-8"))
        f.close()
        self.addCleanup(lambda: os.path.exists(f.name) and os.remove(f.name))
        loc = parse_localisation_file_info(f.name)
        self.assertTrue(loc.info.bom)
        self.assertEqual(loc.info.encoding, "utf-8")
        self.assertEqual(len(loc.entries), 1)
        self.assertEqual(loc.errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
