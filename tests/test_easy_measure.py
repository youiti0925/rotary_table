# -*- coding: utf-8 -*-
"""はじめてモードの「測っています」「こういう結果でした」（2026-11-26 作り直し）。
docs/はじめてモード_測る画面の作り直し_20261126.md。
本物の測定の画面（にせ物の表示器）で、値を入れていって、画面が本当の値を出しているかを見る。"""
import copy
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from nd287_app import easy_art as A
from nd287_app import easy_flow as F
from nd287_app import easy_live as EL


class TestFlowTable(unittest.TestCase):
    def ctx(self, **k):
        c = {"params": "auto", "prog": "auto"}
        c.update(k)
        return c

    def test_summary_comes_before_saving(self):
        self.assertLess(F.INDEX["measuring"], F.INDEX["summary"])
        self.assertLess(F.INDEX["summary"], F.INDEX["result"])
        self.assertEqual(F.step("summary")["title"], "こういう結果でした")

    def test_every_step_has_a_reason(self):
        """「何のためか説明できること」（利用者 2026-11-26）。機械をさわる手順には、必ず1行の理由がある。"""
        for s in F.STEPS:
            if s.get("kind") == "machine":
                self.assertTrue(s.get("why"), s["id"])

    def test_axis_on_is_shown_and_names_the_decided_axis(self):
        st = F.machine_state({"values": {"aut": 0, "run": 0, "emergency": 1, "alarm": 0}}, [], online=True)
        self.assertTrue(F.needed("axis_on", st, self.ctx()))
        title, lines = F.detach_lines([("回転", "z"), ("傾斜", "X")])
        self.assertIn("Z軸・X軸", title)
        self.assertIn("「Z軸」（回転）", lines[0])
        self.assertIn("「X軸」（傾斜）", lines[0])
        self.assertIn(F.DETACH_ON_SIDE + "向き", lines[0])
        title, lines = F.detach_lines([])
        self.assertIn("使う軸", title)                        # 軸が分からないときは、軸の名前を作らない

    def test_jog_step_only_when_origin_must_be_set_again(self):
        """「軸選択を合わせて＋送り」は、ふだんの手順に出さない（利用者 2026-11-26）。"""
        st = F.machine_state({"values": {"aut": 0, "run": 0, "emergency": 0, "alarm": 0}}, [], online=True)
        self.assertFalse(F.needed("jog_origin", st, self.ctx()))
        self.assertFalse(F.needed("dial_ref", st, self.ctx()))

    def test_one_off_condition_skips_the_registration_screen(self):
        st = F.OFFLINE
        self.assertTrue(F.needed("cond_reg", st, self.ctx()))
        self.assertFalse(F.needed("cond_reg", st, self.ctx(cond_once_flow=True)))
        self.assertFalse(F.done_now("cond_check", st, self.ctx(cond_unreg=True)))   # 登録が無いままでは進めない
        self.assertTrue(F.done_now("cond_check", st, self.ctx()))

    def test_detach_drawing_marks_both_axes_of_a_two_axis_product(self):
        one, two = A.axis_detach("Z"), A.axis_detach("ZX")
        self.assertIn("「Z軸」だけ", one)
        self.assertIn("「Z軸・X軸」だけ", two)
        self.assertEqual(two.count('opacity=".35"'), 2)
        self.assertEqual(A.axis_detach("").count('opacity=".35"'), 0)      # 軸が分からないときは、どれも光らせない
        self.assertNotIn("「X軸」", A.jog(False, 0.0, ""))                  # 空の軸で X を指さない


class TestLiveText(unittest.TestCase):
    def test_verdict_never_says_pass_without_a_spec(self):
        tone, title, _sub = EL.verdict(0, False)
        self.assertNotIn("合格", title)
        self.assertNotEqual(tone, "ok")
        tone, title, _sub = EL.verdict(0, True)
        self.assertEqual(tone, "ok")
        tone, title, sub = EL.verdict(2, True, [("ホイール CW 単一誤差", "6.9")])
        self.assertEqual(tone, "stop")
        self.assertIn("2", title)

    def test_merge_tables_keeps_red_cells(self):
        t = {"headers": ["", "単一誤差"], "rows": [[("ホイール CW", ""), ("6.9\"", EL.RED)]], "notes": []}
        m = EL.merge_tables([t])
        self.assertEqual(m["rows"][0][1], ("6.9\"", EL.RED))


