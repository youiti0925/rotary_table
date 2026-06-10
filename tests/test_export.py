# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from nd287_app.analysis import summarize
from nd287_app.export import result_rows, save_csv
from nd287_app.sequence import Sequence


def make_finished_sequence():
    seq = Sequence(wheel_pitch=90.0, worm_pitch=1.0, worm_range=2.0)
    while not seq.done():
        key, target, direction = seq.current()
        seq.record(target + (1.0 if direction > 0 else 2.0) / 3600)
    return seq


class TestExport(unittest.TestCase):
    def test_result_rows_order(self):
        seq = make_finished_sequence()
        summary, _ = summarize(seq.data)
        items = [r[0] for r in result_rows(summary)]
        self.assertIn("ホイールCW 精度PP", items)
        self.assertIn("ホイール バックラッシ MIN", items)
        self.assertIn("真の最大 (CW)", items)
        self.assertIn("真の最小 (CCW)", items)

    def test_save_csv_roundtrip(self):
        seq = make_finished_sequence()
        summary, _ = summarize(seq.data)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "result.csv")
            save_csv(path, seq.data, summary)
            with open(path, encoding="cp932") as f:
                text = f.read()
        self.assertIn("ND287 分割測定結果", text)
        self.assertIn("ホイール CW", text)
        self.assertIn("真の最大 (CW)", text)
        # 生データ行数 = 4+4+3+3 = 14系列点（結果サマリ行は系列名で始まらない）
        series = ("ホイール CW,", "ホイール CCW,", "ウォーム CW,", "ウォーム CCW,")
        data_lines = [l for l in text.splitlines() if l.startswith(series)]
        self.assertEqual(len(data_lines), 14)


if __name__ == "__main__":
    unittest.main()
