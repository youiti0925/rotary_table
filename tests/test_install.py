# -*- coding: utf-8 -*-
"""各PCへ入れる（exe 自身の --install。PowerShell を使わない）。ショートカットは偽物に差し替える。"""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from nd287_app import install as I


def touch(p, data=b"x"):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "配布" / "ND287分割測定" / "app"
        touch(self.src / I.EXE_NAME, b"new-exe")
        touch(self.src / "focas_helper.py", b"helper")
        touch(self.src / "focas" / "python32" / "python.exe")
        touch(self.src / "focas" / "Fwlib32.dll")
        touch(self.src / "マスタ" / "測定条件.csv", b"bundled")
        touch(self.src / "マスタ" / "制御装置マスタ.csv", b"bundled")
        self.dst = self.tmp / "LocalAppData" / "ND287分割測定"
        self.home = self.tmp / "home"
        self.links = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_install(self, **kw):
        kw.setdefault("roots", [(self.home / "Documents", 4), (self.home / "Downloads", 4)])
        kw.setdefault("make_shortcut", lambda lnk, target, wd, desc: self.links.append((lnk, target)))
        self.last_lines = I.install(self.src, self.dst, **kw)
        return self.last_lines

    def mine(self):
        import json
        return json.loads((self.dst / "settings.json").read_text(encoding="utf-8"))

    def old_folder(self, name="rotary_table", settings=b'{"a": 1}', age=0):
        old = self.home / "Downloads" / name
        touch(old / "ND287分割測定.bat")
        s = touch(old / "settings.json", settings)
        t = time.time() - age
        os.utime(s, (t, t))
        return old


class TestFirstInstall(Base):
    def test_copies_app_and_masters(self):
        lines = self.run_install()
        self.assertEqual((self.dst / I.EXE_NAME).read_bytes(), b"new-exe")
        self.assertTrue((self.dst / "focas" / "Fwlib32.dll").is_file())
        self.assertEqual((self.dst / "マスタ" / "測定条件.csv").read_bytes(), b"bundled")
        self.assertIn("入れました", lines[0])
        self.assertTrue(any("FOCAS" in ln and "使えます" in ln for ln in lines))
        self.assertTrue(self.links)                       # ショートカットを作ろうとした
        self.assertTrue(all(t == self.dst / I.EXE_NAME for _l, t in self.links))

    def test_takes_over_old_settings_and_data(self):
        old = self.old_folder()
        touch(old / "測定データ" / "2026" / "a.csv")
        touch(old / "機械バックアップ" / "x" / "p.DAT")
        touch(old / "マスタ" / "測定条件.csv", b"edited-on-this-pc")
        touch(old / "マスタ" / "ユーザー回転条件.csv", b"user")
        lines = self.run_install()
        self.assertEqual(self.mine()["a"], 1)               # 前の設定が引き継がれた（場所の登録が足される）
        self.assertTrue((self.dst / "測定データ" / "2026" / "a.csv").is_file())
        self.assertTrue((self.dst / "機械バックアップ" / "x" / "p.DAT").is_file())
        # このPCで直したマスタが勝つ（配布のマスタで上書きしない）
        self.assertEqual((self.dst / "マスタ" / "測定条件.csv").read_bytes(), b"edited-on-this-pc")
        self.assertEqual((self.dst / "マスタ" / "ユーザー回転条件.csv").read_bytes(), b"user")
        self.assertEqual((self.dst / "マスタ" / "制御装置マスタ.csv").read_bytes(), b"bundled")
        self.assertTrue((old / "settings.json").is_file())  # 元は消さない
        self.assertTrue(any("引き継ぎました" in ln for ln in lines))

    def test_newest_old_folder_wins(self):
        self.old_folder("古い", settings=b'{"old": 1}', age=86400 * 30)
        self.old_folder("新しい", settings=b'{"new": 1}', age=0)
        self.run_install()
        self.assertEqual(self.mine().get("new"), 1)
        self.assertNotIn("old", self.mine())

    def test_bundle_folder_itself_is_not_taken_over(self):
        # 配布セットを置いた開発用フォルダ（src の親）は「前のフォルダ」として拾わない
        repo = self.tmp / "配布" / "ND287分割測定"
        touch(repo / "settings.json")
        touch(repo / "ND287分割測定.bat")
        self.run_install(roots=[(self.tmp / "配布", 4)])
        self.assertNotIn("x", (self.dst / "settings.json").read_text(encoding="utf-8")[:2])   # 引き継いでいない
        self.assertFalse(self.mine().get("old"))
        self.assertNotIn("引き継ぎ", " ".join(self.last_lines))

    def test_skips_venv_and_git(self):
        old = self.home / "Downloads" / "x" / ".venv" / "inner"
        touch(old / "settings.json")
        touch(old / "ND287分割測定.bat")
        self.run_install()
        self.assertEqual(self.mine().get("a"), None)

    def test_no_old_folder(self):
        lines = self.run_install()
        self.assertEqual(set(self.mine()), {"param_product_dir"})   # 場所の登録だけ（この配布セットには BASIC が無い）
        self.assertFalse(any("引き継ぎ" in ln for ln in lines))


