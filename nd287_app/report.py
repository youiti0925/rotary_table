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
    order += ["任意誤差 CW", "任意誤差 CCW"]   # 傾斜分割（精度=H+W）
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


def metrics_from_measurement(kind, payload, meta):
    """読み込み済みの測定（kind/payload/meta）から指標 {名前: 数値[秒]} を作る。

    既に load_measurement したデータから計算したいとき（過去データ一覧など）用。
    """
    metrics = {}
    if kind == "repeat":
        points, data = payload
        rsum = repeatability_summary(points, data)
        for key, name in (("cw", "再現性CW"), ("ccw", "再現性CCW"),
                          ("overall", "再現性総合")):
            if rsum.get(key) is not None:
                metrics[name] = float(rsum[key])
        return metrics
    blcorr = _to_float((meta or {}).get("バックラッシ補正[秒]")) or 0.0
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
    return metrics


def measurement_record(path):
    """1つの保存CSVを読み、横断比較用のレコード(dict)にする。

    metrics は {指標名: 数値[秒]} 。型式・機番などは文字列。読めない値は欠落。
    """
    meta, kind, payload = load_measurement(str(path))
    return {
        "パス": str(path),
        "ファイル": Path(path).name,
        "日付": meta.get("日付", ""),
        "型式": meta.get("型式", ""),
        "機番": meta.get("機番", Path(path).stem),
        "名前": meta.get("名前", ""),
        "モード": meta.get(MODE_KEY, ""),
        "測定温度": _to_float(meta.get("測定温度[°C]")),
        "判定": scan_judgement(path),
        "metrics": metrics_from_measurement(kind, payload, meta),
    }


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


XAXIS_FIELDS = ("機番", "日付", "型式")
AGG_FUNCS = {
    "mean": lambda xs: sum(xs) / len(xs),
    "max": max,
    "min": min,
}


def aggregate_series(records, metric, x_field="機番", agg="none"):
    """横断比較グラフ用の (ラベル列, 値列) を作る。

    metric : 対象の指標名（available_metrics の値）
    x_field: 横軸にする項目（"機番" / "日付" / "型式"）
    agg    : 集計方法。
             "none" … 各測定を1点ずつ（x_field で昇順）。推移・個別比較向き。
             "mean"/"max"/"min" … x_field でまとめて集計（1カテゴリ1点）。
    指標値が無い測定は除外する。
    """
    pairs = []
    for r in records:
        v = r["metrics"].get(metric)
        if v is None:
            continue
        pairs.append((str(r.get(x_field, "") or ""), float(v)))
    if not pairs:
        return [], []
    if agg == "none":
        pairs.sort(key=lambda kv: kv[0])
        return [k for k, _ in pairs], [v for _, v in pairs]
    groups = {}
    for k, v in pairs:
        groups.setdefault(k, []).append(v)
    func = AGG_FUNCS[agg]
    labels = sorted(groups)
    return labels, [float(func(groups[k])) for k in labels]


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


def _head_lines(path, n):
    """ファイルの先頭 n 行だけ読む（cp932）。データ行を読まない＝高速。"""
    out = []
    with open(path, encoding="cp932", errors="ignore") as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            out.append(line.rstrip("\r\n"))
    return out


def _csv_floats(line, count=None):
    vals = [(_to_float(tok.strip()) if tok.strip() else None)
            for tok in (line or "").split(",")]
    return vals[:count] if count else vals


def record_from_bs(path):
    """.BS（回転分割）の**ヘッダだけ**読んでレコードを作る（データ行は読まない＝高速）。

    精度=精度2(acc2)・傾き・総合バックラッシ（規格MIN/MAX欄＝旧アプリが書いた総合値）を
    そのまま使う（再計算しない）。
    """
    lines = _head_lines(path, 13)
    if len(lines) < 13:
        raise ValueError("not a .BS header")
    model = lines[0].strip()
    meta = (lines[1] or "").split(",")
    points = _csv_floats(lines[4], 4)
    slopes = _csv_floats(lines[5], 4)
    acc2 = _csv_floats(lines[7], 4)
    line13 = _csv_floats(lines[12])
    spec_min = line13[4] if len(line13) > 5 else None
    spec_max = line13[5] if len(line13) > 5 else None
    metrics = {}
    # HR=ホイールCW, HL=ホイールCCW, WR=ウォームCW, WL=ウォームCCW
    for i, lab in ((0, "ホイール CW"), (3, "ホイール CCW"),
                   (1, "ウォーム CW"), (2, "ウォーム CCW")):
        if i < len(points) and points[i]:  # 測定した系列だけ
            if i < len(acc2) and acc2[i] is not None:
                metrics[f"{lab} 精度PP"] = acc2[i]
            if i < len(slopes) and slopes[i] is not None:
                metrics[f"{lab} 傾き"] = slopes[i]
    if spec_min is not None and spec_max is not None:
        metrics["総合BL MIN"] = spec_min
        metrics["総合BL MAX"] = spec_max
        metrics["総合BL 平均"] = (spec_min + spec_max) / 2.0
        metrics["総合BL 差"] = spec_max - spec_min
    return {
        "パス": str(path), "ファイル": Path(path).name,
        "日付": meta[0].strip() if meta else "", "型式": model,
        "機番": Path(path).stem,
        "名前": meta[1].strip() if len(meta) > 1 else "",
        "モード": "回転分割",
        "測定温度": _to_float(meta[2]) if len(meta) > 2 else None,
        "判定": "", "metrics": metrics,
    }


