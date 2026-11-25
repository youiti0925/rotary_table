# -*- coding: utf-8 -*-
"""はじめてモード「機械に入れる」専用画面（easy_machine_in / easy_machine_in_ui）と、focas_ops の追加の選択肢。

偽物の機械（fake_fwlib・fake_client）に直接つなぐ。実機にはつながない。
"""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nd287_app import controllers, focas_find                       # noqa: E402
from nd287_app import easy_machine_in as M                          # noqa: E402
from nd287_app import focas_ops as O                                # noqa: E402
from nd287_app.settings import DEFAULTS                             # noqa: E402
from fake_client import DirectClient                                # noqa: E402
from fake_fwlib import FakeFwlib                                    # noqa: E402
from test_focas_ops_write import WriteBase, CNC_ID, ALL_KINDS       # noqa: E402

PWE = [100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]
BATT = [307, 10, 2, "APC ALARM: BATTERY LOW"]          # DS0307（Y 軸）
DS300 = [300, 10, 2, "APC ALARM: NEED REF RETURN"]
PW0 = [0, 1, 0, "POWER MUST BE OFF"]


def setUpModule():
    O.set_estop_mode("released_only")


def tearDownModule():
    O.set_estop_mode("estop_only")


# ------------------------------------------------------------------ focas_ops の追加の選択肢
class TestOpsOptions(WriteBase):
    scenario = {"alarms": []}                 # SW0100 が出ていない機械（実機21: 書込許可は 1 でも、電源の入れ直しまで出なかった）

    def items(self):
        p, pl = self.plan({"20": "4", "21": "1"}, kinds=ALL_KINDS)
        return p, pl["items"]

    def test_default_still_requires_sw0100(self):
        p, items = self.items()
        with self.assertRaises(O.WriteRefused) as cm:
            self.run_write(items, newtext=p["newtext"])
        self.assertIn("パラメータ書込が許可されていません", " ".join(cm.exception.reasons))
        self.assertEqual(self.wr_calls(), 0)

    def test_pwe_by_alarm_false_writes_without_sw0100(self):
        p, items = self.items()
        res = self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False)
        self.assertEqual(res.state, "done", res.stop_reason)
        self.assertFalse(res.protected)

    def test_other_gates_remain_when_pwe_by_alarm_is_false(self):
        p, items = self.items()
        self.m.status["run"] = 3
        with self.assertRaises(O.WriteRefused):
            self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False)

    def test_machine_refusal_rc7_stops_and_is_reported(self):
        self.client.lib.sc["pwe_required"] = True          # 実機は書込許可 0 のとき戻り値 7（SW0100 が出ていないとき）
        p, items = self.items()
        res = self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False)
        self.assertEqual(res.state, "stopped")
        self.assertTrue(res.protected)
        self.assertEqual(res.items[0]["rc"], 7)
        self.assertEqual(self.machine_value(20), self.plan({"20": "4"}, kinds=ALL_KINDS)[1]["items"][0]["old"])

    def test_write_trial_writes_same_value_and_reports_protect(self):
        _p, items = self.items()
        kw = dict(cnc_id=CNC_ID, backup_info=self.info, settings=self.settings, family="31i", log_path=self.log)
        out = O.write_trial(self.client, items[0], **kw)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.machine_value(items[0]["no"], items[0]["label"]), items[0]["old"])      # 値は変わらない
        self.client.lib.sc["pwe_required"] = True
        out = O.write_trial(self.client, items[0], **kw)
        self.assertFalse(out["ok"])
        self.assertTrue(out["protected"])

    def test_own_power_alarms_continue_but_other_new_alarms_stop(self):
        p, items = self.items()
        first = items[0]["no"]
        self.client.lib.sc["write_alarms"] = {first: [PW0, DS300]}
        res = self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False)
        self.assertEqual(res.state, "stopped")                        # 既定: DS0300 で止まる（PW0000 だけなら今までも続く）
        self.setUp()
        p, items = self.items()
        self.client.lib.sc["write_alarms"] = {items[0]["no"]: [PW0, DS300]}
        res = self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False, allow_own_power_alarms=True)
        self.assertEqual(res.state, "done", res.stop_reason)
        self.assertTrue(res.power_cycle)
        self.assertTrue(res.ref_return)

    def test_other_new_alarm_stops_even_with_own_alarms_allowed(self):
        p, items = self.items()
        self.client.lib.sc["write_alarms"] = {items[0]["no"]: [BATT]}
        res = self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False, allow_own_power_alarms=True)
        self.assertEqual(res.state, "stopped")

    def test_alarm_present_before_the_job_stops_it(self):
        p, items = self.items()
        self.m.alarms = [tuple(DS300)]
        with self.assertRaises(O.WriteRefused):
            self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False, allow_own_power_alarms=True)

    def test_ignore_list_is_explicit_and_per_unit(self):
        p, items = self.items()
        self.m.alarms = [tuple(BATT)]
        self.settings["alarm_ignore"] = {"21": ["DS0307"], "*": []}
        with self.assertRaises(O.WriteRefused):                       # 既定は無視しない
            self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False)
        with self.assertRaises(O.WriteRefused):
            self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False, ignore_unit="22")
        res = self.run_write(items, newtext=p["newtext"], pwe_by_alarm=False, ignore_unit="21")
        self.assertEqual(res.state, "done", res.stop_reason)

    def test_ignore_star_applies_to_every_unit(self):
        self.settings["alarm_ignore"] = {"*": ["ds0307"]}
        self.assertEqual(O.ignored_codes(self.settings, "5"), ("DS0307",))
        self.assertEqual(O.allowed_codes(self.settings), ("SW0100",))              # 既定の呼び方は変わらない
        self.assertIn("DS0307", O.allowed_codes(self.settings, "5"))


