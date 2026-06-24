# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from nd287_app.ks_format import (
    data_to_doc,
    doc_to_data,
    format_ks,
    parse_ks,
    tilt_accuracy,
)

FIXTURE = Path(__file__).parent / "fixtures" / "261166ITY.ks"


class TestParseKs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = FIXTURE.read_text(encoding="utf-8")
        cls.doc = parse_ks(cls.text)

    def test_header(self):
        doc = self.doc
        self.assertEqual(doc["model"], "TWA-200")
        self.assertEqual(doc["operator"], "ODA")
        self.assertEqual(doc["start_h"], -1800000)   # -180°
        self.assertEqual(doc["end_h"], 1800000)      # +180°
        self.assertEqual(doc["interval_h"], 50000)   # 5°
        self.assertEqual(doc["range1_start"], 0)     # 評価範囲1 = 0〜90°
        self.assertEqual(doc["range1_end"], 900000)
        self.assertEqual(doc["points_h"], 72)
        self.assertEqual(doc["points_w"], 10)
        self.assertEqual(doc["order"], [1, 3, 4, 2])

    def test_row_counts(self):
        self.assertEqual(len(self.doc["rows"]["wheel"]), 73)
        self.assertEqual(len(self.doc["rows"]["worm"]), 11)

    def test_row_has_signed_command_and_deviations(self):
        # 先頭行: 指令-180°、CW測定179°59'54.5"、CW偏差-5.5"、CCW偏差+7.5"
        row = self.doc["rows"]["wheel"][0]
        self.assertEqual(row[0], 180.0)    # カウンタ表示
        self.assertEqual(row[1], -180.0)   # 符号付指令
        self.assertEqual((row[2], row[3], row[4]), (179.0, 59.0, 54.5))
        self.assertEqual(row[8], -5.5)
        self.assertEqual(row[9], 7.5)

    def test_roundtrip_byte_identical(self):
        self.assertEqual(format_ks(self.doc, newline="\n"), self.text)


class TestTiltAccuracyParity(unittest.TestCase):
    """261166ITY.KS: 旧アプリ表示（精度H/精度W/精度 × 正逆 × 全範囲・範囲1）と一致"""

    @classmethod
    def setUpClass(cls):
        cls.doc = parse_ks(FIXTURE.read_text(encoding="utf-8"))
        cls.data = doc_to_data(cls.doc)

    def test_full_range(self):
        acc = tilt_accuracy(self.data)
        self.assertEqual((acc["cw"]["h"], acc["cw"]["w"], acc["cw"]["total"]),
                         (18.0, 2.0, 20.0))
        self.assertEqual((acc["ccw"]["h"], acc["ccw"]["w"], acc["ccw"]["total"]),
                         (15.5, 4.5, 20.0))

    def test_partial_range1(self):
        # 評価範囲1 = 0〜90°（客先要求の部分抜き出し評価）
        acc = tilt_accuracy(self.data, range_=(0.0, 90.0))
        self.assertEqual((acc["cw"]["h"], acc["cw"]["w"], acc["cw"]["total"]),
                         (7.0, 2.0, 9.0))
        self.assertEqual((acc["ccw"]["h"], acc["ccw"]["w"], acc["ccw"]["total"]),
                         (6.0, 4.5, 10.5))

    def test_partial_range2(self):
        # 評価範囲2 = -30〜+90°（旧アプリ画面の開始角度2/終了角度2で確認）
        acc = tilt_accuracy(self.data, range_=(-30.0, 90.0))
        self.assertEqual((acc["cw"]["h"], acc["cw"]["w"], acc["cw"]["total"]),
                         (7.5, 2.0, 9.5))
        self.assertEqual((acc["ccw"]["h"], acc["ccw"]["w"], acc["ccw"]["total"]),
                         (7.0, 4.5, 11.5))

    def test_detrend_flags_default_is_legacy(self):
        # 既定（detrend_h=False, detrend_w=True）は旧アプリ・.KS と同じ
        acc = tilt_accuracy(self.data, detrend_h=False, detrend_w=True)
        self.assertEqual(acc["cw"]["total"], 20.0)
        self.assertEqual(acc["ccw"]["total"], 20.0)

    def test_before_after_differ(self):
        # 補正前(素H+素W) と 補正後(補正H+補正W) で任意誤差が変わる（画面トグル連動）
        before = tilt_accuracy(self.data, detrend_h=False, detrend_w=False)
        after = tilt_accuracy(self.data, detrend_h=True, detrend_w=True)
        self.assertEqual(before["ccw"]["total"], 19.5)   # 素15.5 + 素4.0
        self.assertEqual(after["ccw"]["total"], 20.5)    # 補正16.0 + 補正4.5
        self.assertNotEqual(before["ccw"]["total"], after["ccw"]["total"])

    def test_data_series(self):
        self.assertEqual(len(self.data["wheel_cw"][0]), 73)
        self.assertEqual(self.data["wheel_cw"][0][0], -180.0)
        self.assertEqual(self.data["wheel_cw"][0][-1], 180.0)
        # CCWは測定順（降順）
        self.assertEqual(self.data["wheel_ccw"][0][0], 180.0)
        self.assertEqual(len(self.data["worm_cw"][0]), 11)


