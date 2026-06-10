# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from nd287_app.settings import DEFAULTS, load_settings, save_settings


class TestSettings(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            s = load_settings(os.path.join(d, "nosuch.json"))
        self.assertEqual(s, DEFAULTS)

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            s = dict(DEFAULTS)
            s.update(baudrate=19200, parity="N", port="COM5", save_root=r"D:\測定データ")
            save_settings(s, path)
            loaded = load_settings(path)
        self.assertEqual(loaded["baudrate"], 19200)
        self.assertEqual(loaded["parity"], "N")
        self.assertEqual(loaded["port"], "COM5")
        self.assertEqual(loaded["save_root"], r"D:\測定データ")

    def test_broken_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w") as f:
                f.write("{ this is not json")
            s = load_settings(path)
        self.assertEqual(s, DEFAULTS)

    def test_partial_file_fills_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"baudrate": 38400}')
            s = load_settings(path)
        self.assertEqual(s["baudrate"], 38400)
        self.assertEqual(s["parity"], DEFAULTS["parity"])


if __name__ == "__main__":
    unittest.main()
