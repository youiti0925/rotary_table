# -*- coding: utf-8 -*-
import unittest

from nd287_app.analysis import (
    adjacent,
    backlash,
    deviation_sec,
    pp,
    summarize,
    true_min_max,
)
from nd287_app.sequence import Sequence


class TestDeviation(unittest.TestCase):
    def test_deviation_sec(self):
        # 指令0°/10°/20°、測定が+1秒/0/-2秒ずれ
        targets = [0.0, 10.0, 20.0]
        measured = [1.0 / 3600, 10.0, 20.0 - 2.0 / 3600]
        d = deviation_sec(targets, measured)
        self.assertAlmostEqual(d[0], 1.0, places=6)
        self.assertAlmostEqual(d[1], 0.0, places=6)
        self.assertAlmostEqual(d[2], -2.0, places=6)

    def test_pp(self):
        self.assertAlmostEqual(pp([1.0, 0.0, -2.0]), 3.0)
        self.assertEqual(pp([]), 0.0)

    def test_adjacent(self):
        # 隣接差: |0-1|=1, |-2-0|=2 → 最大2
        self.assertAlmostEqual(adjacent([1.0, 0.0, -2.0]), 2.0)
        self.assertEqual(adjacent([5.0]), 0.0)


class TestBacklash(unittest.TestCase):
    def test_backlash_min_max(self):
        t = [0.0, 10.0, 20.0]
        d_cw = [0.0, 1.0, -1.0]
        d_ccw = [1.5, 1.5, 1.5]
        # CCW-CW = 1.5, 0.5, 2.5
        bl = backlash(t, d_cw, t, d_ccw)
        self.assertAlmostEqual(bl[0], 0.5)
        self.assertAlmostEqual(bl[1], 2.5)

    def test_no_common_points(self):
        self.assertIsNone(backlash([0.0], [0.0], [5.0], [0.0]))


class TestTrueMinMax(unittest.TestCase):
    def test_composite(self):
        tmin, tmax = true_min_max([1.0, -2.0], [0.5, -0.5])
        self.assertAlmostEqual(tmin, -2.5)
        self.assertAlmostEqual(tmax, 1.5)


class TestSequence(unittest.TestCase):
    def test_step_layout(self):
        # ホイール90°刻み=4点、ウォーム1°刻みで2°ぶん=3点
        seq = Sequence(wheel_pitch=90.0, worm_pitch=1.0, worm_range=2.0)
        self.assertEqual(len(seq), 4 + 4 + 3 + 3)
        # CW は昇順で始まり、CCW は同じ点列の逆順
        self.assertEqual(seq.steps[0], ("wheel_cw", 0.0, +1))
        self.assertEqual(seq.steps[3], ("wheel_cw", 270.0, +1))
        self.assertEqual(seq.steps[4], ("wheel_ccw", 270.0, -1))
        self.assertEqual(seq.steps[7], ("wheel_ccw", 0.0, -1))
        self.assertEqual(seq.steps[8], ("worm_cw", 0.0, +1))
        self.assertEqual(seq.steps[10], ("worm_cw", 2.0, +1))
        self.assertEqual(seq.steps[11], ("worm_ccw", 2.0, -1))

    def test_record_and_undo(self):
        seq = Sequence(wheel_pitch=180.0, worm_pitch=1.0, worm_range=1.0)
        seq.record(0.0)
        seq.record(180.0)
        self.assertEqual(seq.idx, 2)
        self.assertTrue(seq.undo())
        self.assertEqual(seq.idx, 1)
        self.assertEqual(len(seq.data["wheel_cw"][0]), 1)
        self.assertTrue(seq.undo())
        self.assertFalse(seq.undo())

    def test_worm_start_offset(self):
        seq = Sequence(wheel_pitch=180.0, worm_pitch=0.5, worm_range=1.0, worm_start=30.0)
        worm_targets = [t for k, t, _ in seq.steps if k == "worm_cw"]
        self.assertEqual(worm_targets, [30.0, 30.5, 31.0])


class TestSummarize(unittest.TestCase):
    def test_full_run(self):
        # 完走シーケンスを模擬データで埋めてサマリ構造を確認
        seq = Sequence(wheel_pitch=90.0, worm_pitch=1.0, worm_range=2.0)
        while not seq.done():
            key, target, direction = seq.current()
            err = 1.0 / 3600 if direction > 0 else 2.0 / 3600
            seq.record(target + err)
        summary, devs = summarize(seq.data)
        for key in ("wheel_cw", "wheel_ccw", "worm_cw", "worm_ccw"):
            self.assertIn(key, summary)
            self.assertAlmostEqual(summary[key]["pp"], 0.0, places=6)
        # バックラッシ = CCW偏差(2") - CW偏差(1") = 1.0"
        self.assertAlmostEqual(summary["wheel_backlash"]["min"], 1.0, places=6)
        self.assertAlmostEqual(summary["wheel_backlash"]["max"], 1.0, places=6)
        self.assertAlmostEqual(summary["worm_backlash"]["min"], 1.0, places=6)
        # 真の最大最小 = ホイール+ウォームの合成（CW: 1+1=2, CCW: 2+2=4）
        self.assertAlmostEqual(summary["true"]["cw"]["true_max"], 2.0, places=6)
        self.assertAlmostEqual(summary["true"]["ccw"]["true_max"], 4.0, places=6)


if __name__ == "__main__":
    unittest.main()
