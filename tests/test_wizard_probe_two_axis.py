# -*- coding: utf-8 -*-
"""2軸テーブル（傾斜 T ＋ 回転 R の2製品）でも、かんたん作成を開いたときの機械の読み取り（読むだけ）で
製品ごとに軸・モーターを決める。軸は機械の N2165 で決める（2020 では決めない）。2つが同じ軸にならない。"""
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import wizard_probe as P                         # noqa: E402
from test_wizard_machine_write import WizardBase, PRODUCT       # noqa: E402

TN = (1420, 1421, 1422, 1423)          # 傾斜の製品らしい値の番号
RN = (1430, 1431, 1432, 1433)          # 回転の製品らしい値の番号
LET = "XYZABC"


class TestResolveDistinct(unittest.TestCase):
    def test_decided_axis_is_removed_from_the_other(self):
        dec, rest, conf = P.resolve_distinct([[4, 5], [4, 5]], [4, 0])
        self.assertEqual((dec, conf), ([4, 5], []))
        self.assertEqual(P.resolve_distinct([[3], [4, 5]])[0], [3, 0])      # 片方だけ決まる
        self.assertEqual(P.resolve_distinct([[3], [3, 4]])[0], [3, 4])      # 決まった軸は他方から除く → 他方も決まる

    def test_same_only_axis_is_a_conflict_not_decided(self):
        dec, rest, conf = P.resolve_distinct([[3], [3]])
        self.assertEqual((dec, conf), ([0, 0], [0, 1]))
        dec, _r, conf = P.resolve_distinct([[], []], [3, 3])                 # 最初から同じ軸
        self.assertEqual((dec, conf), ([0, 0], [0, 1]))

    def test_two_candidates_each_stay_undecided(self):
        dec, rest, conf = P.resolve_distinct([[4, 5], [4, 5]])
        self.assertEqual((dec, rest, conf), ([0, 0], [[4, 5], [4, 5]], []))

    def test_product_motor_ids(self):
        fs = [{"meta": {"motor_id": "255"}}, {"meta": {}, "values": {"2020": "0"}}]
        self.assertEqual(P.product_motor_ids(fs), ["255", ""])


class TwoAxisBase(WizardBase):
    def setUp(self):
        super().setUp()
        self.settings["focas_registry_csv"] = str(self.work / "reg.csv")
        self.settings["focas_host"] = "127.0.0.1"

    def make(self, t_hv, r_hv, t_hits=None, r_hits=None, t2020="250", r2020="299", t_model=None, r_model=None):
        """23号機の実測（2165 が 25,25,45,85,85,165・2020 が 255,255,273,293,293,303）の偽の機械と、T+R の2製品。
        t_hits / r_hits: 製品らしい値が機械で一致する軸のリスト（軸番号）。hv=True は 400V 40A（→ 200V の機械では 80A＝A・B軸）、
        False は 200V 40A（＝Z軸だけ）。"""
        from fake_client import DirectClient
        from nd287_app import focas_find
        data = Path(self.settings["param_product_dir"])
        r = PRODUCT.replace('"2020","A5","","","299"', f'"2020","A5","","","{r2020}"')
        if r_hv:
            r = r.replace("αiS8/4000-B", "αiS22/3000HV-B")
        if r_model:
            r = r.replace("αiS8/4000-B", r_model)
        (data / "R50014984.prm").write_text(r, encoding="cp932")
        t = (PRODUCT.replace("Axis=R", "Axis=T").replace('"2020","A5","","","299"', f'"2020","A5","","","{t2020}"'))
        if t_hv:
            t = t.replace("αiS8/4000-B", "αiS22/3000HV-B")
        if t_model:
            t = t.replace("αiS8/4000-B", t_model)
        (data / "T50014984.prm").write_text(t, encoding="cp932")
        client = DirectClient()
        m = client.machine
        for k, v in zip(range(1, 7), (255, 255, 273, 293, 293, 303)):
            m.params[(2020, f"A{k}")] = str(v)
        m.types[2020] = m.types[2165]
        for k, v in zip(range(1, 7), (25, 25, 45, 85, 85, 165)):
            m.params[(2165, f"A{k}")] = str(v)
        for no in TN + RN:
            m.types[no] = m.types[2165]
            for k in range(1, 7):
                m.params[(no, f"A{k}")] = "1"
        for k in t_hits or ():
            for no in TN:
                m.params[(no, f"A{k}")] = str(6000 + no)
        for k in r_hits or ():
            for no in RN:
                m.params[(no, f"A{k}")] = str(7000 + no)
        cid = "-".join(f"{int(x):08X}" for x in m.cnc_id)
        focas_find.remember(focas_find.registry_path(self.settings), cid, ip="127.0.0.1", unit="23")
        import nd287_app.gui as G
        w = G.ParamWizardDialog(None, self.settings, probe_machine=True)
        w.warnings = []
        w._probe_client_factory = lambda st: client
        w._probe_port_open = lambda ip, port=8193, timeout=0.3: True
        self.addCleanup(w.done, 0)
        prods = {"傾斜": {no: str(6000 + no) for no in TN}, "回転": {no: str(7000 + no) for no in RN}}
        basic = {no: ["1"] for no in TN + RN}
        w._axis_probe_inputs = lambda f, pr: (prods[f["kind"]], basic)
        w._start_probe()
        w._probe_job.wait(10000)
        self.app.processEvents()
        return w, client

    def search(self, w):
        w.e_seiban.setText("50014984")
        w.search()
        self.assertEqual(sorted(f["kind"] for f in w._selected_files()), ["傾斜", "回転"])
        for _ in range(10):                                   # 製品ごとに順に読む（終わるまで待つ）
            mx = w._mx
            if not (mx and mx["busy"]):
                break
            for j in list(w._axis_jobs):
                j.wait(10000)
            self.app.processEvents()
        self.app.processEvents()

    def cur(self, w):
        c, asg = w._cand[w.cmb_ctrl.currentData()]
        return str(c.unit), list(asg)

    def combo_texts(self, cmb):
        return [cmb.itemText(i) for i in range(cmb.count())]


