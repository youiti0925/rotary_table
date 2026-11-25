"""会社PC(150%)の全画面点検で見つかった、はじめてモードの崩れの再発防止。"""
import os, sys, unittest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))
from PySide6 import QtWidgets
from nd287_app import easy_flow as F, easy_art as A


class TestEasyFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make(self, st):
        from test_easy_mode import FakeHost, FakePoller
        from nd287_app import easy_mode
        host = FakeHost()
        dlg = easy_mode.EasyModeDialog(host, FakePoller(st))
        dlg.resize(1100, 600)
        dlg.show()
        self.app.processEvents()
        return dlg, host

    def test_many_ng_rows_keep_row_height(self):
        st = F.machine_state({"values": {"aut": 0, "run": 0, "emergency": 0, "alarm": 0}}, [], online=True)
        dlg, host = self.make(st)
        dlg._start_flow("measure")
        host.finished, host.ng = True, 8
        host.ng_items = [(f"ピッチ誤差 {i}番目", f"+{i}.5 秒") for i in range(1, 9)]
        dlg._enter(F.INDEX["result"])
        self.app.processEvents()
        sc = dlg._extra_widgets["ng"]
        self.assertGreaterEqual(sc.height(), 100)                       # 1行5ピクセルに潰れない
        host.saved = True
        dlg.close()

    def test_offline_alarm_picture_is_not_a_green_check(self):
        self.assertNotIn(A.GREEN, A.alarm_screen(False, online=False))
        self.assertIn("機械につながっていません", A.alarm_screen(False, online=False))
        self.assertIn(A.GREEN, A.alarm_screen(False, online=True))

    def test_past_result_with_ng_does_not_say_saved(self):
        self.assertNotIn("保存しました", A.verdict(False, saved=False))

    def test_wizard_program_step_has_register_buttons_and_finds_the_host(self):
        import copy, tempfile
        from nd287_app import gui as G
        from nd287_app.settings import DEFAULTS
        host = QtWidgets.QWidget()
        called = []
        host.show_kind_conditions = lambda m="": called.append(("kind", m))
        host.show_condition_editor = lambda: called.append(("cond", ""))
        st = copy.deepcopy(DEFAULTS)
        d = G.ParamWizardDialog(host, st, model="ZZZ-999")
        self.assertIs(d._p_host(), host)
        self.assertFalse(d.box_p_register.isVisibleTo(d))
        d._p_register("cond")
        self.assertEqual(called, [("cond", "")])


if __name__ == "__main__":
    unittest.main()