class TestRealScreens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from nd287_app import settings as S
        cls._tmp = tempfile.mkdtemp()
        cls._orig_path = S.settings_path
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"

    @classmethod
    def tearDownClass(cls):
        from nd287_app import settings as S
        S.settings_path = cls._orig_path
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        from PySide6 import QtWidgets
        from nd287_app import gui as G, nd287, easy_mode
        from nd287_app.settings import DEFAULTS
        import test_easy_mode as TE
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        t = Path(tempfile.mkdtemp(dir=self._tmp))
        st = copy.deepcopy(DEFAULTS)
        st.update(focas_auto_find=False, save_root=str(t / "save"), bs_save_root=str(t / "bs"), webapp_sync_enabled=False)
        orig_sleep = nd287.time.sleep
        nd287.time.sleep = lambda s: None
        self.addCleanup(lambda: setattr(nd287.time, "sleep", orig_sleep))
        self.w = G.MainWindow(nd287.DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        self.w.flow_timer.stop()
        self.w.e_model.setText("RNA-250R")
        self.w.on_model_entered()
        self.w.e_machine.setText("50014861")
        self.w.e_operator.setText("試験")
        self.w.e_temp.setText("20")
        self.dlg = easy_mode.EasyModeDialog(self.w, TE.FakePoller(TE.S(aut=1, emergency=0)))
        self.dlg.resize(1260, 632)
        self.dlg.show()
        self.app.processEvents()
        self.dlg._start_flow("measure")

    def tearDown(self):
        self.dlg._timer.stop()
        self.dlg.host_saved = True
        self.w.saved = True
        self.dlg.hide()
        self.dlg.deleteLater()
        self.w.flow_timer.stop()
        self.w.close()
        self.w.deleteLater()
        from PySide6 import QtCore
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)   # 窓を今こわす（終わるときにまとめてこわすと落ちる）
        self.app.processEvents()

    def test_plan_before_measuring_counts_like_the_real_sequence(self):
        plan = self.w.easy_plan()
        self.assertEqual(plan["total"], sum(n for _l, n in plan["rows"]))
        self.assertEqual(plan["total"], len(self.w.build_sequence()))
        self.assertIsNone(self.w.seq)                                      # 見せるだけ。取込は始めない

    def test_measuring_screen_shows_real_points_and_count(self):
        w, d = self.w, self.dlg
        d._enter(F.INDEX["measuring"])
        w.start()
        total = len(w.seq)
        for _ in range(5):
            w.take_manual()
        d._refresh()
        self.app.processEvents()
        v = d.v_measure
        self.assertFalse(d.wide.isHidden())
        self.assertEqual(v.graph.points, 5)                                # グラフの点は、入った値の数だけ（飾りの線ではない）
        self.assertIn(f"5 ／ {total}", v.lbl_count.text())
        self.assertEqual(v.bar.value(), 5)
        self.assertIn("ずれ", v.lbl_dev.text())
        for _ in range(3):
            w.take_manual()
        d._refresh()
        self.assertEqual(v.graph.points, 8)

    def test_summary_shows_the_same_values_as_the_right_table(self):
        w, d = self.w, self.dlg
        d._enter(F.INDEX["measuring"])
        w.start()
        while w.seq is not None and not w.seq.done():
            w.take_manual()
        self.app.processEvents()
        d._pending_next = False
        d._after_measuring()
        self.assertEqual(F.STEPS[d.i]["id"], "summary")
        d._refresh()
        v = d.v_summary
        m = EL.merge_tables(EL.read_tables(w))
        self.assertTrue(m["rows"])
        shown = [t for t, _r in v.cells]
        for row in m["rows"]:
            for txt, _c in row:
                self.assertIn(txt, shown)
        red_table = sum(1 for row in m["rows"] for _t, c in row if c == EL.RED) + sum(1 for _t, c in m["notes"] if c == EL.RED)
        self.assertEqual(sum(1 for _t, r in v.cells if r), red_table)      # 赤い所は、右の表の赤い字と同じ数（新しい合否は作らない）
        self.assertGreater(v.graph.points, 0)
        self.assertEqual(d.b_next.text(), "保存へ進む ▶")



class TestSelfShotsEntry(unittest.TestCase):
    def test_exe_has_the_self_shot_entry_and_it_runs_in_the_practice_place(self):
        """入れたアプリ自身に画面を写させる入口。練習の場所を用意してから動く（本番を書かない）。"""
        src = (Path(__file__).parent.parent / "nd287_app" / "__main__.py").read_text(encoding="utf-8")
        i, j = src.index('"--self-shots" in sys.argv'), src.index('practice = "--practice"')
        self.assertLess(i, j)
        self.assertIn("prepare(_real_dir())", src[i:j])
        from nd287_app import self_shots
        self.assertTrue(callable(self_shots.run))

if __name__ == "__main__":
    unittest.main()
