# -*- coding: utf-8 -*-
"""focas_ops の書込の流れ（段4。設計 §11.2 の 37〜56・62・63）。偽物の機械へ直接つなぐ。

実機にはつながない。本物の DLL も 32bit Python も呼ばない。
電源待ちと自動再接続（57〜61）は段6なので、ここには無い（PW0000 を見つけて言うところまで）。
"""

import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import fanuc_param as FP            # noqa: E402
from nd287_app import focas_backup as B            # noqa: E402
from nd287_app import focas_client as C            # noqa: E402
from nd287_app import focas_ops as O               # noqa: E402
from nd287_app import param_build as P             # noqa: E402
from nd287_app.settings import DEFAULTS            # noqa: E402
from fake_client import DirectClient               # noqa: E402

PWE = [100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]
CNC_ID = "3C7B5D01-49C1F435-5E28C389-6592C486"
ZERO = ("1850", "1851", "1852")
ALL_KINDS = ["byte/none", "bit/A", "dword/A", "real/A", "word/A", "byte/A", "byte/S"]



def setUpModule():
    # この試験は「非常停止を解除した機械」を前提に書かれている。新しい既定（非常停止のときだけ書く）は test_estop_mode で確かめる
    from nd287_app import focas_ops as _fo
    _fo.set_estop_mode("released_only")


def tearDownModule():
    from nd287_app import focas_ops as _fo
    _fo.set_estop_mode("estop_only")


class WriteBase(unittest.TestCase):
    scenario = {"alarms": [PWE]}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.settings = copy.deepcopy(DEFAULTS)
        self.client = DirectClient(copy.deepcopy(self.scenario)).connect()
        res = B.take_backup(self.client, self.tmp / "控え", self.settings)
        self.assertTrue(res.complete, res.problems)
        self.folder, self.info = res.folder, res.info
        self.raw = (self.folder / "パラメータ.DAT").read_bytes().decode("cp932")
        self.types = O._backup_values(self.folder)[1]
        self.log = self.tmp / "log.csv"
        self.job = self.tmp / O.JOB_NAME
        self.m = self.client.machine

    def tearDown(self):
        self._tmp.cleanup()

    def plan(self, values, *, axis=4, kinds=None, zero_scope="all", raw=None, **kw):
        raw = raw or self.raw
        p = P.prepare(raw, values, axis=axis, reference="", zero_params=kw.pop("zero_params", ()),
                      zero_scope=zero_scope, **kw)
        verified = self.settings["focas_verified_kinds"]["31i"] if kinds is None else kinds
        return p, O.plan_writes(raw, p, self.types, verified=verified)

    def run_write(self, items, *, newtext=None, **kw):
        kw.setdefault("job_path", self.job)
        return O.run_write(self.client, items, cnc_id=CNC_ID, backup_info=self.info,
                           settings=self.settings, family="31i", newtext=newtext, types=self.types,
                           log_path=self.log, **kw)

    def wr_calls(self):
        return self.client.lib.called("cnc_wrparam")

    def machine_value(self, no, label=""):
        return self.client.call("param_read", {"no": no, "label": label})["value"]


