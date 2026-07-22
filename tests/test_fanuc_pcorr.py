# -*- coding: utf-8 -*-
import unittest

from nd287_app import fanuc_pcorr as F


def rows(angle_mean):
    """[(angle, mean_sec)] → compensation_table 形式の行（本モジュールは angle/mean のみ使用）。"""
    return [{"no": i + 1, "angle": a, "cw": m, "ccw": m, "mean": m,
             "units": 0, "comp_sec": 0.0} for i, (a, m) in enumerate(angle_mean)]


class TestBuildPitchParams(unittest.TestCase):
    def test_abs_comp_normalized_to_reference(self):
        # 0°=0", 90°=+3.6", 180°=+7.2", 270°=+3.6"  → A=-誤差（検出単位0.001°）
        r = rows([(0, 0.0), (90, 3.6), (180, 7.2), (270, 3.6)])
        p = F.build_pitch_params(r, interval_deg=90, rotary=False)
        cum = [pt[2] for pt in p["points"]]
        self.assertEqual(cum[0], 0)                       # 基準(0°)は0
        self.assertEqual(cum, [0, -1, -2, -1])            # -誤差/検出単位

    def test_increments_reconstruct_cumulative_non_rotary(self):
        r = rows([(0, 0.0), (90, 3.6), (180, 7.2), (270, 3.6)])
        p = F.build_pitch_params(r, interval_deg=90, rotary=False)
        incr = [pt[3] for pt in p["points"]]
        # 積算で絶対補正に戻る（非回転は残差配分しない＝完全一致）
        acc = []
        s = 0
        for v in incr:
            s += v
            acc.append(s)
        self.assertEqual(acc, [pt[2] for pt in p["points"]])

    def test_rotary_increments_sum_to_zero(self):
        r = rows([(0, 0.0), (90, 3.6), (180, 7.2), (270, 3.6)])
        p = F.build_pitch_params(r, interval_deg=90, rotary=True)
        incr = [pt[3] for pt in p["points"]]
        self.assertEqual(sum(incr), 0)                    # 1回転で閉じる
        self.assertTrue(any("総和を0" in n for n in p["notes"]))

    def test_config_numbers(self):
        r = rows([(0, 0.0), (90, 3.6), (180, 7.2), (270, 3.6)])
        p = F.build_pitch_params(r, interval_deg=90, rotary=True,
                                 detect_unit_deg=0.001, axis=4, data_base=10000)
        cfg = {num: (ax, val) for (num, ax, val, _d) in p["config"]}
        self.assertEqual(cfg[3620], (4, 10000))           # 基準点番号（0°=index0）
        self.assertEqual(cfg[3621], (4, 10000))           # 負側端
        self.assertEqual(cfg[3622], (4, 10004))           # 正側端（+4点）
        self.assertEqual(cfg[3623], (4, 1))               # 倍率
        self.assertEqual(cfg[3624], (4, 90000))           # 間隔 90°/0.001
        self.assertEqual(cfg[3625], (4, 360000))          # 1回転 360°/0.001

    def test_non_rotary_has_no_perrev(self):
        r = rows([(0, 0.0), (90, 3.6), (180, 7.2), (270, 3.6)])
        p = F.build_pitch_params(r, interval_deg=90, rotary=False)
        self.assertNotIn(3625, [num for (num, *_rest) in p["config"]])

    def test_sign_flips_direction(self):
        r = rows([(0, 0.0), (90, 3.6)])
        pos = F.build_pitch_params(r, interval_deg=90, rotary=False, sign=1)
        neg = F.build_pitch_params(r, interval_deg=90, rotary=False, sign=-1)
        self.assertEqual(pos["points"][1][2], -neg["points"][1][2])

    def test_prm_text_wellformed(self):
        r = rows([(0, 0.0), (90, 3.6), (180, 7.2), (270, 3.6)])
        p = F.build_pitch_params(r, interval_deg=90, rotary=True, axis=4)
        text = F.to_prm_text(p, newline="\n")
        lines = text.strip().splitlines()
        self.assertEqual(lines[0], "%")
        self.assertEqual(lines[-1], "%")
        self.assertIn("N03620Q1A4P10000", lines)          # 設定は軸指定つき
        self.assertTrue(any(l.startswith("N10001Q1P") for l in lines))  # データは番号なしP
        # 5桁ゼロ詰め
        self.assertTrue(all(l == "%" or l.startswith("N") for l in lines))

    def test_too_few_points(self):
        p = F.build_pitch_params(rows([(0, 0.0)]), interval_deg=90)
        self.assertEqual(p["config"], [])
        self.assertTrue(p["notes"])


if __name__ == "__main__":
    unittest.main()
