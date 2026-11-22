# -*- coding: utf-8 -*-
"""段4・段5のレビューで見つかった問題の直し（設計 §16「段4・段5のレビューで直した点」）の試験。

偽物の機械だけ。実機にはつながない。本物の DLL も 32bit Python も呼ばない。
A-1（PW0000 のあとで元に戻す）・B-1〜B-10・C。
"""

import copy
import inspect
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import focas_client as C            # noqa: E402
from nd287_app import focas_helper as H            # noqa: E402
from nd287_app import focas_ops as O               # noqa: E402
from nd287_app import param_build as P             # noqa: E402
import fake_fwlib                                  # noqa: E402
from fake_client import DirectClient               # noqa: E402
from test_focas_ops_write import WriteBase, ALL_KINDS, CNC_ID, PWE   # noqa: E402
from test_focas_write_dialog import WriteDialogBase               # noqa: E402

PW_ALARM = {"20": [[0, 1, 0, "POWER MUST BE OFF"]]}


# ---------------------------------------------------------------------------
# A-1 PW0000 が出ていても元に戻せる
# ---------------------------------------------------------------------------

class TestUndoWithPowerAlarm(WriteBase):
    def written_with_pw0000(self):
        _p, pl = self.plan({"20": "4", "21": "1", "22": "1"})
        self.client.lib.sc["write_alarms"] = PW_ALARM
        self.client.lib.sc["write_rc_at"] = {"3": 13}              # 3件目で止まる
        res = self.run_write(pl["items"])
        self.client.lib.sc["write_rc_at"] = {}
        self.assertTrue(res.power_cycle)
        self.assertEqual(len(res.written()), 2)
        return res

    def undo(self, res, **kw):
        return O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                          settings=self.settings, family="31i", log_path=self.log, **kw)

    def test_undo_is_allowed_while_pw0000_is_showing(self):
        res = self.written_with_pw0000()
        self.assertIn("PW0000", [a["code"] for a in self.client.call("alarms")["alarms"]])
        out = self.undo(res)                                       # 入口の確認で断られない
        self.assertEqual(out.state, "done")
        self.assertEqual([i["status"] for i in out.items], ["ok", "ok"])
        self.assertEqual((self.machine_value(20), self.machine_value(21)), ("9", "0"))

    def test_undo_still_says_to_power_cycle(self):
        out = self.undo(self.written_with_pw0000())
        self.assertTrue(out.power_cycle)
        self.assertIn("電源を入れ直してください", "\n".join(O.describe_result(out, undo=True)))

    def test_undo_is_still_refused_for_another_alarm(self):
        res = self.written_with_pw0000()
        self.m.alarms.append((401, 6, 1, "SERVO ALARM"))
        with self.assertRaises(O.WriteRefused) as cm:
            self.undo(res)
        self.assertIn("SV0401", "\n".join(cm.exception.reasons))
        self.assertEqual(self.machine_value(20), "4")              # 何も戻していない

    def test_the_job_file_restores_a_result_that_can_be_undone(self):
        _p, pl = self.plan({"20": "4", "21": "1", "22": "1"})
        self.client.lib.sc["write_alarms"] = PW_ALARM
        self.client.lib.sc["write_rc_at"] = {"3": 13}              # 3件目で止まる（途中のジョブ）
        self.run_write(pl["items"], job_path=self.folder / O.JOB_NAME)
        root = self.folder.parent
        res = O.find_pending_job(root, CNC_ID)                     # 画面の結果が無くても探せる
        self.assertIsNotNone(res)
        self.assertEqual(len(res.written()), 2)
        self.assertEqual(Path(res.folder), self.folder)
        out = self.undo(res)
        self.assertEqual(out.state, "done")
        self.assertEqual((self.machine_value(20), self.machine_value(21)), ("9", "0"))

    def test_a_job_that_was_already_undone_or_of_another_machine_is_not_offered(self):
        _p, pl = self.plan({"20": "4"})
        res = self.run_write(pl["items"], job_path=self.folder / O.JOB_NAME)
        root = self.folder.parent
        self.assertIsNone(O.find_pending_job(root, "FFFF-0-0-0"))
        self.assertIsNone(O.find_pending_job(root, None))
        self.assertIsNone(O.find_pending_job(root / "無い", CNC_ID))
        self.undo(res, job_path=self.folder / ("元に戻す_" + O.JOB_NAME))
        self.assertIsNone(O.find_pending_job(root, CNC_ID))

    def test_a_job_with_nothing_written_is_not_offered(self):
        _p, pl = self.plan({"20": "4"})
        self.m.set_param(20, "", "7")                              # 書く前の確認で止まる
        self.run_write(pl["items"], job_path=self.folder / O.JOB_NAME)
        self.assertIsNone(O.find_pending_job(self.folder.parent, CNC_ID))

    def test_a_broken_job_file_is_ignored(self):
        (self.folder / O.JOB_NAME).write_text("{こわれた", encoding="utf-8")
        self.assertIsNone(O.load_job(self.folder / O.JOB_NAME))
        self.assertIsNone(O.find_pending_job(self.folder.parent, CNC_ID))


