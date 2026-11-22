# -*- coding: utf-8 -*-
"""段4・段5の2回目のレビューで見つかった問題の直し（設計 §16「2回目のレビューで直した点」）の試験。

偽物の機械だけ。実機にはつながない。本物の DLL も 32bit Python も呼ばない。
1 別の機械へ持ち越さない・2 最後まで成功した書込は戻すボタンを出さない・3 元に戻す途中で閉じる・
4 1件も書かなかったジョブは飛ばす・5 控え無しで戻せない・7 読み戻せなかった確認の続き。
"""

import copy
import json
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import focas_backup as B            # noqa: E402
from nd287_app import focas_ops as O               # noqa: E402
from fake_client import DirectClient               # noqa: E402
from test_focas_ops_write import WriteBase, CNC_ID, PWE   # noqa: E402
from test_focas_write_dialog import WriteDialogBase       # noqa: E402

ARGS = {"host": "127.0.0.1", "port": 8193}
OTHER = {"alarms": [PWE], "cnc_id": [1, 2, 3, 4]}


class SlowClient(DirectClient):
    def call(self, cmd, args=None, timeout=None, on_event=None):
        if cmd == "param_write":
            time.sleep(0.25)
        return super().call(cmd, args, timeout, on_event)


class Proxy:
    """つなぎ先を途中で取り替えられる（機械A → 機械B の試験用）。"""
    def __init__(self, a):
        self.cur = a

    def __getattr__(self, n):
        return getattr(self.cur, n)


# ---------------------------------------------------------------------------
# 1 別の機械の結果は戻さない（ops）
# ---------------------------------------------------------------------------

class TestUndoRefusesOtherMachineResult(WriteBase):
    def written(self):
        _p, pl = self.plan({"20": "4", "21": "1"})
        return self.run_write(pl["items"])

    def test_a_result_of_another_cnc_id_is_refused_and_nothing_is_written(self):
        res = self.written()
        res.cnc_id = "FFFFFFFF-0-0-0"
        n = self.wr_calls()
        with self.assertRaises(O.WriteRefused) as cm:
            O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                       settings=self.settings, family="31i")
        self.assertIn("機械のものではありません", "\n".join(cm.exception.reasons))
        self.assertEqual(self.wr_calls(), n)
        self.assertEqual((self.machine_value(20), self.machine_value(21)), ("4", "1"))

    def test_the_verified_extra_path_is_refused_the_same_way(self):
        _p, pl = self.plan({"20": "4", "1815": "00110000"})
        it = next(i for i in pl["items"] if i["no"] == 1815)
        self.client.lib.sc["write_store"] = {"1815": "11111111"}
        out = O.verify_kind(self.client, it, cnc_id=CNC_ID, backup_info=self.info,
                            settings=self.settings, family="31i")
        self.client.lib.sc["write_store"] = {}
        for bad in ("FFFFFFFF-0-0-0", None):
            res = O.verify_undo_result(out, bad)
            with self.assertRaises(O.WriteRefused):
                O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                           settings=self.settings, family="31i", verified_extra=[out["key"]])
        self.assertEqual(self.machine_value(1815, "A4"), "11111111")      # 触っていない

    def test_a_result_without_a_cnc_id_is_refused(self):
        res = self.written()
        res.cnc_id = None
        with self.assertRaises(O.WriteRefused):
            O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                       settings=self.settings, family="31i")

    def test_the_same_machine_still_works(self):
        res = self.written()
        out = O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                         settings=self.settings, family="31i")
        self.assertEqual(out.state, "done")


class TestVfyUndoDoesNotSurviveAMachineChange(WriteDialogBase):
    VALUES = {"20": "4", "1815": "00110000"}

    def test_machine_a_to_machine_b_drops_the_one_item_undo_and_the_alert(self):
        a = DirectClient(copy.deepcopy(self.SCENARIO))
        b = DirectClient(copy.deepcopy(OTHER))
        px = Proxy(a)
        d = self.dialog(client=px)
        self.go(d)
        a.lib.sc["write_store"] = {"1815": "11111111"}
        d.b_verify.click()
        self.wait(d)
        self.assertFalse(d.b_undo_one.isHidden())
        self.assertFalse(d.lbl_alert.isHidden())
        d.b_disconnect.click()
        self.wait(d)
        px.cur = b
        d.b_connect.click()
        self.wait(d)
        self.assertIsNone(d._vfy_undo)
        self.assertTrue(d.b_undo_one.isHidden())
        self.assertFalse(d.b_undo_one.isEnabled())
        self.assertTrue(d.lbl_alert.isHidden())
        self.assertEqual(d.lbl_alert.text(), "")
        # B で控えを取っても、A の確認は戻せない（B に何も書かない）
        d.b_backup.click()
        self.wait(d)
        d.start_undo_one()
        self.wait(d)
        self.assertEqual(b.lib.called("cnc_wrparam"), 0)


