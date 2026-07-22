# -*- coding: utf-8 -*-
"""JIS B 6190-2（ISO 230-2）による位置決め精度評価（回転軸）。

再現性測定のデータ（ブロック指令角度＋方向別の繰返し読み取り）から、
規格の統計値を計算する。単位は秒["]（偏差＝(測定−指令)×3600。0/360また
ぎは analysis.rep_unwrap で折り返し）。

方向の対応: CW（時計回り）＝ ↑（正方向接近）、CCW ＝ ↓（負方向接近）。

規格の式（ISO 230-2:2014 ＝ JIS B 6190-2、3.13〜3.27）:
  位置 i・方向ごと（n回接近）:
    x̄i↑ = (1/n)Σ xij↑                      … 平均片方向位置決め偏差
    si↑ = √( (1/(n-1)) Σ (xij↑ − x̄i↑)² )   … 推定量（標本標準偏差）
    Ri↑ = 4 si↑                             … 片方向繰返し性
    Bi  = x̄i↑ − x̄i↓                        … 反転値
    Ri  = max( 2si↑ + 2si↓ + |Bi| ; Ri↑ ; Ri↓ ) … 双方向繰返し性
  軸まとめ:
    E↑ = max(x̄i↑) − min(x̄i↑)（E↓同様）     … 片方向系統位置決め誤差
    E  = max(x̄i↑; x̄i↓) − min(x̄i↑; x̄i↓)    … 双方向系統位置決め誤差
    M  = max(x̄i) − min(x̄i)  （x̄i=(x̄i↑+x̄i↓)/2）… 平均双方向位置決め誤差
    B  = max|Bi|、 B̄ = (1/m)ΣBi             … 軸反転値・平均反転値
    R↑ = max(Ri↑)（R↓同様）、 R = max(Ri)    … 繰返し性
    A↑ = max(x̄i↑+2si↑) − min(x̄i↑−2si↑)（A↓同様）… 片方向位置決め精度
    A  = max(x̄i↑+2si↑; x̄i↓+2si↓) − min(x̄i↑−2si↑; x̄i↓−2si↓) … 双方向位置決め精度

既存の測定値・画面表示・CSV・.RS/.RSK出力には一切影響しない（読み取り専用）。
試験サイクルの目安（JIS B 6190-2）: 各方向 n=5回、目標位置は 360°軸で
0/90/180/270°を含む8点以上（±30%のランダムオフセット推奨）。回数・点数が
少なくても計算はする（n<2 の方向は s が出ないため除外し notes で知らせる）。
"""

import math

from .analysis import rep_unwrap

DIR_UP, DIR_DN = "cw", "ccw"          # ↑=CW / ↓=CCW


def _mean(xs):
    return sum(xs) / len(xs)


def _sd(xs):
    n = len(xs)
    if n < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _devs(angle, vals):
    """ブロックの読み[deg] → 偏差[秒]のリスト（0/360またぎは折り返し）。"""
    return [(rep_unwrap(angle, v) - angle) * 3600.0 for v in vals]


