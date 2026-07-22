# -*- coding: utf-8 -*-
"""測定結果から FANUC ピッチエラー補正『パラメータ』を作る（閉ループの片側）。

測定→補正表（pcorr.compensation_table）→ここで FANUC 実機が必要とする形へ変換する:
  - 設定パラメータ  No.3620 基準点番号 / 3621 負側端点番号 / 3622 正側端点番号 /
                    3623 補正倍率 / 3624 補正点間隔 / 3625 回転軸1回転あたり移動量
  - 補正点データ    各点の“増分”＝直前の点との差（検出単位）。実機はこれを基準点
                    から積算する。

補正の考え方:
  測定位置 = 指令 + 誤差(θ) なので、これを打ち消す絶対補正 A(θ) = −誤差(θ)。
  基準点(0°)の補正は 0 に正規化する（ピッチ誤差補正は基準位置からの相対量）。
  実機に設定する値は増分  incr[n] = A[n] − A[n-1]  （検出単位）。
  回転軸(rotation type)は1回転で誤差が閉じるはずなので、丸め残差を配分して
  増分の総和を 0 にする（各回転で基準がずれないように）。

※ 番号体系・データ格納先・符号の向きは制御機種で細部が異なる。ここは FANUC
  30i/31i 系の標準的な体系を既定にしている。実機の補正設定済みサンプル .prm を
  もらい次第、番号・符号・格納先を合わせる（すべて引数で変更可能）。
"""

DETECT_UNIT_DEG = 0.001   # 検出単位（回転軸の最小指令単位）。既定 1/1000°


def _abs_comp_units(rows, detect_unit_deg, sign):
    """補正表の行 → 各点の絶対補正量[検出単位・整数]（採用偏差＝CW/CCW平均を打ち消す）。

    rows は pcorr.compensation_table() の出力（no/angle/cw/ccw/mean/…）。mean は秒["]。
    """
    out = []
    for r in rows:
        # A(θ) = −誤差 を検出単位の整数へ。sign は実機の符号違いを吸収する保険。
        units = int(round(sign * (-(r["mean"] / 3600.0)) / detect_unit_deg))
        out.append((float(r["angle"]), units))
    return out


def build_pitch_params(rows, *, axis=4, interval_deg,
                       detect_unit_deg=DETECT_UNIT_DEG, magnification=1,
                       rotary=True, per_rev_deg=360.0, sign=1,
                       cfg_ref=3620, cfg_neg=3621, cfg_pos=3622,
                       cfg_mag=3623, cfg_interval=3624, cfg_perrev=3625,
                       data_base=10000):
    """補正表 rows から FANUC ピッチエラー補正パラメータ一式を作る。

    返り値 dict:
      config: [(番号, 軸, 値, 説明)]              … No.3620〜3625
      data:   [(番号, 増分値, 角度)]               … 補正点データ（検出単位）
      points: [(点番号, 角度, 絶対補正[検出単位], 増分[検出単位])]
      notes:  [str]
    引数の cfg_* / data_base で番号体系を機種に合わせられる。
    """
    notes = []
    pts = _abs_comp_units(rows, detect_unit_deg, sign)
    if len(pts) < 2:
        return {"config": [], "data": [], "points": [], "notes": ["補正点が足りません（分割データが必要）"]}

    # 基準点(最も0°に近い点)の補正を 0 に正規化 ＝ 全点からその値を引く
    ref_i = min(range(len(pts)), key=lambda i: abs(pts[i][0]))
    ref_units = pts[ref_i][1]
    cum = [u - ref_units for (_a, u) in pts]        # 絶対補正（基準=0）
    angles = [a for (a, _u) in pts]

    # 増分 incr[k] = cum[k] − cum[k-1]。先頭は基準からの差（cum[0]−0=cum[0]）
    incr = [cum[0]] + [cum[k] - cum[k - 1] for k in range(1, len(cum))]

    # 回転軸: 1回転で閉じるはずなので総和を0へ（丸め残差を大きい増分から1ずつ配分）
    if rotary:
        residue = sum(incr)
        if residue != 0:
            order = sorted(range(len(incr)), key=lambda k: -abs(incr[k]))
            step = 1 if residue < 0 else -1
            for k in range(abs(residue)):
                incr[order[k % len(order)]] += step
            notes.append(f"回転軸のため増分総和を0に補正（残差 {residue} 検出単位を配分）")

    n = len(pts)
    neg_no = data_base
    pos_no = data_base + n
    ref_no = data_base + ref_i          # 基準点の番号（0°位置）

    config = [
        (cfg_ref, axis, ref_no, "基準位置の補正点番号"),
        (cfg_neg, axis, neg_no, "負側端の補正点番号"),
        (cfg_pos, axis, pos_no, "正側端の補正点番号"),
        (cfg_mag, axis, magnification, "補正倍率"),
        (cfg_interval, axis, int(round(interval_deg / detect_unit_deg)),
         f"補正点間隔（{interval_deg:g}°）"),
    ]
    if rotary:
        config.append(
            (cfg_perrev, axis, int(round(per_rev_deg / detect_unit_deg)),
             f"1回転あたり移動量（{per_rev_deg:g}°）"))

    # データ点: 番号は neg_no+1 .. pos_no（1回転ぶん）。各点に増分を割り当てる
    data = []
    points = []
    for k in range(n):
        point_no = data_base + 1 + k
        data.append((point_no, incr[k], angles[k]))
        points.append((point_no, angles[k], cum[k], incr[k]))

    if any(abs(v) > 127 for v in incr):
        notes.append("増分が±127検出単位を超える点があります（機種により1点の上限を"
                      "超える可能性。補正倍率や間隔の見直しを）")
    return {"config": config, "data": data, "points": points, "notes": notes}


def _line(number, value, axis=None):
    """FANUC ネイティブ .PRM の1行を作る。軸指定ありは A<axis>P、無しは P（番号なし）。"""
    num = str(int(number)).zfill(5)
    if axis is None:
        return f"N{num}Q1P{int(value)}"
    return f"N{num}Q1A{int(axis)}P{int(value)}"


def to_prm_text(params, newline="\n"):
    """パラメータ一式を実機取込用の .PRM テキスト（%…%）にする。

    設定(3620-3625・軸指定)＋補正点データ(番号なしP)。値の捏造はせず、測定から
    決まった値のみを書く。取込は人が PWE=1・電源再投入に注意して実施する。
    """
    lines = ["%"]
    for (number, axis, value, _desc) in params.get("config", []):
        lines.append(_line(number, value, axis))
    for (number, value, _angle) in params.get("data", []):
        lines.append(_line(number, value))
    lines.append("%")
    return newline.join(lines) + newline