# ---------------------------------------------------------------------------
# 2・4 前に書いたジョブの探し方（ops）
# ---------------------------------------------------------------------------

class TestFindPendingJob(WriteBase):
    def write_job(self, folder=None, **kw):
        _p, pl = self.plan({"20": "4", "21": "1"})
        folder = folder or self.folder
        return self.run_write(pl["items"], job_path=folder / O.JOB_NAME, **kw)

    def test_a_finished_write_is_completed_with_the_time_and_no_undo_offer_text(self):
        self.write_job()
        res = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertEqual(res.job_kind, "completed")
        self.assertTrue(res.job_time)
        text = O.describe_pending_job(res)
        self.assertIn(res.job_time, text)
        self.assertIn("そのあとの機械の値は確かめていません", text)       # 3 機械を読まずに「今もそのまま」と言わない
        self.assertNotIn("今もそのまま", text)
        self.assertNotIn("書いたままです", text)
        self.assertNotIn("元に戻す", text)

    def test_a_stopped_write_is_interrupted_and_says_it_was_left_written(self):
        _p, pl = self.plan({"20": "4", "21": "1", "22": "1"})
        self.client.lib.sc["write_rc_at"] = {"3": 13}
        self.run_write(pl["items"], job_path=self.folder / O.JOB_NAME)
        res = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertEqual(res.job_kind, "interrupted")
        text = O.describe_pending_job(res)
        self.assertIn(res.job_time, text)
        self.assertIn("書いたまま", text)
        self.assertIn("途中で止まった", text)

    def test_an_unknown_item_makes_it_interrupted(self):
        self.write_job()
        j = self.folder / O.JOB_NAME
        d = json.loads(j.read_text(encoding="utf-8"))
        d["items"][0]["status"] = "unknown"
        j.write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(O.find_pending_job(self.folder.parent, CNC_ID).job_kind, "interrupted")

    def test_a_partly_undone_job_is_interrupted(self):
        res = self.write_job()
        O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info, settings=self.settings,
                   family="31i", cancel=iter([False, True, True]).__next__,
                   job_path=self.folder / ("元に戻す_" + O.JOB_NAME))
        self.assertEqual(O.find_pending_job(self.folder.parent, CNC_ID).job_kind, "interrupted")

    def test_old_jobs_are_flagged_and_the_text_says_a_confirmation_comes(self):
        self.write_job()
        j = self.folder / O.JOB_NAME
        old = time.time() - 10 * 86400
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(old))
        d = json.loads(j.read_text(encoding="utf-8"))             # 古さは記録の finished_at で決める（6）
        d["started_at"] = d["finished_at"] = stamp
        j.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        res = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertTrue(res.job_old)
        self.assertGreaterEqual(res.job_age_days, 10)
        self.assertFalse(O.find_pending_job(self.folder.parent, CNC_ID, now=old + 86400).job_old)

    def test_an_old_power_cycle_is_dropped_unless_pw0000_is_showing_now(self):
        _p, pl = self.plan({"20": "4", "21": "1"})
        self.client.lib.sc["write_alarms"] = {"20": [[0, 1, 0, "POWER OFF"]]}
        self.run_write(pl["items"], job_path=self.folder / O.JOB_NAME)
        pw = self.client.call("alarms")["alarms"]
        self.assertTrue(O.find_pending_job(self.folder.parent, CNC_ID).power_cycle)       # 渡さないときは今まで通り
        self.assertTrue(O.find_pending_job(self.folder.parent, CNC_ID, alarms=pw).power_cycle)
        gone = [a for a in pw if a["code"] != "PW0000"]
        res = O.find_pending_job(self.folder.parent, CNC_ID, alarms=gone)
        self.assertFalse(res.power_cycle)
        self.assertNotIn("電源", "\n".join(O.describe_result(res)))

    def test_a_newer_job_that_wrote_nothing_does_not_hide_an_older_pending_one(self):         # 4
        self.write_job()
        time.sleep(1.1)
        res3 = B.take_backup(self.client, self.folder.parent, self.settings)
        self.assertNotEqual(res3.folder, self.folder)
        (res3.folder / O.JOB_NAME).write_text(json.dumps(
            {"cnc_id": CNC_ID, "state": "stopped", "items": [
                {"no": 20, "label": "", "old": "4", "new": "5", "key": "byte/none",
                 "status": "stale", "touched": False}]}), encoding="utf-8")
        found = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertIsNotNone(found)
        self.assertEqual(Path(found.folder), self.folder)

    def test_a_newer_undone_job_still_hides_nothing_older_is_offered_for_it(self):
        res = self.write_job()
        O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info, settings=self.settings,
                   family="31i", job_path=self.folder / ("元に戻す_" + O.JOB_NAME))
        self.assertIsNone(O.find_pending_job(self.folder.parent, CNC_ID))


