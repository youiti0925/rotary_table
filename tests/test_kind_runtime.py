# -*- coding: utf-8 -*-
"""測定の種類（モーター／駆動装置／フルクロ）を、測定の流れ・プログラム作り・保存へつなぐ部分。
表・保存先はすべて一時フォルダ（実際のマスタには触らない）。"""
import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nd287_app import kind_conditions as K
from nd287_app import kind_runtime as KR
from nd287_app import wizard_programs as WP

ROWS = [
    {"型式": "TK-1", "測定の種類": "セミ・モーター", "歯数": 72, "ブロック数": 4, "回数": 7, "再現開始": 0, "再現終了": 270},
    {"型式": "TK-1", "測定の種類": "セミ・駆動装置"},                                  # 刻みが決まらない
    {"型式": "TK-1", "測定の種類": "フル・スケール付き", "スケール名": "A", "ホイール刻み": 5},
]


def make_table(rows=ROWS, tilt_rows=()):
    d = tempfile.mkdtemp()
    p = Path(d) / K.FILE_NAME
    K.build_template(p, rows, tilt_rows)
    return p, K.load(p)


class TestEnsureFile(unittest.TestCase):
    def test_default_setting_key(self):
        from nd287_app.settings import DEFAULTS
        self.assertEqual(DEFAULTS["kind_conditions_xlsx"], "マスタ/測定条件_種類別.xlsx")

    def test_creates_once_from_existing_and_never_overwrites(self):
        d = Path(tempfile.mkdtemp())
        masters = {"user_rotary": {"AB-1": {"型式": "AB-1", "ホイール刻み": "7.5", "ウォーム刻み": "0.5",
                                            "ウォーム範囲": "5", "ウォーム開始": "0"}}}
        st = {"kind_conditions_xlsx": str(d / "sub" / "k.xlsx")}
        t = KR.load_table(st, masters)
        self.assertTrue((d / "sub" / "k.xlsx").exists())
        self.assertEqual(t["problems"], [])
        rec = K.find(t, "AB-1", False)                     # 今までの登録を、種類なしで写している
        self.assertEqual(rec["ホイール刻み"], "7.5")
        self.assertEqual(rec["測定の種類"], "")
        before = (d / "sub" / "k.xlsx").read_bytes()
        KR.load_table(st, {"user_rotary": {}})              # 2回目は作らない・上書きしない
        self.assertEqual((d / "sub" / "k.xlsx").read_bytes(), before)
        created, why = KR.ensure_file(d / "sub" / "k.xlsx", {})
        self.assertFalse(created)

    def test_cannot_create_is_reported_not_silent(self):
        d = Path(tempfile.mkdtemp())
        (d / "file").write_text("x")                        # 親がファイル → フォルダを作れない
        t = KR.load_table({"kind_conditions_xlsx": str(d / "file" / "k.xlsx")}, {})
        self.assertTrue(t["problems"])
        self.assertIn("作れませんでした", KR.problems_text(t))

    def test_duplicates_and_unknown_kind_are_problems(self):
        _p, t = make_table([{"型式": "D-1", "測定の種類": "セミ・モーター", "歯数": 10},
                            {"型式": "D-1", "測定の種類": "セミ・モーター", "歯数": 20},
                            {"型式": "D-2", "測定の種類": "でたらめ", "ホイール刻み": 3}])
        text = KR.problems_text(t)
        self.assertIn("重なって", text)
        self.assertIn("分かりません", text)



