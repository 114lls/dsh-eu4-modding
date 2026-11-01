"""skip 分类机制的测试 —— 「没执行」和「通过」必须能被区分开。

为什么单列一个文件
----------------
2026-11-01 实测：同一套测试，无 `EU4_CWT_DIR` 时 skipped=14，有它时 skipped=3。
差出的 11 个是白名单覆盖相关断言（treasury 分类、覆盖率、cross_check、corpus_only 分级）——
它们**没有执行**，不是「通过」。

但「150 tests OK」这种写法会让人误以为 150 个都验过了。
复查者原话：「skip 意味着没执行，而『没执行』和『通过』在发布判定里不是一回事。」

所以：
  1. 每个 SkipTest 都必须带分类前缀（[NEED-CWT] / [NEED-MOD] / [NEED-REF] / [NEED-LOG] / [SLOW]）
  2. 这条规则本身要可测，否则以后新增用例忘加前缀就统计不出来

跑法：
    python -m unittest tests.test_skip_hygiene -v
"""
from __future__ import annotations

import ast
import io
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

# 允许的分类前缀。新增一类必须同时加进这里，并在 status 报告里单列。
TAGS = ("[NEED-CWT]", "[NEED-MOD]", "[NEED-REF]", "[NEED-LOG]", "[SLOW]")


def _py_files() -> list[str]:
    return [os.path.join(HERE, f) for f in sorted(os.listdir(HERE))
            if f.startswith("test_") and f.endswith(".py")]


def _skip_calls(path: str) -> list[tuple[int, str]]:
    """返回 [(行号, 首个参数字符串)]，只抓 unittest.SkipTest("...") 字面量调用。"""
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = ""
        if isinstance(f, ast.Attribute):
            name = f.attr
        elif isinstance(f, ast.Name):
            name = f.id
        if name != "SkipTest":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            out.append((node.lineno, node.args[0].value))
    return out


class TestSkipTags(unittest.TestCase):
    def test_every_skip_has_a_tag(self):
        """★ 每个 SkipTest 都必须带分类前缀，否则无法统计「哪些没执行」。"""
        bad: list[str] = []
        for p in _py_files():
            for lineno, msg in _skip_calls(p):
                if not any(msg.startswith(t) for t in TAGS):
                    bad.append(f"{os.path.basename(p)}:{lineno} {msg[:60]}")
        self.assertEqual(bad, [],
                         "这些 SkipTest 缺分类前缀：\n  " + "\n  ".join(bad))

    def test_tags_are_known(self):
        """前缀必须是已知的五类，防止随手写个新前缀导致统计口径漂移。"""
        unknown: list[str] = []
        for p in _py_files():
            for lineno, msg in _skip_calls(p):
                m = re.match(r"\[([A-Z\-]+)\]", msg)
                if m and f"[{m.group(1)}]" not in TAGS:
                    unknown.append(f"{os.path.basename(p)}:{lineno} [{m.group(1)}]")
        self.assertEqual(unknown, [], "出现了未登记的前缀：\n  " + "\n  ".join(unknown))

    def test_this_file_is_exempt(self):
        """本文件自己不应该含有带前缀的 SkipTest（否则会被误统计）。"""
        p = os.path.abspath(__file__)
        for _ln, msg in _skip_calls(p):
            self.assertFalse(any(msg.startswith(t) for t in TAGS),
                             "本文件的 SkipTest 不该带前缀（会把自身算进统计）")


class TestSkipAccounting(unittest.TestCase):
    """验证「按前缀统计 skip 数」这个口径本身可用。"""

    def test_count_by_tag(self):
        counts: dict[str, int] = {t: 0 for t in TAGS}
        for p in _py_files():
            if os.path.basename(p) == "test_skip_hygiene.py":
                continue
            for _ln, msg in _skip_calls(p):
                for t in TAGS:
                    if msg.startswith(t):
                        counts[t] += 1
                        break
        # 只断言「统计能跑通且非空」，具体数字随用例增删而变，不做硬编码
        self.assertGreater(sum(counts.values()), 0,
                           "一个带前缀的 SkipTest 都没找到 —— 统计口径失效")
        print(f"\n[skip 统计] {counts}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
