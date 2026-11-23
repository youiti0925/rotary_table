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
        return I.install(self.src, self.dst, **kw)

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
        self.assertEqual((self.dst / "settings.json").read_bytes(), b'{"a": 1}')
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
        self.assertEqual((self.dst / "settings.json").read_bytes(), b'{"new": 1}')

    def test_bundle_folder_itself_is_not_taken_over(self):
        # 配布セットを置いた開発用フォルダ（src の親）は「前のフォルダ」として拾わない
        repo = self.tmp / "配布" / "ND287分割測定"
        touch(repo / "settings.json")
        touch(repo / "ND287分割測定.bat")
        self.run_install(roots=[(self.tmp / "配布", 4)])
        self.assertFalse((self.dst / "settings.json").exists())

    def test_skips_venv_and_git(self):
        old = self.home / "Downloads" / "x" / ".venv" / "inner"
        touch(old / "settings.json")
        touch(old / "ND287分割測定.bat")
        self.run_install()
        self.assertFalse((self.dst / "settings.json").exists())

    def test_no_old_folder(self):
        lines = self.run_install()
        self.assertFalse((self.dst / "settings.json").exists())
        self.assertFalse(any("引き継ぎ" in ln for ln in lines))


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