class TestPlan(WriteBase):
    def test_list_is_exactly_the_diff_and_matches_the_preview_count(self):          # 42
        p, pl = self.plan({"20": "4", "21": "1", "1420": "8000.000"}, kinds=ALL_KINDS)
        self.assertIsNone(pl["mismatch"])
        diff = [(int(n), l, o, nw) for n, l, o, nw in FP.diff(self.raw, p["newtext"])]
        self.assertEqual([(i["no"], i["label"], i["old"], i["new"]) for i in pl["items"]], diff)
        changed = [r for r in p["rows"] if r[5]]
        self.assertEqual(len(pl["items"]), len(changed))
        self.assertEqual(pl["expected"], len(changed))
        self.assertEqual(len(pl["items"]), 3)
        self.assertEqual(pl["blockers"], [])

    def test_prepare_with_empty_reference_does_not_warn_about_numbers_on_the_machine(self):   # 82
        raw = self.raw + "\nN09820Q1P0 \n"
        p = P.prepare(raw, {"20": "4"}, axis=4, reference="")
        self.assertFalse([x for x in p["problems"] if "実機のバックアップに無い" in x], p["problems"])

    def test_numbers_missing_on_the_machine_are_dropped_and_counted(self):         # 43
        p, pl = self.plan({"20": "4", "99999": "1"})
        self.assertEqual(len(pl["items"]), 1)
        self.assertEqual(len(pl["missing"]), 1)
        self.assertEqual(pl["missing"][0][4], P.KIND_MISSING)
        self.assertEqual(pl["expected"], 1)
        self.assertIsNone(pl["mismatch"])

    def test_soft_limit_skip_leaves_the_number_out_of_the_list(self):              # 44
        vals = {"20": "4", "1320": "-9999.0", "1321": "9999.0"}
        _p, pl = self.plan(vals, kinds=ALL_KINDS, soft_limit="skip")
        self.assertEqual([i["no"] for i in pl["items"]], [20])
        p2, pl2 = self.plan(vals, kinds=ALL_KINDS, soft_limit="apply")
        self.assertEqual(sorted(i["no"] for i in pl2["items"]), [20, 1320, 1321])
        self.assertIsNone(pl2["mismatch"])
        # プレビューの行と書込の一覧が同じ番号（調査記録 §18 の事故の再発防止）
        shown = sorted(int(r[0].split("(")[0]) for r in p2["rows"] if r[5])
        self.assertEqual(shown, [20, 1320, 1321])

    def _raw_with_individual(self):
        raw = self.raw
        for no in (1850,):
            raw = raw.replace("N01850Q1A1P0A2P0A3P0A4P0A5P0A6P0",
                              "N01850Q1A1P11A2P22A3P33A4P44A5P55A6P66")
        return raw

    def test_zero_rows_all_axes_or_product_axis_only(self):                         # 45
        raw = self._raw_with_individual()
        self.assertIn("A1P11", raw)
        p, pl = self.plan({"20": "4"}, raw=raw, zero_params=ZERO, zero_scope="all", kinds=ALL_KINDS)
        zeros = [(i["no"], i["label"]) for i in pl["items"] if i["no"] == 1850]
        self.assertEqual(zeros, [(1850, f"A{k}") for k in range(1, 7)])
        self.assertIsNone(pl["mismatch"])
        p, pl = self.plan({"20": "4"}, raw=raw, zero_params=ZERO, zero_scope="product",
                          kinds=ALL_KINDS)
        zeros = [(i["no"], i["label"], i["new"]) for i in pl["items"] if i["no"] == 1850]
        self.assertEqual(zeros, [(1850, "A4", "0")])                  # 製品の軸（4）だけ
        self.assertIsNone(pl["mismatch"])
        self.assertEqual(sorted(r[1] for r in p["rows"] if r[4] == P.KIND_ZERO), ["製品の軸"])
        # 製品の軸でない軸の値は、書込の一覧にも newtext にも出てこない
        self.assertIn("A1P11", p["newtext"])

    def test_zero_is_not_in_the_list_when_the_choice_is_off(self):                  # 45
        raw = self._raw_with_individual()
        _p, pl = self.plan({"20": "4"}, raw=raw, zero_params=(), kinds=ALL_KINDS)
        self.assertEqual([i["no"] for i in pl["items"]], [20])

    def test_types_and_verified_flags(self):
        _p, pl = self.plan({"20": "4", "1815": "001*0000", "1420": "8000.000"})
        by = {i["no"]: i for i in pl["items"]}
        self.assertEqual(by[20]["key"], "byte/none")
        self.assertTrue(by[20]["verified"])
        self.assertEqual(by[1815]["key"], "bit/A")
        self.assertFalse(by[1815]["verified"])
        self.assertEqual(by[1420]["key"], "real/A")
        self.assertEqual(sorted(pl["unverified"]), ["bit/A", "real/A"])
        self.assertEqual(len([b for b in pl["blockers"] if "まだ書いたことがありません" in b]), 2)

    def test_l_label_and_unwritable_value_block_the_plan(self):
        _p, pl = self.plan({("3202", "L1"): "11000000"}, kinds=ALL_KINDS + ["bit/L"])
        self.assertTrue(any("系統型" in b for b in pl["blockers"]), pl["blockers"])
        _p, pl = self.plan({"20": "300"})
        self.assertTrue(any("-128" in b or "範囲" in b for b in pl["blockers"]), pl["blockers"])

    def test_non_fanuc_format_is_not_written(self):
        pl = O.plan_writes("Axis,1\nA,B\n", {"fmt": "headercsv", "rows": [], "newtext": ""}, {},
                           verified=["byte/none"])
        self.assertTrue(pl["blockers"])
        self.assertEqual(pl["items"], [])

    def test_count_mismatch_with_the_preview_blocks_the_write(self):
        p, _pl = self.plan({"20": "4"})
        p = dict(p, rows=p["rows"] + [("21", "", "0", "1", P.KIND_PRODUCT, True)])
        pl = O.plan_writes(self.raw, p, self.types, verified=["byte/none"])
        self.assertIsNotNone(pl["mismatch"])
        self.assertIn("合いません", pl["mismatch"])
        self.assertTrue(pl["blockers"])


