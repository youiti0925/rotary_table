# -*- coding: utf-8 -*-
"""使わない機能を画面から隠す（2026-11-23 利用者の決定）。

機械とのやり取りは FOCAS に決めたので、FTP（機械へのFTP送信・アプリ内FTPサーバー）は
既定で画面に出さない。機能は消しておらず、settings の show_ftp=true で元どおり出る。
"""
import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path

PARAMS = dict(rotary=True, include_division=True, include_repeat=False,
              wheel_pitch=10.0, wheel_start=0.0, wheel_end=360.0,
              worm_pitch=1.0, worm_range=10.0, worm_start=0.0,
              blocks=[], repeats=1, title="TEST", machine="")


class TestFtpHidden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 本番の settings.json を触らせない（閉じると _persist が保存する）
        from nd287_app import settings as S
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path = S.settings_path
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"

    @classmethod
    def tearDownClass(cls):
        from nd287_app import settings as S
        S.settings_path = cls._orig_path
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _settings(self, **kw):
        from nd287_app.settings import DEFAULTS
        s = copy.deepcopy(DEFAULTS)
        s.update(kw)
        return s

    def _send_methods(self, s):
        import nd287_app.gui as G
        dlg = G.ProgramDialog(None, s, dict(PARAMS))
        try:
            return [dlg.cmb_send.itemData(i) for i in range(dlg.cmb_send.count())], \
                dlg.cmb_send.currentData()
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_default_is_hidden(self):
        from nd287_app.settings import DEFAULTS
        self.assertFalse(DEFAULTS["show_ftp"])

    def test_send_method_has_no_ftp_by_default(self):
        methods, cur = self._send_methods(self._settings())
        self.assertEqual(methods, ["folder", "focas"])
        self.assertEqual(cur, "folder")

    def test_saved_ftp_falls_back_to_folder_when_hidden(self):
        methods, cur = self._send_methods(self._settings(nc_send_method="ftp"))
        self.assertEqual(cur, "folder")

    def test_send_method_shows_ftp_when_enabled(self):
        methods, cur = self._send_methods(
            self._settings(show_ftp=True, nc_send_method="ftp"))
        self.assertEqual(methods, ["folder", "ftp", "focas"])
        self.assertEqual(cur, "ftp")

    def test_help_text_drops_ftp_when_hidden(self):
        import nd287_app.gui as G
        hidden = G.send_help_text(False)
        shown = G.send_help_text(True)
        self.assertNotIn("FTP", hidden)
        self.assertIn("方式2: FTP", shown)
        self.assertIn("FTPで失敗", shown)
        for t in (hidden, shown):
            self.assertNotIn("<<", t)
            self.assertIn("方式1: フォルダ／メモリカード", t)
            self.assertIn("【注意】", t)

    def _tool_menu_texts(self, s):
        import nd287_app.gui as G
        from nd287_app.nd287 import DummyDevice
        win = G.MainWindow(DummyDevice(), 10.0, 1.0, 10.0, 0.0, s)
        try:
            return [a.text() for a in win.b_tools.menu().actions()]
        finally:
            win.close()
            win.deleteLater()
            self.app.processEvents()

    def test_tools_menu(self):
        hidden = self._tool_menu_texts(self._settings())
        self.assertFalse([t for t in hidden if "FTP" in t])
        self.assertIn("機械と直接つなぐ（FOCAS）…", hidden)
        shown = self._tool_menu_texts(self._settings(show_ftp=True))
        self.assertIn("FTPサーバー…", shown)


if __name__ == "__main__":
    unittest.main()
