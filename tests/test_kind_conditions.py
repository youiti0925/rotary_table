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


class TestSave(unittest.TestCase):
    def test_save_model_roundtrip_keeps_others(self):
        p = make([{"型式": "RTV-231", "測定の種類": "", "ホイール刻み": 10},
                  {"型式": "OTHER", "測定の種類": "セミ・モーター", "歯数": 60}])
        n = K.save_model(p, "RTV-231",
                         rotary_by_kind={"セミ・モーター": {"歯数": 72, "ブロック数": 4, "回数": 7},
                                         "セミ・駆動装置": {"ホイール刻み": 1},
                                         "フル・スケール付き": {}},
                         plan={"フルで測る": "両方", "セミで測る": "分割だけ", "セミの種類": "セミ・モーター"})
        self.assertEqual(n, 3)
        t = K.load(p)
        self.assertEqual(t["problems"], [])
        self.assertEqual(K.find(t, "RTV-231", False, "セミ・モーター")["歯数"], "72")
        self.assertEqual(K.find(t, "OTHER", False, "セミ・モーター")["歯数"], "60")        # ほかの型式はそのまま
        self.assertEqual(K.find(t, "RTV-231", False, "フル・スケール付き")["ホイール刻み"], "10")   # 種類なしの行は残る
        self.assertEqual(K.find_plan(t, "rtv-231")["セミで測る"], "分割だけ")
        self.assertTrue(Path(str(p) + ".bak").exists())
        # 2回目: 同じ型式の種類の行は置き換わり、増えない
        K.save_model(p, "RTV-231", rotary_by_kind={"セミ・モーター": {"歯数": 80}}, plan={})
        t = K.load(p)
        self.assertEqual(K.find(t, "RTV-231", False, "セミ・モーター")["歯数"], "80")
        self.assertEqual(len([r for r in t["rotary"] if r["型式"] == "RTV-231"]), 2)       # 種類なし＋セミ・モーター
        self.assertIsNone(K.find_plan(t, "RTV-231"))                                       # 予定を空で保存 → 消える


class TestTwoAxis(unittest.TestCase):
    def test_rotary_and_tilt_times_three_kinds_in_one_save(self):
        """2軸テーブル: 1つの型式に、回転・傾斜それぞれ3種類を、一度に保存して読める。"""
        p = make([])
        rot = {"セミ・モーター": {"歯数": 72}, "セミ・駆動装置": {"ホイール刻み": 1}, "フル・スケール付き": {"スケール名": "A", "ホイール刻み": 5}}
        tilt = {"セミ・モーター": {"刻み": 2.5}, "セミ・駆動装置": {"刻み": 5}, "フル・スケール付き": {"スケール名": "A", "刻み": 1}}
        n = K.save_model(p, "TW-1", rot, tilt, {"フルで測る": "両方", "セミで測る": "分割だけ", "セミの種類": "セミ・モーター"})
        self.assertEqual(n, 7)                                              # 回転3＋傾斜3＋予定1
        t = K.load(p)
        self.assertEqual(t["problems"], [])
        for kind, want in (("セミ・モーター", "2.5"), ("セミ・駆動装置", "5"), ("フル・スケール付き", "1")):
            self.assertEqual(K.find(t, "TW-1", True, kind, "A")["刻み"], want)       # 傾斜
        self.assertEqual(K.find(t, "TW-1", False, "セミ・モーター")["歯数"], "72")      # 回転
        self.assertEqual(K.find(t, "TW-1", False, "フル・スケール付き", "A")["ホイール刻み"], "5")
        # 傾斜だけ書き直しても、回転は消えない（by_kind=None は触らない）
        K.save_model(p, "TW-1", None, {"セミ・モーター": {"刻み": 3}})
        t = K.load(p)
        self.assertEqual(K.find(t, "TW-1", True, "セミ・モーター")["刻み"], "3")
        self.assertEqual(K.find(t, "TW-1", False, "セミ・モーター")["歯数"], "72")


class TestPlanPerAxis(unittest.TestCase):
    def test_plan_rotary_and_tilt_separately_with_fallback(self):
        p = make([])
        K.save_model(p, "TW-2", None, None, {"回転": {"フルで測る": "両方", "セミで測る": "分割だけ"},
                                              "傾斜": {"フルで測る": "分割だけ"}})
        t = K.load(p)
        self.assertEqual(K.find_plan(t, "TW-2", False)["セミで測る"], "分割だけ")
        self.assertEqual(K.find_plan(t, "TW-2", True)["フルで測る"], "分割だけ")
        self.assertEqual(K.find_plan(t, "TW-2", True)["セミで測る"], "")
        # 傾斜の行が空なら回転と同じ
        K.save_model(p, "TW-3", None, None, {"回転": {"フルで測る": "両方"}})
        t = K.load(p)
        self.assertEqual(K.find_plan(t, "TW-3", True)["フルで測る"], "両方")
        self.assertIsNone(K.find_plan(t, "NONE", True))


