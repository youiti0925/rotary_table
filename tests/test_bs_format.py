# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from nd287_app.analysis import summarize
from nd287_app.bs_format import (
    data_to_doc,
    doc_to_data,
    format_bs,
    join_dms,
    parse_bs,
    split_dms,
)

FIXTURE = Path(__file__).parent / "fixtures" / "260976K_fragment.bs"


class TestDmsColumns(unittest.TestCase):
    def test_split_dms(self):
        self.assertEqual(split_dms(40.0), (40.0, 0.0, 0.0))
        d, m, s = split_dms(39.0 + 59 / 60 + 59.5 / 3600)
        self.assertEqual((d, m), (39.0, 59.0))
        self.assertAlmostEqual(s, 59.5, places=6)

    def test_join_dms(self):
        self.assertAlmostEqual(join_dms(39.0, 59.0, 59.5), 39.0 + 59 / 60 + 59.5 / 3600)
        self.assertAlmostEqual(join_dms(0.0, 30.0, 0.0), 0.5)

    def test_roundtrip_no_60_seconds(self):
        d, m, s = split_dms(10.0 - 1e-10)
        self.assertEqual((d, m, s), (10.0, 0.0, 0.0))


class TestParseRealFile(unittest.TestCase):
    """実物の260976K.BS（61行で完結）が正しく読めること"""

    @classmethod
    def setUpClass(cls):
        cls.text = FIXTURE.read_text(encoding="utf-8")
        cls.doc = parse_bs(cls.text)

    def test_header(self):
        self.assertEqual(self.doc["model"], "RW-250R")
        self.assertEqual(self.doc["date"], "2026/06/11")
        self.assertEqual(self.doc["operator"], "ODA")
        self.assertEqual(self.doc["temperature"], "26")
        self.assertEqual(self.doc["spec_min"], 10.0)
        self.assertEqual(self.doc["spec_max"], 24.0)

    def test_series_params(self):
        hr = self.doc["series"]["HR"]
        self.assertEqual(hr["interval"], 300000)  # 30°（0.0001°単位）
        self.assertEqual(hr["n"], 3)
        self.assertEqual(hr["points"], 12)        # 12×3=36区間+閉じ点=37行
        self.assertEqual(hr["slope"], -4.0)
        self.assertEqual(hr["acc1"], 7.5)
        wr = self.doc["series"]["WR"]
        self.assertEqual(wr["points"], 10)

    def test_block_row_counts(self):
        # 1行にCWとCCWの両方が入るので、ブロックはホイールとウォームの2つ
        self.assertEqual(len(self.doc["rows"]["wheel"]), 12 * 3 + 1)
        self.assertEqual(len(self.doc["rows"]["worm"]), 10 * 1 + 1)

    def test_row_holds_both_directions(self):
        # 40°の行: CW測定 39°59'59.5"、CCW測定 40°00'17.5"
        row = self.doc["rows"]["wheel"][4]
        self.assertEqual(row[0], 40.0)
        self.assertEqual((row[1], row[2], row[3]), (39.0, 59.0, 59.5))
        self.assertEqual((row[4], row[5], row[6]), (40.0, 0.0, 17.5))

    def test_closing_point_wraps_to_zero(self):
        # 360°の行: CCW測定値はカウンタ表示どおり 0°00'15.5"
        row = self.doc["rows"]["wheel"][36]
        self.assertEqual(row[0], 360.0)
        self.assertEqual((row[4], row[5], row[6]), (0.0, 0.0, 15.5))

    def test_roundtrip_byte_identical(self):
        """読み込み→書き出しで実物と一字一句一致すること（完全互換の要）"""
        regenerated = format_bs(self.doc, newline="\n")
        self.assertEqual(regenerated, self.text)


class TestConversion(unittest.TestCase):
    """アプリの測定データ形式との相互変換"""

    @classmethod
    def setUpClass(cls):
        cls.text = FIXTURE.read_text(encoding="utf-8")
        cls.doc = parse_bs(cls.text)
        cls.data = doc_to_data(cls.doc)

    def test_series_lengths_and_order(self):
        self.assertEqual(len(self.data["wheel_cw"][0]), 37)
        self.assertEqual(len(self.data["wheel_ccw"][0]), 37)
        self.assertEqual(len(self.data["worm_cw"][0]), 11)
        # CCWは測定順（降順）
        self.assertEqual(self.data["wheel_ccw"][0][0], 360.0)
        self.assertEqual(self.data["wheel_ccw"][0][-1], 0.0)

    def test_summary_matches_old_app(self):
        summary, _ = summarize(self.data)
        # 旧アプリのヘッダ: HR傾=-4（CW偏差の360°-0°差）→ こちらの定義では昇順なので同じ
        self.assertAlmostEqual(summary["wheel_cw"]["slope"], -4.0, places=6)
        # バックラッシ（CCW-CW）はファイル実データの13.0〜20.0の範囲
        # （最小は210°: +2.0→+15.0、最大は70°: -2.0→+18.0）
        self.assertAlmostEqual(summary["wheel_backlash"]["min"], 13.0, places=6)
        self.assertAlmostEqual(summary["wheel_backlash"]["max"], 20.0, places=6)
        # 閉じ点の巻き戻り（360°でCCW=0°00'15.5"）が偏差+15.5"として扱われる
        # （±180°正規化が無いと-129万秒になる）
        self.assertLess(abs(summary["wheel_ccw"]["pp"]), 100.0)

    def test_data_rows_roundtrip(self):
        """変換→逆変換でデータ行（13行目以降）が実物と完全一致すること"""
        summary, _ = summarize(self.data)
        doc2 = data_to_doc(
            self.data,
            summary,
            model=self.doc["model"],
            date=self.doc["date"],
            operator=self.doc["operator"],
            temperature=self.doc["temperature"],
            spec_min=self.doc["spec_min"],
            spec_max=self.doc["spec_max"],
        )
        original_data_lines = self.text.splitlines()[13:]
        regenerated_data_lines = format_bs(doc2, newline="\n").splitlines()[13:]
        self.assertEqual(regenerated_data_lines, original_data_lines)

    def test_header_slopes_match_old_app(self):
        summary, _ = summarize(self.data)
        doc2 = data_to_doc(self.data, summary, model="x", date="d", operator="o",
                           temperature="26")
        # 旧アプリの傾: HR=-4, WR=1, WL=0, HL=1
        self.assertEqual(doc2["series"]["HR"]["slope"], -4)
        self.assertEqual(doc2["series"]["WR"]["slope"], 1)
        self.assertEqual(doc2["series"]["WL"]["slope"], 0)
        self.assertEqual(doc2["series"]["HL"]["slope"], 1)


if __name__ == "__main__":
    unittest.main()