class TestRunWrite(WriteBase):
    def test_each_item_is_read_then_written_then_read_then_alarms_checked(self):      # 46
        _p, pl = self.plan({"20": "4"})
        before = len(self.client.calls)
        res = self.run_write(pl["items"])
        seq = self.client.calls[before:]
        i = seq.index("param_write")
        self.assertEqual(seq[i - 1], "param_read")
        self.assertEqual(seq[i + 1], "param_read")
        self.assertEqual(seq[i + 2], "alarms")
        self.assertEqual(res.state, "done")
        self.assertEqual(res.items[0]["status"], "ok")
        self.assertEqual(res.items[0]["before"], "9")
        self.assertEqual(res.items[0]["after"], "4")
        self.assertEqual(self.machine_value(20), "4")
        self.assertEqual(self.wr_calls(), 1)

    def test_value_on_the_machine_differs_from_the_base_so_nothing_is_written(self):   # 47
        _p, pl = self.plan({"20": "4"})
        self.m.set_param(20, "", "7")                        # 控えを取ったあとで人が変えた
        res = self.run_write(pl["items"])
        self.assertEqual(self.wr_calls(), 0)
        self.assertEqual(res.state, "stopped")
        self.assertEqual(res.items[0]["status"], "stale")
        self.assertIn("控えを取ったあと", res.items[0]["note"])
        self.assertFalse(res.items[0]["touched"])
        self.assertEqual(self.machine_value(20), "7")

    def test_nonzero_return_stops_and_leaves_the_rest_unwritten(self):                 # 48
        _p, pl = self.plan({"20": "4", "21": "1", "22": "1"})
        self.client.lib.sc["write_rc_at"] = {"2": 13}        # 2件目を機械が受け付けない
        res = self.run_write(pl["items"])
        self.assertEqual([i["status"] for i in res.items], ["ok", "failed", "pending"])
        self.assertEqual(res.state, "stopped")
        self.assertIn("N00021", res.stop_reason)
        self.assertEqual(self.wr_calls(), 2)                 # 3件目は呼んでいない
        self.assertEqual(self.machine_value(22), "0")
        self.assertEqual(res.items[1]["rc"], 13)
        self.assertEqual([i["touched"] for i in res.items], [True, False, False])

    def test_readback_mismatch_stops(self):                                           # 49
        _p, pl = self.plan({"20": "4", "21": "1"})
        self.client.lib.sc["write_ignore"] = [20]            # 戻り値 0 だが書いていない機械
        res = self.run_write(pl["items"])
        self.assertEqual(res.items[0]["status"], "mismatch")
        self.assertEqual(res.items[1]["status"], "pending")
        self.assertEqual(self.wr_calls(), 1)
        self.assertIn("読み戻し", res.items[0]["note"])

    def test_a_disallowed_alarm_stops_but_pw0000_does_not(self):                      # 50
        _p, pl = self.plan({"20": "4", "21": "1"})
        self.client.lib.sc["write_alarms"] = {"20": [[401, 6, 1, "SERVO ALARM"]]}
        res = self.run_write(pl["items"])
        self.assertEqual(res.state, "stopped")
        self.assertEqual(res.items[0]["status"], "ok")       # 値は書けて読み戻せている
        self.assertTrue(res.items[0]["touched"])
        self.assertEqual(res.items[1]["status"], "pending")
        self.assertIn("許していないアラーム", res.stop_reason)
        self.assertIn("SV0401", res.stop_reason)

    def test_pw0000_during_the_write_is_allowed_and_reported(self):                   # 50
        _p, pl = self.plan({"20": "4", "21": "1"})
        self.client.lib.sc["write_alarms"] = {"20": [[0, 1, 0, "POWER MUST BE OFF"]]}
        res = self.run_write(pl["items"])
        self.assertEqual(res.state, "done")
        self.assertEqual([i["status"] for i in res.items], ["ok", "ok"])
        self.assertTrue(res.power_cycle)
        self.assertEqual([a["code"] for a in res.new_alarms], ["PW0000"])
        self.assertFalse(any(a["code"] == "SW0100" for a in res.new_alarms))

    def test_no_power_alarm_when_none_appears(self):
        _p, pl = self.plan({"20": "4"})
        res = self.run_write(pl["items"])
        self.assertFalse(res.power_cycle)
        self.assertEqual(res.new_alarms, [])

    def test_cancel_stops_before_the_next_item(self):                                 # 51
        _p, pl = self.plan({"20": "4", "21": "1", "22": "1"})
        n = {"i": 0}

        def on_item(i, it):
            n["i"] = i + 1

        res = self.run_write(pl["items"], cancel=lambda: n["i"] >= 1, on_item=on_item)
        self.assertEqual(res.state, "cancelled")
        self.assertEqual([i["status"] for i in res.items], ["ok", "pending", "pending"])
        self.assertEqual(self.wr_calls(), 1)

    def test_undo_writes_back_only_what_was_written_in_reverse_order(self):           # 52
        _p, pl = self.plan({"20": "4", "21": "1", "22": "1"})
        self.client.lib.sc["write_rc_at"] = {"3": 13}
        res = self.run_write(pl["items"])
        self.assertEqual(res.state, "stopped")
        self.client.lib.sc["write_rc_at"] = {}
        self.m.writes.clear()
        undo = O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                          settings=self.settings, family="31i", log_path=self.log)
        self.assertEqual(undo.state, "done")
        self.assertEqual(self.m.writes, [(21, "", "0"), (20, "", "9")])      # 書いた2件だけ・逆順
        self.assertEqual(self.machine_value(20), "9")
        self.assertEqual(self.machine_value(21), "0")
        self.assertEqual([i["status"] for i in undo.items], ["ok", "ok"])

    def test_undo_stops_when_a_person_changed_the_value_meanwhile(self):
        _p, pl = self.plan({"20": "4"})
        res = self.run_write(pl["items"])
        self.m.set_param(20, "", "88")
        self.m.writes.clear()
        undo = O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                          settings=self.settings, family="31i")
        self.assertEqual(undo.state, "stopped")
        self.assertEqual(undo.items[0]["status"], "stale")
        self.assertEqual(self.m.writes, [])

    def test_undo_with_nothing_written_is_refused(self):
        _p, pl = self.plan({"20": "4"})
        self.m.set_param(20, "", "7")
        res = self.run_write(pl["items"])
        with self.assertRaises(O.WriteRefused):
            O.run_undo(self.client, res, cnc_id=CNC_ID, backup_info=self.info,
                       settings=self.settings, family="31i")

    def test_unverified_type_is_refused_before_any_write(self):                       # 53
        _p, pl = self.plan({"20": "4", "1815": "001*0000"})
        with self.assertRaises(O.WriteRefused) as cm:
            self.run_write(pl["items"])
        self.assertIn("同じ値を書く確認", "\n".join(cm.exception.reasons))
        self.assertEqual(self.wr_calls(), 0)                  # 20 も書いていない（全部か、何もか）
        self.assertEqual(self.machine_value(20), "9")

    def test_helper_also_refuses_an_unverified_type_if_the_app_were_wrong(self):
        p, pl = self.plan({"1815": "001*0000"}, kinds=ALL_KINDS)
        items = [dict(i, verified=True) for i in pl["items"]]
        self.settings["focas_verified_kinds"] = {"31i": ["byte/none", "bit/A"]}
        self.client.lib.sc["force_rc"] = {}
        res = self.run_write(items)                           # アプリが「確認済み」と渡せば書く
        self.assertEqual(res.state, "done")
        # アプリの表に無い型を渡しても、ヘルパーが断る（二重の守り）
        with self.assertRaises(C.FocasError) as cm:
            self.client.call("param_write", {"no": 1821, "label": "A4", "value": "100",
                                              "verified": ["byte/none"]})
        self.assertEqual(cm.exception.kind, "refused")

    def test_verify_kind_writes_the_same_value_and_adds_the_type_only_on_success(self):   # 54
        _p, pl = self.plan({"1815": "001*0000"})
        it = pl["items"][0]
        self.assertEqual(it["key"], "bit/A")
        before = self.machine_value(1815, "A4")
        out = O.verify_kind(self.client, it, cnc_id=CNC_ID, backup_info=self.info,
                            settings=self.settings, family="31i", log_path=self.log)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.m.writes, [(1815, "A4", before)])             # 今と同じ値を書いた
        self.assertEqual(self.machine_value(1815, "A4"), before)             # 値は変わっていない
        self.assertNotIn("bit/A", O.verified_list(self.settings, "31i"))     # 足すのは呼ぶ側
        O.add_verified_kind(self.settings, "31i", out["key"])
        self.assertIn("bit/A", O.verified_list(self.settings, "31i"))
        self.assertIn("byte/none", O.verified_list(self.settings, "31i"))
        _p, pl2 = self.plan({"1815": "001*0000"})
        self.assertTrue(pl2["items"][0]["verified"])
        self.assertEqual(pl2["unverified"], {})

    def test_verify_kind_failure_is_not_added(self):                                  # 54
        _p, pl = self.plan({"1815": "001*0000"})
        self.client.lib.sc["write_ignore"] = [1815]
        self.client.lib.sc["write_store"] = {}
        self.m.set_param(1815, "A4", "00000000")
        self.client.lib.sc["write_store"] = {1815: "11111111"}
        self.client.lib.sc["write_ignore"] = []
        out = O.verify_kind(self.client, pl["items"][0], cnc_id=CNC_ID, backup_info=self.info,
                            settings=self.settings, family="31i")
        self.assertFalse(out["ok"])
        self.assertEqual(out["result"], "mismatch")
        self.assertNotIn("bit/A", O.verified_list(self.settings, "31i"))

    def test_verify_kind_is_refused_when_not_writable(self):
        _p, pl = self.plan({"1815": "001*0000"})
        self.m.status["run"] = 3
        with self.assertRaises(O.WriteRefused):
            O.verify_kind(self.client, pl["items"][0], cnc_id=CNC_ID, backup_info=self.info,
                          settings=self.settings, family="31i")
        self.assertEqual(self.wr_calls(), 0)

    def test_full_dump_compare_lists_numbers_that_changed_without_being_written(self):   # 55
        p, pl = self.plan({"20": "4"})
        self.client.lib.sc["write_side"] = {"20": [[6753, "L1", "9999"]]}     # 積算カウンタが動いた
        res = self.run_write(pl["items"], newtext=p["newtext"])
        v = res.verify
        self.assertTrue(v["ok"])
        self.assertEqual(v["written_ok"], 1)
        self.assertEqual([(n, l) for n, l, _a, _b in v["unexpected"]], [("06753", "L1")])
        self.assertEqual(v["unexpected"][0][2:], ("9280", "9999"))

    def test_full_dump_compare_catches_a_written_number_that_is_not_what_we_wrote(self):   # 55
        p, pl = self.plan({"20": "4"})
        # 書込直後の読み戻しは通るが、丸ごと吸い出すと違う、という機械は有りえないので、
        # newtext 側を変えて「全行比較が違いを拾う」ことを確かめる
        res = self.run_write(pl["items"], newtext=p["newtext"].replace("N00020Q1P4", "N00020Q1P5"))
        self.assertFalse(res.verify["ok"])
        self.assertEqual(res.verify["written_ng"][0][:2], ("00020", ""))

    def test_nothing_unexpected_when_the_machine_is_quiet(self):
        p, pl = self.plan({"20": "4", "21": "1"})
        res = self.run_write(pl["items"], newtext=p["newtext"])
        self.assertEqual(res.verify["unexpected"], [])
        self.assertEqual(res.verify["written_ng"], [])
        self.assertGreater(res.verify["checked"], 20)

    def test_real_values_are_compared_as_numbers(self):                               # 56
        self.assertTrue(O.same_value("real", "20000.0", "20000"))
        self.assertTrue(O.same_value("real", "20000.0", "20000.000"))
        self.assertFalse(O.same_value("real", "20000.0", "20000.5"))
        self.assertTrue(O.same_value("bit", "00100000", "00100000"))
        self.assertFalse(O.same_value("bit", "00100000", "00100001"))
        self.assertTrue(O.same_value("dword", "+6000", "6000"))
        self.settings["focas_verified_kinds"] = {"31i": ALL_KINDS}
        p, pl = self.plan({"1420": "8000.000"})
        res = self.run_write(pl["items"], newtext=p["newtext"])
        self.assertEqual(res.items[0]["status"], "ok")
        self.assertEqual(res.items[0]["after"], "8000.0")
        self.assertTrue(res.verify["ok"])

    def test_all_types_can_be_written_once_they_are_verified(self):
        self.settings["focas_verified_kinds"] = {"31i": ALL_KINDS}
        p, pl = self.plan({"20": "4", "1815": "001*0000", "1821": "360000", "1420": "8000.000"})
        self.assertEqual(pl["blockers"], [])
        res = self.run_write(pl["items"], newtext=p["newtext"])
        self.assertEqual(res.state, "done")
        self.assertEqual(self.machine_value(1815, "A4"), "00100000")
        self.assertEqual(self.machine_value(1821, "A4"), "360000")
        self.assertTrue(res.verify["ok"])
        self.assertEqual(res.verify["unexpected"], [])
        self.assertEqual(self.wr_calls(), 4)


