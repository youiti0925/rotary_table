# -*- coding: utf-8 -*-
import unittest

from nd287_app import fanuc_param as F

# 実物 F30BASIC.PRM の代表的な行を抜粋（各セグメント型を網羅）。
# 行間の空行・行末スペースもそのまま（バイト保存の検証用）。CRLFは別途確認。
SAMPLE = (
    "%\r\n"
    "\r\n"
    "N00000Q1L1P00000010\r\n"
    "\r\n"
    "N00002Q1P00000000\r\n"
    "\r\n"
    "N00020Q1P4 \r\n"
    "\r\n"
    "N01020Q1A1P88A2P89A3P90A4P65 \r\n"
    "\r\n"
    "N01260Q1A1P360.0A2P360.0A3P360.0A4P360.0 \r\n"
    "\r\n"
    "N01411Q1L1M0.0 \r\n"
    "\r\n"
    "N01825Q1A1P3000A2P3000A3P3000A4P3000 \r\n"
    "\r\n"
    "N02020Q1A1P255A2P273A3P293A4P303 \r\n"
    "\r\n"
    "N02041Q1A1P-3743A2P-4260A3P-6391A4P-4492 \r\n"
    "\r\n"
    "N00982Q1S1P0 \r\n"
    "\r\n"
    "%\r\n"
)


class TestRead(unittest.TestCase):
    def test_looks_like(self):
        self.assertTrue(F.looks_like_fanuc_prm(SAMPLE))
        self.assertFalse(F.looks_like_fanuc_prm("System Version=Ver.1.10\nModel=X\n"))

    def test_segments(self):
        segs = F.segments("N01020Q1A1P88A2P89A3P90A4P65 ")
        self.assertEqual(segs, [("A1", "P", "88"), ("A2", "P", "89"),
                                ("A3", "P", "90"), ("A4", "P", "65")])
        self.assertEqual(F.segments("N00002Q1P00000000"), [("", "P", "00000000")])
        self.assertEqual(F.segments("N01411Q1L1M0.0 "), [("L1", "M", "0.0")])
        self.assertIsNone(F.segments("%"))

    def test_get_value(self):
        self.assertEqual(F.get_value(SAMPLE, "1825", "A4"), "3000")  # ゼロ詰め吸収
        self.assertEqual(F.get_value(SAMPLE, "02020", "A4"), "303")
        self.assertEqual(F.get_value(SAMPLE, "00000", "L1"), "00000010")
        self.assertEqual(F.get_value(SAMPLE, "00002"), "00000000")   # ラベル無し
        self.assertEqual(F.get_value(SAMPLE, "00982", "S1"), "0")
        self.assertIsNone(F.get_value(SAMPLE, "99999"))


class TestByteSafeEdit(unittest.TestCase):
    def test_set_axis_value_only_changes_that_value(self):
        new, ok = F.set_value(SAMPLE, "1825", "2500", "A4")
        self.assertTrue(ok)
        self.assertEqual(F.get_value(new, "1825", "A4"), "2500")
        # 同じ行の他軸は不変
        self.assertEqual(F.get_value(new, "1825", "A1"), "3000")
        # 変わったのは1か所だけ（バイト差分は最小）
        self.assertEqual(new.replace("A4P2500", "A4P3000"), SAMPLE)

    def test_set_single_value(self):
        new, ok = F.set_value(SAMPLE, "00020", "8")  # 'P4 ' → 'P8 '
        self.assertTrue(ok)
        self.assertIn("N00020Q1P8 \r\n", new)
        # 行末スペースも保持
        self.assertNotIn("N00020Q1P8\r\n", new.replace("P8 ", "P8 "))

    def test_set_decimal_and_negative(self):
        new, _ = F.set_value(SAMPLE, "02041", "-5000", "A2")
        self.assertEqual(F.get_value(new, "02041", "A2"), "-5000")
        self.assertEqual(F.get_value(new, "02041", "A1"), "-3743")  # 他は不変

    def test_missing_param_reported(self):
        new, ok = F.set_value(SAMPLE, "99999", "1", "A4")
        self.assertFalse(ok)
        self.assertEqual(new, SAMPLE)  # 失敗時は一切変更しない

    def test_apply_changes(self):
        new, missing = F.apply_changes(SAMPLE, [
            ("1825", "2500", "A4"),
            ("2020", "300", "A4"),
            ("9999", "1", "A4"),
        ])
        self.assertEqual(missing, [("9999", "A4")])
        self.assertEqual(F.get_value(new, "1825", "A4"), "2500")
        self.assertEqual(F.get_value(new, "2020", "A4"), "300")
        # 変更3件（うち1件失敗）以外はバイト不変＝CRLF/空行/行末スペース保持
        restored = new.replace("A4P2500", "A4P3000").replace("A4P300 ", "A4P303 ")
        self.assertEqual(restored, SAMPLE)


