# -*- coding: utf-8 -*-
"""台帳に無い制御装置（取り替えた機械）: 号機を容量から推し、軸も号機も人に選ばせない。

実機の事実（2026-11-25）: 192.168.1.1 の機械が 31i・4軸 X,Y,Z,A・2165=85/165/85/165 に替わり、
CNC ID が台帳に無かったのに、IP が同じというだけで 23号機（6軸）と表示していた。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import controllers, focas_find                    # noqa: E402
from nd287_app import machine_decide as MD                       # noqa: E402
from nd287_app import wizard_steps as G_steps                    # noqa: E402
from nd287_app import wizard_probe as P                          # noqa: E402
from test_wizard_machine_write import WizardBase, REPO           # noqa: E402

MASTER = REPO / "マスタ" / "制御装置マスタ.csv"
OLD21 = (b"21,31i-MA,11.0,AC200V,90E0/0014,80A,160A,80A,160A", b"21,31i-MA,11.0,AC200V,90E0/0014,80A,80A,160A,160A")


def master_bytes(old21=True, drop84=True):
    """試験用のマスタ。old21: 21号機の行が 80/80/160/160（機械の BASIC と違う）だった頃のもの。
    drop84: 35i の 84号機（同じ 80/160/80/160）を除く（machine_decide は系列を見ないので、31i の機械に 84 も出てしまう）。"""
    raw = MASTER.read_bytes()
    assert raw.count(OLD21[0]) == 1
    if old21:
        raw = raw.replace(OLD21[0], OLD21[1])
    if drop84:
        raw = b"".join(x for x in raw.splitlines(True) if not x.startswith(b"84,"))
    return raw


def old_master_bytes():
    return master_bytes(True, True)


bs = chr(92)
OLD_ID = "3C7B5D01-49C1F435-5E28C389-6592C486"                  # 台帳にある 23号機
NEW_ID = "3C7B5D01-69C1BBD5-5E28C389-6792C40B"                  # 取り替えたあとの機械（台帳に無い）


def amr(*vals):
    return {k: {"motor_id": "1", "amr": str(v)} for k, v in enumerate(vals, 1)}


class TestIdentifyUnit(unittest.TestCase):
    """号機は machine_decide.identify_unit で決める（台帳 → 2165 の並びをマスタと照らす。IP は使わない）。"""
    @classmethod
    def setUpClass(cls):
        cls.ctls = controllers.load_controllers(str(MASTER))

    def test_real_case_20_and_21_have_the_same_spec(self):
        r = MD.identify_unit({"axes": amr(85, 165, 85, 165)}, self.ctls)
        self.assertEqual(r["how"], "複数")
        self.assertEqual(r["unit"], "")
        self.assertTrue({"20", "21"} <= set(r["units"]))

    def test_ledger_unit_wins(self):
        r = MD.identify_unit({"axes": amr(85, 165, 85, 165)}, self.ctls, "21")
        self.assertEqual((r["unit"], r["how"]), ("21", "台帳"))

    def test_single_match_and_none(self):
        self.assertEqual(MD.identify_unit({"axes": amr(25, 25, 45, 85, 85, 165)}, self.ctls)["unit"], "23")
        self.assertTrue(MD.identify_unit({"axes": amr(165, 85, 165, 85)}, self.ctls)["unknown"])


class TestMasterDiff(unittest.TestCase):
    def setUp(self):
        self.ctls = {c.unit: c for c in controllers.load_controllers(str(MASTER))}
        self.letter = lambda k: "XYZA"[k - 1]
        self.probe = {"axes": amr(85, 165, 85, 165)}

    def test_unit_21_old_row_differs_from_machine_per_axis(self):
        old21 = controllers.Controller("21", cnc="31i-MA", caps={"X": "80A", "Y": "80A", "Z": "160A", "A": "160A"})
        d = P.master_diff(old21, self.probe, self.letter)
        self.assertEqual(d["lines"], ["マスタの21号機の Y軸は 80A、機械は 160A です",
                                      "マスタの21号機の Z軸は 160A、機械は 80A です"])
        self.assertEqual(d["caps"], {"X": "80A", "Y": "160A", "Z": "80A", "A": "160A"})
        self.assertEqual(d["order"], ["X", "Y", "Z", "A"])

    def test_corrected_21_row_matches(self):
        self.assertEqual(P.master_diff(self.ctls["21"], self.probe, self.letter)["lines"], [])
        self.assertEqual(P.master_diff(self.ctls["20"], self.probe, self.letter)["lines"], [])

    def test_basic_diff_lists_axes(self):
        text = "N01020Q1A1P88A2P89A3P90A4P65" + chr(10) + "N02165Q1A1P85A2P85A3P165A4P165" + chr(10)
        self.assertEqual(P.basic_diff(text, "21", self.probe["axes"], self.letter),
                         ["BASICの21号機の Y軸は 80A、機械は 160A です", "BASICの21号機の Z軸は 160A、機械は 80A です"])

    def test_machine_with_fewer_axes_than_the_row(self):
        d = P.master_diff(self.ctls["23"], self.probe, self.letter)
        self.assertIn("機械に B軸はありません（マスタの23号機は 80A です）", d["lines"])
        self.assertIn("マスタの23号機の X軸は 20A、機械は 80A です", d["lines"])


class TestApplyMasterFix(unittest.TestCase):
    def test_fix_changes_only_caps_and_keeps_backup(self):
        import tempfile, time
        from nd287_app import first_unit
        tmp = Path(tempfile.mkdtemp())
        m = tmp / "m.csv"
        m.write_bytes(MASTER.read_bytes())
        bak = first_unit.apply_master_fix(str(m), "21", {"X": "80A", "Y": "160A", "Z": "80A", "A": "160A"},
                                          order=["X", "Y", "Z", "A"], now=time.localtime(0))
        self.assertEqual(Path(bak).read_bytes(), MASTER.read_bytes())
        self.assertTrue(bak.startswith(str(m) + ".bak_"))
        old = {c.unit: c for c in controllers.load_controllers(str(MASTER))}
        new = {c.unit: c for c in controllers.load_controllers(str(m))}
        self.assertEqual(new["21"].caps_text(), "X:80A Y:160A Z:80A A:160A")
        self.assertEqual(new["21"].amps, old["21"].amps)
        self.assertEqual((new["21"].servo, new["21"].voltage, new["21"].ver), (old["21"].servo, old["21"].voltage, old["21"].ver))
        for u in old:
            if u != "21":
                self.assertEqual(new[u].caps_text(), old[u].caps_text(), u)
                self.assertEqual(new[u].amps, old[u].amps, u)

    def test_fewer_axes_clears_missing_axis(self):
        import tempfile
        from nd287_app import first_unit
        tmp = Path(tempfile.mkdtemp())
        m = tmp / "m.csv"
        m.write_bytes(MASTER.read_bytes())
        first_unit.apply_master_fix(str(m), "23", {"X": "20A", "Y": "20A"}, order=["X", "Y"])
        c = [x for x in controllers.load_controllers(str(m)) if x.unit == "23"][0]
        self.assertEqual(c.axes(), ["X", "Y"])
        self.assertEqual(c.amps["Z"], "")


class TestBreakTie(unittest.TestCase):
    def test_most_matches_wins(self):
        self.assertEqual(P.break_tie([1, 3], {1: 1, 3: 2})[0], 3)

    def test_then_lowest_axis_number(self):
        ax, pool, how = P.break_tie([3, 1], {1: 0, 3: 0})
        self.assertEqual((ax, pool), (1, [1, 3]))
        self.assertIn("最も小さい", how)
        self.assertEqual(P.break_tie([3, 1])[0], 1)

    def test_no_candidates(self):
        self.assertEqual(P.break_tie([], {1: 1})[0], 0)


class StubClient:
    """read_machine だけが使う最小の偽の機械。"""
    def __init__(self, cnc_id, amrs, cnc_type="31", names="XYZA"):
        self.cnc_id, self.amrs, self.cnc_type, self.names = cnc_id, amrs, cnc_type, list(names)
        self.calls = []

    def call(self, cmd, args=None, **kw):
        self.calls.append(cmd)
        if cmd == "connect":
            return {}
        if cmd == "identify":
            return {"cnc_id": self.cnc_id, "cnc_type": self.cnc_type, "axis_names": self.names}
        if cmd == "param_read_many":
            out = []
            for it in args["items"]:
                k = int(it["label"][1:])
                v = self.amrs[k - 1] if it["no"] == P.AMR_PARAM else "297"
                out.append({"ok": True, "no": it["no"], "label": it["label"], "value": v})
            return {"items": out}
        raise AssertionError(cmd)

    def close(self):
        pass


class TestReadMachineUnit(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.reg = self.tmp / "reg.csv"
        focas_find.remember(self.reg, OLD_ID, ip="192.168.1.1", unit="23")
        self.settings = {"focas_registry_csv": str(self.reg), "focas_host": "192.168.1.1"}
        self.ctls = controllers.load_controllers(str(MASTER))

    def read(self, client, **kw):
        return P.read_machine(self.settings, lambda st: client, port_open=lambda *a, **k: True, **kw)

    def test_unknown_cnc_id_does_not_borrow_unit_from_ip(self):
        r = self.read(StubClient(NEW_ID, [85, 165, 85, 165]))
        self.assertEqual((r["unit"], r["unit_from"], r["unit_guess"]), ("", "", []))
        self.assertEqual(r["cnc_type"], "31")
        self.assertEqual(focas_find.load_registry(self.reg).keys(), {OLD_ID})      # 台帳には書かない

    def test_registered_cnc_id_uses_registry(self):
        r = self.read(StubClient(OLD_ID, [25, 25, 45, 85, 85, 165], names="XYZABC"))
        self.assertEqual((r["unit"], r["unit_from"], r["unit_guess"]), ("23", "registry", []))

    def test_registered_cnc_id_with_other_ip_still_found(self):
        focas_find.remember(self.reg, NEW_ID, ip="10.0.0.9", unit="77")
        r = self.read(StubClient(NEW_ID, [85, 165, 85, 165]))
        self.assertEqual((r["unit"], r["unit_from"]), ("77", "registry"))

    def test_unreadable_cnc_id_does_not_use_ip_either(self):
        r = self.read(StubClient(None, [85, 165, 85, 165]))
        self.assertEqual((r["unit"], r["unit_from"]), ("", ""))


class UnknownMachineBase(WizardBase):
    """台帳に無い 31i・4軸の機械につないだかんたん作成（部品）。"""
    def setUp(self):
        super().setUp()
        self.settings["focas_registry_csv"] = str(self.work / "reg.csv")
        self.settings["focas_host"] = "127.0.0.1"
        focas_find.remember(focas_find.registry_path(self.settings), OLD_ID, ip="127.0.0.1", unit="23")

    def make(self, motor_ids=(297, 315, 297, 303), master=None):
        from fake_client import DirectClient
        import nd287_app.gui as G
        if not master:
            master = self.work / "master_old21.csv"
            master.write_bytes(old_master_bytes())
        self.settings["controller_master_csv"] = str(master)
        client = DirectClient()
        m = client.machine
        m.axis_names = [b"X", b"Y", b"Z", b"A"]
        m.cnc_id = [0x3C7B5D01, 0x69C1BBD5, 0x5E28C389, 0x6792C40B]
        m.types[2020] = m.types[2165]
        for k, (mid, a) in enumerate(zip(motor_ids, (85, 165, 85, 165)), 1):
            m.params[(2020, f"A{k}")] = str(mid)
            m.params[(2165, f"A{k}")] = str(a)
        f = Path(self.settings["param_product_dir"]) / "R50014984.prm"
        f.write_text(f.read_text(encoding="cp932").replace("αiS8/4000-B", "αiS22/3000HV-B"), encoding="cp932")
        w = G.ParamWizardDialog(None, self.settings, probe_machine=True)
        w.warnings = []
        w._probe_client_factory = lambda st: client
        w._probe_port_open = lambda ip, port=8193, timeout=0.3: True
        self.addCleanup(w.done, 0)
        w._start_probe()
        w._probe_job.wait(10000)
        self.app.processEvents()
        return w, client

    def search(self, w):
        w.e_seiban.setText("50014984")
        w.search()
        job = w._axis_job
        if job is not None:
            job.wait(10000)
        self.app.processEvents()

    def cand_units(self, w):
        return [str(c.unit) for c, _a in w._cand]

class WizardUnknownMachine(UnknownMachineBase):
    def test_one_guess_is_selected_without_asking(self):
        w, client = self.make()
        pr = w._machine_probe
        self.assertEqual((pr["unit"], pr["unit_from"], pr["unit_guess"]), ("20", "guess", ["20"]))
        self.assertIn("20号機", w.lbl_conn.text())
        self.assertNotIn("23号機", w.lbl_conn.text())
        self.search(w)
        self.assertIn("この機械は 20号機 と判断しました", w.lbl_need.text())
        self.assertIn("違えば選び直してください", w.lbl_need.text())
        self.assertNotIn("78通り", w.lbl_need.text())
        c, asg = w._cand[w.cmb_ctrl.currentData()]
        self.assertEqual(str(c.unit), "20")                                  # 制御装置は選んである
        self.assertEqual(self.cand_units(w)[0], "20")                        # 全部の号機×軸の一覧が先頭ではない
        self.assertEqual(client.machine.writes, [])                          # 読むだけ
        self.assertEqual(set(focas_find.load_registry(focas_find.registry_path(self.settings))), {OLD_ID})

    def test_tie_is_broken_by_rule_and_shown_with_reason(self):
        """X・Z が同じ 80A・同じ 297・値の一致も同じ → 若い X に仮決め。人に選ばせない（［変える］で Z にできる）。"""
        w, _ = self.make()
        self.search(w)
        self.assertEqual(w.e_motor_id.text(), "297")
        self.assertEqual(w._decided_axis, "X")
        self.assertIn("X・Z が同じ条件だったので X に決めました", w.lbl_auto.text())
        self.assertIn("最も小さい", w.lbl_auto.text())
        c, asg = w._cand[w.cmb_ctrl.currentData()]
        self.assertEqual((str(c.unit), list(asg)), ("20", ["X"]))
        # ② 号機・軸が機械から決まっているので、一覧は隠して1行だけ
        w._step = 2
        w._apply_mode()
        self.assertTrue(w.cmb_ctrl.isHidden())
        self.assertTrue(w.cmb_axis_pick.isHidden())
        self.assertEqual(w.lbl_pick.text().splitlines()[0], "使う機械：20号機　使う軸：X軸　自動で決めました（容量から推定）")
        self.assertIn("理由:", w.lbl_pick.text())
        self.assertFalse(w.b_change.isHidden())
        # ［変える］を押すと、同じ容量（80A）の軸 X・Z だけの一覧が出る。号機の一覧は別のボタン
        w.b_change.click()
        self.assertFalse(w.cmb_axis_pick.isHidden())
        self.assertTrue(w.cmb_ctrl.isHidden())
        texts = [w.cmb_axis_pick.itemText(i) for i in range(w.cmb_axis_pick.count())]
        self.assertEqual([x for x in texts if "軸（" in x], ["X軸（297）", "Z軸（297）"])
        i = texts.index("Z軸（297）")
        w.cmb_axis_pick.setCurrentIndex(i)
        w.cmb_axis_pick.activated.emit(i)
        self.assertEqual((w._decided_axis, w.e_motor_id.text()), ("Z", "297"))
        c, asg = w._cand[w.cmb_ctrl.currentData()]
        self.assertEqual((str(c.unit), list(asg)), ("20", ["Z"]))
        self.assertIn("人が選びました", w.lbl_pick.text())
        w.b_change_unit.click()
        self.assertFalse(w.cmb_ctrl.isHidden())

    def test_tie_is_broken_by_most_value_matches_first(self):
        w, client = self.make()
        nums = (1420, 1421)
        m = client.machine
        for no in nums:
            m.types[no] = m.types[2165]
            for k in range(1, 5):
                m.params[(no, f"A{k}")] = "1"
            m.params[(no, "A3")] = str(7000 + no)                            # Z に製品らしい値が2つ（決めるには足りない）
        prod = {no: str(7000 + no) for no in nums}
        w._axis_probe_inputs = lambda f, pr: (prod, {no: ["1"] for no in nums})
        self.search(w)
        self.assertEqual(w._decided_axis, "Z")
        self.assertIn("一致が最も多い", w.lbl_auto.text())

    def test_several_guesses_select_nothing_and_offer_registration(self):
        raw = master_bytes(True, True)
        line = next(x for x in raw.splitlines() if x.startswith(b"20,"))
        twin = self.work / "master2.csv"
        twin.write_bytes(raw.rstrip(b"\r\n") + b"\r\n" + b"90," + line[3:] + b"\r\n")
        w, _ = self.make(master=twin)
        pr = w._machine_probe
        self.assertEqual((pr["unit"], pr["unit_guess"]), ("", ["20", "90"]))      # 号機は決めない
        self.assertIn("20号機か90号機（同じ仕様）", w.lbl_conn.text())
        self.search(w)
        self.assertEqual(set(self.cand_units(w)), {"20", "90"})                  # 同じ条件の号機だけ出す
        c, asg = w._cand[w.cmb_ctrl.currentData()]                               # 止めない: 先頭の号機・決めた軸で先へ進める
        self.assertEqual((str(c.unit), list(asg)), ("20", ["X"]))
        self.assertTrue(w.b_next.isEnabled())
        w._step = 2
        w._apply_mode()
        self.assertEqual(w.lbl_pick.text().splitlines()[0], "使う機械：20号機か90号機（同じ仕様）　使う軸：X軸　自動で決めました")
        self.assertFalse(w.box_reg.isHidden())
        self.assertIn("この機械は登録がありません", w.lbl_reg.text())
        self.assertIn(NEW_ID, w.lbl_reg.text())
        self.assertEqual([w.cmb_reg_unit.itemText(i) for i in range(w.cmb_reg_unit.count())], ["20", "90"])
        self.assertEqual(w.cmb_reg_unit.currentText(), "20")
        self.assertTrue(w.cmb_reg_unit.isEditable())


DAT = ("N01020Q1A1P88A2P89A3P90A4P65\n"
       "N02165Q1A1P{a}A2P{b}A3P{c}A4P{d}\n").format


class WizardRegister(UnknownMachineBase):
    """台帳に無い機械を、機械から BASIC を取って号機として登録する。"""
    def setUp(self):
        super().setUp()
        basic = Path(self.settings["param_basic_dir"])
        for u in ("20", "21"):                                           # 実際と同じ: 20 も 21 も BASIC の 2165 は 85/165/85/165
            (basic / f"F{u}BASICx.DAT").write_bytes(DAT(a=85, b=165, c=85, d=165).encode("cp932"))
            (basic / f"F{u}BASICx.DAT").rename(basic / f"F{u}BASIC.DAT")
        self.master = self.work / "master.csv"
        self.master.write_bytes(old_master_bytes())
        self.settings["controller_master_csv"] = str(self.master)
        self.settings["basic_from_machine_dir"] = str(self.work / "機械から取ったBASIC")
        self.settings["focas_backup_dir"] = str(self.work / "控え")

    def reg_wait(self, w):
        w._reg_job.wait(20000)
        self.app.processEvents()

    def test_same_spec_20_and_21_do_not_block_and_registration_is_offered(self):
        fixed = self.work / "master_fixed.csv"
        fixed.write_bytes(master_bytes(False, True))
        w, client = self.make(master=fixed)
        pr = w._machine_probe
        self.assertEqual((pr["unit"], pr["unit_guess"]), ("", ["20", "21"]))
        self.search(w)
        self.assertNotEqual(w.cmb_ctrl.currentData(), -1)
        self.assertTrue(w.b_next.isEnabled())
        w._step = 2
        w._apply_mode()
        self.assertIn("20号機か21号機（同じ仕様）", w.lbl_pick.text())
        self.assertFalse(w.box_reg.isHidden())
        self.assertEqual(client.machine.writes, [])
        self.assertEqual(set(focas_find.load_registry(focas_find.registry_path(self.settings))), {OLD_ID})   # 台帳は自動では書かない

    def test_missing_basic_does_not_block_next(self):
        w, _ = self.make(master=self.master)
        self.search(w)
        w.e_basic.setText("")
        st = G_steps.step_state(2, ctrl_chosen=True, basic_ok=False)
        self.assertTrue(st["can_next"])
        self.assertTrue(st["warnings"])

    def test_register_existing_basic_only_writes_registry_then_preselects_unit(self):
        w, client = self.make(master=self.master)
        self.search(w)
        w.cmb_reg_unit.setEditText("21")
        before = sorted(p.name for p in Path(self.settings["param_basic_dir"]).iterdir())
        w._reg_start()
        self.reg_wait(w)
        reg = focas_find.registry_path(self.settings)
        self.assertEqual(focas_find.unit_for(reg, NEW_ID), "21")
        self.assertEqual(focas_find.load_registry(reg)[NEW_ID]["ip"], "127.0.0.1")
        self.assertEqual(sorted(p.name for p in Path(self.settings["param_basic_dir"]).iterdir()), before)  # 上書き・追加なし
        self.assertEqual(client.machine.writes, [])
        pr = w._machine_probe
        self.assertEqual((pr["unit"], pr["unit_from"]), ("21", "registry"))
        c, _a = w._cand[w.cmb_ctrl.currentData()]
        self.assertEqual(str(c.unit), "21")
        w._step = 2
        w._apply_mode()
        w._step = 2
        w._apply_mode()
        self.assertTrue(w.box_reg.isHidden())
        # マスタの21号機の行（80/80/160/160）は機械（80/160/80/160）と違う → 直すボタンの箱。黙っては直さない
        self.assertFalse(w.band_diff.isHidden())
        self.assertIn("マスタの21号機の Y軸は 80A、機械は 160A です", w.lbl_fix.text())
        self.assertIn("マスタの21号機の Z軸は 160A、機械は 80A です", w.lbl_fix.text())
        self.assertEqual(w.b_fix.text(), "マスタの21号機の行を、機械の値に直す")
        self.assertEqual(self.master.read_bytes(), old_master_bytes())            # 押すまで黙って直さない
        w._fix_master()
        self.assertTrue(w.band_diff.isHidden())
        row = [c for c in controllers.load_controllers(str(self.master)) if c.unit == "21"][0]
        self.assertEqual([row.caps[a] for a in row.axes()], ["80A", "160A", "80A", "160A"])
        self.assertEqual(row.amps["X"], "A06B-6117-H210")                       # 他の列はそのまま
        self.assertEqual((row.voltage, row.servo), ("AC200V", "90E0/0014"))
        baks = list(self.work.glob("master.csv.bak_*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_bytes(), old_master_bytes())               # 直す前の控え
        others = [c for c in controllers.load_controllers(str(MASTER)) if c.unit == "20"][0]
        self.assertEqual([c.caps_text() for c in controllers.load_controllers(str(self.master)) if c.unit == "20"],
                         [others.caps_text()])

    def test_register_new_unit_takes_backup_and_saves_basic_outside_basic_dir(self):
        w, client = self.make()
        self.search(w)
        w.cmb_reg_unit.setEditText("90")
        w._reg_start()
        self.reg_wait(w)
        self.assertIn("登録しました", w.lbl_reg_msg.text(), w.lbl_reg_msg.text())
        saved = self.work / "機械から取ったBASIC" / "F90BASIC.dat"
        self.assertTrue(saved.is_file())
        self.assertFalse((Path(self.settings["param_basic_dir"]) / "F90BASIC.dat").exists())
        self.assertEqual(focas_find.unit_for(focas_find.registry_path(self.settings), NEW_ID), "90")
        self.assertEqual(client.machine.writes, [])                              # 機械には書かない
        self.assertEqual((w._machine_probe["unit"], w._machine_probe["unit_from"]), ("90", "registry"))

    def registered(self, unit):
        focas_find.remember(focas_find.registry_path(self.settings), NEW_ID, ip="127.0.0.1", unit=unit)

    def test_every_connect_shows_red_band_for_registered_unit(self):
        self.registered("21")
        w, client = self.make(master=self.master)
        self.assertEqual((w._machine_probe["unit"], w._machine_probe["unit_from"]), ("21", "registry"))
        w._step = 2
        w._apply_mode()
        self.assertFalse(w.band_diff.isHidden())
        self.assertIn("マスタの21号機の Y軸は 80A、機械は 160A です", w.lbl_fix.text())
        self.assertFalse(w.b_fix.isHidden())
        self.assertEqual(self.master.read_bytes(), old_master_bytes())
        self.assertEqual(client.machine.writes, [])

    def test_basic_mismatch_is_listed_but_has_no_fix_button(self):
        self.registered("20")
        basic = Path(self.settings["param_basic_dir"])
        (basic / "F20BASIC.DAT").write_bytes(DAT(a=85, b=85, c=165, d=165).encode("cp932"))
        w, _ = self.make(master=self.master)
        self.assertIn("BASICの20号機の Y軸は 80A、機械は 160A です", w.lbl_fix.text())
        self.assertTrue(w.b_fix.isHidden())                                      # BASIC は直さない
        self.assertFalse(w.band_diff.isHidden())

    def test_no_band_when_everything_matches(self):
        self.registered("20")
        w, _ = self.make(master=self.master)
        self.assertTrue(w.band_diff.isHidden())

    def test_undecided_shows_list_and_what_to_choose(self):
        w, _ = self.make(master=self.master)                                    # 台帳に無い・候補は 20 だけ（推定）
        self.search(w)
        w.cmb_ctrl.setCurrentIndex(0)                                           # 人が選び直して、何も選ばない状態にする
        w._step = 2
        w._apply_mode()
        self.assertFalse(w.cmb_ctrl.isHidden())
        self.assertIn("を1つ選んでください", w.lbl_pick.text())
        self.assertTrue(w.b_change.isHidden())

    def test_bad_unit_text_does_nothing(self):
        w, client = self.make()
        w.cmb_reg_unit.setEditText("abc")
        w._reg_start()
        self.assertIsNone(w._reg_job)
        self.assertIn("数字", w.lbl_reg_msg.text())


if __name__ == "__main__":
    unittest.main()
