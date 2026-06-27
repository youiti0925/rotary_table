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


if __name__ == "__main__":
    unittest.main()
