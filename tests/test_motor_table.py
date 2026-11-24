# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from nd287_app import seiban_flow


class TestMotorTable(unittest.TestCase):
    def test_load_and_lookup(self):
        d = Path(tempfile.mkdtemp())
        p = d / "t.csv"
        p.write_text("モーター番号,モーター型式,容量,別名,出典件数\n299,αiF22/3000 HV,40A,,12\n"
                     "293,αiS8/4000,20A,αiS 8/4000・x,3\n", encoding="utf-8-sig")
        t = seiban_flow.load_motor_table(p)
        self.assertEqual(seiban_flow.motor_info(t, "299")["model"], "αiF22/3000 HV")
        self.assertEqual(seiban_flow.motor_info(t, " 0293")["cap"], "20A")
        self.assertEqual(seiban_flow.motor_info(t, "1"), {})
        self.assertEqual(seiban_flow.load_motor_table(d / "none.csv"), {})


if __name__ == "__main__":
    unittest.main()


class TestMotorVoltage(unittest.TestCase):
    def test_hv_is_400v(self):
        from nd287_app import seiban_flow
        self.assertEqual(seiban_flow.motor_voltage("αiF22/3000HV"), "400V")
        self.assertEqual(seiban_flow.motor_voltage("αiF12/4000"), "200V")
