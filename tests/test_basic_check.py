# -*- coding: utf-8 -*-
"""BASICを選んだ時点の点検（GUIの実メソッドを呼ぶ）

制御装置に使えるかを「作った後」ではなく「選んだ時点」で見る。
"""
import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from nd287_app import fanuc_param
from nd287_app.gui import ParamDialog
from nd287_app.settings import DEFAULTS

MACHINE = ("%\n\r\rN00000Q1L1P00000010\n\r\rN01825Q1A1P3000A4P3000\n\r\r%\n\r\r")
# 同じ内容＋その制御装置に無い番号（N09999）を足したPC製
PC_WITH_EXTRA = ("%\nN00000Q1L1P00000010\nN01825Q1A1P3000A4P3000\n"
                 "N09999Q1L1P00000000\n%\n")

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class BasicCheckBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.d = Path(self.dir.name)
        self.dlg = ParamDialog(None, copy.deepcopy(DEFAULTS))

    def tearDown(self):
        self.dlg.deleteLater()
        self.dir.cleanup()

    def write(self, name, text):
        p = self.d / name
        p.write_bytes(text.encode("cp932"))
        return p


class TestReferenceLookup(BasicCheckBase):
    def test_machine_basic_needs_no_reference(self):
        # 実機のものは、それ自体が実機のバックアップ＝照合する相手が要らない
        p = self.write("F23BASIC.DAT", MACHINE)
        self.assertEqual(self.dlg._reference_backup(str(p)), "")

    def test_finds_same_unit_machine_file_next_to_it(self):
        # F23BASIC.prm を選ぶと、隣の F23BASIC.DAT を自動で照合相手にする
        self.write("F23BASIC.DAT", MACHINE)
        pc = self.write("F23BASIC.prm", PC_WITH_EXTRA)
        ref = self.dlg._reference_backup(str(pc))
        self.assertIn("N00000", ref)
        self.assertNotIn("N09999", ref)

    def test_does_not_use_other_units(self):
        # 別の号機のファイルを照合相手にしてはいけない
        self.write("F35BASIC.DAT", MACHINE)
        pc = self.write("F23BASIC.prm", PC_WITH_EXTRA)
        self.assertEqual(self.dlg._reference_backup(str(pc)), "")

    def test_falls_back_to_settings_backup(self):
        backup = self.write("bk.DAT", MACHINE)
        pc = self.write("F23BASIC.prm", PC_WITH_EXTRA)
        self.dlg.settings["param_master_backup"] = str(backup)
        self.assertIn("N00000", self.dlg._reference_backup(str(pc)))

    def test_missing_file_is_not_fatal(self):
        self.assertEqual(self.dlg._reference_backup("/no/such/file"), "")


class TestCheckBasic(BasicCheckBase):
    def test_machine_basic_passes_without_asking(self):
        # 実機のBASICは黙って通る（確認ダイアログを出さない＝Trueが即返る）
        p = self.write("F23BASIC.DAT", MACHINE)
        self.assertTrue(self.dlg._check_basic(str(p)))

    def test_old_format_machine_basic_passes(self):
        old = "%\n\r\rN00000 P 00000010\n\r\rN01825 A1 P 3000 A4 P 3000\n\r\r%\n\r\r"
        p = self.write("F12BASIC.DAT", old)
        self.assertTrue(self.dlg._check_basic(str(p)))

    def test_unreadable_file_does_not_block(self):
        self.assertTrue(self.dlg._check_basic("/no/such/file"))

    def test_extra_numbers_are_detected_via_sibling(self):
        # 確認ダイアログは出さずに、検出そのものを確かめる
        self.write("F23BASIC.DAT", MACHINE)
        pc = self.write("F23BASIC.prm", PC_WITH_EXTRA)
        ref = self.dlg._reference_backup(str(pc))
        extra = fanuc_param.unknown_numbers(PC_WITH_EXTRA, ref)
        self.assertEqual(extra, [9999])


if __name__ == "__main__":
    unittest.main()
