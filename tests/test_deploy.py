# -*- coding: utf-8 -*-
"""配布セット（各PCにダブルクリックで入れる）とインストールした FOCAS 一式の見つけ方。"""
import shutil
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

from nd287_app import deploy, focas_client

REPO = Path(__file__).resolve().parent.parent


def touch(p, data=b"x"):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class TempDir(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestInstallerFiles(unittest.TestCase):
    def test_parts_exist(self):
        for name in ("インストール.bat", "はじめにお読みください.txt"):
            self.assertTrue((REPO / "installer" / name).is_file(), name)
        self.assertTrue((REPO / "配布セットを作る.bat").is_file())
        self.assertTrue((REPO / "nd287_exe_entry.py").is_file())

    def test_no_powershell(self):
        # 会社のPCは Cylance が PowerShell を止める（2026-11-23 CR0579 で確認）
        self.assertFalse((REPO / "installer" / "install.ps1").exists())
        for name in ("インストール.bat",):
            data = (REPO / "installer" / name).read_bytes().lower()
            self.assertNotIn(b"powershell", data.replace(b"no powershell", b""))
            self.assertNotIn(b"cscript", data)
            self.assertNotIn(b"wscript", data)

    def test_bat_is_ascii_and_runs_the_exe(self):
        # コードページに左右されないよう、インストール.bat の中身は英数字だけにする
        data = (REPO / "installer" / "インストール.bat").read_bytes()
        data.decode("ascii")
        self.assertIn(b'start "" /wait "%~dp0app\\nd287_bunkatsu.exe" --install', data)
        self.assertIn(b"pause", data)                          # 失敗したら止まって見せる
        self.assertIn(b"\r\n", data)

    def test_exe_is_built_from_the_entry_file(self):
        # nd287_app\\__main__.py を直接渡すと相対 import で落ちる exe になる
        for name in ("配布セットを作る.bat", "exeを作る.bat"):
            text = (REPO / name).read_text(encoding="utf-8")
            self.assertIn("nd287_exe_entry.py", text, name)
            self.assertNotIn("__main__.py", text, name)

    def test_entry_file_imports_the_package(self):
        text = (REPO / "nd287_exe_entry.py").read_text(encoding="utf-8")
        self.assertIn("from nd287_app.__main__ import main", text)


class TestFwlib(TempDir):
    def test_collects_fwlib_dlls_only(self):
        for n in ("Fwlib32.dll", "fwlibe1.dll", "FWLIB30I.DLL", "other.dll", "fwlib.txt"):
            touch(self.tmp / n)
        self.assertEqual([p.name for p in deploy.fwlib_files(self.tmp)],
                         ["FWLIB30I.DLL", "Fwlib32.dll", "fwlibe1.dll"])

    def test_find_fwlib_dir_from_setting(self):
        touch(self.tmp / "Fwlib32.dll")
        self.assertEqual(deploy.find_fwlib_dir({"focas_dll_dir": str(self.tmp)}), self.tmp)


class TestCopyPython(TempDir):
    def test_skips_what_focas_does_not_need(self):
        src = self.tmp / "Python312-32"
        for rel in ("python.exe", "python312.dll", "vcruntime140.dll", "DLLs/_ctypes.pyd",
                    "Lib/os.py", "Lib/ctypes/__init__.py", "Lib/site-packages/big/x.py",
                    "Lib/test/t.py", "Lib/tkinter/a.py", "Doc/d.chm", "tcl/t.tcl", "Scripts/pip.exe",
                    "Lib/__pycache__/os.cpython-312.pyc"):
            touch(src / rel)
        dst = self.tmp / "out"
        deploy.copy_python32(src, dst)
        for rel in ("python.exe", "python312.dll", "vcruntime140.dll", "DLLs/_ctypes.pyd",
                    "Lib/os.py", "Lib/ctypes/__init__.py"):
            self.assertTrue((dst / rel).is_file(), rel)
        for rel in ("Lib/site-packages", "Lib/test", "Lib/tkinter", "Doc", "tcl", "Scripts",
                    "Lib/__pycache__"):
            self.assertFalse((dst / rel).exists(), rel)

    def test_no_python(self):
        with self.assertRaises(deploy.DeployError):
            deploy.copy_python32(self.tmp, self.tmp / "out")


class TestBundle(TempDir):
    def _make(self, **kw):
        exe = touch(self.tmp / "dist" / "nd287_bunkatsu.exe")
        masters = self.tmp / "マスタ"
        for n in ("測定条件.csv", "ユーザー回転条件.csv", "FOCAS書込ログ.csv", "FOCAS機械台帳.csv",
                  "パラメータ作成ログ.csv"):
            touch(masters / n)
        py = self.tmp / "py32"
        touch(py / "python.exe")
        touch(py / "Lib" / "os.py")
        fw = self.tmp / "fw"
        touch(fw / "Fwlib32.dll")
        touch(fw / "fwlibe1.dll")
        out = self.tmp / "配布" / "ND287分割測定"
        args = dict(exe=exe, helper=REPO / "nd287_app" / "focas_helper.py", masters=masters,
                    py32_dir=py, fwlib_dir=fw)
        args.update(kw)
        lines = deploy.build_bundle(out, **args)
        return out, lines

    def test_layout(self):
        out, lines = self._make()
        for rel in ("インストール.bat", "はじめにお読みください.txt",
                    "app/nd287_bunkatsu.exe", "app/focas_helper.py", "app/focas/python32/python.exe",
                    "app/focas/python32/Lib/os.py", "app/focas/Fwlib32.dll", "app/focas/fwlibe1.dll",
                    "app/マスタ/測定条件.csv"):
            self.assertTrue((out / rel).is_file(), rel)
        # PC ごとのもの（利用者の条件・書込ログ・台帳）は配らない
        for rel in ("app/マスタ/ユーザー回転条件.csv", "app/マスタ/FOCAS書込ログ.csv",
                    "app/マスタ/FOCAS機械台帳.csv", "app/マスタ/パラメータ作成ログ.csv"):
            self.assertFalse((out / rel).exists(), rel)
        self.assertTrue(any("Fwlib32.dll" in ln for ln in lines))

    def test_without_focas(self):
        out, lines = self._make(py32_dir=None, fwlib_dir=None)
        self.assertFalse((out / "app" / "focas").exists())
        self.assertTrue(any("FOCAS は使えません" in ln for ln in lines))

    def test_rebuild_replaces_old_bundle(self):
        out, _ = self._make()
        touch(out / "app" / "古いもの.txt")
        out, _ = self._make()
        self.assertFalse((out / "app" / "古いもの.txt").exists())

    def test_missing_exe(self):
        with self.assertRaises(deploy.DeployError):
            deploy.build_bundle(self.tmp / "o", exe=self.tmp / "no.exe",
                                helper=REPO / "nd287_app" / "focas_helper.py", masters=self.tmp)


class TestBundledFocasIsFound(TempDir):
    """インストールしたPC: アプリの隣の focas\\ にある 32bit Python と Fwlib32.dll を使う。"""

    def test_bundled_python_and_dll(self):
        touch(self.tmp / "focas" / "python32" / "python.exe")
        touch(self.tmp / "focas" / "Fwlib32.dll")
        with mock.patch("nd287_app.settings.app_dir", return_value=self.tmp):
            self.assertEqual(focas_client.resolve_python32(""),
                             str(self.tmp / "focas" / "python32" / "python.exe"))
            self.assertEqual(focas_client.resolve_dll_dir(""), str(self.tmp / "focas"))
            # 設定で選んだものがあれば、そちらが先
            self.assertEqual(focas_client.resolve_dll_dir(r"C:\SG"), r"C:\SG")

    def test_nothing_bundled(self):
        with mock.patch("nd287_app.settings.app_dir", return_value=self.tmp):
            self.assertEqual(focas_client.resolve_dll_dir(""), "")


if __name__ == "__main__":
    unittest.main()
