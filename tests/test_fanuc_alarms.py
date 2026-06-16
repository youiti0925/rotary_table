# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from nd287_app import fanuc_alarms as fa


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.alarms = fa.load_alarms()

    def test_empty_query_returns_all(self):
        self.assertEqual(len(fa.search_alarms(self.alarms, "")), len(self.alarms))

    def test_by_modern_code(self):
        hits = fa.search_alarms(self.alarms, "OT0510")
        self.assertTrue(any(h["code"] == "OT0510" for h in hits))

    def test_by_legacy_number(self):
        # 旧番号 510 でソフトリミットが引ける
        hits = fa.search_alarms(self.alarms, "510")
        self.assertTrue(any("OT0510" == h["code"] for h in hits))

    def test_number_leading_zero_tolerant(self):
        # "100" で PS0100 が引ける
        hits = fa.search_alarms(self.alarms, "100")
        self.assertTrue(any(h["code"] == "PS0100" for h in hits))

    def test_short_number_no_substring_overmatch(self):
        # "10" は PS0010 だけ。PS0100/OT0510/SV0410 等を部分一致で巻き込まない
        codes = {h["code"] for h in fa.search_alarms(self.alarms, "10")}
        self.assertIn("PS0010", codes)
        self.assertNotIn("PS0100", codes)
        self.assertNotIn("OT0510", codes)
        self.assertNotIn("SV0410", codes)

    def test_alnum_query_still_finds_code(self):
        # "ps100"（文字+数字）でも PS0100 を補完的に拾う
        hits = fa.search_alarms(self.alarms, "ps100")
        self.assertTrue(any(h["code"] == "PS0100" for h in hits))

    def test_by_japanese_keyword(self):
        hits = fa.search_alarms(self.alarms, "オーバートラベル")
        self.assertTrue(len(hits) >= 2)
        self.assertTrue(all("OT" in h["group"] or "オーバートラベル" in h["title"]
                            for h in hits))

    def test_by_english_keyword(self):
        hits = fa.search_alarms(self.alarms, "overtravel")
        self.assertTrue(any(h["code"].startswith("OT") for h in hits))

    def test_keyword_battery(self):
        hits = fa.search_alarms(self.alarms, "電池")
        self.assertTrue(any("APC" in h["code"] for h in hits))

    def test_no_match(self):
        self.assertEqual(fa.search_alarms(self.alarms, "ZZZ存在しない番号999999"), [])


class TestUserCsv(unittest.TestCase):
    def test_user_csv_merged_and_searchable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "alarms.csv"
            p.write_text(
                "コード,分類,名称,原因,対処,キーワード\n"
                "PMC0123,PMC,自社固有アラーム,テスト原因,テスト対処,自社 テスト\n",
                encoding="utf-8",
            )
            alarms = fa.load_alarms(str(p))
            self.assertTrue(any(a["code"] == "PMC0123" for a in alarms))
            hits = fa.search_alarms(alarms, "自社")
            self.assertEqual(hits[0]["code"], "PMC0123")
            hits2 = fa.search_alarms(alarms, "123")
            self.assertTrue(any(h["code"] == "PMC0123" for h in hits2))

    def test_missing_csv_just_builtin(self):
        alarms = fa.load_alarms("/no/such/file.csv")
        self.assertEqual(len(alarms), len(fa.BUILTIN_ALARMS))


if __name__ == "__main__":
    unittest.main()
