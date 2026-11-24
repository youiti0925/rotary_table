# -*- coding: utf-8 -*-
"""FocasWriteDialog（機械へ直接書く。確認 → 書込 → 結果）と、パラメータ作成画面からの入口。

偽物の機械だけ。実機にはつながない。本物の DLL も 32bit Python も呼ばない。
settings.json は一時フォルダへ逃がす。画面サイズの試験は
tests/test_batch_build.TestDialogsFitOnSmallScreen.test_focas_write_dialog。
"""

import copy
import csv
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parent

from fake_client import DirectClient               # noqa: E402

PWE = [100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]


class WriteDialogBase(unittest.TestCase):
    SCENARIO = {"alarms": [PWE]}
    VALUES = {"20": "4", "21": "1"}

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from nd287_app import settings as S
        import nd287_app.gui as G
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path = S.settings_path
        cls._orig_app_dir = G.app_dir
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"
        G.app_dir = lambda: Path(cls._tmp)

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
        self.work = Path(tempfile.mkdtemp(dir=self._tmp))
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings.update(
            focas_host="127.0.0.1", focas_backup_dir=str(self.work / "控え"),
            focas_write_log_csv=str(self.work / "書込ログ.csv"),
            controller_master_csv=str(REPO / "マスタ" / "制御装置マスタ.csv"))
        self.dialogs = []
        self.client = DirectClient(copy.deepcopy(self.SCENARIO))
        self.plan_calls = []

    def tearDown(self):
        for d in self.dialogs:
            d.close()
            d.deleteLater()
        self.app.processEvents()

    def make_plan_fn(self, values=None, zero_params=("1850", "1851", "1852")):
        from nd287_app import param_build as P
        values = values or self.VALUES

        def plan_fn(raw, zero_scope="product", keep_motor=False, axis=None):
            self.plan_calls.append((raw, zero_scope))
            self.keep_motor_calls = getattr(self, "keep_motor_calls", []) + [keep_motor]
            self.axis_calls = getattr(self, "axis_calls", []) + [axis]
            return P.prepare(raw, values, axis=axis or 4, reference="", zero_params=zero_params,
                             zero_scope=zero_scope)
        return plan_fn

    def dialog(self, values=None, client=None, **kw):
        import nd287_app.gui as G
        c = client or self.client
        d = G.FocasWriteDialog(None, self.settings, self.make_plan_fn(values),
                               client_factory=lambda s: c, **kw)
        d._timer.setInterval(600000)                 # 試験中は自動の読み直しを止める
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
            time.sleep(0.01)
        self.fail("画面の処理が終わりません")

    def go(self, d, backup=True):
        d.b_connect.click()
        self.wait(d)
        if backup:
            d.b_backup.click()
            self.wait(d)

    def poll(self, d):
        d._poll()
        self.wait(d)

    def rows(self, d):
        return [[d.tbl.item(i, j).text() for j in range(d.tbl.columnCount())]
                for i in range(d.tbl.rowCount())]

    def machine_value(self, no, label=""):
        return self.client.call("param_read", {"no": no, "label": label})["value"]


class TestNoSideEffects(WriteDialogBase):
    def test_creating_the_dialog_starts_no_process_and_touches_no_machine(self):
        import unittest.mock as mock
        with mock.patch.object(subprocess, "Popen",
                               side_effect=AssertionError("子プロセスを起動してはいけない")):
            d = self.dialog()
            for _ in range(5):
                self.app.processEvents()
            self.assertIsNone(d.client)
            self.assertFalse(d._timer.isActive())
            self.assertEqual(self.client.calls, [])
            d.close()

    def test_buttons_before_connecting(self):
        d = self.dialog()
        self.assertTrue(d.b_connect.isEnabled())
        for b in (d.b_disconnect, d.b_backup, d.b_write, d.b_undo, d.b_verify, d.b_stop):
            self.assertFalse(b.isEnabled())
        self.assertTrue(d.b_resolve.isHidden())
        self.assertIn("つながっていません", d.lbl_state.text())
        self.assertIn("まだ機械につながっていません", d.lbl_target.text())

    def test_write_button_needs_a_backup_of_this_time(self):
        d = self.dialog()
        self.go(d, backup=False)
        self.assertFalse(d.b_write.isEnabled())
        self.assertIn("控え", d.lbl_why.text())
        self.assertTrue(d.b_backup.isEnabled())


