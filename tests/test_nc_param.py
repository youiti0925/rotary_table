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

# 制御（MELDAS/FANUC）列つきの例。同じ製品で制御ごとに番号が違う。
SAMPLE_CSV_CTRL = """型式,制御,番号,軸,変更値,メモ
RTT-301DA,MELDAS-60,2003,,1304,減速比
RTT-301DA,MELDAS-800,2206,,1304,減速比
RTT-301DA,FANUC,1820,1,100,CMR
RTT-301DA,,3,,共通,全制御共通の行
"""


class TestParseChanges(unittest.TestCase):
    def setUp(self):
        self.changes = P.parse_changes(SAMPLE_CSV)

    def test_models_parsed(self):
        self.assertEqual(set(self.changes), {"RTT-315", "RWE-200"})

    def test_blank_rows_skipped_and_last_wins(self):
        rtt = self.changes["RTT-315"]
        # 1825/軸1 は後勝ちで 8500、番号は重複しない（制御""・モード""）
        v = [c for c in rtt if c.key() == ("", "", "1825", "1")]
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


class TestController(unittest.TestCase):
    def setUp(self):
        self.ch = P.parse_changes(SAMPLE_CSV_CTRL)

    def test_controllers_listed(self):
        ctrls = P.controllers_for_model(self.ch, "RTT-301DA")
        self.assertEqual(ctrls, ["MELDAS-60", "MELDAS-800", "FANUC"])

    def test_filter_by_controller_includes_common(self):
        # MELDAS-60 を選ぶと、MELDAS-60 の行＋制御空欄(共通)の行
        got = P.changes_for_model(self.ch, "RTT-301DA", controller="MELDAS-60")
        nums = sorted(c.number for c in got)
        self.assertEqual(nums, ["2003", "3"])  # 2003(MELDAS-60) + 3(共通)

    def test_filter_fanuc(self):
        got = P.changes_for_model(self.ch, "RTT-301DA", controller="FANUC")
        nums = sorted(c.number for c in got)
        self.assertEqual(nums, ["1820", "3"])

    def test_no_controller_returns_all(self):
        got = P.changes_for_model(self.ch, "RTT-301DA")
        self.assertEqual(len(got), 4)

    def test_csv_roundtrip_keeps_controller(self):
        import tempfile, os
        path = os.path.join(tempfile.mkdtemp(), "c.csv")
        P.write_changes(path, self.ch)
        again = P.load_changes(path)
        got = P.changes_for_model(again, "RTT-301DA", controller="MELDAS-800")
        self.assertEqual(sorted(c.number for c in got), ["2206", "3"])


def _entry(model, mode="", controller="", axis="", seiban="", kind="", motor="",
           date="", basic="", items=None):
    return P.ParamEntry(model=model, mode=mode, controller=controller, axis=axis,
                        seiban=seiban, kind=kind, motor=motor, date=date, basic=basic,
                        items=items or [])


