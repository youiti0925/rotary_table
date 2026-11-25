"""結果を読む（result_guide.py・result_guide_ui.py・better_app.py）。

守ること: 新しい合否の決め方を作らない（規格と比べ方は画面の表と同じ）・原因を推測で言わない・
記録帳に無いことを「次にやること」に出さない。
"""
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from nd287_app import result_guide as RG
from nd287_app.analysis import adjacent_peak, single_peak, slope_corrected_pp

T = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
CW = [0.0, 1.0, 8.0, 2.0, 1.0, 3.0]       # 10→20 で +7（単一 7）、20 の所がでっぱり（隣接 13）、傾き +3
CCW = [0.0, 1.0, 2.0, 1.0, 0.0, 1.0]
DEVS = {"wheel_cw": (T, CW), "wheel_ccw": (T, CCW)}
LIMITS = {("wheel", "single"): 5, ("wheel", "adjacent"): 10, ("wheel", "slope"): 4, ("wheel", "pp"): None}


class TestLessons(unittest.TestCase):
    def test_values_are_the_same_as_the_table(self):
        """値は analysis と同じ計算（画面の表と同じ）。"""
        m = RG.metric_values(CW)
        self.assertEqual(m["pp"], 8.0)
        self.assertEqual(m["single"], single_peak(np.array(CW))[2])
        self.assertEqual(m["adjacent"], adjacent_peak(np.array(CW))[1])
        self.assertEqual(m["slope"], 3.0)

    def test_over_is_abs_greater_than_limit_only(self):
        """外れは「絶対値が規格を超えた」だけ。ちょうどは外れではない。規格が無ければ何も言わない。"""
        self.assertIs(RG.is_over(5.0, 5), False)
        self.assertIs(RG.is_over(-5.1, 5), True)
        self.assertIsNone(RG.is_over(99, None))
        self.assertIsNone(RG.is_over(99, 0))

    def test_one_card_per_metric_worst_series_and_ng_first(self):
        ls = RG.lessons(DEVS, LIMITS)
        self.assertEqual(len(ls), 4)                           # 精度・単一・隣接・傾き を1枚ずつ
        self.assertEqual([le["over"] for le in ls[:2]], [True, True])
        by = {le["metric"]: le for le in ls}
        self.assertEqual(by["single"]["series"], "wheel_cw")
        self.assertEqual(by["single"]["value"], 7.0)
        self.assertIn("10°", by["single"]["lines"][0])
        self.assertIn("20°", by["single"]["lines"][0])
        self.assertEqual(by["adjacent"]["value"], 13.0)
        self.assertIs(by["slope"]["over"], False)
        self.assertIsNone(by["pp"]["over"])                    # 規格が画面に無い項目は、合格とも外れとも言わない
        self.assertFalse(any("規格の中" in x or "外れています" in x for x in by["pp"]["lines"]))

    def test_no_guess_words(self):
        """原因を言い切らない（事実と、言葉の意味だけ）。"""
        text = " ".join(x for le in RG.lessons(DEVS, LIMITS) for x in le["lines"])
        for word in ("原因は", "のせいです", "故障", "不良です"):
            self.assertNotIn(word, text)

    def test_backlash_card_uses_table_text_as_is(self):
        rows = [("総合バックラッシ MIN", '-0.9"　規格 ≧9.9"　NG'), ("総合バックラッシ MAX", '15.1"　規格 ≦49.5"　OK'),
                ("総合バックラッシ 差", '16.0"')]
        le = RG.backlash_lesson(rows)
        self.assertIs(le["over"], True)
        self.assertEqual(le["value_text"], "-0.9 〜 15.1 秒")
        self.assertTrue(any("-0.9 秒" in x and "外れ" in x for x in le["lines"]))
        self.assertIsNone(RG.backlash_lesson([("総合バックラッシ MIN", '3.0"')])["over"])     # 規格なし
        self.assertIsNone(RG.backlash_lesson([("主点精度", '1.0"')]))

    def test_over_kinds_adds_table_only_items(self):
        ls = RG.lessons(DEVS, LIMITS)
        kinds = RG.over_kinds(ls, [("総合バックラッシ MIN", "-0.9"), ("ホイール CW 単一誤差", "7.0")])
        self.assertEqual(kinds, ["ホイール 単一誤差", "ホイール 隣接誤差", "総合バックラッシ"])


