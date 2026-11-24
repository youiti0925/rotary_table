# -*- coding: utf-8 -*-
"""かんたん作成（受注番号から）の「機械へ直接書く…」。

別の窓として作らず、既存の入口（受注番号 → 号機 → BASIC）に足したもの。
作る中身の決め方は「作成 → 出力先」と同じ（_gather・plan_prepare）で、土台だけを機械の控えに替える。
機械にはつながない（書く窓 FocasWriteDialog は偽物に差し替える）。
"""
import copy
import os
import shutil
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

PRODUCT = (
    ";   System Versio\nSystem Version=Ver.1.10\n;   Model parameters\nModel=RTV-510,WA\n"
    ";   Seiban parameters\nSeiban=50014984\nGear Rate='1/180\n;   Axis parameters\nAxis=R\n"
    "Direction=CCW\nMotor Number=A06B-2215-B000\nMotor Model=αiS8/4000-B\n"
    "Separate Detector=\nServo Amp Model=\n"
    '"番号","軸","和名","英名","値","説明"\n'
    '"2020","A5","","","299",""\n"2022","A5","","","1",""\n')


class WizardBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from nd287_app import settings as S
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path = S.settings_path
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"     # 本番の settings.json を触らせない

    @classmethod
    def tearDownClass(cls):
        from nd287_app import settings as S
        S.settings_path = cls._orig_path
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        from PySide6 import QtWidgets
        from nd287_app.settings import DEFAULTS
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.work = Path(tempfile.mkdtemp(dir=self._tmp))
        prod = self.work / "data"
        prod.mkdir()
        (prod / "R50014984.prm").write_text(PRODUCT, encoding="cp932")
        basic = self.work / "BASIC"
        basic.mkdir()
        shutil.copy(HERE / "fixtures" / "focas_param_31i.DAT", basic / "F23BASIC.dat")
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings.update(
            param_product_dir=str(prod), param_basic_dir=str(basic),
            controller_master_csv=str(REPO / "マスタ" / "制御装置マスタ.csv"),
            param_change_csv=str(self.work / "db.csv"),
            param_custom_index_csv=str(self.work / "idx.csv"),
            motor_capacity_csv=str(REPO / "マスタ" / "モーター容量マスタ.csv"))
        self.opened = []

    def wizard(self):
        import nd287_app.gui as G
        w = G.ParamWizardDialog(None, self.settings)
        w.warnings = []
        return w

    def ready(self, w, unit="23"):
        """受注番号 → 探す → 作るものにチェック → 号機を選ぶ（画面と同じ順）。"""
        w.e_seiban.setText("50014984")
        w.search()
        for f in w._files:
            f["chk"].setChecked(True)
        w._update_candidates()
        i = next((k for k, (c, _a) in enumerate(w._cand) if str(c.unit) == unit), None)
        self.assertIsNotNone(i, f"号機{unit}が候補にありません: {[str(c.unit) for c, _ in w._cand]}")
        w.cmb_ctrl.setCurrentIndex(w.cmb_ctrl.findData(i))
        return w

    def run_machine(self, w):
        import nd287_app.gui as G

        class Fake:
            def __init__(s, *a, **kw):
                self.opened.append((a, kw))

            def exec(s):
                return 0
        with mock.patch.object(G, "FocasWriteDialog", Fake), \
                mock.patch.object(G.QtWidgets.QMessageBox, "question",
                                  lambda *a, **k: G.QtWidgets.QMessageBox.Yes), \
                mock.patch.object(G.QtWidgets.QMessageBox, "warning",
                                  lambda *a, **k: w.warnings.append(a[2] if len(a) > 2 else "")):
            w.write_to_machine()


class TestWizardMachineWrite(WizardBase):
    def test_button_exists_next_to_create(self):
        w = self.wizard()
        self.assertEqual(w.b_machine.text(), "機械へ直接書く…")

    def test_opens_the_write_window_with_the_same_plan_input(self):
        w = self.ready(self.wizard())
        w.e_out.setText("")                       # 出力先は要らない
        self.run_machine(w)
        self.assertEqual(w.warnings, [])
        self.assertEqual(len(self.opened), 1)
        (args, kw) = self.opened[0]
        self.assertTrue(kw["auto"])                              # 自動接続・台帳を使う
        self.assertEqual(kw["seiban"], "50014984")
        self.assertEqual(len(kw["product_axes"]), 1)           # 割り当てた軸1本（どの軸かは号機の割り当て次第）
        self.assertIn("23", kw["subtitle"])
        # 土台を機械の控えに替えた plan_fn が、同じ prepare を通って作成結果を返す
        plan_fn = args[2]
        machine_raw = (HERE / "fixtures" / "focas_param_31i.DAT").read_text(encoding="cp932")
        plan = plan_fn(machine_raw)
        nums = {str(r[0]) for r in plan["rows"]}
        self.assertTrue({"2020", "2022"} <= nums, nums)

    def test_refuses_when_nothing_is_selected(self):
        w = self.wizard()
        self.run_machine(w)
        self.assertEqual(self.opened, [])
        self.assertTrue(any("作るもの" in t for t in w.warnings))

    def test_refuses_bit_options_for_machine_write(self):
        # DGPR・原点確立は BASIC を基準にビットを組む。機械の今の値の他のビットを壊すので、黙って無視せず断る
        w = self.ready(self.wizard())
        w.chk_zero.setChecked(True)
        self.run_machine(w)
        self.assertEqual(self.opened, [])
        self.assertTrue(any("DGPR" in t for t in w.warnings))
        w.chk_zero.setChecked(False)
        w.cmb_origin.setCurrentIndex(w.cmb_origin.findData("on"))
        w.warnings.clear()
        self.run_machine(w)
        self.assertEqual(self.opened, [])
        self.assertTrue(any("原点確立" in t for t in w.warnings))

    def test_create_to_file_still_needs_the_output_folder(self):
        import nd287_app.gui as G
        w = self.ready(self.wizard())
        w.e_out.setText("")
        with mock.patch.object(G.QtWidgets.QMessageBox, "warning",
                               lambda *a, **k: w.warnings.append(a[2])):
            w.create()
        self.assertTrue(any("出力先" in t for t in w.warnings))

    def test_guide_mentions_both_ways(self):
        w = self.ready(self.wizard())
        w.e_out.setText(str(self.work))
        w._update_guide()
        t = w.lbl_guide.text()
        self.assertIn("作成 → 出力先", t)
        self.assertIn("機械へ直接書く", t)


if __name__ == "__main__":
    unittest.main()