class TestEntries(unittest.TestCase):
    """エントリ単位のデータベース: 登録(upsert)・履歴・編集・削除・検索・移行。"""

    def setUp(self):
        import tempfile, os
        self.path = os.path.join(tempfile.mkdtemp(), "db.csv")

    def test_add_and_roundtrip_all_columns(self):
        e = _entry("RTT-137", mode="フル", controller="F30", axis="A4",
                   seiban="50013078", kind="傾斜", motor="αiS4", date="2026/06/23",
                   basic="F30BASIC.PRM",
                   items=[("1825", "2500", "位置ゲイン"), ("2020", "300", "")])
        eid, action = P.upsert_entry(self.path, e)
        self.assertEqual(action, "added")
        got = P.get_entry(P.load_entries(self.path), eid)
        self.assertEqual((got.model, got.mode, got.controller, got.axis, got.seiban,
                          got.kind, got.motor, got.basic),
                         ("RTT-137", "フル", "F30", "A4", "50013078", "傾斜",
                          "αiS4", "F30BASIC.PRM"))
        self.assertEqual(got.values(), {"1825": "2500", "2020": "300"})
        self.assertEqual(got.items[0][2], "位置ゲイン")

    def test_semi_full_separate_entries(self):
        P.upsert_entry(self.path, _entry("RTT-137", mode="フル", controller="F30",
                                         axis="A4", items=[("1815", "00000010", "")]))
        P.upsert_entry(self.path, _entry("RTT-137", mode="セミ", controller="F30",
                                         axis="A4", items=[("1815", "00000000", "")]))
        es = P.load_entries(self.path)
        self.assertEqual(len(es), 2)
        self.assertEqual({e.mode for e in es}, {"フル", "セミ"})

    def test_history_by_axis_and_seiban(self):
        # 同じ型式・モード・制御でも 軸 や Seiban が違えば別エントリ＝履歴
        P.upsert_entry(self.path, _entry("RTT-137", mode="フル", controller="F30",
                                         axis="A4", seiban="A", items=[("1825", "2500", "")]))
        P.upsert_entry(self.path, _entry("RTT-137", mode="フル", controller="F31",
                                         axis="A2", seiban="B", items=[("1825", "2500", "")]))
        self.assertEqual(len(P.load_entries(self.path)), 2)

    def test_upsert_updates_same_config(self):
        P.upsert_entry(self.path, _entry("RTT-137", mode="フル", controller="F30",
                                         axis="A4", seiban="A", items=[("1825", "2500", "")]))
        eid, action = P.upsert_entry(self.path, _entry(
            "RTT-137", mode="フル", controller="F30", axis="A4", seiban="A",
            items=[("1825", "9999", "")]))
        self.assertEqual(action, "updated")
        es = P.load_entries(self.path)
        self.assertEqual(len(es), 1)
        self.assertEqual(es[0].values()["1825"], "9999")

    def test_unchanged_no_write(self):
        import os
        e = _entry("RTT-137", mode="フル", controller="F30", axis="A4", seiban="A",
                   date="2026/06/23", items=[("1825", "2500", "")])
        P.upsert_entry(self.path, e)
        mtime = os.path.getmtime(self.path)
        _, action = P.upsert_entry(self.path, _entry(
            "RTT-137", mode="フル", controller="F30", axis="A4", seiban="A",
            date="2026/06/23", items=[("1825", "2500", "")]))
        self.assertEqual(action, "unchanged")
        self.assertEqual(os.path.getmtime(self.path), mtime)

    def test_delete(self):
        eid, _ = P.upsert_entry(self.path, _entry("RTT-137", items=[("1", "1", "")]))
        P.upsert_entry(self.path, _entry("RWE-200", items=[("2", "2", "")]))
        self.assertTrue(P.delete_entry(self.path, eid))
        es = P.load_entries(self.path)
        self.assertEqual([e.model for e in es], ["RWE-200"])
        self.assertFalse(P.delete_entry(self.path, "zzzz"))

    def test_update_entry_by_id(self):
        eid, _ = P.upsert_entry(self.path, _entry("RTT-137", mode="フル",
                                                  items=[("1825", "2500", "")]))
        e = P.get_entry(P.load_entries(self.path), eid)
        e.motor = "αiS8"
        e.items = [("1825", "2500", ""), ("1826", "8", "")]
        self.assertTrue(P.update_entry(self.path, e))
        got = P.get_entry(P.load_entries(self.path), eid)
        self.assertEqual(got.motor, "αiS8")
        self.assertEqual(got.count(), 2)

    def test_search_and_filter(self):
        P.upsert_entry(self.path, _entry("RTT-137", mode="フル", controller="F30",
                                         kind="傾斜", seiban="50013078",
                                         items=[("1825", "2500", "")]))
        P.upsert_entry(self.path, _entry("RTT-137", mode="セミ", controller="F30",
                                         kind="傾斜", items=[("1825", "2000", "")]))
        P.upsert_entry(self.path, _entry("RWE-200", mode="フル", controller="F31",
                                         kind="回転", items=[("2020", "265", "")]))
        es = P.load_entries(self.path)
        self.assertEqual(len(P.search_entries(es, "50013078")), 1)
        self.assertEqual(len(P.search_entries(es, mode="セミ")), 1)
        self.assertEqual(len(P.search_entries(es, kind="回転")), 1)
        self.assertEqual(len(P.search_entries(es, controller="F30")), 2)
        self.assertEqual(len(P.search_entries(es, "1825")), 2)

    def test_migrates_old_csv_without_id(self):
        # 旧スキーマ(IDなし)のCSVも (型式×制御×モード) でエントリ化して読める
        P.write_changes(self.path, P.parse_changes(SAMPLE_CSV_CTRL))
        es = P.load_entries(self.path)
        self.assertTrue(all(e.id for e in es))  # IDが振られている
        models = {e.model for e in es}
        self.assertIn("RTT-301DA", models)

    def test_helpers(self):
        self.assertEqual(P.kind_from_prefix("T"), "傾斜")
        self.assertEqual(P.kind_from_prefix("R"), "回転")
        self.assertEqual(P.controller_from_basic("/x/F30BASIC.PRM"), "F30")
        self.assertEqual(P.controller_from_basic("F31BASIC.prm"), "F31")

    def test_axis_name_number(self):
        # 第1〜6軸 = X,Y,Z,A,B,C（第4軸が回転/傾斜の A 軸）
        self.assertEqual([P.axis_name(n) for n in range(1, 7)],
                         ["X", "Y", "Z", "A", "B", "C"])
        self.assertEqual(P.axis_number("A"), 4)
        self.assertEqual(P.axis_number("X"), 1)
        self.assertEqual(P.axis_number("C"), 6)
        self.assertEqual(P.axis_number("A4"), 4)  # 'A4'/'4' も許容
        self.assertEqual(P.axis_number(""), 0)


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
