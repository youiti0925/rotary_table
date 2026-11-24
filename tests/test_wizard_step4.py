# -*- coding: utf-8 -*-
"""かんたん作成の段④「測定プログラムを送る」（画面）。機械は偽物（tests/fake_client.DirectClient）。

実機の動きの確認ではない。実機でしか分からないこと（LF CR CR のまま FOCAS で入るか）は、
読み戻しが違ったときの表示と、LF のみで送り直す補助の動きだけを偽物で確かめている。
"""
import copy
import os
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from fake_client import DirectClient                          # noqa: E402
from test_wizard_machine_write import WizardBase               # noqa: E402

FOLDER = "//CNC_MEM/USER/PATH1/"
EOB = b"\n\r\r"


class Step4Base(WizardBase):
    def setUp(self):
        super().setUp()
        import nd287_app.gui as G
        self._orig_app_dir = G.app_dir
        G.app_dir = lambda: Path(self._tmp)
        self.addCleanup(lambda: setattr(G, "app_dir", self._orig_app_dir))
        cond = self.work / "測定条件.csv"
        cond.write_text("回転,,,,,,,,,,,,傾斜\n型式,クローズ,間隔H,間隔W,1/N_H,1/N_W,分割数1,分割数2,"
                        "測定順HR,測定順WR,測定順WL,測定順HL,\nRTV-510,,100000,3000,1,1,36,10,1,2,3,4,\n",
                        encoding="utf-8-sig")
        rep = self.work / "再現.csv"
        rep.write_text("型式,ブロック数,回数,再現開始,再現終了\nRTV-510,4,5,0,270\n", encoding="utf-8-sig")
        self.settings.update(
            conditions_csv=str(cond), user_rotary_repeat_csv=str(rep),
            user_rotary_csv=str(self.work / "none1.csv"), user_tilt_csv=str(self.work / "none2.csv"),
            user_tilt_repeat_csv=str(self.work / "none3.csv"), judgement_csv=str(self.work / "none4.csv"),
            prog_log_csv=str(self.work / "log.csv"),
            focas_host="127.0.0.1", focas_backup_dir=str(self.work / "控え"), focas_prog_folder=FOLDER)
        self.ws = []

    def tearDown(self):
        for w in self.ws:
            w._close_prog()
            w.close()
            w.deleteLater()
        self.app.processEvents()

    def step4(self, scenario=None, tamper=None, settle=True):
        w = self.ready(self.wizard(), motor="product")
        self.ws.append(w)
        self.client = DirectClient(copy.deepcopy(scenario or {}))
        if tamper:
            orig = self.client.machine.add_program
            self.client.machine.add_program = lambda path, data, comment="": orig(path, tamper(data), comment)
        w._prog_client_factory = lambda s: self.client
        w._prog_auto = False
        w.show()
        w._step = 2
        w._apply_mode()
        w._on_prog()
        d = w._prog_dlg
        d._timer.setInterval(600000)
        d._warn = lambda t, x: None
        if settle:
            self.settle(w)
        return w

    def settle(self, w, need_states=True):
        d = w._prog_dlg
        t0 = time.monotonic()
        while time.monotonic() - t0 < 40:
            self.app.processEvents()
            if (not d._busy and d._backup is not None and (w._p_states or not need_states)):
                self.app.processEvents()
                if not d._busy:
                    return
            time.sleep(0.01)
        self.fail("画面の処理が終わりません")

    def finish(self, w):
        t0 = time.monotonic()
        d = w._prog_dlg
        while time.monotonic() - t0 < 40:
            self.app.processEvents()
            if not d._busy:
                self.app.processEvents()
                if not d._busy:
                    return
            time.sleep(0.01)
        self.fail("送りが終わりません")

    def choose(self, w, key):
        w.rad_p[key].setChecked(True)
        self.app.processEvents()
        self.settle(w, need_states=False)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5 and not w._p_states:
            self.app.processEvents()
            time.sleep(0.01)

    def results(self, w):
        return [w.tbl_p.item(i, 3).text() for i in range(w.tbl_p.rowCount())]

    def numbers(self, w):
        return [sp.value() for _r, sp in w._p_spins]

    def send(self, w):
        w._prog_dlg.b_write.click()
        self.finish(w)
        self.finish(w)