def iso_stats(points, data) -> dict:
    """再現性データ → JIS B 6190-2 の統計値。

    points: ブロックの指令角度リスト
    data:   {("cw"|"ccw", ブロック番号): [測定値deg, …]}（RepeatabilitySequence.data）

    戻り値:
      positions: [{angle, n_up, mean_up, s_up, r_up, n_dn, mean_dn, s_dn, r_dn,
                   b, r}]   … s が出ない方向（n<2）は mean/s/r が None
      axis: {A, A_up, A_dn, R, R_up, R_dn, E, E_up, E_dn, M, B, B_mean, m,
             n_min, n_max} … 対象データが無い値は None。m は評価に使えた位置数
      notes: [str]         … 除外・回数など評価の注意書き
    """
    points = list(points or [])
    data = data or {}
    positions = []
    notes = []
    for i, angle in enumerate(points):
        row = {"angle": float(angle)}
        for dirn, tag in ((DIR_UP, "up"), (DIR_DN, "dn")):
            devs = _devs(angle, data.get((dirn, i)) or [])
            row[f"n_{tag}"] = len(devs)
            s = _sd(devs)
            if s is None:
                row[f"mean_{tag}"] = row[f"s_{tag}"] = row[f"r_{tag}"] = None
            else:
                row[f"mean_{tag}"] = _mean(devs)
                row[f"s_{tag}"] = s
                row[f"r_{tag}"] = 4.0 * s
        if row["mean_up"] is not None and row["mean_dn"] is not None:
            row["b"] = row["mean_up"] - row["mean_dn"]
            row["r"] = max(2.0 * row["s_up"] + 2.0 * row["s_dn"] + abs(row["b"]),
                           row["r_up"], row["r_dn"])
        else:
            row["b"] = row["r"] = None
        positions.append(row)

    def vals(key):
        return [p[key] for p in positions if p[key] is not None]

    up = [p for p in positions if p["mean_up"] is not None]
    dn = [p for p in positions if p["mean_dn"] is not None]
    both = [p for p in positions if p["b"] is not None]

    axis = {k: None for k in ("A", "A_up", "A_dn", "R", "R_up", "R_dn",
                              "E", "E_up", "E_dn", "M", "B", "B_mean")}
    axis["m"] = len(both)
    ns = [p["n_up"] for p in up] + [p["n_dn"] for p in dn]
    axis["n_min"] = min(ns) if ns else 0
    axis["n_max"] = max(ns) if ns else 0

    if up:
        axis["E_up"] = max(p["mean_up"] for p in up) - min(p["mean_up"] for p in up)
        axis["A_up"] = (max(p["mean_up"] + 2 * p["s_up"] for p in up)
                        - min(p["mean_up"] - 2 * p["s_up"] for p in up))
        axis["R_up"] = max(p["r_up"] for p in up)
    if dn:
        axis["E_dn"] = max(p["mean_dn"] for p in dn) - min(p["mean_dn"] for p in dn)
        axis["A_dn"] = (max(p["mean_dn"] + 2 * p["s_dn"] for p in dn)
                        - min(p["mean_dn"] - 2 * p["s_dn"] for p in dn))
        axis["R_dn"] = max(p["r_dn"] for p in dn)
    if up or dn:
        means = ([p["mean_up"] for p in up] + [p["mean_dn"] for p in dn])
        axis["E"] = max(means) - min(means)
        highs = ([p["mean_up"] + 2 * p["s_up"] for p in up]
                 + [p["mean_dn"] + 2 * p["s_dn"] for p in dn])
        lows = ([p["mean_up"] - 2 * p["s_up"] for p in up]
                + [p["mean_dn"] - 2 * p["s_dn"] for p in dn])
        axis["A"] = max(highs) - min(lows)
    if both:
        bis = [p["b"] for p in both]
        axis["B"] = max(abs(b) for b in bis)
        axis["B_mean"] = _mean(bis)
        mids = [(p["mean_up"] + p["mean_dn"]) / 2.0 for p in both]
        axis["M"] = max(mids) - min(mids)
        axis["R"] = max(p["r"] for p in both)

    # 注意書き（規格の推奨サイクルとの差・除外を作業者へ知らせる）
    skipped = [f'{p["angle"]:g}°' for p in positions
               if p["mean_up"] is None or p["mean_dn"] is None]
    if skipped:
        notes.append("読みが片方向のみ／2回未満のため双方向評価から除外: "
                     + "、".join(skipped))
    if axis["n_min"] and axis["n_min"] < 5:
        notes.append(f"接近回数が {axis['n_min']} 回の位置があります"
                     "（JIS B 6190-2 の標準サイクルは各方向5回）")
    if 0 < len(points) < 8:
        notes.append(f"目標位置 {len(points)} 点（JISは360°軸で 0/90/180/270°を含む"
                     "8点以上を推奨）")
    notes.append("方向の対応: ↑=CW、↓=CCW。単位は秒[\"]。")
    return {"positions": positions, "axis": axis, "notes": notes}


