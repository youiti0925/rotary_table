# -*- coding: utf-8 -*-
"""FocasProgramSendDialog（測定プログラムを機械へ直接書く）と、ProgramDialog の送り方「機械へ直接（FOCAS）」。

偽物の機械だけ。実機にはつながない。画面サイズの試験は
tests/test_batch_build.TestDialogsFitOnSmallScreen.test_focas_program_send_dialog。
"""

import copy
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parent

from fake_client import DirectClient               # noqa: E402

FOLDER = "//CNC_MEM/USER/PATH1/"


def nc(n):
    from nd287_app import fanuc
    return fanuc.nc_bytes(f"%\nO{n:04d}(TEST)\nG90\nM30\n%\n", fanuc.EOB_PUNCH)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from nd287_app import settings as S
        import nd287_app.gui as G
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path, cls._orig_app_dir = S.settings_path, G.app_dir
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"
        G.app_dir = lambda: Path(cls._tmp)

    @classmethod
    def tearDownClass(cls):
        import shutil
        from nd287_app import settings as S
        import nd287_app.gui as G
        S.settings_path, G.app_dir = cls._orig_path, cls._orig_app_dir
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        from PySide6 import QtWidgets
        from nd287_app.settings import DEFAULTS
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.work = Path(tempfile.mkdtemp(dir=self._tmp))
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings.update(focas_host="127.0.0.1", focas_backup_dir=str(self.work / "控え"),
                             focas_prog_folder=FOLDER)
        self.dialogs = []

    def tearDown(self):
        for d in self.dialogs:
            d.close()
            d.deleteLater()
        self.app.processEvents()

    def dialog(self, data, scenario=None, answer=True):
        import nd287_app.gui as G
        from nd287_app import fanuc
        self.client = DirectClient(copy.deepcopy(scenario or {}))
        d = G.FocasProgramSendDialog(None, self.settings, data, fanuc.EOB_PUNCH,
                                     client_factory=lambda s: self.client)
        d._timer.setInterval(600000)
        d.warnings = []
        d._warn = lambda t, x: d.warnings.append((t, x))
        d.confirms = []

        def confirm(chk):
            d.confirms.append((d._confirm_text(chk), chk))
            return answer
        d._confirm = confirm
        d.show()
        self.dialogs.append(d)
        return d

    def wait(self, d, timeout=40):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.app.processEvents()
            if not d._busy:
                self.app.processEvents()
                if not d._busy:
                    return
            time.sleep(0.01)
        self.fail("画面の処理が終わりません")

    def go(self, d):
        d.b_connect.click()
        self.wait(d)
        for _ in range(50):                      # つないだあとの控え（自動）
            self.app.processEvents()
            self.wait(d)
            if d._backup is not None:
                break
            time.sleep(0.01)


class TestDialog(Base):
    def test_creating_touches_no_machine(self):
        d = self.dialog(nc(555))
        for _ in range(5):
            self.app.processEvents()
        self.assertIsNone(d.client)
        self.assertEqual(self.client.calls, [])
        self.assertFalse(d.b_write.isEnabled())

    def test_connect_backup_confirm_write(self):
        d = self.dialog(nc(555))
        self.go(d)
        self.assertTrue(d.b_write.isEnabled(), d.lbl_backup.text())
        d.b_write.click()
        self.wait(d)
        self.wait(d)
        self.assertEqual(len(d.confirms), 1)
        text, chk = d.confirms[0]
        self.assertIn("O555", text)
        self.assertIn("ありません", text)
        self.assertIn(FOLDER + "O555", self.client.machine.programs)
        self.assertIn("読み戻しも一致", d.txt_result.toPlainText())
        self.assertIn("完全に一致", d.txt_result.toPlainText())

    def test_declined_writes_nothing(self):
        d = self.dialog(nc(555), answer=False)
        self.go(d)
        d.b_write.click()
        self.wait(d)
        self.assertEqual(len(d.confirms), 1)
        self.assertNotIn(FOLDER + "O555", self.client.machine.programs)
        self.assertEqual(self.client.lib.called("cnc_dwnstart4"), 0)
        self.assertIn("何も書いていません", d.txt_result.toPlainText())

    def test_same_number_is_shown_and_overwritten_only_after_approval(self):
        d = self.dialog(nc(901), answer=False)
        self.go(d)
        d.b_write.click()
        self.wait(d)
        text, chk = d.confirms[0]
        self.assertIn("上書き", text)
        self.assertTrue(chk["overwrite_needed"])
        self.assertEqual(self.client.lib.called("cnc_pdf_del"), 0)         # 断ったので消していない
        self.assertIn(FOLDER + "O901", self.client.machine.programs)
        d2 = self.dialog(nc(901), answer=True)
        self.go(d2)
        d2.b_write.click()
        self.wait(d2)
        self.wait(d2)
        self.assertEqual(self.client.lib.called("cnc_pdf_del"), 1)
        self.assertIn("入れ替えました", d2.txt_result.toPlainText())

    def test_running_machine_shows_red_band_and_cannot_write(self):
        d = self.dialog(nc(555), scenario={"status": {"run": 3}})
        d.b_connect.click()
        self.wait(d)
        self.assertTrue(d.lbl_estop.isVisible())
        self.assertIn("今は書けません", d.lbl_estop.text())
        self.assertIn("運転中", d.lbl_estop.text())
        self.assertFalse(d.b_write.isEnabled())
        self.assertEqual(self.client.lib.called("cnc_dwnstart4"), 0)

    def test_emergency_stop_band(self):
        d = self.dialog(nc(555), scenario={"status": {"emergency": 1}})
        d.b_connect.click()
        self.wait(d)
        self.assertIn("非常停止がかかっています", d.lbl_estop.text())
        self.assertFalse(d.b_write.isEnabled())

    def test_bad_program_text_disables_writing(self):
        d = self.dialog(b"G00 X0.\r\r")
        self.go(d)
        self.assertFalse(d.b_write.isEnabled())


class TestProgramDialogEntry(Base):
    def test_focas_choice_opens_the_dialog_with_nc_bytes(self):
        import nd287_app.gui as G
        from PySide6 import QtWidgets
        from nd287_app import fanuc
        seen = []

        class Spy(G.FocasProgramSendDialog):
            def exec(self):
                seen.append(self)
                return 0
        orig = G.FocasProgramSendDialog
        G.FocasProgramSendDialog = Spy
        try:
            d = G.ProgramDialog(None, self.settings, {})
            self.dialogs.append(d)
            i = d.cmb_send.findData("focas")
            self.assertGreaterEqual(i, 0)
            self.assertGreaterEqual(d.cmb_send.findData("folder"), 0)     # 既存の送り方は残る
            self.assertEqual(d.cmb_send.findData("ftp"), -1)              # FTP は隠したまま
            d.cmb_send.setCurrentIndex(i)
            self.assertEqual(d.send_stack.currentIndex(), 2)
            d.regenerate()
            d.do_send()
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]._nc, fanuc.nc_bytes(d.preview.toPlainText(), d._eob()))
            seen[0].close()
        finally:
            G.FocasProgramSendDialog = orig
            for f in ("settings.json", "settings.json.bak"):
                try:
                    os.remove(f)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