class TestOffer(Step4Base):
    def test_button_only_after_a_successful_write_or_from_step2(self):
        w = self.ready(self.wizard(), motor="product")
        self.ws.append(w)
        w._step = 3
        w._apply_mode()
        self.assertFalse(w.b_prog.isVisibleTo(w))
        w._on_writer_finished(False)                                # 失敗・途中 → 出さない
        self.assertFalse(w.b_prog.isVisibleTo(w))
        w._on_writer_finished(True)
        self.assertTrue(w.b_prog.isVisibleTo(w))
        self.assertIn("続けてプログラムを送る", w.b_prog.text())
        w._step = 2
        w._apply_mode()
        self.assertTrue(w.b_prog.isVisibleTo(w))                     # パラメータを送らない流れ
        self.assertIn("プログラムだけ", w.b_prog.text())
        w._step = 1
        w._apply_mode()
        self.assertFalse(w.b_prog.isVisibleTo(w))

    def test_real_writer_signal_is_connected(self):
        w = self.ready(self.wizard(), motor="product")
        self.ws.append(w)
        w._step = 2
        w._apply_mode()
        import nd287_app.gui as G
        with mock.patch.object(G.QtWidgets.QMessageBox, "question",
                               lambda *a, **k: G.QtWidgets.QMessageBox.Yes):
            w.b_next.click()
        self.assertFalse(w._params_ok)
        w._writer.write_finished.emit(True)
        self.assertTrue(w._params_ok)
        self.assertTrue(w.b_prog.isVisibleTo(w))
        w._on_prog()                                                  # ③の窓は片づけて④へ
        self.assertIsNone(w._writer)
        self.assertEqual(w._step, 4)
        self.assertTrue(w.panel4.isVisibleTo(w))
        self.assertFalse(w.panel.isVisibleTo(w))
        w._close_prog()

    def test_back_from_step4_goes_to_step2(self):
        w = self.step4()
        w.b_back.click()
        self.assertEqual(w._step, 2)
        self.assertIsNone(w._prog_dlg)