class TestUndoDialog(WriteDialogBase):
    VALUES = {"20": "4", "21": "1", "22": "1"}

    def write_with_pw0000(self, d):
        self.client.lib.sc["write_alarms"] = PW_ALARM
        self.client.lib.sc["write_rc_at"] = {"3": 13}
        d.b_write.click()
        self.wait(d)
        self.client.lib.sc["write_rc_at"] = {}
        self.poll(d)

    def test_undo_button_works_with_pw0000_showing(self):
        d = self.dialog()
        self.go(d)
        self.write_with_pw0000(d)
        self.assertTrue(d.b_undo.isEnabled())
        d.b_undo.click()
        self.wait(d)
        self.assertEqual((self.machine_value(20), self.machine_value(21)), ("9", "0"))
        self.assertIn("元に戻す", d.txt_result.toPlainText())
        self.assertNotIn("断", d.txt_result.toPlainText().split("\n")[0])

    def test_retaking_the_backup_keeps_the_result_needed_for_undo(self):
        d = self.dialog()
        self.go(d)
        self.write_with_pw0000(d)
        res = d._result
        d.b_backup.click()                                         # 控えを取り直す（PW0000 は出たまま）
        self.wait(d)
        self.assertIs(d._result, res)
        self.assertTrue(d.b_undo.isEnabled())
        self.assertIn("機械に書いたまま", d.txt_result.toPlainText())
        d.b_undo.click()
        self.wait(d)
        self.assertEqual(self.machine_value(20), "9")

    def test_a_new_dialog_finds_the_job_file_and_can_undo(self):
        d = self.dialog()
        self.go(d)
        self.write_with_pw0000(d)
        d.close()
        d2 = self.dialog()                                         # 同じ設定（同じ控えの置き場）
        self.go(d2)
        self.assertTrue(d2.b_undo.isEnabled())
        self.assertIn("機械に書いたまま", d2.txt_result.toPlainText())
        d2.b_undo.click()
        self.wait(d2)
        self.assertEqual(self.machine_value(20), "9")
        self.assertEqual(self.machine_value(21), "0")
        d3 = self.dialog()                                         # 戻したあとは、もう出ない
        self.go(d3)
        self.assertFalse(d3.b_undo.isEnabled())

    def test_refused_undo_says_the_written_values_remain(self):
        d = self.dialog()
        self.go(d)
        self.write_with_pw0000(d)
        self.client.machine.status["run"] = 3                      # 運転中になって断られる
        d.b_undo.click()
        self.wait(d)
        text = d.txt_result.toPlainText()
        self.assertNotIn("何も書いていません", text)
        self.assertIn("書いた値が残っています", text)
        self.assertIn("運転中", text)
        self.assertEqual(self.machine_value(20), "4")

    def test_write_refusal_still_says_nothing_was_written(self):
        d = self.dialog()
        self.go(d)
        d._write_failed(O.WriteRefused(["運転中です"]))
        self.assertIn("何も書いていません", d.txt_result.toPlainText())


