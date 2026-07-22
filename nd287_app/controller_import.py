# -*- coding: utf-8 -*-
"""product-inspection（Firestore）の制御装置ドキュメント → このアプリの制御装置マスタ。

product-inspection 側のフィールド名は英語/日本語/ローマ字などいろいろあり得るので、
広めの別名で拾って controllers.Controller に写す。号機・容量・アンプ・電圧・CNC を
取り、軸ごとの容量/アンプは「フラット（X容量/xCapacity…）」でも「入れ子（axes.X.
capacity…）」でも拾える。合わなかったフィールド名は呼び側へ返し、画面で確認して
別名表を足せるようにする（＝実データを見ながら確実に合わせられる）。
"""

from .controllers import AXES, Controller

# 各列の別名（小文字・記号除去で突き合わせる）
_ALIASES = {
    "unit": ["号機", "機番", "unit", "unitno", "unitnumber", "machine", "machineno",
             "machinenumber", "goki", "no", "number"],
    "cnc": ["cncユニット", "cnc", "cncunit", "cncmodel", "controller", "nc", "ncunit",
            "制御装置", "cncname"],
    "ver": ["ver", "version", "cncver", "ncver"],
    "voltage": ["制御電圧", "電圧", "voltage", "controlvoltage", "volt", "denatsu"],
    "servo": ["servo", "servover", "servoversion", "サーボ", "サーボ版"],
    "b_corr": ["-bなめらか補正", "bなめらか補正", "なめらか補正", "bcorr", "bsmooth",
               "smoothb", "nameraka"],
    "d_drive": ["-d駆動", "d駆動", "駆動", "ddrive", "drive"],
}
# 軸ごとの容量/アンプの別名テンプレ（{a}=軸文字 X/Y/…）
_CAP_TMPL = ["{a}容量", "{a}cap", "{a}capacity", "cap{a}", "capacity{a}", "{a}_capacity"]
_AMP_TMPL = ["{a}アンプ", "{a}amp", "{a}amplifier", "amp{a}", "amplifier{a}", "{a}_amp"]
# 入れ子（axes/軸ごと）を探すキー
_AXES_KEYS = ["axes", "軸", "axis", "軸構成", "servoaxes"]
_NEST_CAP = ["容量", "capacity", "cap"]
_NEST_AMP = ["アンプ", "amp", "amplifier"]


def _norm_key(k):
    """突き合わせ用に小文字化＋記号（空白/アンダーバー/ハイフン/長音）を除く。"""
    return "".join(ch for ch in str(k).lower()
                   if ch not in " _-‐-\t　")


def _index(fields):
    """{正規化キー: (元キー, 値)} を作る（元キーは未対応報告のため保持）。"""
    out = {}
    for k, v in (fields or {}).items():
        out[_norm_key(k)] = (k, v)
    return out


def _pick(index, aliases, used):
    for a in aliases:
        na = _norm_key(a)
        if na in index:
            orig, val = index[na]
            used.add(na)
            return val
    return None


def _str(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def map_controller(doc_id, fields):
    """Firestore ドキュメント → (Controller, 未対応フィールド名リスト)。

    号機が取れなければ doc_id を号機に使う。軸ごとの容量/アンプはフラット・入れ子の
    両方に対応。未対応（マスタ列に写せなかった）フィールド名を返す。
    """
    fields = fields or {}
    index = _index(fields)
    used = set()

    unit = _str(_pick(index, _ALIASES["unit"], used)) or str(doc_id or "").strip()
    cnc = _str(_pick(index, _ALIASES["cnc"], used))
    ver = _str(_pick(index, _ALIASES["ver"], used))
    voltage = _str(_pick(index, _ALIASES["voltage"], used))
    servo = _str(_pick(index, _ALIASES["servo"], used))
    b_corr = _str(_pick(index, _ALIASES["b_corr"], used))
    d_drive = _str(_pick(index, _ALIASES["d_drive"], used))

    caps, amps = {}, {}
    # 1) フラットな X容量 / xCapacity …
    for a in AXES:
        cap = _pick(index, [t.format(a=a) for t in _CAP_TMPL], used)
        amp = _pick(index, [t.format(a=a) for t in _AMP_TMPL], used)
        if cap is not None:
            caps[a] = _str(cap)
        if amp is not None:
            amps[a] = _str(amp)

    # 2) 入れ子（axes: {X: {capacity, amp}}）。フラットで取れなかった軸だけ補う
    for ak in _AXES_KEYS:
        nak = _norm_key(ak)
        if nak in index and isinstance(index[nak][1], dict):
            used.add(nak)
            axmap = index[nak][1]
            sub = {_norm_key(k): v for k, v in axmap.items()}
            for a in AXES:
                na = _norm_key(a)
                if na in sub and isinstance(sub[na], dict):
                    inner = {_norm_key(k): v for k, v in sub[na].items()}
                    if a not in caps:
                        for ck in _NEST_CAP:
                            if _norm_key(ck) in inner:
                                caps[a] = _str(inner[_norm_key(ck)]); break
                    if a not in amps:
                        for mk in _NEST_AMP:
                            if _norm_key(mk) in inner:
                                amps[a] = _str(inner[_norm_key(mk)]); break

    ctl = Controller(unit, cnc=cnc, ver=ver, voltage=voltage, servo=servo,
                     caps=caps, amps=amps, b_corr=b_corr, d_drive=d_drive)
    unmapped = [orig for nk, (orig, _v) in index.items() if nk not in used]
    return ctl, unmapped


def map_controllers(docs):
    """[(doc_id, fields)] → ([Controller], 全体の未対応フィールド名の集合)。

    号機が空の（＝写しても意味がない）行は除く。
    """
    ctls, unmapped = [], set()
    for doc_id, fields in docs:
        ctl, un = map_controller(doc_id, fields)
        if ctl.unit:
            ctls.append(ctl)
        unmapped.update(un)
    return ctls, sorted(unmapped)
