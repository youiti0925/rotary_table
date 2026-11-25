# -*- coding: utf-8 -*-
"""連続測定（フルクロ → セミクロ）。機械は偽物・保存先は一時フォルダ（実機・実際のマスタには触らない）。"""
import copy
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from nd287_app import easy_flow as F                      # noqa: E402
from nd287_app import kind_conditions as K                # noqa: E402
from nd287_app import renzoku as RZ                       # noqa: E402

PLAN_BOTH = {"フルで測る": "両方", "セミで測る": "分割だけ", "セミの種類": "セミ・モーター"}

ROT = [
    {"型式": "TK-1", "測定の種類": "セミ・モーター", "歯数": 72},
    {"型式": "TK-1", "測定の種類": "フル・スケール付き", "スケール名": "A", "ホイール刻み": 5, "ブロック数": 4, "回数": 7,
     "再現開始": 0, "再現終了": 270},
    # 2軸の製品（回転・傾斜にそれぞれ3種類のうち一部）
    {"型式": "TW-2", "測定の種類": "セミ・駆動装置", "ホイール刻み": 10},
    {"型式": "TW-2", "測定の種類": "フル・スケール付き", "スケール名": "A", "ホイール刻み": 5},
    {"型式": "TW-3", "測定の種類": "セミ・駆動装置", "ホイール刻み": 10},
    {"型式": "TW-3", "測定の種類": "フル・スケール付き", "スケール名": "A", "ホイール刻み": 5},
]
TILT = [
    {"型式": "TW-2", "測定の種類": "セミ・モーター", "刻み": 5, "開始角度": -30, "終了角度": 110},   # 回転は駆動装置・傾斜はモーター
    {"型式": "TW-2", "測定の種類": "フル・スケール付き", "スケール名": "A", "刻み": 5, "開始角度": -30, "終了角度": 110},
    {"型式": "TW-3", "測定の種類": "セミ・駆動装置", "刻み": 10, "開始角度": -30, "終了角度": 110},
    {"型式": "TW-3", "測定の種類": "フル・スケール付き", "スケール名": "A", "刻み": 5, "開始角度": -30, "終了角度": 110},
]


def make_xlsx(path):
    K.build_template(path, ROT, TILT)
    K.save_model(path, "TK-1", plan=PLAN_BOTH)
    K.save_model(path, "TW-2", plan={"フルで測る": "分割だけ", "セミで測る": "分割だけ", "セミの種類": "セミ・駆動装置"})
    K.save_model(path, "TW-3", plan={"フルで測る": "分割だけ", "セミで測る": "分割だけ", "セミの種類": "セミ・駆動装置"})
    K.save_model(path, "ONE-1", plan={"フルで測る": "分割だけ", "セミで測る": "", "セミの種類": ""})
    K.save_model(path, "BAD-1", plan={"フルで測る": "分割だけ", "セミで測る": "分割だけ", "セミの種類": ""})