class TestGate(WriteBase):
    def refused(self, items=None):
        if items is None:
            items = self.plan({"20": "4"})[1]["items"]
        with self.assertRaises(O.WriteRefused) as cm:
            self.run_write(items)
        self.assertEqual(self.wr_calls(), 0)
        return "\n".join(cm.exception.reasons)

    def test_running_paused_moving_emergency_are_refused(self):                       # 37・38
        for kw in ({"run": 3}, {"run": 2}, {"run": 1}, {"motion": 1}, {"emergency": 1}):
            self.m.status.update({"run": 0, "motion": 0, "emergency": 0})
            self.m.status.update(kw)
            self.refused()
        self.m.status.update({"run": 0, "motion": 0, "emergency": 0})
        _p, pl = self.plan({"20": "4"})
        self.assertEqual(self.run_write(pl["items"]).state, "done")                  # リセットなら書ける

    def test_other_alarms_are_refused_but_sw0100_alone_is_fine(self):                 # 39
        self.m.alarms.append((401, 6, 1, "SERVO ALARM"))
        self.assertIn("SV0401", self.refused())
        self.m.alarms.pop()
        _p, pl = self.plan({"20": "4"})
        self.assertEqual(self.run_write(pl["items"]).state, "done")

    def test_pwe_off_is_refused_with_guidance(self):                                  # 40
        self.m.alarms.clear()
        self.assertIn("パラメータ書込", self.refused())

    def test_backup_missing_incomplete_or_of_another_machine(self):                   # 41
        _p, pl = self.plan({"20": "4"})
        for info in (None, dict(self.info, complete=False), dict(self.info, cnc_id="FFFF-0-0-0")):
            with self.assertRaises(O.WriteRefused):
                O.run_write(self.client, pl["items"], cnc_id=CNC_ID, backup_info=info,
                            settings=self.settings, family="31i")
        self.assertEqual(self.wr_calls(), 0)

    def test_another_machine_after_connecting_is_refused(self):
        self.m.cnc_id = [1, 2, 3, 4]
        self.assertIn("CNC ID", self.refused())

    def test_gate_is_checked_before_every_item(self):
        _p, pl = self.plan({"20": "4", "21": "1"})
        orig = self.client.call
        state = {"n": 0}

        def call(cmd, args=None, **kw):
            if cmd == "param_write":
                state["n"] += 1
                if state["n"] == 1:
                    self.m.status["run"] = 3           # 1件目を書いた直後に、人が運転を始めた
            return orig(cmd, args, **kw)
        self.client.call = call
        res = self.run_write(pl["items"])
        self.assertEqual(res.items[0]["status"], "ok")
        self.assertEqual(res.items[1]["status"], "refused")
        self.assertEqual(self.wr_calls(), 1)
        self.assertEqual(res.state, "stopped")

    def test_nothing_to_write_is_refused(self):
        self.assertIn("書くもの", self.refused(items=[]))

    def test_unsupported_machine_status_is_not_trusted(self):
        r = O.writable_state({"values": {"run": 0, "motion": 0, "emergency": 0},
                              "text": {}, "interpreted": False}, [], backup_info={
                                  "complete": True, "cnc_id": CNC_ID}, for_params=False)
        self.assertFalse(r["ok"])
        self.assertIn("未確認", "\n".join(r["reasons"]))


