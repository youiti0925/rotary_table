# -*- coding: utf-8 -*-
"""旧LabVIEWアプリの .BS 形式の読み書き

実物（260976K.BS, 61行で完結）から解読した構造:
    1行目   : 型式
    2行目   : 測定日,測定者,温度,（末尾カンマ）
    3〜8行目: 間隔 / 1/N / 点数 / 傾 / 精度1 / 精度2（各行 HR,WR,WL,HL の4列）
              間隔は0.0001°単位（例 300000=30°）
    9〜12行目: 空欄4列（,,,）＝予備（UD, L/R, 傾, 精度）
    13行目  : 1,2,3,4,規格MIN,規格MAX,0.000000,0.000000
    以降データ行（7列, %.6f）。1行に CW と CCW の両方が入る:
        指令角度[十進度], CW測定値の度, 分, 秒, CCW測定値の度, 分, 秒
    データブロックは2つ:
        ホイール（HRの点数×N+1行、0→360°昇順・閉じ点込み）
        ウォーム（WRの点数×N+1行、昇順）
機番はファイル名（<機番>.BS）。文字コードはcp932、改行はCRLFを既定とする。
"""

from .analysis import deviation_sec, pp_step, slope_corrected_pp

SECTION_KEYS = ("HR", "WR", "WL", "HL")  # R=CW, L=CCW

# ヘッダ4列とこのアプリの系列キーの対応
SECTION_TO_SERIES = {
    "HR": "wheel_cw",
    "WR": "worm_cw",
    "WL": "worm_ccw",
    "HL": "wheel_ccw",
}


def split_dms(deg: float):
    """十進度 → (度, 分, 秒) ※.BSのデータ列形式"""
    sign = -1.0 if deg < 0 else 1.0
    a = abs(deg)
    d = int(a)
    mf = (a - d) * 60.0
    m = int(mf)
    s = (mf - m) * 60.0
    if s >= 59.9999995:  # 丸めの繰り上げ
        s = 0.0
        m += 1
        if m >= 60:
            m = 0
            d += 1
    return (sign * d, float(m), s)


def join_dms(d: float, m: float, s: float) -> float:
    """(度, 分, 秒) → 十進度"""
    sign = -1.0 if d < 0 else 1.0
    return sign * (abs(d) + m / 60.0 + s / 3600.0)


def parse_bs(text: str) -> dict:
    """.BSテキストを解析する。"""
    lines = text.splitlines()
    if len(lines) < 13:
        raise ValueError(".BSのヘッダが13行未満です")

    def four(line, conv):
        values = line.split(",")
        return [conv(x) for x in values[:4]]

    doc = {"model": lines[0].strip()}
    meta = lines[1].split(",")
    doc["date"] = meta[0] if len(meta) > 0 else ""
    doc["operator"] = meta[1] if len(meta) > 1 else ""
    doc["temperature"] = meta[2] if len(meta) > 2 else ""

    intervals = four(lines[2], int)
    ns = four(lines[3], int)
    points = four(lines[4], int)
    slopes = four(lines[5], float)
    acc1 = four(lines[6], float)
    acc2 = four(lines[7], float)
    doc["series"] = {}
    for i, key in enumerate(SECTION_KEYS):
        doc["series"][key] = dict(
            interval=intervals[i],
            n=ns[i],
            points=points[i],
            slope=slopes[i],
            acc1=acc1[i],
            acc2=acc2[i],
        )

    line13 = lines[12].split(",")
    doc["spec_min"] = float(line13[4])
    doc["spec_max"] = float(line13[5])

    data_lines = [l for l in lines[13:] if l.strip()]
    hr, wr = doc["series"]["HR"], doc["series"]["WR"]
    wheel_count = hr["points"] * hr["n"] + 1  # 閉じ点込み
    worm_count = wr["points"] * wr["n"] + 1
    doc["rows"] = {"wheel": [], "worm": []}
    for line in data_lines[:wheel_count]:
        doc["rows"]["wheel"].append(tuple(float(x) for x in line.split(",")[:7]))
    for line in data_lines[wheel_count:wheel_count + worm_count]:
        doc["rows"]["worm"].append(tuple(float(x) for x in line.split(",")[:7]))
    return doc


def _format_int_like(value) -> str:
    """傾など: 整数なら整数表記、そうでなければ最短表記"""
    f = float(value)
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def format_bs(doc: dict, newline: str = "\r\n") -> str:
    """parse_bs と対になる書き出し。実物とバイト一致する書式で出す。"""
    series = doc["series"]
    out = [
        doc["model"],
        f'{doc["date"]},{doc["operator"]},{doc["temperature"]},',
        ",".join(str(int(series[k]["interval"])) for k in SECTION_KEYS),
        ",".join(str(int(series[k]["n"])) for k in SECTION_KEYS),
        ",".join(str(int(series[k]["points"])) for k in SECTION_KEYS),
        ",".join(_format_int_like(series[k]["slope"]) for k in SECTION_KEYS),
        ",".join(f'{series[k]["acc1"]:.1f}' for k in SECTION_KEYS),
        ",".join(f'{series[k]["acc2"]:.1f}' for k in SECTION_KEYS),
        ",,,",
        ",,,",
        ",,,",
        ",,,",
        f'1,2,3,4,{doc["spec_min"]:.1f},{doc["spec_max"]:.1f},0.000000,0.000000',
    ]
    for block in ("wheel", "worm"):
        for row in doc["rows"].get(block, []):
            out.append(",".join(f"{v:.6f}" for v in row))
    return newline.join(out) + newline


