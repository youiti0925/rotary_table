# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from nd287_app.masters import (
    condition_params,
    find_entry,
    formula_minmax,
    load_conditions,
    load_judgement,
)

MASTER_DIR = Path(__file__).parent.parent / "マスタ"


class TestConditionsMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conditions = load_conditions(MASTER_DIR / "測定条件.csv")
        cls.judgement = load_judgement(MASTER_DIR / "合否判定.csv")

    def test_rwe200_loads(self):
        cond = find_entry(self.conditions, "RWE-200")
        self.assertEqual(cond["interval_h"], 300000)  # 30°
        self.assertEqual(cond["n_h"], 3)
        self.assertEqual(cond["div1"], 12)
        self.assertEqual(cond["div2"], 10)
        self.assertEqual(cond["order"], ["HR", "WR", "WL", "HL"])

    def test_rwe200_params(self):
        cond = find_entry(self.conditions, "RWE-200")
        judge = find_entry(self.judgement, "RWE-200")
        params = condition_params(cond, judge)
        # 30°×1/3 = 10°刻み
        self.assertAlmostEqual(params["wheel_pitch"], 10.0)
        # 歯数なし → 間隔W=3000(0.3°)×分割数10 = 3°
        self.assertAlmostEqual(params["worm_pitch"], 0.3)
        self.assertAlmostEqual(params["worm_range"], 3.0)

    def test_worm_comes_from_conditions_not_teeth(self):
        # ウォームは測定条件の間隔W基準（歯数はP補正用で測定条件には使わない）
        cond = find_entry(self.conditions, "RW-250")
        judge = find_entry(self.judgement, "RW-250")  # 歯数72があっても無関係
        params = condition_params(cond, judge)
        self.assertAlmostEqual(params["worm_pitch"], 0.3)
        self.assertAlmostEqual(params["worm_range"], 3.0)
        self.assertAlmostEqual(params["wheel_pitch"], 10.0)

    def test_variant_lookup(self):
        # RB-250Ri はRi仕様（ウォーム0.15°×8、測定順 HR→HL→WR→WL）
        cond = find_entry(self.conditions, "RB-250Ri")
        self.assertEqual(cond["close"], "Ri")
        self.assertEqual(cond["interval_w"], 1500)
        self.assertEqual(cond["div2"], 8)
        self.assertEqual(cond["order"], ["HR", "HL", "WR", "WL"])
        # 無印とは別エントリ
        base = find_entry(self.conditions, "RB-250")
        self.assertEqual(base["close"], "")
        self.assertEqual(base["order"], ["HR", "WR", "WL", "HL"])

    def test_order_with_skipped_sections(self):
        # RT-222: 測定順 1,,,2 → HR→HL のみ（ウォームなし）
        cond = find_entry(self.conditions, "RT-222")
        self.assertEqual(cond["order"], ["HR", "HL"])
        # TT-200: 1,2,0,0 → HR→WR のみ（CCWなし）
        cond = find_entry(self.conditions, "TT-200")
        self.assertEqual(cond["order"], ["HR", "WR"])

    def test_unknown_model(self):
        self.assertIsNone(find_entry(self.conditions, "XX-999"))
        self.assertIsNone(find_entry(self.conditions, ""))


class TestJudgementMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.judgement = load_judgement(MASTER_DIR / "合否判定.csv")

    def test_formula_with_temperature(self):
        # RWE-200: MIN=-0.6α+24, MAX=-1.3α+80
        judge = find_entry(self.judgement, "RWE-200")
        mn, mx = formula_minmax(judge, 26.0)
        self.assertAlmostEqual(mn, -0.6 * 26 + 24)   # 8.4
        self.assertAlmostEqual(mx, -1.3 * 26 + 80)   # 46.2
        # 温度が変わると規格が変わる
        mn20, mx20 = formula_minmax(judge, 20.0)
        self.assertAlmostEqual(mn20, 12.0)
        self.assertAlmostEqual(mx20, 54.0)

    def test_constant_spec_when_a_empty(self):
        # RBS-160: A空欄 → 温度に依らず 5〜15
        judge = find_entry(self.judgement, "RBS-160")
        self.assertEqual(formula_minmax(judge, 10.0), (5.0, 15.0))
        self.assertEqual(formula_minmax(judge, 30.0), (5.0, 15.0))

    def test_no_spec_returns_none(self):
        # RT-122: MIN/MAXすべて空欄 → 規格なし
        judge = find_entry(self.judgement, "RT-122")
        self.assertIsNone(formula_minmax(judge, 20.0))
        self.assertIsNone(formula_minmax(None, 20.0))
        self.assertIsNone(formula_minmax(find_entry(self.judgement, "RWE-200"), None))

    def test_slope_specs(self):
        judge = find_entry(self.judgement, "RWE-200")
        self.assertEqual(judge["slope_h"], 4.0)
        self.assertEqual(judge["slope_w"], 7.0)
        judge = find_entry(self.judgement, "RTH-538")
        self.assertEqual(judge["slope_h"], 3.0)
        self.assertEqual(judge["slope_w"], 5.0)

    def test_teeth(self):
        self.assertEqual(find_entry(self.judgement, "RW-250")["teeth"], 72.0)
        self.assertIsNone(find_entry(self.judgement, "RWE-200")["teeth"])

    def test_variant_with_multi_axis(self):
        # RN-100N（多軸N）は表示名キーでも引ける
        judge = find_entry(self.judgement, "RN-100N")
        self.assertEqual(judge["multi"], "N")
        self.assertEqual(judge["teeth"], 36.0)


if __name__ == "__main__":
    unittest.main()
