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
        self.assertEqual(len(list((p.parent / K.BACKUP_DIR).glob("*.xlsx"))), 1)      # 日付つきの控え
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
    BASE = {"min_a": -0.6, "min_b": 24.0, "max_a": -1.5, "max_b": 75.0, "slope_h": 3.0, "slope_w": 5.0}

    def test_group_wise_not_field_wise(self):
        j = K.judgement({"MINA": "-0.8", "MAXB": "80", "傾きH": "4"}, self.BASE)
        self.assertEqual(j["min_a"], -0.8)            # バックラッシは行のまとまり（空の欄は0）
        self.assertEqual(j["min_b"], 0.0)             # 前の表の 24 を混ぜない
        self.assertEqual(j["max_b"], 80.0)
        self.assertEqual(j["slope_h"], 4.0)           # 傾きは行のまとまり
        self.assertNotIn("slope_w", j)                # 片方だけ → 書いていないほうは判定しない（前の表 5 を混ぜない）
        self.assertEqual(K.judgement({}, self.BASE), {k: v for k, v in self.BASE.items()})   # 全部空 → 前の表
        self.assertIsNone(K.judgement({}, None))
        self.assertIsNone(K.judgement({"MINA": "-0.8"}, None))                            # MAXB が無い → 使わない

    def test_unreadable_number_does_not_fall_back(self):
        p = make([{"型式": "J-2", "測定の種類": "セミ・モーター", "歯数": 60, "傾きH": "3,5", "MAXB": 80}])
        t = K.load(p)
        self.assertTrue(any("数字として読めません" in x for x in t["problems"]))
        r = K.resolve_judgement(t, "J-2", False, "セミ・モーター", "", self.BASE)
        self.assertNotIn("slope_h", r["judge"])       # 傾きのまとまりは使わず、前の表 3 にも落とさない
        self.assertEqual(r["judge"]["max_b"], 80.0)
        self.assertTrue(r["problems"])
        self.assertIn("使いません", r["source"]["slope"])

    def test_source_order_and_no_cross_sheet(self):
        p = make([{"型式": "J-3", "測定の種類": "", "傾きH": 6, "傾きW": 7},
                  {"型式": "J-3", "測定の種類": "セミ・モーター", "歯数": 60, "MAXB": 90}],
                 [{"型式": "J-3", "測定の種類": "", "刻み": 5}])
        t = K.load(p)
        r = K.resolve_judgement(t, "J-3", False, "セミ・モーター", "", self.BASE)
        self.assertEqual(r["source"], {"backlash": "この種類の登録", "slope": "種類なしの登録"})
        self.assertEqual(r["judge"]["slope_h"], 6.0)
        r2 = K.resolve_judgement(t, "J-3", True, "", "", self.BASE)     # 傾斜: 回転シートの値・合否判定.csv は使わない
        self.assertIsNone(r2["judge"])
        self.assertEqual(r2["source"]["slope"], "未登録")
        r3 = K.resolve_judgement(t, "NONE-1", False, "", "", self.BASE)  # どの行も無い回転 → 合否判定.csv
        self.assertEqual(r3["source"]["backlash"], "前の表（合否判定.csv）")

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


class TestReading(unittest.TestCase):
    def test_model_forms_hit_same_row(self):
        p = make([{"型式": "RB-250", "測定の種類": "セミ・モーター", "歯数": 60}])
        t = K.load(p)
        for form in ("RB-250", "ｒｂ－２５０", " rb-250 ", "RB‐250", "RB-250Ri", "rb-250ri"):
            self.assertEqual(K.find(t, form, False, "セミ・モーター")["歯数"], "60", form)
        rec, is_kind, via = K.find_via(t, "RB-250Ri", False, "セミ・モーター")
        self.assertIn("末尾の英字", via)

    def test_fullwidth_digits_and_spaces_readable(self):
        self.assertEqual(K._num("３.５"), 3.5)
        self.assertEqual(K._num(" 7 "), 7.0)

    def test_header_typos_and_unknown_columns(self):
        import openpyxl
        p = make([{"型式": "H-1", "測定の種類": "セミ・モーター", "歯数": 60, "傾きH": 4}])
        wb = openpyxl.load_workbook(p)
        ws = wb[K.SHEET_ROTARY]
        hdr = [c.value for c in ws[1]]
        ws.cell(1, hdr.index("傾きH") + 1).value = "傾き H"           # 打ち間違い（空白）
        ws.cell(1, len(hdr) + 1).value = "社内メモ"                  # 人が足した列
        wb.save(p)
        t = K.load(p)
        self.assertEqual(K.find(t, "H-1", False, "セミ・モーター")["傾きH"], "4")
        self.assertTrue(any("知らない列" in x and "社内メモ" in x for x in t["problems"]))

    def test_formula_without_value_is_reported(self):
        import openpyxl
        p = make([{"型式": "F-1", "測定の種類": "セミ・モーター", "歯数": 60}])
        wb = openpyxl.load_workbook(p)
        ws = wb[K.SHEET_ROTARY]
        hdr = [c.value for c in ws[1]]
        ws.cell(2, hdr.index("ホイール刻み") + 1).value = "=360/60/10"     # 保存しただけで値が無い式
        wb.save(p)
        t = K.load(p)
        self.assertTrue(any("数字として読めません" in x for x in t["problems"]))
        self.assertFalse(K.params(K.find(t, "F-1", False, "セミ・モーター"), False)["_ok"])      # 10° などに黙って落とさない

    def test_problems_for_model(self):
        p = make([{"型式": "A-1", "測定の種類": "セミ・モーター", "歯数": "abc"},
                  {"型式": "B-1", "測定の種類": "セミ・モーター", "歯数": 60}])
        t = K.load(p)
        self.assertTrue(K.problems_for(t, "A-1"))
        self.assertFalse(K.problems_for(t, "B-1"))
        self.assertTrue(K.problems_for(t))


