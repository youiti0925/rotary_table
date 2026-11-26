# -*- coding: utf-8 -*-
"""練習モード: 本番の設定・マスタ・測定データ・機械を汚さない。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app import practice, settings as S


class TestPractice(unittest.TestCase):
    def setUp(self):
        self.real = Path(tempfile.mkdtemp())
        (self.real / "マスタ").mkdir()
        (self.real / "マスタ" / "測定条件.csv").write_text("a,b\n", encoding="utf-8")
        self.real_settings = {"focas_host": "192.168.1.1", "focas_auto_connect": True, "save_root": "D:/本番",
                              "bs_save_root": "K:/共有", "param_out_folder": "C:/Downloads", "ui_font_pt": 9}
        (self.real / "settings.json").write_text(json.dumps(self.real_settings, ensure_ascii=False), encoding="utf-8")
        self.before = (self.real / "settings.json").read_bytes()

    def tearDown(self):
        os.environ.pop(practice.ENV, None)

    def test_prepare_copies_and_cuts_but_never_writes_the_real_place(self):
        pdir = practice.prepare(self.real)
        self.assertEqual(pdir, self.real / "練習")
        self.assertEqual((self.real / "settings.json").read_bytes(), self.before)       # 本番の設定は書き換えない
        st = json.loads((pdir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(st["focas_host"], "")
        self.assertFalse(st["focas_auto_connect"])
        self.assertEqual(st["save_root"], "測定データ")
        self.assertEqual(st["bs_save_root"], "")
        self.assertEqual(st["ui_font_pt"], 9)                                           # 見た目の設定は引き継ぐ
        self.assertTrue(str(st["param_out_folder"]).startswith(str(pdir)))
        self.assertTrue((pdir / "マスタ" / "測定条件.csv").is_file())
        self.assertEqual(S.app_dir(), pdir)                                             # 保存・登録は練習の場所へ
        self.assertEqual(S.settings_path(), pdir / "settings.json")

    def test_machine_is_refused(self):
        practice.prepare(self.real)
        from nd287_app import focas_client as FC
        c = FC.FocasClient("python.exe", "helper.py")
        with self.assertRaises(FC.FocasError) as cm:
            c.start()
        self.assertIn("練習モード", str(cm.exception))

    def test_second_open_keeps_practice_data_and_cuts_again(self):
        pdir = practice.prepare(self.real)
        st = json.loads((pdir / "settings.json").read_text(encoding="utf-8"))
        st["focas_host"] = "192.168.1.9"
        st["last_operator"] = "YU"
        (pdir / "settings.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        practice.prepare(self.real)
        st2 = json.loads((pdir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(st2["focas_host"], "")
        self.assertEqual(st2["last_operator"], "YU")


if __name__ == "__main__":
    unittest.main()
