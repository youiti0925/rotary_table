# -*- coding: utf-8 -*-
"""フルクロ → セミクロ変換（Qt なし・純関数）。

設計: 設計書「フルクロ→セミクロ変換」。製品の値の辞書（番号→値）を、prepare に渡す前に書き換えるだけ。
BASIC・機械の控えの行には触らない（値だけ置換するのは今までどおり param_build.prepare の仕事）。

実データで確かめたこと（2026-11-24・共有の製品データ 15,741 件・FANUC 表 13,309 件。読むだけ）:
  ・セミの式 L = 360 × 減速比 ÷ (最小単位 ÷ CMR倍率)。2084/2085 = L/1,000,000 を約分、1821 = L、
    2024 = 12500、2023 = 8192。計算できた 10,803 件中 10,802 件が実物と一致（不一致 1 件は減速比 1/990 で
    割り切れない R60262B）。
  ・セミ全件: 2018 の行なし・2209 #6=0・2000 #0=0。2000 #4 は 1,109 件が 1（1815 が '001*0000' 表記の製品）。
    → 2000 #4 は「フルで 1 のとき 0 にする」を既定とし、差の一覧に「要確認」と出す（未確認）。
  ・別置検出器が MPI/MPR 系のフルだけが 1874〜1876・2015・2141 を持つ（フル 1,897 件・セミ 0 件）
    → セミでは要らないので書かない（外した番号は画面に出す）。
未確認: CMR(1820) が 2 以外のとき。→ ALLOWED_CMR 以外は変換しない（実データの結果が来たら広げる）。
"""

import os
from fractions import Fraction
from pathlib import Path

from . import fanuc_param, param_build, prm_format

# 変換してよい CMR（1820 の設定値。設定値÷2 が倍率）。実データで確かめた値だけ。広げるならここ1か所。
ALLOWED_CMR = (2,)

PPLS_SEMI = 12500          # 2024
VELPULSE_SEMI = 8192       # 2023
GEAR_DIV = 1_000_000       # 2084/2085 = L / 1,000,000
GEAR_MAX = 32767           # 2084/2085 の範囲（FANUC の上限。マニュアルの記憶で、実データでは未確認）

NAMES = {
    1815: "別置検出器", 1821: "レファレンスカウンタ", 2000: "パラメータ設定",
    2018: "別置検出器信号回転方向", 2023: "速度パルス数", 2024: "位置パルス数(PPLS)",
    2084: "ギヤ分子", 2085: "ギヤ分母", 2209: "ポジションゲイン内部精度向上",
}
# セミでは要らない（実データでセミは 0 件）番号。2018 のほか MPI/MPR 系の別置検出器用
DROP_NUMBERS = (2018, 1874, 1875, 1876, 2015, 2141)
DROP_NAMES = {2018: NAMES[2018]}
_MPI_NAME = "別置検出器(MPI/MPR系)用"


def _num_of(key):
    n = fanuc_param._split_common_key(key)[0]
    try:
        return int(str(n).strip().lstrip("Nn"))
    except ValueError:
        return None


def _find_key(values, number):
    """フラットな値の辞書から番号のキーを探す（軸ラベル付きキーは見ない）。"""
    for k in values:
        if isinstance(k, tuple):
            continue
        if _num_of(k) == int(number):
            return k
    return None


def _clean(v):
    return str(v).strip().lstrip("'\"").strip()


def to_number(v):
    """'+360.000' / "'360.0" / '2' → Fraction。読めなければ None。"""
    s = _clean(v).replace("+", "", 1) if _clean(v).startswith("+") else _clean(v)
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


def parse_gear(text):
    """'1/180' '1:180' → Fraction(1,180)。読めなければ None。"""
    s = _clean(text).replace("：", ":").replace("／", "/")
    for sep in ("/", ":"):
        if sep in s:
            a, _, b = s.partition(sep)
            try:
                fa, fb = Fraction(a.strip()), Fraction(b.strip())
                return fa / fb if fb else None
            except (ValueError, ZeroDivisionError):
                return None
    return None


def set_bit_masked(value, bit, on):
    """ビット列（'*'＝どちらでもよい入り）の bit 番だけを 0/1 にする。他の桁は 1 文字も変えない。
    先頭の ' は残す。ビット列でなければ（10進など）None。"""
    raw = str(value).strip()
    q = ""
    while raw[:1] in ("'", '"'):
        q, raw = q + raw[0], raw[1:]
    s = raw.strip()
    if not s or len(s) < 2 or not set(s) <= set("01*"):
        return None
    pad = "*" if "*" in s else "0"
    if len(s) < max(8, bit + 1):
        s = s.rjust(max(8, bit + 1), pad)
    i = len(s) - 1 - bit
    return q + s[:i] + ("1" if on else "0") + s[i + 1:]