class TestBasicAndLocations(Base):
    """BASIC を入れて場所を自動で登録する・製品データの場所の既定値（共有サーバ）。"""

    def setUp(self):
        super().setUp()
        touch(self.src / "BASIC" / "F10BASIC.dat", b"b10")
        touch(self.src / "BASIC" / "F23BASIC.dat", b"b23")

    def settings(self):
        import json
        return json.loads((self.dst / "settings.json").read_text(encoding="utf-8"))

    def test_basic_is_copied_and_registered_on_a_new_pc(self):
        lines = self.run_install()
        self.assertEqual((self.dst / "BASIC" / "F23BASIC.dat").read_bytes(), b"b23")
        st = self.settings()                                  # 設定が無かったPCでも、2つは登録される
        self.assertEqual(st["param_basic_dir"], str(self.dst / "BASIC"))
        self.assertEqual(st["param_product_dir"],
                         "\\\\svn03\\文書共有\\工機部内\\共用\\システム\\パラメータ表\\data")
        self.assertTrue(any("BASICの場所を登録しました" in ln for ln in lines))

    def test_partial_settings_file_is_valid_for_the_app(self):
        from nd287_app import settings as S
        self.run_install()
        st = S.load_settings(self.dst / "settings.json")
        self.assertEqual(st["param_basic_dir"], str(self.dst / "BASIC"))
        self.assertIn("baudrate", st)                          # 足りない項目は既定値で補われる

    def test_existing_valid_basic_dir_is_kept(self):
        import json
        mine = self.tmp / "マイBASIC"
        touch(mine / "F10BASIC.dat")
        touch(self.dst / "settings.json", json.dumps(
            {"param_basic_dir": str(mine), "param_product_dir": "X:\\data"}).encode("utf-8"))
        self.run_install()
        st = self.settings()
        self.assertEqual(st["param_basic_dir"], str(mine))     # このPCで決めた場所は変えない
        self.assertEqual(st["param_product_dir"], "X:\\data")
        self.assertFalse((self.dst / "settings.json.bak_install").exists())   # 変えなければ控えも作らない

    def test_missing_basic_dir_is_replaced_and_backup_kept(self):
        import json
        touch(self.dst / "settings.json", json.dumps(
            {"param_basic_dir": str(self.tmp / "消えたフォルダ"), "param_product_dir": "X:\\data",
             "keep": 1}).encode("utf-8"))
        self.run_install()
        st = self.settings()
        self.assertEqual(st["param_basic_dir"], str(self.dst / "BASIC"))
        self.assertEqual(st["keep"], 1)                        # ほかの項目はそのまま
        self.assertTrue((self.dst / "settings.json.bak_install").is_file())

    def test_update_does_not_overwrite_basic_edited_on_this_pc(self):
        self.run_install()
        (self.dst / "BASIC" / "F23BASIC.dat").write_bytes(b"edited")
        touch(self.src / "BASIC" / "F99BASIC.dat", b"new")
        self.run_install()
        self.assertEqual((self.dst / "BASIC" / "F23BASIC.dat").read_bytes(), b"edited")
        self.assertEqual((self.dst / "BASIC" / "F99BASIC.dat").read_bytes(), b"new")

    def test_no_basic_in_bundle_registers_only_product_dir(self):
        import shutil as sh
        sh.rmtree(self.src / "BASIC")
        self.run_install()
        st = self.settings()
        self.assertNotIn("param_basic_dir", st)
        self.assertIn("param_product_dir", st)

    def test_custom_param_dir_registered_only_when_it_exists_and_empty(self):
        from nd287_app import install
        orig = install.CUSTOM_DIR_CANDIDATES
        try:
            ok = self.tmp / "特注パラメータ"
            ok.mkdir()
            install.CUSTOM_DIR_CANDIDATES = [str(self.tmp / "無い"), str(ok)]
            lines = self.run_install()
            self.assertEqual(self.settings()["param_custom_dir"], str(ok))
            self.assertTrue(any("特注パラの場所を登録しました" in ln for ln in lines))
            install.CUSTOM_DIR_CANDIDATES = [str(self.tmp / "無い")]     # 無ければ登録しない
            (self.dst / "settings.json").unlink()
            self.run_install()
            self.assertNotIn("param_custom_dir", self.settings())
        finally:
            install.CUSTOM_DIR_CANDIDATES = orig

    def test_default_product_dir_is_the_company_share(self):
        from nd287_app.settings import DEFAULTS
        self.assertTrue(DEFAULTS["param_product_dir"].startswith("\\\\svn03\\"))
        self.assertTrue(DEFAULTS["param_product_dir"].endswith("\\パラメータ表\\data"))


