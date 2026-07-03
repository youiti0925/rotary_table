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
    """各ポイントの偏差[秒]（測定値 - 指令値）

    一周の閉じ点ではカウンタ表示が0°へ巻き戻る（指令360°に対し測定0°00'xx"）
    ため、差を±180°に正規化してから秒に換算する。
    """
    t = np.asarray(targets, dtype=float)
    m = np.asarray(measured, dtype=float)
    diff = (m - t + 180.0) % 360.0 - 180.0
    return diff * 3600.0


def rep_unwrap(angle, value):
    """再現性の測定値を、指令角度に最も近い表現へ折り返して返す（0/360またぎ対策）。

    ND287のカウンタは[0,360)へ巻き戻るため、0°ブロックで 359.9999↔0.0001 の
    ようにまたいだ読みが混ざると生差が±360°近い異常値になる。差が±180°を
    超えるときだけ±360°して返す。またがない通常の読みは値をそのまま返す
    （浮動小数点まで不変＝既存出力のバイト一致を保つ）。
    """
    v = float(value)
    d = v - float(angle)
    if d > 180.0:
        return v - 360.0
    if d < -180.0:
        return v + 360.0
    return v


def pp(dev):
    """精度PP[秒] = 偏差の最大 - 最小"""
    dev = np.asarray(dev, dtype=float)
    return float(dev.max() - dev.min()) if len(dev) else 0.0


def pp_step(dev, step=1):
    """主点（間隔グリッド = stepおき）のPP[秒]。旧アプリの「精度1」。

    例: 間隔30°を1/3分割で10°刻み測定した場合、step=3で30°主点のみのPP。
    260976K.BSの全4系列で旧アプリのヘッダ値と一致することを確認済み。
    """
    dev = np.asarray(dev, dtype=float)
    if not len(dev):
        return 0.0
    return pp(dev[::max(int(step), 1)])


def detrended_pp(dev):
    """傾き補正後PP[秒]: 始終点を結ぶ直線成分（傾き）を除いたPP。

    devは角度昇順であること。
    """
    dev = np.asarray(dev, dtype=float)
    if len(dev) < 2:
        return 0.0
    detrended = dev - (dev[-1] - dev[0]) * np.arange(len(dev)) / (len(dev) - 1)
    return pp(detrended)


def slope_corrected_pp(dev):
    """傾き補正後PPと素のPPの小さい方[秒]。旧アプリ（回転分割）の「精度2」。

    補正で悪化する場合は補正しない。260976K.BSの全4系列で旧アプリの
    ヘッダ値（0.5刻み）と一致することを確認済み。
    """
    dev = np.asarray(dev, dtype=float)
    if len(dev) < 2:
        return 0.0
    return min(pp(dev), detrended_pp(dev))


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


def adjacent_peak(dev):
    """隣接誤差(突起)が最大になる点の (index, 値[秒])。点が3未満なら None。

    突起は3点 dev[i-1], dev[i], dev[i+1] の山/谷で、中心の点 i に現れる。
    グラフ上で「ここが隣接の最大」を指すための位置に使う。
    """
    s = step_errors(dev)
    if len(s) < 2:
        return None
    d = np.abs(np.diff(s))           # d[j] は中心点 j+1 の突起の大きさ
    j = int(np.argmax(d))
    return (j + 1, float(d[j]))


def single_peak(dev):
    """単一誤差が最大になるステップの (始点index, 終点index, 値[秒])。無ければ None。"""
    s = step_errors(dev)
    if len(s) < 1:
        return None
    i = int(np.argmax(np.abs(s)))
    return (i, i + 1, float(abs(s[i])))


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


