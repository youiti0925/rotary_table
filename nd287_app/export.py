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

from .analysis import deviation_sec, judge_minmax, rep_unwrap
from .sequence import SERIES_KEYS, SERIES_LABELS

RESULT_LABELS = {
    "wheel_cw": "ホイールCW",
    "wheel_ccw": "ホイールCCW",
    "worm_cw": "ウォームCW",
    "worm_ccw": "ウォームCCW",
}

MODE_KEY = "測定モード"

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


def judgement_texts(summary, temp_c, spec_map):
    """測定温度に応じた合否判定文を項目ごとに作る。

    spec_map: settings.json の judgement_spec
              （wheel_backlash / worm_backlash / true の3項目、空なら判定しない）
    返り値: {"wheel_backlash": "OK（…）", "worm_backlash": …, "true": …}
    """
    out = {}
    if temp_c is None or not spec_map:
        return out

    def fmt(ok, band):
        if ok is None:
            return f"判定不可（{temp_c:g}°Cの規格が未設定）"
        verdict = "OK" if ok else "NG"
        return f'{verdict}（規格 {band["min"]:g}〜{band["max"]:g}" @ {temp_c:g}°C）'

    for key in ("wheel_backlash", "worm_backlash"):
        if key in summary and spec_map.get(key):
            ok, band = judge_minmax(
                summary[key]["min"], summary[key]["max"], temp_c, spec_map[key]
            )
            out[key] = fmt(ok, band)
    true = summary.get("true") or {}
    if true and spec_map.get("true"):
        # CW/CCW合わせた最悪値（真の最小の最小・真の最大の最大）で総合判定
        tmin = min(v["true_min"] for v in true.values())
        tmax = max(v["true_max"] for v in true.values())
        ok, band = judge_minmax(tmin, tmax, temp_c, spec_map["true"])
        out["true"] = fmt(ok, band)
    return out


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


def misc_rows(summary, judgements=None):
    """バックラッシ・合否判定・真の最大最小 → (項目, 値) 行リスト

    judgements: judgement_texts() の返り値
    """
    judgements = judgements or {}
    rows = []
    if "backlash_correction" in summary:
        rows.append(("バックラッシ手動補正", f'{summary["backlash_correction"]:+.2f}"'))
    for grp, label in (("wheel", "ホイール"), ("worm", "ウォーム")):
        key = f"{grp}_backlash"
        if key in summary:
            rows.append((f"{label} バックラッシ MIN", f'{summary[key]["min"]:.2f}"'))
            rows.append((f"{label} バックラッシ MAX", f'{summary[key]["max"]:.2f}"'))
            if key in judgements:
                rows.append((f"{label} バックラッシ 判定", judgements[key]))
    for dirn, label in (("cw", "CW"), ("ccw", "CCW")):
        if dirn in summary.get("true", {}):
            rows.append((f"真の最大 ({label})", f'{summary["true"][dirn]["true_max"]:.2f}"'))
            rows.append((f"真の最小 ({label})", f'{summary["true"][dirn]["true_min"]:.2f}"'))
    if "true" in judgements:
        rows.append(("総合（真の最大最小） 判定", judgements["true"]))
    return rows


def result_rows(summary, judgements=None):
    """結果サマリ全体 → 表示・保存用の (項目, 値) 行リスト"""
    return series_rows(summary) + misc_rows(summary, judgements)