class TestTwoAxisAuto(TwoAxisBase):
    def test_both_decided_by_2165_and_values_and_written_to_both_axes(self):
        """傾斜 200V 40A → Z軸だけ（2165=45）。回転 400V 40A → 80A → A・B軸 → 値が一致する B軸。"""
        w, client = self.make(t_hv=False, r_hv=True, t_hits=(), r_hits=(5,))
        self.search(w)
        self.assertEqual(w._decided_axes, {"傾斜": "Z", "回転": "B"})
        self.assertEqual(self.cur(w), ("23", ["Z", "B"]))       # 傾斜→Z・回転→B（②の割当＝③で書く先）
        t = w.lbl_auto.text()
        for part in ("傾斜: ", "回転: ", "Z軸に決めました", "B軸に決めました", "400V の 40A", "200V なので 80A",
                     "2165 が 80A の軸は A・B軸", "4 個が一致", "違えば直してください"):
            self.assertIn(part, t)
        self.assertLess(t.index("傾斜: "), t.index("回転: "))
        self.assertEqual((w.e_motor_id.text(), w.e_motor_id2.text()), ("273", "293"))     # 製品ごとの仮モーター番号
        self.assertTrue(w.cmb_axis_pick.isHidden() and w.cmb_axis_pick2.isHidden())
        self.assertFalse(w.e_motor_id2.isHidden())
        self.assertEqual(client.machine.writes, [])             # 読むだけ
        # ③ 機械へ直接書く窓に、両方の軸が入る
        w.e_out.setText("")
        self.run_machine(w)
        kw = self.opened[0][1]
        self.assertEqual(sorted(kw["product_axes"]), [3, 5])
        self.assertTrue(kw["keep_motor"])
        self.assertEqual(kw["motor_id"], "")                    # 1つの番号で2軸を照合しない

    def test_two_products_never_get_the_same_axis(self):
        """どちらも A・B 候補で、回転の値は A にも B にも付いている（単独なら決められない）→ 傾斜が A に決まったので回転は B。"""
        w, _ = self.make(t_hv=True, r_hv=True, t_hits=(4,), r_hits=(4, 5))
        self.search(w)
        self.assertEqual(w._decided_axes, {"傾斜": "A", "回転": "B"})
        self.assertEqual(self.cur(w), ("23", ["A", "B"]))
        self.assertNotEqual(*w._decided_axes.values())

    def test_undecided_product_is_assigned_automatically_and_change_lists_same_capacity_axes(self):
        w, client = self.make(t_hv=False, r_hv=True)            # 回転は A・B のどちらにも製品の値が無い → 人に選ばせず最後の B
        self.search(w)
        self.assertEqual(w._decided_axes, {"傾斜": "Z", "回転": "A"})        # 回転は A・B のどちらにも値の手がかりが無い → 若い A
        self.assertEqual(self.cur(w), ("23", ["Z", "A"]))
        self.assertEqual(w.e_motor_id2.text(), "293")
        self.assertIn("最も小さい", w.lbl_auto.text())
        w._step = 2
        w._apply_mode()
        self.assertTrue(w.cmb_ctrl.isHidden())
        self.assertEqual(w.lbl_pick.text().splitlines()[0], "使う機械：23号機　傾斜: Z軸／回転: A軸　自動で決めました")
        self.assertIn("理由:", w.lbl_pick.text())
        self.assertTrue(w.cmb_axis_pick.isHidden() and w.cmb_axis_pick2.isHidden())      # ［変える］まで出さない
        w.b_change.click()
        self.assertFalse(w.cmb_axis_pick2.isHidden())
        texts = self.combo_texts(w.cmb_axis_pick2)
        self.assertEqual([t for t in texts if "軸（" in t], ["A軸（293）", "B軸（293）"])         # 同じ容量の軸だけ
        i = texts.index("B軸（293）")
        w.cmb_axis_pick2.setCurrentIndex(i)
        w.cmb_axis_pick2.activated.emit(i)
        self.search_wait(w)
        self.assertEqual(w._decided_axes, {"傾斜": "Z", "回転": "B"})
        self.assertEqual(self.cur(w), ("23", ["Z", "B"]))       # 人が選んだ軸が書く先
        self.assertEqual(w.e_motor_id2.text(), "293")
        self.assertEqual(client.machine.writes, [])

    def search_wait(self, w):
        for _ in range(10):
            mx = w._mx
            if not (mx and mx["busy"]):
                break
            for j in list(w._axis_jobs):
                j.wait(10000)
            self.app.processEvents()
        self.app.processEvents()

    def test_same_capacity_axes_are_assigned_from_the_lowest_axis_up(self):
        w, _ = self.make(t_hv=True, r_hv=True)                  # 両方 A・B 候補で値の手がかりが無い → 若い順に仮決め
        self.search(w)
        self.assertEqual(w._decided_axes, {"傾斜": "A", "回転": "B"})
        self.assertEqual(self.cur(w), ("23", ["A", "B"]))
        self.assertNotEqual(*w._decided_axes.values())          # 同じ軸は2回使わない
        self.assertIn("番号が最も小さい（若い）軸から割り当てました", w.lbl_auto.text())

    def test_cannot_pick_the_axis_the_other_product_uses(self):
        w, _ = self.make(t_hv=False, r_hv=True)
        self.search(w)                                          # 傾斜は Z。回転は A・B から自動で B
        w.cmb_axis_pick2.addItem("Z軸（273）", 3)               # 無理に出しても、同じ軸にはならない
        w.cmb_axis_pick2.setCurrentIndex(w.cmb_axis_pick2.count() - 1)
        w._pick_axis_multi(1)
        self.assertEqual(w._decided_axes, {"傾斜": "Z", "回転": "A"})

    def test_one_product_only_keeps_the_existing_behaviour(self):
        w, client = self.make(t_hv=False, r_hv=True, r_hits=(5,))
        w.e_seiban.setText("50014984")
        w.search()
        for f in w._files:                                      # 回転だけにする
            f["chk"].setChecked(f["kind"] == "回転")
        self.search_wait(w)
        w._auto_key = None
        w._apply_probe()
        self.search_wait(w)
        for j in list(w._axis_jobs) + [w._axis_job]:
            if j is not None:
                j.wait(10000)
        self.app.processEvents()
        self.assertIsNone(w._mx)
        self.assertEqual(w._decided_axes, {})
        self.assertTrue(w.e_motor_id2.isHidden())
        self.assertEqual(w.e_motor_id.text(), "293")            # 1製品: 容量(A・B)→値の一致で B（従来どおり）
        self.assertEqual(self.cur(w), ("23", ["B"]))
        self.assertEqual(client.machine.writes, [])

    def test_one_product_temp_one_product_motor_drops_2020_only_on_the_temp_axis(self):
        """傾斜の 2020 が機械の Z軸（273）と同じ → 製品のモーター。回転は付いていない番号 → 仮モーター。"""
        w, _ = self.make(t_hv=False, r_hv=True, r_hits=(5,), t2020="273")
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "temp")
        self.assertEqual(w._motor_kind, {"傾斜": "product", "回転": "temp"})
        self.assertEqual(w._decided_axes, {"傾斜": "Z", "回転": "B"})
        self.assertEqual(w.e_motor_id.text(), "")                # 製品のモーターの方には仮モーターの番号を入れない
        self.assertEqual(w.e_motor_id2.text(), "293")
        w.e_out.setText("")
        self.run_machine(w)
        plan_fn = self.opened[0][0][2]
        raw = (Path(__file__).resolve().parent / "fixtures" / "focas_param_31i.DAT").read_text(encoding="cp932")
        full = plan_fn(raw)
        kept = plan_fn(raw, keep_motor=True)
        on = lambda plan: {(str(r[0]), r[1]) for r in plan["rows"] if r[5] and str(r[0]) == "2020"}      # noqa: E731
        self.assertEqual({a for _n, a in on(full)}, {3, 5})
        self.assertEqual({a for _n, a in on(kept)}, {3})     # 回転（B軸）の 2020 だけ書かない

    def test_human_choice_of_motor_is_not_overwritten(self):
        w, _ = self.make(t_hv=False, r_hv=True, r_hits=(5,))
        w.cmb_motor.setCurrentIndex(w.cmb_motor.findData("product"))
        w.cmb_motor.activated.emit(w.cmb_motor.currentIndex())
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "product")
        self.assertEqual(w._motor_kind, {})
        self.assertEqual(w._decided_axes, {"傾斜": "Z", "回転": "B"})   # 軸は 2165 で決まる（モーターの選び方とは別）

    def test_unreachable_machine_is_silent(self):
        w, _ = self.make(t_hv=False, r_hv=True)
        w._machine_probe = None
        w._auto_key = None
        self.search(w)
        self.assertEqual(w._decided_axes, {})
        self.assertIsNone(w._mx)