class TestCompositeBacklash(unittest.TestCase):
    """総合バックラッシ（0°合わせ）: 旧アプリのMAX/MIN表示と一致すること

    ホイールBLを点間補間し、ウォームBL周期成分を乗せ、ウォーム0°位置の
    状態をホイール0°位置の状態に合わせる（差ぶんシフト）。
    """

    @classmethod
    def setUpClass(cls):
        from nd287_app.analysis import composite_backlash_minmax
        from nd287_app.ks_format import _round_tenth
        cls.calc = staticmethod(composite_backlash_minmax)
        cls.disp = staticmethod(_round_tenth)
        cls.data = doc_to_data(parse_ks(FIXTURE.read_text(encoding="utf-8")))

    def test_full_range(self):
        mm = self.calc(self.data)
        self.assertEqual((self.disp(mm[1]), self.disp(mm[0])), (24.2, 9.4))

    def test_range1(self):
        mm = self.calc(self.data, range_=(0.0, 90.0))
        self.assertEqual((self.disp(mm[1]), self.disp(mm[0])), (20.1, 9.4))

    def test_range2(self):
        mm = self.calc(self.data, range_=(-30.0, 90.0))
        self.assertEqual((self.disp(mm[1]), self.disp(mm[0])), (20.2, 9.4))


class TestKsSave(unittest.TestCase):
    """アプリの測定データ → .KS 書き出し"""

    @classmethod
    def setUpClass(cls):
        cls.doc = parse_ks(FIXTURE.read_text(encoding="utf-8"))
        cls.data = doc_to_data(cls.doc)
        cls.rebuilt = data_to_doc(
            cls.data, model="TWA-200", date="2026/06/11", operator="ODA",
            range1=(0.0, 90.0), range2=(-30.0, 90.0), order=[1, 3, 4, 2],
        )

    def test_header_geometry(self):
        doc = self.rebuilt
        self.assertEqual(doc["start_h"], -1800000)
        self.assertEqual(doc["end_h"], 1800000)
        self.assertEqual(doc["interval_h"], 50000)
        self.assertEqual(doc["points_h"], 72)
        self.assertEqual(doc["points_w"], 10)
        self.assertEqual(doc["range1_start"], 0)
        self.assertEqual(doc["range1_end"], 900000)

    def test_header_lines_match_old_app(self):
        # 3〜4行目は範囲2を使っていても3列のまま（実物確認）
        lines = format_ks(self.rebuilt, newline="\n").splitlines()
        self.assertEqual(lines[2], "-1800000,0,0")
        self.assertEqual(lines[3], "1800000,50000,900000")

    def test_accuracy_lines_match_old_app(self):
        # 精度行は旧アプリのヘッダ値（全18値）と一致する
        doc = self.rebuilt
        self.assertEqual(doc["acc_cw"], [18.0, 2.0, 20.0])
        self.assertEqual(doc["acc_ccw"], [15.5, 4.5, 20.0])
        self.assertEqual(doc["range_cw"], [7.0, 2.0, 9.0, 7.5, 2.0, 9.5])
        self.assertEqual(doc["range_ccw"], [6.0, 4.5, 10.5, 7.0, 4.5, 11.5])

    def test_minmax_lines_match_old_app(self):
        # MAX/MIN行（総合バックラッシ）も旧アプリのヘッダ値と一致する
        self.assertEqual(self.rebuilt["max3"], [24.2, 20.1, 20.2])
        self.assertEqual(self.rebuilt["min3"], [9.4, 9.4, 9.4])

    def test_data_rows_roundtrip(self):
        # データ行は実物とバイト一致で再生成される
        original = FIXTURE.read_text(encoding="utf-8").splitlines()[14:]
        regenerated = format_ks(self.rebuilt, newline="\n").splitlines()[14:]
        self.assertEqual(regenerated, original)

    def test_measure_save_byte_identical(self):
        # 測定→セーブ(data_to_doc)で旧アプリ実物と完全バイト一致すること。
        # 4行目のウォーム間隔は 0.5°=30'00"→DDMMSSパックで 3000。
        out = format_ks(self.rebuilt, newline="\n")
        self.assertEqual(out, FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(out.splitlines()[4], "50000,3000")  # 刻み: 5°=50000, 0.5°=3000

    def test_reparse(self):
        # 書いたものを読み戻せる（範囲2の窓は旧アプリ同様ファイルに残らない）
        doc2 = parse_ks(format_ks(self.rebuilt, newline="\n"))
        self.assertIsNone(doc2["range2_start"])
        self.assertEqual(doc2["range_cw"], [7.0, 2.0, 9.0, 7.5, 2.0, 9.5])
        self.assertEqual(len(doc2["rows"]["wheel"]), 73)
        self.assertEqual(doc2["order"], [1, 3, 4, 2])


if __name__ == "__main__":
    unittest.main()
