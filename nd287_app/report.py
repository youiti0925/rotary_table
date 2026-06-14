# -*- coding: utf-8 -*-
"""過去データの集計（横断比較・分析用）

保存済みCSVを走査して、1ファイル＝1レコードの指標一覧にまとめる。
分析画面の横断比較テーブル／グラフ／CSV・Excel出力の共通データ源になる。
"""

import csv
from pathlib import Path

from .analysis import repeatability_summary, summarize
from .export import MODE_KEY, load_measurement
from .sequence import SERIES_LABELS

# 横断比較テーブルの先頭（文字列）列
BASE_COLUMNS = ["日付", "型式", "機番", "名前", "モード", "測定温度", "判定"]

# 数値指標の標準的な並び順（存在するものだけ後で抽出する）
METRIC_SUFFIXES = ("精度PP", "単一誤差", "隣接誤差", "傾き")


def _canonical_metrics():
    order = []
    for label in SERIES_LABELS.values():
        for suffix in METRIC_SUFFIXES:
            order.append(f"{label} {suffix}")
    for label in ("ホイール", "ウォーム"):
        order.append(f"{label}BL MIN")
        order.append(f"{label}BL MAX")
    order += ["再現性CW", "再現性CCW", "再現性総合"]
    return order


CANONICAL_METRICS = _canonical_metrics()


def _to_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def scan_judgement(path):
    """保存CSVの結果サマリから総合判定（NGが1つでもあればNG）を拾う"""
    try:
        text = Path(path).read_text(encoding="cp932", errors="ignore")
    except Exception:
        return ""
    if "NG（" in text or "NG(" in text:
        return "NG"
    if "OK（" in text or "OK(" in text:
        return "OK"
    return ""


def measurement_record(path):
    """1つの保存CSVを読み、横断比較用のレコード(dict)にする。

    metrics は {指標名: 数値[秒]} 。型式・機番などは文字列。読めない値は欠落。
    """
    meta, kind, payload = load_measurement(str(path))
    rec = {
        "パス": str(path),
        "ファイル": Path(path).name,
        "日付": meta.get("日付", ""),
        "型式": meta.get("型式", ""),
        "機番": meta.get("機番", Path(path).stem),
        "名前": meta.get("名前", ""),
        "モード": meta.get(MODE_KEY, ""),
        "測定温度": _to_float(meta.get("測定温度[°C]")),
        "判定": scan_judgement(path),
        "metrics": {},
    }
    metrics = rec["metrics"]
    if kind == "repeat":
        points, data = payload
        rsum = repeatability_summary(points, data)
        for key, name in (("cw", "再現性CW"), ("ccw", "再現性CCW"),
                          ("overall", "再現性総合")):
            if rsum.get(key) is not None:
                metrics[name] = float(rsum[key])
    else:
        blcorr = _to_float(meta.get("バックラッシ補正[秒]")) or 0.0
        summary, _ = summarize(payload, blcorr)
        for key, label in SERIES_LABELS.items():
            if key in summary:
                s = summary[key]
                metrics[f"{label} 精度PP"] = float(s["pp"])
                metrics[f"{label} 単一誤差"] = float(s["single"])
                metrics[f"{label} 隣接誤差"] = float(s["adjacent"])
                metrics[f"{label} 傾き"] = float(s["slope"])
        for grp, label in (("wheel", "ホイール"), ("worm", "ウォーム")):
            bkey = f"{grp}_backlash"
            if bkey in summary:
                metrics[f"{label}BL MIN"] = float(summary[bkey]["min"])
                metrics[f"{label}BL MAX"] = float(summary[bkey]["max"])
    return rec


def scan_measurements(root, recent=None, model=None):
    """保存先ルート以下のCSVを新しい順に走査してレコード列を返す。

    recent: 最大件数（Noneで全件）。model 指定時はその型式に一致するものだけ。
    再現側ファイル(_再現.csv)は分割の付随ファイルなので除外する。
    """
    root = Path(root)
    files = []
    try:
        for p in root.rglob("*.csv"):
            if p.name.endswith("_再現.csv"):
                continue
            files.append(p)
    except Exception:
        files = []
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    records = []
    for p in files:
        try:
            rec = measurement_record(p)
        except Exception:
            continue
        if model and rec["型式"] != model:
            continue
        records.append(rec)
        if recent and len(records) >= recent:
            break
    return records


def distinct_models(records):
    """レコード列から型式の一覧（出現順）"""
    seen = []
    for r in records:
        m = r.get("型式") or ""
        if m and m not in seen:
            seen.append(m)
    return seen


def available_metrics(records):
    """レコード列に実際に現れる指標名を標準順で返す（グラフ・列選択用）"""
    present = set()
    for r in records:
        present.update(r["metrics"].keys())
    ordered = [m for m in CANONICAL_METRICS if m in present]
    extras = [m for m in present if m not in CANONICAL_METRICS]
    return ordered + sorted(extras)


def _fmt_temp(value):
    return "" if value is None else f"{value:g}"


def build_comparison_table(records, metric_columns=None):
    """レコード列 → (ヘッダ, 行リスト)。数値は小数2桁の文字列にする。"""
    if metric_columns is None:
        metric_columns = available_metrics(records)
    headers = BASE_COLUMNS + list(metric_columns)
    rows = []
    for r in records:
        row = [
            r.get("日付", ""), r.get("型式", ""), r.get("機番", ""),
            r.get("名前", ""), r.get("モード", ""),
            _fmt_temp(r.get("測定温度")), r.get("判定", ""),
        ]
        for name in metric_columns:
            v = r["metrics"].get(name)
            row.append("" if v is None else f"{v:.2f}")
        rows.append(row)
    return headers, rows


def deviation_table(series_devs):
    """{key:(targets, devs)} を 指令角度×各系列偏差 の表にする。

    1件詳細のExcel/CSV・ネイティブグラフ用。
    返り値: (headers, rows, val_cols)。val_cols は系列偏差列の0始まり番号。
    """
    keys = [k for k in SERIES_LABELS if series_devs.get(k) and len(series_devs[k][0])]
    angles = sorted({round(float(t), 4) for k in keys for t in series_devs[k][0]})
    maps = {
        k: {round(float(t), 4): dv for t, dv in zip(series_devs[k][0], series_devs[k][1])}
        for k in keys
    }
    headers = ["指令角度[°]"] + [SERIES_LABELS[k] for k in keys]
    rows = []
    for a in angles:
        row = [f"{a:g}"]
        for k in keys:
            v = maps[k].get(a)
            row.append("" if v is None else f"{v:.2f}")
        rows.append(row)
    val_cols = list(range(1, len(keys) + 1))
    return headers, rows, val_cols


def write_table_csv(path, headers, rows):
    """ヘッダ＋行を Excel で開けるCSV(cp932)で書き出す。"""
    with open(path, "w", newline="", encoding="cp932", errors="replace") as f:
        w = csv.writer(f)
        if headers:
            w.writerow(list(headers))
        for row in rows:
            w.writerow(list(row))