class TestProgramsByKind(unittest.TestCase):
    def setUp(self):
        self.p, self.t = make_table()
        self.masters = {"user_rotary": {"TK-1": {"型式": "TK-1", "ホイール刻み": "10", "ウォーム刻み": "0.5",
                                                 "ウォーム範囲": "5", "ウォーム開始": "0"}}}

    def test_no_kind_is_exactly_as_before(self):
        a = WP.division_conditions(self.masters, "TK-1", False)
        b = WP.division_conditions(self.masters, "TK-1", False, kind="", kind_table=self.t)
        self.assertEqual(a, b)
        self.assertEqual(a["params"]["wheel_pitch"], 10.0)
        self.assertIsNone(WP.repeat_conditions(self.masters, "TK-1", False, kind="", kind_table=self.t))

    def test_motor_uses_tooth_division(self):
        d = WP.division_conditions(self.masters, "TK-1", False, kind="セミ・モーター", kind_table=self.t)
        self.assertAlmostEqual(d["params"]["wheel_pitch"], 0.5)          # 360/72/10
        self.assertEqual((d["params"]["wheel_start"], d["params"]["wheel_end"]), (0.0, 360.0))
        self.assertNotIn("error", d)
        self.assertIn("モーター", d["source"])
        r = WP.repeat_conditions(self.masters, "TK-1", False, kind="セミ・モーター", kind_table=self.t)
        self.assertEqual(r["repeats"], 7)
        self.assertEqual(len(r["blocks"]), 4)

    def test_kind_without_repeat_has_no_repeat(self):
        # フルには再現性が書かれていない → 作らない（モーターの値も、今までの登録も使わない）
        self.assertIsNone(WP.repeat_conditions(self.masters, "TK-1", False, kind="フル・スケール付き", kind_table=self.t))

    def test_drive_without_pitch_has_error_and_no_program(self):
        d = WP.division_conditions(self.masters, "TK-1", False, kind="セミ・駆動装置", kind_table=self.t)
        self.assertTrue(d["error"])
        self.assertIsNone(d["params"])
        plan = WP.make_plan({}, self.masters, "TK-1", ["R"], kind="セミ・駆動装置", kind_table=self.t)
        self.assertEqual(plan["programs"], [])
        self.assertTrue(plan["problems"])
        self.assertIn("刻み", plan["problems"][0])
        self.assertEqual(plan["choice"], "")

    def test_make_plan_motor_builds_with_motor_pitch(self):
        plan = WP.make_plan({}, self.masters, "TK-1", ["R"], kind="セミ・モーター", kind_table=self.t)
        self.assertEqual(plan["problems"], [])
        self.assertTrue(plan["programs"])
        self.assertIn("0.5°ごとに 721点", plan["programs"][0]["lines"][plan["programs"][0]["numbers"][0]])
        plain = WP.make_plan({}, self.masters, "TK-1", ["R"])
        self.assertIn("10°ごとに", plain["programs"][0]["lines"][plain["programs"][0]["numbers"][0]])

    def test_machine_in_prog_plan_passes_kind(self):
        from nd287_app import easy_machine_in as M
        plan = M.prog_plan({}, "TK-1", ["R"], masters_dict=self.masters, kind="セミ・駆動装置", kind_table=self.t)
        self.assertEqual(plan["programs"], [])
        self.assertTrue(plan["problems"])
        ok = M.prog_plan({}, "TK-1", ["R"], masters_dict=self.masters, kind="セミ・モーター", kind_table=self.t)
        self.assertTrue(ok["programs"])

    def test_model_without_kinds_ignores_kind(self):
        m = {"user_rotary": {"ZZ-9": {"型式": "ZZ-9", "ホイール刻み": "12"}}}
        a = WP.division_conditions(m, "ZZ-9", False, kind="セミ・モーター", kind_table=self.t)
        self.assertEqual(a["params"]["wheel_pitch"], 12.0)

    def test_tilt_kind(self):
        _p, t = make_table(tilt_rows=[{"型式": "TT-1", "測定の種類": "セミ・駆動装置", "開始角度": -10, "終了角度": 90, "刻み": 5}])
        d = WP.division_conditions({}, "TT-1", True, kind="セミ・駆動装置", kind_table=t)
        self.assertEqual((d["params"]["wheel_start"], d["params"]["wheel_end"], d["params"]["wheel_pitch"]), (-10.0, 90.0, 5.0))


class TestHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from nd287_app import settings as S
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path = S.settings_path
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"

    @classmethod
    def tearDownClass(cls):
        from nd287_app import settings as S
        S.settings_path = cls._orig_path
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        from PySide6 import QtWidgets
        from nd287_app import gui as G
        from nd287_app.nd287 import DummyDevice
        from nd287_app.settings import DEFAULTS
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        t = Path(tempfile.mkdtemp(dir=self._tmp))
        self.t = t
        K.build_template(t / "k.xlsx", ROWS)
        st = copy.deepcopy(DEFAULTS)
        st.update(focas_auto_find=False, save_root=str(t / "save"), bs_save_root=str(t / "bs"), webapp_sync_enabled=False,
                  kind_conditions_xlsx=str(t / "k.xlsx"),
                  user_rotary_csv=str(t / "r.csv"), user_tilt_csv=str(t / "t.csv"),
                  user_rotary_repeat_csv=str(t / "rr.csv"), user_tilt_repeat_csv=str(t / "tr.csv"))
        self.w = G.MainWindow(DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        self.w.flow_timer.stop()

    def tearDown(self):
        self.w.flow_timer.stop()
        self.w.close()
        self.w.deleteLater()

    def fill_data(self):
        from nd287_app.export import SERIES_KEYS
        w = self.w
        pts = [i * 10.0 for i in range(37)]
        w.data = {k: (list(pts), list(pts)) for k in SERIES_KEYS}
        w.view_kind = "indexing"
        w.e_machine.setText("M100")

    def test_combo_appears_only_with_kinds_and_applies_values(self):
        w = self.w
        w.e_model.setText("NO-KINDS")
        w.on_model_entered()
        self.assertTrue(w.kind_btn_box.isHidden())
        w.e_model.setText("TK-1")
        w.on_model_entered()
        self.assertFalse(w.kind_btn_box.isHidden())
        self.assertEqual([w.cmb_kind.itemText(i) for i in range(w.cmb_kind.count())][1:], ["モーター", "駆動装置", "フルクロ"])
        w.cmb_kind.setCurrentIndex(w.cmb_kind.findData("セミ・モーター"))
        self.assertEqual(w.measure_kind, "セミ・モーター")
        self.assertAlmostEqual(w.e_wheel.value(), 0.5)
        st = w.easy_kind_state()
        self.assertEqual(st["kind"], "セミ・モーター")
        self.assertEqual(st["error"], "")
        self.assertEqual([k["label"] for k in st["kinds"]], ["モーター", "駆動装置", "フルクロ"])

    def test_undecidable_pitch_is_shown_and_not_applied(self):
        w = self.w
        w.e_model.setText("TK-1")
        w.e_wheel.setValue(3.0)
        w.easy_set_kind("セミ・駆動装置")
        self.assertAlmostEqual(w.e_wheel.value(), 3.0)                  # 10° にも他の値にもしない
        self.assertTrue(w.easy_kind_state()["error"])
        self.assertIn("決められません", w.statusBar().currentMessage())

    def test_kind_problems_are_visible(self):
        from nd287_app import gui as G
        w = self.w
        K.build_template(self.t / "k2.xlsx", [{"型式": "D-1", "測定の種類": "セミ・モーター", "歯数": 10},
                                              {"型式": "D-1", "測定の種類": "セミ・モーター", "歯数": 20}])
        w.settings["kind_conditions_xlsx"] = str(self.t / "k2.xlsx")
        w.reload_kind_table()
        self.assertFalse(w.lbl_kind_problem.isHidden())
        self.assertIn("重なって", w.lbl_kind_problem.text())
        self.assertIn("重なって", w.easy_kind_state()["problems"])

    def test_semi_and_full_save_names_differ_by_suffix(self):
        """セミ（正規モーター）=末尾なし／フル=C。同じ機番でも別の名前（フルを上書きしない）。BS・Webも同じ名前。"""
        w = self.w
        calls = []
        w.save_bs_file = lambda no, *a, **k: calls.append(("bs", no))
        w.sync_to_webapp = lambda no, *a, **k: calls.append(("web", no))
        w.e_model.setText("TK-1")
        w.on_model_entered()
        self.fill_data()
        w.easy_set_kind("フル・スケール付き")
        self.assertEqual(w.suffix_sel.suffix(), "C")
        w.save()
        w.easy_set_kind("セミ・モーター")
        self.assertEqual(w.suffix_sel.suffix(), "")
        w.save()
        d = self.t / "save" / "TK"
        self.assertTrue((d / "M100C.csv").exists())
        self.assertTrue((d / "M100.csv").exists())
        self.assertEqual(calls, [("bs", "M100C"), ("web", "M100C"), ("bs", "M100"), ("web", "M100")])
        self.assertIn("モーター", (d / "M100.csv").read_text(encoding="cp932"))

    def test_real_bs_file_is_written_for_semi_and_full(self):
        w = self.w
        w.sync_to_webapp = lambda *a, **k: None
        w.e_model.setText("TK-1")
        w.on_model_entered()
        self.fill_data()
        w.easy_set_kind("セミ・モーター")
        w.save()
        w.easy_set_kind("フル・スケール付き")
        w.save()
        names = sorted(x.name.upper() for x in (self.t / "bs").rglob("*") if x.is_file())
        self.assertIn("M100.BS", names)
        self.assertIn("M100C.BS", names)

    def test_auto_preselection(self):
        w = self.w
        w.e_model.setText("TK-1")
        w.on_model_entered()
        w.easy_set_kind("セミ・駆動装置")                     # 駆動装置のセミ → K（刻みの登録が無くても末尾は決まる）
        self.assertEqual(w.suffix_sel.suffix(), "K")
        w.easy_set_kind("セミ・モーター")                     # セミのモーター（正規）→ 末尾なし
        self.assertEqual(w.suffix_sel.suffix(), "")
        w.easy_set_kind("フル・スケール付き")                 # フル → クローズだけ
        self.assertEqual(w.suffix_sel.suffix(), "C")
        w.mode_combo.setCurrentText("傾斜分割")               # モードが変わっても、単品（TT/RR）は自動では選ばない
        self.assertEqual(w.suffix_sel.suffix(), "C")
        w.mode_combo.setCurrentText("回転分割")
        w.settings["fanuc_clamp_enabled"] = True              # クランプ分割 → G
        w.apply_auto_suffix()
        self.assertEqual(w.suffix_sel.suffix(), "GC")
        w.settings["fanuc_clamp_enabled"] = False

        class Dlg:                                           # かんたん作成で仮モーターを選んだ
            class cmb_motor:
                @staticmethod
                def currentData():
                    return "temp"
        w._take_wizard_motor(Dlg)
        self.assertEqual(w.suffix_sel.suffix(), "IC")

    def test_defaults_never_pick_single_or_set_columns(self):
        """単品(T/R)・完組は、人が押したときだけ。自動はセミ=押さない／フル=クローズ(C)だけ。未登録の型式のセミ（正規）は機番そのまま。"""
        w = self.w
        w.e_model.setText("NO-KINDS")                       # 種類が未登録の型式
        w.on_model_entered()
        self.assertEqual(w.suffix_sel.kinds, ["close"])
        self.assertEqual(w.suffix_sel.suffix(), "C")
        for mode in ("回転分割", "傾斜分割", "回転再現性", "傾斜再現性"):
            w.mode_combo.setCurrentText(mode)
            self.assertEqual(w.suffix_sel.suffix(), "C", mode)

        class Dlg:                                           # かんたん作成で「セミクロにする」を選んだ
            class cmb_motor:
                @staticmethod
                def currentData():
                    return "product"

            class cmb_semi:
                @staticmethod
                def currentData():
                    return "semi"
        w._take_wizard_motor(Dlg)
        self.assertEqual(w.suffix_sel.kinds, [])
        self.assertEqual(w.suffix_sel.suffix(), "")          # セミ・正規モーター・クランプなし → 機番だけ
        w.e_machine.setText("900")
        self.fill_data_with(w)
        w.sync_to_webapp = lambda *a, **k: None
        w.save_bs_file = lambda *a, **k: None
        w.save()
        self.assertTrue((self.t / "save" / "NO" / "900.csv").exists())

    def fill_data_with(self, w):
        from nd287_app.export import SERIES_KEYS
        pts = [i * 10.0 for i in range(37)]
        w.data = {k: (list(pts), list(pts)) for k in SERIES_KEYS}
        w.view_kind = "indexing"

    def test_panel_click_gives_table_cells_and_save_names(self):
        """表のマスを押して選ぶ → その名前で保存（表のセルのまま）。"""
        from nd287_app.serial_suffix_ui import ROWS, COLUMNS
        w = self.w
        w.e_model.setText("NO-KINDS")
        w.on_model_entered()
        self.fill_data()
        w.sync_to_webapp = lambda *a, **k: None
        w.save_bs_file = lambda *a, **k: None
        picker = w.suffix_panel.picker

        def click(motor, clamp, kind, axis=1):
            picker._clicked(next(i for i, r in enumerate(ROWS) if r[:2] == (motor, clamp)),
                            next(i for i, c in enumerate(COLUMNS) if c[:2] == (kind, axis)))
        w.suffix_sel.__init__("normal", False, [])             # まっさらから
        click("driver", True, "semi")
        self.assertEqual(w.suffix_sel.suffix(), "KG")
        click("normal", False, "rotary")
        click("normal", False, "close")
        self.assertEqual(w.suffix_sel.suffix(), "RRC")
        click("temp", False, "multi", 2)
        self.assertEqual(w.suffix_sel.suffix(), "IR2C")            # 仮・回転・多軸2軸目・クローズ（表の左から順）
        self.assertEqual(w.lbl_save_name.text().split("（")[0], "保存する名前: M100" + w.suffix_sel.suffix())
        w.save()
        self.assertTrue((self.t / "save" / "NO" / "M100IR2C.csv").exists(), list((self.t / "save").rglob("*")))

    def test_ng_number_increments_when_same_name_exists(self):
        from PySide6 import QtWidgets
        w = self.w
        calls = []
        w.save_bs_file = lambda no, *a, **k: calls.append(("bs", no))
        w.sync_to_webapp = lambda no, *a, **k: calls.append(("web", no))
        asked = []
        orig = QtWidgets.QMessageBox.question
        QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(a[2]), QtWidgets.QMessageBox.Yes)[1])
        self.addCleanup(lambda: setattr(QtWidgets.QMessageBox, "question", orig))
        w.e_model.setText("TK-1")
        w.on_model_entered()
        w.easy_set_kind("フル・スケール付き")
        self.fill_data()
        w.save()
        self.assertEqual(asked, [])                                 # 初回は聞かない
        w.save()
        w.save()
        d = self.t / "save" / "TK"
        self.assertTrue((d / "M100C.csv").exists())
        self.assertTrue((d / "M100C-X1.csv").exists())
        self.assertTrue((d / "M100C-X2.csv").exists())
        self.assertEqual([c for c in calls if c[0] == "bs"], [("bs", "M100C"), ("bs", "M100C-X1"), ("bs", "M100C-X2")])
        self.assertEqual([c for c in calls if c[0] == "web"], [("web", "M100C"), ("web", "M100C-X1"), ("web", "M100C-X2")])
        self.assertIn("-X1", asked[0])
        self.assertEqual(w.suffix_sel.ng, 0)                        # 保存したあとは -X なしに戻る

    def test_ng_proposal_declined_does_not_save(self):
        from PySide6 import QtWidgets
        w = self.w
        w.save_bs_file = lambda *a, **k: None
        w.sync_to_webapp = lambda *a, **k: None
        w.e_model.setText("TK-1")
        w.on_model_entered()
        self.fill_data()
        w.save()
        orig = QtWidgets.QMessageBox.question
        QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.No)
        self.addCleanup(lambda: setattr(QtWidgets.QMessageBox, "question", orig))
        w.save()
        d = self.t / "save" / "TK"
        self.assertEqual(sorted(x.name for x in d.glob("*.csv")), ["M100C.csv"])
        self.assertEqual(w.suffix_sel.ng, 1)                        # 提案の数字は、表に残って直せる


    def test_kind_reset_when_model_has_no_kinds(self):
        w = self.w
        w.e_model.setText("TK-1")
        w.easy_set_kind("セミ・モーター")
        w.e_model.setText("NO-KINDS")
        w.on_model_entered()
        self.assertEqual(w.active_kind(), "")
        self.assertEqual(w.measure_kind, "")

    def test_wizard_dialog_gets_kind(self):
        w = self.w
        w.e_model.setText("TK-1")
        w.on_model_entered()
        w.easy_set_kind("セミ・モーター")
        dlg = w.easy_make_wizard(w)
        self.assertEqual(dlg._kind, "セミ・モーター")
        self.assertIsNotNone(dlg._p_kind_table())
        dlg.deleteLater()
        env = w.easy_machine_in_env()
        self.assertEqual(env["kind_fn"]()[0], "セミ・モーター")


