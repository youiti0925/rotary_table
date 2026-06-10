# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

from nd287_app.analysis import summarize
from nd287_app.export import (
    build_save_path,
    judgement_texts,
    load_csv,
    model_folder,
    result_rows,
    sanitize_filename,
    save_csv,
)
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
        self.assertIn("ホイールCW 単一誤差", items)
        self.assertIn("ウォームCCW 隣接誤差", items)
        self.assertIn("ウォームCW 傾き", items)
        self.assertIn("ホイール バックラッシ MIN", items)
        self.assertIn("真の最大 (CW)", items)
        self.assertIn("真の最小 (CCW)", items)
        self.assertNotIn("ホイール バックラッシ 判定", items)  # 判定文なしのとき

    def test_result_rows_with_judgements(self):
        seq = make_finished_sequence()
        summary, _ = summarize(seq.data)
        judgements = {
            "wheel_backlash": 'OK（規格 0〜25" @ 23.5°C）',
            "worm_backlash": 'NG（規格 0〜1" @ 23.5°C）',
            "true": 'OK（規格 -25〜25" @ 23.5°C）',
        }
        rows = dict(result_rows(summary, judgements))
        self.assertTrue(rows["ホイール バックラッシ 判定"].startswith("OK"))
        self.assertTrue(rows["ウォーム バックラッシ 判定"].startswith("NG"))
        self.assertTrue(rows["総合（真の最大最小） 判定"].startswith("OK"))

    def test_judgement_texts(self):
        seq = make_finished_sequence()  # バックラッシ = CCW誤差2.5"-CW誤差1.0" = 1.5"
        summary, _ = summarize(seq.data)
        band_ok = [dict(temp_min=15.0, temp_max=25.0, min=0.0, max=25.0)]
        band_true = [dict(temp_min=15.0, temp_max=25.0, min=-25.0, max=25.0)]
        spec_map = dict(wheel_backlash=band_ok, worm_backlash=band_ok, true=band_true)

        texts = judgement_texts(summary, 20.0, spec_map)
        self.assertTrue(texts["wheel_backlash"].startswith("OK"))
        self.assertTrue(texts["worm_backlash"].startswith("OK"))
        self.assertTrue(texts["true"].startswith("OK"))

        ng_spec = dict(spec_map, worm_backlash=[dict(temp_min=15.0, temp_max=25.0, min=0.0, max=1.0)])
        texts = judgement_texts(summary, 20.0, ng_spec)
        self.assertTrue(texts["worm_backlash"].startswith("NG"))
        self.assertTrue(texts["wheel_backlash"].startswith("OK"))

        # 温度が規格帯の外 → 判定不可。温度未入力 → 判定なし。規格が空の項目 → 判定なし
        self.assertTrue(
            judgement_texts(summary, 50.0, spec_map)["wheel_backlash"].startswith("判定不可")
        )
        self.assertEqual(judgement_texts(summary, None, spec_map), {})
        no_worm = dict(spec_map, worm_backlash=[])
        self.assertNotIn("worm_backlash", judgement_texts(summary, 20.0, no_worm))

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
        # 生データ行数 = 5+5+3+3 = 16系列点（ホイールは閉じ点360°込み）
        series = ("ホイール CW,", "ホイール CCW,", "ウォーム CW,", "ウォーム CCW,")
        data_lines = [l for l in text.splitlines() if l.startswith(series)]
        self.assertEqual(len(data_lines), 16)


class TestSavePathRules(unittest.TestCase):
    def test_model_folder_takes_prefix_before_hyphen(self):
        self.assertEqual(model_folder("RWE-200"), "RWE")
        self.assertEqual(model_folder(" rt-320 "), "rt")

    def test_model_folder_without_hyphen(self):
        self.assertEqual(model_folder("RWE200"), "RWE200")
        self.assertEqual(model_folder(""), "その他")

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")
        self.assertEqual(sanitize_filename("  "), "無題")

    def test_build_save_path(self):
        path = build_save_path("/data", "RWE-200", "12345")
        self.assertEqual(path, Path("/data/RWE/12345.csv"))


class TestLoadCsv(unittest.TestCase):
    def test_save_load_roundtrip_with_meta(self):
        seq = make_finished_sequence()
        summary, _ = summarize(seq.data)
        meta = {
            "型式": "RWE-200",
            "機番": "12345",
            "ホイール刻み[°]": 90.0,
            "ウォーム刻み[°]": 1.0,
            "ウォーム範囲[°]": 2.0,
            "ウォーム開始[°]": 0.0,
        }
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "12345.csv")
            save_csv(path, seq.data, summary, meta)
            loaded_meta, loaded_data = load_csv(path)

        self.assertEqual(loaded_meta["型式"], "RWE-200")
        self.assertEqual(loaded_meta["機番"], "12345")
        self.assertEqual(float(loaded_meta["ホイール刻み[°]"]), 90.0)
        for key in seq.data:
            self.assertEqual(loaded_data[key][0], seq.data[key][0])
            for orig, back in zip(seq.data[key][1], loaded_data[key][1]):
                self.assertAlmostEqual(orig, back, places=6)
        # ロード後の再計算が元と一致する
        summary2, _ = summarize(loaded_data)
        self.assertAlmostEqual(
            summary2["wheel_cw"]["pp"], summary["wheel_cw"]["pp"], places=2
        )

    def test_load_without_meta(self):
        seq = make_finished_sequence()
        summary, _ = summarize(seq.data)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.csv")
            save_csv(path, seq.data, summary)  # メタなし（旧形式）
            meta, data = load_csv(path)
        self.assertNotIn("型式", meta)
        self.assertEqual(len(data["wheel_cw"][0]), 5)


if __name__ == "__main__":
    unittest.main()