# ---------------------------------------------------------------------------
# B-1 書込の最中に窓を閉じる
# ---------------------------------------------------------------------------

class SlowClient(DirectClient):
    def call(self, cmd, args=None, timeout=None, on_event=None):
        if cmd == "param_write":
            time.sleep(0.25)
        return super().call(cmd, args, timeout, on_event)


class TestCloseWhileWriting(WriteDialogBase):
    VALUES = {"20": "4", "21": "1", "22": "1", "23": "1"}

    def start(self, answer_close=True, after_stop_close=False):
        self.client = SlowClient(copy.deepcopy(self.SCENARIO))
        d = self.dialog()
        self.go(d)
        d.asked = []
        d._confirm_close = lambda: (d.asked.append("close") or answer_close)
        d._ask_close_after_stop = lambda text: (d.asked.append(text) or after_stop_close)
        d.b_write.click()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.4:
            self.app.processEvents()
            time.sleep(0.01)
        return d

    def test_closing_in_the_middle_asks_first(self):
        d = self.start(answer_close=False)
        d.reject()                                                 # Esc
        self.assertEqual(d.asked, ["close"])
        self.assertTrue(d.isVisible())
        self.wait(d)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 4)  # 「いいえ」なら最後まで書く
        self.assertEqual(self.machine_value(23), "1")

    def test_the_x_button_asks_too(self):
        d = self.start(answer_close=False)
        d.close()
        self.assertEqual(d.asked, ["close"])
        self.assertTrue(d.isVisible())
        self.wait(d)

    def test_yes_stops_after_the_current_item_and_shows_the_result_and_undo(self):
        d = self.start(answer_close=True, after_stop_close=False)
        d.reject()
        self.assertTrue(d.isVisible())                             # すぐには閉じない（半分書いて黙って閉じない）
        self.wait(d)
        n = self.client.lib.called("cnc_wrparam")
        self.assertTrue(1 <= n < 4, n)                             # 1件が終わったら止まる
        self.assertEqual(d._result.state, "cancelled")
        self.assertEqual(len(d._result.written()), n)
        self.assertTrue(d.isVisible())
        self.assertTrue(d.b_undo.isEnabled())
        self.assertIn("元に戻す", d.asked[-1])
        self.assertIn(str(n), d.asked[-1])
        self.assertIn("書いていない番号", d.txt_result.toPlainText())
        d.b_undo.click()
        self.wait(d)
        self.assertEqual((self.machine_value(20), self.machine_value(21)), ("9", "0"))

    def test_after_stopping_the_person_can_choose_to_close(self):
        d = self.start(answer_close=True, after_stop_close=True)
        d.reject()
        self.wait(d)
        for _ in range(5):
            self.app.processEvents()
        self.assertFalse(d.isVisible())
        self.assertIsNone(d.client)

    def test_closing_when_idle_does_not_ask(self):
        d = self.dialog()
        self.go(d)
        d.asked = []
        d._confirm_close = lambda: d.asked.append("x") or True
        d.reject()
        self.assertEqual(d.asked, [])
        self.assertFalse(d.isVisible())

    def test_closing_during_the_backup_does_not_ask(self):
        d = self.dialog()
        d.asked = []
        d._confirm_close = lambda: d.asked.append("x") or True
        d._busy, d._busy_quiet, d._mode = True, False, "backup"
        self.assertTrue(d._request_close())
        d._busy, d._mode = False, ""
        self.assertEqual(d.asked, [])


# ---------------------------------------------------------------------------
# B-2・B-3 同じ値を書く確認
# ---------------------------------------------------------------------------