class TestEasyStep(TestHost):
    """はじめてモードの「測り方を確かめる」の段。"""

    def open_step(self, model="TK-1"):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        import test_easy_mode as TE
        from nd287_app import easy_mode, easy_flow
        self.w.e_model.setText(model)
        self.w.on_model_entered()
        dlg = easy_mode.EasyModeDialog(self.w, TE.FakePoller(TE.S(aut=0, emergency=1)))
        self.app.processEvents()                                                  # 起動時の「ホームへ」を先に済ませる
        dlg._start_flow("measure")
        dlg._enter(easy_mode.INDEX["cond_check"])
        self.assertEqual(easy_mode.STEPS[dlg.i]["id"], "cond_check")
        self.addCleanup(dlg.deleteLater)
        self.addCleanup(dlg._timer.stop)
        return dlg, easy_flow

    def test_kind_buttons_gate_next_and_pass_kind_through(self):
        dlg, F = self.open_step()
        self.assertEqual(sorted(dlg._kind_btns), sorted(["セミ・モーター", "セミ・駆動装置", "フル・スケール付き"]))
        self.assertFalse(F.done_now("cond_check", dlg.st, dlg._all_ctx()))        # 選ぶまで次へ進めない
        dlg._choose_kind("セミ・モーター")
        self.assertTrue(F.done_now("cond_check", dlg.st, dlg._all_ctx()))
        self.assertEqual(self.w.measure_kind, "セミ・モーター")
        self.assertAlmostEqual(self.w.e_wheel.value(), 0.5)                      # 測定の画面にも入っている
        self.assertEqual(self.w.easy_make_wizard(self.w)._kind, "セミ・モーター")  # プログラム作りへ

    def test_undecidable_kind_blocks_next_with_reason(self):
        dlg, F = self.open_step()
        dlg._choose_kind("セミ・駆動装置")
        self.assertFalse(F.done_now("cond_check", dlg.st, dlg._all_ctx()))
        self.assertTrue(self.w.easy_kind_state()["error"])

    def test_model_without_kinds_is_unchanged(self):
        dlg, F = self.open_step("NO-KINDS")
        self.assertEqual(dlg._kind_btns if hasattr(dlg, "_kind_btns") else {}, {})
        self.assertTrue(F.done_now("cond_check", dlg.st, dlg._all_ctx()))

    def test_result_step_has_suffix_table_sharing_selection(self):
        from nd287_app import easy_mode
        dlg, _F = self.open_step()
        dlg._choose_kind("フル・スケール付き")
        self.w.e_machine.setText("777")
        dlg._enter(easy_mode.INDEX["result"])
        panel = dlg._suffix_panel
        self.assertEqual(panel.picker.suffix(), "C")
        self.assertIn("777C", dlg._save_name_lbl.text())
        panel.picker._clicked(2, 1)                                 # 正規モータ・アンクランプ × 傾斜単品 → TT が足される
        self.assertEqual(self.w.suffix_sel.suffix(), "TTC")
        self.assertEqual(self.w.suffix_panel.picker.suffix(), "TTC")   # 通常の画面の表にも同じ状態
        self.assertIn("777TTC", dlg._save_name_lbl.text())

    def test_header_name_always_matches_panel_suffix(self):
        """保存する名前の表示 ＝ 機番 ＋ 表の末尾。set_auto・クリック・モード変更・別画面の表のどれでも食い違わない。"""
        from nd287_app import easy_mode
        w = self.w
        dlg, _F = self.open_step()
        dlg._choose_kind("フル・スケール付き")
        w.e_machine.setText("555")
        dlg._enter(easy_mode.INDEX["result"])

        def check():
            for panel, label in ((w.suffix_panel, w.lbl_save_name), (dlg._suffix_panel, dlg._save_name_lbl)):
                suf = panel.picker.suffix()
                self.assertEqual(suf, w.suffix_sel.suffix())
                self.assertTrue(label.text().startswith(f"保存する名前: 555{suf}（"), (label.text(), suf))
                self.assertIn(f"末尾: {suf or '（なし）'}", panel.picker.lbl.text())
        check()
        dlg._suffix_panel.picker._clicked(2, 1)                    # 傾斜単品
        check()
        w.apply_auto_suffix()                                      # 自動に戻す（クリックの後）
        check()
        w.suffix_panel.picker._clicked(0, 0)                       # 通常画面の表で セミクロ
        check()
        w.mode_combo.setCurrentText("傾斜分割")
        check()
        w.suffix_panel.picker.set_auto("temp", True, ["rotary"], 1, 2)   # set_auto を直接呼んでも
        w.suffix_sel = w.suffix_panel.picker.sel
        w._suffix_sync_panels()
        check()

    def test_table_problems_band_shown(self):
        K.build_template(self.t / "k3.xlsx", ROWS + [{"型式": "TK-1", "測定の種類": "セミ・モーター", "歯数": 60}])
        self.w.settings["kind_conditions_xlsx"] = str(self.t / "k3.xlsx")
        self.w.reload_kind_table()
        dlg, _F = self.open_step()
        from PySide6 import QtWidgets
        band = dlg.findChild(QtWidgets.QLabel, "kind_problem")
        self.assertIsNotNone(band)
        self.assertIn("重なって", band.text())


if __name__ == "__main__":
    unittest.main()