class TestFlow(WriteDialogBase):
    def test_connect_backup_confirm_write_result(self):
        d = self.dialog(seiban="T1234")
        self.go(d)
        # 機械の名前が上に大きく出る
        self.assertIn("31i", d.lbl_target.text())
        self.assertIn("3C7B5D01-49C1F435-5E28C389-6592C486", d.lbl_target.text())
        self.assertIn("号機23", d.lbl_target.text())
        self.assertIn("控えは完全です", d.lbl_backup.text())
        # 確認の表: 番号 / 軸 / 区分 / 今の値 / 書く値 / 型 / 確認済み
        self.assertEqual(d.tbl.columnCount(), 8)
        rows = self.rows(d)
        self.assertEqual([r[0] for r in rows], ["N00020", "N00021"])
        self.assertEqual(rows[0][1:5], ["-", "製品値", "9", "4"])
        self.assertTrue(rows[0][5].startswith("バイト"), rows[0][5])      # 利用者には日本語の型名
        self.assertEqual(rows[0][6], "○")
        self.assertEqual(rows[1][3:5], ["0", "1"])
        # ボタン1回で書ける。件数と機械の名前が出る
        self.assertTrue(d.b_write.isEnabled())
        self.assertEqual(d.b_write.text(), "この 2 件を機械に書く")
        self.assertIn("3C7B5D01", d.lbl_why.text())
        self.assertIn("2 件", d.lbl_why.text())
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)       # 押すまで書かない
        d.b_write.click()
        self.wait(d)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 2)
        self.assertEqual(self.machine_value(20), "4")
        self.assertEqual(self.machine_value(21), "1")
        self.assertEqual([r[7] for r in self.rows(d)], ["書けた・読み戻しOK"] * 2)
        res = d.txt_result.toPlainText()
        self.assertIn("2 件が「書けた・読み戻しOK」", res)
        self.assertIn("すべて合っています", res)
        self.assertNotIn("PWE", res)                                     # 戻す案内は出さない（利用者の決定）
        self.assertIn("増えたアラームは、ありません", res)
        # 同じ控えで2回は書かせない
        self.assertFalse(d.b_write.isEnabled())
        self.assertIn("もう書きました", d.lbl_why.text())
        # 書込ログとジョブ・予定が残る
        with open(self.settings["focas_write_log_csv"], encoding="utf-8-sig", newline="") as f:
            log = list(csv.reader(f))
        self.assertEqual(len(log), 3)
        self.assertEqual(log[1][3], "T1234")
        folder = d._backup[0]
        self.assertTrue((folder / "書込ジョブ.json").is_file())
        self.assertTrue((folder / "書込後の予定.DAT").is_file())
        # PW0000 は出ていない
        self.assertNotIn("PW0000", res)

    def test_base_of_the_plan_is_the_backup_taken_this_time_and_scope_is_product(self):
        d = self.dialog()
        self.go(d)
        raw, scope = self.plan_calls[-1]
        self.assertEqual(scope, "product")
        folder = d._backup[0]
        self.assertEqual(raw, (folder / "パラメータ.DAT").read_bytes().decode("cp932"))

    def test_an_already_equal_value_is_not_in_the_list(self):
        self.client.machine.set_param(20, "", "4")
        d = self.dialog()
        self.go(d)
        self.assertEqual([r[0] for r in self.rows(d)], ["N00021"])

    def test_nothing_to_write_disables_the_button(self):
        d = self.dialog(values={"20": "9"})                 # 今と同じ
        self.go(d)
        self.assertEqual(d.tbl.rowCount(), 0)
        self.assertFalse(d.b_write.isEnabled())
        self.assertIn("書くものがありません", d.lbl_why.text())

    def test_numbers_missing_on_the_machine_are_reported_and_left_out(self):
        d = self.dialog(values={"20": "4", "99999": "1"})
        self.go(d)
        self.assertEqual(d.tbl.rowCount(), 1)
        self.assertIn("機械に無い番号", d.lbl_missing.text())
        self.assertIn("99999", d.lbl_missing.text())
        self.assertIn("1 件", d.lbl_summary.text())
        self.assertTrue(d.b_write.isEnabled())

    def test_machine_that_replaced_after_the_backup_cannot_be_written(self):
        d = self.dialog()
        self.go(d)
        self.client.machine.cnc_id = [1, 2, 3, 4]               # 別の機械に替わった
        d._ident = dict(d._ident)                              # 画面の持つ CNC ID は控えと同じまま
        d.start_write()
        self.wait(d)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)
        self.assertIn("機械には何も書いていません", d.txt_result.toPlainText())
        self.assertIn("CNC ID", d.txt_result.toPlainText())


