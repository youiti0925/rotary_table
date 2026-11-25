# -*- coding: utf-8 -*-
"""通常画面で型式を手で入れたとき: 登録済みの種類をボタンで出し、1つなら自動・複数は機械の1815→前回。"""
import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from nd287_app import kind_conditions as K


class TestKindButtons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from nd287_app import settings as S
        cls._tmp = tempfile.mkdtemp()
        cls._orig = S.settings_path
        S.settings_path = lambda: Path(cls._tmp) / "settings.json"

    @classmethod
    def tearDownClass(cls):
        from nd287_app import settings as S
        S.settings_path = cls._orig
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def make(self):
        from PySide6 import QtWidgets
        from nd287_app import gui as G
        from nd287_app.nd287 import DummyDevice
        from nd287_app.settings import DEFAULTS
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        t = Path(self._tmp)
        x = t / K.FILE_NAME
        K.build_template(x, [
            {"型式": "ONE-1", "測定の種類": "セミ・モーター", "歯数": 72},
            {"型式": "RTV-231", "測定の種類": "セミ・モーター", "歯数": 72},
            {"型式": "RTV-231", "測定の種類": "セミ・駆動装置", "ホイール刻み": 1},
            {"型式": "RTV-231", "測定の種類": "フル・スケール付き", "スケール名": "A", "ホイール刻み": 5},
            {"型式": "OLD-1", "測定の種類": "", "ホイール刻み": 10}])
        st = copy.deepcopy(DEFAULTS)
        st.update(focas_auto_find=False, save_root=str(t / "save"), bs_save_root="", webapp_sync_enabled=False,
                  kind_conditions_xlsx=str(x), last_kinds={},
                  user_rotary_csv=str(t / "r.csv"), user_tilt_csv=str(t / "t.csv"),
                  user_rotary_repeat_csv=str(t / "rr.csv"), user_tilt_repeat_csv=str(t / "tr.csv"))
        w = G.MainWindow(DummyDevice(), 10.0, 0.5, 5.0, 0.0, st)
        w.flow_timer.stop()
        return w

    def test_buttons(self):
        w = self.make()
        try:
            w.e_model.setText("OLD-1"); w.on_model_entered()          # 種類なしの登録だけ → 今までどおり（ボタン無し）
            self.assertFalse(w._kind_btns)
            self.assertEqual(w.measure_kind, "")
            w.e_model.setText("ONE-1"); w.on_model_entered()          # 1つだけ → 自動
            self.assertEqual(w.measure_kind, "セミ・モーター")
            w.e_model.setText("RTV-231"); w.on_model_entered()        # 複数・機械なし・前回なし → 押すまで決めない
            self.assertEqual(w.measure_kind, "")
            self.assertEqual(len(w._kind_btns), 4)                    # 今までの登録＋3種類
            w._kind_btns["セミ・駆動装置"].click()
            self.assertEqual(w.measure_kind, "セミ・駆動装置")
            w.e_model.setText("ONE-1"); w.on_model_entered()
            w.e_model.setText("RTV-231"); w.on_model_entered()        # 前回使ったものが選ばれる
            self.assertEqual(w.measure_kind, "セミ・駆動装置")
            w._on_loop_read("フル")                                   # 機械の1815がフル → フル
            w.measure_kind = ""; w._kind_user_none = False
            w.on_model_entered()
            self.assertEqual(w.measure_kind, "フル・スケール付き")
            w.measure_kind = "セミ・モーター"                          # 行を壊して「決められない」状態にする → 止めて登録ボタンが出る
            row = [r for r in w.kind_table["rotary"] if r["型式"] == "RTV-231" and r["測定の種類"] == "セミ・モーター"][0]
            row["歯数"] = ""; row["_bad"] = {"歯数": "abc"}
            w.on_model_entered()
            self.assertFalse(w.b_kind_register.isHidden())
            self.assertIn("数字として読めません", w.statusBar().currentMessage())
            w._kind_btns[""].click()                                  # 「今までの登録」を押し直す → 勝手に戻さない
            self.assertEqual(w.measure_kind, "")
            w.on_model_entered()
            self.assertEqual(w.measure_kind, "")
        finally:
            w.close(); w.deleteLater()


if __name__ == "__main__":
    unittest.main()
