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


class TestBasicScan(unittest.TestCase):
    # 実機同様 \n\r\r 区切りのネイティブBASIC（X,Y,Z,A 軸、AMR=25/45/85/165）
    BASIC = ("%\n\r\r"
             "N01020Q1A1P88A2P89A3P90A4P65A5P0A6P0\n\r\r"
             "N01023Q1A1P1A2P2A3P3A4P4\n\r\r"
             "N02020Q1A1P255A2P273A3P293A4P303\n\r\r"
             "N02165Q1A1P25A2P45A3P85A4P165\n\r\r"
             "%\n")

    def test_amr_to_capacity(self):
        self.assertEqual(C.amr_to_capacity(25), "20A")
        self.assertEqual(C.amr_to_capacity(45), "40A")
        self.assertEqual(C.amr_to_capacity(85), "80A")
        self.assertEqual(C.amr_to_capacity(165), "160A")
        self.assertEqual(C.amr_to_capacity(13), "10A")     # AMR-5寄せ
        self.assertEqual(C.amr_to_capacity(""), "")
        self.assertEqual(C.amr_to_capacity(999), "")

    def test_controller_from_basic_text(self):
        ctl = C.controller_from_basic_text(self.BASIC, "30")
        self.assertEqual(ctl.unit, "30")
        self.assertEqual(ctl.axes(), ["X", "Y", "Z", "A"])
        self.assertEqual(ctl.caps_text(), "X:20A Y:40A Z:80A A:160A")
        self.assertIn("ID255", ctl.amps["X"])

    def test_missing_2020_does_not_write_none(self):
        # 2020(モーターID)が無いBASIC → "IDNone/AMR25" をマスタに書かない
        basic = ("N01020Q1A1P88A2P89\r\n"
                 "N02165Q1A1P25A2P45\r\n")
        ctl = C.controller_from_basic_text(basic, "9")
        self.assertEqual(ctl.amps["X"], "AMR25")
        self.assertEqual(ctl.amps["Y"], "AMR45")
        self.assertNotIn("None", ctl.amps["X"])

    def test_scan_basic_folder(self):
        d = tempfile.mkdtemp()
        (Path(d) / "F30BASIC.DAT").write_bytes(self.BASIC.encode("cp932"))
        (Path(d) / "F84BASIC").write_bytes(
            self.BASIC.replace("A1P25A2P45A3P85A4P165",
                               "A1P85A2P165A3P85A4P165").encode("cp932"))
        scanned = C.scan_basic_folder(d)
        units = {c.unit: c for c, _ in scanned}
        self.assertEqual(set(units), {"30", "84"})
        self.assertEqual(units["84"].caps_text(), "X:80A Y:160A Z:80A A:160A")

    def test_diff_and_append(self):
        ctl30 = C.controller_from_basic_text(self.BASIC, "30")
        ctl10 = C.controller_from_basic_text(self.BASIC, "10")
        existing = [C.Controller("10", caps={"X": "20A", "Y": "40A", "Z": "80A", "A": "160A"})]
        diff = C.diff_scanned_vs_master([(ctl30, "F30"), (ctl10, "F10")], existing)
        by = {d["unit"]: d for d in diff}
        self.assertEqual(by["30"]["status"], "new")
        self.assertEqual(by["10"]["status"], "same")
        # 追記して読み直すと号機30が増えている（既存は保持）
        d = tempfile.mkdtemp()
        path = Path(d) / "master.csv"
        path.write_bytes(("," .join(C.MASTER_HEADER) + "\r\n"
                          "10,18i-M,,AC200V,,20A,40A,80A,160A\r\n").encode("cp932"))
        added = C.append_units_to_master(str(path), [ctl30])
        self.assertEqual(added, 1)
        reloaded = {c.unit: c for c in C.load_controllers(str(path))}
        self.assertEqual(set(reloaded), {"10", "30"})
        self.assertEqual(reloaded["30"].caps_text(), "X:20A Y:40A Z:80A A:160A")
        self.assertEqual(reloaded["10"].cnc, "18i-M")      # 既存行は保持


class TestManualRegister(unittest.TestCase):
    def _path(self):
        d = tempfile.mkdtemp()
        p = Path(d) / "m.csv"
        p.write_bytes((",".join(C.MASTER_HEADER) + "\r\n"
                       "10,18i-M,,AC200V,,20A,40A,80A,160A\r\n").encode("cp932"))
        return str(p)

    def test_add_new_controller(self):
        path = self._path()
        ctl = C.Controller("40", cnc="0i-MF", voltage="AC200V",
                           caps={"X": "40A", "A": "160A"})
        self.assertEqual(C.upsert_controller(path, ctl), "added")
        d = {c.unit: c for c in C.load_controllers(path)}
        self.assertEqual(set(d), {"10", "40"})
        self.assertEqual(d["40"].cnc, "0i-MF")
        self.assertEqual(d["40"].caps_text(), "X:40A A:160A")
        self.assertEqual(d["10"].cnc, "18i-M")          # 既存は保持

    def test_update_existing_controller(self):
        path = self._path()
        ctl = C.Controller("10", cnc="18i-MB", voltage="AC200V",
                           caps={"X": "20A", "Y": "40A", "Z": "80A", "A": "160A"})
        self.assertEqual(C.upsert_controller(path, ctl), "updated")
        d = {c.unit: c for c in C.load_controllers(path)}
        self.assertEqual(len(d), 1)
        self.assertEqual(d["10"].cnc, "18i-MB")

    def test_delete_controller(self):
        path = self._path()
        self.assertTrue(C.delete_controller(path, "10"))
        self.assertEqual(C.load_controllers(path), [])
        self.assertFalse(C.delete_controller(path, "99"))


if __name__ == "__main__":
    unittest.main()