class TestUsual(unittest.TestCase):
    def test_needs_five_results(self):
        stage, text = RG.compare(3.0, RG.usual([1, 2, 3, 4]))
        self.assertEqual(stage, "few")
        self.assertIn("4 件", text)
        self.assertEqual(RG.compare(3.0, RG.usual([]))[0], "few")

    def test_stages(self):
        u = RG.usual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(RG.compare(5, u)[0], "same")
        self.assertEqual(RG.compare(9.5, u)[0], "high")
        self.assertEqual(RG.compare(11, u)[0], "highest")
        self.assertEqual(RG.compare(1.0, u)[0], "low")

    def test_own_machine_is_not_compared(self):
        recs = [{"機番": "12345KC", "metrics": {"ホイール CW 精度PP": 9.0}},
                {"機番": "123456", "metrics": {"ホイール CW 精度PP": 3.0}},
                {"機番": "22222", "metrics": {"ホイール CW 傾き": -2.0}}]
        self.assertEqual(RG.past_values(recs, "ホイール CW 精度PP", exclude_machine="12345"), [3.0])
        self.assertEqual(RG.past_values(recs, "ホイール CW 傾き"), [2.0])        # 傾きは大きさで比べる

    def test_rows_use_the_saved_calculation(self):
        rows = RG.usual_rows(DEVS, [])
        self.assertAlmostEqual(rows[0]["value"], slope_corrected_pp(np.array(CW)))
        self.assertEqual(rows[0]["stage"], "few")