# ---------------------------------------------------------------------------
# 7 読み戻せなかった確認の続き（ops）
# ---------------------------------------------------------------------------

class TestRereadVerifyUndo(WriteBase):
    def lost(self):
        _p, pl = self.plan({"20": "4", "1815": "00110000"})
        it = next(i for i in pl["items"] if i["no"] == 1815)
        out = O.verify_kind(self.client, it, cnc_id=CNC_ID, backup_info=self.info,
                            settings=self.settings, family="31i")
        res = O.verify_undo_result(dict(out, now=None, touched=True), CNC_ID)
        res.items[0]["after"] = None                      # 読み戻せなかった
        return it, res

    def test_unchanged_value_means_nothing_to_undo(self):
        _it, res = self.lost()
        v, cur = O.reread_verify_undo(self.client, res, ARGS, cnc_id=CNC_ID)
        self.assertEqual(v, "unchanged")

    def test_changed_value_is_recorded_and_can_then_be_undone(self):
        it, res = self.lost()
        self.m.set_param(1815, "A4", "11111111")
        # 読み戻せていないままでは、人が変えたかもしれないと止まる
        stopped = O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                             settings=self.settings, family="31i", verified_extra=[it["key"]])
        self.assertEqual(stopped.items[0]["status"], "stale")
        v, cur = O.reread_verify_undo(self.client, res, ARGS, cnc_id=CNC_ID)
        self.assertEqual((v, cur), ("changed", "11111111"))
        out = O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                         settings=self.settings, family="31i", verified_extra=[it["key"]])
        self.assertEqual(out.state, "done")
        self.assertEqual(self.machine_value(1815, "A4"), it["old"])

    def test_another_machine_is_refused(self):
        _it, res = self.lost()
        res.cnc_id = "FFFFFFFF-0-0-0"
        with self.assertRaises(O.WriteRefused):
            O.reread_verify_undo(self.client, res, ARGS, cnc_id=CNC_ID)


# ---------------------------------------------------------------------------
# 2 画面: 前回の最後まで成功した書込・古い途中のジョブ
# ---------------------------------------------------------------------------

