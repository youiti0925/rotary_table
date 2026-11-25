# -*- coding: utf-8 -*-
"""機番末尾のつけ方（社内の表「分割精度測定 機番末尾のつけ方」。docs/機番末尾のつけ方_社内表.md・写真つき）。Qt なし。

表の**セルをそのまま**対応表として持つ（規則から組み立てない。写真を拡大して1つずつ確かめた）。
行 = (モーター, クランプ): 駆動装置 driver=K／正規 normal=なし／仮 temp=I × クランプ G。
列 = 測る種類。None は表で斜線（該当なし）。多軸は [1軸目(M), 2, 3, 4軸目] の4つ。
**正規モータ・アンクランプの傾斜単品・回転単品は「TT」「RR」**（写真で確認。T／R ではない）。
複数の条件は「左から順に」付ける。表の例は 回転単品クローズ=KRC・多軸1軸目の対向（固定）=KMF。
正規モータの複数条件（RR＋C など）の付け方は表に例が無く**未確認**（ここでは「最初のセルの文字＋あとの条件の列の記号」）。
精度NGは機番の一番後ろに -X1、-X2…（測定回数に応じて）。
"""

KIND_ORDER = ("tilt", "rotary", "multi", "fixed", "move", "repair_in", "repair_out", "close",
              "tilt_set", "rotary_set", "tilt_set_after", "rotary_set_after")
KIND_LABEL = {"semi": "セミクロ", "tilt": "傾斜単品", "rotary": "回転単品", "multi": "多軸", "fixed": "対向（固定）",
              "move": "対向（移動）", "repair_in": "修理受入", "repair_out": "修理後", "close": "クローズ（フルクロ）",
              "tilt_set": "傾斜完組", "rotary_set": "回転完組", "tilt_set_after": "傾斜完組・慣らし後",
              "rotary_set_after": "回転完組・慣らし後"}
# 列の記号（表の見出しの［ ］）。複数条件で、2つ目以降に足す文字
COLUMN_MARK = {"tilt": "T", "rotary": "R", "fixed": "F", "move": "S", "repair_in": "U", "repair_out": "E",
               "close": "C", "tilt_set": "TY", "rotary_set": "RY", "tilt_set_after": "DTY", "rotary_set_after": "DRY"}
ROW_PREFIX = {("driver", False): "K", ("driver", True): "KG", ("normal", False): "", ("normal", True): "G",
              ("temp", False): "I", ("temp", True): "IG"}

TABLE = {
    ("driver", False): {
        "semi": "K",
        "tilt": "KT",
        "rotary": "KR",
        "multi": ["KM", "K2", "K3", "K4"],
        "fixed": "KF",
        "move": "KS",
        "repair_in": "KU",
        "repair_out": "KE",
        "close": "KC",
        "tilt_set": "KTY",
        "rotary_set": "KRY",
        "tilt_set_after": "KDTY",
        "rotary_set_after": "KDRY",
    },
    ("driver", True): {
        "semi": "KG",
        "tilt": "KGT",
        "rotary": "KGR",
        "multi": ["KGM", "KG2", "KG3", "KG4"],
        "fixed": "KGF",
        "move": "KGS",
        "repair_in": "KGU",
        "repair_out": "KGE",
        "close": "KGC",
        "tilt_set": None,
        "rotary_set": None,
        "tilt_set_after": None,
        "rotary_set_after": None,
    },
    ("normal", False): {
        "semi": "",
        "tilt": "TT",
        "rotary": "RR",
        "multi": ["M", "2", "3", "4"],
        "fixed": "F",
        "move": "S",
        "repair_in": "U",
        "repair_out": "E",
        "close": "C",
        "tilt_set": "TY",
        "rotary_set": "RY",
        "tilt_set_after": "DTY",
        "rotary_set_after": "DRY",
    },
    ("normal", True): {
        "semi": "G",
        "tilt": "GT",
        "rotary": "GR",
        "multi": ["GM", "G2", "G3", "G4"],
        "fixed": "GF",
        "move": "GS",
        "repair_in": "GU",
        "repair_out": "GE",
        "close": "GC",
        "tilt_set": "GTY",
        "rotary_set": "GRY",
        "tilt_set_after": "GDTY",
        "rotary_set_after": "GDRY",
    },
    ("temp", False): {
        "semi": "I",
        "tilt": "IT",
        "rotary": "IR",
        "multi": ["IM", "I2", "I3", "I4"],
        "fixed": "IF",
        "move": "IS",
        "repair_in": "IU",
        "repair_out": "IE",
        "close": "IC",
        "tilt_set": "ITY",
        "rotary_set": "IRY",
        "tilt_set_after": "IDTY",
        "rotary_set_after": "IDRY",
    },
    ("temp", True): {
        "semi": "IG",
        "tilt": "IGT",
        "rotary": "IGR",
        "multi": ["IGM", "IG2", "IG3", "IG4"],
        "fixed": "IGF",
        "move": "IGS",
        "repair_in": "IGU",
        "repair_out": "IGE",
        "close": "IGC",
        "tilt_set": "IGTY",
        "rotary_set": "IGRY",
        "tilt_set_after": "IGDTY",
        "rotary_set_after": "IGDRY",
    },
}


