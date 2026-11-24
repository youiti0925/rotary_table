# -*- coding: utf-8 -*-
"""初めての制御装置: 控えを「その号機の BASIC」として登録する（偽物の機械・一時フォルダだけ）。"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from nd287_app import controllers, first_unit, focas_find   # noqa: E402

DAT = ("N01020Q1A1P88A2P89A3P90\n"
       "N02165Q1A1P25A2P45A3P99\n"
       "N02020Q1A1P1A2P2A3P3\n").encode("cp932")
CNC_ID = "AAAA-BBBB-CCCC-DDDD"


class Base(unittest.TestCase):
    def setUp(self):
        from nd287_app import settings as S
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = S.settings_path
        S.settings_path = lambda: self.tmp / "settings.json"     # 本番設定を読まない・作らない
        self.basic = self.tmp / "data" / "BASIC"
        self.basic.mkdir(parents=True)
        self.master = self.tmp / "制御装置マスタ.csv"
        self.folder = self.tmp / "控え" / "x"
        self.folder.mkdir(parents=True)
        (self.folder / "パラメータ.DAT").write_bytes(DAT)
        self.info = {"complete": True, "cnc_id": CNC_ID,
                     "cnc": {"cnc_type": "31", "mt_type": "B", "version": "G"}}
        self.settings = {"param_basic_dir": str(self.basic), "controller_master_csv": str(self.master),
                         "focas_registry_csv": str(self.tmp / "台帳.csv")}

    def tearDown(self):
        from nd287_app import settings as S
        S.settings_path = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def reg(self, unit="12"):
        return first_unit.register_first_unit(self.settings, self.folder, self.info, unit, app_dir=self.tmp)


class TestRegister(Base):
    def test_file_goes_to_separate_folder_bytes_unchanged(self):
        r = self.reg()
        dest = self.tmp / "data" / "機械から取ったBASIC" / "F12BASIC.dat"
        self.assertEqual(Path(r["file"]), dest)
        self.assertEqual(dest.read_bytes(), DAT)
        self.assertEqual(list(self.basic.iterdir()), [])          # BASIC フォルダには何も置かない

    def test_setting_overrides_folder(self):
        self.settings["basic_from_machine_dir"] = str(self.tmp / "別")
        self.reg()
        self.assertTrue((self.tmp / "別" / "F12BASIC.dat").is_file())

    def test_dest_inside_basic_refused(self):
        self.settings["basic_from_machine_dir"] = str(self.basic / "sub")
        with self.assertRaises(first_unit.FirstUnitError):
            self.reg()
        self.assertEqual(list(self.basic.iterdir()), [])

    def test_master_gets_provisional_row_with_blanks(self):
        r = self.reg()
        self.assertEqual(r["master"], "added")
        c = [x for x in controllers.load_controllers(self.master) if x.unit == "12"][0]
        self.assertEqual(c.cnc, "31i-B")
        self.assertEqual(c.axes(), ["X", "Y"])          # 容量が決まる軸だけ。Z は AMR 99 で容量不明
        self.assertEqual(c.voltage, "")
        self.assertEqual(c.servo, "")
        self.assertIn("Z軸の容量", r["blank"])

    def test_existing_unit_in_master_not_overwritten(self):
        controllers.append_units_to_master(
            self.master, [controllers.Controller("12", cnc="30i-B", caps={"X": "20A"})])
        before = self.master.read_bytes()
        with self.assertRaises(first_unit.FirstUnitError) as cm:
            self.reg()
        self.assertIn("もうあります", str(cm.exception))
        self.assertEqual(self.master.read_bytes(), before)
        self.assertFalse((self.tmp / "data" / "機械から取ったBASIC").exists())

    def test_existing_basic_not_overwritten(self):
        (self.basic / "F12BASIC.PRM").write_bytes(b"x")
        with self.assertRaises(first_unit.FirstUnitError):
            self.reg()

    def test_registry_remembers_pair(self):
        self.reg()
        self.assertEqual(focas_find.unit_for(self.tmp / "台帳.csv", CNC_ID), "12")

    def test_basic_file_for_unit_finds_it(self):
        self.assertIsNone(controllers.basic_file_for_unit(str(self.basic), "12"))
        self.reg()
        p = controllers.basic_file_for_unit(str(self.basic), "12")
        self.assertEqual(Path(p).name, "F12BASIC.dat")
        self.assertIsNone(controllers.basic_file_for_unit(str(self.basic), "1"))

    def test_incomplete_or_bad_unit_refused(self):
        with self.assertRaises(first_unit.FirstUnitError):
            self.reg("")
        with self.assertRaises(first_unit.FirstUnitError):
            self.reg("abc")
        self.info["complete"] = False
        with self.assertRaises(first_unit.FirstUnitError):
            self.reg()


if __name__ == "__main__":
    unittest.main()