def record_from_ks(path):
    """.KS（傾斜分割）の**ヘッダだけ**読んでレコードを作る（データ行は読まない）。

    精度(正/逆のH・W)と総合バックラッシ（MAX/MIN行の全範囲値）をそのまま使う。
    """
    lines = _head_lines(path, 14)
    if len(lines) < 14:
        raise ValueError("not a .KS header")
    model = lines[0].strip()
    meta = (lines[1] or "").split(",")
    acc_cw = _csv_floats(lines[5])    # 精度H, 精度W, 精度
    acc_ccw = _csv_floats(lines[6])
    max3 = _csv_floats(lines[9])      # 総合バックラッシ MAX（全範囲/範囲1/範囲2）
    min3 = _csv_floats(lines[10])
    metrics = {}

    def put(key, arr, idx):
        if len(arr) > idx and arr[idx] is not None:
            metrics[key] = arr[idx]
    put("ホイール CW 精度PP", acc_cw, 0)
    put("ウォーム CW 精度PP", acc_cw, 1)
    put("任意誤差 CW", acc_cw, 2)          # 精度=H+W（傾斜分割の任意誤差）
    put("ホイール CCW 精度PP", acc_ccw, 0)
    put("ウォーム CCW 精度PP", acc_ccw, 1)
    put("任意誤差 CCW", acc_ccw, 2)
    mn = min3[0] if min3 else None
    mx = max3[0] if max3 else None
    if mn is not None and mx is not None:
        metrics["総合BL MIN"] = mn
        metrics["総合BL MAX"] = mx
        metrics["総合BL 平均"] = (mn + mx) / 2.0
        metrics["総合BL 差"] = mx - mn
    return {
        "パス": str(path), "ファイル": Path(path).name,
        "日付": meta[0].strip() if meta else "", "型式": model,
        "機番": Path(path).stem,
        "名前": meta[1].strip() if len(meta) > 1 else "",
        "モード": "傾斜分割", "測定温度": None, "判定": "", "metrics": metrics,
    }


def search_inspection(root, model="", machine="", limit=None):
    """.BS/.KS の検査表フォルダを型式・機番で検索してレコード列を返す（新しい順）。

    型式があるときは**その頭文字フォルダ（例 RWE）だけ**を走査する（全体を歩かない＝
    ネットワーク共有でも軽い）。各ファイルはヘッダだけ読む。limit で早期打切り。
    """
    root = Path(root)
    records = []
    if not root.exists():
        return records
    model_u = (model or "").strip().upper()
    machine_s = (machine or "").strip()
    series = model_u.split("-")[0] if model_u else ""   # 例 RWE-200 → RWE
    has_number = any(c.isdigit() for c in model_u)

    paths = []
    seen = set()

    def collect(folder, recursive):
        globber = folder.rglob if recursive else folder.glob
        for pattern in ("*.[bB][sS]", "*.[kK][sS]"):
            try:
                for p in globber(pattern):
                    if p not in seen:
                        seen.add(p)
                        paths.append(p)
            except Exception:
                pass

    folders = []
    if series and root.is_dir():
        try:
            folders = [d for d in root.iterdir()
                       if d.is_dir() and series in d.name.upper()]
        except Exception:
            folders = []
    if folders:
        for d in folders:
            collect(d, recursive=True)
        collect(root, recursive=False)   # フォルダ分けせずroot直下に置いた分も拾う
    else:
        collect(root, recursive=True)    # 型式なし/該当フォルダなし → 全体

    if machine_s:
        paths = [p for p in paths if machine_s in p.stem]
    try:
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        pass
    for p in paths:
        try:
            rec = (record_from_bs(p) if p.suffix.lower() == ".bs"
                   else record_from_ks(p))
        except Exception:
            continue
        if model_u and has_number and model_u not in (rec["型式"] or "").upper() \
                and series not in p.parent.name.upper():
            continue
        records.append(rec)
        if limit and len(records) >= limit:
            break
    return records


def write_table_csv(path, headers, rows):
    """ヘッダ＋行を Excel で開けるCSV(cp932)で書き出す。"""
    with open(path, "w", newline="", encoding="cp932", errors="replace") as f:
        w = csv.writer(f)
        if headers:
            w.writerow(list(headers))
        for row in rows:
            w.writerow(list(row))