class TestWritableBand(WriteDialogBase):
    def check_blocked(self, d, text):
        self.assertFalse(d.b_write.isEnabled())
        self.assertIn(text, d.lbl_why.text())

    def test_running_paused_and_emergency_block_the_button_and_say_why(self):
        d = self.dialog()
        self.go(d)
        for kw, word in (({"run": 3}, "運転中"), ({"run": 2}, "運転中"), ({"emergency": 1}, "非常停止"),
                         ({"motion": 1}, "軸が動いています")):
            self.client.machine.status.update({"run": 0, "motion": 0, "emergency": 0})
            self.client.machine.status.update(kw)
            self.poll(d)
            self.check_blocked(d, word)
        self.client.machine.status.update({"run": 0, "motion": 0, "emergency": 0})
        self.poll(d)
        self.assertTrue(d.b_write.isEnabled())

    def test_other_alarm_blocks_but_sw0100_alone_is_fine(self):
        d = self.dialog()
        self.go(d)
        self.client.machine.alarms.append((401, 6, 1, "SERVO ALARM"))
        self.poll(d)
        self.check_blocked(d, "SV0401")
        self.client.machine.alarms.pop()
        self.poll(d)
        self.assertTrue(d.b_write.isEnabled())

    def test_pwe_off_blocks_with_guidance(self):
        d = self.dialog()
        self.go(d)
        self.client.machine.alarms.clear()
        self.poll(d)
        self.check_blocked(d, "パラメータ書込")

    def test_all_reasons_are_listed_together(self):
        d = self.dialog()
        self.go(d)
        self.client.machine.status.update({"run": 3, "emergency": 1})
        self.client.machine.alarms.clear()
        self.poll(d)
        for w in ("運転中", "非常停止", "パラメータ書込"):
            self.assertIn(w, d.lbl_why.text())

    def test_incomplete_backup_cannot_be_written(self):
        d = self.dialog()
        self.go(d, backup=False)
        self.client.lib.sc["alldir_end_rc"] = 5               # プログラム一覧が最後まで読めない → 控えは不完全
        d.b_backup.click()
        self.wait(d)
        self.assertIn("不完全", d.lbl_backup.text())
        self.assertFalse(d.b_write.isEnabled())
        self.assertEqual(d.tbl.rowCount(), 0)

    def test_status_of_the_machine_is_shown_with_raw_numbers(self):
        d = self.dialog()
        self.go(d)
        self.assertIn("リセット", d.lbl_status.text())
        self.assertIn("SW0100", d.lbl_status.text())


class TestUnverifiedTypes(WriteDialogBase):
    VALUES = {"20": "4", "1815": "001*0000"}

    def test_unverified_type_cannot_be_written_and_the_reason_is_shown(self):
        d = self.dialog()
        self.go(d)
        rows = self.rows(d)
        by = {r[0]: r for r in rows}
        self.assertTrue(by["N01815"][5].startswith("ビット・軸型"), by["N01815"][5])
        self.assertEqual(by["N01815"][6], "×（書けません）")
        self.assertEqual(by["N00020"][6], "○")
        self.assertFalse(d.b_write.isEnabled())
        self.assertIn("ビット・軸型", d.lbl_why.text())
        self.assertNotIn("bit/A", d.lbl_why.text())
        self.assertIn("同じ値を書く確認", d.lbl_why.text())
        self.assertEqual(d.cmb_verify.count(), 1)
        self.assertTrue(d.b_verify.isEnabled())
        d.start_write()                                         # 押せなくても、呼ばれても書かない
        self.wait(d)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)

    def test_same_value_check_enables_the_type(self):
        d = self.dialog()
        self.go(d)
        before = self.machine_value(1815, "A4")
        d.b_verify.click()
        self.wait(d)
        self.assertEqual(self.machine_value(1815, "A4"), before)      # 値は変わっていない
        self.assertEqual(self.client.machine.writes, [(1815, "A4", before)])
        self.assertIn("確認済み", d.txt_result.toPlainText())
        self.assertIn("bit/A", self.settings["focas_verified_kinds"]["31i"])
        by = {r[0]: r for r in self.rows(d)}
        self.assertEqual(by["N01815"][6], "○")
        self.assertTrue(d.b_write.isEnabled())
        self.assertEqual(d.cmb_verify.count(), 0)
        d.b_write.click()
        self.wait(d)
        self.assertEqual(self.machine_value(1815, "A4"), "00100000")
        self.assertEqual(self.machine_value(20), "4")

    def test_same_value_check_that_fails_does_not_enable_the_type(self):
        d = self.dialog()
        self.go(d)
        self.client.lib.sc["write_store"] = {1815: "11111111"}      # 別の値が入ってしまう機械
        d.b_verify.click()
        self.wait(d)
        self.assertIn("通りませんでした", d.txt_result.toPlainText())
        self.assertNotIn("bit/A", self.settings["focas_verified_kinds"]["31i"])
        self.assertFalse(d.b_write.isEnabled())

    def test_verify_button_needs_a_writable_machine(self):
        d = self.dialog()
        self.go(d)
        self.client.machine.status["run"] = 3
        self.poll(d)
        self.assertFalse(d.b_verify.isEnabled())


