# -*- coding: utf-8 -*-
import unittest
from pathlib import Path
from nd287_app import machine_decide as M, controllers as C

CTL = C.load_controllers(str(Path(__file__).resolve().parent.parent / "マスタ" / "制御装置マスタ.csv"))
PROBE21 = {"axes": {1: {"amr": "85", "motor_id": "255"}, 2: {"amr": "165", "motor_id": "262"},
                    3: {"amr": "85", "motor_id": "273"}, 4: {"amr": "165", "motor_id": "272"}}}


class T(unittest.TestCase):
    def test_caps(self):
        self.assertEqual(M.machine_caps(PROBE21), {"X": "80A", "Y": "160A", "Z": "80A", "A": "160A"})

    def test_ledger_wins_and_ip_is_not_used(self):
        r = M.identify_unit(PROBE21, CTL, ledger_unit="21")
        self.assertEqual((r["unit"], r["how"]), ("21", "台帳"))
        r = M.identify_unit(PROBE21, CTL)
        self.assertEqual(r["how"], "複数")                  # 同じ並びが複数 → 止めずに進む
        self.assertIn("21", r["units"])
        self.assertEqual(r["unit"], "")

    def test_unknown_machine(self):
        probe = {"axes": {1: {"amr": "45"}, 2: {"amr": "45"}, 3: {"amr": "45"}}}
        r = M.identify_unit(probe, CTL)
        self.assertTrue(r["unknown"])
        self.assertEqual(M.identify_unit({"axes": {}}, CTL)["how"], "無し")

    def test_mismatch(self):
        c21 = next(c for c in CTL if c.unit == "21")
        self.assertEqual(M.mismatch(PROBE21, c21), [])
        c21.caps = dict(c21.caps, Y="80A", Z="160A")
        self.assertEqual(M.mismatch(PROBE21, c21), [("Y", "80A", "160A"), ("Z", "160A", "80A")])

    def test_400v_product_on_200v_machine(self):
        r = M.choose_axes([{"cap": "40A", "voltage": "400V", "motor_id": "273"}], PROBE21, "200V")
        self.assertEqual(r[0]["axis"], "Z")                 # 40A→80A、モーター番号が同じ Z
        self.assertEqual(r[0]["alternatives"], ["X", "Z"])  # ［変える］は同じ容量だけ

    def test_two_products_distinct(self):
        r = M.choose_axes([{"cap": "80A", "voltage": "200V"}, {"cap": "80A", "voltage": "200V"}], PROBE21, "200V")
        self.assertEqual(sorted(x["axis"] for x in r), ["X", "Z"])

    def test_unknown_capacity_not_decided(self):
        r = M.choose_axes([{"cap": "", "voltage": ""}], PROBE21, "200V")
        self.assertEqual(r[0]["axis"], "")


if __name__ == "__main__":
    unittest.main()
