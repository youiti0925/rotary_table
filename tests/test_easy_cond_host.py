# -*- coding: utf-8 -*-
"""はじめてモード「測り方を決める」の、測定の画面側（gui.MainWindow.easy_cond_*）。登録先は一時フォルダ。"""
import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestEasyCondHost(unittest.TestCase):
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
        from nd287_app import gui as G
        from nd287_app.nd287 import DummyDevice
        from nd287_app.settings import DEFAULTS
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        st = copy.deepcopy(DEFAULTS)
        t = Path(self._tmp)
        st.update(focas_auto_find=False, save_root=str(t / "save"), bs_save_root="", webapp_sync_enabled=False,
                  user_rotary_csv=str(t / "r.csv"), user_tilt_csv=str(t / "t.csv"),
                  user_rotary_repeat_csv=str(t / "rr.csv"), user_tilt_repeat_csv=str(t / "tr.csv"))
        self.w = G.MainWindow(DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        self.w.flow_timer.stop()
        for f in ("r.csv", "t.csv", "rr.csv", "tr.csv"):
            (t / f).unlink(missing_ok=True)

    def tearDown(self):
        self.w.flow_timer.stop()
        self.w.close()
        self.w.deleteLater()

    def test_register_then_auto_fill_next_time(self):
        w = self.w
        w.e_model.setText("ZZ-TEST-1")                    # 会社の一覧に無い型式
        w.easy_cond_mode("one", False)
        self.assertEqual(w.current_mode(), "回転分割+再現")
        st = w.easy_cond_state()
        self.assertEqual((st["div"], st["rep"]), ("なし", "なし"))
        w.easy_cond_set("wheel", 15)
        w.easy_cond_set("blocks", 3)
        w.easy_cond_set("rend", 180)
        msgs = w.easy_cond_register("one", False)
        self.assertEqual(len(msgs), 2)
        self.assertTrue(all("登録しました" in m for m in msgs))
        st = w.easy_cond_state()
        self.assertEqual((st["div"], st["rep"]), ("登録", "登録"))
        # 次の機会: 値をばらばらにしてから型式を入れ直すと、登録した値が自動で入る
        w.easy_cond_set("wheel", 5)
        w.easy_cond_set("blocks", 4)
        w.easy_cond_mode("one", False)
        g = w.easy_cond_get()
        self.assertEqual(g["wheel"], 15)
        self.assertEqual(g["blocks"], 3)
        self.assertEqual(w.easy_cond_state()["blocks"], [0.0, 90.0, 180.0])

    def test_tilt_goes_to_tilt_files_not_rotary(self):
        w = self.w
        w.e_model.setText("ZZ-TILT-1")
        w.easy_cond_mode("division", True)
        self.assertEqual(w.current_mode(), "傾斜分割")
        w.easy_cond_register("division", True)
        self.assertTrue(Path(w.settings["user_tilt_csv"]).exists())
        self.assertFalse(Path(w.settings["user_rotary_csv"]).exists())     # 回転には混ぜない

    def test_needs_model(self):
        self.w.e_model.setText("")
        with self.assertRaises(RuntimeError):
            self.w.easy_cond_register("division", False)

    def test_company_list_same_values_are_not_registered(self):
        """会社の一覧にある型式で値を変えていなければ、自分の登録は作らない（測定順・主点評価が自動で入らなくなるため）。"""
        w = self.w
        entries = (w.masters or {}).get("conditions") or {}
        if not entries:
            self.skipTest("会社の一覧が無い環境")
        model = next(iter(entries.values())).get("model") or next(iter(entries))
        w.e_model.setText(str(model))
        w.easy_cond_mode("division", False)
        st = w.easy_cond_state()
        if st["div"] != "会社の一覧":
            self.skipTest("この型式は会社の一覧の分割条件ではない")
        self.assertTrue(st["div_same_master"])
        msgs = w.easy_cond_register("division", False)
        self.assertIn("会社の一覧", msgs[0])
        self.assertFalse(Path(w.settings["user_rotary_csv"]).exists())


if __name__ == "__main__":
    unittest.main()