class TestStopAndUndo(WriteDialogBase):
    VALUES = {"20": "4", "21": "1", "22": "1"}

    def test_stop_midway_shows_where_and_undo_restores(self):
        d = self.dialog()
        self.go(d)
        self.client.lib.sc["write_rc_at"] = {"3": 13}               # 3件目を機械が受け付けない
        d.b_write.click()
        self.wait(d)
        res = d.txt_result.toPlainText()
        self.assertIn("途中で止まりました", res)
        self.assertIn("N00022", res)
        self.assertIn("2 件が「書けた・読み戻しOK」", res)
        self.assertEqual([r[7].split("：")[0] for r in self.rows(d)],
                         ["書けた・読み戻しOK", "書けた・読み戻しOK", "書けなかった"])
        self.assertIn("2 件", d.b_undo.text())
        self.assertTrue(d.b_undo.isEnabled())
        self.client.lib.sc["write_rc_at"] = {}
        self.client.machine.writes.clear()
        d.b_undo.click()
        self.wait(d)
        self.assertEqual(self.client.machine.writes, [(21, "", "0"), (20, "", "9")])   # 逆順
        self.assertEqual(self.machine_value(20), "9")
        self.assertEqual(self.machine_value(21), "0")
        self.assertIn("元に戻す", d.txt_result.toPlainText())
        self.assertEqual([r[7] for r in self.rows(d)][:2], ["元に戻した", "元に戻した"])
        self.assertFalse(d.b_undo.isEnabled())

    def test_undo_is_a_button_and_nothing_is_written_automatically(self):
        d = self.dialog()
        self.go(d)
        self.client.lib.sc["write_rc_at"] = {"2": 13}
        d.b_write.click()
        self.wait(d)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 2)    # 書いた1件＋断られた1件。戻しは自動でしない
        self.assertEqual(self.machine_value(20), "4")

    def test_a_disallowed_alarm_after_a_write_stops_and_is_shown(self):
        d = self.dialog()
        self.go(d)
        self.client.lib.sc["write_alarms"] = {"20": [[401, 6, 1, "SERVO ALARM"]]}
        d.b_write.click()
        self.wait(d)
        res = d.txt_result.toPlainText()
        self.assertIn("許していないアラーム", res)
        self.assertIn("SV0401", res)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 1)

    def test_pw0000_is_shown_with_a_power_cycle_notice_and_no_waiting(self):
        d = self.dialog()
        self.go(d)
        self.client.lib.sc["write_alarms"] = {"20": [[0, 1, 0, "POWER MUST BE OFF"]]}
        d.b_write.click()
        self.wait(d)
        res = d.txt_result.toPlainText()
        self.assertIn("PW0000", res)
        self.assertIn("電源を入れ直してください", res)
        self.assertIn("書込が合っているかどうかとは別", res)       # アラームを書込の失敗とは言わない
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 3)    # 3件とも書いた（途中の PW0000 は許す）
        self.assertFalse(d._timer.isActive() and d._busy)


class TestUnknown(WriteDialogBase):
    def test_timeout_is_unknown_and_reconnect_button_decides_by_reading_back(self):
        from nd287_app import focas_client as C

        class Flaky(DirectClient):
            def call(self, cmd, args=None, timeout=None, on_event=None):
                if cmd == "param_write":
                    raise C._from_dict({"kind": "timeout", "seconds": 5, "cmd": cmd})
                return super().call(cmd, args, timeout, on_event)

        self.client = Flaky(copy.deepcopy(self.SCENARIO))
        d = self.dialog(values={"20": "4"})
        self.go(d)
        d.b_write.click()
        self.wait(d)
        res = d.txt_result.toPlainText()
        self.assertIn("不明", res)
        self.assertFalse(d.b_resolve.isHidden())
        self.assertTrue(d.b_resolve.isEnabled())
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)     # 自動でやり直さない
        d.b_resolve.click()
        self.wait(d)
        self.assertIn("書けていませんでした", d.txt_result.toPlainText())
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)


