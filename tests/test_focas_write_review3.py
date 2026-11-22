# -*- coding: utf-8 -*-
"""段4・段5の3回目のレビューで残った小さい点（設計 §16「3回目のレビューで残った小さい点」1〜9）の直しの試験。

偽物の機械だけ。実機にはつながない。本物の DLL も 32bit Python も呼ばない。
1 元に戻し済みは飛ばす・2 最後まで成功した書込は戻さない・3 機械を読まずに「今もそのまま」と言わない・
4 全行照合の不一致は最後まで成功にしない・5 元に戻した記録が読めないときは「分からない」・
6 古さは記録の時刻で決める・7 文とボタン名・切断時の理由・9 通信切れからの流れを画面で通す。
"""

import copy
import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import focas_client as C            # noqa: E402
from nd287_app import focas_ops as O               # noqa: E402
from fake_client import DirectClient               # noqa: E402
from test_focas_ops_write import WriteBase, CNC_ID, PWE   # noqa: E402
from test_focas_write_dialog import WriteDialogBase       # noqa: E402


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


class JobBase(WriteBase):
    def write_stopped_job(self, folder=None):
        """3件目で止まった（機械に2件書いたままの）ジョブ。"""
        _p, pl = self.plan({"20": "4", "21": "1", "22": "1"})
        self.client.lib.sc["write_rc_at"] = {"3": 13}
        res = self.run_write(pl["items"], job_path=(folder or self.folder) / O.JOB_NAME)
        self.client.lib.sc["write_rc_at"] = {}
        return res

    def write_done_job(self, **kw):
        _p, pl = self.plan({"20": "4", "21": "1"})
        return self.run_write(pl["items"], job_path=self.folder / O.JOB_NAME, **kw)

    def edit(self, path, **kw):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d.update(kw)
        Path(path).write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return d

    def copy_job(self, name, **kw):
        """今のジョブを別のフォルダへ写して、記録を書き換える（別の日に書いたジョブの代わり）。"""
        dst = self.folder.parent / name
        dst.mkdir()
        shutil.copy(self.folder / O.JOB_NAME, dst / O.JOB_NAME)
        self.edit(dst / O.JOB_NAME, **kw)
        return dst


# ---------------------------------------------------------------------------
# 1 元に戻し済みのジョブは飛ばして、前の途中ジョブを探す
# ---------------------------------------------------------------------------

class TestSkipUndoneJob(JobBase):
    def test_an_undone_newer_job_does_not_hide_an_older_pending_one(self):
        self.write_stopped_job()
        t = time.time()
        self.edit(self.folder / O.JOB_NAME, started_at=stamp(t - 3 * 86400),
                  finished_at=stamp(t - 3 * 86400))
        newer = self.copy_job("新しい", started_at=stamp(t - 86400), finished_at=stamp(t - 86400),
                              state="done")
        self.edit(newer / O.JOB_NAME, state="done")
        (newer / ("元に戻す_" + O.JOB_NAME)).write_text(
            json.dumps({"cnc_id": CNC_ID, "state": "done", "items": []}), encoding="utf-8")
        found = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertIsNotNone(found)
        self.assertEqual(Path(found.folder), self.folder)           # 新しい方は戻し済み。前の途中を出す
        self.assertEqual(found.job_kind, "interrupted")

    def test_when_every_job_is_undone_nothing_is_offered(self):
        res = self.write_done_job()
        O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info, settings=self.settings,
                   family="31i", job_path=self.folder / ("元に戻す_" + O.JOB_NAME))
        self.assertIsNone(O.find_pending_job(self.folder.parent, CNC_ID))


# ---------------------------------------------------------------------------
# 2 最後まで成功した書込は、関数の側でも戻さない
# ---------------------------------------------------------------------------

class TestCompletedJobIsNotUndone(JobBase):
    def test_run_undo_refuses_the_result_of_a_completed_earlier_write(self):
        self.write_done_job()
        found = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertEqual(found.job_kind, "completed")
        self.m.set_param(20, "", "7")                              # そのあと人が変えた
        n = self.wr_calls()
        with self.assertRaises(O.WriteRefused) as cm:
            O.run_undo(self.client, found, cnc_id=CNC_ID, backup_info=self.info,
                       settings=self.settings, family="31i")
        self.assertIn("最後まで成功", "\n".join(cm.exception.reasons))
        self.assertEqual(self.wr_calls(), n)
        self.assertEqual(self.machine_value(20), "7")

    def test_the_result_just_written_in_this_session_can_still_be_undone(self):
        res = self.write_done_job()
        self.assertIsNone(res.job_kind)
        out = O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                         settings=self.settings, family="31i")
        self.assertEqual(out.state, "done")

    def test_an_interrupted_earlier_job_can_still_be_undone(self):
        self.write_stopped_job()
        found = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertEqual(found.job_kind, "interrupted")
        out = O.run_undo(self.client, found, cnc_id=CNC_ID, backup_info=self.info,
                         settings=self.settings, family="31i")
        self.assertEqual(out.state, "done")


