# -*- coding: utf-8 -*-
"""測定結果のCSV保存・読み込み（Excelで開ける Shift-JIS / cp932）

保存パスの規則:
    <保存先ルート>/<型式の系列フォルダ>/<機番>.csv
    例: 型式「RWE-200」機番「12345」 → 測定データ/RWE/12345.csv
"""

import csv
import re
from datetime import datetime
from pathlib import Path

from .analysis import deviation_sec, judge_backlash
from .sequence import SERIES_KEYS, SERIES_LABELS

RESULT_LABELS = {
    "wheel_cw": "ホイールCW",
    "wheel_ccw": "ホイールCCW",
    "worm_cw": "ウォームCW",
    "worm_ccw": "ウォームCCW",
}

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str) -> str:
    """Windowsで使えない文字を置き換える"""
    cleaned = _INVALID_FILENAME_CHARS.sub("_", (name or "").strip())
    return cleaned or "無題"


def model_folder(model: str) -> str:
    """型式から保存フォルダ名を作る（ハイフン前の系列記号）。例 RWE-200 → RWE"""
    m = (model or "").strip()
    head = m.split("-")[0].strip()
    return sanitize_filename(head or m or "その他")


def build_save_path(root, model: str, machine_no: str) -> Path:
    """保存先ルート＋型式＋機番 → 保存ファイルパス"""
    return Path(root) / model_folder(model) / f"{sanitize_filename(machine_no)}.csv"


def backlash_judgement_text(summary, temp_c, spec):
    """測定温度に応じたホイールバックラッシの合否判定文。判定できなければ None。"""
    if "wheel_backlash" not in summary or temp_c is None:
        return None
    ok, band = judge_backlash(
        summary["wheel_backlash"]["min"], summary["wheel_backlash"]["max"], temp_c, spec
    )
    if ok is None:
        return f"判定不可（{temp_c:g}°Cの規格が未設定）"
    verdict = "OK" if ok else "NG"
    return f'{verdict}（規格 {band["min"]:g}〜{band["max"]:g}" @ {temp_c:g}°C）'


def series_rows(summary):
    """系列ごとの結果 → (項目, 値) 行リスト"""
    rows = []
    for key, label in RESULT_LABELS.items():
        if key in summary:
            s = summary[key]
            rows.append((f"{label} 精度PP", f'{s["pp"]:.2f}"'))
            rows.append((f"{label} 単一誤差", f'{s["single"]:.2f}"'))
            rows.append((f"{label} 隣接誤差", f'{s["adjacent"]:.2f}"'))
            rows.append((f"{label} 傾き", f'{s["slope"]:+.2f}"'))
    return rows


def misc_rows(summary, judgement_text=None):
    """バックラッシ・合否判定・真の最大最小 → (項目, 値) 行リスト"""
    rows = []
    for grp, label in (("wheel", "ホイール"), ("worm", "ウォーム")):
        key = f"{grp}_backlash"
        if key in summary:
            rows.append((f"{label} バックラッシ MIN", f'{summary[key]["min"]:.2f}"'))
            rows.append((f"{label} バックラッシ MAX", f'{summary[key]["max"]:.2f}"'))
    if judgement_text is not None:
        rows.append(("ホイール バックラッシ 判定", judgement_text))
    for dirn, label in (("cw", "CW"), ("ccw", "CCW")):
        if dirn in summary.get("true", {}):
            rows.append((f"真の最大 ({label})", f'{summary["true"][dirn]["true_max"]:.2f}"'))
            rows.append((f"真の最小 ({label})", f'{summary["true"][dirn]["true_min"]:.2f}"'))
    return rows


def result_rows(summary, judgement_text=None):
    """結果サマリ全体 → 表示・保存用の (項目, 値) 行リスト"""
    return series_rows(summary) + misc_rows(summary, judgement_text)


def save_csv(path, data, summary, meta=None, judgement_text=None):
    """測定生データと結果サマリを1つのCSVに保存する。

    meta: 型式・機番・日付・名前・測定温度・測定条件などの dict（ロード時に復元される）
    judgement_text: バックラッシ合否の判定文（結果サマリに含めて保存）
    """
    with open(path, "w", newline="", encoding="cp932", errors="replace") as f:
        w = csv.writer(f)
        w.writerow(["ND287 分割測定結果"])
        w.writerow(["保存日時", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        for key, value in (meta or {}).items():
            w.writerow([key, value])
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
        for item, value in result_rows(summary, judgement_text):
            w.writerow([item, value])


def load_csv(path):
    """save_csv が書いたCSVを読み戻す。(meta, data) を返す。

    結果サマリ行は読まない（ロード後に生データから再計算する）。
    """
    label_to_key = {v: k for k, v in SERIES_LABELS.items()}
    meta = {}
    data = {k: ([], []) for k in SERIES_KEYS}
    with open(path, encoding="cp932") as f:
        rows = list(csv.reader(f))
    mode = "meta"
    for row in rows:
        if not row or not row[0]:
            continue
        if row[0] == "系列":
            mode = "data"
            continue
        if row[0] == "項目":
            break
        if mode == "meta":
            if row[0] != "ND287 分割測定結果" and len(row) >= 2:
                meta[row[0]] = row[1]
        elif row[0] in label_to_key and len(row) >= 3:
            key = label_to_key[row[0]]
            data[key][0].append(float(row[1]))
            data[key][1].append(float(row[2]))
    return meta, data