def load_bs(path) -> dict:
    with open(path, encoding="cp932") as f:
        return parse_bs(f.read())


def save_bs(path, doc: dict):
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        f.write(format_bs(doc))


# ──────────────────────────────────────────────
#  このアプリの測定データとの相互変換
# ──────────────────────────────────────────────


def doc_to_data(doc: dict) -> dict:
    """.BSの行 → アプリの系列データ dict

    測定値はファイルのまま（カウンタ表示の0..360°）。閉じ点の巻き戻りは
    analysis.deviation_sec が±180°正規化して扱う。
    CCWは実際の測定順（角度の降順）に並べ替えて返す。
    """
    data = {}
    for block, (cw_key, ccw_key) in (
        ("wheel", ("wheel_cw", "wheel_ccw")),
        ("worm", ("worm_cw", "worm_ccw")),
    ):
        rows = doc["rows"].get(block, [])
        targets = [r[0] for r in rows]
        cw = [join_dms(r[1], r[2], r[3]) for r in rows]
        ccw = [join_dms(r[4], r[5], r[6]) for r in rows]
        data[cw_key] = (list(targets), list(cw))
        data[ccw_key] = (list(reversed(targets)), list(reversed(ccw)))
    return data


def _ascending_slope(targets, measured):
    """旧アプリの「傾」: 角度昇順での 終点偏差 - 始点偏差 [秒]（整数丸め）"""
    if len(targets) < 2:
        return 0
    pairs = sorted(zip(targets, measured))
    devs = deviation_sec([p[0] for p in pairs], [p[1] for p in pairs])
    return int(round(float(devs[-1] - devs[0])))


def _half_round(value: float) -> float:
    """旧アプリの表示分解能（0.5"刻み）に丸める"""
    return round(float(value) * 2.0) / 2.0


def data_to_doc(data, summary, *, model, date, operator, temperature,
                spec_min=0.0, spec_max=0.0, wheel_n=1, worm_n=1) -> dict:
    """アプリの測定データ＋結果サマリ → .BS構造

    CW/CCWを指令角度でペアにして1行にまとめる。測定値はカウンタ表示
    （0..360°）の度分秒で書く（旧アプリと同じ。例: 指令360°のCCW測定値は
    0°00'xx"）。
    精度1 = 主点（間隔グリッド = wheel_n/worm_n おき）のPP、
    精度2 = 傾き補正後PP（補正で悪化する場合は素のPP）、0.5"刻み。
    260976K.BSで旧アプリのヘッダ値と全12値一致を確認済み。
    """
    doc = dict(model=model, date=date, operator=operator, temperature=temperature,
               spec_min=float(spec_min), spec_max=float(spec_max))
    doc["series"] = {}
    doc["rows"] = {}

    for block, (cw_key, ccw_key) in (
        ("wheel", ("wheel_cw", "wheel_ccw")),
        ("worm", ("worm_cw", "worm_ccw")),
    ):
        targets, cw_meas = data.get(cw_key, ([], []))
        ccw_targets, ccw_meas = data.get(ccw_key, ([], []))
        ccw_by_target = {round(t, 6): m for t, m in zip(ccw_targets, ccw_meas)}
        min_target = min(targets) if targets else 0.0
        rows = []
        for t, m_cw in sorted(zip(targets, cw_meas)):
            m_ccw = ccw_by_target.get(round(t, 6), t)
            row = (t,) + split_dms(_wrap_counter(m_cw, min_target)) \
                       + split_dms(_wrap_counter(m_ccw, min_target))
            rows.append(row)
        doc["rows"][block] = rows

        pitch = abs(targets[1] - targets[0]) if len(targets) >= 2 else 0.0
        n_points = max(len(rows) - 1, 0)
        step = wheel_n if block == "wheel" else worm_n
        for direction, series_key, meas_targets, meas in (
            ("R", cw_key, targets, cw_meas),
            ("L", ccw_key, ccw_targets, ccw_meas),
        ):
            section = ("H" if block == "wheel" else "W") + direction
            ordered = sorted(zip(meas_targets, meas))
            devs = (
                deviation_sec([p[0] for p in ordered], [p[1] for p in ordered])
                if ordered else []
            )
            doc["series"][section] = dict(
                interval=int(round(pitch * 10000)),
                n=1,
                points=n_points,
                slope=_ascending_slope(meas_targets, meas),
                acc1=_half_round(pp_step(devs, step)),
                acc2=_half_round(slope_corrected_pp(devs)),
            )
    return doc


def _wrap_counter(measured: float, min_target: float) -> float:
    """測定値をカウンタ表示（0..360°）に揃える。

    傾斜測定（負の角度が正規にある場合）はそのまま。
    """
    if measured >= 360.0:
        return measured % 360.0
    if measured < 0.0 and min_target >= 0.0:
        return measured % 360.0
    return measured