class TestPure(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.p = self.d / "k.xlsx"
        make_xlsx(self.p)
        self.t = K.load(self.p)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_plan_from_table(self):
        pl = RZ.make_plan(self.t, "TK-1")
        self.assertTrue(pl["continuous"])
        self.assertEqual([s["stage"] for s in pl["stages"]], ["full", "semi"])
        self.assertEqual(RZ.plan_line(pl["stages"]), "測る予定: フル（分割＋再現性） → セミ（分割だけ）")
        self.assertEqual(pl["stages"][1]["kind"], "セミ・モーター")
        self.assertIsNone(RZ.make_plan(self.t, "NOPE"))                     # 予定なし＝今までどおり1回測る
        self.assertFalse(RZ.make_plan(self.t, "ONE-1")["continuous"])      # 片方だけは連続にしない
        bad = RZ.make_plan(self.t, "BAD-1")                                 # セミの種類が空: 黙って決めない
        self.assertFalse(bad["continuous"])
        self.assertIn("セミの種類", bad["problems"][0])

    def test_default_first_follows_machine_state(self):
        st = RZ.make_plan(self.t, "TK-1")["stages"]
        self.assertEqual(RZ.default_first(st, "semi"), "semi")
        self.assertEqual(RZ.default_first(st, "full"), "full")
        self.assertEqual(RZ.default_first(st, None), "full")                # 読めないときはフル先

    def test_run_order_and_return(self):
        pl = RZ.make_plan(self.t, "TK-1")
        r = RZ.Run(pl, "full", "full")
        self.assertEqual(r.current["stage"], "full")
        self.assertEqual(r.relation(), "same")
        self.assertEqual(r.upcoming["stage"], "semi")
        self.assertFalse(r.can_return())
        self.assertTrue(r.advance())
        self.assertTrue(r.is_last)
        self.assertEqual(r.relation(), "switch")                            # 機械はまだフル → セミへ切り替え
        self.assertTrue(r.can_return())                                     # 最後がセミ: フルへもどせる（既定は戻す）
        self.assertTrue(r.return_choice)
        r2 = RZ.Run(pl, "semi", "semi")                                     # セミ先 → 最後はフル: 聞かない
        r2.advance()
        self.assertFalse(r2.can_return())

    def test_skip_write_only_when_read_same_and_same_model(self):
        pl = RZ.make_plan(self.t, "TK-1")
        r = RZ.Run(pl, "semi", "semi", unit=2)
        self.assertTrue(r.skip_write("TK-1", "TK-1", "semi"))
        self.assertFalse(r.skip_write("TK-1", "TK-1", None))               # 読めていない: 書いた記憶では飛ばさない
        self.assertFalse(r.skip_write("TK-1", "TK-1", "full"))
        self.assertFalse(r.skip_write("TK-1", "OTHER", "semi"))
        self.assertFalse(RZ.Run(pl, "semi", "semi", unit=1).skip_write("TK-1", "TK-1", "semi"))   # 最初の1台は必ず入れる

    def test_flow_steps_for_skip_and_return(self):
        st = F.machine_state({"values": {"aut": 0, "run": 0, "emergency": 1, "alarm": 0}}, [], online=True)
        ids = lambda ctx: [s["id"] for s in F.STEPS if "measure" in s["fl"] and F.needed(s["id"], st, ctx)]
        base = {"param_estop": "estop_only", "rz_active": True, "rz_planned": True}
        self.assertIn("machine_in", ids(base))
        sk = ids(dict(base, rz_skip_write=True, rz_unit=2))
        self.assertNotIn("machine_in", sk)
        self.assertNotIn("cond_check", sk)
        self.assertIn("fields", sk)
        self.assertIn("measuring", sk)
        ret = ids(dict(base, rz_return_mode=True))
        self.assertIn("machine_in", ret)
        self.assertNotIn("measuring", ret)
        self.assertNotIn("result", ret)
        self.assertIn("finish", ret)
        self.assertIn("rz_return", ids({"rz_can_return": True}))
        self.assertNotIn("rz_return", ids({"rz_can_return": True, "rz_return_done": True}))
        self.assertNotIn("rz_plan", ids(base))
        self.assertIn("rz_plan", ids({"rz_active": True}))

    def test_same_save_names_are_a_conflict(self):
        self.assertEqual(RZ.save_name_conflict(["C", ""]), [])
        self.assertEqual(RZ.save_name_conflict(["K", "K"]), ["K"])


class HostBase(unittest.TestCase):
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
        make_xlsx(t / "k.xlsx")
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
        w._flow_measured = True


class TestHostPlan(HostBase):
    def test_plan_checks_both_axes_and_save_names(self):
        w = self.w
        w.e_model.setText("TK-1")
        w.on_model_entered()
        pl = w.easy_rz_plan()
        self.assertTrue(pl["continuous"], pl["problems"])
        self.assertEqual(pl["suffix"], {"full": "C", "semi": ""})           # フルとセミは別の名前（上書きしない）
        w.e_model.setText("NOPE")
        self.assertIsNone(w.easy_rz_plan())

    def test_two_axis_kinds_are_separate_and_plan_gap_is_reported(self):
        w = self.w
        w.e_model.setText("TW-2")
        w.on_model_entered()
        axes = w.easy_kind_axes()
        self.assertEqual([a["label"] for a in axes], ["回転", "傾斜"])
        # 回転と傾斜で、別の種類を選べる
        w.easy_set_kind("セミ・駆動装置", False)
        w.easy_set_kind("セミ・モーター", True)
        self.assertEqual(w.active_kind(False), "セミ・駆動装置")
        self.assertEqual(w.active_kind(True), "セミ・モーター")
        self.assertEqual(w.measure_kind, "セミ・駆動装置")                  # いまの軸（回転）の分
        w.mode_combo.setCurrentText("傾斜分割")
        self.assertEqual(w.measure_kind, "セミ・モーター")                  # 軸が替わると、その軸の種類
        self.assertEqual(w.easy_kind_state()["kind"], "セミ・モーター")
        self.assertEqual(w.suffix_sel.suffix(), "")                         # 傾斜=モーター（正規）のセミ
        w.mode_combo.setCurrentText("回転分割")
        self.assertEqual(w.suffix_sel.suffix(), "K")                        # 回転=駆動装置のセミ
        # 測る予定は型式に1行。種類が軸ごとに違うと、予定の「セミの種類」ではまかなえない
        pl = w.easy_rz_plan()
        self.assertFalse(pl["continuous"])
        self.assertTrue(any("傾斜" in p and "測り方が登録されていません" in p for p in pl["problems"]), pl["problems"])
        w.e_model.setText("TW-3")
        w.on_model_entered()
        self.assertTrue(w.easy_rz_plan()["continuous"])                     # 両方の軸に同じ種類があれば足りる

    def test_apply_stage_sets_kind_on_both_axes_and_mode(self):
        w = self.w
        w.e_model.setText("TW-3")
        w.on_model_entered()
        pl = w.easy_rz_plan()
        w.easy_rz_apply(pl["stages"][1])
        self.assertEqual((w.active_kind(False), w.active_kind(True)), ("セミ・駆動装置", "セミ・駆動装置"))
        self.assertEqual(w.current_mode(), "回転分割")
        w.e_model.setText("TK-1")
        w.on_model_entered()
        w.easy_rz_apply(RZ.make_plan(w.kind_table, "TK-1")["stages"][0])
        self.assertEqual(w.current_mode(), "回転分割+再現")                  # 両方 → 分割＋再現性

    def test_per_axis_kind_reaches_program_plan(self):
        """傾斜のプログラムは傾斜で選んだ種類の値で作る（回転の種類で作らない）。"""
        from nd287_app import easy_machine_in as M
        w = self.w
        w.e_model.setText("TW-2")
        w.on_model_entered()
        w.easy_set_kind("セミ・駆動装置", False)
        w.easy_set_kind("セミ・モーター", True)
        env = w.easy_machine_in_env()
        self.assertEqual(env["kind_fn"](True)[0], "セミ・モーター")
        self.assertEqual(env["kind_fn"](False)[0], "セミ・駆動装置")
        plan = M.prog_plan(w.settings, "TW-2", ["傾斜"], tilt=True, kind="セミ・モーター", kind_table=w.kind_table,
                           masters_dict=w.masters)
        self.assertTrue(plan["tilt"])
        self.assertEqual(plan["problems"], [])

    def test_machine_loop_read_with_fake_machine(self):
        from fake_client import DirectClient
        from fake_fwlib import FakeFwlib
        lib = FakeFwlib(scenario={"status": {"emergency": 1}})
        for k in range(1, 13):
            lib.m.params[(1815, f"A{k}")] = "00000010"       # #1=1 → フル
            lib.m.types[1815] = 6
        self.w._rz_client_factory = lambda st: DirectClient(lib=lib)
        self.w._rz_port_open = lambda ip, port, timeout=0.3: True
        self.w.settings["focas_registry_csv"] = str(self.t / "ledger.csv")
        from nd287_app import focas_find
        focas_find.remember(focas_find.registry_path(self.w.settings), "3C7B5D01-49C1F435-5E28C389-6592C486",
                            ip="127.0.0.1", unit="23")
        r = self.w.easy_machine_loop()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["stage"], "full")
        for k in range(1, 13):
            lib.m.params[(1815, f"A{k}")] = "00000000"
        self.assertEqual(self.w.easy_machine_loop()["stage"], "semi")
        lib.m.params[(1815, "A2")] = "00000010"                          # 軸で違うときは、決めない（読めない扱い）
        r = self.w.easy_machine_loop()
        self.assertIsNone(r["stage"])


