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
        dlg._enter(F.INDEX["summary"])
        self.app.processEvents()
        rows = [c for c in dlg.v_summary.cell_labels if c.text().startswith("ピッチ誤差")]
        self.assertEqual(len(rows), 8)
        for c in rows:
            self.assertGreaterEqual(c.height(), 14)                     # 1行5ピクセルに潰れない
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

    def test_now_band_does_not_keep_growing(self):
        """かんたん作成の色の帯の高さが、呼ぶたびに +20 ずつ伸び続けた（会社PC 150% の確認）。何度呼んでも同じ高さ。"""
        import copy
        from nd287_app import gui as G
        from nd287_app.settings import DEFAULTS
        d = G.ParamWizardDialog(None, copy.deepcopy(DEFAULTS), model="RWE-200")
        d.resize(900, 600)
        d.show()
        self.app.processEvents()
        d._set_now("機械の今の中身を、PC に控えています（読むだけ）。待ってください。" * 2, "wait")
        heights = []
        for _ in range(6):
            self.app.processEvents()
            d._set_now("機械の今の中身を、PC に控えています（読むだけ）。待ってください。" * 2, "wait")
            heights.append(d.lbl_now.minimumHeight())
        self.assertEqual(len(set(heights)), 1, heights)
        self.assertLess(heights[0], 120)

    def test_embedded_writer_puts_write_box_up_and_hides_close(self):
        from nd287_app import gui as G
        from nd287_app.settings import DEFAULTS
        import copy
        d = G.FocasWriteDialog(None, copy.deepcopy(DEFAULTS), lambda raw, zero_scope="product": None)
        d.arrange_embedded()
        iv = d._cf_box.parentWidget().layout()
        self.assertEqual(iv.indexOf(d._cf_box), iv.indexOf(d.lbl_target) + 1)   # ③は対象の行のすぐ下
        self.assertTrue(d._bbx.isHidden())                                      # 閉じるは二重なので出さない
        self.assertEqual(d._cl_layout.indexOf(d._vr_widget), 1)
        self.assertEqual(d._cl_layout.indexOf(d._wr_widget), 0)                 # 書くボタンは箱の先頭


if __name__ == "__main__":
    unittest.main()
