# -*- coding: utf-8 -*-
import unittest

from nd287_app import controller_import as C


class TestMapController(unittest.TestCase):
    def test_japanese_flat_fields(self):
        ctl, unmapped = C.map_controller("doc1", {
            "号機": "84", "CNCユニット": "31i-B", "制御電圧": "200V", "SERVO": "Ver.9",
            "X容量": "40", "Xアンプ": "ID262/AMR45", "A容量": "160A",
        })
        self.assertEqual(ctl.unit, "84")
        self.assertEqual(ctl.cnc, "31i-B")
        self.assertEqual(ctl.voltage, "200V")
        self.assertEqual(ctl.servo, "Ver.9")
        self.assertEqual(ctl.caps["X"], "40A")       # 正規化される
        self.assertEqual(ctl.caps["A"], "160A")
        self.assertEqual(ctl.amps["X"], "ID262/AMR45")
        self.assertEqual(unmapped, [])

    def test_english_camelcase(self):
        ctl, unmapped = C.map_controller("doc2", {
            "machineNo": "100", "cncUnit": "0i-F", "voltage": "400V",
            "xCapacity": "80A", "xAmp": "SVM1-80", "somethingElse": "z",
        })
        self.assertEqual(ctl.unit, "100")
        self.assertEqual(ctl.cnc, "0i-F")
        self.assertEqual(ctl.voltage, "400V")
        self.assertEqual(ctl.caps["X"], "80A")
        self.assertEqual(ctl.amps["X"], "SVM1-80")
        self.assertEqual(unmapped, ["somethingElse"])  # 未対応は報告

    def test_nested_axes_map(self):
        ctl, unmapped = C.map_controller("doc3", {
            "unit": "5",
            "axes": {"X": {"capacity": "20A", "amp": "SVM-20"},
                     "A": {"capacity": "40A"}},
        })
        self.assertEqual(ctl.unit, "5")
        self.assertEqual(ctl.caps["X"], "20A")
        self.assertEqual(ctl.caps["A"], "40A")
        self.assertEqual(ctl.amps["X"], "SVM-20")
        self.assertEqual(unmapped, [])                 # axes は消費済み

    def test_doc_id_used_when_no_unit(self):
        ctl, _ = C.map_controller("42", {"cnc": "31i"})
        self.assertEqual(ctl.unit, "42")               # 号機が無ければdoc_id

    def test_map_controllers_filters_empty_unit(self):
        docs = [("", {"cnc": "31i"}),                  # 号機もdoc_idも無い→除外
                ("7", {"CNCユニット": "31i-A", "Z容量": "20A"})]
        ctls, unmapped = C.map_controllers(docs)
        self.assertEqual([c.unit for c in ctls], ["7"])
        self.assertEqual(ctls[0].caps["Z"], "20A")

    def test_underscore_and_case_insensitive(self):
        ctl, _ = C.map_controller("d", {"MACHINE_NO": "9", "control_voltage": "200V"})
        self.assertEqual(ctl.unit, "9")
        self.assertEqual(ctl.voltage, "200V")


if __name__ == "__main__":
    unittest.main()