def bit_char(value, bit):
    """'0' / '1' / '*'。読めなければ ''。"""
    s = _clean(value)
    if len(s) < 2 or not set(s) <= set("01*"):
        return ""
    if len(s) <= bit:
        return "*" if "*" in s else "0"
    return s[len(s) - 1 - bit]


def _fmt_frac(f):
    return str(int(f)) if f.denominator == 1 else str(float(f))


def _unit(v1013):
    """設定単位(度)。1013 の #0=ISA・#1=ISC。読めなければ (None, 理由)。"""
    if v1013 is None:
        return None, "1013（設定単位）が読めません"
    a, c = bit_char(v1013, 0), bit_char(v1013, 1)
    if a not in ("0", "1") or c not in ("0", "1"):
        return None, f"1013（設定単位）が読めません（値: {v1013}）"
    if a == "1" and c == "1":
        return None, f"1013 の設定単位が決まりません（値: {_clean(v1013)}）"
    if c == "1":
        return Fraction(1, 10000), ""
    if a == "1":
        return Fraction(1, 100), ""
    return Fraction(1, 1000), ""


def back_calc_gear(ppls_full, unit_eff, one_turn=360):
    """フルの 2024 = 12500÷N の切り捨て から減速比 1/N を逆算する。
    整数 N に1つに決まるものだけ返す（1つに決まらなければ None）。N はモーター1回転のパルスが整数になるもの。"""
    p = to_number(ppls_full)
    if p is None or p.denominator != 1 or p <= 0:
        return None
    p = int(p)
    lo, hi = 12500 // (p + 1), 12500 // p      # lo < N <= hi
    cands = []
    for n in range(lo + 1, hi + 1):
        if 12500 // n != p:
            continue
        L = Fraction(one_turn) / unit_eff / n
        if L.denominator == 1:
            cands.append(n)
    return Fraction(1, cands[0]) if len(cands) == 1 else None


def detect(raw, values, axis, *, loop_number="1815", loop_bit=1, full_when=1):
    """'フル' / 'セミ' / ''（読めない）。param_build.detect_mode と同じ判定。"""
    try:
        mode, _eff = param_build.detect_mode(raw or "", values or {}, axis, number=loop_number,
                                             bit=int(loop_bit), full_when=int(full_when))
    except Exception:
        return ""
    return mode