# ---------------------------------------------------------------------------
# 4 全行照合の結果を残し、不一致は「最後まで成功」にしない
# ---------------------------------------------------------------------------

class TestVerifyResultIsKept(JobBase):
    def test_the_job_file_keeps_the_full_check_result(self):
        p, pl = self.plan({"20": "4", "21": "1"})
        self.run_write(pl["items"], newtext=p["newtext"], job_path=self.folder / O.JOB_NAME)
        d = json.loads((self.folder / O.JOB_NAME).read_text(encoding="utf-8"))
        self.assertTrue(d["verify"]["ok"])
        self.assertEqual(d["verify"]["written_ok"], 2)
        self.assertEqual(O.find_pending_job(self.folder.parent, CNC_ID).job_kind, "completed")

    def test_a_mismatch_in_the_full_check_is_not_completed_after_reopening(self):
        p, pl = self.plan({"20": "4"})
        res = self.run_write(pl["items"], newtext=p["newtext"].replace("N00020Q1P4", "N00020Q1P5"),
                             job_path=self.folder / O.JOB_NAME)
        self.assertFalse(res.verify["ok"])
        found = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertIsNotNone(found)
        self.assertNotEqual(found.job_kind, "completed")
        self.assertFalse(found.verify["ok"])
        text = O.describe_pending_job(found)
        self.assertIn("照合で合わない", text)
        self.assertNotIn("最後まで書けています", text)

    def test_a_failed_full_check_that_could_not_run_is_not_completed_either(self):
        self.write_done_job()
        self.edit(self.folder / O.JOB_NAME, verify={"ok": False, "error": "通信が切れました"})
        self.assertNotEqual(O.find_pending_job(self.folder.parent, CNC_ID).job_kind, "completed")

    def test_no_full_check_result_keeps_the_old_behaviour(self):
        self.write_done_job()                                       # newtext なし = 照合していない
        d = json.loads((self.folder / O.JOB_NAME).read_text(encoding="utf-8"))
        self.assertIsNone(d["verify"])
        self.assertEqual(O.find_pending_job(self.folder.parent, CNC_ID).job_kind, "completed")

    def test_many_unexpected_changes_are_cut_but_counted(self):
        v = {"ok": True, "unexpected": [("0", str(i), "1", "2") for i in range(500)]}
        out = O._verify_for_job(v)
        self.assertEqual(len(out["unexpected"]), 200)
        self.assertEqual(out["unexpected_count"], 500)
        self.assertIsNone(O._verify_for_job(None))


# ---------------------------------------------------------------------------
# 5 元に戻した記録が読めないとき
# ---------------------------------------------------------------------------

class TestUnreadableUndoRecord(JobBase):
    def test_an_unreadable_undo_record_is_unknown_not_completed(self):
        self.write_done_job()
        (self.folder / ("元に戻す_" + O.JOB_NAME)).write_text("{こわれた", encoding="utf-8")
        found = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertEqual(found.job_kind, "undo_unreadable")
        text = O.describe_pending_job(found)
        self.assertIn("分かりません", text)
        self.assertIn("記録が読めません", text)
        self.assertNotIn("最後まで書けています", text)

    def test_the_unreadable_case_can_still_be_undone_with_the_value_check(self):
        self.write_done_job()
        (self.folder / ("元に戻す_" + O.JOB_NAME)).write_text("", encoding="utf-8")
        found = O.find_pending_job(self.folder.parent, CNC_ID)
        out = O.run_undo(self.client, found, cnc_id=CNC_ID, backup_info=self.info,
                         settings=self.settings, family="31i")
        self.assertEqual(out.state, "done")


# ---------------------------------------------------------------------------
# 6 古さは記録の時刻で決める。読めないときだけファイルの更新時刻
# ---------------------------------------------------------------------------

