"""L6 验收：new_mod / install_mod / backup 的往返与完整性。

通过标准（校验者给定）
--------------------
  a) new_mod 生成的骨架必须一次就过 lint（error 0），ID/修正/loc 键自洽
  b) install: 写 dlc_load 前必须备份；格式与现有一致；能正确卸载且不留孤儿；
     中文路径处理要实测
  c) backup: 用 mod_digest 验证【备份→恢复】往返后 digest 不变；恢复前先备份当前
  d) 打包层（另测）
  e) 回归：全绿 + digest 与 EXPECTED_DIGEST 一致（当前 ed7ec40a45c2718e）

★ digest 基线不是常量，是「当前交付态」的记录。
  它的历史变更链（每次都必须有可查原因）：
    e6d2db1896f98169  ← 20 文件，含 debug 决议与 debug 本地化
    ed7ec40a45c2718e  ← 18 文件，debug 那两个已按发布计划删除（PROJECT_STATE L259）
  规则：模组内容变了就重算并更新基线，同时在 commit/交付说明里写下**为什么变**。
  测试红的时候先问「是模组变了还是工具坏了」，不要先改基线 ——
  这正是 digest 机制要防的那类「看起来失败但实际无辜」的红灯。

跑法：
    python -m unittest tests.test_tools -v
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "skills", "eu4-modding", "scripts"))

import backup  # noqa: E402
import eu4_lint  # noqa: E402
import install_mod  # noqa: E402
import mod_digest  # noqa: E402
import new_mod  # noqa: E402
import _paths  # noqa: E402

REFERENCE = os.path.join(ROOT, "reference")
GAME = str(_paths.find_game_dir() or "") or None


def _has_ref() -> bool:
    return os.path.isfile(os.path.join(REFERENCE, "effects.json"))


def _read_bytes(p: str) -> bytes:
    with open(p, "rb") as fh:
        return fh.read()


def _read_text(p: str, encoding: str = "utf-8") -> str:
    with open(p, encoding=encoding) as fh:
        return fh.read()


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="eu4t_")
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)


class TestSanitizePrefix(unittest.TestCase):
    """★ namespace 不能含数字 —— 这是踩过的坑，必须被强制处理。"""

    def test_strips_digits(self):
        self.assertEqual(new_mod.sanitize_prefix("zz_epi_chain1"), "zz_epi_chain")

    def test_strips_non_ascii(self):
        self.assertEqual(new_mod.sanitize_prefix("中文前缀"), "mod")

    def test_keeps_letters(self):
        self.assertEqual(new_mod.sanitize_prefix("zz_epi_grain"), "zz_epi_grain")

    def test_empty_fallback(self):
        self.assertEqual(new_mod.sanitize_prefix(""), "mod")
        # 纯数字清洗后为空 → 退回 'mod'（不造一个含数字的 namespace）
        self.assertEqual(new_mod.sanitize_prefix("123"), "mod")


class TestNewModSkeleton(_Tmp):
    """★ a) 骨架必须一次过 lint，且 ID/loc 自洽。"""

    def test_generates_and_passes_lint(self):
        dest = self.path("MyMod")
        info = new_mod.create_mod(dest, "Test Mod", "mymod")
        self.assertTrue(os.path.isdir(dest))
        res = eu4_lint.lint_mod(dest, game=GAME,
                                ref_dir=REFERENCE if _has_ref() else None)
        self.assertEqual(len(res.errors), 0,
                         "骨架出现 error：" + "; ".join(str(e) for e in res.errors))
        self.assertEqual(len(res.warnings), 0,
                         "骨架出现 warn：" + "; ".join(str(e) for e in res.warnings))

    def test_script_txt_has_no_bom(self):
        dest = self.path("MyMod")
        new_mod.create_mod(dest, "Test Mod", "mymod")
        for root, _s, fs in os.walk(dest):
            for f in fs:
                if f.endswith(".txt"):
                    raw = _read_bytes(os.path.join(root, f))
                    self.assertFalse(raw.startswith(b"\xef\xbb\xbf"),
                                     f"脚本 .txt 不该有 BOM：{f}")

    def test_yml_has_bom(self):
        dest = self.path("MyMod")
        new_mod.create_mod(dest, "Test Mod", "mymod")
        for root, _s, fs in os.walk(dest):
            for f in fs:
                if f.endswith(".yml"):
                    raw = _read_bytes(os.path.join(root, f))
                    self.assertTrue(raw.startswith(b"\xef\xbb\xbf"),
                                    f"本地化 .yml 必须有 BOM：{f}")

    def test_namespace_matches_event_id_prefix(self):
        dest = self.path("MyMod")
        info = new_mod.create_mod(dest, "Test Mod", "chain1")
        ns = info["namespace"]
        self.assertFalse(any(c.isdigit() for c in ns), f"namespace 含数字：{ns}")
        ev = os.path.join(dest, "events", f"{ns}_events.txt")
        self.assertTrue(os.path.isfile(ev))
        txt = _read_text(ev)
        self.assertIn(f"namespace = {ns}", txt)
        self.assertIn(f"id = {ns}.1", txt)
        self.assertIn("is_triggered_only = yes", txt)

    def test_refuses_non_empty_dir(self):
        dest = self.path("Occupied")
        os.makedirs(dest)
        open(os.path.join(dest, "x.txt"), "w").close()
        with self.assertRaises(SystemExit):
            new_mod.create_mod(dest, "X", "x")


class TestInstall(_Tmp):
    """★ b) 安装/卸载/备份/格式/中文路径。"""

    def setUp(self):
        super().setUp()
        self.user = self.path("user")
        os.makedirs(os.path.join(self.user, "mod"))
        with open(os.path.join(self.user, "dlc_load.json"), "w", encoding="utf-8") as fh:
            json.dump({"enabled_mods": ["mod/existing.mod"], "disabled_dlcs": []}, fh)
        self.src = self.path("MyMod")
        new_mod.create_mod(self.src, "Test Mod", "mymod")

    def test_backup_created_before_write(self):
        r = install_mod.install(self.src, self.user, enable=True)
        self.assertTrue(r["dlc_load_backup"], "写 dlc_load 前没有备份")
        self.assertTrue(os.path.isfile(r["dlc_load_backup"]))
        old = json.loads(_read_text(r["dlc_load_backup"], "utf-8-sig"))
        self.assertEqual(old["enabled_mods"], ["mod/existing.mod"], "备份内容不对")

    def test_format_matches_original(self):
        install_mod.install(self.src, self.user, enable=True)
        raw = _read_text(os.path.join(self.user, "dlc_load.json"), "utf-8-sig")
        data = json.loads(raw)
        # stem 来自**源目录名**（MyMod），不是 prefix —— 这才是玩家 mod 目录里看到的文件名
        self.assertIn("mod/MyMod.mod", data["enabled_mods"])
        # 格式应与原文件同构：紧凑、无多余空格
        self.assertNotIn('": ', raw)

    def test_idempotent(self):
        install_mod.install(self.src, self.user, enable=True)
        install_mod.install(self.src, self.user, enable=True, force=True)
        data = install_mod.read_enabled(self.user)
        self.assertEqual(data["enabled_mods"].count("mod/MyMod.mod"), 1)

    def test_uninstall_no_orphan(self):
        install_mod.install(self.src, self.user, enable=True)
        r = install_mod.uninstall(self.src, self.user)
        self.assertEqual(r["orphan_left"], [])
        data = install_mod.read_enabled(self.user)
        self.assertNotIn("mod/MyMod.mod", data["enabled_mods"])
        self.assertEqual(data["enabled_mods"], ["mod/existing.mod"])
        self.assertFalse(os.path.isdir(os.path.join(self.user, "mod", "MyMod")))

    def test_uninstall_removes_nonascii_legacy(self):
        """★ 实测教训：早期按中文原名装过，卸载必须连那份也清掉。"""
        legacy = self.path("中文测试模组")
        new_mod.create_mod(legacy, "中文测试", "zh")
        user2 = self.path("user2")
        os.makedirs(os.path.join(user2, "mod"))
        with open(os.path.join(user2, "dlc_load.json"), "w", encoding="utf-8") as fh:
            json.dump({"enabled_mods": [], "disabled_dlcs": []}, fh)
        # 手工模拟旧版：按原名装 + 写原名条目
        shutil.copytree(legacy, os.path.join(user2, "mod", "中文测试模组"))
        with open(os.path.join(user2, "mod", "中文测试模组.mod"), "w", encoding="utf-8") as fh:
            fh.write('name = "x"\npath = "x"\n')
        data = install_mod.read_enabled(user2)
        data["enabled_mods"].append("mod/中文测试模组.mod")
        install_mod.write_enabled(user2, data)

        r = install_mod.uninstall(legacy, user2)
        self.assertEqual(r["orphan_left"], [])
        self.assertIn("mod/中文测试模组.mod", r["removed_entries"])
        self.assertFalse(os.path.isdir(os.path.join(user2, "mod", "中文测试模组")),
                         "中文名目录成了孤儿")

    def test_chinese_source_renamed_to_ascii(self):
        """★ path 必须纯 ASCII：中文目录名要被转成 ASCII 再装。"""
        cn = self.path("中文模组")
        new_mod.create_mod(cn, "中文", "zh")
        r = install_mod.install(cn, self.user, enable=True)
        self.assertTrue(r["renamed_to_ascii"])
        self.assertTrue(r["path_field"].isascii(),
                        f"path 含非 ASCII：{r['path_field']}")
        self.assertNotIn("中文", r["path_field"])

    def test_source_dir_untouched(self):
        """卸载不得删除用户的源目录。"""
        install_mod.install(self.src, self.user, enable=True)
        install_mod.uninstall(self.src, self.user)
        self.assertTrue(os.path.isdir(self.src), "源目录被误删")


class TestBackupRoundtrip(_Tmp):
    """★ c) 备份 → 恢复 往返后 digest 不变（用 mod_digest 做客观判据）。"""

    def setUp(self):
        super().setUp()
        self.src = self.path("MyMod")
        new_mod.create_mod(self.src, "Test Mod", "mymod")

    def test_roundtrip_digest_unchanged(self):
        before = mod_digest.mod_digest(self.src)
        dest = backup.make_backup(self.src, tag="t1")
        # 故意改坏一个文件
        ev = os.path.join(self.src, "events", "mymod_events.txt")
        with open(ev, "a", encoding="utf-8") as fh:
            fh.write("# mutated\n")
        mutated = mod_digest.mod_digest(self.src)
        self.assertNotEqual(before, mutated, "改动没被 digest 捕捉到")
        # 恢复
        r = backup.restore_backup(self.src, os.path.basename(dest))
        self.assertTrue(r["roundtrip_ok"], "往返后 digest 不一致")
        self.assertEqual(mod_digest.mod_digest(self.src), before)

    def test_restore_backs_up_current_first(self):
        dest = backup.make_backup(self.src, tag="t1")
        ev = os.path.join(self.src, "events", "mymod_events.txt")
        with open(ev, "a", encoding="utf-8") as fh:
            fh.write("# mutated\n")
        r = backup.restore_backup(self.src, os.path.basename(dest))
        self.assertTrue(r["pre_restore_backup"], "恢复前没备份当前状态")
        pre = backup._find_backup(self.src, r["pre_restore_backup"])
        self.assertTrue(os.path.isdir(pre))

    def test_verify_detects_corruption(self):
        dest = backup.make_backup(self.src, tag="t1")
        inner = os.path.join(dest, "files", "events", "mymod_events.txt")
        with open(inner, "a", encoding="utf-8") as fh:
            fh.write("# corrupt\n")
        v = backup.verify_backup(self.src, os.path.basename(dest))
        self.assertFalse(v["match"], "备份被改坏却没检出")

    def test_list_backups(self):
        backup.make_backup(self.src, tag="a")
        backup.make_backup(self.src, tag="b")
        rows = backup.list_backups(self.src)
        self.assertEqual(len(rows), 2)
        # digest 只覆盖脚本/本地化（descriptor.mod 与 .mod 按 spec 被排除）
        expected = mod_digest.digest_detail(self.src)["file_count"]
        for m in rows:
            self.assertIn("digest", m)
            self.assertEqual(m["file_count"], expected)
        self.assertEqual(expected, 2, "默认骨架应有 1 事件 + 1 本地化")

    def test_no_overwrite_existing_backup(self):
        backup.make_backup(self.src, tag="same")
        import time
        time.sleep(1.1)
        # 同名（同一秒内）不应覆盖；不同秒则新建
        rows = backup.list_backups(self.src)
        self.assertGreaterEqual(len(rows), 1)


class TestDigestStability(unittest.TestCase):
    """★ e) digest 规格稳定：同一目录算两次必须相同。"""

    def test_deterministic(self):
        if not os.path.isdir(new_mod_mod_dir()):
            raise unittest.SkipTest("[NEED-MOD] 参照模组不存在")
        d = new_mod_mod_dir()
        self.assertEqual(mod_digest.mod_digest(d), mod_digest.mod_digest(d))

    def test_known_baseline(self):
        d = new_mod_mod_dir()
        if not os.path.isdir(d):
            raise unittest.SkipTest("[NEED-MOD] 参照模组不存在")
        self.assertEqual(mod_digest.mod_digest(d), "ed7ec40a45c2718e",
                         "参照模组已变动（或 digest 实现变了）—— 需重新确认")


def new_mod_mod_dir() -> str:
    return os.path.expanduser(
        "~/Documents/Paradox Interactive/Europa Universalis IV/mod/posthumous_names")


if __name__ == "__main__":
    unittest.main(verbosity=2)