class TestVerifyKind(WriteBase):
    def item(self, kinds=()):
        _p, pl = self.plan({"20": "4", "1815": "00110000"}, kinds=list(kinds))
        return next(i for i in pl["items"] if i["no"] == 1815)

    def verify(self, it):
        return O.verify_kind(self.client, it, cnc_id=CNC_ID, backup_info=self.info,
                             settings=self.settings, family="31i", log_path=self.log)

    def test_it_compares_the_value_with_the_backup_before_writing(self):         # B-2
        it = self.item()
        self.m.set_param(1815, "A4", "11111111")                  # 控えのあとで人が変えた
        out = self.verify(it)
        self.assertFalse(out["ok"])
        self.assertEqual(out["result"], "stale")
        self.assertEqual(self.wr_calls(), 0)                      # 書かずに止める
        self.assertIn("控えの値", out["note"])
        self.assertFalse(out["changed"])

    def test_it_writes_when_the_value_is_the_same_as_the_backup(self):
        it = self.item()
        out = self.verify(it)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.wr_calls(), 1)
        self.assertFalse(out["changed"])

    def test_a_changed_readback_is_reported_with_the_original_value(self):       # B-3
        it = self.item()
        self.client.lib.sc["write_store"] = {"1815": "11111111"}
        out = self.verify(it)
        self.assertFalse(out["ok"])
        self.assertEqual(out["result"], "mismatch")
        self.assertTrue(out["changed"])
        self.assertEqual(out["original"], it["old"])
        self.assertEqual(out["now"], "11111111")
        self.assertEqual(self.client.call("param_read", {"no": 1815, "label": "A4"})["value"], "11111111")

    def test_that_one_item_can_be_undone(self):
        it = self.item()
        self.client.lib.sc["write_store"] = {"1815": "11111111"}
        out = self.verify(it)
        self.client.lib.sc["write_store"] = {}
        res = O.verify_undo_result(out, CNC_ID)
        back = O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info, settings=self.settings,
                          family="31i", verified_extra=[out["key"]])    # まだ確認済みでない型でも戻せる
        self.assertEqual(back.state, "done")
        self.assertEqual(self.client.call("param_read", {"no": 1815, "label": "A4"})["value"], it["old"])


class TestVerifyDialog(WriteDialogBase):
    VALUES = {"20": "4", "1815": "00110000"}

    def test_a_changed_value_is_shown_big_with_a_button_to_undo_that_one(self):
        d = self.dialog()
        self.go(d)
        self.assertTrue(d.lbl_alert.isHidden())
        self.assertTrue(d.b_undo_one.isHidden())
        old = self.machine_value(1815, "A4")
        self.client.lib.sc["write_store"] = {"1815": "11111111"}
        d.b_verify.click()
        self.wait(d)
        self.assertFalse(d.lbl_alert.isHidden())
        self.assertIn("機械の値が変わりました", d.lbl_alert.text())
        self.assertIn(old, d.lbl_alert.text())
        self.assertIn("11111111", d.lbl_alert.text())
        self.assertFalse(d.b_undo_one.isHidden())
        self.assertTrue(d.b_undo_one.isEnabled())
        self.assertNotIn("bit/A", d.txt_result.toPlainText())      # 型の名前は日本語
        self.client.lib.sc["write_store"] = {}
        d.b_undo_one.click()
        self.wait(d)
        self.assertEqual(self.machine_value(1815, "A4"), old)
        self.assertTrue(d.lbl_alert.isHidden())
        self.assertTrue(d.b_undo_one.isHidden())

    def test_a_stale_value_stops_without_writing(self):
        d = self.dialog()
        self.go(d)
        self.client.machine.set_param(1815, "A4", "11111111")
        d.b_verify.click()
        self.wait(d)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)
        self.assertIn("何も書いていません", d.txt_result.toPlainText())
        self.assertTrue(d.b_undo_one.isHidden())


# ---------------------------------------------------------------------------
# B-4 CNC ID が取れない機械には書かない
# ---------------------------------------------------------------------------

class NoIdLib(fake_fwlib.FakeFwlib):
    def _cnc_rdcncid(self, h, arr):
        return 6


