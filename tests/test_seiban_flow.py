# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app import seiban_flow as S
from nd287_app import controllers as C


def _prm(model="RTT-137", seiban="50013078", axis="T", amp="", motor="αiS4/5000-B",
         sep=""):
    return (
        ";   System Versio\nSystem Version=Ver.1.10\n"
        f";   Model parameters\nModel={model},AA\n"
        f";   Seiban parameters\nSeiban={seiban}\n"
        "Gear Rate='1/36\n"
        f";   Axis parameters\nAxis={axis}\n"
        "Direction=CCW\n"
        f"Motor Number=A06B-2215-B000\nMotor Model={motor}\n"
        f"Separate Detector={sep}\n"
        f"Servo Amp Model={amp}\n"
        '"番号","軸","和名","英名","値","説明"\n'
        '"1815","A4","","","00000010",""\n')


class TestFindSeibanFiles(unittest.TestCase):
    def test_finds_tilt_and_rotary_by_prefix(self):
        d = tempfile.mkdtemp()
        for n in ("T50013078.prm", "R50013078.prm", "Z99999.prm", "T70000.prm"):
            (Path(d) / n).write_text("%\n", encoding="cp932")
        got = S.find_seiban_files(d, "50013078")
        self.assertEqual([f["prefix"] for f in got], ["T", "R"])  # T,Rの順
        self.assertEqual([f["kind"] for f in got], ["傾斜", "回転"])
        self.assertTrue(got[0]["name"].startswith("T50013078"))

    def test_only_rotary_present(self):
        d = tempfile.mkdtemp()
        (Path(d) / "R260976.prm").write_text("%\n", encoding="cp932")
        got = S.find_seiban_files(d, "260976")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "回転")

    def test_no_match(self):
        d = tempfile.mkdtemp()
        (Path(d) / "T111.prm").write_text("%\n", encoding="cp932")
        self.assertEqual(S.find_seiban_files(d, "999"), [])


class TestDeriveCapacity(unittest.TestCase):
    def test_alpha_amp(self):
        self.assertEqual(S.derive_capacity("αiSV 40"), "40A")
        self.assertEqual(S.derive_capacity("βiSV 80"), "80A")
        self.assertEqual(S.derive_capacity("SVM 160"), "160A")
        self.assertEqual(S.derive_capacity("20"), "20A")

    def test_unknown_returns_empty(self):
        self.assertEqual(S.derive_capacity(""), "")
        self.assertEqual(S.derive_capacity("A06B-6240-H208"), "")  # 容量数字が無い

    def test_no_partial_digit_match(self):
        # 4000 を 40 と誤検出しない（語境界）
        self.assertEqual(S.derive_capacity("αiA4000"), "")


class TestReadProductMeta(unittest.TestCase):
    def test_reads_header_fields(self):
        meta = S.read_product_meta(_prm(axis="T", amp="αiSV 160"))
        self.assertEqual(meta["model"], "RTT-137,AA")
        self.assertEqual(meta["kind"], "傾斜")
        self.assertEqual(meta["capacity"], "160A")
        self.assertEqual(meta["gear"], "1/36")

    def test_axis_field_overrides(self):
        meta = S.read_product_meta(_prm(axis="R"), kind="傾斜")
        self.assertEqual(meta["kind"], "回転")  # 中身の Axis=R を優先

    def test_separate_detector_full_hint(self):
        meta = S.read_product_meta(_prm(sep="αiCZ"))
        self.assertEqual(meta["mode_hint"], "フル")


