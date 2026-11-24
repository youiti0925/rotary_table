# -*- coding: utf-8 -*-
"""「いま何をすればよいか」の案内（flow_guide）と、それを出す画面。

2026-11-24 利用者: 初心者は、どこから触るのか・どの順番かが分からない。アプリが1つずつ案内すること。
機械は偽物だけ。実機の確認ではない。
"""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from nd287_app import flow_guide as F                    # noqa: E402
from nd287_app import wizard_programs as WP               # noqa: E402
from test_focas_write_dialog import WriteDialogBase       # noqa: E402
from test_wizard_step4 import Step4Base                   # noqa: E402


class TestMainFlow(unittest.TestCase):
    """測定の画面の帯: 機械の準備 → 測定の準備 → 測る → 結果 → 保存 の順に、1つだけ出す。"""

    def test_starts_with_the_machine(self):
        g = F.main_flow()
        self.assertEqual((g["stage"], g["action"], g["skip"]), (0, "open_wizard", True))
        self.assertIn("LAN ケーブル", g["text"])
        g = F.main_flow(machine_host="192.168.1.1", machine_unit="23")
        self.assertIn("機械が見えています（192.168.1.1・前回 23号機）", g["text"])

    def test_then_the_readout_then_the_fields(self):
        g = F.main_flow(machine_done=True)
        self.assertEqual((g["stage"], g["kind"], g["action"]), (1, "stop", "reconnect"))
        g = F.main_flow(machine_done=True, nd_ok=True, missing=("型式", "機番"))
        self.assertEqual((g["stage"], g["action"]), (1, "focus"))
        self.assertIn("型式", g["text"])
        self.assertNotIn("測る人", g["text"])

    def test_start_names_the_program_and_puts_capture_first(self):
        g = F.main_flow(machine_done=True, nd_ok=True, programs=(100,), mode="回転分割")
        self.assertEqual((g["stage"], g["action"]), (2, "start"))
        self.assertLess(g["text"].index("取込開始"), g["text"].index("O0100"))   # 取込開始が先、機械の起動があと

    def test_capturing_shows_the_count(self):
        g = F.main_flow(machine_done=True, nd_ok=True, capturing=True, count=(3, 96))
        self.assertEqual((g["stage"], g["kind"], g["button"]), (2, "wait", ""))
        self.assertIn("3／96", g["text"])

    def test_finished_says_how_many_are_out_and_offers_save(self):
        g = F.main_flow(machine_done=True, nd_ok=True, finished=True, ng=5, has_spec=True)
        self.assertEqual((g["kind"], g["action"]), ("stop", "save"))
        self.assertIn("5 か所", g["text"])
        g = F.main_flow(machine_done=True, nd_ok=True, finished=True, ng=0, has_spec=True)
        self.assertEqual((g["kind"], g["action"]), ("do", "save"))
        g = F.main_flow(machine_done=True, nd_ok=True, finished=True, ng=0, has_spec=False)
        self.assertIn("規格が画面に出ていません", g["text"])            # 規格が無いのに「合格」と言わない

    def test_saved_then_next_measurement_or_done(self):
        g = F.main_flow(finished=True, saved=True, saved_path="X.csv")
        self.assertEqual((g["stage"], g["kind"], g["action"]), (4, "done", "new"))
        g = F.main_flow(finished=True, saved=True, next_mode="回転再現性")
        self.assertEqual(g["action"], "next_mode")


