# -*- coding: utf-8 -*-
"""focas_ops（書ける状態の判定・自己診断・診断）。偽物の機械へ直接つなぐ。実機にはつながない。
書込の流れ（段4以降）はまだ無い。"""

import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import focas_backup as B            # noqa: E402
from nd287_app import focas_client as C            # noqa: E402
from nd287_app import focas_ops as O               # noqa: E402
from nd287_app.settings import DEFAULTS            # noqa: E402
from fake_client import DirectClient               # noqa: E402

CNC_ID = "3C7B5D01-49C1F435-5E28C389-6592C486"
GOOD_STATUS = {"values": {"run": 0, "motion": 0, "emergency": 0, "aut": 0, "alarm": 1},
               "text": {"run": "リセット", "motion": "なし", "emergency": "なし", "aut": "MDI",
                        "alarm": "アラーム"}}
SW0100 = {"code": "SW0100", "text": "ﾊﾟﾗﾒｰﾀ書き込み可能です", "no": 100, "type": 0}
OK_BACKUP = {"complete": True, "cnc_id": CNC_ID}


def status(**kw):
    s = copy.deepcopy(GOOD_STATUS)
    s["values"].update(kw)
    return s



def setUpModule():
    # この試験は「非常停止を解除した機械」を前提に書かれている。新しい既定（非常停止のときだけ書く）は test_estop_mode で確かめる
    from nd287_app import focas_ops as _fo
    _fo.set_estop_mode("released_only")


def tearDownModule():
    from nd287_app import focas_ops as _fo
    _fo.set_estop_mode("estop_only")


class TestWritableState(unittest.TestCase):
    def ws(self, st=GOOD_STATUS, alarms=(SW0100,), backup=OK_BACKUP, **kw):
        return O.writable_state(st, list(alarms), backup_info=backup, cnc_id=CNC_ID, **kw)

    def test_reset_and_no_alarms_but_pwe_is_ok_for_programs(self):
        r = self.ws(alarms=(), for_params=False)
        self.assertTrue(r["ok"], r)

    def test_sw0100_only_is_ok_for_parameters(self):
        r = self.ws()
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["pwe"])

    def test_running_paused_moving_emergency_are_refused_and_all_reasons_listed(self):
        for kw in ({"run": 3}, {"run": 2}, {"run": 1}, {"motion": 1}, {"emergency": 1}):
            r = self.ws(status(**kw))
            self.assertFalse(r["ok"], kw)
        r = self.ws(status(run=3, motion=1, emergency=1))
        text = "\n".join(r["reasons"])
        self.assertEqual(len(r["reasons"]), 3)
        self.assertIn("非常停止", text)
        self.assertIn("軸が動いています", text)

    def test_other_alarm_with_sw0100_is_refused(self):
        r = self.ws(alarms=(SW0100, {"code": "SV0401", "text": "サーボ"}))
        self.assertFalse(r["ok"])
        self.assertIn("SV0401", "\n".join(r["reasons"]))

    def test_no_pwe_blocks_parameters_with_guidance_but_not_programs(self):
        r = self.ws(alarms=())
        self.assertFalse(r["ok"])
        self.assertIn("パラメータ書込", "\n".join(r["reasons"]))
        self.assertIn("触りません", "\n".join(r["reasons"]))
        self.assertTrue(self.ws(alarms=(), for_params=False)["ok"])

    def test_allowed_alarms_are_configurable(self):
        r = self.ws(alarms=(SW0100, {"code": "OT0510", "text": "x"}), allowed_alarms=("SW0100", "OT0510"))
        self.assertTrue(r["ok"], r)

    def test_backup_missing_incomplete_or_of_another_machine(self):
        self.assertFalse(self.ws(backup=None)["ok"])
        self.assertFalse(self.ws(backup={"complete": False, "cnc_id": CNC_ID})["ok"])
        r = self.ws(backup={"complete": True, "cnc_id": "FFFFFFFF-0-0-0"})
        self.assertFalse(r["ok"])
        self.assertIn("別の機械", "\n".join(r["reasons"]))

    def test_unread_status_is_refused(self):
        self.assertFalse(O.writable_state(None, [], backup_info=OK_BACKUP)["ok"])


