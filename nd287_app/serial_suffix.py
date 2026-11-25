# -*- coding: utf-8 -*-
"""機番末尾のつけ方（社内の表。docs/機番末尾のつけ方_社内表.md）。Qt なし。

機番 ＋ モーター（駆動装置 K／正規 なし／仮 I）＋ クランプ（G）＋ 測る種類（表の列の順）＋ 精度NG（-X番号）。
フルクロ（クローズ）は末尾 C、セミクロは文字なし。記号の表はここ1か所（違えば直す）。
"""

MOTOR = {"driver": "K", "normal": "", "temp": "I"}            # 駆動装置 / 正規モータ / 仮モータ
# 測る種類（表の列の順。複数あるときはこの順に並べる）
KIND_ORDER = ("tilt", "rotary", "multi", "fixed", "move", "repair_in", "repair_out", "close",
              "tilt_set", "rotary_set", "tilt_set_after", "rotary_set_after")
KIND_CODE = {"tilt": "T", "rotary": "R", "multi": "M", "fixed": "F", "move": "S", "repair_in": "U",
             "repair_out": "E", "close": "C", "tilt_set": "TY", "rotary_set": "RY",
             "tilt_set_after": "DTY", "rotary_set_after": "DRY"}
KIND_LABEL = {"semi": "セミクロ", "tilt": "傾斜単品", "rotary": "回転単品", "multi": "多軸", "fixed": "対向（固定）",
              "move": "対向（移動）", "repair_in": "修理受入", "repair_out": "修理後", "close": "クローズ（フルクロ）",
              "tilt_set": "傾斜完組", "rotary_set": "回転完組", "tilt_set_after": "傾斜完組・慣らし後",
              "rotary_set_after": "回転完組・慣らし後"}


def suffix(motor="normal", clamp=False, kinds=(), multi_axis=1, ng=0):
    """末尾の文字列を返す。

    motor: driver / normal / temp。clamp: クランプか。kinds: 測る種類（複数可。何も無い＝セミクロ＝文字なし）。
    multi_axis: 多軸のとき何軸目か（1=M、2〜4=数字）。ng: 精度NGの回数（0=付けない、1=-X1、2=-X2…）。
    """
    if motor not in MOTOR:
        raise ValueError(f"モーターの種類が分かりません: {motor}")
    ks = [k for k in KIND_ORDER if k in set(kinds)]
    unknown = set(kinds) - set(KIND_ORDER) - {"semi"}
    if unknown:
        raise ValueError(f"測る種類が分かりません: {sorted(unknown)}")
    out = MOTOR[motor] + ("G" if clamp else "")
    for k in ks:
        if k == "multi":
            n = int(multi_axis)
            if not 1 <= n <= 4:
                raise ValueError("多軸は1〜4軸目です")
            out += "M" if n == 1 else str(n)
        else:
            out += KIND_CODE[k]
    if ng:
        out += f"-X{int(ng)}"
    return out


def file_stem(serial, **kw):
    """機番に末尾を付けた名前（拡張子なし）。"""
    return f"{str(serial).strip()}{suffix(**kw)}"