# ------------------------------------------------------------------ Qt なしの決め方
CTL = controllers.load_controllers(str(Path(__file__).resolve().parent.parent / "マスタ" / "制御装置マスタ.csv"))
PROBE21 = {"axes": {1: {"amr": "85", "motor_id": "297"}, 2: {"amr": "165", "motor_id": "315"},
                    3: {"amr": "85", "motor_id": "297"}, 4: {"amr": "165", "motor_id": "303"}}, "cnc_type": "31"}


class TestLogic(unittest.TestCase):
    def test_write_needs_power_cycle_and_reference_return(self):
        it = {"no": 1815, "label": "A2", "old": "00110000", "new": "00100000", "type_code": 0}
        n = M.write_needs([it])
        self.assertTrue(n["power_cycle"])
        self.assertEqual(n["ref_axes"], ["Y"])
        self.assertEqual(n["lines"], ["書いたあと、電源の入れ直しが要ります",
                                      "Y軸の原点の決め直し（レファレンス点復帰）が要ります"])

    def test_write_needs_by_type_bit_only(self):
        n = M.write_needs([{"no": 1020, "label": "A1", "old": "1", "new": "2", "type_code": 0x41}])
        self.assertTrue(n["power_cycle"])
        self.assertEqual(n["ref_axes"], [])
        n = M.write_needs([{"no": 1020, "label": "A1", "old": "1", "new": "2", "type_code": 0x01}])
        self.assertFalse(n["power_cycle"])
        self.assertEqual(n["lines"], [])

    def test_n1815_without_apz_change_needs_power_but_no_reference_return(self):
        n = M.write_needs([{"no": 1815, "label": "A2", "old": "00110000", "new": "00110010", "type_code": 0}])
        self.assertTrue(n["power_cycle"])
        self.assertEqual(n["ref_axes"], [])

    def test_apz_set_back_by_the_machine_is_accepted(self):
        it = {"no": 1815, "new": "00100000"}
        self.assertTrue(M.apz_tolerant_same(it, "00110000"))
        self.assertFalse(M.apz_tolerant_same(it, "00100000"))                # 同じなら、これは使わない
        self.assertFalse(M.apz_tolerant_same(it, "01110000"))                # 別のビットが違えば合っていない
        self.assertFalse(M.apz_tolerant_same({"no": 1020, "new": "1"}, "2"))

    def test_battery_text(self):
        al = [{"code": "DS0307", "type": 10, "no": 307, "axis": 2, "text": "x"}]
        self.assertEqual(M.battery_lines(al), ["Y軸の位置を覚える電池が弱っています。早めに交換してください"])
        self.assertEqual(M.battery_lines([{"code": "PS0010", "axis": 0}]), [])

    def test_ignore_add_remove_and_split(self):
        st = {}
        al = [{"code": "DS0307"}, {"code": "PS0010"}]
        self.assertEqual(M.split_alarms(al, st, "21"), (al, []))             # 既定は空
        self.assertEqual(M.add_ignore(st, "21", "ds0307"), "21")
        shown, hidden = M.split_alarms(al, st, "21")
        self.assertEqual([a["code"] for a in shown], ["PS0010"])
        self.assertEqual([a["code"] for a in hidden], ["DS0307"])
        self.assertEqual(M.split_alarms(al, st, "22")[1], [])                # 別の号機では無視しない
        M.add_ignore(st, "", "DS0308")                                       # 号機が分からなければ全部の機械
        self.assertEqual(st["alarm_ignore"]["*"], ["DS0308"])
        M.remove_ignore(st, "21", "DS0307")
        self.assertEqual(M.split_alarms(al, st, "21")[1], [])

    def test_unit_phrase_same_spec_continues(self):
        idn = M.identify(PROBE21, CTL)
        self.assertIn("21", idn["units"])
        self.assertIn("20", idn["units"])
        self.assertRegex(M.unit_phrase(idn), r"20号機か21号機（同じ仕様）")

    def test_card_axes_motor_and_change(self):
        ctl21 = next(c for c in CTL if c.unit == "21")
        prods = [{"kind": "回転", "cap": "160A", "voltage": "200V", "motor_id": "315"},
                 {"kind": "傾斜", "cap": "80A", "voltage": "200V", "motor_id": "999"}]
        rows = M.decide_rows(prods, PROBE21, ctl21)
        self.assertEqual([r["axis"] for r in rows], ["Y", "A" if False else rows[1]["axis"]])
        self.assertEqual(rows[0]["axis"], "Y")                                # 160A は Y か A。モーター番号が同じ Y
        self.assertEqual(rows[0]["motor"], "product")
        self.assertEqual(rows[1]["motor"], "temp")                           # 製品の 2020 が機械と違う → 仮（機械のいまの番号）
        self.assertEqual(rows[1]["motor_id"], PROBE21["axes"][1 if rows[1]["axis"] == "X" else 3]["motor_id"])
        self.assertIn("回転→Y軸", M.card_text({"units": ["21"]}, rows))
        self.assertIn("モーター：製品／仮（297）", M.card_text({"units": ["21"]}, rows))
        self.assertTrue(M.changeable(rows))
        other = [a for a in rows[1]["alternatives"] if a != rows[1]["axis"]][0]
        out = M.change_axis(rows, 1, other, PROBE21)
        self.assertEqual(out[1]["axis"], other)
        with self.assertRaises(M.EasyError):
            M.change_axis(rows, 1, "Y", PROBE21)                             # 容量が違う軸には替えられない

    def test_no_capacity_match_leaves_a_choice_of_the_free_axes(self):
        ctl21 = next(c for c in CTL if c.unit == "21")
        prods = [{"kind": "傾斜", "cap": "80A", "voltage": "200V", "motor_id": "297"},
                 {"kind": "回転", "cap": "40A", "voltage": "200V", "motor_id": "293"}]
        rows = M.decide_rows(prods, PROBE21, ctl21)
        self.assertEqual(rows[0]["axis"], "X")
        self.assertEqual(rows[1]["axis"], "")                                 # 40A の軸は無い → 決めない
        self.assertEqual(rows[1]["alternatives"], ["Y", "Z", "A"])            # 使われていない軸から選ぶ
        out = M.change_axis(rows, 1, "Y", PROBE21)
        self.assertEqual(out[1]["axis"], "Y")
        self.assertEqual(out[1]["motor"], "temp")                             # 製品の 2020 と機械の 315 が違う → 仮（315）
        self.assertEqual(out[1]["motor_id"], "315")
        self.assertIn("回転→Y軸", M.card_text({"units": ["21"]}, out))

    def test_400v_product_uses_double_capacity_axis(self):
        ctl21 = next(c for c in CTL if c.unit == "21")
        rows = M.decide_rows([{"kind": "回転", "cap": "40A", "voltage": "400V", "motor_id": ""}], PROBE21, ctl21)
        self.assertIn(rows[0]["axis"], ("X", "Z"))