class TestWriterGuide(unittest.TestCase):
    """かんたん作成の段③の帯。"""

    def test_cannot_connect_tells_what_to_check(self):
        g = F.writer_guide(state_err="192.168.1.1 に機械が見つかりません")
        self.assertEqual(g["kind"], "stop")
        self.assertIn("LAN ケーブル", g["text"])
        self.assertFalse(g["next"])

    def test_already_same_values_lets_the_user_go_on(self):
        g = F.writer_guide(connected=True, has_backup=True, plan_ready=True, n_items=0,
                           reasons=["書くものがありません（機械はすでに同じ値です）"])
        self.assertEqual((g["kind"], g["next"]), ("done", True))
        g = F.writer_guide(connected=True, has_backup=True, plan_ready=True, n_items=0,
                           reasons=["書くものがありません（機械はすでに同じ値です）", "非常停止です（生の値 1）"])
        self.assertEqual((g["kind"], g["next"]), ("stop", False))     # ほかの理由があれば進めない

    def test_ready_names_the_button(self):
        g = F.writer_guide(connected=True, has_backup=True, plan_ready=True, n_items=12)
        self.assertEqual(g["kind"], "do")
        self.assertIn("この 12 件を機械に書く", g["text"])

    def test_written_then_power_cycle_first(self):
        g = F.writer_guide(connected=True, has_backup=True, plan_ready=True, result_state="done",
                           result_all_ok=True, power_cycle=True)
        self.assertTrue(g["next"])
        self.assertLess(g["text"].index("入れ直して"), g["text"].index("続けてプログラムを送る"))
        g = F.writer_guide(connected=True, has_backup=True, plan_ready=True, result_state="stopped")
        self.assertEqual((g["kind"], g["next"]), ("stop", False))

    def test_two_step_waits_for_power(self):
        g = F.writer_guide(connected=False, stage1_done=True)
        self.assertIn("つなぎ直して続ける", g["text"])
        self.assertFalse(g["next"])

    def test_unverified_type_points_to_the_button(self):
        g = F.writer_guide(connected=True, has_backup=True, plan_ready=True, n_items=3, unverified=1,
                           reasons=["型 X はこの機種ではまだ書いたことがありません（3 件）。先に『同じ値を書く確認』をしてください"])
        self.assertEqual(g["kind"], "stop")
        self.assertIn("進めるようになります", g["text"])


class TestProgramGuide(unittest.TestCase):
    def test_states(self):
        self.assertEqual(F.program_guide(busy=True)["kind"], "wait")
        self.assertIn("『送る』", F.program_guide(connected=True, has_backup=True)["text"])
        self.assertIn("入れ替える", F.program_guide(connected=True, has_backup=True, differs=1)["text"])
        self.assertEqual(F.program_guide(connected=True, has_backup=True, mismatch=True)["kind"], "stop")
        self.assertEqual(F.program_guide(done_text="入りました")["kind"], "done")


class TestHandoff(unittest.TestCase):
    MASTERS = {"conditions": {"RTV-510": {"x": 1}}}

    def test_model_spelling_follows_the_master(self):
        # 登録に載っている名前にする（製品データの「RTV-510,WA」→ 登録の「RTV-510」）
        self.assertEqual(WP.master_model(self.MASTERS, "RTV-510,WA", False), "RTV-510")
        self.assertEqual(WP.master_model(self.MASTERS, "RTV-510", False), "RTV-510")
        self.assertEqual(WP.master_model(self.MASTERS, "RTV-510Ri", False), "RTV-510")   # 末尾の英字を外して当たる
        self.assertEqual(WP.master_model(self.MASTERS, "ZZZ-1", False), "")     # 当たらなければ書き換えない

    def test_two_programs_are_measured_in_two_steps(self):
        plan = {"tilt": False, "choice": "two", "programs": [{"numbers": [100]}, {"numbers": [101, 1000]}]}
        h = WP.handoff(self.MASTERS, "RTV-510", plan)
        self.assertEqual(h["steps"], [{"mode": "回転分割", "programs": [100]},
                                      {"mode": "回転再現性", "programs": [101]}])
        plan = {"tilt": True, "choice": "one", "programs": [{"numbers": [100, 1000]}]}
        self.assertEqual(WP.handoff({}, "X", plan)["steps"], [{"mode": "傾斜分割+再現", "programs": [100]}])


class TestWriterBand(WriteDialogBase):
    """書く窓の guide_state（本物の窓・偽物の機械）。"""

    def test_from_connect_to_written(self):
        d = self.dialog()
        self.assertIn("『つなぐ』", d.guide_state()["text"])
        self.go(d)
        g = d.guide_state()
        self.assertEqual(g["kind"], "do")
        self.assertIn(d.b_write.text(), g["text"])
        d.b_write.click()
        self.wait(d)
        g = d.guide_state()
        self.assertTrue(g["next"])
        self.assertIn("機械に書けました", g["text"])

    def test_machine_already_has_the_values(self):
        d = self.dialog()
        self.go(d)
        d.b_write.click()
        self.wait(d)
        d.b_backup.click()                       # 控えを取り直す = もう一度つないだのと同じ状態
        self.wait(d)
        self.assertFalse(d.b_write.isEnabled())
        g = d.guide_state()
        self.assertEqual((g["kind"], g["next"]), ("done", True))
        self.assertIn("もう同じ値", g["text"])