class TestUpdate(Base):
    def test_update_keeps_settings_data_and_masters(self):
        touch(self.dst / I.EXE_NAME, b"old-exe")
        touch(self.dst / "settings.json", b"mine")
        touch(self.dst / "測定データ" / "a.csv", b"data")
        touch(self.dst / "マスタ" / "測定条件.csv", b"edited")
        self.old_folder(settings=b"should-not-be-used")
        lines = self.run_install()
        self.assertIn("更新しました", lines[0])
        self.assertEqual((self.dst / I.EXE_NAME).read_bytes(), b"new-exe")
        self.assertEqual((self.dst / "settings.json").read_bytes(), b"mine")
        self.assertEqual((self.dst / "測定データ" / "a.csv").read_bytes(), b"data")
        self.assertEqual((self.dst / "マスタ" / "測定条件.csv").read_bytes(), b"edited")
        self.assertEqual((self.dst / "マスタ" / "制御装置マスタ.csv").read_bytes(), b"bundled")


class TestErrors(Base):
    def test_no_exe_in_bundle(self):
        (self.src / I.EXE_NAME).unlink()
        with self.assertRaises(I.InstallError):
            self.run_install()

    def test_refuses_to_install_onto_itself(self):
        with self.assertRaises(I.InstallError):
            I.install(self.src, self.src, roots=[], make_shortcut=lambda *a: None)

    def test_shortcut_failure_does_not_stop_install(self):
        def boom(*a):
            raise OSError("だめ")
        lines = self.run_install(make_shortcut=boom)
        self.assertTrue((self.dst / I.EXE_NAME).is_file())
        self.assertTrue(any("ショートカットを作れませんでした" in ln for ln in lines))

    def test_main_quiet_writes_log_and_exit_code(self):
        import unittest.mock as mock
        with mock.patch("nd287_app.settings.app_dir", return_value=self.src), \
                mock.patch.object(I, "create_shortcut", lambda *a: None), \
                mock.patch.object(I, "default_roots", return_value=[]):
            self.assertEqual(I.main(["--install", "--quiet", "--dest", str(self.dst)]), 0)
            self.assertIn("入れました", (self.dst / "install.log").read_text(encoding="utf-8"))
            (self.src / I.EXE_NAME).unlink()
            self.assertEqual(I.main(["--install", "--quiet", "--dest", str(self.dst)]), 1)


if __name__ == "__main__":
    unittest.main()
