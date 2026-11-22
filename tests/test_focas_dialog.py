# -*- coding: utf-8 -*-
"""FocasDialog（つなぐ・状態・控え・自己診断）。偽物の機械を差し込んだヘルパーを子プロセスで起動する。

実機にはつながない（hosts は 127.0.0.1。偽物のヘルパーは接続先を見ない）。
本物の DLL も 32bit Python も呼ばない。settings.json は一時フォルダへ逃がす。
画面サイズの試験は tests/test_batch_build.TestDialogsFitOnSmallScreen.test_focas_dialog。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAKE_HELPER = str(HERE / "fake_focas_helper.py")
REPO = HERE.parent


class DialogBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from nd287_app import settings as S
        import nd287_app.gui as G
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path = S.settings_path
        cls._orig_app_dir = G.app_dir
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"
        G.app_dir = lambda: Path(cls._tmp)               # focas_helper.log も一時フォルダへ

    @classmethod
    def tearDownClass(cls):
        import shutil
        from nd287_app import settings as S
        import nd287_app.gui as G
        S.settings_path = cls._orig_path
        G.app_dir = cls._orig_app_dir
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        from PySide6 import QtWidgets
        from nd287_app.settings import DEFAULTS
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self._env_old = os.environ.get("FAKE_FOCAS_SCENARIO")
        self.work = tempfile.mkdtemp(dir=self._tmp)
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings.update(
            focas_python32=sys.executable, focas_helper_path=FAKE_HELPER,
            focas_host="127.0.0.1", focas_backup_dir=str(Path(self.work) / "控え"),
            controller_master_csv=str(REPO / "マスタ" / "制御装置マスタ.csv"))
        self.dialogs = []

    def tearDown(self):
        for d in self.dialogs:
            d.close()
            d.deleteLater()
        self.app.processEvents()
        if self._env_old is None:
            os.environ.pop("FAKE_FOCAS_SCENARIO", None)
        else:
            os.environ["FAKE_FOCAS_SCENARIO"] = self._env_old

    def scenario(self, sc):
        os.environ["FAKE_FOCAS_SCENARIO"] = json.dumps(sc)

    def dialog(self):
        import nd287_app.gui as G
        d = G.FocasDialog(None, self.settings)
        d._timer.setInterval(600000)                     # 試験中は自動の読み直しを止める
        d.warnings = []
        d._warn = lambda title, text: d.warnings.append((title, text))
        d._info = lambda title, text: d.warnings.append((title, text))
        d.show()
        self.dialogs.append(d)
        return d

    def wait(self, d, timeout=40):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.app.processEvents()
            if not d._busy:
                self.app.processEvents()
                return
            time.sleep(0.02)
        self.fail("画面の処理が終わりません")

    def connect(self, d):
        d.b_connect.click()
        self.wait(d)


class TestNoSideEffects(DialogBase):
    def test_creating_the_dialog_starts_no_process(self):
        import unittest.mock as mock
        with mock.patch.object(subprocess, "Popen",
                               side_effect=AssertionError("子プロセスを起動してはいけない")):
            d = self.dialog()
            for _ in range(5):
                self.app.processEvents()
            self.assertIsNone(d.client)
            self.assertFalse(d._timer.isActive())
            d.close()

    def test_buttons_before_connecting(self):
        d = self.dialog()
        self.assertTrue(d.b_connect.isEnabled())
        for b in (d.b_disconnect, d.b_backup, d.b_diag, d.b_view, d.b_progs, d.b_write):
            self.assertFalse(b.isEnabled())
        self.assertIn("つながっていません", d.lbl_state.text())

    def test_write_features_are_not_there(self):
        d = self.dialog()
        self.assertFalse(d.b_write.isEnabled())
        self.assertFalse(hasattr(d, "write_params"))


class TestConnect(DialogBase):
    def test_connect_shows_machine_status_and_unit_candidate(self):
        self.scenario({"alarms": [[100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]]})
        d = self.dialog()
        self.connect(d)
        self.assertIn("つながりました", d.lbl_state.text())
        self.assertIn("31i", d.lbl_machine.text())
        self.assertIn("18.0", d.lbl_machine.text())
        self.assertIn("3C7B5D01-49C1F435-5E28C389-6592C486", d.lbl_machine.text())
        self.assertIn("号機23", d.lbl_unit.text())
        self.assertIn("とみられます", d.lbl_unit.text())
        self.assertGreaterEqual(d.cmb_unit.findData("23"), 0)
        self.assertEqual(d.cmb_unit.currentData(), "")           # 候補があっても黙って選ばない
        self.assertIn("リセット", d.lbl_status.text())
        self.assertEqual(d.tbl_alarm.rowCount(), 1)
        self.assertEqual(d.tbl_alarm.item(0, 0).text(), "SW0100")
        self.assertTrue(d._timer.isActive())
        self.assertTrue(d.b_disconnect.isEnabled())
        self.assertFalse(d.b_connect.isEnabled())
        self.assertTrue(d.b_backup.isEnabled())

    def test_writable_band_says_why_it_cannot_write(self):
        d = self.dialog()
        self.connect(d)
        text = d.lbl_writable.text()
        self.assertIn("書けません", text)
        self.assertIn("控え", text)
        self.assertIn("パラメータ書込", text)                      # PWE が OFF（SW0100 なし）

    def test_running_machine_is_shown(self):
        self.scenario({"status": {"run": 3}, "alarms": [[100, 0, 0, "x"]]})
        d = self.dialog()
        self.connect(d)
        self.assertIn("運転中", d.lbl_status.text())
        self.assertIn("運転中", d.lbl_writable.text())

    def test_poll_refreshes_state(self):
        d = self.dialog()
        self.connect(d)
        d.lbl_status.setText("古い")
        d._poll()
        self.wait(d)
        self.assertIn("リセット", d.lbl_status.text())

    def test_connect_failure_is_shown_and_can_retry(self):
        self.scenario({"connect_fail_count": 1})
        d = self.dialog()
        self.connect(d)
        self.assertIn("-16", d.lbl_state.text())
        self.assertIn("つながりません", d.lbl_state.text())
        self.assertTrue(d.b_connect.isEnabled())
        self.assertFalse(d.b_disconnect.isEnabled())
        self.connect(d)                                           # 2回目はつながる
        self.assertIn("つながりました", d.lbl_state.text())

    def test_missing_python_is_explained(self):
        self.settings["focas_python32"] = r"C:\ない\python.exe"
        d = self.dialog()
        self.connect(d)
        self.assertIn("32bit", d.lbl_state.text())
        self.assertTrue(d.b_connect.isEnabled())

    def test_unsupported_machine_can_only_look(self):
        self.scenario({"cnc_type": "0"})
        d = self.dialog()
        self.connect(d)
        self.assertIn("まだ対応していません", d.lbl_machine.text())

    def test_disconnect(self):
        d = self.dialog()
        self.connect(d)
        d.b_disconnect.click()
        self.wait(d)
        self.assertIn("切りました", d.lbl_state.text())
        self.assertFalse(d._timer.isActive())
        self.assertTrue(d.b_connect.isEnabled())
        self.assertEqual(d.tbl_alarm.rowCount(), 0)

    def test_closing_disconnects_and_ends_the_helper(self):
        d = self.dialog()
        self.connect(d)
        proc = d.client._proc
        self.assertIsNone(proc.poll())
        d.close()
        self.assertIsNotNone(proc.poll())
        self.assertIsNone(d.client)

    def test_connection_lost_is_shown(self):
        self.scenario({"lost_after": 4})
        d = self.dialog()
        self.connect(d)                                          # 接続・機種・id・状態・アラームの途中で切れる
        self.assertIn("切れ", d.lbl_state.text())
        self.assertFalse(d._connected)


class TestBackupAndDiagnosis(DialogBase):
    def test_backup_button_takes_a_backup_and_checks_it(self):
        self.scenario({"alarms": [[100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]]})
        d = self.dialog()
        self.connect(d)
        d.cmb_unit.setCurrentIndex(d.cmb_unit.findData("23"))
        d.b_backup.click()
        self.wait(d)
        self.assertIn("点検 OK", d.lbl_check.text())
        folder = d._session_backup[0]
        self.assertTrue((folder / "パラメータ.DAT").is_file())
        self.assertEqual(folder.parent.name, "3C7B5D01_31i_号機23")
        self.assertEqual(d.bar.value(), d.bar.maximum())
        self.assertIn("前回の控え", d.lbl_last.text())
        self.assertIn("完全", d.lbl_last.text())
        self.assertTrue(d.b_view.isEnabled())
        self.assertTrue(d.b_open.isEnabled())
        # 控えが取れ、状態もよく、PWE も ON（SW0100）→ 「書ける？」は緑（書込の機能はまだ無い）
        self.assertIn("いま書けます", d.lbl_writable.text())
        self.assertIn("まだ入っていません", d.lbl_writable.text())

    def test_incomplete_backup_is_flagged(self):
        d = self.dialog()
        self.connect(d)
        import nd287_app.focas_backup as B
        orig = B.take_backup

        def bad(*a, **k):
            r = orig(*a, **k)
            r.problems.append("テスト用の指摘")
            r.complete = False
            return r
        B.take_backup = bad
        try:
            d.b_backup.click()
            self.wait(d)
        finally:
            B.take_backup = orig
        self.assertIn("不完全", d.lbl_check.text())
        self.assertIn("テスト用の指摘", d.lbl_check.text())

    def test_backup_inside_basic_dir_is_refused_before_reading(self):
        basic = Path(self.work) / "BASIC"
        basic.mkdir()
        self.settings["param_basic_dir"] = str(basic)
        d = self.dialog()
        self.connect(d)
        d.e_backup.setText(str(basic / "控え"))
        d.b_backup.click()
        self.wait(d)
        self.assertTrue(any("BASIC" in t for _, t in d.warnings), d.warnings)
        self.assertFalse((basic / "控え").exists())
        self.assertIsNone(d._session_backup)
        # 設定にも保存されない（1回目の確認）
        self.assertNotIn("BASIC", self.settings["focas_backup_dir"])

    def test_unsafe_backup_folder_is_not_saved_to_settings(self):
        product = Path(self.work) / "製品"
        product.mkdir()
        self.settings["param_product_dir"] = str(product)
        before = self.settings["focas_backup_dir"]
        d = self.dialog()
        d.e_backup.setText(str(product))
        d._persist()
        self.assertEqual(self.settings["focas_backup_dir"], before)
        self.assertEqual(d.e_backup.text(), before)
        self.assertTrue(d.warnings)

    def test_cancel_button_stops_the_backup(self):
        d = self.dialog()
        self.connect(d)
        d.b_backup.click()
        self.assertTrue(d.b_cancel.isEnabled())                  # 取っている間だけ押せる
        d._cancel_job()
        self.assertTrue(d._cancel)
        self.wait(d)
        self.assertIsNotNone(d._session_backup)                   # 途中でも結果は残り、点検に出る
        self.assertFalse(d.b_cancel.isEnabled())

    def test_self_check_runs_and_shows_the_report(self):
        d = self.dialog()
        self.connect(d)
        d.b_backup.click()
        self.wait(d)
        d.b_diag.click()
        self.wait(d)
        text = d.diag.toPlainText()
        self.assertIn("[OK ]", text)
        self.assertIn("機種 31i", text)
        self.assertIn("A1", text)
        self.assertNotIn("[NG ]", text)
        d._copy_report()
        self.assertIn("機種 31i", self.app.clipboard().text())

    def test_self_check_without_backup_says_it_could_not_compare(self):
        d = self.dialog()
        self.connect(d)
        d.b_diag.click()
        self.wait(d)
        self.assertIn("控えが無い", d.diag.toPlainText())

    def test_diagnosis_button_explains(self):
        d = self.dialog()
        d.show_diagnosis()
        self.assertTrue(d.warnings)
        self.assertIn("まだ起動していません", d.warnings[-1][1])

    def test_view_params_opens_the_existing_viewer(self):
        import nd287_app.gui as G
        d = self.dialog()
        self.connect(d)
        d.b_backup.click()
        self.wait(d)
        opened = []
        orig = G.ParamViewerDialog.exec
        G.ParamViewerDialog.exec = lambda self_: opened.append(self_.e_a.text()) or 0
        try:
            d.view_params()
        finally:
            G.ParamViewerDialog.exec = orig
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].endswith("パラメータ.DAT"))

    def test_nothing_is_written_to_the_machine(self):
        """画面を一通り操作しても、書込・削除・運転開始系の関数は1度も呼ばれない。"""
        with tempfile.TemporaryDirectory() as t:
            log = os.path.join(t, "calls.txt")
            self.scenario({"calls_log": log, "alarms": [[100, 0, 0, "x"]]})
            d = self.dialog()
            self.connect(d)
            d.b_backup.click()
            self.wait(d)
            d.b_diag.click()
            self.wait(d)
            d.chk_full.setChecked(True)
            d.b_diag.click()
            self.wait(d)
            d.close()
            calls = set(Path(log).read_text().split())
        self.assertIn("cnc_rdparam", calls)
        for bad in ("cnc_wrparam", "cnc_dwnstart4", "cnc_download4", "cnc_dwnend4", "cnc_pdf_del",
                    "cnc_start", "cnc_wrmacro", "cnc_search", "pmc_wrpmcrng"):
            self.assertNotIn(bad, calls)


class TestSettings(unittest.TestCase):
    def test_focas_defaults_and_old_settings_file(self):
        from nd287_app.settings import DEFAULTS, load_settings
        for k in ("focas_python32", "focas_dll_dir", "focas_helper_path", "focas_host", "focas_port",
                  "focas_timeout", "focas_backup_dir", "focas_prog_folder", "focas_allowed_alarms",
                  "focas_verified_kinds", "focas_families", "focas_write_log_csv"):
            self.assertIn(k, DEFAULTS, k)
        self.assertEqual((DEFAULTS["focas_host"], DEFAULTS["focas_port"]), ("192.168.1.1", 8193))
        self.assertEqual(DEFAULTS["focas_allowed_alarms"], ["SW0100"])
        self.assertEqual(DEFAULTS["focas_families"], {"31": "31i"})
        self.assertEqual(DEFAULTS["focas_verified_kinds"], {"31i": ["byte/none"]})
        # focas_* を知らない古い設定ファイルを読んでも落ちず、既定値で埋まる
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "settings.json"
            p.write_text(json.dumps({"ui_font_pt": 9, "save_root": "x"}), encoding="utf-8")
            s = load_settings(p)
            self.assertEqual(s["ui_font_pt"], 9)
            self.assertEqual(s["focas_port"], 8193)
            self.assertNotIn("_load_error", s)


if __name__ == "__main__":
    unittest.main()