class FlakyClient(DirectClient):
    """param_write が時間切れ・接続切れになる機械（試験専用）。"""

    def __init__(self, *a, kind="timeout", reach=False, **kw):
        super().__init__(*a, **kw)
        self.kind, self.reach = kind, reach

    def call(self, cmd, args=None, timeout=None, on_event=None):
        if cmd == "param_write":
            if self.reach:                         # 書いたあとで返事が来なかった
                super().call(cmd, args, timeout, on_event)
            raise C._from_dict({"kind": self.kind, "seconds": 5, "cmd": cmd})
        return super().call(cmd, args, timeout, on_event)


class TestUnknown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.settings = copy.deepcopy(DEFAULTS)

    def tearDown(self):
        self._tmp.cleanup()

    def prepare(self, reach):
        c = FlakyClient({"alarms": [PWE]}, reach=reach).connect()
        res = B.take_backup(c, self.tmp / "控え", self.settings)
        raw = (res.folder / "パラメータ.DAT").read_bytes().decode("cp932")
        types = O._backup_values(res.folder)[1]
        p = P.prepare(raw, {"20": "4"}, axis=4, reference="")
        pl = O.plan_writes(raw, p, types, verified=["byte/none"])
        return c, res, pl

    def run_it(self, c, res, pl):
        return O.run_write(c, pl["items"], cnc_id=CNC_ID, backup_info=res.info,
                           settings=self.settings, family="31i", log_path=self.tmp / "l.csv")

    def test_timeout_is_unknown_then_resolved_by_reading_back_not_written(self):    # 62
        c, res, pl = self.prepare(reach=False)
        out = self.run_it(c, res, pl)
        it = out.items[0]
        self.assertEqual(it["status"], "unknown")
        self.assertEqual(out.state, "stopped")
        self.assertIn("不明", it["note"])
        self.assertTrue(it["touched"])
        self.assertEqual(c.lib.called("cnc_wrparam"), 0)     # 届いていない
        self.assertEqual(O.resolve_unknown(c, it, {"host": "127.0.0.1", "port": 8193},
                                           cnc_id=CNC_ID), ("not_written", "9"))
        self.assertEqual(c.lib.called("cnc_wrparam"), 0)     # 自動でやり直さない

    def test_unknown_and_it_was_written(self):                                        # 62
        c, res, pl = self.prepare(reach=True)
        out = self.run_it(c, res, pl)
        self.assertEqual(out.items[0]["status"], "unknown")
        self.assertEqual(O.resolve_unknown(c, out.items[0], {"host": "127.0.0.1"},
                                           cnc_id=CNC_ID), ("written", "4"))
        self.assertEqual(c.lib.called("cnc_wrparam"), 1)

    def test_unknown_and_neither_value(self):                                         # 62
        c, res, pl = self.prepare(reach=False)
        out = self.run_it(c, res, pl)
        c.machine.set_param(20, "", "77")
        self.assertEqual(O.resolve_unknown(c, out.items[0], {"host": "127.0.0.1"},
                                           cnc_id=CNC_ID)[0], "other")

    def test_resolving_against_another_machine_is_refused(self):
        c, res, pl = self.prepare(reach=False)
        out = self.run_it(c, res, pl)
        c.machine.cnc_id = [9, 9, 9, 9]
        with self.assertRaises(O.WriteRefused):
            O.resolve_unknown(c, out.items[0], {"host": "127.0.0.1"}, cnc_id=CNC_ID)

    def test_lost_connection_is_unknown_too(self):
        c = FlakyClient({"alarms": [PWE]}, kind="lost").connect()
        res = B.take_backup(c, self.tmp / "控え2", self.settings)
        raw = (res.folder / "パラメータ.DAT").read_bytes().decode("cp932")
        p = P.prepare(raw, {"20": "4"}, axis=4, reference="")
        pl = O.plan_writes(raw, p, O._backup_values(res.folder)[1], verified=["byte/none"])
        out = self.run_it(c, res, pl)
        self.assertEqual(out.items[0]["status"], "unknown")