class TestDiff(unittest.TestCase):
    def test_diff_finds_changed_params(self):
        # マスタを元に、製品別で 1825 の A4 と 02020 の A4 を変えたものを作る
        product, _ = F.set_value(SAMPLE, "1825", "2500", "A4")
        product, _ = F.set_value(product, "02020", "300", "A4")
        d = F.diff(SAMPLE, product)
        self.assertEqual(d, [
            ("01825", "A4", "3000", "2500"),
            ("02020", "A4", "303", "300"),
        ])

    def test_diff_empty_when_same(self):
        self.assertEqual(F.diff(SAMPLE, SAMPLE), [])

    def test_apply_diff_reproduces_product(self):
        # diff で得た変更を別途マスタへ適用すると製品ファイルに一致（バイト単位）
        product, _ = F.set_value(SAMPLE, "1825", "2500", "A4")
        d = F.diff(SAMPLE, product)
        changes = [(num, newv, label) for (num, label, oldv, newv) in d]
        rebuilt, missing = F.apply_changes(SAMPLE, changes)
        self.assertEqual(missing, [])
        self.assertEqual(rebuilt, product)


class TestApplyToAxis(unittest.TestCase):
    def test_set_on_axis_uses_chosen_axis(self):
        # 軸2を選んで 1825 を 2500 に → A2 だけ変わる、A4等は不変
        new, ok = F.set_on_axis(SAMPLE, "1825", "2500", 2)
        self.assertTrue(ok)
        self.assertEqual(F.get_value(new, "1825", "A2"), "2500")
        self.assertEqual(F.get_value(new, "1825", "A4"), "3000")

    def test_set_on_axis_falls_back_to_single(self):
        # 番号なしP の 00002 は軸が無い → 単一値へ反映
        new, ok = F.set_on_axis(SAMPLE, "00002", "11111111", 3)
        self.assertTrue(ok)
        self.assertEqual(F.get_value(new, "00002"), "11111111")
        # S1 の 00982 も軸が無い → S1 へ
        new, ok = F.set_on_axis(SAMPLE, "00982", "1", 3)
        self.assertTrue(ok)
        self.assertEqual(F.get_value(new, "00982", "S1"), "1")

    def test_apply_product_values_axis4(self):
        # 製品の値を A4（回転軸）へ。工程「その軸のパラメータだけ変更」を再現
        values = {"1825": "2500", "02020": "300", "00020": "8", "9999": "1"}
        new, missing = F.apply_product_values(SAMPLE, values, 4)
        self.assertEqual(missing, ["9999"])
        self.assertEqual(F.get_value(new, "1825", "A4"), "2500")
        self.assertEqual(F.get_value(new, "02020", "A4"), "300")
        self.assertEqual(F.get_value(new, "00020"), "8")     # 単一値
        # A1〜A3は不変（その軸だけ変える）
        self.assertEqual(F.get_value(new, "1825", "A1"), "3000")
        self.assertEqual(F.get_value(new, "02020", "A1"), "255")


if __name__ == "__main__":
    unittest.main()
