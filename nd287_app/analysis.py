# -*- coding: utf-8 -*-
"""分割精度の計算層

偏差 = (測定値 - 指令値) を秒["]に換算して評価する。
  - 精度PP   : 偏差の最大値 - 最小値
  - 単一誤差 : その点から次の点へ移ったときに発生した誤差（偏差の1ステップ差）の最大
  - 隣接誤差 : 連続する2つの単一誤差が逆向きに重なってできる突起の最大。
               例: ある点への移動誤差が +4"、次の点への移動誤差が -6" なら
               合わせて 10" の突起 → 隣接誤差 10"
  - 傾き     : 開始角度と終了角度の偏差の差（本来戻ってくるところに
               戻ってこないときの数字。測定順の最後 - 最初）
  - バックラッシ : 同一指令角度での CCW偏差 - CW偏差。その MIN / MAX
  - 温度別合否 : 測定温度に応じた規格と突き合わせて判定。ホイールが合金製のため
               熱膨張でホイール・ウォーム・総合のいずれも温度で変わる
  - 真の最大最小 : ホイール偏差とウォーム偏差が最悪方向に重なった合成値（仮実装）
"""

import numpy as np


def deviation_sec(targets, measured):
    """各ポイントの偏差[秒]（測定値 - 指令値）"""
    t = np.asarray(targets, dtype=float)
    m = np.asarray(measured, dtype=float)
    return (m - t) * 3600.0


def pp(dev):
    """精度PP[秒] = 偏差の最大 - 最小"""
    dev = np.asarray(dev, dtype=float)
    return float(dev.max() - dev.min()) if len(dev) else 0.0


def step_errors(dev):
    """各ステップの単一誤差[秒] = その点から次の点へ移ったときに発生した誤差"""
    dev = np.asarray(dev, dtype=float)
    return np.diff(dev)


def single(dev):
    """単一誤差[秒] = 1ステップで発生した誤差の絶対値の最大"""
    s = step_errors(dev)
    return float(np.abs(s).max()) if len(s) else 0.0


def adjacent(dev):
    """隣接誤差[秒] = 連続する2つの単一誤差が逆向きに重なってできる突起の最大

    例: +4" の次のステップが -6" → |+4 - (-6)| = 10" の突起。
    """
    s = step_errors(dev)
    return float(np.abs(np.diff(s)).max()) if len(s) >= 2 else 0.0


def slope(dev):
    """傾き[秒] = 終了角度の偏差 - 開始角度の偏差（測定順）

    一周（またはウォーム1回転）して本来戻ってくるはずの位置に
    戻ってこなかったぶん。
    """
    dev = np.asarray(dev, dtype=float)
    return float(dev[-1] - dev[0]) if len(dev) >= 2 else 0.0


def backlash(t_cw, d_cw, t_ccw, d_ccw):
    """同一指令角度での CCW偏差 - CW偏差。(min, max) を返す。共通点が無ければ None。"""
    common, i_cw, i_ccw = np.intersect1d(
        np.round(np.asarray(t_cw, dtype=float), 6),
        np.round(np.asarray(t_ccw, dtype=float), 6),
        return_indices=True,
    )
    if not len(common):
        return None
    bl = np.asarray(d_ccw, dtype=float)[i_ccw] - np.asarray(d_cw, dtype=float)[i_cw]
    return float(bl.min()), float(bl.max())


def true_min_max(wheel_dev, worm_dev):
    """機械の真の最大最小[秒]（仮実装）

    ホイール偏差の最大＋ウォーム偏差の最大／最小＋最小、という単純合成。
    現場の計算式が異なる場合はこの関数だけ差し替える。
    """
    w = np.asarray(wheel_dev, dtype=float)
    v = np.asarray(worm_dev, dtype=float)
    return float(w.min() + v.min()), float(w.max() + v.max())


def band_for_temp(spec, temp_c):
    """温度に該当する規格帯を返す。無ければ None。

    spec: [{"temp_min":…, "temp_max":…, "min":…, "max":…}, …]
    """
    for band in spec or []:
        if band["temp_min"] <= temp_c < band["temp_max"]:
            return band
    return None


def judge_minmax(value_min, value_max, temp_c, spec):
    """測定温度に応じた規格で MIN/MAX の組を合否判定する。

    バックラッシ（ホイール・ウォーム）にも総合（真の最大最小）にも使う。
    返り値: (合否 True/False, 使用した規格帯)。温度に該当する規格が
    無ければ (None, None)。
    """
    band = band_for_temp(spec, temp_c)
    if band is None:
        return None, None
    ok = (band["min"] <= value_min) and (value_max <= band["max"])
    return ok, band


def summarize(data):
    """測定データ一式から結果サマリを作る。

    data: {"wheel_cw": (targets, measured), "wheel_ccw": ..., "worm_cw": ..., "worm_ccw": ...}
    返り値: (summary dict, 系列ごとの (targets, deviations) dict)
    """
    out = {}
    devs = {}
    for key, (targets, measured) in data.items():
        if targets:
            d = deviation_sec(targets, measured)
            devs[key] = (np.asarray(targets, dtype=float), d)
            out[key] = dict(pp=pp(d), single=single(d), adjacent=adjacent(d), slope=slope(d))

    for grp in ("wheel", "worm"):
        cw = devs.get(f"{grp}_cw")
        ccw = devs.get(f"{grp}_ccw")
        if cw and ccw:
            bl = backlash(cw[0], cw[1], ccw[0], ccw[1])
            if bl is not None:
                out[f"{grp}_backlash"] = dict(min=bl[0], max=bl[1])

    out["true"] = {}
    for dirn in ("cw", "ccw"):
        w = devs.get(f"wheel_{dirn}")
        v = devs.get(f"worm_{dirn}")
        if w and v:
            tmin, tmax = true_min_max(w[1], v[1])
            out["true"][dirn] = dict(true_min=tmin, true_max=tmax)

    return out, devs
