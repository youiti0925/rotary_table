# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from nd287_app.analysis import repeatability_summary
from nd287_app.export import (
    MODE_KEY,
    load_measurement,
    repeat_result_rows,
    save_repeat_csv,
)
from nd287_app.sequence import (
    IndexingSequence,
    RepeatabilitySequence,
    block_points,
    rotary_blocks,
    tilt_blocks,
)

SEC = 1.0 / 3600.0


class TestBlockPoints(unittest.TestCase):
    def test_rotary_blocks_exclude_closing_point(self):
        # 回転再現性: 0,90,180,270 の4ブロック（360°=0°なので含めない）
        self.assertEqual(block_points(0.0, 360.0, 90.0, include_end=False),
                         [0.0, 90.0, 180.0, 270.0])

    def test_tilt_range_includes_both_ends(self):
        pts = block_points(-30.0, 110.0, 10.0)
        self.assertEqual(len(pts), 15)
        self.assertEqual(pts[0], -30.0)
        self.assertEqual(pts[-1], 110.0)

    def test_rotary_blocks_by_count(self):
        # ブロック数指定: 4箇所 → 0,90,180,270
        self.assertEqual(rotary_blocks(4), [0.0, 90.0, 180.0, 270.0])
        self.assertEqual(rotary_blocks(6), [0.0, 60.0, 120.0, 180.0, 240.0, 300.0])

    def test_tilt_blocks_by_count(self):
        # 傾斜: 範囲をn箇所で等分割（両端含む）
        pts = tilt_blocks(-30.0, 110.0, 4)
        self.assertEqual(len(pts), 4)
        self.assertAlmostEqual(pts[0], -30.0)
        self.assertAlmostEqual(pts[1], 16.0 + 2.0 / 3.0, places=6)
        self.assertAlmostEqual(pts[-1], 110.0)
        self.assertEqual(tilt_blocks(0.0, 100.0, 1), [0.0])


class TestTiltIndexingSequence(unittest.TestCase):
    def test_tilt_range(self):
        seq = IndexingSequence(10.0, 0.5, 5.0, 0.0, wheel_start=-30.0, wheel_end=110.0)
        wheel_cw = [s for s in seq.steps if s[0] == "wheel_cw"]
        self.assertEqual(len(wheel_cw), 15)
        self.assertEqual(wheel_cw[0], ("wheel_cw", -30.0, +1))
        self.assertEqual(wheel_cw[-1], ("wheel_cw", 110.0, +1))
        # CCWは逆順で始まる
        first_ccw = next(s for s in seq.steps if s[0] == "wheel_ccw")
        self.assertEqual(first_ccw, ("wheel_ccw", 110.0, -1))


class TestRepeatabilitySequence(unittest.TestCase):
    def test_step_layout(self):
        # 2ブロック × 3回 × CW/CCW = 12ステップ。
        # 各ブロックで CW×3 → CCW×3 → 次のブロック
        seq = RepeatabilitySequence([0.0, 90.0], repeats=3)
        self.assertEqual(len(seq), 12)
        self.assertEqual(seq.current(), ("cw", 0.0, +1))
        dirs = [(s[0], s[1]) for s in seq.steps]
        self.assertEqual(dirs[:6], [("cw", 0)] * 3 + [("ccw", 0)] * 3)
        self.assertEqual(dirs[6:], [("cw", 1)] * 3 + [("ccw", 1)] * 3)

    def test_guide_text(self):
        seq = RepeatabilitySequence([0.0, 90.0], repeats=3)
        self.assertIn("ブロック1/2 CW 1/3回目", seq.guide_text())
        for _ in range(3):
            seq.record(0.0)
        self.assertIn("ブロック1/2 CCW 1/3回目", seq.guide_text())

    def test_record_and_undo(self):
        seq = RepeatabilitySequence([0.0], repeats=2)
        seq.record(0.0)
        seq.record(0.001)
        self.assertEqual(seq.data[("cw", 0)], [0.0, 0.001])
        self.assertTrue(seq.undo())
        self.assertEqual(seq.data[("cw", 0)], [0.0])
        self.assertTrue(seq.undo())
        self.assertNotIn(("cw", 0), seq.data)
        self.assertFalse(seq.undo())

    def test_full_run_done(self):
        seq = RepeatabilitySequence([0.0, 90.0, 180.0, 270.0], repeats=7)
        self.assertEqual(len(seq), 4 * 7 * 2)
        while not seq.done():
            _, target, _ = seq.current()
            seq.record(target)
        self.assertTrue(seq.done())

    def test_counts(self):
        seq = RepeatabilitySequence([0.0, 90.0], repeats=3)
        self.assertEqual(seq.counts(), [("CW", 0, 6), ("CCW", 0, 6)])
        for _ in range(4):  # ブロック1 CW×3 + CCW×1
            _, target, _ = seq.current()
            seq.record(target)
        self.assertEqual(seq.counts(), [("CW", 3, 6), ("CCW", 1, 6)])

    def test_indexing_counts(self):
        seq = IndexingSequence(90.0, 1.0, 2.0)  # ホイール5点×2方向、ウォーム3点×2方向
        counts = dict((label, (cur, req)) for label, cur, req in seq.counts())
        self.assertEqual(counts["ホイール CW"], (0, 5))
        self.assertEqual(counts["ウォーム CCW"], (0, 3))
        for _ in range(6):  # ホイールCW全5点 + CCW1点
            _, target, _ = seq.current()
            seq.record(target)
        counts = dict((label, (cur, req)) for label, cur, req in seq.counts())
        self.assertEqual(counts["ホイール CW"], (5, 5))
        self.assertEqual(counts["ホイール CCW"], (1, 5))


