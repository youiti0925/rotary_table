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


class TestEquivalentCap(unittest.TestCase):
    def test_400v_to_200v_doubles(self):
        from nd287_app import seiban_flow as sf
        self.assertEqual(sf.equivalent_cap("40A", "400V", "200V"), "80A")
        self.assertEqual(sf.equivalent_cap("80A", "200V", "400V"), "40A")
        self.assertEqual(sf.equivalent_cap("40A", "400V", "400V"), "40A")
        self.assertEqual(sf.equivalent_cap("", "400V", "200V"), "")


class TestCapacityUppercaseIF(unittest.TestCase):
    def test_uppercase_IF_is_read(self):
        from nd287_app import seiban_flow as sf
        self.assertEqual(sf.standard_capacity("αIF8/3000 HV-B"), "20A")
        self.assertEqual(sf.standard_capacity("αiF8/3000 HV-B"), "20A")
        self.assertEqual(sf.standard_capacity("αIF12/4000"), "40A")


class TestDDriveMotor(unittest.TestCase):
    """末尾 -D のモーターは -D駆動 ○ の号機だけ（2026-11-24 利用者「-D付はD駆動の事」）。"""

    def test_is_d_motor(self):
        from nd287_app import seiban_flow as sf
        for m in ("αiF12/4000-D", "αIF 8/3000-D", "αiF8/3000 HV-D", "αiS2/5000-D"):
            self.assertTrue(sf.is_d_motor(m), m)
        for m in ("αiF12/4000", "α22/2000", "", "DiS 100", "αiF8/3000 HV"):
            self.assertFalse(sf.is_d_motor(m), m)

    def test_only_d_drive_uses_master_column(self):
        from nd287_app import seiban_flow as sf, controllers
        ctls = controllers.load_controllers("マスタ/制御装置マスタ.csv") \
            if hasattr(controllers, "load_controllers") else []
        if not ctls:
            self.skipTest("loader name differs")
        got = sorted(str(c.unit) for c, _a in sf.only_d_drive([(c, []) for c in ctls]))
        self.assertEqual(got, ["31", "82", "84"])

    def test_b_motor_and_generation(self):
        from nd287_app import seiban_flow as sf, controllers
        for m in ("αiS2/5000-B", "αiS2/5000HV-B", "βiS8/3000-B"):
            self.assertTrue(sf.is_b_motor(m), m)
        for m in ("αiS2/5000", "αiF2/5000-D", ""):
            self.assertFalse(sf.is_b_motor(m), m)
        ctls = controllers.load_controllers("マスタ/制御装置マスタ.csv")
        got = sorted(str(c.unit) for c, _a in sf.only_ib_generation([(c, []) for c in ctls]))
        self.assertEqual(got, ["20", "21", "22", "23", "31", "82", "84"])
