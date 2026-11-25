# -*- coding: utf-8 -*-
import unittest
from nd287_app.serial_suffix import suffix, file_stem


class T(unittest.TestCase):
    def test_table_examples(self):
        self.assertEqual(file_stem("123456", motor="driver", kinds=["rotary", "close"]), "123456KRC")
        self.assertEqual(file_stem("123456", motor="driver", kinds=["multi", "fixed"], multi_axis=1), "123456KMF")
        self.assertEqual(file_stem("123456", motor="driver", ng=1), "123456K-X1")

    def test_rows(self):
        self.assertEqual(suffix("driver"), "K")
        self.assertEqual(suffix("driver", True), "KG")
        self.assertEqual(suffix("normal"), "")
        self.assertEqual(suffix("normal", True), "G")
        self.assertEqual(suffix("temp"), "I")
        self.assertEqual(suffix("temp", True), "IG")

    def test_columns_and_order(self):
        self.assertEqual(suffix("driver", kinds=["close", "rotary"]), "KRC")        # 並べ替わる（表の列の順）
        self.assertEqual(suffix("temp", True, ["rotary", "close"]), "IGRC")
        self.assertEqual(suffix("normal", kinds=["tilt_set_after"]), "DTY")
        self.assertEqual(suffix("driver", True, ["multi"], multi_axis=3), "KG3")
        self.assertEqual(suffix("normal", kinds=["semi"]), "")                      # セミクロは文字なし

    def test_ng_and_errors(self):
        self.assertEqual(suffix("temp", ng=3), "I-X3")
        with self.assertRaises(ValueError):
            suffix("x")
        with self.assertRaises(ValueError):
            suffix("normal", kinds=["nope"])


if __name__ == "__main__":
    unittest.main()