# ------------------------------------------------------------------ 画面（本物の部品。偽物は機械だけ）
from test_wizard_machine_write import WizardBase                    # noqa: E402


class PanelBase(WizardBase):
    scenario = {"status": {"emergency": 1}}

    def setUp(self):
        super().setUp()
        from PySide6 import QtWidgets
        self.settings.update(focas_backup_dir=str(self.work / "控え"), focas_write_log_csv=str(self.work / "log.csv"),
                             focas_families={"31": "31i"}, basic_from_machine_dir=str(self.work / "取ったBASIC"),
                             focas_registry_csv=str(self.work / "ledger.csv"))
        self.lib = FakeFwlib(scenario=copy.deepcopy(self.scenario))
        self.saved = []
        m = self.lib.m                                   # この機械の Z 軸(A3)に、仮のモーター 290 が付いていて、2022 は 0
        lines = m.param_dat.decode("latin-1").split("\n")
        end = max(i for i, x in enumerate(lines) if x.strip() == "%")
        for no, z in ((2020, "290"), (2022, "0")):
            m.types[no] = 6
            vals = {k: (z if k == 3 else "0") for k in range(1, 13)}
            for k, v in vals.items():
                m.params[(no, f"A{k}")] = v
            lines.insert(end, f"N{no:05d}Q1" + "".join(f"A{k}P{v}" for k, v in vals.items()) + " ")
            end += 1
        m.param_dat = "\n".join(lines).encode("latin-1")
        reg = focas_find.registry_path(self.settings)
        focas_find.remember(reg, "3C7B5D01-49C1F435-5E28C389-6592C486", ip="127.0.0.1", unit="23")
        self.models = []
        O.set_estop_mode("estop_only")

    def tearDown(self):
        O.set_estop_mode("released_only")

    def panel(self, **extra):
        from nd287_app.easy_machine_in_ui import MachineInPanel
        env = {"settings": self.settings, "sync": True, "client_factory": lambda st: DirectClient(lib=self.lib),
               "port_open": lambda ip, port, timeout=0.3: True, "set_model": self.models.append,
               "save_settings": lambda: self.saved.append(1), "backup_root": self.work / "控え"}
        env.update(extra)
        p = MachineInPanel(env)
        self.addCleanup(p.reject)
        return p