class TestPreviousJobDialog(WriteDialogBase):
    VALUES = {"20": "4", "21": "1"}

    def jobs(self):
        return list(Path(self.settings["focas_backup_dir"]).rglob(O.JOB_NAME))

    def test_a_finished_write_of_before_is_plain_text_with_the_time_and_no_undo_button(self):
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        self.assertEqual(d._result.state, "done")
        self.assertTrue(d.b_undo.isEnabled())                      # 今回の書込は、その場で戻せる
        d.close()
        d2 = self.dialog(values={"22": "1"})
        self.go(d2)
        text = d2.txt_result.toPlainText()
        self.assertIn("前回（", text)
        self.assertIn("そのあとの機械の値は確かめていません", text)
        self.assertNotIn("今もそのまま", text)
        self.assertNotIn("書いたままです", text)
        self.assertTrue(d2.b_undo.isHidden())
        self.assertFalse(d2.b_undo.isEnabled())

    def test_the_result_of_this_session_names_the_time_when_the_backup_is_retaken(self):
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        d.b_backup.click()
        self.wait(d)
        self.assertIn(d._result.started_at, d.txt_result.toPlainText())

    def test_an_old_unfinished_job_asks_once_before_undoing(self):
        self.client.lib.sc["write_rc_at"] = {"2": 13}
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        self.client.lib.sc["write_rc_at"] = {}
        d.close()
        for p in self.jobs():
            old = time.time() - 200 * 86400
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(old))
            d = json.loads(p.read_text(encoding="utf-8"))
            d["started_at"] = d["finished_at"] = stamp
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        d2 = self.dialog()
        self.go(d2)
        self.assertIn("書いたまま", d2.txt_result.toPlainText())
        self.assertIn("途中で止まった", d2.txt_result.toPlainText())
        self.assertTrue(d2.b_undo.isEnabled())
        asked = []
        d2._confirm_old_undo = lambda r: (asked.append(1) or False)
        d2.b_undo.click()
        self.wait(d2)
        self.assertEqual(asked, [1])
        self.assertEqual(self.machine_value(20), "4")             # 「いいえ」なら戻さない
        d2._confirm_old_undo = lambda r: (asked.append(2) or True)
        d2.b_undo.click()
        self.wait(d2)
        self.assertEqual(asked, [1, 2])
        self.assertEqual(self.machine_value(20), "9")

    def test_a_recent_unfinished_job_does_not_ask(self):
        self.client.lib.sc["write_rc_at"] = {"2": 13}
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        self.client.lib.sc["write_rc_at"] = {}
        d.close()
        d2 = self.dialog()
        self.go(d2)
        d2._confirm_old_undo = lambda r: self.fail("新しいジョブで確認を出してはいけない")
        d2.b_undo.click()
        self.wait(d2)
        self.assertEqual(self.machine_value(20), "9")


# ---------------------------------------------------------------------------
# 3 元に戻す途中で閉じる（画面）
# ---------------------------------------------------------------------------

class TestCloseWhileUndoing(WriteDialogBase):
    VALUES = {"20": "4", "21": "1", "22": "1", "23": "1"}

    def start(self, after_stop_close=False):
        self.client = SlowClient(copy.deepcopy(self.SCENARIO))
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        d.asked = []
        d._confirm_close = lambda: (d.asked.append("close:" + d._mode) or True)
        d._ask_close_after_stop = lambda text: (d.asked.append(text) or after_stop_close)
        return d

    def test_the_confirmation_says_it_is_undoing(self):
        import nd287_app.gui as G
        from PySide6 import QtWidgets
        seen = []
        orig = QtWidgets.QMessageBox.question
        QtWidgets.QMessageBox.question = staticmethod(
            lambda parent, title, body, *a, **k: (seen.append((title, body)) or QtWidgets.QMessageBox.No))
        try:
            d = self.dialog()
            d._busy, d._busy_quiet, d._mode = True, False, "undo"
            self.assertFalse(d._request_close())
            self.assertIn("元の値に戻している途中です", seen[0][1])
            d._mode = "write"
            d._request_close()
            self.assertIn("書いている途中です", seen[1][1])
            d._busy, d._mode = False, ""
        finally:
            QtWidgets.QMessageBox.question = orig

    def test_closing_before_anything_was_restored_does_not_close_silently(self):
        d = self.start()
        d.b_undo.click()
        d.reject()                                                 # まだ1件も戻していない
        self.wait(d)
        for _ in range(5):
            self.app.processEvents()
        self.assertEqual(d.asked[0], "close:undo")
        self.assertEqual(len(d.asked), 2)
        self.assertIn("元に戻せたのは 0 件", d.asked[1])
        self.assertIn("まだ書いたままの番号が 4 件", d.asked[1])
        self.assertIn("［書いた分を元に戻す］", d.asked[1])              # 7 1件も戻せていないので、ボタンも（続き）ではない
        self.assertNotIn("元に戻す（続き）", d.asked[1])
        self.assertIn("書いた分を元に戻す", d.b_undo.text())
        self.assertTrue(d.isVisible())
        self.assertEqual(self.machine_value(23), "1")              # 戻っていない

    def test_a_partial_undo_shows_the_result_and_a_continue_button_that_finishes(self):
        d = self.start()
        d.b_undo.click()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.4:
            self.app.processEvents()
            time.sleep(0.01)
        d.reject()
        self.wait(d)
        for _ in range(5):
            self.app.processEvents()
        back = sum(1 for n in (20, 21, 22, 23) if self.machine_value(n) != {"20": "4", "21": "1", "22": "1", "23": "1"}[str(n)])
        self.assertTrue(1 <= back < 4, back)
        self.assertIn(f"元に戻せたのは {back} 件", d.asked[-1])
        self.assertTrue(d.isVisible())
        self.assertIn("元に戻す（続き）", d.b_undo.text())
        self.assertTrue(d.b_undo.isEnabled())
        d.b_undo.click()
        self.wait(d)
        self.assertEqual([self.machine_value(n) for n in (20, 21, 22, 23)], ["9", "0", "0", "0"])
        self.assertNotIn("続き", d.b_undo.text())

    def test_a_finished_undo_closes_without_asking_after_the_stop(self):
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        d.asked = []
        d._ask_close_after_stop = lambda text: (d.asked.append(text) or False)
        d._closing = True                                          # 閉じると言われたあとに、全部戻せた
        d.b_undo.click()
        self.wait(d)
        for _ in range(5):
            self.app.processEvents()
        self.assertEqual(d.asked, [])
        self.assertFalse(d.isVisible())


