# -*- coding: utf-8 -*-
"""かんたん作成を開いたとき、つながる機械を読むだけで見て、モーター（製品/仮）を自動で決める。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import wizard_probe as P                         # noqa: E402
from test_wizard_machine_write import WizardBase               # noqa: E402


def ax(**kw):
    return {int(k[1:]): {"motor_id": v, "amr": "40"} for k, v in kw.items()}


class TestDecide(unittest.TestCase):
    def test_same_axis_means_product(self):
        d = P.decide(ax(a1="293", a2="293", a5="299"), "299")
        self.assertEqual((d["kind"], d["axes"]), ("product", [5]))

    def test_no_same_axis_means_temp_and_number_when_one_odd_axis(self):
        d = P.decide(ax(a1="293", a2="293", a3="293", a5="1234"), "299")
        self.assertEqual((d["kind"], d["axis"], d["temp_id"]), ("temp", 5, "1234"))

    def test_temp_number_left_empty_when_not_certain(self):
        for axes in (ax(a1="293", a2="293", a3="1", a4="2"),      # 違う軸が2つ
                     ax(a1="293", a2="1"),                         # 2軸ではどちらか決められない
                     ax(a1="293", a2="293", a3="293")):            # 全部同じ
            d = P.decide(axes, "299")
            self.assertEqual((d["kind"], d["axis"], d["temp_id"]), ("temp", 0, ""), axes)

    def test_unknown_product_or_unread_machine_decides_nothing(self):
        self.assertEqual(P.decide(ax(a1="293"), "")["kind"], "")
        self.assertEqual(P.decide(ax(a1=""), "299")["kind"], "")
        self.assertEqual(P.decide({}, "299")["kind"], "")

    def test_conn_text_has_only_facts(self):
        pr = {"unit": "23", "axes": ax(a1="293", a2="293", a5="299")}
        self.assertEqual(P.conn_text(pr, lambda k: "XYZAB"[k - 1]),
                         "● 機械につながっています: 23号機　X・Y軸に 293、B軸に 299 が付いています")
        self.assertEqual(P.conn_text({"unit": "23", "axes": {}}, str), "● 機械につながっています: 23号機")


class TestAxisByMatches(unittest.TestCase):
    PROD = {1420: "7771", 1421: "7772", 1422: "7773", 1423: "7774", 2020: "299"}
    BASIC = {1420: ["1.0", "1.0"], 1421: ["1.0"], 1422: ["1.0"], 1423: ["1.0"], 2020: ["255", "293"], 9999: ["5"]}

    def test_pick_numbers_only_axis_specific_and_distinctive(self):
        prod = {**self.PROD, 1424: "1.0", 1425: "9", 2165: "40"}
        basic = {**self.BASIC, 1424: ["1"], 1425: None}      # 1424=BASICと同じ値(数で比べる)・1425=軸別でない
        self.assertEqual(P.pick_numbers(prod, basic), [1420, 1421, 1422, 1423])   # 2020/2165 は対象外

    def test_pick_numbers_has_upper_limit(self):
        prod = {n: "7" for n in range(3000, 3100)}
        basic = {n: ["1"] for n in range(3000, 3100)}
        self.assertEqual(len(P.pick_numbers(prod, basic)), P.MAX_AXIS_NUMBERS)

    def test_one_axis_matching_decides_that_axis(self):
        m = {1: {1420: "1.0"}, 5: {1420: "7771", 1421: "7772", 1422: "7773", 1423: "7774"}, 6: {1420: "7771"}}
        c = P.count_matches(self.PROD, m)
        self.assertEqual(c, {1: 0, 5: 4, 6: 1})
        self.assertEqual(P.decide_axis(c), 5)

    def test_tie_decides_nothing(self):
        row = {1420: "7771", 1421: "7772", 1422: "7773"}
        self.assertEqual(P.decide_axis(P.count_matches(self.PROD, {4: row, 5: dict(row)})), 0)

    def test_no_product_values_decides_nothing(self):
        self.assertEqual(P.count_matches({}, {5: {1420: "7771"}}), {5: 0})
        self.assertEqual(P.decide_axis({5: 0, 6: 0}), 0)
        self.assertEqual(P.decide_axis({}), 0)

    def test_few_or_close_matches_decide_nothing(self):
        self.assertEqual(P.decide_axis({1: 2, 2: 0}), 0)            # 少なすぎる
        self.assertEqual(P.decide_axis({1: 5, 2: 4}), 0)            # 2位との差が小さい
        self.assertEqual(P.decide_axis({1: 5, 2: 3}), 1)

    def test_unreadable_numbers_are_skipped(self):
        m = {5: {1420: "7771", 1421: "7772", 1422: "7773"}, 6: {}}  # 6軸は何も読めなかった
        self.assertEqual(P.count_matches(self.PROD, m), {5: 3, 6: 0})
        self.assertEqual(P.decide_axis(P.count_matches(self.PROD, m)), 5)

    AXES23 = {k: {"motor_id": m, "amr": a} for k, m, a in
              zip(range(1, 7), ("255", "255", "273", "293", "293", "303"), ("25", "25", "45", "85", "85", "165"))}

    def test_capacity_axes_unit_23_400v_40a_is_80a_axes_a_and_b(self):
        r = P.capacity_axes("40A", "400V", "AC200V", self.AXES23)
        self.assertEqual((r["cap"], r["axes"]), ("80A", [4, 5]))
        self.assertIn("400V の 40A", r["note"])
        self.assertIn("200V なので 80A", r["note"])
        self.assertEqual(P.capacity_axes("40A", "200V", "AC200V", self.AXES23)["axes"], [3])

    def test_capacity_axes_unknown_when_no_capacity_or_cannot_convert(self):
        self.assertEqual(P.capacity_axes("", "400V", "AC200V", self.AXES23)["axes"], [])
        self.assertEqual(P.capacity_axes("40A", "", "", self.AXES23)["axes"], [3])
        self.assertIn("電圧が分からない", P.capacity_axes("40A", "", "", self.AXES23)["note"])
        self.assertEqual(P.capacity_axes("45A", "200V", "AC400V", self.AXES23)["axes"], [])

    def test_amr_axes_and_product_amr(self):
        self.assertEqual(P.product_amr({2165: "85", 1420: "5"}), "85")
        self.assertEqual(P.product_amr({"02165": "85.0"}), "85.0")
        self.assertEqual(P.product_amr({1420: "5"}), "")
        self.assertEqual(P.amr_axes("85", self.AXES23), [4, 5])
        self.assertEqual(P.amr_axes("85.0", self.AXES23), [4, 5])
        self.assertEqual(P.amr_axes("", self.AXES23), [])

    def test_narrow_by_motor_id_only_narrows_candidates(self):
        axes = {4: {"motor_id": "293"}, 5: {"motor_id": "300"}}
        self.assertEqual(P.narrow_by_motor_id([4, 5], axes, "300"), ([5], True))
        self.assertEqual(P.narrow_by_motor_id([4, 5], axes, ""), ([4, 5], False))
        self.assertEqual(P.narrow_by_motor_id([4, 5], axes, "999"), ([4, 5], False))
        self.assertEqual(P.narrow_by_motor_id([4], axes, "300"), ([4], False))

    def test_axis_choices_list_axes_with_motor_id(self):
        axes = ax(a1="255", a2="", a5="293")
        self.assertEqual(P.axis_choices(axes, lambda k: "XYZAB"[k - 1]),
                         [("X軸（255）", 1, "255"), ("B軸（293）", 5, "293")])
        self.assertEqual(P.axis_choices(axes, lambda k: "XYZAB"[k - 1], only=[5]), [("B軸（293）", 5, "293")])


class TestWizardAutoProbe(WizardBase):
    def setUp(self):
        super().setUp()
        self.settings["focas_registry_csv"] = str(self.work / "reg.csv")
        self.settings["focas_host"] = "127.0.0.1"

    def make(self, m2020, unit="23", reachable=True):
        """偽の機械（軸ごとの 2020）を読むウィザード。"""
        from fake_client import DirectClient
        from nd287_app import focas_find
        client = DirectClient()
        m = client.machine
        for k, v in m2020.items():
            m.params[(2020, f"A{k}")] = str(v)
        m.types[2020] = m.types[2165]
        cid = "-".join(f"{int(x):08X}" for x in m.cnc_id)
        if unit:
            focas_find.remember(focas_find.registry_path(self.settings), cid, ip="127.0.0.1", unit=unit)
        import nd287_app.gui as G
        w = G.ParamWizardDialog(None, self.settings, probe_machine=True)
        w.warnings = []
        w._probe_client_factory = lambda st: client
        w._probe_port_open = lambda ip, port=8193, timeout=0.3: reachable
        self.addCleanup(w.done, 0)
        return w, client

    def run_probe(self, w):
        w._start_probe()
        w._probe_job.wait(10000)
        self.app.processEvents()

    def search(self, w):
        w.e_seiban.setText("50014984")
        w.search()

    def test_product_motor_chosen_and_shown(self):
        w, client = self.make({1: 293, 2: 293, 3: 299, 4: 293, 5: 293, 6: 293})
        self.run_probe(w)
        self.assertIn("23号機", w.lbl_conn.text())
        self.assertIn("Z軸に 299", w.lbl_conn.text())
        self.assertEqual(client.machine.writes, [])                     # 読むだけ
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "product")
        self.assertIn("自動で決めました", w.lbl_auto.text())
        self.assertIn("違えば直してください", w.lbl_auto.text())
        c, a = w._cand[w.cmb_ctrl.currentData()]
        self.assertEqual((str(c.unit), a), ("23", ["Z"]))

    def test_temp_motor_and_number_from_the_odd_axis(self):
        w, _ = self.make({1: 293, 2: 293, 3: 293, 4: 293, 5: 1234, 6: 293})
        self.run_probe(w)
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "temp")
        self.assertEqual(w.e_motor_id.text(), "1234")

    def test_temp_motor_number_comes_from_capacity_axis_not_from_motor_ids(self):
        # 2020 が3軸ともバラバラでも、製品の容量（200V 40A）に合う軸は Z だけ → その軸の 2020
        w, _ = self.make({1: 293, 2: 7, 3: 8})
        self.run_probe(w)
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "temp")
        self.assertEqual(w.e_motor_id.text(), "8")
        self.assertIn("Z軸", w.lbl_auto.text())

    def test_human_choice_is_not_overwritten(self):
        w, _ = self.make({1: 293, 2: 293, 3: 293, 5: 299})
        self.run_probe(w)
        w.cmb_motor.setCurrentIndex(w.cmb_motor.findData("temp"))
        w.cmb_motor.activated.emit(w.cmb_motor.currentIndex())          # 人が選んだ
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "temp")
        self.assertEqual(w.lbl_auto.text(), "")

    def test_human_edit_of_number_is_kept(self):
        w, _ = self.make({1: 293, 2: 293, 3: 293, 5: 1234})
        self.run_probe(w)
        w.e_motor_id.textEdited.emit("777")
        w.e_motor_id.setText("777")
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "temp")
        self.assertEqual(w.e_motor_id.text(), "777")

    def test_unreachable_machine_is_silent(self):
        w, client = self.make({1: 293}, reachable=False)
        self.run_probe(w)
        self.assertIsNone(w._machine_probe)
        self.assertEqual(client.calls, [])
        self.assertIn("まだつながっていません", w.lbl_conn.text())
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "")
        self.assertEqual(w.lbl_auto.text(), "")

    def test_read_failure_is_silent(self):
        w, client = self.make({1: 293})
        client.call = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        self.run_probe(w)
        self.assertIsNone(w._machine_probe)
        self.assertIn("まだつながっていません", w.lbl_conn.text())

    def test_no_probe_by_default(self):
        w = self.wizard()
        self.assertFalse(w._probe_machine)
        self.assertIsNone(w._probe_job)

    def test_unit_unknown_when_not_in_registry(self):
        w, _ = self.make({1: 293, 5: 299}, unit="")
        self.run_probe(w)
        self.assertIn("台帳にありません", w.lbl_conn.text())

    NUMS = (1420, 1421, 1422, 1423)

    def make_axes(self, b_hits, hv=True):
        """23号機の実測どおり（2020 が 255,255,273,293,293,303）。B軸(5)に製品の値が付いている。
        hv=True は 400V の製品（容量 40A → 200V の機械では 80A → A軸・B軸が候補）。"""
        w, client = self.make({1: 255, 2: 255, 3: 273, 4: 293, 5: 293, 6: 303})
        if hv:
            f = Path(self.settings["param_product_dir"]) / "R50014984.prm"
            f.write_text(f.read_text(encoding="cp932").replace("αiS8/4000-B", "αiS22/3000HV-B"),
                         encoding="cp932")
        m = client.machine
        for k, v in zip(range(1, 7), (25, 25, 45, 85, 85, 165)):          # 23号機の 2165（20,20,40,80,80,160A）
            m.params[(2165, f"A{k}")] = str(v)
        for no in self.NUMS:
            m.types[no] = m.types[2165]
            for k in range(1, 7):
                m.params[(no, f"A{k}")] = "1"
        for no in b_hits:
            m.params[(no, "A5")] = str(7000 + no)
        prod = {no: str(7000 + no) for no in self.NUMS}
        basic = {no: ["1"] for no in self.NUMS}
        w._axis_probe_inputs = lambda f, pr: (prod, basic)
        self.run_probe(w)
        return w, client

    def finish_axis(self, w):
        job = w._axis_job
        if job is not None:
            job.wait(10000)
        self.app.processEvents()

    def test_axis_by_capacity_then_values_for_unit_23(self):
        """400V 40A → 200V なら 80A → 機械の 2165 が 85 の軸は A・B → 製品の値が一致する B軸（293）。"""
        w, client = self.make_axes(self.NUMS)
        self.search(w)
        self.finish_axis(w)
        self.assertEqual(w.cmb_motor.currentData(), "temp")
        self.assertEqual(w.e_motor_id.text(), "293")
        t = w.lbl_auto.text()
        for part in ("400V の 40A", "200V なので 80A", "2165 が 80A の軸は A・B軸", "4 個が一致", "293 が付いているB軸に決めました",
                     "違えば直してください"):
            self.assertIn(part, t)
        self.assertTrue(w.cmb_axis_pick.isHidden())
        self.assertEqual(client.machine.writes, [])                     # 読むだけ
        self.assert_write_axis(w, "23", "B")                            # 書く先も B軸（容量40Aで Z に選び直さない）

    def assert_write_axis(self, w, unit, axis):
        """②の割り当て（＝③で書く先）が、自動で決めた軸になっていること。"""
        self.assertTrue(w._cand)
        c, axes = w._cand[0]
        self.assertEqual((str(c.unit), list(axes)), (unit, [axis]))
        i = w.cmb_ctrl.currentData()
        self.assertTrue(i is not None and i >= 0)
        c2, axes2 = w._cand[i]
        self.assertEqual((str(c2.unit), list(axes2)), (unit, [axis]))

    def test_products_own_2165_has_priority_over_capacity(self):
        """製品データ自身の N2165=85 → 機械の 2165 が 85 の軸 A・B（電圧の読み替えは使わない）→ 値の一致で B。"""
        w, client = self.make_axes(self.NUMS, hv=False)                 # 容量だけなら Z になる製品
        prod = {2165: "85", **{no: str(7000 + no) for no in self.NUMS}}
        w._axis_probe_inputs = lambda f, pr: (prod, {no: ["1"] for no in self.NUMS})
        self.search(w)
        self.finish_axis(w)
        self.assertEqual(w.e_motor_id.text(), "293")
        self.assertIn("製品のアンプ最大電流 85 と同じ軸は A・B軸", w.lbl_auto.text())
        self.assertIn("293 が付いているB軸に決めました", w.lbl_auto.text())

    def test_capacity_alone_decides_when_one_axis_has_it(self):
        w, _ = self.make_axes((), hv=False)                             # 200V 40A → Z軸だけ（273）
        self.search(w)
        self.finish_axis(w)
        self.assertEqual(w.e_motor_id.text(), "273")
        self.assertIn("2165 が 40A の軸は Z軸", w.lbl_auto.text())
        self.assertTrue(w.cmb_axis_pick.isHidden())

    def test_not_decided_shows_choice_of_candidate_axes_only(self):
        w, client = self.make_axes(())                                  # 候補は A・B。どちらにも製品の値が無い
        self.search(w)
        self.finish_axis(w)
        self.assertEqual(w.e_motor_id.text(), "")
        self.assertFalse(w.cmb_axis_pick.isHidden())
        texts = [w.cmb_axis_pick.itemText(i) for i in range(w.cmb_axis_pick.count())]
        self.assertIn("A軸（293）", texts)
        self.assertIn("B軸（293）", texts)
        self.assertFalse(any(t.startswith(("X", "Y", "Z", "C")) for t in texts))     # 全軸は出さない
        self.assertIn("軸を決められませんでした", w.lbl_auto.text())
        i = texts.index("B軸（293）")
        w.cmb_axis_pick.setCurrentIndex(i)
        w.cmb_axis_pick.activated.emit(i)
        self.assertEqual(w.e_motor_id.text(), "293")
        self.assertEqual(client.machine.writes, [])
        self.assert_write_axis(w, "23", "B")                            # 人が選んだ軸が書く先になる

    def test_read_numbers_are_limited_and_unreadable_ones_skipped(self):
        w, client = self.make_axes(self.NUMS)
        got = wizard_probe_read(w, client)
        self.assertEqual(sorted(got), [1, 2, 3, 4, 5, 6])
        self.assertEqual(got[5][1420], "8420")
        self.assertNotIn(99999, got[5])                                 # 機械に無い番号は飛ばす
        self.assertEqual(client.machine.writes, [])

    def test_axis_probe_inputs_from_db_product_and_basic(self):
        w, _ = self.make({1: 293})
        self.run_probe(w)
        prod, basic_axes = w._axis_probe_inputs.__func__(w, {"source": "db", "values": {"1320": "5", ("1", "L2"): "1"}},
                                                         {"unit": "23"})
        self.assertEqual(prod, {"1320": "5"})
        self.assertTrue(len(basic_axes[1320]) >= 6)
        self.assertTrue(all(isinstance(k, int) for k in basic_axes))


def wizard_probe_read(w, client):
    return P.read_axis_params(w.settings, lambda st: client, "127.0.0.1", list(TestWizardAutoProbe.NUMS) + [99999], 6)


if __name__ == "__main__":
    unittest.main()

