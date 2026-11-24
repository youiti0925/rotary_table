# -*- coding: utf-8 -*-
"""測定プログラムを機械へ直接書く（prog_write・focas_ops.run_program_send）。偽物の機械で試す。

実機で確認したのは 2026-11-22 の LF 区切りの書込・削除だけ。ここは偽物の機械での試験で、
実機の動きの確認ではない。
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nd287_app import fanuc                       # noqa: E402
from nd287_app import focas_backup as B          # noqa: E402
from nd287_app import focas_client as C          # noqa: E402
from nd287_app import focas_ops as O             # noqa: E402
from fake_client import DirectClient             # noqa: E402

FOLDER = "//CNC_MEM/USER/PATH1/"
EOB = fanuc.EOB_PUNCH


def nc_text(n=555):
    return f"%\nO{n:04d}(TEST)\nG90 G00 X0.\nM98 P0901\nM30\n%\n"


def nc(n=555, eob=EOB):
    return fanuc.nc_bytes(nc_text(n), eob)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def machine(self, scenario=None):
        c = DirectClient(scenario or {}).connect()
        self.addCleanup(c.close)
        return c

    def backed(self, c):
        res = B.take_backup(c, self.tmp.name, {}, purpose="測定プログラム書込前")
        self.assertTrue(res.complete, res.problems)
        return res.info, c.call("identify")["cnc_id"]

    def send(self, c, data=None, **kw):
        info, cid = self.backed(c)
        kw.setdefault("approved", True)
        return O.run_program_send(c, data or nc(), EOB, folder=FOLDER, cnc_id=cid, backup_info=info, **kw)


class TestSplit(unittest.TestCase):
    def test_wire_and_expect_keep_every_byte(self):
        data = nc(555)
        p = O.split_programs(data, EOB)[0]
        self.assertEqual(p["number"], 555)
        self.assertTrue(p["wire"].startswith(EOB.encode()) and p["wire"].endswith(b"%"))
        self.assertIn(b"\n\r\r", p["wire"])                       # LF CR CR のまま
        self.assertNotIn(b"\r\n", p["wire"])
        self.assertEqual(p["expect"], b"%" + p["wire"])
        self.assertEqual(p["expect"] + EOB.encode(), data)       # 先頭・末尾の % 以外は 1 バイトも変わらない

    def test_split_by_o_number(self):
        text = "%\nO0001(MAIN)\nM98 P2\nM30\nO0002(SUB)\nM99\n%\n"
        ps = O.split_programs(fanuc.nc_bytes(text, EOB), EOB)
        self.assertEqual([p["number"] for p in ps], [1, 2])
        self.assertNotIn(b"O0002", ps[0]["wire"])

    def test_bad_text_refused(self):
        with self.assertRaises(O.ProgramSplitError):
            O.split_programs(b"G00 X0.\r\r", EOB)


class TestWrite(Base):
    def test_writes_and_reads_back_identical_with_lf_cr_cr(self):
        c = self.machine()
        res = self.send(c)
        self.assertTrue(res["ok"], res)
        it = res["items"][0]
        self.assertEqual(it["match_text"], "完全に一致")
        got = c.machine.programs[FOLDER + "O555"][0]
        self.assertEqual(got + EOB.encode(), nc(555))            # LF CR CR が保たれて機械に入った
        self.assertEqual(c.lib.called("cnc_dwnend4"), 1)
        self.assertEqual(c.lib.called("cnc_pdf_del"), 0)

    def test_buffer_busy_is_retried_and_still_matches(self):
        c = self.machine({"download_buffer_every": 2})
        self.assertTrue(self.send(c)["ok"])

    def test_same_number_stops_without_touching_machine(self):
        c = self.machine()
        before = dict(c.machine.programs)
        with self.assertRaises(O.WriteRefused) as cm:
            self.send(c, nc(901))
        self.assertIn("同じ番号", "".join(cm.exception.reasons))
        self.assertEqual(c.machine.programs, before)
        self.assertEqual(c.lib.called("cnc_pdf_del"), 0)
        self.assertEqual(c.lib.called("cnc_dwnstart4"), 0)

    def test_check_reports_existing_without_writing(self):
        c = self.machine()
        info, cid = self.backed(c)
        chk = O.check_program_send(c, nc(901), EOB, folder=FOLDER, cnc_id=cid, backup_info=info)
        self.assertTrue(chk["overwrite_needed"])
        self.assertTrue(chk["programs"][0]["exists"])
        self.assertEqual(chk["reasons"], [])
        chk = O.check_program_send(c, nc(555), EOB, folder=FOLDER, cnc_id=cid, backup_info=info)
        self.assertFalse(chk["overwrite_needed"])
        self.assertEqual(c.lib.called("cnc_dwnstart4"), 0)

    def test_overwrite_after_approval(self):
        c = self.machine()
        res = self.send(c, nc(901), overwrite=True)
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["items"][0]["deleted"])
        self.assertEqual(c.machine.deleted_programs, [FOLDER + "O901"])
        self.assertEqual(c.machine.programs[FOLDER + "O901"][0] + EOB.encode(), nc(901))
        self.assertIn(FOLDER + "O1000", c.machine.programs)      # 他の1本も消えていない

    def test_helper_does_not_overwrite_by_itself(self):
        c = self.machine()
        import base64
        w = O.split_programs(nc(901), EOB)[0]["wire"]
        r = c.call("prog_write", {"folder": FOLDER, "number": 901,
                                  "data": base64.b64encode(w).decode()})
        self.assertFalse(r["written"])
        self.assertTrue(r["exists"])
        self.assertEqual(c.lib.called("cnc_pdf_del"), 0)

    def test_not_approved_never_writes(self):
        c = self.machine()
        for bad in (False, None, 1, "yes"):
            with self.assertRaises(O.WriteRefused):
                self.send(c, approved=bad)
        self.assertEqual(c.lib.called("cnc_dwnstart4"), 0)
        self.assertNotIn(FOLDER + "O555", c.machine.programs)

    def test_running_or_emergency_refused(self):
        for st in ({"run": 3}, {"emergency": 1}, {"motion": 1}):
            c = self.machine({"status": st})
            with self.assertRaises(O.WriteRefused):
                self.send(c)
            self.assertEqual(c.lib.called("cnc_dwnstart4"), 0, st)

    def test_helper_itself_refuses_when_running(self):
        c = self.machine()
        import base64
        w = O.split_programs(nc(555), EOB)[0]["wire"]
        c.machine.status["run"] = 3
        with self.assertRaises(C.FocasError) as cm:
            c.call("prog_write", {"folder": FOLDER, "number": 555, "data": base64.b64encode(w).decode()})
        self.assertEqual(cm.exception.kind, "refused")
        self.assertEqual(c.lib.called("cnc_dwnstart4"), 0)

    def test_alarm_refused(self):
        c = self.machine({"alarms": [[1, 6, 0, "SERVO"]]})
        with self.assertRaises(O.WriteRefused):
            self.send(c)

    def test_no_backup_refused(self):
        c = self.machine()
        with self.assertRaises(O.WriteRefused):
            O.run_program_send(c, nc(), EOB, approved=True, folder=FOLDER, cnc_id="x", backup_info=None)

    def test_capacity_checks(self):
        c = self.machine({"remaining": 0})
        with self.assertRaises(O.WriteRefused) as cm:
            self.send(c)
        self.assertIn("残り本数", "".join(cm.exception.reasons))

    def test_dwnstart_failure_does_not_download(self):
        c = self.machine({"dwn_rc": 7})
        res = self.send(c)
        self.assertFalse(res["ok"])
        self.assertEqual(c.lib.called("cnc_download4"), 0)
        self.assertIn("書けませんでした", res["items"][0]["note"])

    def test_download_failure_still_ends(self):
        c = self.machine({"download_rc": 5})
        res = self.send(c)
        self.assertFalse(res["ok"])
        self.assertEqual(c.lib.called("cnc_dwnend4"), 1)

    def test_mismatch_is_reported(self):
        c = self.machine()
        orig = c.machine.add_program

        def tamper(path, data, comment=""):
            orig(path, data.replace(b"G90", b"G91"), comment)
        c.machine.add_program = tamper
        res = self.send(c)
        self.assertFalse(res["ok"])
        self.assertIn("違います", res["items"][0]["match_text"])

    def test_two_programs_written_one_by_one(self):
        text = "%\nO0555(MAIN)\nM98 P556\nM30\nO0556(SUB)\nM99\n%\n"
        c = self.machine()
        res = self.send(c, fanuc.nc_bytes(text, EOB))
        self.assertTrue(res["ok"], res)
        self.assertEqual(c.lib.called("cnc_dwnstart4"), 2)
        self.assertIn(FOLDER + "O556", c.machine.programs)

    def test_backup_runs_before_and_saves_existing_program(self):
        c = self.machine()
        info, _ = self.backed(c)
        self.assertGreater(info["programs"]["saved"], 0)
        self.assertIn(FOLDER + "O901", info["programs"]["paths"])


if __name__ == "__main__":
    unittest.main()
