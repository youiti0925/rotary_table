# -*- coding: utf-8 -*-
import unittest
from nd287_app import serial_suffix as S
from nd287_app.serial_suffix import suffix, file_stem, cell

# 写真の表を、1行ずつ書き写したもの（行: (モーター, クランプ)）。列: semi, T, R, M(1〜4), F, S, U, E, C, TY, RY, DTY, DRY
PHOTO = {
    ("driver", False): "K KT KR KM,K2,K3,K4 KF KS KU KE KC KTY KRY KDTY KDRY",
    ("driver", True): "KG KGT KGR KGM,KG2,KG3,KG4 KGF KGS KGU KGE KGC - - - -",
    ("normal", False): "- TT RR M,2,3,4 F S U E C TY RY DTY DRY",
    ("normal", True): "G GT GR GM,G2,G3,G4 GF GS GU GE GC GTY GRY GDTY GDRY",
    ("temp", False): "I IT IR IM,I2,I3,I4 IF IS IU IE IC ITY IRY IDTY IDRY",
    ("temp", True): "IG IGT IGR IGM,IG2,IG3,IG4 IGF IGS IGU IGE IGC IGTY IGRY IGDTY IGDRY",
}
COLS = ["semi", "tilt", "rotary", "multi", "fixed", "move", "repair_in", "repair_out", "close",
        "tilt_set", "rotary_set", "tilt_set_after", "rotary_set_after"]


class T(unittest.TestCase):
    def test_every_cell_matches_the_photo(self):
        for (motor, clamp), text in PHOTO.items():
            for kind, want in zip(COLS, text.split()):
                if want == "-" and kind == "semi":
                    self.assertEqual(cell(motor, clamp, "semi"), "")          # 正規・アンクランプのセミクロ=ナシ
                elif want == "-":
                    with self.assertRaises(ValueError):
                        cell(motor, clamp, kind)                              # 斜線（該当なし）
                elif kind == "multi":
                    for n, w in enumerate(want.split(","), 1):
                        self.assertEqual(cell(motor, clamp, "multi", n), w)
                else:
                    self.assertEqual(cell(motor, clamp, kind), want, (motor, clamp, kind))

    def test_semi_has_a_letter_for_every_row_except_plain_normal(self):
        self.assertEqual([suffix(m, c) for m, c in PHOTO],
                         ["K", "KG", "", "G", "I", "IG"])                      # 「セミクロは何も付かない」は正規・アンクランプだけ

    def test_close_is_C_per_row(self):
        self.assertEqual([suffix(m, c, ["close"]) for m, c in PHOTO],
                         ["KC", "KGC", "C", "GC", "IC", "IGC"])

    def test_table_examples(self):
        self.assertEqual(file_stem("123456", motor="driver", kinds=["rotary", "close"]), "123456KRC")
        self.assertEqual(file_stem("123456", motor="driver", kinds=["multi", "fixed"], multi_axis=1), "123456KMF")
        self.assertEqual(file_stem("123456", motor="driver", ng=1), "123456K-X1")
        self.assertEqual(suffix("temp", True, ["rotary", "close"]), "IGRC")
        self.assertEqual(suffix("driver", kinds=["close", "rotary"]), "KRC")      # 列の順に並べ直す

    def test_not_applicable_stops_with_reason(self):
        with self.assertRaises(ValueError) as e:
            suffix("driver", True, ["tilt_set"])
        self.assertIn("斜線", str(e.exception))

    def test_ng_and_errors(self):
        self.assertEqual(suffix("temp", ng=3), "I-X3")
        with self.assertRaises(ValueError):
            suffix("x")
        with self.assertRaises(ValueError):
            suffix("normal", kinds=["nope"])


class TestSelection(unittest.TestCase):
    def test_press_order_does_not_matter(self):
        a = S.Selection("driver")
        a.press("driver", False, "close")
        a.press("driver", False, "rotary")
        self.assertEqual(a.suffix(), "KRC")                      # 押した順ではなく表の左から
        a.press("driver", False, "close")
        self.assertEqual(a.suffix(), "KR")                       # もう一度押すと外れる
        a.press("driver", False, "semi")
        self.assertEqual(a.suffix(), "K")

    def test_row_switch_drops_na_and_keeps_valid(self):
        a = S.Selection("driver", False, ["tilt_set", "close"])
        a.press("driver", True, "rotary")                        # クランプ行へ: 斜線の完組は外れる
        self.assertEqual(a.suffix(), "KGRC")
        with self.assertRaises(ValueError):
            a.press("driver", True, "tilt_set")                  # 斜線は押せない
        self.assertTrue(a.is_on("driver", True, "close"))
        self.assertFalse(a.is_on("driver", False, "close"))

    def test_multi_axis_and_ng(self):
        a = S.Selection("driver", False, ["fixed"], ng=2)
        a.press("driver", False, "multi", 1)
        self.assertEqual(a.suffix(), "KMF-X2")                   # 表の例 KMF
        a.press("driver", False, "multi", 3)
        self.assertEqual(a.suffix(), "K3F-X2")
        self.assertTrue(a.is_on("driver", False, "semi") is False)

    def test_normal_row_semi_is_empty(self):
        a = S.Selection("normal")
        self.assertEqual(a.suffix(), "")
        self.assertTrue(a.is_on("normal", False, "semi"))


if __name__ == "__main__":
    unittest.main()