class TestNoCncId(unittest.TestCase):
    def test_writable_state_needs_the_cnc_id(self):
        st = {"values": {"run": 0, "motion": 0, "emergency": 0},
              "text": {"run": "", "motion": "", "emergency": ""}, "interpreted": True}
        al = [{"code": "SW0100", "text": ""}]
        ok = O.writable_state(st, al, backup_info={"complete": True, "cnc_id": CNC_ID}, cnc_id=CNC_ID)
        self.assertTrue(ok["ok"], ok["reasons"])
        for cid, binfo in ((None, {"complete": True, "cnc_id": CNC_ID}),
                           (CNC_ID, {"complete": True, "cnc_id": None}),
                           ("", {"complete": True, "cnc_id": ""})):
            r = O.writable_state(st, al, backup_info=binfo, cnc_id=cid)
            self.assertFalse(r["ok"])
            self.assertIn("機械ごとの番号（CNC ID）", "\n".join(r["reasons"]))

    def test_nothing_is_written_to_a_machine_without_a_cnc_id(self):
        import tempfile
        from nd287_app import focas_backup as B
        from nd287_app.settings import DEFAULTS
        c = DirectClient(lib=NoIdLib(scenario={"alarms": [PWE]})).connect()
        self.assertFalse(c.call("identify").get("cnc_id"))
        tmp = Path(tempfile.mkdtemp())
        s = copy.deepcopy(DEFAULTS)
        res = B.take_backup(c, tmp / "k", s)
        raw = (res.folder / "パラメータ.DAT").read_bytes().decode("cp932")
        types = O._backup_values(res.folder)[1]
        p = P.prepare(raw, {"20": "4"}, axis=4, reference="", zero_params=())
        pl = O.plan_writes(raw, p, types, verified=["byte/none"])
        for cnc in (None, ""):
            with self.assertRaises(O.WriteRefused) as cm:
                O.run_write(c, pl["items"], cnc_id=cnc, backup_info=res.info, settings=s, family="31i")
            self.assertIn("機械ごとの番号（CNC ID）", "\n".join(cm.exception.reasons))
        self.assertEqual(c.lib.called("cnc_wrparam"), 0)
        self.assertEqual(c.call("param_read", {"no": 20, "label": ""})["value"], "9")


class TestNoCncIdDialog(WriteDialogBase):
    def test_the_reason_is_shown_and_the_buttons_stay_off(self):
        self.client = DirectClient(lib=NoIdLib(scenario={"alarms": [PWE]}))
        d = self.dialog()
        self.go(d)
        self.assertIn("機械ごとの番号（CNC ID）が読めない", d.lbl_state.text())
        self.assertFalse(d.b_write.isEnabled())
        self.assertIn("機械ごとの番号（CNC ID）", d.lbl_why.text())
        self.assertFalse(d.b_verify.isEnabled())
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)


# ---------------------------------------------------------------------------
# B-5 型コードのビットとラベルの食い違い
# ---------------------------------------------------------------------------

class TestLabelTypeMismatch(WriteBase):
    def test_problem_function(self):
        for code, label in ((0x11, ""), (0x14, "A4"), (0x47, "A1"), (0x145, "S1"), (0x1007, "A4"),
                            (0x11, "-"), (0x0, ""), (0x11, "L1"), (0x14, "T1")):
            self.assertIsNone(H.label_type_problem(code, label), (hex(code), label))
        for code, label in ((0x14, ""), (0x5, "S1"), (0x145, "A1"), (0x11, "A1"), (0x11, "S1"),
                            (0x147, "A4")):
            self.assertIn("食い違", H.label_type_problem(code, label), (hex(code), label))

    def test_the_helper_refuses_a_mismatched_pair_and_writes_nothing(self):
        for no, label in ((1020, ""), (1020, "S1"), (20, "A1"), (982, "A1"), (982, "")):
            with self.assertRaises(C.FocasError) as cm:
                self.client.call("param_write", {"no": no, "label": label, "value": "1",
                                                 "verified": ALL_KINDS + ["byte/none"]})
            self.assertEqual(cm.exception.kind, "refused")
            self.assertEqual(cm.exception.detail.get("reason"), "label_type_mismatch")
        self.assertEqual(self.wr_calls(), 0)

    def test_matching_pairs_are_still_written(self):
        out = self.client.call("param_write", {"no": 20, "label": "", "value": "4",
                                               "verified": ["byte/none"]})
        self.assertEqual(out["rc"], 0)
        out = self.client.call("param_write", {"no": 982, "label": "S1", "value": "1",
                                               "verified": ["byte/S"]})
        self.assertEqual(out["rc"], 0)

    def test_the_plan_blocks_a_mismatched_pair_before_any_write(self):
        p, _pl = self.plan({"20": "4", "21": "1"})
        types = dict(self.types)
        types[21] = 0x14                                           # 軸型の型コードなのにラベルなし
        pl = O.plan_writes(self.raw, p, types, verified=ALL_KINDS)
        bad = [i for i in pl["items"] if i["no"] == 21][0]
        self.assertTrue(any("食い違" in x for x in bad["problems"]))
        self.assertTrue(pl["blockers"])
        with self.assertRaises(O.WriteRefused):
            self.run_write(pl["items"])
        self.assertEqual(self.wr_calls(), 0)