def recommended_targets(base, lo, hi):
    """JIS B 6190-2 の推奨に沿い、等間隔目標に決定論的な擬似ランダムオフセットを
    与える（周期的な誤差成分と目標位置が一致してしまうのを避けるため）。

    両端は測定範囲を保つため固定し、内側の点だけをずらす。昇順・単調増加を保証。
    再現性のため乱数は使わず、インデックスから決まる固定の係数列を使う（同じ設定
    なら常に同じ目標位置になる＝測定プログラムと評価で必ず一致する）。
    """
    pts = [float(b) for b in base]
    n = len(pts)
    if n < 3:
        return pts
    step = (hi - lo) / (n - 1)
    amp = step * 0.30
    # 固定の擬似乱数係数（-1..1）。乱数を使わないので毎回同じ結果になる。
    coeffs = [-0.6, 0.7, -0.3, 0.5, -0.8, 0.2, 0.9, -0.4, 0.6, -0.7,
              0.3, -0.5, 0.8, -0.2, 0.4]
    out = [round(pts[0], 3)]
    for i in range(1, n - 1):
        out.append(round(pts[i] + amp * coeffs[i % len(coeffs)], 3))
    out.append(round(pts[-1], 3))
    # 単調増加を保証（丸め・係数で逆転しないよう最小間隔を確保）
    for i in range(1, len(out)):
        if out[i] <= out[i - 1]:
            out[i] = round(out[i - 1] + step * 0.1, 3)
    return out


AXIS_LABELS = [
    ("A", "A 双方向位置決め精度"),
    ("A_up", "A↑ 片方向精度（CW）"),
    ("A_dn", "A↓ 片方向精度（CCW）"),
    ("R", "R 双方向繰返し性"),
    ("R_up", "R↑ 繰返し性（CW）"),
    ("R_dn", "R↓ 繰返し性（CCW）"),
    ("E", "E 双方向系統誤差"),
    ("E_up", "E↑ 系統誤差（CW）"),
    ("E_dn", "E↓ 系統誤差（CCW）"),
    ("M", "M 平均双方向誤差"),
    ("B", "B 反転値（最大）"),
    ("B_mean", "B̄ 平均反転値"),
]


def axis_rows(stats) -> list:
    """軸まとめを [(表示名, '12.3\"')] 形式へ（None は '―'）。"""
    axis = stats["axis"]
    out = []
    for key, label in AXIS_LABELS:
        v = axis.get(key)
        out.append((label, f'{v:.2f}"' if v is not None else "―"))
    return out


def position_rows(stats) -> list:
    """位置別の表 [(角度, n↑, x̄↑, 2s↑, n↓, x̄↓, 2s↓, Bi, Ri)]（文字列整形済み）。"""
    def f(v):
        return f"{v:.2f}" if v is not None else "―"

    rows = []
    for p in stats["positions"]:
        rows.append((f'{p["angle"]:g}°',
                     str(p["n_up"]), f(p["mean_up"]),
                     f(2 * p["s_up"]) if p["s_up"] is not None else "―",
                     str(p["n_dn"]), f(p["mean_dn"]),
                     f(2 * p["s_dn"]) if p["s_dn"] is not None else "―",
                     f(p["b"]), f(p["r"])))
    return rows


def to_csv_text(stats, meta=None) -> str:
    """評価結果をCSVテキストへ（成績書・Excel貼り付け用。cp932で保存する想定）。"""
    lines = ["JIS B 6190-2 (ISO 230-2) 位置決め精度評価"]
    for k, v in (meta or {}).items():
        lines.append(f"{k},{v}")
    lines.append("")
    lines.append("項目,値")
    for label, val in axis_rows(stats):
        lines.append(f"{label},{val}")
    lines.append("")
    lines.append('角度,n↑(CW),x̄i↑["],2si↑["],n↓(CCW),x̄i↓["],2si↓["],Bi["],Ri["]')
    for row in position_rows(stats):
        lines.append(",".join(row))
    lines.append("")
    for n in stats["notes"]:
        lines.append(f"※ {n}")
    return "\n".join(lines) + "\n"
