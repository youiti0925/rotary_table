# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from nd287_app import controllers, motor_rules as mr

ROOT = Path(__file__).resolve().parent.parent
MASTER = str(ROOT / "マスタ" / "制御装置マスタ.csv")


def _units(cands):
    return sorted(str(c.unit) for c, _a in cands)


class TestMotorRules(unittest.TestCase):
    def setUp(self):
        self.ctls = controllers.load_controllers(MASTER)
        self.cands = [(c, []) for c in self.ctls]

    def test_default_d_and_b(self):
        rules = mr.load_rules(None)
        out, used = mr.apply(self.cands, ["αiF12/4000-D"], rules)
        self.assertEqual(_units(out), ["31", "82", "84"])
        out, used = mr.apply(self.cands, ["αiS8/4000-B"], rules)
        self.assertEqual(_units(out), ["20", "21", "22", "23", "31", "82", "84"])
        out, used = mr.apply(self.cands, ["αiS8/4000", "DiS 100"], rules)
        self.assertEqual(len(out), len(self.cands))
        self.assertEqual(used, [])

    def test_shipped_csv_equals_default(self):
        rules = mr.load_rules(str(ROOT / "マスタ" / "モーター規則.csv"))
        self.assertEqual([(r["末尾"], r["見る列"], r["条件"], r["根拠"]) for r in rules],
                         [(r["末尾"], r["見る列"], r["条件"], r["根拠"]) for r in mr.DEFAULT_RULES])

    def test_edit_and_new_controller_follow_the_table(self):
        newc = controllers.Controller("90", cnc="0i-MF Plus", caps={"X": "20A"}, d_drive="○")
        cands = self.cands + [(newc, [])]
        out, _ = mr.apply(cands, ["αiF2/5000-D"], mr.load_rules(None))
        self.assertIn("90", _units(out))               # マスタに○で足した新しい制御装置は、コードを直さず -D の候補に入る
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.csv"
            mr.save_rules(p, [{"末尾": "B", "見る列": "-Bなめらか補正", "条件": "○", "根拠": "マスタ", "説明": ""}])
            out, used = mr.apply(cands, ["x-B"], mr.load_rules(p))
        self.assertEqual(_units(out), ["31", "82", "84"])
        self.assertEqual(used[0]["根拠"], "マスタ")

    def test_controller_edit_keeps_amps_and_flags(self):
        import nd287_app.gui as G
        from PySide6 import QtWidgets
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        c = controllers.Controller("99", cnc="35i-MB", caps={"X": "20A"}, amps={"X": "A06B-6240-H206"},
                                   b_corr="○", d_drive="○")
        d = G.ControllerEditDialog(None, c)
        d._accept()
        r = d.result_controller
        self.assertEqual((r.amps.get("X"), r.b_corr, r.d_drive), ("A06B-6240-H206", "○", "○"))