class TestJobAge(JobBase):
    def test_the_record_time_wins_over_a_fresh_file_time(self):
        self.write_stopped_job()
        t = time.time() - 30 * 86400
        self.edit(self.folder / O.JOB_NAME, started_at=stamp(t), finished_at=stamp(t))
        res = O.find_pending_job(self.folder.parent, CNC_ID)         # ファイルの更新時刻は今
        self.assertTrue(res.job_old)
        self.assertGreaterEqual(res.job_age_days, 29)

    def test_the_record_time_wins_over_an_old_file_time(self):
        self.write_stopped_job()
        j = self.folder / O.JOB_NAME
        old = time.time() - 30 * 86400
        os.utime(j, (old, old))                                     # コピー・同期の逆（記録は今）
        res = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertFalse(res.job_old)
        self.assertEqual(res.job_age_days, 0)

    def test_the_file_time_is_used_only_when_the_record_time_cannot_be_read(self):
        self.write_stopped_job()
        j = self.folder / O.JOB_NAME
        for bad in (None, "", "きのう"):
            self.edit(j, started_at=bad, finished_at=bad)
            old = time.time() - 20 * 86400
            os.utime(j, (old, old))
            res = O.find_pending_job(self.folder.parent, CNC_ID)
            self.assertTrue(res.job_old, bad)
            self.assertGreaterEqual(res.job_age_days, 19, bad)

    def test_the_order_also_follows_the_record_time(self):
        self.write_stopped_job()
        t = time.time()
        self.edit(self.folder / O.JOB_NAME, started_at=stamp(t - 5 * 86400),
                  finished_at=stamp(t - 5 * 86400))                 # 記録は古い・ファイルは新しい
        other = self.copy_job("もう一つ", started_at=stamp(t - 86400), finished_at=stamp(t - 86400))
        old = t - 40 * 86400
        os.utime(other / O.JOB_NAME, (old, old))                    # 記録は新しい・ファイルは古い
        found = O.find_pending_job(self.folder.parent, CNC_ID)
        self.assertEqual(Path(found.folder), other)


# ---------------------------------------------------------------------------
# 画面: 2・5・7・9
# ---------------------------------------------------------------------------

class Flaky(DirectClient):
    """arm したあとの最初の書込のすぐあとで通信が切れる。つなぎ直す（connect）と戻る。"""
    lost = False
    arm = False

    def call(self, cmd, args=None, timeout=None, on_event=None):
        if cmd == "connect":
            self.lost = False
        if self.lost:
            raise C.FocasError("lost", "通信が切れました")
        r = super().call(cmd, args, timeout, on_event)
        if cmd == "param_write" and self.arm:
            self.arm, self.lost = False, True
        return r


