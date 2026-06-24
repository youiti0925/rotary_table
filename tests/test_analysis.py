# -*- coding: utf-8 -*-
import unittest

from nd287_app.analysis import (
    adjacent,
    backlash,
    band_for_temp,
    deviation_sec,
    judge_minmax,
    pp,
    single,
    slope,
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

    def test_single(self):
        # 単一誤差 = 1ステップで発生した誤差の最大: ステップは -1, -2 → 最大2
        self.assertAlmostEqual(single([1.0, 0.0, -2.0]), 2.0)
        self.assertEqual(single([5.0]), 0.0)
        self.assertEqual(single([]), 0.0)

    def test_adjacent(self):
        # 隣接誤差 = 連続する単一誤差が逆向きに重なった突起。
        # +4" のステップの次が -6" のステップ → 10" の突起
        self.assertAlmostEqual(adjacent([0.0, 4.0, -2.0]), 10.0)
        # ステップ -1, -2（同方向）→ 突起は |-1-(-2)| = 1
        self.assertAlmostEqual(adjacent([1.0, 0.0, -2.0]), 1.0)
        self.assertEqual(adjacent([5.0, 6.0]), 0.0)

    def test_adjacent_peak_location(self):
        from nd287_app.analysis import adjacent_peak, single_peak
        # steps +4,-6 → 突起10" は中心点(index1)に出る
        self.assertEqual(adjacent_peak([0.0, 4.0, -2.0]), (1, 10.0))
        # 点が3未満なら None
        self.assertIsNone(adjacent_peak([5.0, 6.0]))
        # 単一誤差最大は -6 のステップ（index1→2）、値6
        self.assertEqual(single_peak([0.0, 4.0, -2.0]), (1, 2, 6.0))

    def test_slope(self):
        # 傾き = 終了の偏差 - 開始の偏差（測定順）
        self.assertAlmostEqual(slope([1.0, 0.0, -2.0]), -3.0)
        self.assertAlmostEqual(slope([-1.0, 0.0, 2.5]), 3.5)
        self.assertEqual(slope([5.0]), 0.0)


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


SPEC = [
    dict(temp_min=0.0, temp_max=15.0, min=0.0, max=30.0),
    dict(temp_min=15.0, temp_max=25.0, min=0.0, max=25.0),
    dict(temp_min=25.0, temp_max=40.0, min=0.0, max=20.0),
]


class TestTemperatureJudgement(unittest.TestCase):
    def test_band_selection_by_temperature(self):
        self.assertEqual(band_for_temp(SPEC, 10.0)["max"], 30.0)
        self.assertEqual(band_for_temp(SPEC, 20.0)["max"], 25.0)
        self.assertEqual(band_for_temp(SPEC, 30.0)["max"], 20.0)
        self.assertIsNone(band_for_temp(SPEC, 50.0))
        self.assertIsNone(band_for_temp([], 20.0))

    def test_temperature_changes_verdict(self):
        # バックラッシ22"は、10°C（規格30"まで）ならOK、30°C（規格20"まで）ならNG
        ok_cold, _ = judge_minmax(5.0, 22.0, 10.0, SPEC)
        ok_hot, _ = judge_minmax(5.0, 22.0, 30.0, SPEC)
        self.assertTrue(ok_cold)
        self.assertFalse(ok_hot)

    def test_below_lower_limit_is_ng(self):
        ok, _ = judge_minmax(-1.0, 10.0, 20.0, SPEC)
        self.assertFalse(ok)

    def test_no_band_returns_none(self):
        ok, band = judge_minmax(0.0, 10.0, 99.0, SPEC)
        self.assertIsNone(ok)
        self.assertIsNone(band)

    def test_minus_range_for_true_minmax(self):
        # 総合（真の最大最小）のような ± の規格にも使える
        spec = [dict(temp_min=15.0, temp_max=25.0, min=-25.0, max=25.0)]
        ok, _ = judge_minmax(-10.0, 20.0, 20.0, spec)
        self.assertTrue(ok)
        ok, _ = judge_minmax(-30.0, 20.0, 20.0, spec)
        self.assertFalse(ok)


class TestSequence(unittest.TestCase):
    def test_step_layout(self):
        # ホイール90°刻み=0,90,180,270,360の5点（閉じ点込み）、ウォーム1°刻みで2°ぶん=3点
        seq = Sequence(wheel_pitch=90.0, worm_pitch=1.0, worm_range=2.0)
        self.assertEqual(len(seq), 5 + 5 + 3 + 3)
        # CW は昇順で始まり閉じ点360°で終わる。CCW は同じ点列の逆順
        self.assertEqual(seq.steps[0], ("wheel_cw", 0.0, +1))
        self.assertEqual(seq.steps[4], ("wheel_cw", 360.0, +1))
        self.assertEqual(seq.steps[5], ("wheel_ccw", 360.0, -1))
        self.assertEqual(seq.steps[9], ("wheel_ccw", 0.0, -1))
        self.assertEqual(seq.steps[10], ("worm_cw", 0.0, +1))
        self.assertEqual(seq.steps[12], ("worm_cw", 2.0, +1))
        self.assertEqual(seq.steps[13], ("worm_ccw", 2.0, -1))

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
    @staticmethod
    def run_sequence():
        # CW誤差1"・CCW誤差2"の一定オフセット → バックラッシ1.0"
        seq = Sequence(wheel_pitch=90.0, worm_pitch=1.0, worm_range=2.0)
        while not seq.done():
            key, target, direction = seq.current()
            err = 1.0 / 3600 if direction > 0 else 2.0 / 3600
            seq.record(target + err)
        return seq

    def test_backlash_manual_correction(self):
        seq = self.run_sequence()
        summary, _ = summarize(seq.data, backlash_correction=3.0)
        # 補正なしバックラッシ1.0" + 補正3.0" = 4.0"（ホイール・ウォーム両方）
        self.assertAlmostEqual(summary["wheel_backlash"]["min"], 4.0, places=6)
        self.assertAlmostEqual(summary["wheel_backlash"]["max"], 4.0, places=6)
        self.assertAlmostEqual(summary["worm_backlash"]["max"], 4.0, places=6)
        self.assertAlmostEqual(summary["backlash_correction"], 3.0)
        # マイナス補正（実際の隙間が測定結果より少ない）
        summary_m, _ = summarize(seq.data, backlash_correction=-0.5)
        self.assertAlmostEqual(summary_m["wheel_backlash"]["min"], 0.5, places=6)
        # 真の最大最小は補正の影響を受けない
        self.assertAlmostEqual(summary["true"]["cw"]["true_max"], 2.0, places=6)
        # 補正0なら補正キーは出ない
        summary0, _ = summarize(seq.data)
        self.assertNotIn("backlash_correction", summary0)

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
            # 一定オフセット誤差なのでステップ差は出ない: 単一・隣接・傾きとも0
            self.assertAlmostEqual(summary[key]["single"], 0.0, places=6)
            self.assertAlmostEqual(summary[key]["adjacent"], 0.0, places=6)
            self.assertAlmostEqual(summary[key]["slope"], 0.0, places=6)
        # バックラッシ = CCW偏差(2") - CW偏差(1") = 1.0"
        self.assertAlmostEqual(summary["wheel_backlash"]["min"], 1.0, places=6)
        self.assertAlmostEqual(summary["wheel_backlash"]["max"], 1.0, places=6)
        self.assertAlmostEqual(summary["worm_backlash"]["min"], 1.0, places=6)
        # 真の最大最小 = ホイール+ウォームの合成（CW: 1+1=2, CCW: 2+2=4）
        self.assertAlmostEqual(summary["true"]["cw"]["true_max"], 2.0, places=6)
        self.assertAlmostEqual(summary["true"]["ccw"]["true_max"], 4.0, places=6)


if __name__ == "__main__":
    unittest.main()