class TestEstopBanner(WriteDialogBase):
    VALUES = {"20": "4"}

    def test_emergency_stop_is_announced_first_after_connecting(self):
        d = self.dialog()
        self.client.machine.status.update({"emergency": 1})
        self.go(d, backup=False)
        self.assertFalse(d.lbl_estop.isHidden())
        self.assertIn("非常停止", d.lbl_estop.text())
        self.assertIn("解除", d.lbl_estop.text())
        self.client.machine.status.update({"emergency": 0})
        self.poll(d)
        self.assertTrue(d.lbl_estop.isHidden())

    def test_no_banner_when_machine_is_fine(self):
        d = self.dialog()
        self.go(d, backup=False)
        self.assertTrue(d.lbl_estop.isHidden())


class TestKeepMotor(WriteDialogBase):
    VALUES = {"20": "4"}

    def test_checkbox_passes_keep_motor_and_says_so(self):
        d = self.dialog()
        self.go(d)
        self.assertFalse(d.chk_keep_motor.isChecked())
        d.chk_keep_motor.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.keep_motor_calls[-1])
        self.assertIn("書きません", d.lbl_motor.text())
        self.assertFalse(d.lbl_motor.isHidden())
        d.chk_keep_motor.setChecked(False)
        self.app.processEvents()
        self.assertNotIn("書きません", d.lbl_motor.text())


class TestMotorIdCheck(WriteDialogBase):
    VALUES = {"20": "4"}

    def test_warns_when_typed_motor_id_differs_from_machine(self):
        d = self.dialog(keep_motor=True, motor_id="99999", product_axes=(4,))
        self.go(d)
        d._raw = "N02020Q1A1P1A2P2A3P3A4P299A5P5\n"          # この機械の 2020（A4=299）
        d._show_motor_note()
        self.assertIn("99999", d.lbl_motor.text())
        self.assertIn("違います", d.lbl_motor.text())


class TestExpectUnitAndAutoBackup(WriteDialogBase):
    VALUES = {"20": "4"}

    def test_auto_backup_after_connect(self):
        d = self.dialog(auto_backup=True)
        d.b_connect.click()
        self.wait(d)
        self.wait(d)
        self.assertIsNotNone(d._backup)               # 押さなくても控えを取った

    def test_no_auto_backup_by_default(self):
        d = self.dialog()
        d.b_connect.click()
        self.wait(d)
        self.assertIsNone(d._backup)

    def test_unit_mismatch_blocks_writing(self):
        d = self.dialog(expect_unit="27")
        self.go(d)
        d.cmb_unit.addItem("号機23", "23")
        d.cmb_unit.setCurrentIndex(d.cmb_unit.findData("23"))
        ok, reasons = d._writable()
        self.assertFalse(ok)
        self.assertTrue(any("号機が違います" in r for r in reasons), reasons)
        d.cmb_unit.setCurrentIndex(d.cmb_unit.findData("23"))
        d._expect_unit = "23"
        self.assertEqual(d._unit_mismatch(), "")

    def test_axis_is_decided_from_motor_id_on_machine(self):
        d = self.dialog(keep_motor=True, motor_id="299", product_axes=(4,))
        self.go(d, backup=False)
        raw = "N02020Q1A1P1A2P2A3P3A4P7A5P299\n"
        self.assertEqual(d._axis_from_motor_id(raw), 5)
        self.assertIsNone(d._axis_from_motor_id("N02020Q1A1P299A2P299\n"))   # 2軸一致なら決めない
        self.assertIsNone(d._axis_from_motor_id("N02020Q1A1P1\n"))           # 一致なし


class TestFirstContactRegistersExpectedUnit(WriteDialogBase):
    VALUES = {"20": "4"}

    def test_unknown_machine_gets_the_unit_chosen_in_the_wizard(self):
        from nd287_app import focas_find
        d = self.dialog(expect_unit="23", auto=True)
        d.b_connect.click()
        self.wait(d)
        cands = [d.cmb_unit.itemData(i) for i in range(d.cmb_unit.count())]
        if "23" not in cands:
            self.skipTest("偽機械の CNC ID では号機23が候補に出ない")
        self.assertEqual(d.cmb_unit.currentData(), "23")
        self.assertIn("台帳に残しました", d.lbl_target.text())