class TestOverwriteNames(HostBase):
    def test_stage_save_names_do_not_overwrite_each_other(self):
        w = self.w
        w.sync_to_webapp = lambda *a, **k: None
        asked = []
        w._ask_overwrite = lambda existing, x: (asked.append(existing), "cancel")[1]
        w.e_model.setText("TK-1")
        w.on_model_entered()
        pl = RZ.make_plan(w.kind_table, "TK-1")
        for stage in pl["stages"]:
            w.easy_rz_apply(stage)
            self.fill_data()
            w.e_machine.setText("M100")
            w.save()
        self.assertEqual(asked, [])                                       # フルとセミで名前が違うので、聞かれない
        d = self.t / "save" / "TK"
        self.assertTrue((d / "M100C.csv").exists())
        self.assertTrue((d / "M100.csv").exists())
        names = sorted(x.name.upper() for x in (self.t / "bs").rglob("*") if x.is_file())
        self.assertIn("M100C.BS", names)
        self.assertIn("M100.BS", names)


import test_easy_semi as _TS                             # noqa: E402


class TestPanelPrefs(_TS.SemiBase):
    """連続測定が「機械に入れる」画面へ渡す、初めの選び（セミクロにする・分け方）。選び直しは人ができる。"""

    def test_semi_pref_preselects_semi_conversion_and_can_be_undone(self):
        p = self.panel(semi_pref=True)
        self.order(p)
        self.assertEqual(p.semi, [True])
        self.assertEqual(self.combos(p)[0].currentIndex(), 1)
        self.combos(p)[0].setCurrentIndex(0)                              # 人が「製品どおり」に直せる（押し付けない）
        self.assertEqual(p.semi, [False])
        p._refresh_semi()
        self.assertEqual(p.semi, [False])

    def test_no_pref_keeps_product_as_is(self):
        p = self.panel()
        self.order(p)
        self.assertEqual(p.semi, [False])

    def test_result_tells_whether_semi_was_written(self):
        p = self.panel(semi_pref=True)
        self.order(p)
        r = p.result()
        self.assertTrue(r["semi_written"])
        self.assertTrue(r["has_full_product"])
        p2 = self.panel()
        self.order(p2)
        self.assertFalse(p2.result()["semi_written"])


