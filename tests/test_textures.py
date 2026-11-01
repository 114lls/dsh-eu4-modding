"""纹理路径 / sprite 引用检查的测试。

背景：EU4 里图标路径写错**不报错**，只是图标空白 —— 又一个静默失败。
实测错法三种：
  · picture 指向的文件不存在
  · icon 用了未在本 mod 定义、也非 vanilla 的 sprite
  · 路径写了绝对路径/反斜杠（vanilla 只认 gfx/ 下相对路径）

分级：
  picture 指向明确文件但找不到 → warn（可确定是错）
  icon 用了未定义 sprite      → info（可能来自 vanilla，不能断言是错）

跑法：
    python -m unittest tests.test_textures -v
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
FIX = os.path.join(HERE, "fixtures", "textures")


def _has_ref() -> bool:
    return os.path.isfile(os.path.join(REFERENCE, "effects.json"))


class TestTextureCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = eu4_lint.lint_mod(FIX, game=GAME,
                                    ref_dir=REFERENCE if _has_ref() else None,
                                    only={"crossrefs"})

    def test_missing_picture_is_warn(self):
        hits = [i for i in self.res.issues if i.code == "texture-path"]
        self.assertTrue(hits, "没抓到 picture 指向不存在文件")
        for i in hits:
            self.assertEqual(i.severity, "warn")
            self.assertIn("zz_missing_picture", i.message)

    def test_undefined_sprite_is_info(self):
        """★ 不确定的必须报 info：sprite 可能来自 vanilla。"""
        hits = [i for i in self.res.issues if i.code == "sprite-ref"]
        self.assertTrue(hits, "没抓到未定义 sprite")
        for i in hits:
            self.assertEqual(i.severity, "info",
                             f"应为 info（可能来自 vanilla），实际 {i.severity}")

    def test_counts(self):
        s = self.res.stats
        self.assertEqual(s.get("texture_fields_checked"), 2)
        self.assertEqual(s.get("mod_images"), 0)
        self.assertEqual(s.get("mod_sprites"), 0)

    def test_has_usability_hint(self):
        """新增的 code 必须配 usability_hint，否则 --json 消费者拿不到指引。"""
        for code in ("texture-path", "sprite-ref"):
            self.assertIn(code, eu4_lint.USABILITY_HINT,
                          f"{code} 缺 usability_hint")


class TestNoFalsePositiveOnRealMod(unittest.TestCase):
    """真实模组上不能误报（它的 picture 是 DLC 内建图，不该被当成缺失）。"""

    MOD = os.path.expanduser(
        "~/Documents/Paradox Interactive/Europa Universalis IV/mod/posthumous_names")

    def test_real_mod_no_texture_warn(self):
        if not os.path.isdir(self.MOD):
            raise unittest.SkipTest("[NEED-MOD] posthumous_names 不存在")
        if not _has_ref():
            raise unittest.SkipTest("[NEED-REF] 需要 reference/（先跑 build_reference.py）")
        res = eu4_lint.lint_mod(self.MOD, game=GAME, ref_dir=REFERENCE,
                                only={"crossrefs"})
        bad = [i for i in res.issues if i.code in ("texture-path", "sprite-ref")]
        self.assertEqual(bad, [],
                         "真实模组出现纹理误报：" + "; ".join(str(i) for i in bad[:5]))


class TestCategoryMapping(unittest.TestCase):
    """纹理检查必须归入某个类别，否则 --only 会漏掉它。"""

    def test_textures_has_category(self):
        self.assertIn("check_textures", eu4_lint.CATEGORY_OF_CHECK)
        self.assertEqual(eu4_lint.CATEGORY_OF_CHECK["check_textures"], "crossrefs")

    def test_only_crossrefs_runs_textures(self):
        res = eu4_lint.lint_mod(FIX, game=GAME,
                                ref_dir=REFERENCE if _has_ref() else None,
                                only={"crossrefs"})
        self.assertGreater(res.stats.get("texture_fields_checked", 0), 0,
                           "--only crossrefs 没跑到纹理检查")


if __name__ == "__main__":
    unittest.main(verbosity=2)
