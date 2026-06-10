# -*- coding: utf-8 -*-
"""分割精度の計算層

偏差 = (測定値 - 指令値) を秒["]に換算して評価する。
  - 精度PP   : 偏差の最大値 - 最小値
  - 最大隣接 : 隣り合う割出ポイント間の偏差差の最大値
  - バックラッシ : 同一指令角度での CCW偏差 - CW偏差。その MIN / MAX
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


def adjacent(dev):
    """最大隣接誤差[秒] = 隣接ポイント間の偏差差の最大値"""
    dev = np.asarray(dev, dtype=float)
    return float(np.abs(np.diff(dev)).max()) if len(dev) >= 2 else 0.0


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
            out[key] = dict(pp=pp(d), adjacent=adjacent(d))

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