class TestWizardBands(Step4Base):
    def test_step3_band_and_program_button_when_nothing_to_write(self):
        w = self.ready(self.wizard(), motor="product")
        self.ws.append(w)
        self.assertFalse(w._writer_auto)                     # 試験で作った画面は、勝手につながない

        class FakeWriter:
            def guide_state(s):
                return {"kind": "done", "text": "機械には、もう同じ値が入っています。", "next": True}
        w._writer = FakeWriter()
        w._step = 3
        w._apply_mode()
        w._tick_now()
        self.assertTrue(w.lbl_now.isVisibleTo(w))
        self.assertIn("もう同じ値", w.lbl_now.text())
        self.assertTrue(w.b_prog.isVisibleTo(w))             # 書くものが無くても、プログラムへ進める
        w._writer = None

    def test_step4_same_programs_need_no_send(self):
        w = self.step4()
        self.choose(w, "division")
        self.assertIsNone(w.handoff)
        self.assertFalse(w.b_measure.isVisibleTo(w))
        w._tick_now()
        self.assertIn("『送る』", w.lbl_now.text())
        self.send(w)
        self.assertIsNotNone(w.handoff)
        w._prog_client_factory = lambda s: self.client        # 同じ機械にもう一度入る
        w._on_prog()
        w._prog_dlg._timer.setInterval(600000)
        self.settle(w)
        self.choose(w, "division")
        self.assertIn("もう同じ中身で機械に入っています", w.lbl_p_done.text())
        self.assertTrue(w.b_measure.isVisibleTo(w))
        self.assertEqual(w.handoff["steps"][0]["programs"], [100])


class TestMainWindowBar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from nd287_app import settings as S
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path = S.settings_path
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"

    @classmethod
    def tearDownClass(cls):
        import shutil
        from nd287_app import settings as S
        S.settings_path = cls._orig_path
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        from PySide6 import QtWidgets
        from nd287_app import gui as G
        from nd287_app.nd287 import DummyDevice
        from nd287_app.settings import DEFAULTS
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        st = copy.deepcopy(DEFAULTS)
        st.update(focas_auto_find=False, save_root=str(Path(self._tmp) / "save"), bs_save_root="",
                  webapp_sync_enabled=False)
        self.w = G.MainWindow(DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        self.w.flow_timer.stop()
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        self.w.flow_timer.stop()
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()

    def test_walk_from_start_to_measured(self):
        w = self.w
        w._refresh_flow()
        self.assertIn("機械に入れる", w.b_flow.text())
        self.assertTrue(w.b_flow_skip.isVisibleTo(w))
        w._flow_skip()
        self.assertEqual(w._flow_action, "focus")
        w._flow_do()
        w.e_model.setText("RWE-200")
        w.e_machine.setText("T1")
        w.e_operator.setText("A")
        w.e_temp.setText("23.5")
        w._refresh_flow()
        self.assertEqual(w._flow_action, "start")
        w._flow_do()                                         # 取込開始
        self.assertIsNotNone(w.seq)
        self.assertIn("測っています", w.lbl_flow.text())
        n = 0
        while not w.seq.done() and n < 5000:
            w.take_manual()
            n += 1
        w._refresh_flow()
        self.assertEqual(w._flow_action, "save")
        self.assertIn("測り終わりました", w.lbl_flow.text())

    def test_handoff_sets_model_and_mode(self):
        w = self.w
        w._apply_wizard_handoff({"model": "RWE-200", "steps": [
            {"mode": "回転分割", "programs": [100]}, {"mode": "回転再現性", "programs": [101]}]})
        self.assertTrue(w._flow_machine_done)
        self.assertEqual(w.e_model.text(), "RWE-200")
        self.assertEqual(w.current_mode(), "回転分割")
        g = w._flow_inputs()
        self.assertEqual((g["programs"], g["next_mode"]), ((100,), "回転再現性"))

    def test_loaded_result_is_not_called_measured(self):
        w = self.w
        w._flow_skip()
        w._flow_measured = False
        self.assertFalse(w._flow_inputs()["finished"])

    def test_bar_can_be_hidden_from_the_tools_menu(self):
        w = self.w
        w.act_flow.setChecked(False)
        self.assertFalse(w.flow_bar.isVisibleTo(w))
        self.assertFalse(w.settings["show_flow_guide"])
        w.act_flow.setChecked(True)
        self.assertTrue(w.flow_bar.isVisibleTo(w))


if __name__ == "__main__":
    unittest.main()