def cell(motor, clamp, kind, multi_axis=1):
    """表のセル1つ。表で斜線（該当なし）のときは ValueError（理由つき）。"""
    row = TABLE.get((motor, bool(clamp)))
    if row is None:
        raise ValueError(f"モーターの種類が分かりません: {motor}")
    if kind not in row:
        raise ValueError(f"測る種類が分かりません: {kind}")
    v = row[kind]
    if v is None:
        raise ValueError(f"{KIND_LABEL[kind]}は、この行（{motor}・{'クランプ' if clamp else 'アンクランプ'}）では表で斜線（該当なし）です")
    if kind == "multi":
        n = int(multi_axis)
        if not 1 <= n <= 4:
            raise ValueError("多軸は1〜4軸目です")
        return v[n - 1]
    return v


def suffix(motor="normal", clamp=False, kinds=(), multi_axis=1, ng=0):
    """末尾の文字列。kinds が空 or ["semi"] のときはセミクロのセル（駆動装置=K・正規=なし…）。複数は列の順に並べる。"""
    ks = [k for k in KIND_ORDER if k in set(kinds)]
    bad = set(kinds) - set(KIND_ORDER) - {"semi"}
    if bad:
        raise ValueError(f"測る種類が分かりません: {sorted(bad)}")
    if not ks:
        out = cell(motor, clamp, "semi")
    else:
        out = cell(motor, clamp, ks[0], multi_axis)
        prefix = ROW_PREFIX[(motor, bool(clamp))]
        for k in ks[1:]:
            if k == "multi":
                full = cell(motor, clamp, "multi", multi_axis)
                out += full[len(prefix):]
            else:
                cell(motor, clamp, k)                      # 斜線のセルなら、ここで理由つきで止める
                out += COLUMN_MARK[k]
    if ng:
        out += f"-X{int(ng)}"
    return out


def file_stem(serial, **kw):
    """機番に末尾を付けた名前（拡張子なし）。"""
    return f"{str(serial).strip()}{suffix(**kw)}"


class Selection:
    """画面の表で押したマスの状態（Qt なし）。押した順ではなく、表の左から順に末尾を作る。

    行（モーター×クランプ）は1つだけ。ほかの行のマスを押すと行が替わり、新しい行で斜線になる条件は外れる。
    セミクロのマスを押すと、条件は全部外れる（その行のセミクロの記号だけ）。同じマスをもう一度押すと外れる。
    """

    def __init__(self, motor="normal", clamp=False, kinds=(), multi_axis=1, ng=0):
        self.motor, self.clamp = motor, bool(clamp)
        self.kinds = [k for k in KIND_ORDER if k in set(kinds)]
        self.multi_axis, self.ng = int(multi_axis), int(ng)

    def suffix(self):
        return suffix(self.motor, self.clamp, self.kinds, self.multi_axis, self.ng)

    def press(self, motor, clamp, kind, multi_axis=1):
        """表のマスを押す。斜線のマスは ValueError（押せない）。"""
        cell(motor, clamp, kind, multi_axis)                       # 斜線ならここで止める
        if (motor, bool(clamp)) != (self.motor, self.clamp):
            self.motor, self.clamp = motor, bool(clamp)
            self.kinds = [k for k in self.kinds if TABLE[(motor, bool(clamp))].get(k) is not None]
        if kind == "semi":
            self.kinds = []
        elif kind == "multi":
            if "multi" in self.kinds and self.multi_axis == int(multi_axis):
                self.kinds.remove("multi")
            else:
                self.multi_axis = int(multi_axis)
                if "multi" not in self.kinds:
                    self.kinds.append("multi")
        elif kind in self.kinds:
            self.kinds.remove(kind)
        else:
            self.kinds.append(kind)
        self.kinds = [k for k in KIND_ORDER if k in set(self.kinds)]

    def is_on(self, motor, clamp, kind, multi_axis=1):
        """このマスが選ばれているか（画面で色を付ける用）。セミクロのマスは、その行で条件が空のとき。"""
        if (motor, bool(clamp)) != (self.motor, self.clamp):
            return False
        if kind == "semi":
            return not self.kinds
        if kind == "multi":
            return "multi" in self.kinds and self.multi_axis == int(multi_axis)
        return kind in self.kinds