class TestZeroScope(WriteDialogBase):
    VALUES = {"20": "4"}

    def test_checkbox_switches_between_product_axis_only_and_all_axes(self):
        self.client.machine.set_param(1850, "A1", "11")
        self.client.machine.set_param(1850, "A4", "44")
        self.client.machine.set_param(1850, "A6", "66")
        self.settings["focas_verified_kinds"] = {"31i": ["byte/none", "dword/A"]}
        d = self.dialog()
        self.go(d)
        self.assertFalse(d.chk_zero_all.isChecked())                   # 既定は製品の軸だけ
        zeros = [r for r in self.rows(d) if r[0] == "N01850"]
        self.assertEqual([(r[1], r[3], r[4]) for r in zeros], [("A4", "44", "0")])
        self.assertEqual(zeros[0][2], "0にする")
        d.chk_zero_all.setChecked(True)
        self.app.processEvents()
        zeros = [r for r in self.rows(d) if r[0] == "N01850"]
        self.assertEqual([r[1] for r in zeros], ["A1", "A4", "A6"])
        self.assertEqual(self.plan_calls[-1][1], "all")
        d.chk_zero_all.setChecked(False)
        self.assertEqual(self.plan_calls[-1][1], "product")


class TestClose(WriteDialogBase):
    def test_closing_disconnects_and_closes_the_client(self):
        closed = []
        self.client.close = lambda: closed.append(1)
        d = self.dialog()
        self.go(d)
        d.close()
        self.assertEqual(closed, [1])
        self.assertIsNone(d.client)
        self.assertFalse(d._timer.isActive())

    def test_a_read_failure_of_the_poll_goes_away_but_a_user_failure_stays(self):
        from nd287_app import focas_client as C
        d = self.dialog()
        self.go(d)
        orig = self.client.call
        state = {"fail": True}

        def flaky(cmd, args=None, **kw):
            if cmd == "status" and state["fail"]:
                state["fail"] = False
                raise C.FocasError("focas", "機械が cnc_statinfo を受け付けませんでした（戻り値 5）。",
                                   {"kind": "focas", "rc": 5})
            return orig(cmd, args, **kw)
        self.client.call = flaky
        self.poll(d)
        self.assertIn("受け付けませんでした", d.lbl_state.text())
        self.poll(d)
        self.assertIn("つながっています", d.lbl_state.text())          # 読み直しで戻ったら消える


class TestWithRealClientAndFakeHelper(unittest.TestCase):
    """子プロセスの偽物ヘルパーを通して、画面が最後まで動くこと（実機にはつながない）。"""

    @classmethod
    def setUpClass(cls):
        WriteDialogBase.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        WriteDialogBase.tearDownClass.__func__(cls)

    def test_full_flow_through_the_helper_process(self):
        import json
        from PySide6 import QtWidgets
        from nd287_app import param_build as P
        from nd287_app.settings import DEFAULTS
        import nd287_app.gui as G
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        work = Path(tempfile.mkdtemp(dir=self._tmp))
        st = copy.deepcopy(DEFAULTS)
        st.update(focas_python32=sys.executable, focas_helper_path=str(HERE / "fake_focas_helper.py"),
                  focas_host="127.0.0.1", focas_backup_dir=str(work / "控え"),
                  focas_write_log_csv=str(work / "log.csv"),
                  controller_master_csv=str(REPO / "マスタ" / "制御装置マスタ.csv"))
        old = os.environ.get("FAKE_FOCAS_SCENARIO")
        os.environ["FAKE_FOCAS_SCENARIO"] = json.dumps({"alarms": [PWE]})
        try:
            d = G.FocasWriteDialog(
                None, st, lambda raw, zero_scope="product": P.prepare(
                    raw, {"20": "4"}, axis=4, reference="", zero_scope=zero_scope))
            d._timer.setInterval(600000)
            d._warn = d._info = lambda *a: None
            d.show()

            def wait():
                t0 = time.monotonic()
                while d._busy and time.monotonic() - t0 < 60:
                    app.processEvents()
                    time.sleep(0.01)
                app.processEvents()
            d.b_connect.click()
            wait()
            d.b_backup.click()
            wait()
            self.assertTrue(d.b_write.isEnabled(), d.lbl_why.text())
            d.b_write.click()
            wait()
            self.assertIn("1 件が「書けた・読み戻しOK」", d.txt_result.toPlainText())
            self.assertIn("すべて合っています", d.txt_result.toPlainText())
            d.close()
            d.deleteLater()
        finally:
            if old is None:
                os.environ.pop("FAKE_FOCAS_SCENARIO", None)
            else:
                os.environ["FAKE_FOCAS_SCENARIO"] = old


