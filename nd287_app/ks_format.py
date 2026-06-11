# -*- coding: utf-8 -*-
"""旧LabVIEWアプリ（傾斜分割）の .KS 形式の読み書き

実物（261166ITY.KS）から解読した構造:
    1行目  : 型式
    2行目  : 測定日,測定者
    3行目  : 開始角度H, 開始角度W, 評価範囲1の開始角度（0.0001°単位）
    4行目  : 終了角度H, 終了角度W, 評価範囲1の終了角度
    5行目  : 間隔H, 間隔W
    6行目  : 正（CW）の 精度H, 精度W, 精度（=H+W）
    7行目  : 逆（CCW）の 精度H, 精度W, 精度
    8行目  : 範囲1の正(H,W,計) + 範囲2の正(H,W,計)
    9行目  : 範囲1の逆(H,W,計) + 範囲2の逆(H,W,計)
    10行目 : MAX ×3（全範囲・範囲1・範囲2）※定義未確認
    11行目 : MIN ×3 ※定義未確認
    12行目 : B補正値, P補正間隔, P補正単位
    13行目 : 測定順（HR,WR,WL,HL）
    14行目 : 点数H, 点数W
    データ行（10列, %.6f）:
        カウンタ指令(0..360), 符号付指令, CW測定の度,分,秒,
        CCW測定の度,分,秒, CW偏差["], CCW偏差["]
    ホイール（点数H+1行）→ ウォーム（点数W+1行）

検証済みの計算（261166ITYで旧アプリ表示と一致）:
    精度H = 素のPP（0.5"丸め）
    精度W = 傾き補正後PP（0.5"丸め）
    精度  = 精度H + 精度W（任意誤差評価）
    範囲評価 = 範囲内のホイール素PP + 精度W
バックラッシの合否判定はしない。MAX/MIN行の定義は未確認。
"""

from .analysis import detrended_pp, deviation_sec, pp
from .bs_format import join_dms

SECTION_KEYS = ("HR", "WR", "WL", "HL")


def _nums(line, conv=float):
    out = []
    for token in line.split(","):
        token = token.strip()
        out.append(conv(token) if token else None)
    return out


def parse_ks(text: str) -> dict:
    lines = text.splitlines()
    if len(lines) < 14:
        raise ValueError(".KSのヘッダが14行未満です")
    doc = {"model": lines[0].strip()}
    meta = lines[1].split(",")
    doc["date"] = meta[0] if len(meta) > 0 else ""
    doc["operator"] = meta[1] if len(meta) > 1 else ""

    starts = _nums(lines[2])
    ends = _nums(lines[3])
    intervals = _nums(lines[4])
    doc["start_h"], doc["start_w"] = starts[0], starts[1]
    doc["range1_start"] = starts[2] if len(starts) > 2 else None
    doc["end_h"], doc["end_w"] = ends[0], ends[1]
    doc["range1_end"] = ends[2] if len(ends) > 2 else None
    doc["interval_h"], doc["interval_w"] = intervals[0], intervals[1]

    doc["acc_cw"] = _nums(lines[5])    # 精度H, 精度W, 精度
    doc["acc_ccw"] = _nums(lines[6])
    doc["range_cw"] = _nums(lines[7])  # 範囲1(H,W,計)+範囲2(H,W,計)
    doc["range_ccw"] = _nums(lines[8])
    doc["max3"] = _nums(lines[9])
    doc["min3"] = _nums(lines[10])
    corr = _nums(lines[11])
    doc["b_corr"], doc["p_interval"], doc["p_unit"] = (corr + [None] * 3)[:3]
    doc["order"] = [int(x) for x in _nums(lines[12]) if x is not None]
    counts = _nums(lines[13], conv=lambda s: int(float(s)))
    doc["points_h"], doc["points_w"] = counts[0], counts[1]

    data_lines = [l for l in lines[14:] if l.strip()]
    wheel_count = doc["points_h"] + 1
    worm_count = doc["points_w"] + 1
    doc["rows"] = {"wheel": [], "worm": []}
    for line in data_lines[:wheel_count]:
        doc["rows"]["wheel"].append(tuple(float(x) for x in line.split(",")[:10]))
    for line in data_lines[wheel_count:wheel_count + worm_count]:
        doc["rows"]["worm"].append(tuple(float(x) for x in line.split(",")[:10]))
    return doc


