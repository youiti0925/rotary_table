# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app.settings import (
    DEFAULTS,
    apply_active_profile,
    load_settings,
    save_settings,
)


class TestSettings(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(os.path.join(d, "nosuch.json"))
        self.assertEqual(s, DEFAULTS)

    def test_save_load_roundtrip(self):
        # 接続値はアクティブプロファイル経由で保持される
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            s = load_settings(path)
            s["profiles"]["X32"].update(baudrate=19200, parity="N", port="COM5")
            s["save_root"] = r"D:\測定データ"
            apply_active_profile(s)
            save_settings(s, path)
            loaded = load_settings(path)
        self.assertEqual(loaded["baudrate"], 19200)
        self.assertEqual(loaded["parity"], "N")
        self.assertEqual(loaded["port"], "COM5")
        self.assertEqual(loaded["save_root"], r"D:\測定データ")

    def test_broken_file_returns_defaults(self):
        """壊れていても既定値で起動できる。ただし黙って戻さない。

        黙って既定値にすると、次の保存で本番の設定（保存先のK:ドライブ・
        BASICの場所・FTPのパスワード等）が上書きで消える。
        壊れたファイルは名前を変えて残し、画面に出すための印を付ける。
        """
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                f.write("{ this is not json")
            s = load_settings(path)
            broken = [f for f in os.listdir(d) if ".broken-" in f]
            self.assertEqual(len(broken), 1)          # 退避してある
            self.assertFalse(os.path.exists(path))    # 壊れたまま残さない
        self.assertIn("_load_error", s)
        self.assertEqual({k: v for k, v in s.items() if not k.startswith("_")},
                         DEFAULTS)

    def test_save_keeps_one_backup_and_is_atomic(self):
        from nd287_app.settings import save_settings
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            save_settings({"a": 1}, path)
            save_settings({"a": 2}, path)
            self.assertEqual(json.loads(open(path).read())["a"], 2)
            bak = path + ".bak"
            self.assertTrue(os.path.exists(bak))       # 直前の1世代が残る
            self.assertEqual(json.loads(open(bak).read())["a"], 1)
            self.assertFalse(os.path.exists(path + ".tmp"))   # 書きかけを残さない

    def test_save_does_not_write_internal_marks(self):
        from nd287_app.settings import save_settings
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            save_settings({"a": 1, "_load_error": "x"}, path)
            self.assertNotIn("_load_error", json.loads(open(path).read()))

    def test_partial_file_fills_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"baudrate": 38400}')
            s = load_settings(path)
        self.assertEqual(s["baudrate"], 38400)
        self.assertEqual(s["parity"], DEFAULTS["parity"])


class TestConnectionProfiles(unittest.TestCase):
    def test_defaults_have_two_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(os.path.join(d, "nosuch.json"))
        self.assertEqual(s["active_profile"], "X32")
        self.assertIn("X32", s["profiles"])
        self.assertIn("X31", s["profiles"])

    def test_switch_profile_changes_connection(self):
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(os.path.join(d, "nosuch.json"))
        s["profiles"]["X31"].update(port="COM7", baudrate=19200, parity="N")
        s["active_profile"] = "X31"
        apply_active_profile(s)
        self.assertEqual(s["port"], "COM7")
        self.assertEqual(s["baudrate"], 19200)
        self.assertEqual(s["parity"], "N")
        s["active_profile"] = "X32"
        apply_active_profile(s)
        self.assertEqual(s["port"], "auto")
        self.assertEqual(s["baudrate"], 4800)   # X32既定はND287に合わせ4800 8E1

    def test_default_profiles_are_4800_even(self):
        # X31/X32 とも初期値は ND287 に合わせ 4800 8E1（実機確認済み）
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(os.path.join(d, "nosuch.json"))
        for prof in ("X32", "X31"):
            self.assertEqual(s["profiles"][prof]["baudrate"], 4800, prof)
            self.assertEqual(s["profiles"][prof]["parity"], "E", prof)

    def test_old_format_migrates_to_active_profile(self):
        # 旧settings.json（プロファイル無し・トップレベルにport等）からの移行
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"port": "COM5", "baudrate": 9600, "parity": "E"}')
            s = load_settings(path)
        self.assertEqual(s["profiles"]["X32"]["port"], "COM5")
        self.assertEqual(s["port"], "COM5")
        self.assertEqual(s["profiles"]["X31"]["port"], "auto")

    def test_profiles_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            s = load_settings(path)
            s["profiles"]["X31"].update(port="COM7", baudrate=38400)
            s["active_profile"] = "X31"
            apply_active_profile(s)
            save_settings(s, path)
            loaded = load_settings(path)
        self.assertEqual(loaded["active_profile"], "X31")
        self.assertEqual(loaded["port"], "COM7")
        self.assertEqual(loaded["profiles"]["X32"]["port"], "auto")



class TestFanucMigration(unittest.TestCase):
    """機械が受け付けない古い値（X軸・保護O番号）を1度だけ直す"""

    def _load(self, stored):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text(json.dumps(stored), encoding="utf-8")
            return load_settings(p)

    def test_axis_x_becomes_machine_axis(self):
        s = self._load({"fanuc_axis": "X"})
        self.assertEqual(s["fanuc_axis"], "Z")

    def test_protected_sub_number_moved(self):
        s = self._load({"fanuc_rep_sub_number": 9001})
        self.assertEqual(s["fanuc_rep_sub_number"], 1000)

    def test_runs_only_once(self):
        # 1度移行した後で自分でXに戻したなら、その選択を尊重する
        s = self._load({"fanuc_axis": "X", "fanuc_migrated": True})
        self.assertEqual(s["fanuc_axis"], "X")

    def test_other_axis_kept(self):
        s = self._load({"fanuc_axis": "B"})
        self.assertEqual(s["fanuc_axis"], "B")

    def test_defaults_are_machine_ready(self):
        s = self._load({})
        self.assertEqual(s["fanuc_axis"], "Z")
        self.assertLess(s["fanuc_rep_sub_number"], 8000)
        self.assertEqual(s["nc_eob"], "\n\r\r")


if __name__ == "__main__":
    unittest.main()
