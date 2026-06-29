# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from nd287_app import xls_param as X

W = 9


def _row(*cells):
    r = list(cells) + [""] * (W - len(cells))
    return r[:W]


def grid_template_A():
    """テンプレA（NC ATT）相当。番地@0/設定値@3、番地@5/設定値@8、列1/6は '---'。"""
    return [
        _row("NC ATT.PARAMETER LIST (FANUC SERIAL)"),
        _row(" 型  式", "", "MZF-50010,ZS010"),
        _row(" 受注伝票番号", "", "50014470"),
        _row("", "", "", "", "", " GEAR RATE.(減速比)", "", "", "1/1"),
        _row("", "", "", "", "", " AXIS（軸）", "", "", "A-AXIS"),
        _row("", "", "", "", "", " MOTOR MODEL", "", "", "A06B-0495-B400"),
        _row("", "", "", "", "", "", "", "", "Dis500/250 (Liquid cooling)"),
        _row("番 地", "", "項  目", "設定値", "", "番 地", "", "項  目", "設定値"),
        _row("31iM", "---", "", "", "", "31iM", "---", "", ""),
        _row(1815.0, "---", "別置検出器", "*01*0000", "", 2084.0, "---", "ｷﾞﾔ分子", 36.0),
        _row("2300.0", "---", "DDﾓｰﾀ制御", "1***111*", "", 2020.0, "---", "ﾓｰﾀ型式", 563.0),
        _row(1825.0, "---", "位置ﾙｰﾌﾟｹﾞｲﾝ", 3000.0, "", 2085.0, "---", "ｷﾞﾔ分母", 5.0),
        _row("注意：PWE=1で入力のこと"),
        _row(9999.0, "---", "これは表外なので無視", 7.0),
    ]


class TestNormalize(unittest.TestCase):
    def test_is_addr(self):
        self.assertTrue(X.is_addr(1815.0))
        self.assertTrue(X.is_addr("1815"))
        self.assertTrue(X.is_addr("1815.0"))
        self.assertFalse(X.is_addr(999.0))      # 3桁未満は範囲外
        self.assertFalse(X.is_addr("---"))
        self.assertFalse(X.is_addr(""))

    def test_norm(self):
        self.assertEqual(X.norm_addr(1815.0), "1815")
        self.assertEqual(X.norm_addr("2300.0"), "2300")
        self.assertEqual(X.norm_value(563.0), "563")
        self.assertEqual(X.norm_value("*01*0000"), "*01*0000")  # ビット列保持
        self.assertEqual(X.norm_value(360.5), "360.5")


class TestParseTemplateA(unittest.TestCase):
    def setUp(self):
        self.s = X.parse_grid(grid_template_A(), "MZF50010")

    def test_meta(self):
        self.assertEqual(self.s["model"], "MZF-50010,ZS010")
        self.assertEqual(self.s["serial"], "50014470")
        self.assertEqual(self.s["motor"], "A06B-0495-B400")
        self.assertEqual(self.s["gear"], "1/1")
        self.assertEqual(self.s["axis"], "A-AXIS")
        self.assertEqual(self.s["cnc"], "31iM")
        self.assertEqual(self.s["kind"], "回転")    # A-AXIS → 回転
        self.assertTrue(self.s["dd"])              # 2300 #2=1

    def test_values_both_columns(self):
        v = self.s["values"]
        self.assertEqual(v["1815"], "*01*0000")
        self.assertEqual(v["2084"], "36")          # 右ブロック・float整数化
        self.assertEqual(v["2020"], "563")
        self.assertEqual(v["2300"], "1***111*")
        self.assertEqual(v["1825"], "3000")

    def test_notes_row_stops_table(self):
        # 「注意」以降(9999)は取り込まない
        self.assertNotIn("9999", self.s["values"])


class TestTemplateB_MH(unittest.TestCase):
    def test_no_value_header(self):
        g = [
            _row("NC PARAMETER LIST ( FANUC Series 31i )"),
            _row("", "", "", "", "MODEL", "MZF-50021"),
            _row("", "", "", "", "LOT NO.", "#67850"),
            _row("NO.", "項目/ITEM", "値/VALUE", "NO.", "項目/ITEM", "値/VALUE"),
            _row(1815.0, "別置検出器", "00000000", 2020.0, "ﾓｰﾀ型式", 262.0),
        ]
        s = X.parse_grid(g, "C-axis")
        self.assertEqual(s["serial"], "67850")     # # は除去
        self.assertEqual(s["values"]["1815"], "00000000")
        self.assertEqual(s["values"]["2020"], "262")


class TestDD(unittest.TestCase):
    def test_is_dd_by_2300_bit2(self):
        self.assertTrue(X.is_dd({"2300": "1***111*"}))
        self.assertTrue(X.is_dd({"2300": "0***011*"}))   # #2=1（先頭0でもDD）
        self.assertTrue(X.is_dd({"2300": "0****1**"}))
        self.assertFalse(X.is_dd({"2300": "00000000"}))  # #2=0 はDDでない

    def test_is_dd_by_motor_gear(self):
        self.assertTrue(X.is_dd({}, motor="Dis260/300"))
        self.assertTrue(X.is_dd({}, gear="1/1"))
        self.assertFalse(X.is_dd({}, motor="αiS2/5000", gear="1/36"))


class TestLoaderAndFind(unittest.TestCase):
    def _make_xlsx(self, path, serial="50014470"):
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "P"
        for r in grid_template_A():
            ws.append(list(r))
        # serial を差し替え
        ws["C3"] = serial
        wb.save(path)

    def test_read_xlsx_and_find(self):
        d = tempfile.mkdtemp()
        sub = Path(d, "RTV", "RTV-313"); sub.mkdir(parents=True)
        self._make_xlsx(str(sub / "20240101_RTV-313 50014470 客先.xlsx"))
        # OLD配下は無視されること
        old = Path(d, "RTV", "OLD"); old.mkdir(parents=True)
        self._make_xlsx(str(old / "old_50014470.xlsx"), serial="50014470")
        got = X.find_custom_files(d, "50014470")
        self.assertEqual(len(got), 1)              # OLDは除外で1件
        sheets = got[0]["sheets"]
        self.assertEqual(sheets[0]["model"], "MZF-50010,ZS010")
        self.assertEqual(sheets[0]["values"]["2300"], "1***111*")
        self.assertTrue(sheets[0]["dd"])
        self.assertEqual(X.find_custom_files(d, "99999"), [])


if __name__ == "__main__":
    unittest.main()
