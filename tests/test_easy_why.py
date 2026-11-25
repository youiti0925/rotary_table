"""はじめてモードの「なぜ、これをするのか」のカード（easy_why.py）。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nd287_app import easy_flow as F, easy_why, easy_words


class TestWhy(unittest.TestCase):
    def test_cards_shape_and_steps_exist(self):
        import nd287_app.easy_art as A
        for cid, c in easy_why.CARDS.items():
            self.assertTrue(c["title"].startswith("なぜ"), cid)
            self.assertLessEqual(len(c["lines"]), 3, cid)
            self.assertTrue(callable(getattr(A, c["art"])), cid)
            getattr(A, c["art"])()                                  # 引数なしで描ける絵だけ使う
        for sid, ids in easy_why.FOR_STEP.items():
            self.assertIn(sid, F.INDEX)
            for cid in ids:
                self.assertIn(cid, easy_why.CARDS)

    def test_why_comes_first_and_old_cards_stay(self):
        self.assertEqual(easy_words.cards_for("measuring")[:3], ("why_no_touch", "why_both", "why_worm"))
        self.assertIn("value_in", easy_words.cards_for("measuring"))           # 前からあるカードは残る
        self.assertEqual(easy_words.cards_for("start1"), ("why_first", "capture"))
        self.assertEqual(easy_words.cards_for("estop_press"), ("why_estop",))
        self.assertNotIn("why_estop", easy_words.ALL)                          # ことばの一覧には混ぜない
        self.assertEqual(easy_words.cards_for("home"), easy_words.ALL)

    def test_no_guess_words(self):
        text = "".join(x for c in easy_why.CARDS.values() for x in c["lines"])
        for word in ("はずです", "と思われ", "かもしれ"):
            self.assertNotIn(word, text)

    def test_dialog_opens_on_the_why_card(self):
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from nd287_app.easy_mode import WordsDialog
        d = WordsDialog(None, easy_words.cards_for("measuring"))
        self.assertEqual(d.lbl_t.text(), "なぜ、さわってはいけないのか")
        d.show_card("why_both")
        self.assertIn("バックラッシ", d.lbl_l.text())
        d.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
