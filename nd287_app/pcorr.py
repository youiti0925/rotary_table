# -*- coding: utf-8 -*-
"""ピッチエラー補正（P補正）

測定結果から客先（FANUC・キタムラ等）へ提出する補正表を作る。
- 補正間隔ごとのホイール偏差（CW/CCWの平均）を打ち消す補正値を、
  補正単位[°]の整数倍に量子化して出す
- 補正後の偏差（機械が補正点間を直線補間する想定）も計算できる

※補正値の符号・採用偏差（平均かCWか）・表の体裁は客先フォーマットの
  見本が来たら合わせること。
"""

import numpy as np

from .analysis import deviation_sec


def _wheel_devs(data, key):
    targets, measured = data.get(key, ([], []))
    pairs = sorted(zip(targets, measured))
    if not pairs:
        return [], []
    t = [p[0] for p in pairs]
    return t, list(deviation_sec(t, [p[1] for p in pairs]))


def compensation_table(data, interval_deg, unit_deg):
    """補正表の行リストを返す。

    行: dict(no, angle, cw, ccw, mean, units, comp_sec)
        units = 補正単位の整数倍で表した補正値（偏差平均を打ち消す向き）
        comp_sec = units × unit_deg × 3600 [秒]
    """
    t_cw, dev_cw = _wheel_devs(data, "wheel_cw")
    t_ccw, dev_ccw = _wheel_devs(data, "wheel_ccw")
    if not t_cw or interval_deg <= 0 or unit_deg <= 0:
        return []
    ccw_by_t = {round(t, 6): d for t, d in zip(t_ccw, dev_ccw)}
    rows = []
    start = t_cw[0]
    no = 1
    for t, d_cw in zip(t_cw, dev_cw):
        offset = (t - start) / interval_deg
        if abs(offset - round(offset)) > 1e-6:
            continue  # 補正間隔の格子に乗っていない点は対象外
        d_ccw = ccw_by_t.get(round(t, 6))
        mean = d_cw if d_ccw is None else (d_cw + d_ccw) / 2.0
        units = int(round(-(mean / 3600.0) / unit_deg))
        rows.append(dict(
            no=no, angle=float(t), cw=float(d_cw),
            ccw=None if d_ccw is None else float(d_ccw),
            mean=float(mean), units=units,
            comp_sec=units * unit_deg * 3600.0,
        ))
        no += 1
    return rows


def apply_compensation(data, table):
    """補正後の偏差を返す: {"wheel_cw": (角度列, 補正後偏差列), "wheel_ccw": …}

    補正点の間は直線補間（機械側の挙動の一般的な想定）。
    """
    if not table:
        return {}
    comp_t = np.array([row["angle"] for row in table])
    comp_v = np.array([row["comp_sec"] for row in table])
    out = {}
    for key in ("wheel_cw", "wheel_ccw"):
        t, dev = _wheel_devs(data, key)
        if not t:
            continue
        comp = np.interp(np.asarray(t, dtype=float), comp_t, comp_v)
        out[key] = (list(t), list(np.asarray(dev) + comp))
    return out
