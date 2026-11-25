# -*- coding: utf-8 -*-
"""測定の画面の上の2段（2026-11-25 作り直し・案A。nd287_app/main_top.py）。

利用者: 「上のあたりは後から無理やり足した感じで見づらい」。作り直す前は、幅 1366 の画面で
「設定」「ヘルプ」が右にはみ出して見えなかった。ここで見ているのは:
- 右上の3つ（はじめてモード・設定・ヘルプ）が、狭い幅・大きい字でも必ず窓の中にあること
- 入りきらないボタンは「その他」の中へ移り、無くならないこと
- 帯の文は短い文（short）で、長い文（text）は残っていること
"""
import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nd287_app import flow_guide


class TestShortText(unittest.TestCase):
    def test_every_case_has_short_and_full_text(self):
        cases = [
            dict(),
            dict(machine_host="192.168.1.1"),
            dict(machine_done=True),
            dict(machine_done=True, nd_ok=True, missing=("型式", "機番")),
            dict(machine_done=True, nd_ok=True, mode="回転分割", programs=(100,)),
            dict(capturing=True, count=(3, 96)),
            dict(finished=True, ng=5, has_spec=True),
            dict(finished=True, has_spec=True),
            dict(finished=True),
            dict(finished=True, saved=True, next_mode="回転再現性"),
            dict(finished=True, saved=True, saved_path="x.BS"),
        ]
        for kw in cases:
            g = flow_guide.main_flow(**kw)
            self.assertTrue(g["short"], kw)
            self.assertTrue(g["text"], kw)
            self.assertLessEqual(len(g["short"]), len(g["text"]) + 5, kw)
            self.assertLessEqual(len(g["short"]), 80, kw)

    def test_short_keeps_the_numbers(self):
        g = flow_guide.main_flow(capturing=True, count=(3, 96), programs=(100,))
        self.assertIn("3／96", g["short"])
        self.assertIn("O0100", g["short"])
        g = flow_guide.main_flow(finished=True, ng=5, has_spec=True)
        self.assertIn("5 か所", g["short"])


class TestTopRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _window(self, width, pt=None):
        from PySide6 import QtWidgets
        from nd287_app import gui as G
        from nd287_app import themes
        from nd287_app.nd287 import DummyDevice
        from nd287_app.settings import DEFAULTS
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self._font = self.app.font()
        self._qss = self.app.styleSheet()
        themes.apply_font(self.app, pt or themes.DEFAULT_FONT_PT)
        themes.apply_theme(self.app, themes.DEFAULT_THEME)
        st = copy.deepcopy(DEFAULTS)
        st.update(focas_auto_find=False, save_root=str(Path(self._tmp) / "save"), bs_save_root="",
                  webapp_sync_enabled=False)
        w = G.MainWindow(DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        w.flow_timer.stop()
        w.setMaximumSize(5000, 5000)        # 試験の画面は小さい（800）ので、窓の上限を外してから幅を決める
        w.resize(width, 700)
        w.show()
        for _ in range(5):
            self.app.processEvents()
        w._fit_top()
        for _ in range(5):
            self.app.processEvents()
        self.addCleanup(self._close, w)
        return w

    def _close(self, w):
        w.flow_timer.stop()
        w.close()
        w.deleteLater()
        self.app.setFont(self._font)
        self.app.setStyleSheet(self._qss)
        self.app.processEvents()

    def _right_edge(self, w, button):
        return button.mapTo(w, button.rect().topRight()).x()

    def test_groups_are_in_work_order(self):
        w = self._window(1366)
        xs = [self._right_edge(w, b) for b in (w.b_start, w.b_save, w.b_load, w.b_to_machine, w.b_tools, w.b_easy)]
        self.assertEqual(xs, sorted(xs))
        for b in (w.b_start, w.b_auto, w.b_partial, w.b_save, w.b_print, w.b_pdf, w.b_load, w.b_past, w.b_raw,
                  w.b_analysis, w.b_to_machine, w.b_alarm, w.b_tools, w.b_easy):
            self.assertTrue(b.isVisibleTo(w), b.text())

    def test_right_three_stay_inside_the_window(self):
        for width, pt in ((1366, None), (1024, None), (1366, 11), (1024, 14)):
            w = self._window(width, pt)
            for b in w.b_easy.parentWidget().findChildren(type(w.b_easy)):
                self.assertTrue(b.isVisibleTo(w), (width, pt, b.text()))
                self.assertLessEqual(self._right_edge(w, b), w.width(), (width, pt, b.text()))
            self.assertLessEqual(self._right_edge(w, w.b_tools), self._right_edge(w, w.b_easy), (width, pt))
            self._close(w)

    def test_buttons_that_do_not_fit_move_into_the_menu(self):
        w = self._window(1024, 14)
        hidden = [b for b, _a in w._top_overflow if not b.isVisibleTo(w)]
        self.assertTrue(hidden)
        for b, act in w._top_overflow:
            self.assertEqual(act.isVisible(), not b.isVisibleTo(w), b.text())
        texts = [a.text() for a in w.b_tools.menu().actions() if a.isVisible()]
        for b in hidden:
            self.assertIn(b.text() + "…", texts)
        # いつも外に出しておくもの
        for b in (w.b_start, w.b_save, w.b_to_machine, w.b_tools):
            self.assertTrue(b.isVisibleTo(w), b.text())

    def test_menu_keeps_what_left_the_bar(self):
        w = self._window(1366)
        texts = [a.text() for a in w.b_tools.menu().actions()]
        for t in ("測定条件の登録…", "測定条件を種類別に登録…", "ピッチエラー補正…", "型式ごとに一括作成…",
                  "機械と直接つなぐ（FOCAS）…", "はじめての手順を見る…", "作業の案内を出す（上の帯）"):
            self.assertIn(t, texts)

    def test_now_button_follows_the_guide(self):
        w = self._window(1366)
        w._flow_key = None
        w._refresh_flow()
        self.assertTrue(w.b_to_machine.property("now"))
        self.assertFalse(w.b_start.property("now"))
        self.assertEqual(w.flow_steps.stage(), 0)
        w._flow_skip()
        w.e_model.setText("RWE-200"); w.e_machine.setText("T1"); w.e_operator.setText("A"); w.e_temp.setText("23.5")
        w._refresh_flow()
        self.assertTrue(w.b_start.property("now"))
        self.assertFalse(w.b_to_machine.property("now"))
        self.assertEqual(w.lbl_flow.toolTip(), flow_guide.main_flow(**w._flow_inputs())["text"])
        w.act_flow.setChecked(False)                 # 帯を隠したら、色も付けない
        self.assertFalse(w.b_start.property("now"))

    def test_chips_do_not_name_a_unit_from_the_ip(self):
        w = self._window(1366)
        w._focas_watch_host = "192.168.1.1"
        w._refresh_chips()
        self.assertIn("見えています", w.chip_machine.text())
        self.assertNotIn("号機", w.chip_machine.text())
        w._focas_watch_host = ""
        w._refresh_chips()
        self.assertIn("まだ", w.chip_machine.text())


if __name__ == "__main__":
    unittest.main()


class TestLowScreen(TestTopRows):
    """会社PC（1920×1080 を 150% 表示。使える画面 1280×672）で、窓の最低の高さが 924 になり下がはみ出した
    （2026-11-25 実機の画面写真で分かった）。低い画面では、上の2段目を1行ぶんに詰め、測定条件とグラフの
    最低の高さを下げて、窓の最低の高さを画面に入る高さまでにする。"""

    def _low(self, room=640):
        w = self._window(1280)
        w._suffix_fit_pending = False
        if w.suffix_panel.isChecked():
            w.suffix_panel.setChecked(False)      # 低い画面では _fit_suffix_panel が畳む
        w._screen_room = lambda: room
        w._fit_top()
        w._fit_cond_box()
        for _ in range(8):
            self.app.processEvents()
        w._fit_cond_box()
        w._fit_window_height()
        for _ in range(8):
            self.app.processEvents()
        return w

    def test_groups_are_in_work_order(self):
        pass                                       # 親の試験は繰り返さない

    test_right_three_stay_inside_the_window = test_buttons_that_do_not_fit_move_into_the_menu = \
        test_menu_keeps_what_left_the_bar = test_now_button_follows_the_guide = \
        test_chips_do_not_name_a_unit_from_the_ip = test_groups_are_in_work_order

    def test_minimum_height_fits_a_low_screen(self):
        w = self._low(640)
        self.assertLessEqual(w.minimumSizeHint().height(), 640)
        self.assertLess(w.plots_widget.minimumHeight(), 241)
        self.assertGreaterEqual(w.plots_widget.minimumHeight(), 110)

    def test_second_row_is_one_line_high_on_a_low_screen(self):
        w = self._low(640)
        self.assertTrue(w._flow_tight)
        self.assertEqual(w.chip_machine.text(), "機械")
        self.assertIn("つない", w.chip_machine.toolTip())          # 全部の字はマウスを乗せると出る
        self.assertEqual(w.b_flow_skip.text(), "飛ばす")
        w._refresh_flow()
        self.assertNotIn("（", w.b_flow.text())
        self.assertLessEqual(w.flow_bar.sizeHint().height(), w.b_flow.sizeHint().height() * 3)

    def test_tall_screen_keeps_the_usual_sizes(self):
        w = self._low(1000)
        self.assertFalse(w._flow_tight)
        self.assertEqual(w.plots_widget.minimumHeight(), 240)
        self.assertIn("まだつないでいません", w.chip_machine.text())

    def test_empty_result_tables_do_not_leave_small_boxes(self):
        w = self._low(1000)
        w._shrink_result_tables()
        self.assertEqual(w.table_series.rowCount(), 0)
        self.assertEqual(w.table_series.height(), 0)
        self.assertTrue(w.lbl_no_result.isVisibleTo(w))
        w.fill_misc_table([("総合バックラッシ MIN", '-0.9"')])
        self.assertFalse(w.lbl_no_result.isVisibleTo(w))
        self.assertGreater(w.table_misc.height(), 0)

    def test_bottom_strip_is_lifted_when_the_maximized_window_is_taller_than_the_screen(self):
        # 2026-11-25 会社PC: 最大にした窓の中身が画面の使える高さと同じ 672 になり、題名の帯のぶん下の帯が画面の外へ出た
        from PySide6 import QtCore
        w = self._low(640)
        avail = w.screen().availableGeometry()
        w.move(avail.x(), avail.y())
        w.resize(w.width(), avail.height() + 30)
        w.isMaximized = lambda: True
        for _ in range(5):
            self.app.processEvents()
        w._keep_bottom_on_screen()
        for _ in range(8):
            self.app.processEvents()
        over = w.mapToGlobal(QtCore.QPoint(0, w.height())).y() - (avail.y() + avail.height())
        self.assertGreater(over, 1)
        self.assertEqual(w.contentsMargins().bottom(), over)
        sb = w.statusBar()
        self.assertLessEqual(sb.mapToGlobal(QtCore.QPoint(0, sb.height())).y(), avail.y() + avail.height())
        w.isMaximized = lambda: False                 # 最大でなくなったら、あきは戻す
        w._keep_bottom_on_screen()
        self.assertEqual(w.contentsMargins().bottom(), 0)