class TestRepeatabilitySummary(unittest.TestCase):
    def test_block_ranges_and_worst(self):
        points = [0.0, 90.0]
        data = {
            ("cw", 0): [0.0, 1 * SEC, 2 * SEC],          # 範囲2"
            ("cw", 1): [90.0, 90.0 + 5 * SEC],           # 範囲5" ← CWの最大
            ("ccw", 0): [0.0, 3 * SEC],                  # 範囲3"
            ("ccw", 1): [90.0 + 1 * SEC, 90.0 + 2 * SEC],  # 範囲1"
        }
        rsum = repeatability_summary(points, data)
        self.assertAlmostEqual(rsum["blocks"][0]["cw"], 2.0, places=6)
        self.assertAlmostEqual(rsum["blocks"][1]["cw"], 5.0, places=6)
        self.assertAlmostEqual(rsum["cw"], 5.0, places=6)
        self.assertAlmostEqual(rsum["ccw"], 3.0, places=6)
        self.assertAlmostEqual(rsum["overall"], 5.0, places=6)

    def test_missing_direction_is_none(self):
        rsum = repeatability_summary([0.0], {("cw", 0): [0.0, 1 * SEC]})
        self.assertAlmostEqual(rsum["blocks"][0]["cw"], 1.0, places=6)
        self.assertIsNone(rsum["blocks"][0]["ccw"])
        self.assertIsNone(rsum["ccw"])
        self.assertAlmostEqual(rsum["overall"], 1.0, places=6)

    def test_zero_wrap_normalized(self):
        # 0°ブロックでカウンタが 359.9999… に巻き戻った読みが混ざっても、
        # ±360°折り返して正しい範囲になる（以前は約129.6万秒の異常値になった）
        rsum = repeatability_summary(
            [0.0], {("cw", 0): [360.0 - 1 * SEC, 0.0, 1 * SEC]})
        self.assertAlmostEqual(rsum["blocks"][0]["cw"], 2.0, places=6)
        self.assertAlmostEqual(rsum["overall"], 2.0, places=6)

    def test_no_wrap_bit_identical(self):
        # またがない通常値は折り返し導入前と完全一致（浮動小数点まで不変）
        vals = [90.0, 90.0 + 5 * SEC, 90.0 + 1.23456 * SEC]
        rsum = repeatability_summary([90.0], {("cw", 0): vals})
        self.assertEqual(rsum["blocks"][0]["cw"],
                         (max(vals) - min(vals)) * 3600.0)

    def test_result_rows(self):
        points = [0.0, 90.0]
        data = {
            ("cw", 0): [0.0, 2 * SEC],
            ("ccw", 0): [0.0, 3 * SEC],
            ("cw", 1): [90.0, 90.0 + 5 * SEC],
            ("ccw", 1): [90.0, 90.0 + 1 * SEC],
        }
        rows = dict(repeat_result_rows(repeatability_summary(points, data)))
        self.assertEqual(rows["ブロック1 (0°) CW範囲"], '2.00"')
        self.assertEqual(rows["再現性 CW（全ブロック最大）"], '5.00"')
        self.assertEqual(rows["再現性 総合"], '5.00"')


class TestRepeatCsvRoundtrip(unittest.TestCase):
    def test_save_and_load(self):
        seq = RepeatabilitySequence([0.0, 90.0, 180.0, 270.0], repeats=7)
        i = 0
        while not seq.done():
            _, target, direction = seq.current()
            jitter = ((i * 7) % 5) * SEC  # ブロック内でばらつかせる
            backlash = 2 * SEC if direction < 0 else 0.0
            seq.record(target + jitter + backlash)
            i += 1
        rsum = repeatability_summary(seq.points, seq.data)
        meta = {MODE_KEY: "回転再現性", "型式": "RWE-200", "機番": "12345",
                "ブロック刻み[°]": 90.0, "回数": 7}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "12345.csv")
            save_repeat_csv(path, seq.points, seq.data, rsum, meta)
            meta2, kind, (points2, data2) = load_measurement(path)
        self.assertEqual(kind, "repeat")
        self.assertEqual(meta2[MODE_KEY], "回転再現性")
        self.assertEqual(points2, seq.points)
        for key, vals in seq.data.items():
            self.assertEqual(len(data2[key]), len(vals))
            for a, b in zip(vals, data2[key]):
                self.assertAlmostEqual(a, b, places=6)
        # ロード後の再計算が一致
        rsum2 = repeatability_summary(points2, data2)
        self.assertAlmostEqual(rsum2["overall"], rsum["overall"], places=2)

    def test_load_measurement_detects_indexing(self):
        from nd287_app.analysis import summarize
        from nd287_app.export import save_csv
        from nd287_app.sequence import Sequence

        seq = Sequence(wheel_pitch=90.0, worm_pitch=1.0, worm_range=2.0)
        while not seq.done():
            _, target, _ = seq.current()
            seq.record(target)
        summary, _ = summarize(seq.data)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.csv")
            save_csv(path, seq.data, summary, {MODE_KEY: "回転分割"})
            meta, kind, payload = load_measurement(path)
        self.assertEqual(kind, "indexing")
        self.assertEqual(meta[MODE_KEY], "回転分割")
        self.assertEqual(len(payload["wheel_cw"][0]), 5)


if __name__ == "__main__":
    unittest.main()