class TestDialog(WriteDialogBase):
    VALUES = {"20": "4", "21": "1"}

    def writes(self):
        return self.client.lib.called("cnc_wrparam")

    def jobs(self):
        return list(Path(self.settings["focas_backup_dir"]).rglob(O.JOB_NAME))

    def test_2_start_undo_itself_refuses_a_completed_earlier_write(self):
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        d.close()
        self.client.machine.set_param(20, "", "7")                  # そのあと人が変えた
        d2 = self.dialog(values={"22": "1"})
        self.go(d2)
        self.assertEqual(d2._result.job_kind, "completed")
        self.assertTrue(d2.b_undo.isHidden())
        n = self.writes()
        d2.start_undo()                                             # ボタンが隠れていても、関数が断る
        self.wait(d2)
        self.assertEqual(self.writes(), n)
        self.assertEqual(self.machine_value(20), "7")

    def test_5_an_unreadable_undo_record_is_shown_as_unknown_and_not_hidden(self):
        self.client.lib.sc["write_rc_at"] = {"2": 13}
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        self.client.lib.sc["write_rc_at"] = {}
        d.close()
        for p in self.jobs():
            (p.parent / ("元に戻す_" + O.JOB_NAME)).write_text("{こわれた", encoding="utf-8")
        d2 = self.dialog()
        self.go(d2)
        text = d2.txt_result.toPlainText()
        self.assertIn("分かりません", text)
        self.assertIn("記録が読めません", text)
        self.assertNotIn("最後まで書けています", text)
        self.assertFalse(d2.b_undo.isHidden())                     # 成功扱いにせず、戻せるようにしておく
        self.assertTrue(d2.b_undo.isEnabled())

    def test_9_same_value_check_lost_readback_reconnect_reread_and_undo_in_the_real_order(self):
        self.client = Flaky(copy.deepcopy(self.SCENARIO))
        kinds_before = copy.deepcopy(self.settings["focas_verified_kinds"])
        d = self.dialog(values={"20": "4", "1815": "00110000"})
        self.go(d)
        self.assertTrue(d.b_verify.isEnabled())
        orig = self.machine_value(1815, "A4")
        self.client.arm = True                                      # 確認の書込の直後に通信が切れる
        self.client.lib.sc["write_store"] = {"1815": "11111111"}    # 書いたら値が変わってしまう型
        d.b_verify.click()
        self.wait(d)
        # (1) 確認は通らず、値が変わったかもしれない。読み戻せていない
        self.assertEqual(d.settings["focas_verified_kinds"], kinds_before)      # 『確認済み』にしていない
        self.assertTrue(d.lbl_alert.isVisible() or not d.lbl_alert.isHidden())
        self.assertIn("つなぎ直して今の値を読む", d.lbl_alert.text())
        self.assertIsNotNone(d._vfy_undo)
        self.assertIsNone(d._vfy_undo.items[0].get("after"))
        self.assertEqual(d.b_undo_one.text(), "つなぎ直して今の値を読む")
        # (2) 通信が切れていると読む画面の自動読み直しで切断になり、ボタンは押せない。理由が出る
        d._poll()
        self.wait(d)
        self.assertFalse(d._connected)
        self.assertFalse(d.b_undo_one.isEnabled())
        why = d.lbl_undo_why.text()
        self.assertFalse(d.lbl_undo_why.isHidden())
        self.assertIn("つなぎ直して今の値を読む", why)
        self.assertIn("機械につないでください", why)
        # (3) つなぎ直す。確認の記録も控えも残っている。理由は消える
        d.b_connect.click()
        self.wait(d)
        self.assertTrue(d._connected)
        self.assertIsNotNone(d._vfy_undo)
        self.assertIsNotNone(d._backup)
        self.assertTrue(d.b_undo_one.isEnabled())
        self.assertNotIn("機械につないでください", d.lbl_undo_why.text())
        # (4) 今の値を読む（書かない）。変わっていたので「元に戻す」に切り替わる
        n = self.writes()
        d.b_undo_one.click()
        self.wait(d)
        self.assertEqual(self.writes(), n)
        self.assertEqual(d.b_undo_one.text(), "確認した1件を元の値に戻す")
        self.assertTrue(d.b_undo_one.isEnabled())
        self.assertIn("11111111", d.txt_result.toPlainText())
        self.assertEqual(self.machine_value(1815, "A4"), "11111111")
        # (5) 戻す（機械が今度は書いた値のまま受け付ける）。1件だけ書いて、元の値に戻る
        self.client.lib.sc["write_store"] = {}
        d.b_undo_one.click()
        self.wait(d)
        self.assertEqual(self.writes(), n + 1)
        self.assertEqual(self.machine_value(1815, "A4"), orig)
        self.assertTrue(d.b_undo_one.isHidden())
        self.assertIsNone(d._vfy_undo)
        self.assertEqual(d.settings["focas_verified_kinds"], kinds_before)

    def test_7_the_wording_names_the_button_that_is_really_there(self):
        self.client.lib.sc["write_rc_at"] = {"2": 13}
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        self.assertTrue(d.b_undo.text().startswith("書いた分を元に戻す"))
        self.assertEqual(d._undo_name(), "書いた分を元に戻す")
        d._undo_partial = True
        self.assertEqual(d._undo_name(), "元に戻す（続き）")
        self.assertEqual(d._undo_name_after(0), "元に戻す（続き）")
        d._undo_partial = False
        self.assertEqual(d._undo_name_after(0), "書いた分を元に戻す")
        self.assertEqual(d._undo_name_after(2), "元に戻す（続き）")


class TestWording(unittest.TestCase):
    def test_8_no_long_runs_of_spaces_before_a_keyword_in_focas_ops(self):
        import re
        src = (Path(__file__).resolve().parent.parent / "nd287_app" / "focas_ops.py").read_text(
            encoding="utf-8")
        for n, line in enumerate(src.splitlines(), 1):
            body = line.lstrip(" ")
            if body.startswith("#"):
                continue
            code = re.sub(r"\"[^\"]*\"|'[^']*'", '""', body.split("  #")[0])      # 文字列の中の空白は見ない
            self.assertIsNone(re.search(r"[\w)\]] {8,}(and|or|if|else|not|in)\b", code),
                              f"{n}行目: {line[:60]}")


if __name__ == "__main__":
    unittest.main()
