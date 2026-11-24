# -*- coding: utf-8 -*-
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nd287_app import rules

ROOT = Path(__file__).resolve().parent.parent


class TestRuleRegistry(unittest.TestCase):
    def test_every_rule_has_source_and_place(self):
        for rid, (text, src, where, test) in rules.RULES.items():
            self.assertTrue(text and src and where and test, rid)
            self.assertIn(src.split("（")[0].split("＋")[0], ("利用者", "マスタ", "推測"), rid)

    def test_b_filter_is_marked_as_guess(self):
        self.assertTrue(rules.RULES["B_FILTER"][1].startswith("推測"))
        from nd287_app import motor_rules
        self.assertEqual([r["根拠"] for r in motor_rules.DEFAULT_RULES], ["利用者", "推測"])

    def test_rules_exist_in_code(self):
        gui = (ROOT / "nd287_app" / "gui.py").read_text(encoding="utf-8")
        sf = (ROOT / "nd287_app" / "seiban_flow.py").read_text(encoding="utf-8")
        self.assertIn("def equivalent_cap", sf)
        mr = (ROOT / "nd287_app" / "motor_rules.py").read_text(encoding="utf-8")
        self.assertIn("def apply", mr)
        for needle in ("motor_rules.apply(", '".RSK" if self.is_tilt() else ".RS"',
                       '.BS"', '.KS"', "rules.append_result"):
            self.assertIn(needle, gui, needle)

    def test_result_log_row(self):
        res = SimpleNamespace(state="done", items=[{"status": "ok"}], new_alarms=[], folder="F")
        bad = SimpleNamespace(state="done", items=[{"status": "alarm"}], new_alarms=[{"code": "SV0417"}], folder="F")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x" / rules.LOG_NAME
            ctx = {"seiban": "50014984", "motor": "αiS8/4000-B", "unit": "23", "rules": ["B_FILTER", "AXIS_2165"]}
            self.assertTrue(rules.append_result(p, ctx, res, now="t"))
            self.assertTrue(rules.append_result(p, ctx, bad, now="t"))
            rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        self.assertEqual(rows[0]["結果"], "OK（読み戻し一致）")
        self.assertEqual(rows[0]["使った規則"], "B_FILTER・AXIS_2165")
        self.assertEqual(rows[1]["結果"], "アラーム")
        self.assertEqual(rows[1]["アラーム"], "SV0417")
