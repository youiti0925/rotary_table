# -*- coding: utf-8 -*-
import unittest

from nd287_app import help_text as H


class TestLearnedSection(unittest.TestCase):
    def test_section_is_listed_and_covers_everything(self):
        titles = [t for t, _ in H.HELP_SECTIONS]
        self.assertTrue(any("2026-11-24" in t for t in titles))
        body = H.LEARNED_20261124
        for needle in ("-D駆動", "90J0", "28.0", "11ページ", "2165", "2020", "DGPR", "1815", "2300", "SW0100", "PW0000",
                       "フルクロ", "セミクロ", "組探し", "製品の型式索引.json", "クローズ", "規則の結果記録.csv",
                       "モーター規則.csv", "G:\\マイドライブ\\特注パラメータ", "未確認", "推測", "利用者", "データ"):
            self.assertIn(needle, body, needle)
        self.assertEqual(body.count("<table"), body.count("</table>"))

    def test_facts_match_the_code(self):
        from nd287_app import semi_convert as S, settings as ST
        self.assertEqual(S.ALLOWED_CMR, (2, 20))
        self.assertEqual((S.PPLS_SEMI, S.VELPULSE_SEMI), (12500, 8192))
        self.assertEqual(ST.DEFAULTS["amp_current_map"], {"20A": "25", "40A": "45", "80A": "85", "160A": "165"})
        self.assertEqual((ST.DEFAULTS["motor_zero_param"], ST.DEFAULTS["motor_zero_bit"]), ("2000", 1))
