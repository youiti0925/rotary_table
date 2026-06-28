# -*- coding: utf-8 -*-
import unittest

from nd287_app import param_view as V

# ネイティブ(N#####Q1) — \n\r\r 改行も含む実機相当
NATIVE_A = ("%\n\r\r"
            "N01815Q1A1P00000000A2P00000000A3P00000000A4P00000010\n\r\r"
            "N01825Q1A1P3000A2P3000A3P3000A4P3000\n\r\r"
            "N02020Q1A1P255A2P273A3P293A4P303\n\r\r%\n")
NATIVE_B = ("%\n\r\r"
            "N01815Q1A1P00000000A2P00000000A3P00000000A4P00000010\n\r\r"
            "N01825Q1A1P3000A2P3000A3P3000A4P2500\n\r\r"   # A4 を 3000→2500
            "N02020Q1A1P255A2P273A3P293A4P303\n\r\r%\n")

# ヘッダ＋CSV
HEADER_CSV = (
    "System Version=Ver.1.10\nModel=X\nSeiban=1\n"
    '"1815","---","","","\'001*0000","別置検出器\nSEPARATE DETECTOR"\n'
    '"1825","---","","","3000","位置ﾙｰﾌﾟｹﾞｲﾝ\nPOS.LOOP GAIN"\n')


class TestReadRows(unittest.TestCase):
    def test_native_axes(self):
        rows = V.read_rows(NATIVE_A)
        d = {(r["num"], r["label"]): r["value"] for r in rows}
        self.assertEqual(d[("01815", "A4")], "00000010")
        self.assertEqual(d[("01825", "A1")], "3000")
        self.assertEqual(d[("02020", "A4")], "303")
        # 1825 は4軸ぶん行がある
        self.assertEqual(sum(1 for r in rows if r["num"] == "01825"), 4)

    def test_header_csv(self):
        rows = V.read_rows(HEADER_CSV)
        d = {r["num"]: r for r in rows}
        self.assertEqual(d["1815"]["value"], "'001*0000")
        self.assertEqual(d["1815"]["desc"], "別置検出器")   # 和名
        self.assertEqual(d["1825"]["label"], "")


class TestCompare(unittest.TestCase):
    def test_marks_difference(self):
        rows = V.compare(NATIVE_A, NATIVE_B)
        diffs = [r for r in rows if r["differ"]]
        self.assertEqual(len(diffs), 1)
        self.assertEqual((diffs[0]["num"], diffs[0]["label"]), ("01825", "A4"))
        self.assertEqual((diffs[0]["a"], diffs[0]["b"]), ("3000", "2500"))

    def test_missing_on_one_side(self):
        extra = NATIVE_A.replace("%\n", "N09999Q1L1P7\n\r\r%\n", 1)
        rows = V.compare(extra, NATIVE_A)
        r = next(r for r in rows if r["num"] == "09999")
        self.assertEqual(r["a"], "7")
        self.assertIsNone(r["b"])
        self.assertTrue(r["differ"])

    def test_num_normalization_cross(self):
        # ネイティブ '01825' と ヘッダ+CSV '1825' が同じ番号として揃う
        rows = V.compare(NATIVE_A, HEADER_CSV)
        keys = {(r["num"], r["label"]) for r in rows}
        # 1825: native は A1..A4、csv は '' → ラベルが違うので別行（構造差）になる
        nums = {V._num_int(r["num"]) for r in rows}
        self.assertIn(1825, nums)
        self.assertIn(1815, nums)


class TestFilter(unittest.TestCase):
    def test_range(self):
        rows = V.read_rows(NATIVE_A)
        got = V.filter_rows(rows, lo=1000, hi=1900)
        nums = {r["num"] for r in got}
        self.assertEqual(nums, {"01815", "01825"})   # 2020は範囲外
        self.assertNotIn("02020", nums)

    def test_diff_only(self):
        rows = V.compare(NATIVE_A, NATIVE_B)
        got = V.filter_rows(rows, diff_only=True)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["num"], "01825")

    def test_diff_only_ignored_for_single(self):
        rows = V.read_rows(NATIVE_A)            # "differ" を持たない
        self.assertEqual(V.filter_rows(rows, diff_only=True), [])  # 全部スキップ


if __name__ == "__main__":
    unittest.main()
