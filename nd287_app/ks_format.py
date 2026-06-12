# -*- coding: utf-8 -*-
"""旧LabVIEWアプリ（傾斜分割）の .KS 形式の読み書き

実物（261166ITY.KS）から解読した構造:
    1行目  : 型式
    2行目  : 測定日,測定者
    3行目  : 開始角度H, 開始角度W, 評価範囲1の開始角度[, 評価範囲2の開始角度]
    4行目  : 終了角度H, 終了角度W, 評価範囲1の終了角度[, 評価範囲2の終了角度]
              （0.0001°単位。範囲2の列は推測——範囲2入りの実物で要確認）
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

検証済みの計算（261166ITYで旧アプリ表示と一致。全範囲・範囲1(0〜90°)・
範囲2(-30〜+90°)の計18値）:
    精度H = 素のPP（0.5"丸め）
    精度W = 傾き補正後PP（0.5"丸め）
    精度  = 精度H + 精度W（任意誤差評価）
    範囲評価 = 範囲内のホイール素PP + 精度W
バックラッシの合否判定はしない。MAX/MIN行はバックラッシの最大/最小
（旧アプリは補正後の値のため、保存時はこちらの端点補正後の値を書く）。
"""

from .analysis import detrended_pp, deviation_sec, pp
from .bs_format import join_dms, split_dms

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
    doc["range2_start"] = starts[3] if len(starts) > 3 else None
    doc["end_h"], doc["end_w"] = ends[0], ends[1]
    doc["range1_end"] = ends[2] if len(ends) > 2 else None
    doc["range2_end"] = ends[3] if len(ends) > 3 else None
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

    # 範囲2が無いファイルは3列のまま（実物とのバイト一致のため）
    starts = [doc["start_h"], doc["start_w"], doc["range1_start"]]
    ends = [doc["end_h"], doc["end_w"], doc["range1_end"]]
    if doc.get("range2_start") is not None or doc.get("range2_end") is not None:
        starts.append(doc.get("range2_start"))
        ends.append(doc.get("range2_end"))
    out = [
        doc["model"],
        f'{doc["date"]},{doc["operator"]}',
        join(starts),
        join(ends),
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


def save_ks(path, doc: dict):
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        f.write(format_ks(doc))


def _detrend(values):
    if len(values) < 2:
        return list(values)
    n = len(values) - 1
    return [v - (values[-1] - values[0]) * i / n for i, v in enumerate(values)]


def backlash_minmax(data, range_=None, blcorr=0.0):
    """ホイールの補正後バックラッシ（CCW偏差-CW偏差、各系列を端点補正）のMIN/MAX。

    .KSヘッダのMAX/MIN行用。旧アプリも補正後の値のため完全一致はしない場合がある。
    """
    targets, cw_meas = data.get("wheel_cw", ([], []))
    ccw_by_target = {round(t, 6): m
                     for t, m in zip(*data.get("wheel_ccw", ([], [])))}
    pairs = []
    for t, m_cw in sorted(zip(targets, cw_meas)):
        m_ccw = ccw_by_target.get(round(t, 6))
        if m_ccw is None:
            continue
        if range_ is not None and not (range_[0] <= t <= range_[1]):
            continue
        pairs.append((t, m_cw, m_ccw))
    if len(pairs) < 2:
        return None
    cw_dev = list(deviation_sec([p[0] for p in pairs], [p[1] for p in pairs]))
    ccw_dev = list(deviation_sec([p[0] for p in pairs], [p[2] for p in pairs]))
    bl = [c2 - c1 + blcorr for c1, c2 in zip(_detrend(cw_dev), _detrend(ccw_dev))]
    return (min(bl), max(bl))


def data_to_doc(data, *, model, date, operator, range1=None, range2=None,
                b_corr=0.0, p_interval=100000, p_unit=0.001,
                order=(1, 2, 3, 4)) -> dict:
    """アプリの測定データ → .KS構造（傾斜分割の保存用）

    range1/range2: (開始角度, 終了角度)[°] または None（未使用）。
    精度行は tilt_accuracy（旧アプリと一致を確認した式）で計算する。
    """
    doc = dict(model=model, date=date, operator=operator,
               b_corr=b_corr, p_interval=p_interval, p_unit=p_unit,
               order=list(order))

    doc["rows"] = {}
    for block, (cw_key, ccw_key) in (
        ("wheel", ("wheel_cw", "wheel_ccw")),
        ("worm", ("worm_cw", "worm_ccw")),
    ):
        targets, cw_meas = data.get(cw_key, ([], []))
        ccw_by_target = {round(t, 6): m
                         for t, m in zip(*data.get(ccw_key, ([], [])))}
        rows = []
        for t, m_cw in sorted(zip(targets, cw_meas)):
            m_ccw = ccw_by_target.get(round(t, 6), t)
            cw_disp = m_cw % 360.0
            ccw_disp = m_ccw % 360.0
            cw_dev = float(deviation_sec([t], [m_cw])[0])
            ccw_dev = float(deviation_sec([t], [m_ccw])[0])
            rows.append((t % 360.0, t) + split_dms(cw_disp) + split_dms(ccw_disp)
                        + (cw_dev, ccw_dev))
        doc["rows"][block] = rows

    wheel_t = sorted(data.get("wheel_cw", ([], []))[0])
    worm_t = sorted(data.get("worm_cw", ([], []))[0])
    doc["start_h"] = round(wheel_t[0] * 10000) if wheel_t else 0
    doc["end_h"] = round(wheel_t[-1] * 10000) if wheel_t else 0
    doc["interval_h"] = round((wheel_t[1] - wheel_t[0]) * 10000) if len(wheel_t) > 1 else 0
    doc["start_w"] = round(worm_t[0] * 10000) if worm_t else 0
    doc["end_w"] = round(worm_t[-1] * 10000) if worm_t else 0
    doc["interval_w"] = round((worm_t[1] - worm_t[0]) * 10000) if len(worm_t) > 1 else 0
    doc["points_h"] = max(len(wheel_t) - 1, 0)
    doc["points_w"] = max(len(worm_t) - 1, 0)
    doc["range1_start"] = round(range1[0] * 10000) if range1 else None
    doc["range1_end"] = round(range1[1] * 10000) if range1 else None
    doc["range2_start"] = round(range2[0] * 10000) if range2 else None
    doc["range2_end"] = round(range2[1] * 10000) if range2 else None

    # 精度行（全範囲・範囲1・範囲2）
    full = tilt_accuracy(data)
    doc["acc_cw"] = [full["cw"].get(k) for k in ("h", "w", "total")]
    doc["acc_ccw"] = [full["ccw"].get(k) for k in ("h", "w", "total")]
    range_cw, range_ccw = [], []
    for rng in (range1, range2):
        if rng:
            acc = tilt_accuracy(data, range_=rng)
            range_cw += [acc["cw"].get(k) for k in ("h", "w", "total")]
            range_ccw += [acc["ccw"].get(k) for k in ("h", "w", "total")]
        else:
            range_cw += [None] * 3
            range_ccw += [None] * 3
    doc["range_cw"] = range_cw
    doc["range_ccw"] = range_ccw

    # MAX/MIN行 = バックラッシ（補正後）の最大/最小（全範囲・範囲1・範囲2）
    max3, min3 = [], []
    full_mm = backlash_minmax(data, blcorr=b_corr)
    for slot_range, used in ((None, True), (range1, range1 is not None),
                             (range2, range2 is not None)):
        mm = (full_mm if slot_range is None
              else backlash_minmax(data, range_=slot_range, blcorr=b_corr)) if used else None
        max3.append(round(mm[1], 1) if mm else None)
        min3.append(round(mm[0], 1) if mm else None)
    doc["max3"] = max3
    doc["min3"] = min3
    return doc
