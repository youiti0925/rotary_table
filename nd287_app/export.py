# -*- coding: utf-8 -*-
"""測定結果のCSV保存（Excelで開ける Shift-JIS / cp932）"""

import csv
from datetime import datetime

from .analysis import deviation_sec
from .sequence import SERIES_KEYS, SERIES_LABELS

RESULT_LABELS = {
    "wheel_cw": "ホイールCW",
    "wheel_ccw": "ホイールCCW",
    "worm_cw": "ウォームCW",
    "worm_ccw": "ウォームCCW",
}


def result_rows(summary):
    """結果サマリ → 表示・保存用の (項目, 値) 行リスト"""
    rows = []
    for key, label in RESULT_LABELS.items():
        if key in summary:
            rows.append((f"{label} 精度PP", f'{summary[key]["pp"]:.2f}"'))
            rows.append((f"{label} 隣接", f'{summary[key]["adjacent"]:.2f}"'))
    for grp, label in (("wheel", "ホイール"), ("worm", "ウォーム")):
        key = f"{grp}_backlash"
        if key in summary:
            rows.append((f"{label} バックラッシ MIN", f'{summary[key]["min"]:.2f}"'))
            rows.append((f"{label} バックラッシ MAX", f'{summary[key]["max"]:.2f}"'))
    for dirn, label in (("cw", "CW"), ("ccw", "CCW")):
        if dirn in summary.get("true", {}):
            rows.append((f"真の最大 ({label})", f'{summary["true"][dirn]["true_max"]:.2f}"'))
            rows.append((f"真の最小 ({label})", f'{summary["true"][dirn]["true_min"]:.2f}"'))
    return rows


def save_csv(path, data, summary):
    """測定生データと結果サマリを1つのCSVに保存する。"""
    with open(path, "w", newline="", encoding="cp932", errors="replace") as f:
        w = csv.writer(f)
        w.writerow(["ND287 分割測定結果"])
        w.writerow(["保存日時", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow([])
        w.writerow(["系列", "指令角度[°]", "測定値[°]", "偏差[\"]"])
        for key in SERIES_KEYS:
            targets, measured = data[key]
            if not targets:
                continue
            devs = deviation_sec(targets, measured)
            for t, m, d in zip(targets, measured, devs):
                w.writerow([SERIES_LABELS[key], f"{t:.4f}", f"{m:.6f}", f"{d:.2f}"])
        w.writerow([])
        w.writerow(["項目", "値"])
        for item, value in result_rows(summary):
            w.writerow([item, value])
