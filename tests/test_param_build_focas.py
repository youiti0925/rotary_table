# -*- coding: utf-8 -*-
"""FOCAS の控えを土台にした param_build.prepare（設計 §11.2 の 79〜82）と、個体データを0にする範囲。

既存の param_build / param_origin の振る舞いを変えていないことの固定も兼ねる
（既定の zero_scope="all" は今までどおり全軸）。
"""

import unittest
from pathlib import Path

from nd287_app import fanuc_param as F
from nd287_app import param_build as B
from nd287_app import param_origin as O

FIX = Path(__file__).resolve().parent / "fixtures" / "focas_param_31i.DAT"
ZERO = ("1850", "1851", "1852")


def focas_raw():
    return FIX.read_bytes().decode("cp932")


def with_individual(raw):
    """1850 に全軸の実測値を入れる（ほかの機械の個体データがある状態）。"""
    return raw.replace("N01850Q1A1P0A2P0A3P0A4P0A5P0A6P0",
                       "N01850Q1A1P11A2P22A3P33A4P44A5P55A6P66")


class TestFocasShapeIsClassifiedAsPc(unittest.TestCase):
    def test_79_focas_shape_is_judged_pc_made(self):
        # 今の動きの固定。変えたら（FOCAS の控えの扱いが変わるので）気づけるように
        info = O.classify(FIX.read_bytes())
        self.assertEqual(info["verdict"], "pc")


class TestPrepareOnFocasBackup(unittest.TestCase):
    VALUES = {"1420": "8000.000", "1815": "001*0000", "1821": "360000"}

    def test_80_only_the_changed_rows_change_and_shape_is_kept(self):
        raw = focas_raw()
        p = B.prepare(raw, self.VALUES, axis=4, reference="")
        self.assertEqual(p["problems"], [])
        new = p["newtext"].split("\n")
        old = raw.split("\n")
        self.assertEqual(len(new), len(old))                       # 行数
        changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
        self.assertEqual(len(changed), 3)
        self.assertEqual(sorted(F.param_number(new[i]) for i in changed), ["01420", "01815", "01821"])
        # 末尾空白・M/P・ほかの軸が保たれる
        by_no = {F.param_number(l): l for l in new if F.param_number(l)}
        self.assertTrue(by_no["01420"].endswith(" "))
        self.assertIn("A4P8000.0", by_no["01420"])
        self.assertIn("A1P6000.0", by_no["01420"])
        self.assertIn("A12P0.0", by_no["01420"])
        self.assertEqual(old[0], new[0])                           # 先頭の %
        self.assertTrue(p["newtext"].endswith("%"))
        self.assertEqual([(n, l) for n, l, _a, _b in F.diff(raw, p["newtext"])],
                         [("01420", "A4"), ("01815", "A4"), ("01821", "A4")])

    def test_81_only_a4_changes_in_a_row_that_has_a9_to_a12(self):
        raw = focas_raw()
        p = B.prepare(raw, {"1420": "8000.000"}, axis=4, reference="")
        old = {l[:6]: l for l in raw.split("\n")}
        new = {l[:6]: l for l in p["newtext"].split("\n")}
        a, b = old["N01420"], new["N01420"]
        self.assertEqual(a.replace("A4P20000.0", "A4P8000.0"), b)
        for k in ("A9P0.0", "A10P0.0", "A11P0.0", "A12P0.0"):
            self.assertIn(k, b)

    def test_82_empty_reference_does_not_call_machine_numbers_unknown(self):
        raw = focas_raw()
        self.assertIn("N09802", raw)
        p = B.prepare(raw, {"20": "4"}, axis=4, reference="")
        self.assertEqual([x for x in p["problems"] if "実機のバックアップに無い" in x], [])
        # 比べる相手（BASIC の和集合のようなもの）を渡すと、機械に実在する番号まで警告になる
        # ＝ FOCAS の控えを土台にするときに reference="" にする理由
        ref = "%\nN00020Q1P9 \nN00021Q1P0 \n%"
        q = B.prepare(raw, {"20": "4"}, axis=4, reference=ref)
        self.assertTrue([x for x in q["problems"] if "実機のバックアップに無い" in x])