# ---------------------------------------------------------------------------
# 5 控えが無いとき（画面）
# ---------------------------------------------------------------------------

class TestUndoWithoutBackup(WriteDialogBase):
    VALUES = {"20": "4", "21": "1"}

    def test_no_backup_disables_undo_and_says_why_and_a_forced_call_does_not_crash(self):
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        self.assertTrue(d.lbl_undo_why.isHidden())
        d._backup_failed("x")
        self.assertFalse(d.b_undo.isEnabled())
        self.assertFalse(d.lbl_undo_why.isHidden())
        self.assertIn("控え", d.lbl_undo_why.text())
        d.start_undo()                                             # TypeError にならない
        self.wait(d)
        self.assertEqual(self.machine_value(20), "4")

    def test_not_connected_says_why(self):
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        d.b_disconnect.click()
        self.wait(d)
        self.assertFalse(d.b_undo.isEnabled())
        self.assertIn("つないで", d.lbl_undo_why.text())


# ---------------------------------------------------------------------------
# 7 読み戻せなかった確認（画面）
# ---------------------------------------------------------------------------

class TestVerifyReadbackLost(WriteDialogBase):
    VALUES = {"20": "4", "1815": "00110000"}

    def lost_dialog(self):
        d = self.dialog()
        self.go(d)
        it = next(x for x in d._wplan["items"] if x["no"] == 1815)
        out = {"ok": False, "result": "unknown", "key": it["key"], "no": 1815, "label": "A4",
               "touched": True, "changed": False, "now": None, "original": it["old"],
               "item": dict(it, before=it["old"], after=None, rc=None, touched=True)}
        d._vfy_undo = O.verify_undo_result(out, CNC_ID)
        d._vfy_undo.items[0]["after"] = None
        d._refresh_enabled()
        return d, it

    def test_the_button_says_to_reconnect_and_read_and_unchanged_clears_it(self):
        d, it = self.lost_dialog()
        self.assertFalse(d.b_undo_one.isHidden())
        self.assertIn("つなぎ直して今の値を読む", d.b_undo_one.text())
        d.b_undo_one.click()
        self.wait(d)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)        # 読むだけ
        self.assertTrue(d.b_undo_one.isHidden())
        self.assertIn("戻すものはありません", d.txt_result.toPlainText())

    def test_a_changed_value_after_reading_can_then_be_undone(self):
        d, it = self.lost_dialog()
        self.client.machine.set_param(1815, "A4", "11111111")
        d.b_undo_one.click()
        self.wait(d)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)
        self.assertFalse(d.b_undo_one.isHidden())
        self.assertIn("確認した1件を元の値に戻す", d.b_undo_one.text())
        self.assertIn("11111111", d.lbl_alert.text())
        d.b_undo_one.click()
        self.wait(d)
        self.assertEqual(self.machine_value(1815, "A4"), it["old"])
        self.assertTrue(d.b_undo_one.isHidden())


if __name__ == "__main__":
    unittest.main()