def convert(values, *, raw="", axis=None, gear_header="", gear_db="", lookup=None,
            loop_number="1815", loop_bit=1, full_when=1):
    """製品の値（{番号: 値}）をセミクロ用にする。

    戻り値 dict:
      mode     … 製品の判定 'フル' / 'セミ' / ''
      stop     … 変換できない理由（空なら変換できる）。人に値を入れさせない
      values   … セミクロにした値（stop があれば元のまま）。prepare に渡すもの
      changes  … [{num, name, full, semi, note, review}]  差の一覧（変える番号）
      skipped  … [{num, name, full, note}]               書かない番号（製品の値から外す）
      basis    … 計算の根拠 1 行
    lookup(番号) は、製品の値に無い番号の「今の値」（BASIC の当該軸）を返す関数。
    """
    values = dict(values or {})
    out = {"mode": "", "stop": [], "values": values, "changes": [], "skipped": [], "basis": "",
           "gear": ""}
    if lookup is None and raw and axis:
        lookup = lambda n: fanuc_param.value_on_axis(raw, n, axis)

    def get(n):
        k = _find_key(values, n)
        if k is not None:
            return values[k]
        return lookup(n) if lookup else None

    mode = detect(raw, values, axis, loop_number=loop_number, loop_bit=loop_bit, full_when=full_when)
    out["mode"] = mode
    if mode == "セミ":
        out["stop"].append("この製品はもうセミクロ用です（1815 の別置検出器が 0）")
        return out
    if mode != "フル":
        out["stop"].append(f"製品の {loop_number} #{loop_bit} が読めないため、フルクロか分かりません")
        return out

    stop = out["stop"]
    # ---- 単位・CMR・1回転の移動量 ----
    unit, why = _unit(get(1013))
    if unit is None:
        stop.append(why)
    cmr = to_number(get(1820))
    mult = None
    if cmr is None:
        stop.append("1820（CMR）が読めません")
    elif cmr.denominator != 1 or int(cmr) not in ALLOWED_CMR:
        stop.append(f"CMR が {'・'.join(str(x) for x in ALLOWED_CMR)} 以外のため確かめていません"
                    f"（値: {_clean(get(1820))}）")
    else:
        mult = cmr / 2
    one = to_number(get(1260))
    if one is None:
        stop.append("1260（1回転の移動量）が読めません")
    elif one != 360:
        stop.append(f"1260（1回転の移動量）が 360 ではないため確かめていません（値: {_clean(get(1260))}）")
    if stop:
        return out
    unit_eff = unit / mult                       # 最小単位 ÷ CMR倍率
    table_pulses = Fraction(360) / unit_eff

    # ---- 減速比（ヘッダ → 登録DB → フルの 2024 から逆算）----
    rh, rd = parse_gear(gear_header), parse_gear(gear_db)
    if rh is not None and rd is not None and rh != rd:
        stop.append(f"減速比が食い違います（製品データ {_clean(gear_header)} ／ 登録 {_clean(gear_db)}）")
        return out
    ratio, src = (rh, "製品データ") if rh is not None else ((rd, "登録") if rd is not None else (None, ""))
    ppls_full = None
    k2024 = _find_key(values, 2024)
    if k2024 is not None:
        ppls_full = values[k2024]
    if ratio is None:
        ratio = back_calc_gear(ppls_full, unit_eff) if ppls_full is not None else None
        src = "フルクロの 2024 から逆算"
        if ratio is None:
            stop.append("減速比が製品データにも登録にも無く、フルクロの 2024 からも1つに決まりません")
            return out
    elif ratio.numerator == 1 and ppls_full is not None:
        p = to_number(ppls_full)
        if p is not None and p.denominator == 1 and 12500 // int(ratio.denominator) != int(p):
            stop.append(f"減速比 {ratio.numerator}/{ratio.denominator}（{src}）とフルクロの 2024"
                        f"（{_clean(ppls_full)}）が合いません")
            return out
    if ratio >= 1:
        stop.append("減速比が 1/1 以上（ダイレクトドライブ）のため、変換の対象外です")
        return out
    out["gear"] = f"{ratio.numerator}/{ratio.denominator}"
    L = table_pulses * ratio                     # モーター1回転のパルス数
    if L.denominator != 1 or L <= 0:
        stop.append(f"減速比 {out['gear']} ではモーター1回転のパルス数が割り切れません"
                    f"（{float(L):g}）。自動では出せません")
        return out
    L = int(L)
    g = Fraction(L, GEAR_DIV)
    if g.numerator > GEAR_MAX or g.denominator > GEAR_MAX:
        stop.append(f"ギヤ分子/分母（{g.numerator}/{g.denominator}）が範囲 {GEAR_MAX} を超えます。自動では出せません")
        return out
    out["basis"] = (f"減速比 {out['gear']}（{src}）・テーブル1回転 {int(table_pulses)} パルス"
                    f"・モーター1回転 {L} パルス")

    # ---- 値を作る ----
    new = dict(values)
    changes = out["changes"]

    def getn(n):                                   # 変えた後の値（同じ番号を2回変えても消し合わない）
        k = _find_key(new, n)
        return new[k] if k is not None else get(n)

    def put(n, v):
        k = _find_key(new, n)
        new[k if k is not None else str(n)] = v

    def num_change(n, target, note):
        cur = getn(n)
        cv = to_number(cur) if cur is not None else None
        if cv is not None and cv == target:
            return
        s = str(int(target)) if Fraction(target).denominator == 1 else str(float(target))
        put(n, s)
        changes.append({"num": n, "name": NAMES[n], "full": "" if cur is None else _clean(cur),
                        "semi": s, "note": note, "review": False})

    def bit_change(n, bit, on, note, *, only_if=None, force=False, review=False):
        cur = getn(n)
        if cur is None:
            return
        ch = bit_char(cur, bit)
        if ch == "":
            return
        if not force and ch != only_if:
            return
        if ch == ("1" if on else "0") and not force:
            return
        newv = set_bit_masked(cur, bit, on)
        if newv is None or newv == str(cur).strip():
            return
        put(n, newv)
        changes.append({"num": n, "name": NAMES[n], "full": _clean(cur), "semi": _clean(newv),
                        "note": note, "review": review})

    bit_change(int(str(loop_number)), int(loop_bit), not bool(int(full_when)),
               f"#{loop_bit} を 0 に", force=True)
    num_change(1821, Fraction(L), f"{int(table_pulses)} × {out['gear']} ＝ モーター1回転のパルス数")
    bit_change(2000, 0, False, "#0 を 0 に（セミクロは全部 0）", only_if="1")
    bit_change(2000, 4, False, "#4 を 0 に（実データで一番多い傾向。要確認）", only_if="1", review=True)
    num_change(2023, Fraction(VELPULSE_SEMI), "セミクロは 8192")
    num_change(2024, Fraction(PPLS_SEMI), "セミクロは 12500")
    num_change(2084, Fraction(g.numerator), f"{L} / {GEAR_DIV} を約分")
    num_change(2085, Fraction(g.denominator), f"{L} / {GEAR_DIV} を約分")
    bit_change(2209, 6, False, "#6 を 0 に", only_if="1")

    # ---- 書かない番号 ----
    kept, dropped = param_build.drop_params(new, DROP_NUMBERS)
    for k, v in dropped.items():
        n = _num_of(k)
        out["skipped"].append({"num": n, "name": DROP_NAMES.get(n, _MPI_NAME), "full": _clean(v),
                               "note": "書かない（製品データから外す。機械の今の値のまま）"})
    out["values"] = kept
    return out


