# -*- coding: utf-8 -*-
import unittest

from nd287_app import nc_param as P


SAMPLE_CSV = """型式,番号,軸,変更値,メモ
RTT-315,1825,1,8000,位置ループゲイン
RTT-315,1826,1,20,インポジ幅
RTT-315,3003,,00000001,ビット
RWE-200,1825,1,9000,別仕様
,,,,
RTT-315,1825,1,8500,後勝ちで上書き
"""


class TestParseChanges(unittest.TestCase):
    def setUp(self):
        self.changes = P.parse_changes(SAMPLE_CSV)

    def test_models_parsed(self):
        self.assertEqual(set(self.changes), {"RTT-315", "RWE-200"})

    def test_blank_rows_skipped_and_last_wins(self):
        rtt = self.changes["RTT-315"]
        # 1825/軸1 は後勝ちで 8500、番号は重複しない
        v = [c for c in rtt if c.key() == ("1825", "1")]
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0].value, "8500")
        # 1826, 3003 も読めている
        self.assertEqual({c.number for c in rtt}, {"1825", "1826", "3003"})

    def test_column_alias(self):
        csv2 = "機種,パラメータ,軸,設定値,備考\nX,100,,1,test\n"
        ch = P.parse_changes(csv2)
        self.assertEqual(ch["X"][0].number, "100")
        self.assertEqual(ch["X"][0].value, "1")
        self.assertEqual(ch["X"][0].note, "test")

    def test_roundtrip_csv(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        path = os.path.join(d, "p.csv")
        P.write_changes(path, self.changes)
        again = P.load_changes(path)
        self.assertEqual(P.changes_for_model(again, "RTT-315"),
                         P.changes_for_model(self.changes, "RTT-315"))


class TestModelMatch(unittest.TestCase):
    def test_normalized_match(self):
        ch = P.parse_changes(SAMPLE_CSV)
        # 記号/空白ゆらぎでも当たる
        got = P.changes_for_model(ch, "rtt315")
        self.assertTrue(got)
        self.assertEqual({c.number for c in got}, {"1825", "1826", "3003"})

    def test_no_match(self):
        ch = P.parse_changes(SAMPLE_CSV)
        self.assertEqual(P.changes_for_model(ch, "ZZZ"), [])


class TestChecklist(unittest.TestCase):
    def test_checklist_old_value_from_master(self):
        ch = P.changes_for_model(P.parse_changes(SAMPLE_CSV), "RTT-315")
        master = {("1825", "1"): "5000", ("1826", "1"): "10"}
        rows = P.checklist_rows(ch, master)
        d = {(n, a): (old, new) for (n, a, old, new, note) in rows}
        self.assertEqual(d[("1825", "1")], ("5000", "8500"))
        self.assertEqual(d[("1826", "1")], ("10", "20"))
        # マスタに無い 3003 は旧値空
        self.assertEqual(d[("3003", "")][0], "")

    def test_format_checklist_has_all(self):
        ch = P.changes_for_model(P.parse_changes(SAMPLE_CSV), "RTT-315")
        text = P.format_checklist("RTT-315", ch)
        for num in ("1825", "1826", "3003"):
            self.assertIn(num, text)
        self.assertIn("変更点数: 3", text)


class TestParamFile(unittest.TestCase):
    def test_diff_file_only_changed_params(self):
        ch = P.changes_for_model(P.parse_changes(SAMPLE_CSV), "RTT-315")
        text = P.format_param_file(ch)
        self.assertTrue(text.startswith("%"))
        self.assertIn("\r\n", text)
        # 変更する3番号だけが載る（他のパラメータは載らない＝書き換わらない）
        body = [l for l in text.splitlines() if l.startswith("N")]
        self.assertEqual(len(body), 3)
        self.assertIn("N1825 A1 P8500", text)
        self.assertIn("N3003 P00000001", text)

    def test_parse_backup_loose(self):
        backup = "%\nN1825 A1 5000 ;\nN1826 A1 10 ;\nゴミ行\nN3003 00000001 ;\n%\n"
        table = P.parse_param_backup(backup)
        self.assertEqual(table[("1825", "1")], "5000")
        self.assertEqual(table[("1826", "1")], "10")


if __name__ == "__main__":
    unittest.main()