class TestTwoAxisSameCapacity(TwoAxisBase):
    def test_both_20a_get_x_and_y_and_both_are_temp_motors(self):
        """受注 50014905 相当: 傾斜も回転も αiF4/5000（2020=273・20A・200V）。23号機の 20A は X・Y（2020 は 255・255）。"""
        w, client = self.make(t_hv=False, r_hv=False, t_model="αiF4/5000", r_model="αiF4/5000",
                              t2020="273", r2020="273")
        self.search(w)
        self.assertEqual(w._decided_axes, {"傾斜": "X", "回転": "Y"})      # 同じ容量が X・Y → 若い順
        self.assertEqual(self.cur(w), ("23", ["X", "Y"]))
        self.assertEqual(w.cmb_motor.currentData(), "temp")           # X・Y の 2020 は 255 で製品の 273 と違う
        self.assertEqual(w._motor_kind, {})                           # 両方仮モーター（製品ごとの区別は要らない）
        t = w.lbl_auto.text()
        self.assertNotIn("決めていません", t)
        self.assertNotIn("Z軸", t)                                    # 2020 の一致で Z(273) を候補にしない
        for part in ("傾斜: ", "回転: ", "X軸に決めました", "Y軸に決めました", "仮モーター", "2165 が 20A の軸は X・Y軸"):
            self.assertIn(part, t)
        self.assertEqual((w.e_motor_id.text(), w.e_motor_id2.text()), ("255", "255"))
        self.assertEqual(client.machine.writes, [])
        w.e_out.setText("")
        self.run_machine(w)
        self.assertEqual(sorted(self.opened[0][1]["product_axes"]), [1, 2])

    def test_two_40a_do_not_fit_unit_23(self):
        """受注 50014962 相当: 400V 20A → 200V の機械では 40A が2つ要る。23号機の 40A は Z だけ。"""
        w, client = self.make(t_hv=False, r_hv=False, t_model="αiF12/4000HV-B", r_model="αiS8/4000HV-B")
        self.search(w)
        self.assertEqual(w._decided_axes, {})
        self.assertIn("23号機には2軸とも載りません（40A の軸が1つしかありません）", w.lbl_auto.text())
        self.assertTrue(w.cmb_axis_pick.isHidden() and w.cmb_axis_pick2.isHidden())
        self.assertEqual(client.machine.writes, [])


class TestTwoAxisSemi(TwoAxisBase):
    def test_semi_results_use_each_products_own_axis(self):
        w, _ = self.make(t_hv=False, r_hv=True, r_hits=(5,))
        self.search(w)
        seen = []

        def fake(f, raw, axnum, vals=None):
            seen.append((f["kind"], axnum))
            return {"mode": "フル" if f["kind"] == "回転" else "セミ", "stop": [], "values": {}, "changes": [],
                    "skipped": [], "basis": "", "source": "計算式"}
        with mock.patch.object(w, "_semi_convert_file", fake):
            res = w._semi_results()
        self.assertEqual(sorted(seen), [("傾斜", 3), ("回転", 5)])        # 傾斜→Z(3)・回転→B(5)。片方だけフルでもよい
        self.assertEqual([f["kind"] for f, _r in res], ["回転"])


if __name__ == "__main__":
    unittest.main()
