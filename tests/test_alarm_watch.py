# -*- coding: utf-8 -*-
"""アラームを読む・意味を引く・直ったか比べる・記録する部品（段3）。機械は偽物。実機の確認ではない。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from nd287_app import alarm_watch as AW, fanuc_alarms as FA, focas_ops   # noqa: E402


def al(typ, no, text="", axis=0):
    from nd287_app import focas_helper as H
    return {"no": no, "type": typ, "axis": axis, "text": text, "code": H.alarm_code(typ, no)}


class TestRanking(unittest.TestCase):
    def setUp(self):
        self.db = FA.load_alarms()

    def test_search_alarms_unchanged(self):
        # 今までの検索は変えていない（PW0000 を探すと PS0000 が先、SW0100 は PS0100、SV0436 は 0 件）
        self.assertEqual(FA.search_alarms(self.db, "PW0000")[0]["code"], "PS0000")
        self.assertEqual(FA.search_alarms(self.db, "SW0100")[0]["code"], "PS0100")
        self.assertEqual(FA.search_alarms(self.db, "SV0436"), [])

    def test_ranked_puts_exact_first(self):
        r = FA.search_alarms_ranked(self.db, "PW0000")
        self.assertEqual(r[0]["code"], "PW0000")
        self.assertEqual({a["code"] for a in r}, {a["code"] for a in FA.search_alarms(self.db, "PW0000")})

    def test_exact_code_ignores_leading_zero_and_case(self):
        self.assertEqual(AW.lookup(self.db, "ps10")["exact"][0]["code"], "PS0010")
        self.assertEqual(AW.lookup(self.db, "PS 0010")["exact"][0]["code"], "PS0010")

    def test_digits_only_query_is_not_exact(self):
        self.assertFalse(FA.is_exact_code({"code": "OT0510"}, "510"))


class TestLookup(unittest.TestCase):
    def setUp(self):
        self.db = FA.load_alarms()

    def test_pw0000_exact_first_and_ps0000_is_near(self):
        r = AW.lookup(self.db, "PW0000", "POWER MUST BE OFF")
        self.assertTrue(r["known"])
        self.assertEqual([a["code"] for a in r["exact"]], ["PW0000"])
        self.assertEqual([a["code"] for a in r["near"]], ["PS0000"])     # 数字だけ同じ別の種類
        self.assertEqual(r["machine_text"], "POWER MUST BE OFF")

    def test_sw0100_is_not_in_the_list(self):
        r = AW.lookup(self.db, "SW0100", "PARAMETER WRITE ENABLE")
        self.assertFalse(r["known"])
        self.assertEqual(r["exact"], [])
        self.assertEqual([a["code"] for a in r["near"]], ["PS0100"])
        self.assertEqual(r["machine_text"], "PARAMETER WRITE ENABLE")      # 機械の文字を出せる

    def test_sv0436_has_no_hits(self):
        r = AW.lookup(self.db, "SV0436", "SERVO ALARM")
        self.assertEqual((r["exact"], r["near"], r["known"]), ([], [], False))

    def test_empty_code(self):
        self.assertFalse(AW.lookup(self.db, "")["known"])


class TestNormalAndDiff(unittest.TestCase):
    def test_sw0100_is_normal_by_default(self):
        self.assertTrue(AW.is_normal(al(0, 100), focas_ops.DEFAULT_ALLOWED_ALARMS))
        self.assertTrue(AW.is_normal({"code": "sw0100"}, focas_ops.allowed_codes({})))
        self.assertFalse(AW.is_normal(al(1, 0), focas_ops.DEFAULT_ALLOWED_ALARMS))
        self.assertTrue(AW.is_normal(al(1, 0), focas_ops.allowed_codes({"focas_allowed_alarms": ["SW0100", "PW0000"]})))

    def test_normalize(self):
        out = AW.normalize([{"type": 4, "no": 510, "text": " OT "}, "x", {}, {"code": "PS0010", "text": "G"}])
        self.assertEqual([a["code"] for a in out], ["OT0510", "PS0010"])
        self.assertEqual(out[0]["text"], "OT")

    def test_diff_gone_still_new(self):
        before = [al(1, 0, "POWER"), al(0, 100, "PWE"), al(6, 436, "SV", axis=1)]
        now = [al(0, 100, "PWE"), al(3, 10, "G CODE")]
        d = AW.alarm_diff(before, now)
        self.assertEqual([a["code"] for a in d["gone"]], ["PW0000", "SV0436"])
        self.assertEqual([a["code"] for a in d["still"]], ["SW0100"])
        self.assertEqual([a["code"] for a in d["new"]], ["PS0010"])

    def test_diff_same_key_as_focas_ops(self):
        # 軸が違えば別のアラーム（focas_ops._alarm_key と同じ比べ方）
        d = AW.alarm_diff([al(6, 436, "x", axis=1)], [al(6, 436, "x", axis=2)])
        self.assertEqual((len(d["gone"]), len(d["still"]), len(d["new"])), (1, 0, 1))

    def test_diff_with_code_and_text_only(self):
        # はじめてモードの読む係が渡す形（番号と文字だけ）でも比べられる
        d = AW.alarm_diff([{"code": "PW0000", "text": "a"}],
                          [{"code": "PW0000", "text": "a"}, {"code": "OT0510", "text": "b"}])
        self.assertEqual(([a["code"] for a in d["still"]], [a["code"] for a in d["new"]]), (["PW0000"], ["OT0510"]))
        self.assertEqual(AW.alarm_diff([{"code": "PW0000"}], [])["gone"][0]["code"], "PW0000")


class TestRecord(unittest.TestCase):
    def test_round_trip_and_last_fix(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "アラーム記録.csv"
            self.assertIsNone(AW.last_fix(p, "PW0000"))
            AW.append_record(p, {"号機": "23", "CNC ID": "ABC", "番号": "PW0000", "機械の文字": "POWER",
                                 "やったこと": "電源を入れ直した", "結果": AW.RESULT_FIXED})
            AW.append_record(p, {"号機": "23", "番号": "PW0000", "やったこと": "ケーブルを差し直した", "結果": AW.RESULT_FIXED})
            AW.append_record(p, {"番号": "PW0000", "やったこと": "何かした", "結果": "直らなかった"})
            AW.append_record(p, {"番号": "OT0510", "やったこと": "手でもどした, 一言", "結果": AW.RESULT_FIXED})
            raw = p.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(raw.count(b"\xef\xbb\xbf"), 1)                  # BOM を重ねない
            self.assertTrue(raw.decode("utf-8-sig").splitlines()[0].startswith(
                "日時,機番,CNC ID,番号,機械の文字,やったこと,結果"))
            rows = AW.read_records(p)
            self.assertEqual(len(rows), 4)
            self.assertTrue(rows[0]["日時"])
            self.assertEqual(AW.last_fix(p, "PW0000"), "ケーブルを差し直した")   # 直ったもののうち、いちばん新しい
            self.assertEqual(AW.last_fix(p, "pw0"), "ケーブルを差し直した")
            self.assertEqual(AW.last_fix(p, "OT0510"), "手でもどした, 一言")
            self.assertIsNone(AW.last_fix(p, "SV0436"))

    def test_remedy_art_uses_existing_pictures(self):
        self.assertEqual(AW.remedy_art("PW0000"), "power_cycle")
        self.assertEqual(AW.remedy_art("PS0000"), "power_cycle")
        self.assertEqual(AW.remedy_art("EX1000", "EMERGENCY STOP"), "estop")
        self.assertIsNone(AW.remedy_art("SV0436", "SERVO ALARM"))


class TestRecordFile(unittest.TestCase):
    def test_multiline_quoted_field_and_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.csv"
            AW.append_record(p, {"番号": "PW0000", "機械の文字": "A\nB", "やったこと": 'x, "y"', "結果": "直った"})
            AW.append_record(p, {"番号": "OT0510", "やったこと": "z", "結果": "直った"})
            rows = AW.read_records(p)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["機械の文字"], "A\nB")
            self.assertEqual(rows[0]["やったこと"], 'x, "y"')
            self.assertIn("機番", AW.RECORD_HEADER)

    def test_cp932_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.csv"
            p.write_bytes("日時,番号,やったこと,結果\r\n2026-01-01,PW0000,再起動,直った\r\n".encode("cp932"))
            self.assertEqual(AW.last_fix(p, "PW0000"), "再起動")
            p.write_bytes(b"\xff\xfe\x00\x81")                     # 読めない中身でも落ちない
            self.assertIsInstance(AW.read_records(p), list)

    def test_append_adds_missing_newline(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.csv"
            p.write_text("日時,番号,やったこと,結果\r\nt,PW0000,a,直った", encoding="utf-8")   # 最後に改行なし
            AW.append_record(p, {"日時": "t2", "番号": "PW0000", "やったこと": "b", "結果": "直った"})
            self.assertEqual(len(AW.read_records(p)), 2)


class TestMachineRead(unittest.TestCase):
    """通常の画面のアラーム検索「機械から読む」。偽物の機械へ直接つなぐ（実機ではない）。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def make(self, scenario, settings=None):
        from fake_client import DirectClient
        from nd287_app import gui
        made = []

        def factory(st):
            c = DirectClient(scenario=scenario)
            made.append(c)
            return c
        dlg = gui.AlarmHelpDialog(None, settings if settings is not None else {"focas_host": "127.0.0.1"})
        dlg._client_factory = factory
        return dlg, made

    def wait(self, dlg):
        import time
        t = time.time()
        while dlg._read_timer.isActive() and time.time() - t < 10:
            self.app.processEvents()
            time.sleep(0.02)
        self.app.processEvents()

    def test_read_puts_machine_alarms_first_and_disconnects(self):
        dlg, made = self.make({"alarms": [(0, 1, 0, "POWER MUST BE OFF"), (436, 6, 1, "SERVO ALARM"),
                                          (100, 0, 0, "PWE")]})
        dlg._read_from_machine()
        self.wait(dlg)
        self.assertEqual([a["code"] for a in dlg._results[:3]], ["PW0000", "SV0436", "SW0100"])
        self.assertNotEqual(dlg._results[0]["code"], "PS0000")              # 番号がぴったり同じ PW0000 が先
        self.assertEqual(dlg.list.count(), len(dlg._results))
        self.assertIn("アプリの一覧にありません", dlg._results[1]["cause"])
        self.assertIn("SERVO ALARM", dlg._results[1]["cause"])
        self.assertIn("異常ではありません", dlg._results[2]["title"])
        self.assertIn("機械から読みました", dlg.lbl_machine.text())
        self.assertEqual(made[0].calls, ["connect", "alarms", "disconnect"])     # 読むだけ。すぐ切る
        self.assertTrue(dlg.b_machine.isEnabled())

    def test_no_alarm_and_no_host(self):
        dlg, made = self.make({"alarms": []})
        dlg._read_from_machine()
        self.wait(dlg)
        self.assertIn("ありません", dlg.lbl_machine.text())
        dlg2, made2 = self.make({"alarms": []}, settings={})
        dlg2._read_from_machine()
        self.assertIn("IP アドレス", dlg2.lbl_machine.text())
        self.assertEqual(made2, [])

    def test_alarms_given_means_no_second_connection(self):
        from nd287_app import gui
        made = []
        dlg = gui.AlarmHelpDialog(None, {"focas_host": "127.0.0.1"},
                                  machine_alarms=[{"code": "PW0000", "text": "POWER <MUST> BE OFF"}])
        dlg._client_factory = lambda st: made.append(1)
        self.assertTrue(dlg.b_machine.isHidden())
        self.assertFalse(dlg.b_machine.isEnabled())
        dlg._read_from_machine()                                      # 押せても、つながない
        dlg.b_machine.click()
        self.assertEqual(made, [])
        self.assertEqual(dlg._results[0]["code"], "PW0000")
        self.assertNotIn("<MUST>", dlg._results[0]["cause"])           # 機械の文字は html として扱わない

    def test_unexpected_error_is_plain_japanese(self):
        from nd287_app import gui

        class Bad:
            def call(self, cmd, args=None, **k):
                raise OSError("boom")

            def close(self):
                pass
        dlg = gui.AlarmHelpDialog(None, {"focas_host": "127.0.0.1"})
        dlg._client_factory = lambda st: Bad()
        dlg._read_from_machine()
        self.wait(dlg)
        self.assertIn("機械と話せませんでした（OSError）", dlg.lbl_machine.text())

    def test_cannot_connect_shows_one_line_reason(self):
        from nd287_app import focas_client as C
        from nd287_app import gui

        class Bad:
            def call(self, cmd, args=None, **k):
                raise C.FocasError("connect", "機械につながりません（cnc_allclibhndl3、戻り値 -16）。")

            def close(self):
                pass
        dlg = gui.AlarmHelpDialog(None, {"focas_host": "127.0.0.1"})
        dlg._client_factory = lambda st: Bad()
        dlg._read_from_machine()
        self.wait(dlg)
        self.assertIn("読めませんでした: 機械につながりません", dlg.lbl_machine.text())
        self.assertTrue(dlg.b_machine.isEnabled())


if __name__ == "__main__":
    unittest.main()
