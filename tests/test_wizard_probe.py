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

    def test_temp_motor_number_stays_empty_when_not_certain(self):
        w, _ = self.make({1: 293, 2: 7, 3: 8})
        self.run_probe(w)
        self.search(w)
        self.assertEqual(w.cmb_motor.currentData(), "temp")
        self.assertEqual(w.e_motor_id.text(), "")

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


if __name__ == "__main__":
    unittest.main()
