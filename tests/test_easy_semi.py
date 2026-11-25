# -*- coding: utf-8 -*-
"""はじめてモード「機械に入れる」の「位置を読む検出器: 製品どおり／セミクロにする」。

出すのはフルクロの製品だけ・既定は毎回「製品どおり」。変換は、かんたん作成の semi_convert を同じ順で呼ぶ
（セミクロ変換 → 作成時オプション → 仮モーターなら 2020 を外す → DGPR → prepare）。変換した値は製品の履歴(DB)に入れない。
偽物の機械につなぐ（実機には触らない）。
"""
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from nd287_app import easy_machine_in as M                     # noqa: E402
from nd287_app import focas_ops as O                            # noqa: E402
from nd287_app import fanuc_param                              # noqa: E402
from test_easy_machine_in import PanelBase, TestPanel          # noqa: E402
from test_wizard_machine_write import PRODUCT                  # noqa: E402


def setUpModule():
    O.set_estop_mode("released_only")


def tearDownModule():
    O.set_estop_mode("estop_only")                             # 他の試験の「既定は estop_only」を壊さない


def full_product(v1815="00100010", cmr="2", extra=None):
    """50014984 の製品（回転 400V 40A・減速比 1/180）に、フルクロの値を足したもの。"""
    vals = {"1013": "00000000", "1260": "360.000", "1820": cmr, "1815": v1815, "1821": "45000",
            "2000": "***1***0", "2018": "*******1", "2023": "8192", "2024": "69", "2084": "9",
            "2085": "25", "2209": "*1******"}
    vals.update(extra or {})
    rows = "".join(f'"{k}","A5","","","\'{v}",""\n' if set(v) <= set("01*") and len(v) == 8
                   else f'"{k}","A5","","","{v}",""\n' for k, v in vals.items())
    return PRODUCT + rows


class SemiBase(PanelBase):
    def setUp(self):
        super().setUp()
        self.prod = Path(self.settings["param_product_dir"])
        self.write_product(full_product())

    def write_product(self, text):
        (self.prod / "R50014984.prm").write_text(text, encoding="cp932")

    def order(self, p, seiban="50014984"):
        p.start()
        p.e_seiban.setText(seiban)
        p._search()
        self.assertTrue(p.products)

    def combos(self, p):
        from PySide6 import QtWidgets
        return [p.extra.itemAt(i).layout().itemAt(1).widget() for i in range(p.extra.count())
                if p.extra.itemAt(i).layout() is not None
                and isinstance(p.extra.itemAt(i).layout().itemAt(1).widget(), QtWidgets.QComboBox)]

    def texts(self, p):
        from PySide6 import QtWidgets
        out = []

        def walk(lay):
            for i in range(lay.count()):
                it = lay.itemAt(i)
                if it.widget() is not None and isinstance(it.widget(), (QtWidgets.QLabel, QtWidgets.QPushButton)):
                    out.append(it.widget().text())
                elif it.layout() is not None:
                    walk(it.layout())
        walk(p.extra)
        return "\n".join(out + [p.lbl_text.text()])

    def to_decide(self, p, choose=True, unit="23"):
        self.order(p)
        if choose:
            self.combos(p)[0].setCurrentIndex(1)
        p._main()
        TestPanel.pick(self, p, unit)
        p._main()
        p._read()
        self.assertTrue(p.rd["ok"], p.rd)
        p._main()
        self.assertEqual(p.page, "decide")


