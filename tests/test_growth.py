"""登録の育ち具合（growth.py）。数えるだけ・数えられないものを 0 と言わない。"""
import unittest

from nd287_app import growth as GR


class TestGrowth(unittest.TestCase):
    def test_uncounted_is_not_zero(self):
        its = {i["key"]: i for i in GR.items({"times": 3, "alarms": None})}
        self.assertEqual(GR.count_text(its["times"]), "3 回")
        self.assertEqual(GR.count_text(its["alarms"]), "数えられませんでした")
        self.assertEqual(len(its), 7)
        for i in its.values():
            self.assertTrue(i["gives"] and i["grow"])

    def test_model_rows_and_next_best(self):
        self.assertEqual(GR.for_model("", has_conditions=True, saved=9, times=1, fixes=0), [])
        rows = GR.for_model("RWE-200", has_conditions=False, saved=3, times=0, fixes=0)
        self.assertIn("測定条件を登録すると", GR.next_best(rows))            # 一番先に効くものを1つだけ
        rows = GR.for_model("RWE-200", has_conditions=True, saved=3, times=0, fixes=0)
        self.assertIn("あと 2 件", GR.next_best(rows))
        rows = GR.for_model("RWE-200", has_conditions=True, saved=5, times=0, fixes=0)
        self.assertIn("最後まで1回測ると", GR.next_best(rows))
        rows = GR.for_model("RWE-200", has_conditions=True, saved=5, times=2, fixes=0)
        self.assertEqual(GR.next_best(rows), "")                               # 直ったやり方は、外れなければ増えないので促さない
        rows = GR.for_model("RWE-200", has_conditions=True, saved=None, times=2, fixes=0)
        self.assertIn("数えられませんでした", rows[1]["state"])

    def test_count_models_without_duplicates(self):
        table = {"rotary": [{"型式": "RWE-200"}, {"型式": "rwe-200"}, {"型式": "RTV-231"}], "tilt": [{"型式": "RTV-231"}]}
        self.assertEqual(GR.count_models(table, {"RWE-200": {}, "NEW-1": {}}), 3)
        self.assertEqual(GR.count_models(None), 0)

    def test_rows_for_model(self):
        rows = [{"型式": "rwe-200"}, {"型式": "RWE-2000"}, {"型式": ""}]
        self.assertEqual(len(GR.rows_for_model(rows, "RWE-200")), 1)
        self.assertEqual(GR.rows_for_model(rows, ""), [])


if __name__ == "__main__":
    unittest.main()


class TestPeople(unittest.TestCase):
    def test_counts_finished_measurements_per_person(self):
        rows = [{"日時": "2026-11-20 10:00", "測り方": "回転分割", "名前": "YU"},
                {"日時": "2026-11-26 09:00", "測り方": "回転分割", "名前": "YU"},
                {"日時": "2026-11-25 09:00", "測り方": "回転再現性", "名前": "YU"},
                {"日時": "2026-11-25 09:00", "測り方": "回転分割", "名前": "SATO"},
                {"日時": "2026-11-25 09:00", "測り方": "回転分割", "名前": ""},
                {"日時": "2026-11-25 09:00", "測り方": "回転分割"}]              # 前の形の行（名前なし）は数えない
        p = GR.people(rows)
        self.assertEqual([d["name"] for d in p], ["YU", "SATO"])
        self.assertEqual(p[0]["by_mode"], {"回転分割": 2, "回転再現性": 1})
        self.assertEqual((p[0]["first"], p[0]["last"]), ("2026-11-20", "2026-11-26"))
        self.assertEqual(GR.people_modes(p), ["回転分割", "回転再現性"])
        self.assertEqual(GR.people([]), [])