class TestEntryFromParamDialog(unittest.TestCase):
    """パラメータ作成画面（ParamDialog）の「機械へ直接書く…」。prepare を通る・土台は機械の控え。"""

    @classmethod
    def setUpClass(cls):
        WriteDialogBase.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        WriteDialogBase.tearDownClass.__func__(cls)

    def setUp(self):
        from PySide6 import QtWidgets
        from nd287_app.settings import DEFAULTS
        import nd287_app.gui as G
        from nd287_app import nc_param
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.G = G
        self.work = Path(tempfile.mkdtemp(dir=self._tmp))
        self.basic = self.work / "F23BASIC.prm"
        raw = (HERE / "fixtures" / "focas_param_31i.DAT").read_bytes().decode("cp932")
        self.basic.write_bytes(raw.replace("N00020Q1P9 ", "N00020Q1P1 ").encode("cp932"))
        self.d = G.ParamDialog(None, copy.deepcopy(DEFAULTS))
        self.d._check_basic = lambda p: True
        self.d.e_master_prm.setText(str(self.basic))
        self.d.e_seiban.setText("T9999")
        self.d.e_product.setText("")
        self.d._changes = [nc_param.ParamChange("20", "", "4")]
        self.opened = []
        self._orig_exec = G.FocasWriteDialog.exec
        G.FocasWriteDialog.exec = lambda s_: self.opened.append(s_) or 0

    def tearDown(self):
        self.G.FocasWriteDialog.exec = self._orig_exec
        for w in self.opened:
            w.deleteLater()
        self.d.deleteLater()

    def test_button_exists(self):
        texts = [b.text() for b in self.d.findChildren(self.G.QtWidgets.QPushButton)]
        self.assertIn("機械へ直接書く…", texts)

    def test_opens_the_write_dialog_with_a_plan_function_through_prepare(self):
        self.d.write_to_machine()
        self.assertEqual(len(self.opened), 1)
        dlg = self.opened[0]
        self.assertEqual(dlg.seiban, "T9999")
        # 土台を機械の控えに替えて呼ぶ。BASIC には無い番号（N09802）があっても警告しない
        foc = (HERE / "fixtures" / "focas_param_31i.DAT").read_bytes().decode("cp932")
        plan = dlg.plan_fn(foc)
        self.assertEqual(plan["fmt"], "fanuc")
        self.assertEqual([r[0] for r in plan["rows"] if r[5]], ["20"])
        self.assertEqual([x for x in plan["problems"] if "実機のバックアップに無い" in x], [])
        # 既定は、製品の軸だけ 0 にする
        foc2 = foc.replace("N01850Q1A1P0A2P0A3P0A4P0A5P0A6P0", "N01850Q1A1P11A2P22A3P33A4P44A5P55A6P66")
        zero_rows = lambda p: [r for r in p["rows"] if r[4] == "0にする"]    # noqa: E731
        self.assertEqual(len(zero_rows(dlg.plan_fn(foc2))), 1)
        self.assertEqual(len(zero_rows(dlg.plan_fn(foc2, zero_scope="all"))), 6)

    def test_other_create_paths_are_unchanged(self):
        # 今までの _plan は BASIC を参照し、0にするのは全軸のまま
        from nd287_app import param_build as P
        raw = self.basic.read_bytes().decode("cp932").replace(
            "N01850Q1A1P0A2P0A3P0A4P0A5P0A6P0", "N01850Q1A1P11A2P22A3P33A4P44A5P55A6P66")
        p = self.d._plan(raw, values={"20": "4"}, axis=4, seiban="T", master_path=str(self.basic))
        self.assertEqual(len([r for r in p["rows"] if r[4] == P.KIND_ZERO]), 6)
        self.assertEqual({r[1] for r in p["rows"] if r[4] == P.KIND_ZERO}, {"全軸"})

    def test_no_basic_is_explained(self):
        warned = []
        orig = self.G.QtWidgets.QMessageBox.warning
        self.G.QtWidgets.QMessageBox.warning = lambda *a, **k: warned.append(a[2])
        try:
            self.d.e_master_prm.setText("")
            self.d.write_to_machine()
        finally:
            self.G.QtWidgets.QMessageBox.warning = orig
        self.assertEqual(self.opened, [])
        self.assertTrue(warned)

    def test_dialog_is_not_created_without_pressing_the_button(self):
        self.assertEqual(self.opened, [])