def _fmt(value):
    if value is None:
        return ""
    f = float(value)
    if f == int(f) and abs(f) < 1e7:
        # 角度・点数などの整数系は整数表記
        return str(int(f))
    return f"{f:g}"


def format_ks(doc: dict, newline: str = "\r\n") -> str:
    def join(values, fmt=_fmt):
        return ",".join(fmt(v) for v in values)

    def acc(values):
        return ",".join("" if v is None else f"{float(v):.1f}" for v in values)

    out = [
        doc["model"],
        f'{doc["date"]},{doc["operator"]}',
        join([doc["start_h"], doc["start_w"], doc["range1_start"]]),
        join([doc["end_h"], doc["end_w"], doc["range1_end"]]),
        join([doc["interval_h"], doc["interval_w"]]),
        acc(doc["acc_cw"]),
        acc(doc["acc_ccw"]),
        acc(doc["range_cw"]),
        acc(doc["range_ccw"]),
        acc(doc["max3"]),
        acc(doc["min3"]),
        join([doc["b_corr"], doc["p_interval"], doc["p_unit"]]),
        ",".join(str(x) for x in doc["order"]),
        join([doc["points_h"], doc["points_w"]]),
    ]
    for block in ("wheel", "worm"):
        for row in doc["rows"].get(block, []):
            out.append(",".join(f"{v:.6f}" for v in row))
    return newline.join(out) + newline


def load_ks(path) -> dict:
    with open(path, encoding="cp932") as f:
        return parse_ks(f.read())


def doc_to_data(doc: dict) -> dict:
    """.KSの行 → アプリの系列データ dict（指令は符号付角度）"""
    data = {}
    for block, (cw_key, ccw_key) in (
        ("wheel", ("wheel_cw", "wheel_ccw")),
        ("worm", ("worm_cw", "worm_ccw")),
    ):
        rows = doc["rows"].get(block, [])
        targets = [r[1] for r in rows]  # 符号付指令
        cw = [join_dms(r[2], r[3], r[4]) for r in rows]
        ccw = [join_dms(r[5], r[6], r[7]) for r in rows]
        data[cw_key] = (list(targets), list(cw))
        data[ccw_key] = (list(reversed(targets)), list(reversed(ccw)))
    return data


# ──────────────────────────────────────────────
#  傾斜分割の評価（任意誤差評価）
# ──────────────────────────────────────────────

def _half_round(value):
    return round(float(value) * 2.0) / 2.0


def _devs(targets, measured):
    if not targets:
        return None
    pairs = sorted(zip(targets, measured))
    return deviation_sec([p[0] for p in pairs], [p[1] for p in pairs])


def tilt_accuracy(data, range_=None) -> dict:
    """傾斜分割の精度評価（261166ITYで旧アプリと一致を確認した式）

    range_: (開始角度, 終了角度) を渡すとホイールをその範囲だけで評価する
            （客先要求による部分抜き出し評価）。ウォームは常に全範囲。
    返り値: {"cw": {"h":…, "w":…, "total":…}, "ccw": {...}}
    """
    result = {}
    for dirn, h_key, w_key in (("cw", "wheel_cw", "worm_cw"),
                               ("ccw", "wheel_ccw", "worm_ccw")):
        h_targets, h_meas = data.get(h_key, ([], []))
        if range_ is not None:
            lo, hi = range_
            pairs = [(t, m) for t, m in zip(h_targets, h_meas) if lo <= t <= hi]
            h_targets = [p[0] for p in pairs]
            h_meas = [p[1] for p in pairs]
        h_dev = _devs(h_targets, h_meas)
        w_dev = _devs(*data.get(w_key, ([], [])))
        entry = {}
        if h_dev is not None and len(h_dev) >= 2:
            entry["h"] = _half_round(pp(h_dev))           # 精度H: 素のPP
        if w_dev is not None and len(w_dev) >= 2:
            entry["w"] = _half_round(detrended_pp(w_dev))  # 精度W: 傾き補正後PP
        if "h" in entry and "w" in entry:
            entry["total"] = entry["h"] + entry["w"]  # 任意誤差 = H + W
        result[dirn] = entry
    return result
