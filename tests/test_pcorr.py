# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from nd287_app.bs_format import parse_bs, doc_to_data
from nd287_app.pcorr import apply_compensation, compensation_table

FIXTURE = Path(__file__).parent / "fixtures" / "260976K_fragment.bs"


class TestCompensationTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = doc_to_data(parse_bs(FIXTURE.read_text(encoding="utf-8")))

    def test_grid_points(self):
        # 10°間隔 → 0,10,…,360 の37点（測定も10°刻みなので全点）
        table = compensation_table(self.data, 10.0, 0.001)
        self.assertEqual(len(table), 37)
        self.assertEqual(table[0]["angle"], 0.0)
        self.assertEqual(table[-1]["angle"], 360.0)
        # 30°間隔 → 13点（主点のみ）
        table30 = compensation_table(self.data, 30.0, 0.001)
        self.assertEqual(len(table30), 13)
        self.assertEqual([r["angle"] for r in table30][:3], [0.0, 30.0, 60.0])

    def test_compensation_cancels_mean(self):
        # 40°点: CW -0.5", CCW +17.5" → 平均 +8.5" → 補正 -8.5"/3600/0.001 ≈ -2単位
        table = compensation_table(self.data, 10.0, 0.001)
        row = table[4]
        self.assertEqual(row["angle"], 40.0)
        self.assertAlmostEqual(row["cw"], -0.5, places=6)
        self.assertAlmostEqual(row["ccw"], 17.5, places=6)
        self.assertAlmostEqual(row["mean"], 8.5, places=6)
        self.assertEqual(row["units"], round(-8.5 / 3600.0 / 0.001))
        self.assertAlmostEqual(row["comp_sec"], row["units"] * 3.6, places=6)

    def test_apply_reduces_spread(self):
        # 補正後はCW/CCWの平均が量子化誤差（±半単位=±1.8"）以内に収まる
        table = compensation_table(self.data, 10.0, 0.001)
        corrected = apply_compensation(self.data, table)
        t_cw, dev_cw = corrected["wheel_cw"]
        t_ccw, dev_ccw = corrected["wheel_ccw"]
        for d1, d2 in zip(dev_cw, dev_ccw):
            self.assertLessEqual(abs((d1 + d2) / 2.0), 1.8 + 1e-6)

    def test_empty_data(self):
        self.assertEqual(compensation_table({}, 10.0, 0.001), [])
        self.assertEqual(apply_compensation({}, []), {})


if __name__ == "__main__":
    unittest.main()