# ------------------------------------------------------------------ 同じ型式のセミクロ実物（参考）
def _norm_gear(s):
    f = parse_gear(s)
    return f if f is not None else _clean(s)


def find_semi_reference(folder, *, model, gear="", motor_no="", kind="", exclude_path="",
                        loop_number="1815", loop_bit=1, full_when=1, cancel=None, max_files=None):
    """製品の場所から、型式・減速比・モーター・傾斜/回転が同じで 1815 #1=0（セミ）の実物を1つ探す。
    読むだけ。画面を固めないよう、呼ぶ側が別スレッドで呼ぶ（cancel() が真なら中止）。
    戻り値 {path, name, values{番号:値}}、無ければ None。"""
    model = str(model or "").strip()
    prefix = {"傾斜": "T", "回転": "R"}.get(kind, "")
    if not model or not folder or not Path(folder).is_dir():
        return None
    ex = os.path.normcase(os.path.abspath(exclude_path)) if exclude_path else ""
    seen = 0
    with os.scandir(folder) as it:
        names = sorted((e.name for e in it
                        if e.name[:1].upper() in (("T", "R") if not prefix else (prefix,))
                        and ("." + e.name.rpartition(".")[2].lower()) in (".prm", ".txt", ".dat")))
    for name in names:
        if cancel and cancel():
            return None
        if max_files is not None and seen >= max_files:
            return None
        p = Path(folder) / name
        if ex and os.path.normcase(os.path.abspath(p)) == ex:
            continue
        seen += 1
        try:                                  # 先頭だけ読んで型式が違えば捨てる（共有に約1.5万件あるため）
            with open(p, "rb") as fh:
                hb = fh.read(4096)
                if ("Model=" + model) not in hb.decode("cp932", errors="replace"):
                    continue
                text = (hb + fh.read()).decode("cp932", errors="replace")
        except (OSError, UnicodeError):
            continue
        try:
            doc = prm_format.parse_prm(text)
        except Exception:
            continue
        hv = lambda k: prm_format.header_get(doc, k, "").strip()
        if hv("Model") != model:
            continue
        if gear and _norm_gear(hv("Gear Rate")) != _norm_gear(gear):
            continue
        if motor_no and hv("Motor Number") and hv("Motor Number") != str(motor_no).strip():
            continue
        v1815 = prm_format.param_value(doc, loop_number)
        if fanuc_param.closed_loop_mode(v1815, int(loop_bit), int(full_when)) != "セミ":
            continue
        vals = {num: v for (num, v, _jp, _en) in prm_format.iter_params(doc) if v != ""}
        return {"path": str(p), "name": name, "values": vals}
    return None


def _same_value(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    sa, sb = _clean(a), _clean(b)
    if len(sa) > 1 and len(sb) > 1 and set(sa) <= set("01*") and set(sb) <= set("01*"):
        n = max(len(sa), len(sb))
        sa, sb = sa.rjust(n, "*"), sb.rjust(n, "*")
        return all(x == y or "*" in (x, y) for x, y in zip(sa, sb))
    fa, fb = to_number(sa), to_number(sb)
    if fa is not None and fb is not None:
        return fa == fb
    return sa == sb


def compare_reference(result, actual_values):
    """計算した値と実物の値を、変える番号・書かない番号ごとに比べる。
    戻り値 {matched, total, mismatch:[(番号, 計算の値, 実物の値)]}。実物は参考（書く値は変えない）。"""
    vals = result.get("values") or {}
    act = {}
    for k, v in (actual_values or {}).items():
        n = _num_of(k)
        if n is not None:
            act.setdefault(n, v)
    nums = [c["num"] for c in result.get("changes", [])] + [s["num"] for s in result.get("skipped", [])]
    matched, bad = 0, []
    for n in nums:
        k = _find_key(vals, n)
        mine = vals[k] if k is not None else None      # 書かない番号は None
        if _same_value(mine, act.get(n)):
            matched += 1
        else:
            bad.append((n, "—" if mine is None else _clean(mine),
                        "（行なし）" if act.get(n) is None else _clean(act[n])))
    return {"matched": matched, "total": len(nums), "mismatch": bad}