def summarize(data, backlash_correction=0.0):
    """測定データ一式から結果サマリを作る。

    data: {"wheel_cw": (targets, measured), "wheel_ccw": ..., "worm_cw": ..., "worm_ccw": ...}
    backlash_correction: バックラッシ手動補正[秒]。実際のメカ的な隙間が測定結果と
        差がある場合に、調べた差分（測定結果に対して何秒多いか少ないか）を
        ホイール・ウォーム両方のバックラッシMIN/MAXに加算する。合否判定も
        補正後の値で行われる。生データ・偏差・真の最大最小には影響しない。
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
                out[f"{grp}_backlash"] = dict(
                    min=bl[0] + backlash_correction, max=bl[1] + backlash_correction
                )
    if backlash_correction:
        out["backlash_correction"] = float(backlash_correction)

    out["true"] = {}
    for dirn in ("cw", "ccw"):
        w = devs.get(f"wheel_{dirn}")
        v = devs.get(f"worm_{dirn}")
        if w and v:
            tmin, tmax = true_min_max(w[1], v[1])
            out["true"][dirn] = dict(true_min=tmin, true_max=tmax)

    return out, devs


def repeatability_summary(points, data):
    """再現性測定の結果サマリ。

    points: ブロックの指令角度リスト
    data: {("cw"|"ccw", ブロック番号): [測定値, …]}（RepeatabilitySequence.data）

    各ブロック・方向で読み取り値の最大−最小[秒]を出し、
    全ブロックで一番大きいものを再現性の結果にする。
    """
    blocks = []
    for i, angle in enumerate(points):
        row = dict(angle=float(angle))
        for dirn in ("cw", "ccw"):
            vals = data.get((dirn, i)) or []
            # 0/360またぎだけ折り返してから最大−最小（またがない値はビット同一）
            vals = [rep_unwrap(angle, v) for v in vals]
            row[dirn] = (max(vals) - min(vals)) * 3600.0 if len(vals) >= 2 else None
        blocks.append(row)

    def worst(dirn):
        vals = [b[dirn] for b in blocks if b[dirn] is not None]
        return max(vals) if vals else None

    cw, ccw = worst("cw"), worst("ccw")
    candidates = [v for v in (cw, ccw) if v is not None]
    return dict(
        blocks=blocks, cw=cw, ccw=ccw, overall=max(candidates) if candidates else None
    )


def _paired_backlash(data, block):
    """同一指令角度でペアにした (角度, バックラッシ[秒]) を角度昇順で返す"""
    cw_targets, cw_meas = data.get(f"{block}_cw", ([], []))
    ccw_by_target = {round(t, 6): m
                     for t, m in zip(*data.get(f"{block}_ccw", ([], [])))}
    pairs = []
    for t, m_cw in sorted(zip(cw_targets, cw_meas)):
        m_ccw = ccw_by_target.get(round(t, 6))
        if m_ccw is None:
            continue
        bl = float(deviation_sec([t], [m_ccw])[0] - deviation_sec([t], [m_cw])[0])
        pairs.append((float(t), bl))
    return pairs


def composite_backlash_minmax(data, range_=None):
    """機械総合のバックラッシ（0°合わせ）のMIN/MAX[秒]。

    ホイールの各点バックラッシ（CCW-CW）を点間で直線補間し、その上に
    ウォームのバックラッシ周期成分（自身の始終差の直線を除去）を乗せる。
    ウォーム0°位置の状態とホイール0°位置の状態の差を全体にシフトして合わせる。
    261166ITY.KSの旧アプリ表示（全範囲24.2/9.4・範囲1 20.1/9.4・
    範囲2 20.2/9.4）と一致することを確認済み。

    range_: ホイール角度の評価範囲 (開始, 終了)。Noneなら全範囲。
    返り値: (min, max) または計算不能のとき None。
    """
    wheel = _paired_backlash(data, "wheel")
    worm = _paired_backlash(data, "worm")
    if len(wheel) < 2 or len(worm) < 2:
        return None
    h_t = np.array([p[0] for p in wheel])
    bl_h = np.array([p[1] for p in wheel])
    w_t = np.array([p[0] for p in worm])
    bl_w = np.array([p[1] for p in worm])
    span = float(w_t[-1] - w_t[0])
    if span <= 0:
        return None
    # ウォームの周期成分（始終差の直線をベースラインとして除去）
    w_rel = bl_w - (bl_w[0] + (bl_w[-1] - bl_w[0]) * (w_t - w_t[0]) / span)
    phis = (w_t - w_t[0])[:-1]  # 周期の終端は次の繰り返しの始点と同じ
    w_rel = w_rel[:-1]
    # 0°合わせ: ウォーム0°位置の状態とホイール0°位置の状態の差をシフト
    zero_index = int(np.argmin(np.abs(h_t)))
    shift = float(bl_w[0] - bl_h[zero_index])

    def in_range(t):
        return range_ is None or (range_[0] - 1e-9 <= t <= range_[1] + 1e-9)

    values = []
    for i in range(len(h_t) - 1):
        seg = float(h_t[i + 1] - h_t[i])
        if seg <= 0:
            continue
        offset = 0.0
        while offset < seg - 1e-9:
            for phi, rel in zip(phis, w_rel):
                tp = offset + phi
                if tp >= seg - 1e-9:
                    break
                t = h_t[i] + tp
                if in_range(t):
                    frac = tp / seg
                    values.append(bl_h[i] * (1 - frac) + bl_h[i + 1] * frac
                                  + rel + shift)
            offset += span
    if in_range(h_t[-1]):
        values.append(float(bl_h[-1]) + float(w_rel[0]) + shift)
    if not values:
        return None
    return (float(min(values)), float(max(values)))


def composite_backlash_at_zero(data):
    """総合バックラッシの 0°位置の値[秒]（補正前）。

    0°合わせの定義上、0°位置の総合バックラッシ＝ウォーム0°位置のバックラッシ
    （composite_backlash_minmax の導出より）。バックラッシ手動補正で「回転＝
    0°位置のバックラッシ量を実測値に合わせる」ときの基準『今の値』に使う。
    計算不能なら None。
    """
    worm = _paired_backlash(data, "worm")
    if not worm:
        return None
    # ウォームの 0°位置（|t| 最小）の点のバックラッシ
    t0 = min(range(len(worm)), key=lambda i: abs(worm[i][0]))
    return float(worm[t0][1])
