# -*- coding: utf-8 -*-
import csv
import tempfile
import unittest
from pathlib import Path

from nd287_app.analysis import repeatability_summary, summarize
from nd287_app.export import MODE_KEY, save_csv, save_repeat_csv
from nd287_app import report


def _make_indexing(folder, model, machine, day, scale):
    t = [i * 30.0 for i in range(12)]
    data = {
        "wheel_cw": (t, [ti + scale * 1e-4 * (i % 3) for i, ti in enumerate(t)]),
        "wheel_ccw": (t, [ti + scale * 1e-4 * ((i + 1) % 3) for i, ti in enumerate(t)]),
        "worm_cw": ([0, 0.3, 0.6], [0, 0.3001, 0.6002]),
        "worm_ccw": ([0, 0.3, 0.6], [0, 0.3002, 0.6003]),
    }
    summary, _ = summarize(data)
    path = Path(folder) / f"{machine}.csv"
    meta = {
        MODE_KEY: "回転分割", "型式": model, "機番": machine,
        "日付": f"2026-06-{day}", "名前": "山田", "測定温度[°C]": "22.0",
    }
    save_csv(str(path), data, summary, meta, {"wheel_backlash": "OK（規格 …）"})
    return str(path)


def _make_repeat(folder, model, machine, day):
    pts = [0.0, 90.0, 180.0, 270.0]
    data = {}
    for i, a in enumerate(pts):
        data[("cw", i)] = [a + 0.0001, a + 0.0004]
        data[("ccw", i)] = [a + 0.0002, a + 0.0006]
    rsum = repeatability_summary(pts, data)
    path = Path(folder) / f"{machine}.csv"
    meta = {
        MODE_KEY: "回転再現性", "型式": model, "機番": machine,
        "日付": f"2026-06-{day}", "名前": "山田", "測定温度[°C]": "22.0",
    }
    save_repeat_csv(str(path), pts, data, rsum, meta)
    return str(path)


class TestReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "RWE").mkdir()
        (root / "RB").mkdir()
        _make_indexing(root / "RWE", "RWE-200", "10001", "11", 6)
        _make_indexing(root / "RWE", "RWE-200", "10002", "12", 9)
        _make_indexing(root / "RB", "RB-250", "20001", "13", 4)
        _make_repeat(root / "RWE", "RWE-200", "10009", "14")
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_and_models(self):
        recs = report.scan_measurements(self.root)
        self.assertEqual(len(recs), 4)
        self.assertEqual(sorted(report.distinct_models(recs)), ["RB-250", "RWE-200"])

    def test_model_filter_and_recent(self):
        recs = report.scan_measurements(self.root, model="RWE-200")
        self.assertEqual({r["型式"] for r in recs}, {"RWE-200"})
        self.assertEqual(len(recs), 3)
        recs2 = report.scan_measurements(self.root, recent=2)
        self.assertEqual(len(recs2), 2)

    def test_metrics_extracted(self):
        recs = report.scan_measurements(self.root, model="RB-250")
        rec = recs[0]
        self.assertIn("ホイール CW 精度PP", rec["metrics"])
        self.assertIn("ホイールBL MIN", rec["metrics"])
        self.assertEqual(rec["判定"], "OK")
        self.assertAlmostEqual(rec["測定温度"], 22.0)

    def test_repeat_metric(self):
        recs = report.scan_measurements(self.root, model="RWE-200")
        rep = [r for r in recs if "再現" in r["モード"]][0]
        self.assertIn("再現性総合", rep["metrics"])

    def test_comparison_table_and_csv(self):
        recs = report.scan_measurements(self.root, model="RWE-200")
        metrics = report.available_metrics(recs)
        headers, rows = report.build_comparison_table(recs, metrics)
        self.assertEqual(headers[:7], report.BASE_COLUMNS)
        self.assertEqual(len(rows), len(recs))
        # 数値は2桁文字列
        col = headers.index("ホイール CW 精度PP")
        self.assertRegex(rows[0][col] or "0.00", r"^\d+\.\d{2}$|^$")
        out = Path(self.tmp.name) / "t.csv"
        report.write_table_csv(out, headers, rows)
        with open(out, encoding="cp932") as f:
            back = list(csv.reader(f))
        self.assertEqual(back[0], headers)
        self.assertEqual(len(back), len(rows) + 1)

    def test_aggregate_individual(self):
        recs = report.scan_measurements(self.root, model="RWE-200")
        metric = "ホイール CW 精度PP"
        labels, values = report.aggregate_series(recs, metric, "機番", "none")
        self.assertEqual(len(labels), len(values))
        # 個別なので測定数ぶん（指標値があるもの）。機番で昇順
        self.assertEqual(labels, sorted(labels))
        self.assertTrue(all(isinstance(v, float) for v in values))

    def test_aggregate_mean_by_model(self):
        recs = report.scan_measurements(self.root)  # 全型式
        metric = "ホイール CW 精度PP"
        labels, values = report.aggregate_series(recs, metric, "型式", "mean")
        # 型式でまとめるので RB-250 と RWE-200 の2カテゴリ
        self.assertEqual(labels, ["RB-250", "RWE-200"])
        # RWE-200 の平均が個別2件の平均に一致（再現性測定はこの指標を持たない）
        indiv = [r["metrics"][metric] for r in recs
                 if r["型式"] == "RWE-200" and metric in r["metrics"]]
        self.assertAlmostEqual(values[1], sum(indiv) / len(indiv))

    def test_aggregate_max_min(self):
        recs = report.scan_measurements(self.root, model="RWE-200")
        metric = "ホイール CW 精度PP"
        _, mx = report.aggregate_series(recs, metric, "型式", "max")
        _, mn = report.aggregate_series(recs, metric, "型式", "min")
        indiv = [r["metrics"][metric] for r in recs if metric in r["metrics"]]
        self.assertAlmostEqual(mx[0], max(indiv))
        self.assertAlmostEqual(mn[0], min(indiv))

    def test_aggregate_missing_metric(self):
        recs = report.scan_measurements(self.root)
        labels, values = report.aggregate_series(recs, "存在しない指標", "機番", "none")
        self.assertEqual((labels, values), ([], []))

    def test_deviation_table(self):
        series = {
            "wheel_cw": ([0.0, 30.0, 60.0], [1.0, 2.0, 3.0]),
            "wheel_ccw": ([0.0, 30.0], [1.5, 2.5]),
        }
        headers, rows, val_cols = report.deviation_table(series)
        self.assertEqual(headers[0], "指令角度[°]")
        self.assertEqual(headers[1:], ["ホイール CW", "ホイール CCW"])
        self.assertEqual(len(rows), 3)        # 角度 0/30/60
        self.assertEqual(val_cols, [1, 2])
        # 60°行は CCW が空
        self.assertEqual(rows[2][2], "")


if __name__ == "__main__":
    unittest.main()