class TestPanel(PanelBase):
    def test_first_screen_reads_only_and_shows_the_unit(self):
        p = self.panel()
        p.start()
        self.assertEqual(p.page, "connect")
        self.assertEqual(p.idn["unit"], "23")
        self.assertTrue(p.b_main.isEnabled())
        self.assertEqual(self.lib.called("cnc_wrparam"), 0)                 # 読むだけ
        self.assertIn("23号機", p.lbl_title.text())

    def test_battery_alarm_makes_a_red_band_and_ignore_hides_it(self):
        self.lib.m.alarms = [tuple(BATT)]
        p = self.panel()
        p.start()
        bands = [p.bands.itemAt(i).layout().itemAt(0).widget().text() for i in range(p.bands.count())]
        self.assertTrue(any("Y軸の位置を覚える電池が弱っています。早めに交換してください" in b for b in bands), bands)
        M.add_ignore(self.settings, "23", "DS0307")
        p._render()
        bands = [p.bands.itemAt(i).layout().itemAt(0).widget().text() for i in range(p.bands.count())]
        self.assertFalse(any("電池" in b for b in bands))
        self.assertTrue(any(b == "無視中: DS0307" for b in bands), bands)

    def band_buttons(self, p):
        out = []
        for i in range(p.bands.count()):
            lay = p.bands.itemAt(i).layout()
            out.append((lay.itemAt(0).widget().text(), [lay.itemAt(k).widget() for k in range(1, lay.count())]))
        return out

    def test_battery_band_has_an_ignore_button_that_unblocks_writing(self):
        self.lib.m.alarms = [tuple(BATT)]
        p = self.panel()
        p.start()
        bands = self.band_buttons(p)
        batt = [b for b in bands if "電池" in b[0]]
        self.assertTrue(batt, bands)
        btn = batt[0][1][0]
        self.assertEqual(btn.text(), "この機械では無視する")
        self.assertEqual(self.settings.get("alarm_ignore") or {}, {})          # 押すまでは、何も変えない
        self.assertEqual(self.saved, [])
        btn.click()
        self.assertEqual(self.settings["alarm_ignore"], {"23": ["DS0307"]})   # 既存の設定（号機ごと）に残す
        self.assertEqual(self.saved, [1])                                      # 既存の保存の道
        bands = self.band_buttons(p)
        self.assertFalse(any("電池" in b[0] for b in bands))
        self.assertTrue(any(b[0] == "無視中: DS0307" for b in bands), bands)
        self.walk_to_write(p)                                                  # 無視したので、書き試しが止まらない
        p._do_trial()
        self.assertTrue(p.trial_ok, p._outcome)

    def test_battery_alarm_not_ignored_still_blocks_writing(self):
        self.lib.m.alarms = [tuple(BATT)]
        p = self.panel()
        self.walk_to_write(p)
        p._do_trial()
        self.assertFalse(p.trial_ok)
        self.assertEqual(p._outcome["state"], "not_ready")

    def test_unknown_machine_offers_registration_with_candidates(self):
        self.lib.m.cnc_id = [1, 2, 3, 4]                                      # 台帳に無い機械
        p = self.panel()
        p.start()
        if p.idn["unknown"]:
            self.assertFalse(p.b_main.isEnabled())
            self.assertTrue(hasattr(p, "e_unit"))
        else:                                                                 # 容量の並びが合う号機がある（20・21 など）
            self.assertTrue(p.idn["units"])

    def test_master_mismatch_red_band_and_fix_on_press(self):
        import shutil
        mp = self.work / "master.csv"
        raw = (Path(__file__).resolve().parent.parent / "マスタ" / "制御装置マスタ.csv").read_bytes()
        mp.write_bytes(raw.replace(b"\r\n23,", b"\r\n23,", 1))
        self.settings["controller_master_csv"] = str(mp)
        from nd287_app import controllers as C
        ctl = next(c for c in C.load_controllers(str(mp)) if c.unit == "23")
        # マスタの23号機の X 軸の容量を、機械と違う値にする
        from nd287_app import first_unit
        first_unit.apply_master_fix(str(mp), "23", {"X": "160A"}, "", order=["X", "Y", "Z", "A", "B", "C"])
        p = self.panel()
        p.start()
        self.assertEqual(p.idn["how"], "台帳")
        texts = [p.bands.itemAt(i).layout().itemAt(0).widget().text() for i in range(p.bands.count())]
        self.assertTrue(any("マスタの23号機" in t for t in texts), texts)
        p._fix_master(M.mismatch_lines(p.rd["probe"], M.controller_of(p.planner.controllers(), "23")))
        p._render()
        texts = [p.bands.itemAt(i).layout().itemAt(0).widget().text() for i in range(p.bands.count())]
        self.assertFalse(any("マスタの23号機" in t for t in texts), texts)
        self.assertTrue(list(self.work.glob("master.csv.bak_*")))             # 直す前の控えが残る

    def walk_to_write(self, p):
        p.start()
        p._main()
        self.assertEqual(p.page, "order")
        p.e_seiban.setText("50014984")
        p._search()
        self.assertTrue(p.products)
        self.assertEqual(self.models, [p.products[0]["model"]])               # 型式は製品のデータから自動で入る
        p._main()
        self.assertEqual(p.page, "decide")
        self.assertIsNotNone(p.job, p.lbl_text.text())
        p._main()
        self.assertEqual(p.page, "write")

    def test_full_flow_trial_then_write_then_power_cycle_check(self):
        p = self.panel()
        done = []
        p.finished.connect(done.append)
        self.walk_to_write(p)
        self.assertFalse(p.b_main.isEnabled())                                # 書き試しを押すまで書けない
        self.assertEqual(self.lib.called("cnc_wrparam"), 0)
        p._do_trial()
        self.assertTrue(p.trial_ok, p._outcome)
        self.assertTrue(p.b_main.isEnabled())
        before = self.lib.called("cnc_wrparam")
        self.assertGreaterEqual(before, 1)                                    # 書き試しは今と同じ値を書く
        p._main()                                                             # 書く
        self.assertEqual(p.page, "power", p._outcome)
        self.assertEqual(p._outcome["state"], "done")
        self.assertGreater(self.lib.called("cnc_wrparam"), before)
        p._main()                                                             # 確かめる
        self.assertTrue(p._checked["all_ok"], p._checked)
        p._main()                                                             # 次へ → 測定プログラムを送る段（読むだけ）
        self.assertEqual(p.page, "prog")
        self.assertEqual(done, [])
        p._prog_skip()                                                        # 送らない（機械で用意する）
        self.assertEqual(done, [1])
        r = p.result()
        self.assertTrue(r["handoff"])
        self.assertTrue(r["program_manual"])
        self.assertNotIn("program_handoff", r)

    def test_refusal_is_shown_with_the_setting_instruction(self):
        self.lib.sc["pwe_required"] = True                                    # 書込許可 0（SW0100 も出ていない）
        p = self.panel()
        self.walk_to_write(p)
        p._do_trial()
        self.assertFalse(p.trial_ok)
        self.assertEqual(p._outcome["state"], "protected")
        self.assertIn("『パラメータ書込』を 1 にしてください", p.lbl_msg.text())
        self.assertFalse(p.b_main.isEnabled())

    def test_no_sw0100_is_not_a_reason_to_stop(self):
        p = self.panel()
        self.walk_to_write(p)
        self.assertEqual(self.lib.m.alarms, [])
        p._do_trial()
        self.assertTrue(p.trial_ok, p._outcome)

    def test_writing_notice_for_origin_bit(self):
        p = self.panel()
        self.walk_to_write(p)
        needs = M.write_needs(p.job["items"])
        texts = [p.extra.itemAt(i).widget().text() for i in range(p.extra.count()) if p.extra.itemAt(i).widget() is not None
                 and hasattr(p.extra.itemAt(i).widget(), "text")]
        for line in needs["lines"]:
            self.assertIn(line, texts)

    def test_poller_is_paused_while_panel_is_inside_the_easy_dialog(self):
        from test_easy_mode import FakeHost, FakePoller, S
        host = FakeHost()
        host.easy_machine_in_env = lambda: {"settings": self.settings, "sync": True,
                                            "client_factory": lambda st: DirectClient(lib=self.lib),
                                            "port_open": lambda ip, port, timeout=0.3: True}
        from nd287_app import easy_mode
        poller = FakePoller(S(aut=0, emergency=1))
        dlg = easy_mode.EasyModeDialog(host, poller)
        self.addCleanup(dlg.close)
        self.app.processEvents()
        dlg._start_flow("measure")
        dlg._enter(easy_mode.INDEX["machine_in"])
        self.assertEqual(type(dlg._wiz).__name__, "MachineInPanel")
        self.assertEqual(poller.log, ["pause"])
        self.assertTrue(dlg.b_next.isHidden())
        dlg._close_wizard()
        self.assertEqual(poller.log, ["pause", "resume"])


