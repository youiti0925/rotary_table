# -*- coding: utf-8 -*-
import unittest

from nd287_app import iso230 as I

SEC = 1.0 / 3600.0


def _d(angle, secs):
    """指令角度＋偏差[秒]リスト → 測定値[deg]リスト"""
    return [angle + s * SEC for s in secs]


class TestIsoStats(unittest.TestCase):
    """手計算例: 位置0°/90°、各方向3回。

    0°: ↑(CW)偏差 [0,1,2] → x̄=1, s=1, R↑=4 ／ ↓(CCW) [3,4,5] → x̄=4, s=1, R↓=4
        B0 = 1−4 = −3、R0 = max(2+2+3, 4, 4) = 7
    90°: ↑ [10,10,10] → x̄=10, s=0 ／ ↓ [8,9,10] → x̄=9, s=1
        B1 = 1、R1 = max(0+2+1, 0, 4) = 4
    軸: E↑=9, E↓=5, E=9, M=7, B=3, B̄=−1, R=7,
        A↑ = max(3,10)−min(−1,10) = 11
        A↓ = max(6,11)−min(2,7) = 9
        A  = max(3,10,6,11)−min(−1,2,10,7) = 11−(−1) = 12
    """

    def setUp(self):
        points = [0.0, 90.0]
        data = {
            ("cw", 0): _d(0.0, [0, 1, 2]),
            ("ccw", 0): _d(0.0, [3, 4, 5]),
            ("cw", 1): _d(90.0, [10, 10, 10]),
            ("ccw", 1): _d(90.0, [8, 9, 10]),
        }
        self.st = I.iso_stats(points, data)

    def test_positions(self):
        p0, p1 = self.st["positions"]
        self.assertAlmostEqual(p0["mean_up"], 1.0, places=6)
        self.assertAlmostEqual(p0["s_up"], 1.0, places=6)
        self.assertAlmostEqual(p0["r_up"], 4.0, places=6)
        self.assertAlmostEqual(p0["mean_dn"], 4.0, places=6)
        self.assertAlmostEqual(p0["b"], -3.0, places=6)
        self.assertAlmostEqual(p0["r"], 7.0, places=6)
        self.assertAlmostEqual(p1["s_up"], 0.0, places=6)
        self.assertAlmostEqual(p1["b"], 1.0, places=6)
        self.assertAlmostEqual(p1["r"], 4.0, places=6)

    def test_axis(self):
        a = self.st["axis"]
        self.assertAlmostEqual(a["E_up"], 9.0, places=6)
        self.assertAlmostEqual(a["E_dn"], 5.0, places=6)
        self.assertAlmostEqual(a["E"], 9.0, places=6)
        self.assertAlmostEqual(a["M"], 7.0, places=6)
        self.assertAlmostEqual(a["B"], 3.0, places=6)
        self.assertAlmostEqual(a["B_mean"], -1.0, places=6)
        self.assertAlmostEqual(a["R"], 7.0, places=6)
        self.assertAlmostEqual(a["A_up"], 11.0, places=6)
        self.assertAlmostEqual(a["A_dn"], 9.0, places=6)
        self.assertAlmostEqual(a["A"], 12.0, places=6)
        self.assertEqual(a["m"], 2)
        self.assertEqual((a["n_min"], a["n_max"]), (3, 3))

    def test_notes_mention_cycle(self):
        text = " ".join(self.st["notes"])
        self.assertIn("5回", text)      # 接近回数がJIS標準(5回)未満
        self.assertIn("8点", text)      # 目標位置が8点未満


class TestEdgeCases(unittest.TestCase):
    def test_one_direction_only(self):
        st = I.iso_stats([0.0], {("cw", 0): _d(0.0, [0, 2])})
        p = st["positions"][0]
        self.assertAlmostEqual(p["mean_up"], 1.0, places=6)
        self.assertIsNone(p["b"])
        a = st["axis"]
        self.assertIsNone(a["B"])       # 双方向値は出ない
        self.assertIsNone(a["R"])
        self.assertAlmostEqual(a["E_up"], 0.0, places=6)
        self.assertIsNotNone(a["A_up"])
        self.assertEqual(a["m"], 0)
        self.assertIn("片方向", " ".join(st["notes"]))

    def test_single_reading_excluded(self):
        # n=1 は s が出ない → その方向は除外（クラッシュしない）
        st = I.iso_stats([0.0], {("cw", 0): _d(0.0, [1]),
                                 ("ccw", 0): _d(0.0, [0, 2])})
        p = st["positions"][0]
        self.assertIsNone(p["mean_up"])
        self.assertAlmostEqual(p["mean_dn"], 1.0, places=6)

    def test_zero_wrap(self):
        # 0°ブロックの巻き戻り読み（359.9999…）も正しく偏差になる
        st = I.iso_stats([0.0], {
            ("cw", 0): [360.0 - 1 * SEC, 1 * SEC],     # -1" と +1"
            ("ccw", 0): [0.0, 0.0],
        })
        p = st["positions"][0]
        self.assertAlmostEqual(p["mean_up"], 0.0, places=6)
        self.assertAlmostEqual(2 * p["s_up"], 2.0 * 1.4142135, places=5)

    def test_empty(self):
        st = I.iso_stats([], {})
        self.assertEqual(st["positions"], [])
        self.assertIsNone(st["axis"]["A"])

    def test_csv_text(self):
        st = I.iso_stats([0.0], {("cw", 0): _d(0.0, [0, 2]),
                                 ("ccw", 0): _d(0.0, [1, 3])})
        text = I.to_csv_text(st, meta={"型式": "RTT-135", "機番": "X1"})
        self.assertIn("JIS B 6190-2", text)
        self.assertIn("RTT-135", text)
        self.assertIn("A 双方向位置決め精度", text)
        self.assertIn("0°", text)


class TestRecommendedTargets(unittest.TestCase):
    """専用ISOモードの目標位置生成（JIS推奨の擬似ランダムオフセット）。"""

    def test_deterministic(self):
        base = [0, 45, 90, 135, 180, 225, 270, 315]
        a = I.recommended_targets(base, 0, 315)
        b = I.recommended_targets(base, 0, 315)
        self.assertEqual(a, b)                       # 乱数不使用＝毎回同じ

    def test_endpoints_fixed_and_monotonic(self):
        base = [0, 45, 90, 135, 180, 225, 270, 315]
        t = I.recommended_targets(base, 0, 315)
        self.assertEqual(t[0], 0.0)                  # 両端は範囲を保つため固定
        self.assertEqual(t[-1], 315.0)
        self.assertTrue(all(t[i] < t[i + 1] for i in range(len(t) - 1)))
        self.assertNotEqual(t, [float(x) for x in base])  # 内側はずれる

    def test_interior_within_range(self):
        base = [0, 45, 90, 135, 180, 225, 270, 315]
        t = I.recommended_targets(base, 0, 315)
        self.assertTrue(all(0.0 <= v <= 315.0 for v in t))

    def test_small_n_untouched(self):
        # 2点以下はオフセットせずそのまま（両端しか無いので）
        self.assertEqual(I.recommended_targets([0, 90], 0, 90), [0.0, 90.0])
        self.assertEqual(I.recommended_targets([0], 0, 0), [0.0])


if __name__ == "__main__":
    unittest.main()
