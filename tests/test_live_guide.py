"""測っているあいだの実況と見張り・かかる時間を覚える（live_guide.py・live_guide_ui.py）。"""
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app import live_guide as LG


class TestTimeBook(unittest.TestCase):
    def setUp(self):
        self.p = Path(tempfile.mkdtemp()) / LG.TIME_FILE

    def test_usual_is_same_model_mode_and_points_only(self):
        self.assertEqual(LG.usual_seconds(LG.load_times(self.p), "A", "回転分割", 96), (None, 0))
        for sec in (600, 660, 3000):
            LG.add_time(self.p, model="a", mode="回転分割", points=96, seconds=sec)
        LG.add_time(self.p, model="A", mode="回転再現性", points=96, seconds=10)
        LG.add_time(self.p, model="B", mode="回転分割", points=96, seconds=10)
        LG.add_time(self.p, model="A", mode="回転分割", points=50, seconds=10)
        self.assertEqual(LG.usual_seconds(LG.load_times(self.p), "A", "回転分割", 96), (660.0, 3))   # 真ん中の値（長い1回に引かれない）

    def test_nothing_is_recorded_without_model_or_time(self):
        self.assertFalse(LG.add_time(self.p, model="", mode="x", points=5, seconds=10))
        self.assertFalse(LG.add_time(self.p, model="A", mode="x", points=0, seconds=10))
        self.assertFalse(LG.add_time(self.p, model="A", mode="x", points=5, seconds=0))
        self.assertFalse(self.p.exists())

    def test_minutes(self):
        self.assertEqual(LG.minutes_text(30), "1 分より短い")
        self.assertEqual(LG.minutes_text(700), "約 12 分")

    def test_remaining(self):
        self.assertIsNone(LG.remaining(10, 2, 96))                     # 3 点までは言わない
        self.assertEqual(LG.remaining(30, 3, 9), 60.0)
        self.assertEqual(LG.remaining(999, 10, 20, intervals=[5, 5, 5, 300]), 50.0)   # 途中で止まっても狂わない


class TestWatch(unittest.TestCase):
    def test_step_over_the_single_spec(self):
        t = [0.0, 10.0, 20.0]
        m = [0.0, 10.0 + 2 / 3600, 20.0 + 9 / 3600]                    # ずれ 0, +2, +9 秒
        text = LG.step_watch(t, m, 5)
        self.assertIn("10° から 20°", text)
        self.assertIn("7.0 秒", text)
        self.assertIn("続けて大丈夫", text)
        self.assertEqual(LG.step_watch(t[:2], m[:2], 5), "")            # +2 は規格の中
        self.assertEqual(LG.step_watch(t, m, None), "")                 # 規格が無ければ何も言わない
        self.assertEqual(LG.step_watch(t, [0.0, 10.0, 20.0 + 4.9 / 3600], 5), "")   # 規格の中は言わない
        self.assertEqual(LG.count_steps_over(t, m, 5), 1)

    def test_wrap_at_360(self):
        self.assertEqual(LG.step_watch([350.0, 360.0], [350.0, 0.0002], 5), "")

    def test_stall_needs_history_and_a_long_gap(self):
        self.assertEqual(LG.stall_watch(500, [6, 6, 6]), "")            # 間隔が5つたまるまで言わない
        iv = [6, 7, 6, 8, 7]
        self.assertEqual(LG.stall_watch(29, iv), "")
        self.assertEqual(LG.stall_watch(28.5, [2] * 6), "")             # 短い間隔でも 30 秒までは言わない
        text = LG.stall_watch(95, iv)
        self.assertIn("95 秒", text)
        self.assertIn("約 7 秒ごと", text)
        for word in ("故障", "原因は"):
            self.assertNotIn(word, text)

    def test_narration(self):
        n = LG.narration("wheel_ccw", 0, 96, usual=700, usual_n=3)
        self.assertEqual(n["count"], "0 ／ 96 点")
        self.assertIn("約 12 分", n["remain"])
        self.assertIn("3 回の記録", n["usual"])
        self.assertIn("バックラッシ", n["note"])
        n = LG.narration("wheel_cw", 1, 96)
        self.assertIn("3 点測ると", n["remain"])
        self.assertIn("まだありません", n["usual"])


class TestScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import copy
        from PySide6 import QtWidgets
        from nd287_app import settings as S, nd287, live_guide_ui as LU
        cls.tmp = Path(tempfile.mkdtemp())
        cls._orig = S.settings_path
        S.settings_path = lambda: cls.tmp / "settings.json"
        cls._sleep = nd287.time.sleep
        nd287.time.sleep = lambda s: None
        cls.LU = LU
        cls._tp = LU.time_path
        LU.time_path = lambda s=None: cls.tmp / LG.TIME_FILE
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        import nd287_app.gui as G
        st = copy.deepcopy(S.DEFAULTS)
        st.update(focas_auto_find=False, save_root=str(cls.tmp / "save"), bs_save_root=str(cls.tmp / "bs"),
                  webapp_sync_enabled=False)
        cls.w = G.MainWindow(nd287.DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        cls.w.show()

    @classmethod
    def tearDownClass(cls):
        from nd287_app import settings as S, nd287
        cls.w.close()
        S.settings_path = cls._orig
        nd287.time.sleep = cls._sleep
        cls.LU.time_path = cls._tp

    def test_panel_only_while_measuring_and_time_is_kept(self):
        w = self.w
        live = w.better.live
        clock = [100.0]
        live.clock = lambda: clock[0]
        w.better.tick()
        self.assertTrue(live.panel.isHidden())
        min_h = w.minimumSizeHint().height()
        w.e_model.setText("RWE-200")
        w.on_model_entered()
        w.e_machine.setText("12345")
        w.e_operator.setText("YU")
        w.e_temp.setText("23.5")
        w.start()
        self.app.processEvents()
        w.better.tick()
        self.assertFalse(live.panel.isHidden())
        self.assertIn("まだ値が入っていません", live.panel.lbl_ok.text())
        for _ in range(8):
            w.take_manual()
            clock[0] += 6
            w.better.tick()
        self.assertEqual(live.panel.lbl_count.text(), f"8 ／ {len(w.seq)} 点")
        self.assertIn("あと", live.panel.lbl_remain.text())
        self.assertTrue(live.panel.lbl_stall.isHidden())
        clock[0] += 200
        w.better.tick()
        self.assertIn("次の値が来ていません", live.panel.lbl_stall.text())
        self.assertLessEqual(w.minimumSizeHint().height(), max(min_h, 640))     # 札を出しても、窓の最低の高さを上げない
        idx = w.seq.idx                                                         # 見張りは測定に触らない
        w.better.tick()
        self.assertEqual(w.seq.idx, idx)
        live.keep_dummy = True                                                  # 本物の表示器のつもりで、時間が残るか
        while not w.seq.done():
            w.take_manual()
            clock[0] += 6
            w.better.tick()
        w.better.tick()
        live.keep_dummy = False
        self.assertTrue(live.panel.isHidden())
        rows = LG.load_times(self.LU.time_path())
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["型式"], rows[0]["測り方"]), ("RWE-200", w.current_mode()))
        w.better.tick()
        self.assertEqual(len(LG.load_times(self.LU.time_path())), 1)            # 2回は残さない


if __name__ == "__main__":
    unittest.main()