class TestStandardCapacity(unittest.TestCase):
    def test_size_bands(self):
        self.assertEqual(S.standard_capacity("αiS2/5000"), "20A")
        self.assertEqual(S.standard_capacity("αiS4/5000-B"), "20A")
        self.assertEqual(S.standard_capacity("αiS8/4000"), "40A")
        self.assertEqual(S.standard_capacity("αiS12/4000"), "40A")
        self.assertEqual(S.standard_capacity("αiS22/4000"), "80A")
        self.assertEqual(S.standard_capacity("αiS30/4000"), "80A")
        self.assertEqual(S.standard_capacity("αiS40/4000"), "160A")
        self.assertEqual(S.standard_capacity("αiS50/3000"), "160A")
        self.assertEqual(S.standard_capacity("αiF8/3000"), "40A")

    def test_does_not_pick_speed(self):
        # 5000(回転数)ではなく 2(番手)を見る
        self.assertEqual(S.standard_capacity("αiS2/5000"), "20A")

    def test_out_of_range_and_empty(self):
        self.assertEqual(S.standard_capacity("αiS100/2500"), "")  # マスタ範囲外
        self.assertEqual(S.standard_capacity(""), "")

    def test_dd_motor_not_guessed(self):
        # FANUC DDモーター(DiS系)は番手＝トルク。αiSの番手として誤推定しないこと
        self.assertTrue(S.is_dd_motor("DiS60"))
        self.assertTrue(S.is_dd_motor("DiS-60-B"))
        self.assertEqual(S.standard_capacity("DiS60"), "")
        self.assertEqual(S.standard_capacity("DiS-60-B"), "")
        self.assertEqual(S.standard_capacity("DiS22"), "")   # 旧コードは80Aと誤答していた

    def test_non_fanuc_motors_empty(self):
        # 三菱HG/安川SGM/TPC等は容量推定しない（"" → 手入力/対応表）
        for mm in ("HG-H104T", "SGM7P-08A7K", "TPC-Jr-K3B", "MDS-E/EH"):
            self.assertEqual(S.standard_capacity(mm), "", mm)

    def test_hv_halves_capacity(self):
        # 400V(HV)は電流が約半分のコード
        self.assertEqual(S.standard_capacity("αiS8/4000HV"), "20A")   # 200Vなら40A
        self.assertEqual(S.standard_capacity("αiS22/4000HV"), "40A")  # 200Vなら80A
        self.assertEqual(S.standard_capacity("αiS40/4000HV"), "80A")  # 200Vなら160A
        self.assertEqual(S.standard_capacity("αiS2/5000HV"), "10A")   # 200Vなら20A

    def test_motor_voltage(self):
        self.assertEqual(S.motor_voltage("αiS8/4000HV"), "400V")
        self.assertEqual(S.motor_voltage("αiS8/4000"), "200V")
        self.assertEqual(S.motor_voltage(""), "")
        self.assertTrue(S.is_hv("αiS8/4000HV"))
        self.assertFalse(S.is_hv("αiS8/4000"))

    def test_meta_hv_sets_voltage_and_half_capacity(self):
        text = _prm(axis="R", amp="", motor="αiS22/4000HV")
        meta = S.read_product_meta(text)
        self.assertEqual(meta["voltage"], "400V")
        self.assertEqual(meta["capacity"], "40A")    # 200Vなら80A

    def test_meta_uses_standard_when_no_table(self):
        text = _prm(axis="R", amp="", motor="αiS2/5000")
        meta = S.read_product_meta(text)        # 対応表なしでも標準で判定
        self.assertEqual(meta["capacity"], "20A")
        self.assertEqual(meta["capacity_src"], "標準")

    def test_table_overrides_standard(self):
        import tempfile
        p = Path(tempfile.mkdtemp(), "m.csv")
        p.write_text("モーター型式,容量\nαiS2/5000,40A\n", encoding="cp932")
        t = S.load_motor_caps(str(p))
        text = _prm(axis="R", amp="", motor="αiS2/5000")
        meta = S.read_product_meta(text, motor_caps=t)
        self.assertEqual(meta["capacity"], "40A")        # 対応表が標準を上書き
        self.assertEqual(meta["capacity_src"], "対応表")