class TestZeroScope(unittest.TestCase):
    def prep(self, scope, **kw):
        return B.prepare(with_individual(focas_raw()), {"20": "4"}, axis=4, reference="",
                         zero_params=ZERO, zero_scope=scope, **kw)

    def test_default_is_all_axes_like_before(self):
        p = B.prepare(with_individual(focas_raw()), {"20": "4"}, axis=4, reference="",
                      zero_params=ZERO)
        q = self.prep("all")
        self.assertEqual(p["newtext"], q["newtext"])
        self.assertEqual(p["rows"], q["rows"])
        zeros = [r for r in p["rows"] if r[4] == B.KIND_ZERO]
        self.assertEqual(len(zeros), 6)
        self.assertEqual({r[1] for r in zeros}, {"全軸"})

    def test_product_scope_zeroes_only_the_product_axis(self):
        p = self.prep("product")
        zeros = [r for r in p["rows"] if r[4] == B.KIND_ZERO]
        self.assertEqual([(r[0], r[2], r[3]) for r in zeros], [("1850(A4)", "44", "0")])
        self.assertEqual({r[1] for r in zeros}, {"製品の軸"})
        line = next(l for l in p["newtext"].split("\n") if l.startswith("N01850"))
        self.assertEqual(line, "N01850Q1A1P11A2P22A3P33A4P0A5P55A6P66A7P0A8P0A9P0A10P0A11P0A12P0")

    def test_product_scope_two_axis_table(self):
        raw = with_individual(focas_raw())
        p = B.prepare(raw, axis_values={4: {"20": "4"}, 5: {"21": "1"}}, reference="",
                      zero_params=ZERO, zero_scope="product")
        zeros = sorted(r[0] for r in p["rows"] if r[4] == B.KIND_ZERO)
        self.assertEqual(zeros, ["1850(A4)", "1850(A5)"])
        line = next(l for l in p["newtext"].split("\n") if l.startswith("N01850"))
        self.assertIn("A1P11", line)
        self.assertIn("A4P0", line)
        self.assertIn("A5P0", line)
        self.assertIn("A6P66", line)

    def test_product_scope_with_soft_limit_disable_keeps_working(self):
        p = self.prep("product", soft_limit="disable")
        self.assertEqual(p["problems"], [])

    def test_scope_changes_nothing_when_zero_params_is_empty(self):
        raw = with_individual(focas_raw())
        a = B.prepare(raw, {"20": "4"}, axis=4, reference="", zero_params=(), zero_scope="product")
        b = B.prepare(raw, {"20": "4"}, axis=4, reference="", zero_params=(), zero_scope="all")
        self.assertEqual(a["newtext"], b["newtext"])

    def test_unknown_scope_is_an_error(self):
        with self.assertRaises(ValueError):
            B.prepare(focas_raw(), {"20": "4"}, axis=4, reference="", zero_scope="some")

    def test_existing_helpers_default_to_all_axes(self):
        raw = with_individual(focas_raw())
        self.assertEqual(len(B.individual_zero_rows(raw, ZERO)), 6)
        self.assertEqual(len(B.individual_zero_rows(raw, ZERO, axes=[4])), 1)
        self.assertEqual(len(B.individual_zero_rows(raw, ZERO, axes=[])), 0)
        text, rows = B.zero_individual(raw, ZERO)
        self.assertEqual(len(rows), 6)
        text, rows = B.zero_individual(raw, ZERO, axes=[2, 3])
        self.assertEqual([r[1] for r in rows], ["A2", "A3"])
        self.assertEqual(len(B.preview_zero_rows(raw, ZERO, axes=[1])), 1)


if __name__ == "__main__":
    unittest.main()