# ---------------------------------------------------------------------------
# B-6 実数・桁数
# ---------------------------------------------------------------------------

class TestRealCheckedBeforeWriting(WriteBase):
    def test_an_unrepresentable_real_blocks_everything_before_the_first_write(self):
        p, pl = self.plan({"20": "4", "1420": "8000.0005"}, kinds=ALL_KINDS)
        real = [i for i in pl["items"] if i["no"] == 1420][0]
        self.assertTrue(real["problems"], real)
        self.assertIn("丸めて書くことはしません", "".join(real["problems"]))
        self.assertTrue(pl["blockers"])
        with self.assertRaises(O.WriteRefused) as cm:
            self.run_write(pl["items"])
        self.assertIn("N01420", "\n".join(cm.exception.reasons))
        self.assertEqual(self.wr_calls(), 0)                      # 20 番も書いていない
        self.assertEqual(self.machine_value(20), "9")

    def test_a_representable_real_passes_the_check(self):
        _p, pl = self.plan({"1420": "8000.0"}, kinds=ALL_KINDS)
        self.assertEqual(pl["blockers"], [])

    def test_negative_decimals_are_refused(self):
        with self.assertRaises(H.FocasError) as cm:
            H.encode_param_value("real", "10", -1)
        self.assertIn("負", cm.exception.detail["message"])
        self.assertEqual(H.encode_param_value("real", "10", 0), H.encode_param_value("real", "10", 0))
        self.assertEqual(H.real_decimals("8000.000"), 3)
        self.assertEqual(H.real_decimals("8000"), 0)


# ---------------------------------------------------------------------------
# B-7 書いたあとは全軸 0 を変えられない
# ---------------------------------------------------------------------------

class TestZeroAfterWrite(WriteDialogBase):
    VALUES = {"20": "4"}

    def test_checkbox_is_off_after_writing_and_the_plan_file_is_not_rewritten(self):
        self.client.machine.set_param(1850, "A1", "11")
        self.client.machine.set_param(1850, "A4", "44")
        self.settings["focas_verified_kinds"] = {"31i": ["byte/none", "word/none", "dword/A"]}
        d = self.dialog()
        self.go(d)
        self.assertTrue(d.chk_zero_all.isEnabled())
        d.b_write.click()
        self.wait(d)
        self.assertFalse(d.chk_zero_all.isEnabled())
        folder = d._backup[0]
        before = (Path(folder) / "書込後の予定.DAT").read_bytes()
        rows = self.rows(d)
        d.chk_zero_all.setChecked(True)                            # 押せなくても、呼ばれても変えない
        self.app.processEvents()
        self.assertFalse(d.chk_zero_all.isChecked())
        self.assertEqual((Path(folder) / "書込後の予定.DAT").read_bytes(), before)
        self.assertEqual(self.rows(d), rows)
        d.b_backup.click()                                         # 控えを取り直すと、また選べる
        self.wait(d)
        self.assertTrue(d.chk_zero_all.isEnabled())


