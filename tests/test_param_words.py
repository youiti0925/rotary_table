"""機械に書く値の意味（param_words.py）。分からない番号には何も出さない・CSV で直せる。"""
import tempfile
import unittest
from pathlib import Path

from nd287_app import param_words as PW


class TestParamWords(unittest.TestCase):
    def test_known_and_unknown(self):
        w = PW.load(None)
        self.assertIn("モーター番号", PW.word(w, 2020))
        self.assertEqual(PW.word(w, 99999), "")                 # 推測で書かない
        self.assertEqual(PW.word(w, "x"), "")

    def test_number_forms(self):
        self.assertEqual(PW.number("N01815"), 1815)
        self.assertEqual(PW.number("No.2020"), 2020)
        self.assertIsNone(PW.number("番号"))

    def test_csv_overrides_adds_and_removes(self):
        p = Path(tempfile.mkdtemp()) / PW.WORDS_FILE
        p.write_text("番号,意味\nN2020,モーターの種類の番号\n3111,サーボ画面を出す\n1420,\n", encoding="utf-8-sig")
        w = PW.load(p)
        self.assertEqual(PW.word(w, 2020), "モーターの種類の番号")
        self.assertEqual(PW.word(w, 3111), "サーボ画面を出す")
        self.assertEqual(PW.word(w, 1420), "")                   # 空にした番号は出さない
        self.assertIn("アンプ", PW.word(w, 2165))                # 触っていない番号は初めの文のまま
        self.assertEqual(PW.template_rows()[0], ["番号", "意味"])

    def test_agrees_with_names_already_in_the_app(self):
        """semi_convert.NAMES（前から画面に出している名前）と食い違わない。"""
        w = PW.load(None)
        self.assertIn("レファレンスカウンタ", w[1821])
        self.assertIn("速度パルス数", w[2023])
        self.assertIn("位置パルス数", w[2024])
        self.assertIn("分子", w[2084])
        self.assertIn("分母", w[2085])


if __name__ == "__main__":
    unittest.main()