class TestSemiChoice(SemiBase):
    def test_shown_only_for_a_full_closed_product_and_default_is_product(self):
        p = self.panel()
        self.order(p)
        self.assertEqual(len(self.combos(p)), 1)
        c = self.combos(p)[0]
        self.assertEqual(c.currentIndex(), 0)
        self.assertEqual(c.itemText(0), "製品どおり")
        self.assertIn("セミクロにする", c.itemText(1))
        self.assertEqual(p.semi, [False])
        self.assertIn("位置を読む検出器", self.texts(p))

    def test_not_shown_for_a_semi_closed_product(self):
        self.write_product(full_product(v1815="00100000"))
        p = self.panel()
        self.order(p)
        self.assertEqual(self.combos(p), [])
        self.assertNotIn("位置を読む検出器", self.texts(p))

    def test_not_shown_for_a_product_without_1815(self):
        self.write_product(PRODUCT)
        p = self.panel()
        self.order(p)
        self.assertEqual(self.combos(p), [])

    def test_default_returns_to_product_every_time(self):
        p = self.panel()
        self.order(p)
        self.combos(p)[0].setCurrentIndex(1)
        self.assertEqual(p.semi, [True])
        p.e_seiban.setText("50014984")
        p._search()                                              # 探し直すと、また「製品どおり」
        self.assertEqual(p.semi, [False])
        self.assertEqual(self.combos(p)[0].currentIndex(), 0)

    def test_choosing_semi_searches_pairs_once_and_shows_the_basis(self):
        p = self.panel()
        self.order(p)
        calls = []
        real = p.planner.find_pairs
        p.planner.find_pairs = lambda i: (calls.append(i), real(i))[1]
        self.combos(p)[0].setCurrentIndex(1)
        self.assertEqual(calls, [0])
        self.assertFalse(p._semi_busy)
        self.assertIn("根拠: 計算式", self.texts(p))              # 同じ型式の組は無い → 計算式
        self.combos(p)[0].setCurrentIndex(0)
        self.combos(p)[0].setCurrentIndex(1)
        self.assertEqual(calls, [0])                              # 2回目は探し直さない

    def test_pair_in_the_product_folder_is_the_basis(self):
        (self.prod / "R50000008.prm").write_text(full_product(extra={"1850": "5"}), encoding="cp932")
        (self.prod / "R50000009.prm").write_text(full_product(
            v1815="00100000", extra={"1821": "2000", "2000": "***0***0", "2024": "12500", "2084": "1", "2085": "500",
                                     "2209": "*0******", "2018": "", "1850": "9"}), encoding="cp932")
        p = self.panel()
        self.order(p)
        self.combos(p)[0].setCurrentIndex(1)
        self.assertIn("根拠: 同じ型式の組 1組", self.texts(p))

    def test_impossible_conversion_shows_the_reason_and_cannot_be_chosen(self):
        self.write_product(full_product(cmr="4"))
        p = self.panel()
        self.order(p)
        c = self.combos(p)[0]
        self.assertFalse(c.model().item(1).isEnabled())
        self.assertIn("CMR", self.texts(p))
        self.assertIn("セミクロにはできません", self.texts(p))
        self.assertFalse(p.semi_on(0))