class FakeMachine:
    def __init__(self, stage="full"):
        self.stage = stage


class TestDialogFlow(HostBase):
    def setUp(self):
        super().setUp()
        from test_easy_mode import FakePoller, S
        from nd287_app import easy_mode
        self.S = S
        self.machine = FakeMachine("full")
        self.w.easy_machine_loop = lambda: {"ok": True, "stage": self.machine.stage, "error": ""}
        self.w.sync_to_webapp = lambda *a, **k: None
        self.w._ask_overwrite = lambda existing, x: "cancel"
        self.poller = FakePoller(S(aut=0, emergency=1))
        self.dlg = easy_mode.EasyModeDialog(self.w, self.poller)
        self.dlg.show()
        self.dlg.st = self.poller.state()
        self.app.processEvents()
        self.addCleanup(self.dlg.close)

    def sid(self):
        return F.STEPS[self.dlg.i]["id"]

    def to_plan(self, model="TK-1"):
        d = self.dlg
        d._start_flow("measure")
        d.st = self.poller.state()
        self.w.e_model.setText(model)
        self.w.on_model_entered()
        d._enter(F.INDEX["cond_check"])
        self.assertTrue(d.b_next.isEnabled(), "予定のある型式は、種類を選ばなくても進める（予定から入れる）")
        d._next()
        self.assertEqual(self.sid(), "rz_plan")

    def finish_wizard(self, semi_written=False):
        d = self.dlg
        self.assertEqual(self.sid(), "machine_in")
        d._close_wizard()
        d._rz_after_wizard({"semi_written": semi_written, "has_full_product": True})
        d.ctx["handoff"] = True
        d._next()

    def run_stage_to_save(self):
        d = self.dlg
        d._enter(F.INDEX["fields"])                                        # 電源・原点などの機械の手順は、ここでは省く
        self.w.e_machine.setText("M100")
        self.fill_data()
        d._enter(F.INDEX["result"])
        self.w.save()
        d.ctx["saved"] = True
        d._refresh()

    def test_plan_screen_defaults_and_choice(self):
        d = self.dlg
        self.to_plan()
        self.assertEqual(d.ctx["rz_first"], "full")                        # 機械はフル → フル先
        self.assertEqual(d.ctx["rz_machine"], "full")
        self.assertTrue(d.b_next.isEnabled())
        self.assertIn("フル（分割＋再現性） → セミ（分割だけ）", d.findChild(type(d.lbl_title), "rz_line").text())
        self.assertIn("最初の1台は", d.findChild(type(d.lbl_title), "rz_note").text())
        self.machine.stage = "semi"
        d._rz_reread()
        self.assertEqual(d.ctx["rz_first"], "semi")                        # 今の状態のまま測れる方が既定
        d._rz_pick_first("full")                                           # 人が選べる
        self.assertEqual(d.ctx["rz_first"], "full")
        d._rz_reread()
        self.assertEqual(d.ctx["rz_first"], "semi")

    def test_unreadable_machine_defaults_to_full_first_and_says_so(self):
        d = self.dlg
        self.w.easy_machine_loop = lambda: {"ok": False, "stage": None, "error": "つながりません"}
        self.to_plan()
        self.assertEqual(d.ctx["rz_first"], "full")
        self.assertIn("読めません", d.findChild(type(d.lbl_title), "rz_machine").text())

    def test_full_then_semi_then_return_flow(self):
        d = self.dlg
        self.to_plan()
        d._next()                                                          # フル先で確定
        self.assertEqual(self.sid(), "machine_in")
        self.assertEqual(self.w.active_kind(False), "フル・スケール付き")
        self.assertEqual(self.w.current_mode(), "回転分割+再現")
        self.assertTrue(d.lbl_rz.isVisible() or not d.lbl_rz.isHidden())
        self.assertIn("● フル", d.lbl_rz.text())
        env_seen = []
        orig = type(d._wiz).__init__ if d._wiz is not None else None
        self.assertTrue(d._wiz is not None and d._wiz.env["semi_pref"] is False)     # フルは「製品どおり」
        self.assertEqual(d._wiz.env["choice_pref"], "one")
        self.finish_wizard(semi_written=False)
        self.assertEqual(d.rz.machine, "full")
        self.run_stage_to_save()
        self.assertTrue((self.t / "save" / "TK" / "M100C.csv").exists())
        d._next()                                                          # result → finish
        self.assertEqual(self.sid(), "finish")
        self.assertIn("フル（分割＋再現性）が終わりました", d.lbl_title.text())
        self.assertIn("つぎはセミクロ", d.b_next.text())
        d._next()                                                          # つぎの工程へ
        self.assertEqual(d.rz.current["stage"], "semi")
        self.assertEqual(self.w.active_kind(False), "セミ・モーター")
        self.assertEqual(self.w.current_mode(), "回転分割")
        self.assertFalse(self.w._flow_saved)                               # 結果は空・保存済みの印も消えている
        self.assertNotEqual(self.sid(), "finish")
        self.assertFalse(d.ctx["handoff"])                                 # 切り替えるので、書く画面をもう一度通る
        self.assertEqual(self.sid(), "machine_in")
        self.assertTrue(d._wiz.env["semi_pref"] is True)                   # セミは「セミクロにする」を初めから選ぶ
        self.assertEqual(d._wiz.env["choice_pref"], "division")
        self.finish_wizard(semi_written=True)
        self.assertEqual(d.rz.machine, "semi")
        self.run_stage_to_save()
        self.assertTrue((self.t / "save" / "TK" / "M100.csv").exists())
        self.assertTrue((self.t / "save" / "TK" / "M100C.csv").exists())   # フルの結果は残っている
        d._next()
        self.assertEqual(self.sid(), "rz_return")                          # 最後に、フルへもどすか聞く
        self.assertFalse(d.b_next.isEnabled())
        d._rz_return_yes()
        self.assertTrue(d.ctx["rz_return_mode"])
        self.assertEqual(self.sid(), "machine_in")
        self.assertEqual(d._wiz.env["semi_pref"], False)                   # 製品の値（フル）を書く
        self.finish_wizard(semi_written=False)
        self.assertEqual(d.rz.machine, "full")
        seen = []
        for _ in range(10):                                                # 非常停止・原点など、機械の手順だけを通って終わる
            if self.sid() == "finish":
                break
            seen.append(self.sid())
            d._next()
        self.assertEqual(self.sid(), "finish")                             # 測り直さない
        for sid in ("fields", "measuring", "result", "rz_return"):
            self.assertNotIn(sid, seen)
        self.assertIn("フルクロにもどしました", d.lbl_title.text())

    def test_not_returning_goes_to_finish_with_machine_state(self):
        d = self.dlg
        self.machine.stage = "semi"
        self.to_plan()
        d._rz_pick_first("full")
        d._next()
        self.finish_wizard()
        self.run_stage_to_save()
        d._next()
        d._next()
        self.finish_wizard(semi_written=True)
        self.run_stage_to_save()
        d._next()
        self.assertEqual(self.sid(), "rz_return")
        d._rz_return_no()
        self.assertEqual(self.sid(), "finish")
        self.assertIn("もどしていません", d.lbl_lines.text())

    def test_semi_first_has_no_return_question(self):
        d = self.dlg
        self.machine.stage = "semi"
        self.to_plan()
        self.assertEqual(d.ctx["rz_first"], "semi")
        d._next()
        self.finish_wizard(semi_written=True)
        self.run_stage_to_save()
        d._next()
        self.assertEqual(self.sid(), "finish")
        d._next()                                                          # フルへ切り替え
        self.finish_wizard(semi_written=False)
        self.run_stage_to_save()
        d._next()
        self.assertEqual(self.sid(), "finish")                             # 最後はフル: もどすかは聞かない
        self.assertTrue((self.t / "save" / "TK" / "M100.csv").exists())
        self.assertTrue((self.t / "save" / "TK" / "M100C.csv").exists())

    def test_second_unit_same_state_skips_writing(self):
        d = self.dlg
        self.machine.stage = "semi"
        self.to_plan()
        d._next()
        self.finish_wizard(semi_written=True)
        self.run_stage_to_save()
        d._next()
        d._next()                                                          # フルへ切り替え
        self.finish_wizard(semi_written=False)
        self.run_stage_to_save()
        d._next()
        self.assertEqual(self.sid(), "finish")
        self.machine.stage = "full"                                        # 最後はフル。載せ替え
        d._rz_next_unit()
        self.assertEqual(self.sid(), "rz_plan")
        self.assertEqual(self.w.e_machine.text(), "")
        self.assertEqual(d.ctx["rz_first"], "full")                        # いまの状態（フル）から
        self.assertIn("このまま測れます", d.findChild(type(d.lbl_title), "rz_note").text())
        d._next()
        self.assertTrue(d.ctx["rz_skip_write"])
        self.assertEqual(self.sid(), "fields")                             # 書く・送る・電源・原点を飛ばして、測る準備へ
        self.assertIn("2台目", d.lbl_rz.text())

    def test_second_unit_other_state_goes_through_switch(self):
        d = self.dlg
        self.to_plan()
        d._next()
        self.finish_wizard()
        self.run_stage_to_save()
        d._next()
        d._next()
        self.finish_wizard(semi_written=True)
        self.run_stage_to_save()
        d._next()
        d._rz_return_no()                                                  # 戻さない: 機械はセミのまま
        d._rz_next_unit()
        self.machine.stage = "semi"
        d._rz_reread()
        self.assertEqual(d.ctx["rz_first"], "semi")
        d._rz_pick_first("full")                                           # 人がフルを選ぶ → 切り替え
        self.assertIn("切り替えます", d.findChild(type(d.lbl_title), "rz_note").text())
        d._next()
        self.assertFalse(d.ctx["rz_skip_write"])
        self.assertEqual(self.sid(), "machine_in")                         # 切り替えるときだけ、書く画面を通る

    def test_unread_machine_is_never_skipped(self):
        d = self.dlg
        self.to_plan()
        d._next()
        self.finish_wizard()
        self.run_stage_to_save()
        d._next()
        d._next()
        self.finish_wizard(semi_written=True)
        self.run_stage_to_save()
        d._next()
        d._rz_return_no()
        self.w.easy_machine_loop = lambda: {"ok": False, "stage": None, "error": "x"}
        d._rz_next_unit()
        d._next()
        self.assertFalse(d.ctx["rz_skip_write"])                           # 読めないときは、記憶だけでは飛ばさない
        self.assertEqual(self.sid(), "machine_in")

    def test_problem_blocks_the_plan_screen(self):
        d = self.dlg
        d._start_flow("measure")
        self.w.e_model.setText("TW-2")                                     # 傾斜にフル・セミ駆動装置が無い予定
        self.w.on_model_entered()
        d._enter(F.INDEX["rz_plan"])
        self.assertFalse(d.b_next.isEnabled())
        self.assertIsNotNone(d.findChild(type(d.lbl_title), "kind_problem"))

    def test_two_axis_model_chooses_kind_per_axis_in_the_measure_step(self):
        """2軸の製品: 測り方の段に、回転と傾斜の選びが別々に出る。両方選ぶまで進めない。選びは軸ごとに残る。"""
        d = self.dlg
        d._start_flow("measure")
        self.w.e_model.setText("TW-2")
        self.w.on_model_entered()
        self.w.easy_rz_plan = lambda: None                                # 予定ではなく、ふつうの選びを見る
        d._enter(F.INDEX["cond_check"])
        self.assertIsNotNone(d.findChild(type(d.lbl_title), "kind_head_rotary"))
        self.assertIsNotNone(d.findChild(type(d.lbl_title), "kind_head_tilt"))
        self.assertIn("回転", d.findChild(type(d.lbl_title), "kind_head_rotary").text())
        self.assertIn("傾斜", d.findChild(type(d.lbl_title), "kind_head_tilt").text())
        self.assertFalse(d.b_next.isEnabled())
        d._choose_kind("セミ・駆動装置", False)
        self.assertFalse(d.b_next.isEnabled())                            # 傾斜がまだ
        d._choose_kind("セミ・モーター", True)
        self.assertTrue(d.b_next.isEnabled())
        self.assertEqual((self.w.active_kind(False), self.w.active_kind(True)), ("セミ・駆動装置", "セミ・モーター"))
        self.assertEqual(sorted(d._kind_btns_axis), ["rotary", "tilt"])

    def test_one_axis_model_keeps_the_old_single_chooser(self):
        d = self.dlg
        d._start_flow("measure")
        self.w.e_model.setText("TK-1")
        self.w.on_model_entered()
        self.w.easy_rz_plan = lambda: None
        d._enter(F.INDEX["cond_check"])
        self.assertEqual(sorted(d._kind_btns_axis), ["rotary"])
        self.assertNotIn("【", d.findChild(type(d.lbl_title), "kind_head_rotary").text())

    def test_normal_model_without_plan_is_unchanged(self):
        d = self.dlg
        d._start_flow("measure")
        self.w.e_model.setText("NOPE")
        d._enter(F.INDEX["cond_check"])
        self.assertFalse(d._all_ctx()["rz_active"])
        d._next()
        self.assertEqual(self.sid(), "machine_in")


if __name__ == "__main__":
    unittest.main()