class TestPickKind(unittest.TestCase):
    def test_pick(self):
        from nd287_app import kind_runtime as KR
        M, D, F = "セミ・モーター", "セミ・駆動装置", "フル・スケール付き"
        self.assertEqual(KR.pick_kind([M]), M)                       # 1つだけ → 自動
        self.assertEqual(KR.pick_kind([M, D, F], "フル"), F)         # 機械がフル
        self.assertEqual(KR.pick_kind([M, F], "セミ"), M)
        self.assertEqual(KR.pick_kind([M, D, F], "セミ", D), D)      # セミが2つ → 前回
        self.assertEqual(KR.pick_kind([M, D, F], "セミ"), "")        # 前回も無い → 人が押す
        self.assertEqual(KR.pick_kind([M, D, F], None, D), D)        # つながっていない → 前回
        self.assertEqual(KR.pick_kind([M, D, F]), "")
        self.assertEqual(KR.pick_kind([]), "")


class TestRowsFromExisting(unittest.TestCase):
    def test_same_model_twice_gives_one_row(self):
        m = {"user_rotary": {"a": {"型式": "RB-250", "ホイール刻み": 10}, "b": {"型式": "rb-250", "ホイール刻み": 5}}}
        rot, _ = K.rows_from_existing(m)
        self.assertEqual(len(rot), 1)


class TestJudgement(unittest.TestCase):
    def test_kind_values_and_fallback_to_csv(self):
        base = {"min_a": -0.6, "min_b": 24.0, "max_a": -1.5, "max_b": 75.0, "slope_h": 3.0, "slope_w": 5.0}
        j = K.judgement({"MINA": "-0.8", "傾きH": "4"}, base)
        self.assertEqual(j["min_a"], -0.8)            # 種類の行の値
        self.assertEqual(j["slope_h"], 4.0)
        self.assertEqual(j["max_b"], 75.0)            # 空欄は合否判定.csv の値
        self.assertEqual(K.judgement({}, base)["min_a"], -0.6)
        self.assertIsNone(K.judgement({}, None))

    def test_save_adds_missing_columns_and_roundtrip_and_copy_from_csv(self):
        import openpyxl
        p = make([])
        wb = openpyxl.load_workbook(p)                  # 以前の形式（合否の列なし）に戻す
        ws = wb[K.SHEET_ROTARY]
        ws.delete_cols(ws.max_column - 6, 7); ws.append([]); wb.save(p)
        K.save_model(p, "J-1", {"セミ・モーター": {"歯数": 60, "MINA": -0.7, "MAXB": 80}})
        t = K.load(p)
        r = K.find(t, "J-1", False, "セミ・モーター")
        self.assertEqual(K.judgement(r)["min_a"], -0.7)
        self.assertEqual(K.judgement(r)["max_b"], 80.0)
        m = {"user_rotary": {"x": {"型式": "RB-250", "ホイール刻み": 10}},
             "judgement": {"RB-250": {"min_a": -0.6, "min_b": 24.0, "max_a": -1.5, "max_b": 75.0, "slope_h": 3.0, "slope_w": 5.0}}}
        rot, _ = K.rows_from_existing(m)
        self.assertEqual(rot[0]["MINA"], -0.6)
        self.assertEqual(rot[0]["傾きW"], 5.0)


class TestDialog(unittest.TestCase):
    def test_dialog_save_and_reload(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from nd287_app import kind_conditions_ui as U
        d = tempfile.mkdtemp()
        st = {U.SETTING_KEY: str(Path(d) / K.FILE_NAME)}
        dlg = U.KindConditionDialog(None, st, "RTV-231")
        self.assertFalse(dlg.save() is False)                   # ファイルが無くても作って保存できる
        dlg.g_rot.item(U.ROTARY_FIELDS.index("歯数"), 0).setText("72")
        dlg.g_rot.item(U.ROTARY_FIELDS.index("ブロック数"), 0).setText("4")
        dlg.g_rot.item(U.ROTARY_FIELDS.index("回数"), 0).setText("7")
        dlg.cmb_full.setCurrentText("両方")
        dlg.cmb_semi.setCurrentText("分割だけ")
        self.assertTrue(dlg.save())
        dlg2 = U.KindConditionDialog(None, st, "RTV-231")
        self.assertEqual(dlg2.g_rot.item(U.ROTARY_FIELDS.index("歯数"), 0).text(), "72")
        self.assertEqual(dlg2.cmb_semi.currentText(), "分割だけ")
        t = K.load(st[U.SETTING_KEY])
        self.assertEqual(K.repeat(K.find(t, "RTV-231", False, "セミ・モーター"))["repeats"], 7)


if __name__ == "__main__":
    unittest.main()