class TestFocasDialogPollMessage(unittest.TestCase):
    """FocasDialog: 利用者が押した操作の失敗の文は、読み直しが成功しても次の操作まで残す。"""

    @classmethod
    def setUpClass(cls):
        WriteDialogBase.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        WriteDialogBase.tearDownClass.__func__(cls)

    def test_user_failure_text_stays_until_the_next_operation(self):
        from PySide6 import QtWidgets
        from nd287_app import focas_client as C
        from nd287_app.settings import DEFAULTS
        import nd287_app.gui as G
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        work = Path(tempfile.mkdtemp(dir=self._tmp))
        st = copy.deepcopy(DEFAULTS)
        st.update(focas_host="127.0.0.1", focas_backup_dir=str(work / "控え"),
                  controller_master_csv=str(REPO / "マスタ" / "制御装置マスタ.csv"))
        client = DirectClient({"alarms": [PWE]})
        d = G.FocasDialog(None, st)
        d.client = client
        client.on_log = None
        d._ensure_client = lambda: client
        d._timer.setInterval(600000)
        d._warn = d._info = lambda *a: None
        d.show()

        def wait():
            t0 = time.monotonic()
            while d._busy and time.monotonic() - t0 < 30:
                app.processEvents()
                time.sleep(0.01)
            app.processEvents()
        d.b_connect.click()
        wait()
        self.assertTrue(d._connected)
        # 利用者が押した操作（自己診断）が失敗する
        orig = client.call

        def failing(cmd, args=None, **kw):
            if cmd == "prog_info":
                raise C.FocasError("focas", "機械が cnc_rdproginfo を受け付けませんでした（戻り値 5）。",
                                   {"kind": "focas", "rc": 5})
            return orig(cmd, args, **kw)
        client.call = failing
        d.b_diag.click()
        wait()
        self.assertIn("受け付けませんでした", d.lbl_state.text())
        self.assertIn("b91c1c", d.lbl_state.styleSheet())
        # そのあと読み直し（2秒ごと）が成功しても、赤い文は消さない
        for _ in range(3):
            d._poll()
            wait()
        self.assertIn("受け付けませんでした", d.lbl_state.text())
        self.assertIn("b91c1c", d.lbl_state.styleSheet())
        # 次の操作を始めたら消える
        client.call = orig
        d.b_diag.click()
        wait()
        self.assertNotIn("受け付けませんでした", d.lbl_state.text())
        d.close()
        d.deleteLater()


class TestFirstUnitButton(WriteDialogBase):
    def setUp(self):
        super().setUp()
        self.settings["controller_master_csv"] = str(self.work / "マスタ.csv")     # 号機の候補が空になる
        self.settings["param_basic_dir"] = str(self.work / "data" / "BASIC")
        self.settings["focas_registry_csv"] = str(self.work / "台帳.csv")
        (self.work / "data" / "BASIC").mkdir(parents=True)

    def test_button_shown_only_when_unknown_and_backup_complete(self):
        d = self.dialog()
        self.go(d, backup=False)
        self.assertFalse(d.box_first.isVisible())          # 控えがまだ
        d.b_backup.click()
        self.wait(d)
        self.assertTrue(d._backup[1]["complete"])
        self.assertTrue(d.box_first.isVisible())

    def test_button_hidden_when_registry_knows_machine(self):
        d = self.dialog()
        self.go(d)
        d._reg_unit = "7"
        d._refresh_enabled()
        self.assertFalse(d.box_first.isVisible())

    def test_register_creates_file_elsewhere(self):
        d = self.dialog()
        self.go(d)
        d.e_first_unit.setText("31")
        d.b_first_unit.click()
        self.assertTrue((self.work / "data" / "機械から取ったBASIC" / "F31BASIC.dat").is_file())
        self.assertEqual(list((self.work / "data" / "BASIC").iterdir()), [])
        self.assertFalse(d.box_first.isVisible())
        self.assertEqual(d.cmb_unit.currentData(), "31")


if __name__ == "__main__":
    unittest.main()
