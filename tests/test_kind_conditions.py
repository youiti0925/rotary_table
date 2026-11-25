# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from nd287_app import kind_conditions as K


def make(rows_rot, rows_tilt=()):
    d = tempfile.mkdtemp()
    p = Path(d) / K.FILE_NAME
    K.build_template(p, rows_rot, rows_tilt)
    return p


class T(unittest.TestCase):
    def test_find_kind_then_blank_fallback(self):
        p = make([{"型式": "RTV-231", "測定の種類": "", "ホイール刻み": 10},
                  {"型式": "RTV-231", "測定の種類": "セミ・モーター", "歯数": 72},
                  {"型式": "RTV-231", "測定の種類": "フル・スケール付き", "スケール名": "A", "ホイール刻み": 5}])
        t = K.load(p)
        self.assertEqual(t["problems"], [])
        self.assertEqual(K.find(t, "RTV-231", False, "セミ・モーター")["歯数"], "72")
        self.assertEqual(K.find(t, "RTV-231", False, "セミ・駆動装置")["ホイール刻み"], "10")   # 種類なしの行へ
        self.assertEqual(K.find(t, "RTV-231", False, "フル・スケール付き", "A")["ホイール刻み"], "5")
        self.assertIsNone(K.find(t, "NOPE", False, "セミ・モーター"))
        self.assertEqual(K.kinds_of(t, "RTV-231", False), ["セミ・モーター", "フル・スケール付き"])

    def test_motor_pitch_is_tooth_divided_by_10(self):
        p = make([{"型式": "X-1", "測定の種類": "セミ・モーター", "歯数": 72}])
        rec = K.find(K.load(p), "X-1", False, "セミ・モーター")
        pr = K.params(rec, False)
        self.assertAlmostEqual(pr["wheel_pitch"], 0.5)                 # 360/72/10
        self.assertTrue(pr["_ok"])

    def test_drive_without_pitch_is_not_ok(self):
        p = make([{"型式": "X-1", "測定の種類": "セミ・駆動装置"}])
        pr = K.params(K.find(K.load(p), "X-1", False, "セミ・駆動装置"), False)
        self.assertFalse(pr["_ok"])                                    # 黙って10°にしない

    def test_repeat_and_bad_kind_and_duplicates(self):
        p = make([{"型式": "RTV-231", "測定の種類": "セミ・モーター", "歯数": 72, "ブロック数": 4, "回数": 7},
                  {"型式": "RTV-231", "測定の種類": "セミ・モーター", "歯数": 72},
                  {"型式": "Y", "測定の種類": "でたらめ"}])
        t = K.load(p)
        self.assertEqual(len(t["problems"]), 2)
        self.assertEqual(K.repeat(K.find(t, "RTV-231", False, "セミ・モーター"))["repeats"], 7)

    def test_repeat_differs_per_kind(self):
        p = make([{"型式": "RTV-231", "測定の種類": "セミ・モーター", "歯数": 72, "ブロック数": 4, "回数": 7},
                  {"型式": "RTV-231", "測定の種類": "セミ・駆動装置", "ホイール刻み": 1, "ブロック数": 2, "回数": 3},
                  {"型式": "RTV-231", "測定の種類": "フル・スケール付き", "スケール名": "A", "ホイール刻み": 5}])
        t = K.load(p)
        r = lambda k: K.repeat(K.find(t, "RTV-231", False, k, "A" if k.startswith("フル") else ""))
        self.assertEqual((r("セミ・モーター")["blocks_n"], r("セミ・モーター")["repeats"]), (4, 7))
        self.assertEqual((r("セミ・駆動装置")["blocks_n"], r("セミ・駆動装置")["repeats"]), (2, 3))
        self.assertIsNone(r("フル・スケール付き"))            # 再現性の欄が空 → 再現性は作らない（他の種類の値を使わない）

    def test_missing_file_is_empty_and_no_overwrite(self):
        self.assertEqual(K.load("/nonexistent/x.xlsx")["rotary"], [])
        p = make([])
        with self.assertRaises(FileExistsError):
            K.build_template(p)

    def test_rows_from_existing(self):
        from nd287_app import masters
        m = masters.load_all({}) if hasattr(masters, "load_all") else {}
        rot, tilt = K.rows_from_existing(m or {})
        for r in rot:
            self.assertEqual(r["測定の種類"], "")


if __name__ == "__main__":
    unittest.main()