def save_csv(path, data, summary, meta=None, judgements=None):
    """測定生データと結果サマリを1つのCSVに保存する。

    meta: 型式・機番・日付・名前・測定温度・測定条件などの dict（ロード時に復元される）
    judgements: judgement_texts() の返り値（結果サマリに含めて保存）
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
        for item, value in result_rows(summary, judgements):
            w.writerow([item, value])


def repeat_result_rows(rsum):
    """再現性測定の結果サマリ → (項目, 値) 行リスト

    rsum: analysis.repeatability_summary() の返り値
    """
    rows = []
    for i, b in enumerate(rsum["blocks"]):
        for dirn, label in (("cw", "CW"), ("ccw", "CCW")):
            if b[dirn] is not None:
                rows.append((f"ブロック{i + 1} ({b['angle']:g}°) {label}範囲", f'{b[dirn]:.2f}"'))
    for key, label in (
        ("cw", "再現性 CW（全ブロック最大）"),
        ("ccw", "再現性 CCW（全ブロック最大）"),
        ("overall", "再現性 総合"),
    ):
        if rsum.get(key) is not None:
            rows.append((label, f'{rsum[key]:.2f}"'))
    return rows


def save_repeat_csv(path, points, data, rsum, meta=None):
    """再現性測定の生データと結果サマリをCSVに保存する。

    points/data: RepeatabilitySequence の points / data
    """
    with open(path, "w", newline="", encoding="cp932", errors="replace") as f:
        w = csv.writer(f)
        w.writerow(["ND287 再現性測定結果"])
        w.writerow(["保存日時", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        for key, value in (meta or {}).items():
            w.writerow([key, value])
        w.writerow([])
        w.writerow(["ブロック", "指令角度[°]", "方向", "回", "測定値[°]", "偏差[\"]"])
        for i, angle in enumerate(points):
            for dirn, label in (("cw", "CW"), ("ccw", "CCW")):
                for r, v in enumerate(data.get((dirn, i), []), start=1):
                    dev = (rep_unwrap(angle, v) - angle) * 3600.0  # 0/360またぎ対策
                    w.writerow(
                        [i + 1, f"{angle:.4f}", label, r, f"{v:.6f}", f"{dev:.2f}"]
                    )
        w.writerow([])
        w.writerow(["項目", "値"])
        for item, value in repeat_result_rows(rsum):
            w.writerow([item, value])


def _read_rows(path):
    with open(path, encoding="cp932") as f:
        return list(csv.reader(f))


def _parse_indexing(rows):
    label_to_key = {v: k for k, v in SERIES_LABELS.items()}
    data = {k: ([], []) for k in SERIES_KEYS}
    in_data = False
    for row in rows:
        if not row or not row[0]:
            continue
        if row[0] == "系列":
            in_data = True
            continue
        if row[0] == "項目":
            break
        if in_data and row[0] in label_to_key and len(row) >= 3:
            key = label_to_key[row[0]]
            data[key][0].append(float(row[1]))
            data[key][1].append(float(row[2]))
    return data


def _parse_repeat(rows):
    angles = {}  # ブロック番号(0始まり) -> 指令角度
    data = {}
    in_data = False
    for row in rows:
        if not row or not row[0]:
            continue
        if row[0] == "ブロック":
            in_data = True
            continue
        if row[0] == "項目":
            break
        if in_data and len(row) >= 5:
            block = int(row[0]) - 1
            angles[block] = float(row[1])
            dirn = "cw" if row[2].upper() == "CW" else "ccw"
            data.setdefault((dirn, block), []).append(float(row[4]))
    points = [angles[i] for i in sorted(angles)]
    return points, data


def load_measurement(path):
    """保存済みCSVを測定モードを判別して読み戻す。

    返り値: (meta, kind, payload)
      kind="indexing" → payload は系列データ dict
      kind="repeat"   → payload は (points, data)
    """
    rows = _read_rows(path)
    meta = {}
    header = None
    for row in rows:
        if not row or not row[0]:
            continue
        if row[0] in ("系列", "ブロック", "項目"):
            header = row[0]
            break
        if not row[0].startswith("ND287") and len(row) >= 2:
            meta[row[0]] = row[1]
    is_repeat = "再現性" in meta.get(MODE_KEY, "") or header == "ブロック"
    if is_repeat:
        return meta, "repeat", _parse_repeat(rows)
    return meta, "indexing", _parse_indexing(rows)


def load_csv(path):
    """save_csv が書いた分割測定CSVを読み戻す。(meta, data) を返す。

    結果サマリ行は読まない（ロード後に生データから再計算する）。
    """
    meta, kind, payload = load_measurement(path)
    if kind != "indexing":
        raise ValueError("分割測定のファイルではありません")
    return meta, payload
