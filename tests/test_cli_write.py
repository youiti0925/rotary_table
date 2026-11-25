# -*- coding: utf-8 -*-
"""機械へ書く、画面なしの入口（cli_write）。偽物の機械へ直接つなぐ。実機の確認ではない。

朝の件（2026-11-25）: 入口が無く、その場でスクリプトを作って25分かかった。ここでは
 ・plan は機械に書かない  ・承認コードが無ければ1件も書かない  ・型の確認が通ってから本番を書く
 ・1行ずつ進み具合が出る  ・止まったら理由が出る  を確かめる。
"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import cli_write as W            # noqa: E402
from nd287_app import focas_ops as O            # noqa: E402
from nd287_app import param_build as P          # noqa: E402
from nd287_app.settings import DEFAULTS         # noqa: E402
from fake_client import DirectClient            # noqa: E402

PWE = [100, 0, 0, "ﾊﾟﾗﾒｰﾀ書き込み可能です"]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings["focas_backup_dir"] = str(self.tmp / "控え")
        self.settings["focas_verified_kinds"] = {"31i": ["byte/none"]}
        self.lines = []
        self.emit = W.Progress(self.tmp / "進み具合", echo=False)
        self.client = DirectClient({"alarms": [PWE], "status": {"emergency": 1}}).connect()
        self.values = {"20": "4", "21": "1", "1420": "8000.000"}

    def tearDown(self):
        self._tmp.cleanup()

    def plan_fn(self, raw, zero_scope="product", keep_motor=False, axis=None):
        return P.prepare(raw, self.values, axis=axis or 4, reference="", zero_scope=zero_scope)

    def plan(self):
        return W.plan_job(self.client, self.settings, self.plan_fn, seiban="50014861", axis=4,
                          keep_motor=True, unit="23", host="127.0.0.1", port=8193, emit=self.emit,
                          state_dir=self.tmp / "進み具合")

    def log(self):
        return self.emit.path.read_text(encoding="utf-8")


class TestPlan(Base):
    def test_plan_writes_nothing_and_gives_a_code(self):
        job = self.plan()
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)           # 機械に書いていない
        self.assertEqual(len(job["items"]), 3)
        self.assertEqual(len(job["code"]), 8)
        self.assertTrue(Path(job["backup_folder"], W.JOB_FILE).is_file())
        self.assertIn("承認コード " + job["code"], self.log())
        self.assertIn("N00020", self.log())                                    # 1件ずつ一覧が出る

    def test_plan_cannot_call_write_commands(self):
        ro = W.ReadOnlyClient(self.client)
        with self.assertRaises(W.CliError):
            ro.call("param_write", {"no": 20, "value": "1"})
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)

    def test_nothing_to_write_stops_with_a_reason(self):
        self.values = {}
        with self.assertRaises(W.CliError) as cm:
            self.plan()
        self.assertIn("書くものがありません", str(cm.exception))

    def test_code_changes_when_the_list_changes(self):
        a = self.plan()
        self.values = {"20": "4", "21": "1", "1420": "8001.000"}
        b = self.plan()
        self.assertNotEqual(a["code"], b["code"])

    def test_axis_names(self):
        self.assertEqual(W.axis_number("Z"), 3)
        self.assertEqual(W.axis_number("b"), 5)
        self.assertEqual(W.axis_number("4"), 4)
        with self.assertRaises(W.CliError):
            W.axis_number("Q")


class TestWrite(Base):
    def test_wrong_code_writes_nothing(self):
        job = self.plan()
        with self.assertRaises(W.CliError):
            W.write_job(self.client, self.settings, job, "ZZZZZZZZ", self.emit)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)

    def test_tampered_list_writes_nothing(self):
        job = self.plan()
        job["items"][0]["new"] = "9"                       # 一覧を書き換えた
        with self.assertRaises(W.CliError):
            W.write_job(self.client, self.settings, job, job["code"], self.emit)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)

    def test_write_verifies_kinds_first_then_writes_and_reports_each_line(self):
        job = self.plan()
        self.assertTrue(job["verify_keys"])                 # この機種で初めての型がある
        res = W.write_job(self.client, self.settings, job, job["code"].lower(), self.emit,
                          save=lambda s: None, sleep=lambda s: None)
        self.assertTrue(all(it["status"] == "ok" for it in res.items))
        log = self.log()
        self.assertLess(log.index("[型の確認]"), log.index("[書込] 1/3"))   # 型の確認が先
        self.assertIn("[書込] 3/3", log)
        self.assertIn("3/3 件が、書けて読み戻しも合いました", log)
        m = self.client.machine
        self.assertEqual(self.client.call("param_read", {"no": 20, "label": ""})["value"], "4")

    def test_not_ready_waits_then_stops_without_writing(self):
        job = self.plan()
        self.client.machine.status["emergency"] = 0     # 非常停止が解除された
        with self.assertRaises(W.CliError) as cm:
            W.write_job(self.client, self.settings, job, job["code"], self.emit, wait=0,
                        sleep=lambda s: None)
        self.assertIn("書ける状態になりませんでした", str(cm.exception))
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)
        self.assertIn("[待ち]", self.log())

    def test_other_machine_is_refused(self):
        job = self.plan()
        job["cnc_id"] = "OTHER"
        job["code"] = W.approval_code("OTHER", job["items"], job["verify_keys"])
        with self.assertRaises(W.CliError):
            W.write_job(self.client, self.settings, job, job["code"], self.emit)
        self.assertEqual(self.client.lib.called("cnc_wrparam"), 0)

    def test_check_reads_back_without_writing(self):
        job = self.plan()
        W.write_job(self.client, self.settings, job, job["code"], self.emit, save=lambda s: None,
                    sleep=lambda s: None)
        n = self.client.lib.called("cnc_wrparam")
        self.assertTrue(W.check_job(self.client, self.settings, job, "127.0.0.1", 8193, self.emit))
        self.assertEqual(self.client.lib.called("cnc_wrparam"), n)       # 読むだけ
        self.assertIn("[読み戻し]", self.log())

    def test_status_shows_the_last_lines(self):
        self.plan()
        import nd287_app.cli_write as M
        M.progress_dir = lambda settings=None: self.tmp / "進み具合"
        lines = M.last_lines()
        self.assertTrue(lines)
        self.assertIn("承認", lines[-1])


if __name__ == "__main__":
    unittest.main()


class TestBuildPlanFn(unittest.TestCase):
    """画面なしで、かんたん作成と同じ plan_fn を取る（作る中身は画面と同じ）。"""

    def test_same_plan_fn_as_the_wizard(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_wizard_machine_write as T
        T.WizardBase.setUpClass()
        try:
            b = T.WizardBase("setUp")
            b.setUp()
            raw = (T.HERE / "fixtures" / "focas_param_31i.DAT").read_text(encoding="cp932")
            plan_fn, kw = W.build_plan_fn(b.settings, "50014984", "23", "product")
            nums = {str(r[0]) for r in plan_fn(raw)["rows"]}
            self.assertTrue({"2020", "2022"} <= nums, nums)
            self.assertEqual(kw["expect_unit"], "23")
            plan_fn, kw = W.build_plan_fn(b.settings, "50014984", "23", "temp")
            self.assertTrue(kw["keep_motor"])
            with self.assertRaises(W.CliError):
                W.build_plan_fn(b.settings, "99999999", "23", "product")
            with self.assertRaises(W.CliError):
                W.build_plan_fn(b.settings, "50014984", "99", "product")
        finally:
            T.WizardBase.tearDownClass()