class TestSemiPlan(SemiBase):
    def plan_values(self, p):
        """書く一覧（job）の新しい値を、軸ごとに。"""
        return {(it["no"], it["label"]): it["new"] for it in p.job["items"]}

    def test_semi_plan_equals_the_wizards_own_semi_plan_for_the_same_product(self):
        """同じ製品を、かんたん作成の窓でセミクロにして作った plan と、この画面の plan が同じ値になる。"""
        p = self.panel()
        self.to_decide(p)
        self.assertIsNotNone(p.job, p.lbl_text.text())
        self.assertEqual([e["applied"] for e in p.semi_report], [True])
        # 同じ product を、かんたん作成の窓（同じ planner の窓）の cmb_semi でセミクロにして作る
        w = p.planner.w
        w.cmb_semi.setCurrentIndex(1)
        w._semi_pending = False
        import nd287_app.gui as G
        with mock.patch.object(G.QtWidgets.QMessageBox, "question", lambda *a, **k: G.QtWidgets.QMessageBox.Yes), \
                mock.patch.object(G.QtWidgets.QMessageBox, "warning", lambda *a, **k: None):
            (_s, wiz_fn), _kw = w._machine_job()
        raw = p.job and Path(p.job["backup_folder"], "パラメータ.DAT").read_bytes().decode("cp932", "replace")
        ours = p.planner.build(p.seiban, p.unit, p.rows, semi=[True])
        a = ours(raw, keep_motor=True)
        b = wiz_fn(raw, keep_motor=True)
        self.assertEqual(a["newtext"], b["newtext"])
        self.assertEqual(a["axis_values"], b["axis_values"])

    def test_converted_values_reach_the_plan(self):
        p = self.panel()
        self.to_decide(p)
        vals = self.plan_values(p)
        ax = "A" + str(1 + "XYZABC".index(p.rows[0]["axis"]))
        self.assertEqual(vals.get((1815, ax)), "00100000")         # 別置検出器のビット(#1)を 0 に（製品は 00100010）
        t = p.job["newtext"]
        n = 1 + "XYZABC".index(p.rows[0]["axis"])
        self.assertEqual(fanuc_param.value_on_axis(t, 1815, n)[-2], "0")
        self.assertIn("セミクロに変換して書きます（根拠: 計算式）", self.texts(p))
        self.assertTrue(p.job["semi"])

    def test_product_as_is_keeps_full_values_and_has_no_semi_text(self):
        p = self.panel()
        self.to_decide(p, choose=False)
        self.assertIsNotNone(p.job, p.lbl_text.text())
        self.assertEqual(p.semi_report, [])
        vals = self.plan_values(p)
        ax = "A" + str(1 + "XYZABC".index(p.rows[0]["axis"]))
        self.assertEqual(vals.get((1815, ax)), "00100010")         # 製品どおり（フルクロのまま）
        self.assertFalse(p.job["semi"])
        self.assertNotIn("セミクロに変換", self.texts(p))

    def test_conversion_runs_before_motor_drop_and_prepare_in_the_wizard_order(self):
        """順番: セミクロ変換 → 作成時オプション → 2020 を外す → DGPR・prepare（plan）。"""
        import nd287_app.gui as G
        from nd287_app import semi_convert
        seen = []
        real_conv = semi_convert.convert
        real_opts = G.param_build.with_servo_options
        real_drop = G.drop_param_numbers
        real_plan = G.ParamWizardDialog._plan

        def conv(*a, **k):
            seen.append("convert")
            return real_conv(*a, **k)

        def opts(*a, **k):
            seen.append("options")
            return real_opts(*a, **k)

        def drop(*a, **k):
            seen.append("drop2020")
            return real_drop(*a, **k)

        def plan(self_, *a, **k):
            seen.append("plan")
            return real_plan(self_, *a, **k)
        p = self.panel()
        with mock.patch.object(semi_convert, "convert", conv), \
                mock.patch.object(G.param_build, "with_servo_options", opts), \
                mock.patch.object(G, "drop_param_numbers", drop), \
                mock.patch.object(G.ParamWizardDialog, "_plan", plan):
            self.to_decide(p)
        first = [s for i, s in enumerate(seen) if s != "convert" or i == seen.index("convert")]
        self.assertLess(seen.index("convert"), seen.index("options"))
        self.assertLess(seen.index("options"), seen.index("drop2020"))
        self.assertLess(seen.index("drop2020"), seen.index("plan"))
        self.assertIn("convert", first)

    def test_impossible_conversion_keeps_product_values_and_says_why(self):
        self.write_product(full_product(cmr="4"))
        p = self.panel()
        self.order(p)
        p.semi = [True]                                           # 画面は選ばせないが、選びが残っていても変換しない
        p.job = None
        p._main()
        TestPanel.pick(self, p, "23")
        p._main()
        p._read()
        p._main()
        self.assertEqual(p.page, "decide")
        self.assertIsNotNone(p.job, p.lbl_text.text())
        self.assertEqual([e["applied"] for e in p.semi_report], [False])
        t = self.texts(p)
        self.assertIn("セミクロにはできません", t)
        self.assertIn("CMR", t)
        self.assertIn("製品の値のまま書きます", t)
        self.assertFalse(p.job["semi"])
        vals = self.plan_values(p)
        ax = "A" + str(1 + "XYZABC".index(p.rows[0]["axis"]))
        self.assertEqual(vals.get((1815, ax)), "00100010")        # 製品の値のまま

    def test_semi_plan_file_name_has_semi_and_is_saved_in_the_backup_folder(self):
        p = self.panel()
        self.to_decide(p)
        name = p.job["plan_file"]
        self.assertTrue(name.endswith("_SEMI.DAT"), name)
        self.assertTrue(name.startswith("R50014984"), name)
        self.assertTrue(Path(p.job["backup_folder"], name).is_file())
        self.assertFalse(Path(p.job["backup_folder"], "書込後の予定.DAT").exists())    # 取り違えないよう、SEMI の名前だけ

    def test_product_as_is_saves_the_usual_plan_file(self):
        p = self.panel()
        self.to_decide(p, choose=False)
        self.assertTrue(Path(p.job["backup_folder"], "書込後の予定.DAT").is_file())

    def test_write_screen_says_semi_and_the_converted_values_are_not_registered(self):
        db = Path(self.settings["param_change_csv"])
        before = db.read_bytes() if db.exists() else None
        p = self.panel()
        self.to_decide(p)
        p._main()
        self.assertEqual(p.page, "write")
        self.assertIn("セミクロに変換して書きます（根拠: 計算式）", self.texts(p))
        p._do_trial()
        self.assertTrue(p.trial_ok, p._outcome)
        p._main()
        self.assertEqual(p.page, "power", p._outcome)
        self.assertEqual(db.read_bytes() if db.exists() else None, before)       # 製品の履歴(DB)に入れない

    def test_the_easy_flow_never_registers_into_the_product_history(self):
        src = (Path(__file__).resolve().parent.parent / "nd287_app")
        for name in ("easy_machine_in.py", "easy_machine_in_ui.py"):
            t = (src / name).read_text(encoding="utf-8")
            self.assertNotIn("upsert_entry", t)
            self.assertNotIn("ParamEntry", t)

    def test_two_products_choose_per_product(self):
        p = self.panel()
        self.order(p)
        one = p.products[0]
        p.products = [dict(one, kind="傾斜"), dict(one, kind="回転")]
        sel = p.planner._sel()
        # 片方だけセミクロにしたとき、変換が呼ばれるのは選んだ製品の分だけ
        from nd287_app import semi_convert
        calls = []
        real = semi_convert.convert
        with mock.patch.object(semi_convert, "convert", lambda *a, **k: (calls.append(1), real(*a, **k))[1]):
            r = p.planner.semi_options("", [])
        self.assertEqual(len(r), len(sel))
        self.assertEqual([o["full"] for o in r], [True] * len(sel))


class TestSemiLogic(unittest.TestCase):
    def test_basis_text(self):
        self.assertEqual(M.semi_basis({"source": "組", "basis": "同じ型式の組 3組から（フル a ／ セミ b）"}), "同じ型式の組 3組")
        self.assertEqual(M.semi_basis({"source": "", "basis": "計算式"}), "計算式")
        self.assertEqual(M.semi_basis(None), "計算式")

    def test_lines(self):
        ls = M.semi_lines([{"kind": "回転", "applied": True, "stop": "", "basis": "計算式"},
                           {"kind": "傾斜", "applied": False, "stop": "CMR が 2 以外", "basis": ""}])
        self.assertEqual(ls[0], "回転：セミクロに変換して書きます（根拠: 計算式）")
        self.assertIn("セミクロにはできません（CMR が 2 以外）", ls[1])
        self.assertIn("製品の値のまま", ls[1])


if __name__ == "__main__":
    unittest.main()