# ---------------------------------------------------------------------------
# B-8 利用者に見える名前
# ---------------------------------------------------------------------------

class TestJapaneseNames(WriteBase):
    def test_kind_labels(self):
        self.assertEqual(H.kind_label("byte/none"), "バイト")
        self.assertEqual(H.kind_label("dword/A"), "2ワード・軸型")
        self.assertEqual(H.kind_label("bit/A"), "ビット・軸型")
        self.assertEqual(H.kind_label("byte/S"), "バイト・主軸型")
        self.assertEqual(H.kind_label("real/A"), "実数・軸型")
        self.assertEqual(H.kind_label("word/none"), "ワード")
        self.assertEqual(O.kind_label("byte/none"), "バイト")

    def test_unverified_type_messages_use_japanese(self):
        _p, pl = self.plan({"20": "4", "1815": "00110000"}, kinds=["byte/none"])
        text = "\n".join(pl["blockers"])
        self.assertIn("ビット・軸型", text)
        self.assertNotIn("bit/A", text)
        with self.assertRaises(C.FocasError) as cm:
            self.client.call("param_write", {"no": 1815, "label": "A4", "value": "00110000",
                                             "verified": []})
        self.assertIn("ビット・軸型", cm.exception.message)

    def test_unknown_bits_are_kept_on_the_row(self):
        _p, pl = self.plan({"20": "4"})
        self.assertEqual(pl["items"][0]["unknown_bits"], [0x10])   # 型コード 0x11
        _p, pl = self.plan({"1815": "00110000"}, kinds=ALL_KINDS)
        self.assertEqual(pl["items"][0]["unknown_bits"], [0x40])   # 型コード 0x44


class TestJapaneseNamesDialog(WriteDialogBase):
    VALUES = {"20": "4", "1815": "00110000"}

    def test_rows_say_the_type_in_japanese_and_flag_unknown_bits(self):
        d = self.dialog()
        self.go(d)
        by = {r[0]: r for r in self.rows(d)}
        self.assertIn("意味が分かっていない印あり", by["N00020"][5])
        self.assertIn("意味が分かっていない印", d.lbl_missing.text())
        self.assertIn("機械ごとの番号（CNC ID）", d.lbl_target.text())
        self.assertIn("ビット・軸型", d.cmb_verify.itemText(0))
        self.assertNotIn("/", d.cmb_verify.itemText(0).split("（")[0])


# ---------------------------------------------------------------------------
# B-9 待ち時間
# ---------------------------------------------------------------------------

class TestWriteTimeout(unittest.TestCase):
    def test_the_client_waits_longer_than_the_helper(self):
        self.assertGreater(C.DEFAULT_TIMEOUTS["param_write"], H.PARAM_WRITE_LIMIT)
        self.assertEqual((H.PARAM_WRITE_LIMIT, C.DEFAULT_TIMEOUTS["param_write"]), (5, 8))
        self.assertGreater(C.make_client({}).timeouts["param_write"], H.PARAM_WRITE_LIMIT)


# ---------------------------------------------------------------------------
# B-10 前の製品の値が残っているかもしれない注意
# ---------------------------------------------------------------------------