class TestFixBook(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_only_recorded_things_are_shown(self):
        p = self.tmp / RG.FIX_FILE
        steps = RG.next_steps(["ホイール 単一誤差"], RG.load_fixes(p), [])
        self.assertEqual(steps[0]["before"], [])
        self.assertTrue(any("まだありません" in x for x in RG.step_lines(steps[0])))
        RG.add_fix(p, model="A", machine="1", kinds=["ホイール 単一誤差", "ホイール 隣接誤差"], did="測定器を付け直した")
        RG.add_fix(p, model="A", machine="2", kinds=["ホイール 単一誤差"], did="測定器を付け直した")
        RG.add_fix(p, model="A", machine="3", kinds=["ホイール 単一誤差"], did="製品を調整した")
        st, total = RG.fix_stats(RG.load_fixes(p), "ホイール 単一誤差")
        self.assertEqual((st[0], total), (("測定器を付け直した", 2), 3))
        lines = RG.step_lines(RG.next_steps(["ホイール 単一誤差"], RG.load_fixes(p), [])[0])
        self.assertTrue(any("測定器を付け直した（3 回のうち 2 回）" in x for x in lines))
        self.assertEqual(RG.fix_stats(RG.load_fixes(p), "ウォーム 傾き"), ([], 0))

    def test_tips(self):
        p = self.tmp / RG.TIP_FILE
        self.assertTrue(RG.tips_for(RG.load_tips(p), "ホイール 傾き"))            # 初めから入っている手引き
        RG.add_tip(p, "総合バックラッシ", "ウォームの当たりを見てください。", who="YU")
        self.assertEqual(RG.tips_for(RG.load_tips(p), "総合バックラッシ"), ["ウォームの当たりを見てください。"])

    def test_tracker_finds_what_got_fixed(self):
        t = RG.Tracker()
        self.assertEqual(t.observe("12345", ["ホイール 単一誤差", "総合バックラッシ"]), [])
        self.assertEqual(t.observe("99999", []), [])                               # 別の機番は関係ない
        self.assertEqual(t.observe("12345-X1", ["総合バックラッシ"]), ["ホイール 単一誤差"])
        self.assertEqual(t.observe("12345", ["総合バックラッシ"]), [])
        self.assertEqual(t.observe("", ["x"]), [])


class TestScreen(unittest.TestCase):
    """本物の測定の画面で開く（ダミーの表示器で1回測る）。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import copy
        from PySide6 import QtWidgets
        from nd287_app import settings as S, nd287
        cls.tmp = Path(tempfile.mkdtemp())
        cls._orig = S.settings_path
        S.settings_path = lambda: cls.tmp / "settings.json"
        cls._sleep = nd287.time.sleep
        nd287.time.sleep = lambda s: None
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        import nd287_app.gui as G
        from nd287_app import result_guide_ui as U
        cls.U = U
        cls._fix = U.fix_path
        U.fix_path = lambda s=None: cls.tmp / RG.FIX_FILE
        st = copy.deepcopy(S.DEFAULTS)
        st.update(focas_auto_find=False, save_root=str(cls.tmp / "save"), bs_save_root=str(cls.tmp / "bs"),
                  webapp_sync_enabled=False, result_tips_csv=str(cls.tmp / "tips.csv"))
        cls.w = G.MainWindow(nd287.DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        cls.w.show()

    @classmethod
    def tearDownClass(cls):
        from nd287_app import settings as S, nd287
        cls.w.close()
        S.settings_path = cls._orig
        nd287.time.sleep = cls._sleep
        cls.U.fix_path = cls._fix

    def pump(self, n=10):
        for _ in range(n):
            self.app.processEvents()

    def measure(self, machine="12345"):
        w = self.w
        w.e_model.setText("RWE-200")
        w.on_model_entered()
        w.e_machine.setText(machine)
        w.e_operator.setText("YU")
        w.e_temp.setText("23.5")
        w.start()
        self.pump()
        while w.seq is not None and not w.seq.done():
            w.take_manual()
        self.pump(20)

    def test_1_button_is_in_the_group_and_off_until_there_is_a_result(self):
        w = self.w
        w.better.tick()
        self.assertFalse(w.b_read.isEnabled())
        self.assertIn(w.b_read, [b for b, _a in w._top_overflow])      # 幅が足りなければ「その他」へ移る（無くならない）

    def test_2_after_measuring_the_window_matches_the_red_cells(self):
        w = self.w
        self.measure()
        w.better.tick()
        self.assertTrue(w.b_read.isEnabled())
        ng_names = [n for n, _v in w._flow_ng_items()]
        self.assertEqual(bool(w.b_read.property("now")), bool(ng_names))
        d = self.U.ResultGuideDialog(w, search=lambda *a, **k: [])
        d.show()
        self.pump(20)
        # 札の「外れ」は、表の赤い字と同じ（札で外れた項目は、必ず表でも赤い）
        for le in d._lessons:
            if le["over"] and le["metric"] != "backlash":
                label = RG.METRIC_NAME[le["metric"]]
                self.assertTrue(any(label in n or (label == "精度" and "精度" in n) for n in ng_names), le["title"])
        # 表の赤い字の項目は、全部「次にやること」に出る
        for n, v in w._flow_ng_items():
            k = RG.kind_of_item(n) or RG.kind_of_item(v)
            if k:
                self.assertIn(k, d._kinds)
        for i in range(len(d._lessons)):
            d.show_lesson(i)
        d.show_page(1)
        d.show_page(2)
        self.pump(10)
        d.close()

    def test_3_fixed_kinds_are_asked_once_and_recorded(self):
        w = self.w
        if not w.has_view_data():
            self.measure()
        d = self.U.ResultGuideDialog(w, fixed_kinds=["ウォーム 傾き"], search=lambda *a, **k: [])
        d.show()
        self.pump(10)
        d._record_fix(RG.FIX_CHOICES[1])
        rows = RG.load_fixes(self.U.fix_path())
        self.assertEqual(rows[-1]["外れた項目"], "ウォーム 傾き")
        self.assertEqual(rows[-1]["やったこと"], RG.FIX_CHOICES[1])
        self.assertEqual(rows[-1]["型式"], "RWE-200")
        d.close()
        self.assertFalse((Path(__file__).resolve().parents[1] / RG.FIX_FILE).exists())    # アプリのフォルダを汚さない


if __name__ == "__main__":
    unittest.main()