class TestSelfCheck(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.settings = copy.deepcopy(DEFAULTS)

    def tearDown(self):
        self._tmp.cleanup()

    def backed_up(self, scenario=None):
        c = DirectClient(scenario).connect()
        r = B.take_backup(c, self.root, self.settings)
        return c, r.folder

    def test_without_backup_it_still_reads_and_says_it_could_not_compare(self):
        c = DirectClient().connect()
        out = O.self_check(c)
        titles = " ".join(l["title"] for l in out)
        self.assertIn("機種", titles)
        self.assertIn("状態の数値", titles)
        self.assertIn("アラーム", titles)
        self.assertTrue(any(l["level"] == "warn" and "A1" in l["title"] for l in out))
        self.assertFalse(O.report_has_ng(out))

    def test_all_read_commands_only(self):
        c, folder = self.backed_up()
        before = len(c.calls)
        O.self_check(c, folder)
        used = set(c.calls[before:])
        self.assertTrue(used <= {"identify", "status", "alarms", "prog_info", "param_info",
                                 "param_read_many", "param_read"}, used)
        for bad in ("cnc_wrparam", "cnc_dwnstart4", "cnc_pdf_del", "cnc_start"):
            self.assertEqual(c.lib.called(bad), 0)

    def test_values_match_the_backup(self):
        c, folder = self.backed_up()
        out = O.self_check(c, folder)
        a1 = next(l for l in out if "A1" in l["title"])
        self.assertEqual(a1["level"], "ok", a1)
        self.assertIn("一致", a1["title"])
        self.assertIn("違う 0", a1["title"])
        self.assertFalse(O.report_has_ng(out))

    def test_full_mode_reads_every_number_in_the_backup(self):
        c, folder = self.backed_up()
        got = []
        out = O.self_check(c, folder, full=True, progress=lambda d, t, n: got.append((d, t)))
        a1 = next(l for l in out if "A1" in l["title"])
        self.assertIn("全部", a1["title"])
        self.assertEqual(a1["level"], "ok", a1)
        self.assertEqual(got[-1][0], got[-1][1])
        n_read = c.lib.called("cnc_rdparam")
        self.assertGreater(n_read, 100)            # A1〜A12 を含めて読んでいる

    def test_a_value_that_differs_is_reported_as_ng_with_both_values(self):
        c, folder = self.backed_up()
        c.machine.params[(1420, "A4")] = "12345.0"
        out = O.self_check(c, folder)
        a1 = next(l for l in out if "A1" in l["title"])
        self.assertEqual(a1["level"], "ng")
        text = "\n".join(a1["lines"])
        self.assertIn("N01420", text)
        self.assertIn("20000.0", text)
        self.assertIn("12345.0", text)
        self.assertIn("型コード 4103", text)
        self.assertTrue(O.report_has_ng(out))

    def test_negative_byte_values_match_in_full_mode(self):
        # 控えが -49 と言うのに、1つずつ読むと 207（符号なし）と読んでしまう、という食い違いの検出
        c, folder = self.backed_up()
        c.machine.params[(7220, "L1")] = "-49"
        out = O.self_check(c, folder, full=True)
        self.assertEqual(next(l for l in out if "A1" in l["title"])["level"], "ok")

    def test_type_table_difference_is_listed(self):
        c, folder = self.backed_up()
        c.machine.types[9999] = 1                   # 型情報にだけある番号
        out = O.self_check(c, folder)
        a4 = next(l for l in out if "A4" in l["title"])
        self.assertEqual(a4["level"], "warn")
        self.assertIn("9999", "\n".join(a4["lines"]))

    def test_l_label_probe_shows_axis_field_candidates(self):
        c, folder = self.backed_up()
        out = O.self_check(c, folder)
        a2 = next(l for l in out if "A2" in l["title"])
        text = "\n".join(a2["lines"])
        self.assertIn("N00000", text)
        self.assertIn("軸の欄 0", text)
        self.assertIn("軸の欄 2", text)
        self.assertIn("書きません", text)

    def test_status_numbers_are_shown_raw_and_marked_unconfirmed(self):
        c = DirectClient({"status": {"run": 3, "aut": 1}}).connect()
        out = O.self_check(c)
        st = next(l for l in out if "状態の数値" in l["title"])
        self.assertIn("A3", st["title"])
        self.assertIn("run=3", st["lines"][0])
        self.assertIn("運転中", "\n".join(st["lines"]))

    def test_remaining_zero_is_called_out(self):
        c = DirectClient({"remaining": 0}).connect()
        out = O.self_check(c)
        pi = next(l for l in out if "プログラムの登録" in l["title"])
        self.assertIn("1本も入りません", "\n".join(pi["lines"]))

    def test_unsupported_machine_stops_after_status(self):
        c = DirectClient()
        c.lib.m.cnc_type = b"0 "
        c.connect()
        out = O.self_check(c)
        self.assertTrue(any("ここで止めました" in l["title"] for l in out))
        self.assertEqual(c.lib.called("cnc_rdparam"), 0)

    def test_lost_connection_raises(self):
        c, folder = self.backed_up()
        c.lib.sc["lost_after"] = c.lib.count + 4
        with self.assertRaises(C.FocasError) as cm:
            O.self_check(c, folder)
        self.assertEqual(cm.exception.kind, "lost")

    def test_report_text(self):
        c, folder = self.backed_up()
        c.machine.params[(1420, "A4")] = "1.0"
        text = O.format_report(O.self_check(c, folder))
        self.assertIn("[NG ]", text)
        self.assertIn("[OK ]", text)
        self.assertIn("機種 31i", text)


class TestDiagnose(unittest.TestCase):
    def test_no_python_or_helper(self):
        head, todo = O.diagnose(None, {"focas_python32": r"C:\ない\python.exe"})
        self.assertIn("32bit", head)
        head, todo = O.diagnose(None, {"focas_python32": sys.executable,
                                       "focas_helper_path": r"C:\ない\h.py"})
        self.assertIn("focas_helper.py", head)

    def test_not_started_not_connected_connected(self):
        s = {"focas_python32": sys.executable,
             "focas_helper_path": str(Path(__file__).resolve().parent / "fake_focas_helper.py")}
        head, todo = O.diagnose(None, s)
        self.assertIn("まだ起動していません", head)
        c = DirectClient()
        head, todo = O.diagnose(c, s)
        self.assertIn("つながっていません", head)
        self.assertTrue(any("IP" in t for t in todo))
        c.connect()
        head, todo = O.diagnose(c, s, last_error="なにか")
        self.assertIn("つながっています", head)
        self.assertIn("なにか", head)


class TestSelfCheckUnreadValues(unittest.TestCase):
    """A-2: 「読めず」が1つでもあれば OK にしない。全部読めなければ NG、一部なら「一部読めず」。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.settings = copy.deepcopy(DEFAULTS)

    def tearDown(self):
        self._tmp.cleanup()

    def backed_up(self):
        c = DirectClient().connect()
        r = B.take_backup(c, self.root, self.settings)
        return c, r.folder

    def a1(self, c, folder, **kw):
        return next(l for l in O.self_check(c, folder, **kw) if "A1" in l["title"])

    def test_nothing_read_is_ng_and_never_says_it_matches(self):
        c, folder = self.backed_up()
        c.lib.sc["force_rc"] = {"cnc_rdparam": 3}
        a1 = self.a1(c, folder)
        self.assertEqual(a1["level"], "ng")
        self.assertIn("全部読めませんでした", a1["title"])
        self.assertIn("一致 0", a1["title"])
        self.assertNotIn("合っています", "\n".join(a1["lines"]))
        self.assertTrue(O.report_has_ng(O.self_check(c, folder)))

    def test_some_unread_is_warn_not_ok(self):
        c, folder = self.backed_up()
        orig = c.lib._cnc_rdparam

        def some(h, no, axis, length, buf):
            if no.value == 20:
                return 3
            return orig(h, no, axis, length, buf)
        c.lib.cnc_rdparam.impl = some
        a1 = self.a1(c, folder)
        self.assertEqual(a1["level"], "warn")
        self.assertIn("一部読めず", a1["title"])
        self.assertIn("読めず", " ".join(a1["lines"]))
        self.assertGreater(int(a1["title"].split("一致 ")[1].split("・")[0]), 0)
        # 一致したものがあるときだけ「合っています」が出る（読めた分だけ、と断る）
        self.assertIn("合っています", "\n".join(a1["lines"]))
        self.assertIn("読めた", "\n".join(a1["lines"]))

    def test_all_matching_is_ok_and_says_so(self):
        c, folder = self.backed_up()
        a1 = self.a1(c, folder)
        self.assertEqual(a1["level"], "ok")
        self.assertIn("合っています", "\n".join(a1["lines"]))

    def test_different_value_is_still_ng(self):
        c, folder = self.backed_up()
        orig = c.lib._cnc_rdparam

        def wrong(h, no, axis, length, buf):
            rc = orig(h, no, axis, length, buf)
            if rc == 0 and no.value == 20:
                import ctypes
                import struct
                ctypes.memmove(buf, struct.pack("<hhb", no.value, axis.value, 99), 5)
            return rc
        c.lib.cnc_rdparam.impl = wrong
        a1 = self.a1(c, folder)
        self.assertEqual(a1["level"], "ng")
        self.assertNotIn("合っています", "\n".join(a1["lines"]))


class TestSelfCheckOtherModels(unittest.TestCase):
    """B-5: 31i 以外では状態を 31i の並びで日本語にしない。"""

    def test_status_is_raw_only_on_an_unsupported_model(self):
        c = DirectClient({"cnc_type": "0", "status": {"aut": 1, "run": 3}})
        c.connect()
        out = O.self_check(c)
        st = next(l for l in out if "状態の数値" in l["title"])
        self.assertIn("生の数値だけ", st["title"])
        self.assertIn("run=3", st["lines"][0])
        text = "\n".join(st["lines"])
        for word in ("運転中", "メモリ", "読み:"):
            self.assertNotIn(word, text)


class TestUserFacingWords(unittest.TestCase):
    """C: 利用者向けの文に「ヘルパー」「SHA-256」「JSON」を説明なしで出さない。"""

    def test_helper_is_always_explained_in_messages(self):
        for kind in ("internal", "protocol", "no_helper", "helper_old"):
            msg, hint = C.describe({"kind": kind, "message": ""})
            if "ヘルパー" in msg:
                self.assertIn("機械と話す小さなプログラム", msg, kind)
        msg, hint = O.diagnose(None, {"focas_python32": sys.executable,
                                      "focas_helper_path": r"C:\ない\h.py"})
        self.assertIn("機械と話す小さなプログラム", msg)

    def test_diagnose_wording(self):
        head, todo = O.diagnose(None, {"focas_python32": sys.executable,
                                       "focas_helper_path": __file__})
        self.assertNotIn("ヘルパー", head)

    def test_no_bare_jargon_in_messages(self):
        for err in ({"kind": "protocol", "message": ""}, {"kind": "internal", "message": ""}):
            msg, hint = C.describe(err)
            self.assertNotIn("JSON", msg + hint)
            self.assertNotIn("SHA", msg + hint)
        with self.assertRaises(C.FocasError) as cm:
            C.FocasClient.decode_data({"data": "AAAA", "sha256": "x", "size": 3})
        self.assertNotIn("SHA", cm.exception.message)
        self.assertIn("指紋", cm.exception.message)


if __name__ == "__main__":
    unittest.main()