# ------------------------------------------------------------------ 測定プログラムを送る段（かんたん作成の段④と同じ中身）
FOLDER = "//CNC_MEM/USER/PATH1/"


class TestPanelPrograms(PanelBase):
    def setUp(self):
        super().setUp()
        cond = self.work / "測定条件.csv"
        cond.write_text("回転,,,,,,,,,,,,傾斜\n型式,クローズ,間隔H,間隔W,1/N_H,1/N_W,分割数1,分割数2,"
                        "測定順HR,測定順WR,測定順WL,測定順HL,\nRTV-510,,100000,3000,1,1,36,10,1,2,3,4,\n",
                        encoding="utf-8-sig")
        rep = self.work / "再現.csv"
        rep.write_text("型式,ブロック数,回数,再現開始,再現終了\nRTV-510,4,5,0,270\n", encoding="utf-8-sig")
        self.settings.update(
            conditions_csv=str(cond), user_rotary_repeat_csv=str(rep),
            user_rotary_csv=str(self.work / "none1.csv"), user_tilt_csv=str(self.work / "none2.csv"),
            user_tilt_repeat_csv=str(self.work / "none3.csv"), judgement_csv=str(self.work / "none4.csv"),
            prog_log_csv=str(self.work / "proglog.csv"), focas_prog_folder=FOLDER)
        self.m = self.lib.m
        self.m.programs.pop(FOLDER + "O1000", None)                  # 偽物の機械にもともとある O1000（別の中身）は、別の試験で使う

    def panel(self, **extra):
        extra.setdefault("model_fn", lambda: "RTV-510")             # 製品を探さずに来たときの、測定の画面の型式
        return super().panel(**extra)

    def written(self):
        return list(getattr(self.m, "written_programs", []))

    def to_prog_after_params(self, p):
        TestPanel.walk_to_write(self, p)
        p._do_trial()
        p._main()
        p._main()                                                            # 確かめる
        self.assertTrue(p._checked["all_ok"], p._checked)
        p._main()
        self.assertEqual(p.page, "prog")

    def labels(self, p):
        from PySide6 import QtWidgets
        return [p.extra.itemAt(i).widget().text() for i in range(p.extra.count())
                if isinstance(p.extra.itemAt(i).widget(), (QtWidgets.QLabel, QtWidgets.QCheckBox, QtWidgets.QPushButton))]

    def old_program(self, main):
        return b"%\nO" + b"%04d" % main + b"(OLD) \nM30 \n%"

    def test_other_content_on_the_sub_number_is_held_not_overwritten(self):
        """偽物の機械にもとからある O1000（別の中身）。本体とサブはセット。チェックが無ければ、どちらも送らない。"""
        self.m.add_program(FOLDER + "O1000", b"%\nO1000(MAIN) \nM98P901 \nM30 \n%", "MAIN")
        old = self.m.programs[FOLDER + "O1000"]
        self.m.status["emergency"] = 0
        p = self.panel()
        p.start()
        p._skip_params()
        self.assertEqual(p.prog_rd["states"][1000], "differs")
        p._prog_do_send()
        self.assertEqual(self.written(), [])                                     # セットの片方だけは送らない
        self.assertEqual(self.m.programs[FOLDER + "O1000"], old)
        texts = " ".join(self.labels(p))
        self.assertIn("送っていません", texts)
        self.assertIn("もう一度確かめる", texts)
        self.assertFalse(p.b_main.isEnabled())

    def test_battery_alarm_blocks_program_send_until_ignored_on_the_connect_band(self):
        self.m.status["emergency"] = 0
        self.m.alarms = [tuple(BATT)]
        p = self.panel()
        p.start()
        p._skip_params()
        self.assertTrue(any("DS0307" in r for r in p.prog_rd["chk"]["reasons"]), p.prog_rd["chk"]["reasons"])
        p._prog_do_send()
        self.assertEqual(self.written(), [])                                     # 無視していないので、書かない
        p._go("connect")
        batt = [b for b in TestPanel.band_buttons(self, p) if "電池" in b[0]]
        batt[0][1][0].click()                                                    # ［この機械では無視する］
        p._skip_params()
        self.assertEqual(p.prog_rd["chk"]["reasons"], [])
        p._prog_do_send()
        self.assertTrue(self.written())

    def test_stage_names_include_programs(self):
        p = self.panel()
        p.start()
        self.assertIn("プログラム", p.lbl_stage.text())

    def test_program_stage_reads_only_then_sends_once_with_readback(self):
        p = self.panel()
        self.to_prog_after_params(p)
        wr_params = self.lib.called("cnc_wrparam")
        self.assertEqual(self.written(), [])                                     # 読んだだけ。まだ何も書いていない
        nums = M.prog_numbers(p.prog_plan)
        self.assertTrue(nums and nums[0] == 100, nums)
        texts = " ".join(self.labels(p))
        for n in nums:
            self.assertIn(f"O{n:04d}", texts)                                    # 送る番号が見える
        self.assertTrue(any("非常停止" in t for t in self.labels(p)))            # プログラムは非常停止をもどしてから
        self.assertFalse(p.b_main.isEnabled())
        p._prog_do_send()                                                         # 非常停止のままなら、押しても書かない
        self.assertEqual(self.written(), [])
        self.m.status["emergency"] = 0                                            # もどした
        p._prog_reread()
        self.assertEqual(p.prog_rd["chk"]["reasons"], [], p.prog_rd["chk"]["reasons"])
        self.assertIn("送ります", " ".join(self.labels(p)))                      # 機械に同じ番号が無い
        self.assertTrue(p.b_main.isEnabled())
        self.assertEqual(p.b_main.text(), "送る")
        self.assertEqual(self.written(), [])
        p._main()                                                                 # ［送る］
        written = self.written()
        self.assertEqual(len(written), len(nums), written)
        self.assertEqual(self.lib.called("cnc_wrparam"), wr_params)              # パラメータは、ここでは書いていない
        texts = self.labels(p)
        for n in nums:
            self.assertTrue(any(t.startswith("✓") and f"O{n:04d}" in t for t in texts), texts)   # 番号ごとに ✓（読み戻し一致）
        self.assertTrue(all(it["match"] for it in p.prog_results.values()))
        self.assertEqual(p.b_main.text(), "次へ ▶")
        done = []
        p.finished.connect(done.append)
        p._main()                                                                 # 次へ。もう一度は送らない
        self.assertEqual(done, [1])
        self.assertEqual(self.written(), written)
        r = p.result()
        self.assertFalse(r["program_manual"])
        self.assertEqual(r["program_handoff"]["steps"][0]["programs"], [100])
        self.assertTrue(r["handoff"])
        self.assertTrue(Path(self.settings["prog_log_csv"]).exists())            # 送った記録は、段④と同じ所に残る
        self.assertEqual(self.settings["wizard_prog_choice"], p.prog_plan["choice"])

    def test_second_press_of_send_does_not_write_again(self):
        p = self.panel()
        self.m.status["emergency"] = 0
        p.start()
        p._skip_params()
        self.assertEqual(p.page, "prog")
        p._prog_do_send()
        n = len(self.written())
        self.assertGreaterEqual(n, 1)
        p._prog_do_send()
        self.assertEqual(len(self.written()), n)

    def test_usable_when_parameters_are_skipped(self):
        self.m.status["emergency"] = 0
        p = self.panel()
        p.start()
        self.assertTrue(any(getattr(w, "text", lambda: "")().startswith("パラメータは書かない") for w in
                            [p.extra.itemAt(i).widget() for i in range(p.extra.count())]))
        p._skip_params()
        self.assertEqual(p.page, "prog")
        self.assertEqual(self.lib.called("cnc_wrparam"), 0)                      # パラメータは書かない
        self.assertEqual(self.written(), [])
        self.assertEqual(p.prog_rd["chk"]["reasons"], [])
        p._main()
        self.assertTrue(self.written())
        self.assertEqual(self.lib.called("cnc_wrparam"), 0)
        p._main()
        r = p.result()
        self.assertFalse(r["program_manual"])
        self.assertFalse(r["cycled"])
        self.assertIsNotNone(r["program_handoff"])

    def test_same_number_with_other_content_is_not_overwritten_without_the_check(self):
        self.m.status["emergency"] = 0
        p = self.panel()
        p.start()
        main = 100
        path = FOLDER + f"O{main}"
        self.m.add_program(path, self.old_program(main), "OLD")
        old = self.m.programs[path]
        p._skip_params()
        self.assertEqual(p.prog_rd["states"][main], "differs")
        texts = self.labels(p)
        self.assertTrue(any("中身が違います" in t for t in texts), texts)        # 何が上書きになるか見える
        self.assertIn(main, p._prog_checks)
        self.assertFalse(p._prog_checks[main].isChecked())                       # 初めは入れ替えない
        asked = []
        p._confirm_overwrite = lambda nums: (asked.append(nums), True)[1]
        p._prog_do_send()
        self.assertEqual(asked, [])                                              # チェックが無いので聞かず、入れ替えない
        self.assertEqual(self.m.programs[path], old)

    def test_overwrite_needs_the_check_and_an_explicit_confirmation(self):
        self.m.status["emergency"] = 0
        p = self.panel()
        p.start()
        main = 100
        path = FOLDER + f"O{main}"
        self.m.add_program(path, self.old_program(main), "OLD")
        old = self.m.programs[path]
        p._skip_params()
        p._prog_checks[main].setChecked(True)
        p._confirm_overwrite = lambda nums: False                                # 確認で［やめる］
        p._prog_do_send()
        self.assertEqual(self.m.programs[path], old)
        self.assertFalse(p.prog_sent)
        asked = []
        p._confirm_overwrite = lambda nums: (asked.append(nums), True)[1]
        p._prog_do_send()
        self.assertEqual(asked, [[main]])
        self.assertNotEqual(self.m.programs[path], old)                          # 確認のあと、入れ替わった
        self.assertTrue(any(t.startswith("✓") and f"O{main:04d}" in t for t in self.labels(p)))

    def test_already_same_content_is_not_sent_and_shows_a_check(self):
        self.m.status["emergency"] = 0
        p = self.panel()
        p.start()
        p._skip_params()
        p._prog_do_send()
        first = self.written()
        p2 = self.panel()
        p2.start()
        p2._skip_params()
        self.assertTrue(all(st == "same" for st in p2.prog_rd["states"].values()), p2.prog_rd["states"])
        self.assertTrue(any("もう入っています" in t for t in self.labels(p2)))
        p2._prog_do_send()                                                       # 同じ中身: 送るものは無い
        self.assertEqual(self.written(), first)

    def test_cannot_send_with_a_reason_and_skip_is_always_offered(self):
        p = self.panel()
        p.start()
        p._skip_params()                                                         # 非常停止のまま
        self.assertTrue(p.prog_rd["chk"]["reasons"])
        self.assertFalse(p.b_main.isEnabled())
        self.assertTrue(any(t.startswith("プログラムは機械で用意する") for t in self.labels(p)))
        done = []
        p.finished.connect(done.append)
        p._prog_skip()
        self.assertEqual(done, [1])
        self.assertTrue(p.result()["program_manual"])
        self.assertEqual(self.written(), [])

    def test_easy_dialog_applies_the_program_handoff_and_keeps_programs_auto(self):
        from PySide6 import QtWidgets
        from test_easy_mode import FakeHost, FakePoller, S
        from nd287_app import easy_mode

        class MachineInPanel(QtWidgets.QWidget):
            def result(self):
                return {"handoff": True, "cycled": True, "unit": "23", "program_manual": False, "panel": True,
                        "program_handoff": {"model": "RTV-510", "steps": [{"mode": "回転分割+再現", "programs": [100]}]}}

            def reject(self):
                pass

        host = FakeHost()
        host.easy_apply_handoff = lambda h: host.calls.append(("handoff", h["model"]))
        dlg = easy_mode.EasyModeDialog(host, FakePoller(S(aut=0, emergency=1)))
        self.addCleanup(dlg.close)
        dlg._start_flow("measure")
        dlg._wiz = MachineInPanel(dlg.embed)
        dlg._wizard_finished(1)
        self.assertIn(("handoff", "RTV-510"), host.calls)
        self.assertNotEqual(dlg.ctx.get("prog"), "manual")                       # 送ったので、プログラムは機械で用意しない
        self.assertTrue(dlg.ctx["cycled"])                                       # 電源の入れ直しは、画面の中で済んだ
        self.assertTrue(dlg.ctx["handoff"])


if __name__ == "__main__":
    unittest.main()
