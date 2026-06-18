# -*- coding: utf-8 -*-
"""旧LabVIEWアプリ（再現性）の .RS / .RSK 形式の書き出し

実物2件（回転再現性=.RS、傾斜再現性=.RSK）から解読した構造:
    1行目  : 型式,クローズ
    2行目  : 測定日(yyyy/MM/dd),測定者
    3行目  : ブロック数,回数
    4行目  : 総合再現性, 総合バックラッシMIN, 総合バックラッシMAX
              総合再現性 = 全ブロックの max(CW範囲, CCW範囲) の最大
              総合BL MIN/MAX = 全読取の (CCW値 − CW値) の最小/最大
    5行目〜 : ブロックごとに「番号, CW範囲, CCW範囲, 平均バックラッシ」
              CW範囲 = そのブロックのCW値の(最大−最小)、CCW範囲も同様
              平均バックラッシ = mean(CCW) − mean(CW)
    データ行: 通し番号, CW値, CCW値（すべて %.6f）
              ブロック1の全回 → ブロック2の全回 … の順。値は偏差["]。
    拡張子   : 回転再現性=.RS / 傾斜再現性=.RSK。ファイル名は <機番>.RS(K)。
    小数は1位（4行目・ブロック行）、データは6位。改行CRLF・cp932。

検証: 提供された2サンプル（回転6点なし/4点、傾斜6点）で全フィールド一致を確認。
"""

import math


def _round1(value) -> float:
    """0.1刻みで四捨五入（0から離れる向き＝LabVIEW流）。-0.0 は 0.0 に。"""
    value = float(value)
    r = math.floor(abs(value) * 10.0 + 0.5) / 10.0 * (1.0 if value >= 0 else -1.0)
    return 0.0 if r == 0 else r


def _f1(value) -> str:
    return f"{_round1(value):.1f}"


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def data_to_rs_doc(rep_points, rep_data, *, model, close="", date="", operator="",
                   repeats=None) -> dict:
    """アプリの再現性データ → .RS/.RSK 構造。

    rep_points: ブロックの指令角度[deg] のリスト
    rep_data  : {("cw"|"ccw", ブロック番号): [測定角度deg, …]}
    値は (測定 − 指令) × 3600 ["] に変換して格納する。
    """
    blocks = []
    for b, nominal in enumerate(rep_points):
        cw = [(m - nominal) * 3600.0 for m in rep_data.get(("cw", b), [])]
        ccw = [(m - nominal) * 3600.0 for m in rep_data.get(("ccw", b), [])]
        blocks.append((cw, ccw))
    if repeats is None:
        repeats = max((len(cw) for cw, _ in blocks), default=0)
    return dict(model=model, close=close, date=date, operator=operator,
                repeats=int(repeats), blocks=blocks)


def format_rs(doc: dict, newline: str = "\r\n") -> str:
    blocks = doc["blocks"]
    out = [
        f'{doc["model"]},{doc.get("close", "")}',
        f'{doc.get("date", "")},{doc.get("operator", "")}',
        f'{len(blocks)},{int(doc.get("repeats", 0))}',
    ]
    total_rep = 0.0
    backlash_all = []
    block_lines = []
    for i, (cw, ccw) in enumerate(blocks, start=1):
        cw_range = (max(cw) - min(cw)) if cw else 0.0
        ccw_range = (max(ccw) - min(ccw)) if ccw else 0.0
        total_rep = max(total_rep, cw_range, ccw_range)
        backlash_all += [b - a for a, b in zip(cw, ccw)]
        mean_bl = _mean(ccw) - _mean(cw)
        block_lines.append(
            f"{i},{_f1(cw_range)},{_f1(ccw_range)},{_f1(mean_bl)}")
    bl_min = min(backlash_all) if backlash_all else 0.0
    bl_max = max(backlash_all) if backlash_all else 0.0
    out.append(f"{_f1(total_rep)},{_f1(bl_min)},{_f1(bl_max)}")
    out.extend(block_lines)

    index = 1
    for cw, ccw in blocks:
        for a, b in zip(cw, ccw):
            out.append(f"{index:.6f},{a:.6f},{b:.6f}")
            index += 1
    return newline.join(out) + newline


def parse_rs(text: str) -> dict:
    """.RS/.RSK を構造 dict に読む（検証・過去データ用）。"""
    lines = [l for l in text.splitlines()]
    if len(lines) < 4:
        raise ValueError(".RSのヘッダが不足しています")
    model_close = (lines[0].split(",") + ["", ""])[:2]
    date_op = (lines[1].split(",") + ["", ""])[:2]
    nblocks_s, repeats_s = (lines[2].split(",") + ["0", "0"])[:2]
    nblocks, repeats = int(float(nblocks_s)), int(float(repeats_s))
    data = [l for l in lines[4 + nblocks:] if l.strip()]
    blocks = []
    k = 0
    for _ in range(nblocks):
        cw, ccw = [], []
        for _ in range(repeats):
            parts = data[k].split(",")
            cw.append(float(parts[1]))
            ccw.append(float(parts[2]))
            k += 1
        blocks.append((cw, ccw))
    return dict(model=model_close[0], close=model_close[1],
                date=date_op[0], operator=date_op[1],
                repeats=repeats, blocks=blocks)


def save_rs(path, doc: dict):
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        f.write(format_rs(doc))