class TestMotorCaps(unittest.TestCase):
    CSV = ("モーター型式,モーター番号,モーターID,容量,メモ\n"
           "αiS2/5000,A06B-0212-B000,262,20A,TWA-130\n"
           "αiS4/5000-B,A06B-2215-B000,,40,\n"
           "空行は無視,,, ,\n")

    def _table(self):
        d = tempfile.mkdtemp()
        p = Path(d, "motor.csv")
        p.write_text(self.CSV, encoding="cp932")
        return S.load_motor_caps(str(p))

    def test_lookup_by_number_model_id(self):
        t = self._table()
        self.assertEqual(S.capacity_for_motor(t, motor_no="A06B-0212-B000"), "20A")
        self.assertEqual(S.capacity_for_motor(t, motor_model="αiS4/5000-B"), "40A")
        self.assertEqual(S.capacity_for_motor(t, motor_id="262"), "20A")
        self.assertEqual(S.capacity_for_motor(t, motor_no="UNKNOWN"), "")

    def test_blank_capacity_rows_ignored(self):
        d = tempfile.mkdtemp()
        p = Path(d, "m.csv")
        p.write_text("モーター型式,容量\nαiS2/5000,\n", encoding="cp932")
        t = S.load_motor_caps(str(p))
        self.assertEqual(S.capacity_for_motor(t, motor_model="αiS2/5000"), "")

    def test_meta_uses_table_when_amp_empty(self):
        # 実データ相当: Servo Amp Model 空、Motor で対応表から容量を引く
        t = self._table()
        text = _prm(model="TWA-130", axis="R", amp="", motor="αiS2/5000")
        # Motor Number を実データに合わせる
        text = text.replace("A06B-2215-B000", "A06B-0212-B000")
        meta = S.read_product_meta(text, motor_caps=t)
        self.assertEqual(meta["capacity"], "20A")
        self.assertEqual(meta["capacity_src"], "対応表")
        self.assertEqual(meta["kind"], "回転")

    def test_meta_reads_param_2020(self):
        text = _prm(amp="")
        text = text.replace('"1815","A4","","","00000010",""\n',
                            '"1815","A4","","","00000010",""\n"2020","---","","","262",""\n')
        meta = S.read_product_meta(text)
        self.assertEqual(meta["motor_id"], "262")


class TestCapableControllers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        real = Path(__file__).resolve().parent.parent / "マスタ" / "制御装置マスタ.csv"
        cls.ctls = C.load_controllers(str(real))

    def test_single_axis_need(self):
        got = S.capable_controllers(self.ctls, ["160A"])
        units = {c.unit for c, _ in got}
        self.assertIn("10", units)   # 10号機は A=160A
        # 各割り当ては容量に一致
        for c, asg in got:
            self.assertEqual(c.caps[asg[0]], "160A")

    def test_two_axes_need_distinct(self):
        got = S.capable_controllers(self.ctls, ["160A", "160A"])
        for c, asg in got:
            self.assertEqual(len(set(asg)), 2)              # 別々の軸
            self.assertTrue(all(c.caps[a] == "160A" for a in asg))

    def test_unknown_capacity_uses_any_axis(self):
        got = S.capable_controllers(self.ctls, ["", ""])
        # 2軸以上ある号機はすべて該当
        for c, asg in got:
            self.assertEqual(len(set(asg)), 2)

    def test_empty_needs_returns_all(self):
        got = S.capable_controllers(self.ctls, [])
        self.assertEqual(len(got), len(self.ctls))

    def test_voltage_filter_400v(self):
        # 400V製品は400V号機(27/28)のみ。号機27は AV400V で 20A 軸あり
        got = S.capable_controllers(self.ctls, ["20A"], voltage="400V")
        units = {c.unit for c, _ in got}
        self.assertEqual(units, {"27", "28"})
        for c, _ in got:
            self.assertEqual(S.controller_voltage(c), "400V")

    def test_voltage_filter_200v_excludes_400v(self):
        got = S.capable_controllers(self.ctls, ["20A"], voltage="200V")
        units = {c.unit for c, _ in got}
        self.assertNotIn("27", units)
        self.assertNotIn("28", units)

    def test_controller_voltage_norm(self):
        c27 = next(c for c in self.ctls if c.unit == "27")
        self.assertEqual(S.controller_voltage(c27), "400V")
        c10 = next(c for c in self.ctls if c.unit == "10")
        self.assertEqual(S.controller_voltage(c10), "200V")


if __name__ == "__main__":
    unittest.main()