class TestLogsAndJob(WriteBase):
    def read_log(self):
        with open(self.log, encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f))

    def test_write_log_csv_has_one_row_per_item(self):                                # 63
        _p, pl = self.plan({"20": "4", "21": "1"})
        self.run_write(pl["items"], ctx={"unit": "23", "seiban": "T1234"})
        rows = self.read_log()
        self.assertEqual(rows[0], O.LOG_HEADER)
        self.assertEqual(len(rows), 3)
        r = dict(zip(rows[0], rows[1]))
        self.assertEqual((r["CNC ID"], r["号機"], r["製番"], r["番号"], r["旧値"], r["新値"],
                          r["戻り値"], r["読み戻し"]), (CNC_ID, "23", "T1234", "00020", "9", "4", "0", "4"))
        self.assertIn("読み戻しOK", r["結果"])
        self.assertTrue(r["日時"])

    def test_log_records_stopped_and_refused_items_too(self):
        _p, pl = self.plan({"20": "4"})
        self.m.set_param(20, "", "7")
        self.run_write(pl["items"])
        rows = self.read_log()
        self.assertIn("止めた", rows[1][-1])

    def test_log_failure_does_not_stop_the_write(self):
        _p, pl = self.plan({"20": "4"})
        res = O.run_write(self.client, pl["items"], cnc_id=CNC_ID, backup_info=self.info,
                          settings=self.settings, family="31i",
                          log_path=str(self.tmp / "a.txt" / "x" / "log.csv"))
        self.assertEqual(res.state, "done")

    def test_job_file_and_plan_file_are_kept_with_the_backup(self):
        p, pl = self.plan({"20": "4"})
        job = self.folder / O.JOB_NAME
        res = self.run_write(pl["items"], newtext=p["newtext"], job_path=job, ctx={"seiban": "T1"})
        d = json.loads(job.read_text(encoding="utf-8"))
        self.assertEqual(d["cnc_id"], CNC_ID)
        self.assertEqual(d["state"], "done")
        self.assertEqual(d["items"][0]["old"], "9")
        self.assertEqual(d["items"][0]["new"], "4")
        self.assertEqual(d["context"]["seiban"], "T1")
        f = O.save_plan_file(self.folder, p["newtext"])
        data = f.read_bytes()
        self.assertEqual(data, p["newtext"].encode("cp932"))
        self.assertNotIn(b"\r", data)                       # 区切りは LF のまま
        self.assertEqual(res.state, "done")

    def test_backup_folder_keeps_the_same_files_plus_the_new_ones(self):
        # 控えのフォルダに足すのは書込ジョブ.json と 書込後の予定.DAT だけ。
        # F<数字>BASIC の名前ではない（BASIC を拾う仕組みに拾われない）
        import re
        for name in (O.JOB_NAME, O.PLAN_NAME):
            self.assertIsNone(re.match(r"^F\d+BASIC", name, re.I))


if __name__ == "__main__":
    unittest.main()