class TestTiltJudgement(unittest.TestCase):
    def test_tilt_has_own_judgement_columns_and_no_inheritance(self):
        p = make([{"型式": "TW-9", "測定の種類": "", "ホイール刻み": 10, "MINA": -0.6, "傾きH": 3}])
        K.save_model(p, "TW-9", None, {"セミ・モーター": {"刻み": 2.5, "傾きH": 7}, "フル・スケール付き": {"刻み": 1}})
        t = K.load(p)
        self.assertEqual(K.judgement(K.find(t, "TW-9", True, "セミ・モーター"))["slope_h"], 7.0)
        self.assertIsNone(K.judgement(K.find(t, "TW-9", True, "フル・スケール付き")))       # 空 → 規格なし（回転を引き継がない）


class TestSaveInPlace(unittest.TestCase):
    def test_update_in_place_keeps_order_unknown_columns_formulas_and_validation(self):
        import openpyxl
        p = make([{"型式": "A-1", "測定の種類": "セミ・モーター", "歯数": 60},
                  {"型式": "Z-9", "測定の種類": "", "ホイール刻み": 1},
                  {"型式": "A-1", "測定の種類": "セミ・駆動装置", "ホイール刻み": 2}])
        wb = openpyxl.load_workbook(p)
        ws = wb[K.SHEET_ROTARY]
        hdr = [c.value for c in ws[1]]
        ws.cell(1, len(hdr) + 1).value = "社内メモ"
        ws.cell(2, len(hdr) + 1).value = "人が書いた"
        ws.cell(2, hdr.index("ホイール刻み") + 1).value = "=360/60/10"
        wb.save(p)
        K.save_model(p, "a-1", {"セミ・モーター": {"歯数": "72", "ブロック数": "4"},
                               "セミ・駆動装置": {"ホイール刻み": "2"}})
        wb = openpyxl.load_workbook(p)
        ws = wb[K.SHEET_ROTARY]
        hdr = [c.value for c in ws[1]]
        col = {h: i + 1 for i, h in enumerate(hdr)}
        self.assertEqual([ws.cell(r, 1).value for r in (2, 3, 4)], ["A-1", "Z-9", "A-1"])          # 行の順番はそのまま
        self.assertEqual(ws.cell(2, col["歯数"]).value, 72)
        self.assertEqual(ws.cell(2, col["ブロック数"]).value, 4)
        self.assertEqual(ws.cell(2, col["社内メモ"]).value, "人が書いた")                          # 人が足した列は残る
        self.assertEqual(ws.cell(2, col["ホイール刻み"]).value, "=360/60/10")                      # 式は消さない
        self.assertEqual(ws.max_row, 4)                                                            # 行は増えていない
        self.assertTrue(len(wb[K.SHEET_ROTARY].data_validations.dataValidation) >= 1)             # 一覧から選ぶ

    def test_delete_asks_and_can_stop(self):
        p = make([{"型式": "D-1", "測定の種類": "セミ・モーター", "歯数": 60},
                  {"型式": "D-1", "測定の種類": "セミ・駆動装置", "ホイール刻み": 2}])
        asked = []
        with self.assertRaises(RuntimeError):
            K.save_model(p, "D-1", {"セミ・モーター": {"歯数": "60"}}, confirm_delete=lambda n: (asked.append(n), False)[1])
        self.assertTrue(asked and "駆動装置" in asked[0][0])
        self.assertEqual(len(K.load(p)["rotary"]), 2)               # 止めたので何も消えていない
        K.save_model(p, "D-1", {"セミ・モーター": {"歯数": "60"}}, confirm_delete=lambda n: True)
        self.assertEqual(len(K.load(p)["rotary"]), 1)

    def test_backups_keep_ten(self):
        p = make([])
        for i in range(13):
            K.save_model(p, "B-1", {"セミ・モーター": {"歯数": str(60 + i)}})
        self.assertEqual(len(list((p.parent / K.BACKUP_DIR).glob("*.xlsx"))), K.BACKUP_KEEP)


class TestPlanInPlace(unittest.TestCase):
    def test_plan_rows_updated_in_place_and_cleared_with_confirm(self):
        import openpyxl
        p = make([])
        K.save_model(p, "P-1", None, None, {"回転": {"フルで測る": "両方"}, "傾斜": {"セミで測る": "分割だけ", "セミの種類": "モーター"}})
        K.save_model(p, "P-2", None, None, {"回転": {"フルで測る": "分割だけ"}})
        K.save_model(p, "P-1", None, None, {"回転": {"フルで測る": "分割だけ"}, "傾斜": {"セミで測る": "分割だけ", "セミの種類": "モーター"}})
        ws = openpyxl.load_workbook(p)[K.SHEET_PLAN]
        self.assertEqual([ws.cell(r, 1).value for r in (2, 3, 4)], ["P-1", "P-1", "P-2"])          # 並びはそのまま（P-2 は後ろに戻らない）
        asked = []
        K.save_model(p, "P-1", None, None, {"回転": {"フルで測る": "分割だけ"}}, confirm_delete=lambda n: (asked.append(n), True)[1])
        self.assertTrue(asked and "傾斜" in asked[0][0])
        t = K.load(p)
        self.assertEqual(K.find_plan(t, "P-1", False)["フルで測る"], "分割だけ")
        self.assertEqual(K.find_plan(t, "P-1", True)["フルで測る"], "分割だけ")               # 傾斜の行が無い → 回転と同じ
        self.assertEqual(K.find_plan(t, "P-2", False)["フルで測る"], "分割だけ")


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