class TestLeftovers(WriteBase):
    def basic(self):
        # BASIC は A4 の 1420 が機械と違い、A3 の 1420 も違う（製品の軸ではない）
        text = self.raw.replace("A4P20000.0", "A4P18000.0").replace("A3P4000.0", "A3P3000.0")
        self.assertNotEqual(text, self.raw)
        return text

    def test_counts_numbers_that_differ_on_the_product_axis_and_are_not_written(self):
        p, _pl = self.plan({"20": "4"})
        out = O.basic_leftovers(self.raw, self.basic(), p, self.types, [4])
        self.assertEqual(out, [(1420, "A4", "20000.0", "18000.0")])

    def test_a_number_written_this_time_is_not_counted(self):
        p, _pl = self.plan({"1420": "18000.0"}, kinds=ALL_KINDS)
        self.assertEqual(O.basic_leftovers(self.raw, self.basic(), p, self.types, [4]), [])

    def test_other_axes_and_missing_inputs_are_ignored(self):
        p, _pl = self.plan({"20": "4"})
        self.assertEqual(O.basic_leftovers(self.raw, self.basic(), p, self.types, [5]), [])
        self.assertEqual(O.basic_leftovers(self.raw, None, p, self.types, [4]), [])
        self.assertEqual(O.basic_leftovers(self.raw, self.basic(), p, self.types, []), [])
        two = O.basic_leftovers(self.raw, self.basic(), p, self.types, [3, 4])
        self.assertEqual([(n, l) for n, l, _a, _b in two], [(1420, "A3"), (1420, "A4")])

    def test_it_does_not_change_what_is_written(self):
        p, pl = self.plan({"20": "4"})
        before = copy.deepcopy([(i["no"], i["label"], i["old"], i["new"]) for i in pl["items"]])
        O.basic_leftovers(self.raw, self.basic(), p, self.types, [4])
        self.assertEqual(before, [(i["no"], i["label"], i["old"], i["new"]) for i in pl["items"]])


class TestLeftoversDialog(WriteDialogBase):
    VALUES = {"20": "4"}

    def test_the_notice_is_shown_above_the_table_with_a_list(self):
        raw = (Path(__file__).resolve().parent / "fixtures" / "focas_param_31i.DAT").read_bytes().decode("cp932")
        basic = raw.replace("A4P20000.0", "A4P18000.0")
        d = self.dialog(basic_text=basic, product_axes=[4])
        self.go(d)
        self.assertFalse(d.leftover_box.isHidden())
        self.assertIn("前の製品の値が残っている可能性があります：1 件", d.lbl_leftover.text())
        shown = []
        d._info = lambda title, text: shown.append(text)
        d.b_leftover.click()
        self.assertIn("N01420 A4", shown[0])
        self.assertIn("18000.0", shown[0])
        self.assertEqual([r[0] for r in self.rows(d)], ["N00020"])  # 書く内容は変わらない

    def test_no_notice_without_basic(self):
        d = self.dialog()
        self.go(d)
        self.assertTrue(d.leftover_box.isHidden())


# ---------------------------------------------------------------------------
# C 事実と仕様の書き分け・二重の点検
# ---------------------------------------------------------------------------

class TestSpecNotFact(unittest.TestCase):
    def test_ew_buffer_retry_is_marked_as_spec_for_cnc_wrparam(self):
        src = inspect.getsource(H.Family31i.param_write)
        for line in src.splitlines():
            if "EW_BUFFER" in line or "待って同じ分を送り直す" in line:
                self.assertNotIn("【事実】", line, line)
        self.assertIn("【仕様", src)
        self.assertIn("未確認", src)


class TestRunWriteDoubleCheck(WriteBase):
    def test_count_mismatch_given_to_run_write_stops_it(self):
        p, pl = self.plan({"20": "4", "21": "1"})
        with self.assertRaises(O.WriteRefused) as cm:
            self.run_write(pl["items"], expected=pl["expected"] + 1, unwritable=pl["unwritable"])
        self.assertIn("合いません", "\n".join(cm.exception.reasons))
        self.assertEqual(self.wr_calls(), 0)

    def test_numbers_on_one_side_only_given_to_run_write_stop_it(self):
        p, pl = self.plan({"20": "4"})
        with self.assertRaises(O.WriteRefused) as cm:
            self.run_write(pl["items"], expected=2, unwritable=[("99999", "", None, "1")])
        self.assertIn("片側にしか無い", "\n".join(cm.exception.reasons))
        self.assertEqual(self.wr_calls(), 0)

    def test_matching_counts_write_normally(self):
        p, pl = self.plan({"20": "4", "21": "1"})
        res = self.run_write(pl["items"], expected=pl["expected"], unwritable=pl["unwritable"])
        self.assertEqual(res.state, "done")


if __name__ == "__main__":
    unittest.main()
