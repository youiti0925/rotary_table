# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app import controllers as C

REAL = Path(__file__).resolve().parent.parent / "マスタ" / "制御装置マスタ.csv"


class TestControllerMaster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctls = C.load_controllers(str(REAL))

    def test_loads_all_units(self):
        units = [c.unit for c in self.ctls]
        self.assertEqual(len(units), 23)
        self.assertIn("10", units)
        self.assertIn("86", units)

    def test_capacities_per_axis(self):
        c10 = next(c for c in self.ctls if c.unit == "10")
        self.assertEqual(c10.cnc, "18i-M")
        self.assertEqual([c10.caps[a] for a in ("X", "Y", "Z", "A")],
                         ["20A", "40A", "80A", "160A"])
        # 号機23 は B/C 軸まである
        c23 = next(c for c in self.ctls if c.unit == "23")
        self.assertEqual(c23.caps["B"], "80A")
        self.assertEqual(c23.caps["C"], "160A")
        self.assertEqual(c23.axes(), ["X", "Y", "Z", "A", "B", "C"])

    def test_all_capacities_sorted(self):
        self.assertEqual(C.all_capacities(self.ctls), ["20A", "40A", "80A", "160A"])

    def test_filter_by_capacity(self):
        # 20A の軸を持つ号機だけ
        got = {c.unit for c in C.filter_by_capacity(self.ctls, "20A")}
        self.assertIn("10", got)       # X=20A
        self.assertNotIn("17", got)    # 17 は全軸160A
        # 容量空なら全件
        self.assertEqual(len(C.filter_by_capacity(self.ctls, "")), 23)

    def test_axes_with_capacity(self):
        c10 = next(c for c in self.ctls if c.unit == "10")
        self.assertEqual(c10.axes_with_capacity("80A"), ["Z"])
        self.assertEqual(c10.axes_with_capacity("160A"), ["A"])

    def test_norm_cap(self):
        self.assertEqual(C._norm_cap("40"), "40A")
        self.assertEqual(C._norm_cap(" 80a "), "80A")
        self.assertEqual(C._norm_cap(""), "")

    def test_basic_file_for_unit_token_match(self):
        d = tempfile.mkdtemp()
        for n in ("F10BASIC.PRM", "F100BASIC.PRM", "F86BASIC.prm"):
            (Path(d) / n).write_text("%\n", encoding="cp932")
        self.assertTrue(C.basic_file_for_unit(d, "10").endswith("F10BASIC.PRM"))
        self.assertTrue(C.basic_file_for_unit(d, "86").endswith("F86BASIC.prm"))
        # '1' は 'F100'/'F10' の数字トークン(100/10)に一致しない
        self.assertIsNone(C.basic_file_for_unit(d, "1"))
        self.assertIsNone(C.basic_file_for_unit(d, "99"))

    def test_basic_file_varied_extensions(self):
        # 実データは .DAT・拡張子なし・.dat 等もある。すべて拾えること
        d = tempfile.mkdtemp()
        (Path(d) / "F35BASIC").write_text("%\n", encoding="cp932")        # 拡張子なし
        (Path(d) / "F10BASIC.dat").write_text("%\n", encoding="cp932")    # .dat
        (Path(d) / "F86BASIC.DAT").write_text("%\n", encoding="cp932")    # .DAT
        self.assertTrue(C.basic_file_for_unit(d, "35").endswith("F35BASIC"))
        self.assertTrue(C.basic_file_for_unit(d, "10").endswith("F10BASIC.dat"))
        self.assertTrue(C.basic_file_for_unit(d, "86").endswith("F86BASIC.DAT"))

    def test_basic_file_prefers_prm(self):
        # 同じ号機に複数あれば .prm を優先
        d = tempfile.mkdtemp()
        (Path(d) / "F30BASIC.DAT").write_text("%\n", encoding="cp932")
        (Path(d) / "F30BASIC.PRM").write_text("%\n", encoding="cp932")
        self.assertTrue(C.basic_file_for_unit(d, "30").endswith("F30BASIC.PRM"))


if __name__ == "__main__":
    unittest.main()