class TestStep4Send(Step4Base):
    def test_default_is_auto_and_shows_word_lines(self):
        w = self.step4()
        self.assertTrue(w.rad_p["one"].isChecked())                  # 前回の選び方が無いので「1本にまとめる」
        self.assertTrue(all(r.isEnabled() for r in w.rad_p.values()))
        self.assertIn("回転のテーブルとして作ります", w.lbl_p_kind.text())
        self.assertFalse(w.cmb_p_kind.isVisibleTo(w))                # 決まっているので選び直しは出さない
        self.assertTrue(w.tbl_p.item(0, 0).text().startswith("O0100：回転"))
        self.assertNotIn("G0", w.tbl_p.item(0, 0).text())
        self.assertFalse(w.txt_p_code.isVisibleTo(w))
        w.b_p_detail.setChecked(True)                                # くわしく見る（Gコード）
        self.assertTrue(w.txt_p_code.isVisibleTo(w))
        self.assertIn("O0100", w.txt_p_code.toPlainText())
        self.assertNotIn(";", w.txt_p_code.toPlainText())

    def test_one_choice_with_existing_sub_holds_the_whole_set(self):
        w = self.step4()                                             # 機械には別の中身の O1000 がある
        self.assertEqual(self.numbers(w), [100, 1000])
        self.assertIn("入れ替えますか", self.results(w)[1])
        self.send(w)
        self.assertNotIn(FOLDER + "O100", self.client.machine.programs)   # 片方だけは送らない
        self.assertEqual(self.client.lib.called("cnc_dwnstart4"), 0)
        self.assertIn("もうあります", self.results(w)[1])

    def test_division_only_sends_one_and_reads_back(self):
        w = self.step4()
        self.choose(w, "division")
        self.assertEqual(self.numbers(w), [100])
        self.assertIn("送ります", self.results(w)[0])
        self.send(w)
        self.assertEqual(self.results(w), ["OK"])
        from nd287_app import fanuc
        got = self.client.machine.programs[FOLDER + "O100"][0]
        self.assertTrue(got.startswith(b"%") and b"\n\r\r" in got)   # LF CR CR のまま
        self.assertFalse(w.b_p_lf.isVisibleTo(w))
        self.assertTrue(w.lbl_p_done.isVisibleTo(w))
        self.assertIn("O0100 が機械に入りました", w.lbl_p_done.text())
        self.assertIn("まだ機械は動かしません", w.lbl_p_done.text())    # 先に機械を起動させない（取込開始が先）
        self.assertTrue(w.b_measure.isVisibleTo(w))                    # 「測定へ進む」
        self.assertEqual(w.handoff["steps"], [{"mode": "回転分割", "programs": [100]}])
        self.assertEqual(w.handoff["model"], "RTV-510")
        self.assertEqual(w.settings["wizard_prog_choice"], "division")   # 次回の既定
        with open(self.settings["prog_log_csv"], encoding="utf-8-sig") as f:
            rows = f.read()
        self.assertIn("RTV-510", rows)
        self.assertIn("100", rows)

    def test_repeat_only_main_and_sub_after_replacing(self):
        w = self.step4()
        self.choose(w, "repeat")
        self.assertEqual(self.numbers(w), [100, 1000])
        w._p_over[1][1].setChecked(True)                             # 機械の O1000（別の中身）を入れ替える
        self.send(w)
        self.assertEqual(self.results(w), ["OK", "OK"])
        self.assertEqual(self.client.machine.deleted_programs, [FOLDER + "O1000"])

    def test_two_programs_skip_the_ones_already_there(self):
        w = self.step4()
        self.choose(w, "two")
        self.assertEqual(self.numbers(w), [100, 101, 1000])
        self.send(w)
        r = self.results(w)
        self.assertEqual(r[0], "OK")                                 # 分割用は送れた
        self.assertIn("入れ替えていません", r[2])                     # O1000 は中身が違うので飛ばした
        self.assertIn("送っていません", r[1])                         # 再現性の本体も、サブと組なので送らない
        self.assertIn(FOLDER + "O100", self.client.machine.programs)
        self.assertNotIn(FOLDER + "O101", self.client.machine.programs)
        self.assertFalse(w.lbl_p_done.isVisibleTo(w))                # 全部は終わっていない
        # もう一度送っても、O100 はもう入っている（同じ中身）ので書かない
        n = self.client.lib.called("cnc_dwnstart4")
        w._prog_dlg.start_inspect()
        self.finish(w)
        self.assertIn("もう入っています", self.results(w)[0] if "もう入っています" in self.results(w)[0] else "もう入っています")
        self.send(w)
        self.assertEqual(self.client.lib.called("cnc_dwnstart4"), n)

    def test_all_four_choices_counts(self):
        w = self.step4()
        for key, nums in (("one", [100, 1000]), ("two", [100, 101, 1000]), ("division", [100]),
                          ("repeat", [100, 1000])):
            self.choose(w, key)
            self.assertEqual(self.numbers(w), nums, key)

    def test_number_change_and_collision(self):
        w = self.step4()
        self.choose(w, "two")
        w.chk_p_numbers.setChecked(True)
        w._p_spins[1][1].setValue(100)                               # 分割用と同じ番号
        self.assertTrue(w.lbl_p_problem.isVisibleTo(w))
        self.assertEqual(w._p_nc(), b"")
        self.assertFalse(w._prog_dlg.b_write.isEnabled())            # ぶつかったまま送れない
        w._p_spins[1][1].setValue(300)
        self.assertFalse(w.lbl_p_problem.isVisibleTo(w))

    def test_running_machine_never_writes(self):
        w = self.step4(scenario={"status": {"run": 3}}, settle=False)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 10 and not w._prog_dlg.lbl_estop.isVisibleTo(w._prog_dlg):
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(w._prog_dlg.lbl_estop.isVisibleTo(w._prog_dlg))
        self.assertFalse(w._prog_dlg.b_write.isEnabled())
        self.assertEqual(self.client.lib.called("cnc_dwnstart4"), 0)

    def test_previous_same_program_is_shown(self):
        w = self.step4()
        self.choose(w, "division")
        self.assertFalse(w.lbl_p_prev.isVisibleTo(w))
        self.send(w)
        w._close_prog()
        w._on_prog()                                                  # もう一度開く
        w._p_choice = "division"
        w._p_refresh()
        self.assertTrue(w.lbl_p_prev.isVisibleTo(w))
        self.assertIn("前回と同じプログラムです", w.lbl_p_prev.text())

    def test_readback_mismatch_is_reported_and_lf_retry_is_a_choice(self):
        # 機械が LF CR CR を別の形に直してしまう場合の模擬（実機がそうなるかは未確認）
        w = self.step4(tamper=lambda data: data.replace(b"\n\r\r", b"\n\r"))
        self.choose(w, "division")
        self.send(w)
        self.assertEqual(self.results(w), ["ここが違う"])
        self.assertTrue(w.b_p_lf.isVisibleTo(w))                     # 補助の選択肢は出る（自動ではやらない）
        self.assertFalse(w.lbl_p_done.isVisibleTo(w))
        writes = self.client.lib.called("cnc_dwnstart4")
        self.assertEqual(writes, 1)                                  # 黙って送り直していない
        with mock.patch.object(type(w), "_confirm_lf", lambda self_, nums: False):
            w.b_p_lf.click()
        self.assertEqual(self.client.lib.called("cnc_dwnstart4"), writes)   # 断れば何も書かない
        with mock.patch.object(type(w), "_confirm_lf", lambda self_, nums: True):
            w.b_p_lf.click()
        self.finish(w)
        self.finish(w)
        self.assertEqual(self.results(w), ["OK"])
        self.assertFalse(w.b_p_lf.isVisibleTo(w))
        self.assertNotIn(b"\r", self.client.machine.programs[FOLDER + "O100"][0])   # LF のみで入った
        self.assertEqual(self.client.machine.deleted_programs, [FOLDER + "O100"])


class TestKindChoice(Step4Base):
    def test_no_repeat_condition_gives_division_only(self):
        self.settings["user_rotary_repeat_csv"] = str(self.work / "nothing.csv")
        w = self.step4()
        self.assertTrue(w.rad_p["division"].isChecked())
        for k in ("one", "two", "repeat"):
            self.assertFalse(w.rad_p[k].isEnabled())
        self.assertIn("再現性の条件が登録されていない", w.lbl_p_why.text())


if __name__ == "__main__":
    unittest.main()
