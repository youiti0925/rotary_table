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
CMR(1820): 実データで 2 と 20 が合った（倍率=設定値÷2）。それ以外は ALLOWED_CMR 外として変換しない。
"""

import os
from fractions import Fraction
from pathlib import Path

from . import fanuc_param, param_build, prm_format

# 変換してよい CMR（1820 の設定値。設定値÷2 が倍率）。実データで確かめた値だけ。広げるならここ1か所。
ALLOWED_CMR = (2, 20)            # 実データ(セミ10,802件)で 2 と 20 が「倍率=設定値÷2」で合った（2026-11-24）

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


def convert_formula(values, *, raw="", axis=None, gear_header="", gear_db="", lookup=None,
                    loop_number="1815", loop_bit=1, full_when=1):
    """計算式で、製品の値（{番号: 値}）をセミクロ用にする。

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
           "gear": "", "source": "", "compare": ""}
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
    ed = _Edit(values, lookup)
    ed.set_bit(int(str(loop_number)), int(loop_bit), not bool(int(full_when)),
               f"#{loop_bit} を 0 に", force=True)
    ed.set_num(1821, Fraction(L), f"{int(table_pulses)} × {out['gear']} ＝ モーター1回転のパルス数")
    ed.set_bit(2000, 0, False, "#0 を 0 に（セミクロは全部 0）", only_if="1")
    ed.set_bit(2000, 4, False, "#4 を 0 に（実データで一番多い傾向。要確認）", only_if="1", review=True)
    ed.set_num(2023, Fraction(VELPULSE_SEMI), "セミクロは 8192")
    ed.set_num(2024, Fraction(PPLS_SEMI), "セミクロは 12500")
    ed.set_num(2084, Fraction(g.numerator), f"{L} / {GEAR_DIV} を約分")
    ed.set_num(2085, Fraction(g.denominator), f"{L} / {GEAR_DIV} を約分")
    ed.set_bit(2209, 6, False, "#6 を 0 に", only_if="1")
    ed.drop(DROP_NUMBERS, "書かない（製品データから外す。機械の今の値のまま）")
    out["values"], out["changes"], out["skipped"] = ed.new, ed.changes, ed.skipped
    out["source"] = "計算式"
    return out


class _Edit:
    """製品の値の辞書を書き換え、差の一覧（changes / skipped）を作る。元の辞書は変えない。"""

    def __init__(self, values, lookup=None):
        self.new = dict(values)
        self.lookup = lookup
        self.changes, self.skipped = [], []

    def get(self, n):
        k = _find_key(self.new, n)
        if k is not None:
            return self.new[k]
        return self.lookup(n) if self.lookup else None

    def put(self, n, v):
        k = _find_key(self.new, n)
        self.new[k if k is not None else str(n)] = v

    def _name(self, n):
        return NAMES.get(n) or (_MPI_NAME if n in DROP_NUMBERS else "")

    def set_num(self, n, target, note, review=False):
        cur = self.get(n)
        cv = to_number(cur) if cur is not None else None
        if cv is not None and cv == target:
            return
        s = str(int(target)) if Fraction(target).denominator == 1 else str(float(target))
        self.put(n, s)
        self.changes.append({"num": n, "name": self._name(n), "full": "" if cur is None else _clean(cur),
                             "semi": s, "note": note, "review": review})

    def set_text(self, n, text, note, review=False):
        """数として読めない値（そのままの文字）を置く。数として同じなら何もしない。"""
        cur = self.get(n)
        if cur is not None and _same_value(cur, text):
            return
        self.put(n, text)
        self.changes.append({"num": n, "name": self._name(n), "full": "" if cur is None else _clean(cur),
                             "semi": _clean(text), "note": note, "review": review})

    def set_bit(self, n, bit, on, note, *, only_if=None, force=False, review=False):
        cur = self.get(n)
        if cur is None:
            return
        ch = bit_char(cur, bit)
        if ch == "":
            return
        if not force and only_if is not None and ch != only_if:
            return
        if not force and ch == ("1" if on else "0"):
            return
        newv = set_bit_masked(cur, bit, on)
        if newv is None or newv == str(cur).strip():
            return
        self.put(n, newv)
        prev = next((c for c in self.changes if c["num"] == n and c.get("bits")), None)
        if prev:                                    # 同じ番号の別のビット: 1行にまとめる
            prev["semi"] = _clean(newv)
            prev["note"] += f"／{note}"
            prev["review"] = prev["review"] or review
            return
        self.changes.append({"num": n, "name": self._name(n), "full": _clean(cur), "semi": _clean(newv),
                             "note": note, "review": review, "bits": True})

    def drop(self, numbers, note):
        kept, dropped = param_build.drop_params(self.new, numbers)
        for k, v in dropped.items():
            n = _num_of(k)
            self.skipped.append({"num": n, "name": self._name(n), "full": _clean(v), "note": note})
        self.new = kept


# ------------------------------------------------------------------ 同じ型式のフル／セミの組（第一の根拠）
# 受注番号が違っても、型式・減速比・モーター・傾斜/回転が同じなら「もの」は同じ。共有にフルとセミの両方があれば、
# その組で値が違う番号が、フル→セミの関係そのもの（実物の根拠）。計算式はその次。
INDIVIDUAL_NUMBERS = (1850, 1851, 1852, 2020, 6750, 6751, 6752, 6753, 6754)   # 受注・据付けごとに違う値は組の差に入れない
INDIVIDUAL_BITS = ((1815, 5),)                                                  # 原点確立(APZ)も据付けごと
KNOWN_NUMBERS = tuple(NAMES) + tuple(DROP_NUMBERS)                              # 実データで確かめた番号。外は「要確認」
MAX_PAIRS = 24


def _compact(m):
    return "".join(ch for ch in str(m or "").upper() if not ch.isspace() and ch != ",")


def _head(m):
    return str(m or "").split(",")[0].strip().upper()


def model_level(want, got):
    """型式の照合の順: 0=そのまま／1=空白・カンマを詰めた形／2=カンマの前だけ。合わなければ None。"""
    want, got = str(want or "").strip(), str(got or "").strip()
    if not want or not got:
        return None
    if want == got:
        return 0
    if _compact(want) == _compact(got):
        return 1
    if _head(want) == _head(got):
        return 2
    return None


def _is_bits(v):
    s = _clean(v)
    return len(s) == 8 and set(s) <= set("01*")


def pair_rules(fvals, svals):
    """フル1本・セミ1本の値から「フル→セミで変わるところ」を取り出す（キーの集合）。
    ビット列は桁ごと。受注ごとに違う番号（INDIVIDUAL_*）は入れない。"""
    rules = set()
    f = {}
    s = {}
    for src, dst in ((fvals, f), (svals, s)):
        for k, v in (src or {}).items():
            n = _num_of(k)
            if n is not None and _clean(v) != "":
                dst.setdefault(n, v)
    for n in sorted(set(f) | set(s)):
        if n in INDIVIDUAL_NUMBERS:
            continue
        if n in f and n not in s:
            rules.add(("drop", n))
        elif n in s and n not in f:
            rules.add(("val", n, _clean(s[n])))
        elif _is_bits(f[n]) and _is_bits(s[n]):
            a, b = _clean(f[n]), _clean(s[n])
            for i in range(8):
                bit = 7 - i
                if a[i] in "01" and b[i] in "01" and a[i] != b[i] and (n, bit) not in INDIVIDUAL_BITS:
                    rules.add(("bit", n, bit, b[i]))
        elif not _same_value(f[n], s[n]):
            rules.add(("val", n, _clean(s[n])))
    return rules


def vote_rules(per_pair):
    """組ごとの規則の集合 → 多数決。戻り値 (採る規則のリスト, 採らなかった [(規則, 件数)])。
    同じ番号（ビットは桁）で値が割れたときは一番多いものを採る（同数なら採らない）。"""
    from collections import Counter
    n = len(per_pair)
    cnt = Counter(r for rs in per_pair for r in rs)
    groups = {}
    for r, c in cnt.items():
        slot = r[:2] if r[0] != "bit" else r[:3]
        groups.setdefault(slot, []).append((r, c))
    taken, left = [], []
    for slot, lst in groups.items():
        lst.sort(key=lambda x: -x[1])
        top = lst[0]
        tie = len(lst) > 1 and lst[1][1] == top[1]
        if top[1] * 2 > n and not tie:
            taken.append((top[0], top[1]))
            left += lst[1:]
        else:
            left += lst
    key = lambda x: (x[0][1], x[0][0], str(x[0][2:]))
    return sorted(taken, key=key), sorted(left, key=key)


def motor_key(m):
    """モーター品番の比べ方: '#0100' など # 以降（仕様違いの印）は無視する。実データで 'A06B-2229-B000#0100' が
    33件中31件あり、完全一致で比べて 0組になっていた（2026-11-24）。"""
    return str(m or "").split("#")[0].strip().upper()


INDEX_VERSION = 1


def _load_index(path):
    try:
        import json
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("v") == INDEX_VERSION and isinstance(d.get("f"), dict):
            return d["f"]
    except Exception:
        pass
    return {}


def _save_index(path, files):
    try:
        import json
        tmp = str(path) + ".tmp"
        Path(tmp).write_text(json.dumps({"v": INDEX_VERSION, "f": files}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass                                       # 索引が書けなくても探せる（毎回読むだけ遅い）


def header_index(folder, prefixes=("T", "R"), *, index_path="", cancel=None):
    """製品の場所の全ファイルの『型式・減速比・モーター品番』を返す {名前: (型式, 減速比, モーター)}。
    先頭4KBだけ読む。共有では1件約20ms かかる（1.1万件で約220秒）ので、更新日時と大きさが同じものは
    index_path の索引から返し、変わったものだけ読み直す。索引を作る途中で cancel() が真なら None。"""
    cache = _load_index(index_path) if index_path else {}
    out, new, dirty = {}, {}, False
    with os.scandir(folder) as it:
        entries = sorted((e for e in it
                          if e.name[:1].upper() in prefixes
                          and ("." + e.name.rpartition(".")[2].lower()) in (".prm", ".txt", ".dat")),
                         key=lambda e: e.name)
    for n, e in enumerate(entries):
        if cancel and cancel():
            return None
        try:
            st = e.stat()
            sig = [int(st.st_mtime_ns), int(st.st_size)]
        except OSError:
            continue
        c = cache.get(e.name)
        if c and c[0] == sig:
            out[e.name] = (c[1], c[2], c[3]); new[e.name] = c
            continue
        try:
            with open(e.path, "rb") as fh:
                hb = fh.read(4096)
            doc = prm_format.parse_prm(hb.decode("cp932", errors="replace"))
            g = lambda k: prm_format.header_get(doc, k, "").strip()
            rec = (g("Model"), g("Gear Rate"), g("Motor Number"))
        except Exception:
            rec = ("", "", "")
        out[e.name] = rec
        new[e.name] = [sig, *rec]
        dirty = True
        if index_path and dirty and n % 2000 == 1999:
            _save_index(index_path, new)            # 途中まで保存（止めても続きから）
    if index_path and (dirty or len(new) != len(cache)):
        _save_index(index_path, new)
    return out


def find_pair_rules(folder, *, model, gear="", motor_no="", kind="", exclude_path="",
                    loop_number="1815", loop_bit=1, full_when=1, cancel=None, max_files=None, cap=6,
                    index_path=""):
    """製品の場所から、型式（そのまま→詰めた形→カンマ前の順）・減速比・モーター・傾斜/回転が同じ
    フルとセミの組を探し、「フル→セミで変わるところ」を多数決で決める。読むだけ。
    画面を固めないよう、呼ぶ側が別スレッドで呼ぶ（cancel() が真なら None）。
    index_path を渡すと、型式などの索引を保存して2回目から速くする（header_index）。

    戻り値 None（同じ型式のものが1本も無い）、または dict:
      level / n_pairs / fulls / semis（ファイル名）／ rules [(規則, 件数)] ／ unapplied [(規則, 件数)]
      semi_ref … セミの実物1本 {name, values}（組が作れないときの参考用）
    """
    model = str(model or "").strip()
    prefix = {"傾斜": "T", "回転": "R"}.get(kind, "")
    if not model or not folder or not Path(folder).is_dir():
        return None
    ex = os.path.normcase(os.path.abspath(exclude_path)) if exclude_path else ""
    head_u = _head(model)
    heads = header_index(folder, (prefix,) if prefix else ("T", "R"), index_path=index_path, cancel=cancel)
    if heads is None:
        return None
    names = []
    for name, (hmodel, hgear, hmotor) in heads.items():
        if head_u not in str(hmodel).upper():
            continue
        if model_level(model, hmodel) is None:
            continue
        if gear and hgear and _norm_gear(hgear) != _norm_gear(gear):
            continue
        if motor_no and hmotor and motor_key(hmotor) != motor_key(motor_no):
            continue
        names.append(name)
    names.sort()
    found, seen = [], 0
    for name in names:
        if cancel and cancel():
            return None
        if max_files is not None and seen >= max_files:
            break
        p = Path(folder) / name
        if ex and os.path.normcase(os.path.abspath(p)) == ex:
            continue
        seen += 1
        try:                                  # 索引で絞った候補だけ全文を読む
            text = Path(p).read_bytes().decode("cp932", errors="replace")
        except (OSError, UnicodeError):
            continue
        try:
            doc = prm_format.parse_prm(text)
        except Exception:
            continue
        hv = lambda k: prm_format.header_get(doc, k, "").strip()
        lv = model_level(model, hv("Model"))
        if lv is None:
            continue
        if gear and _norm_gear(hv("Gear Rate")) != _norm_gear(gear):
            continue
        if motor_no and hv("Motor Number") and motor_key(hv("Motor Number")) != motor_key(motor_no):
            continue
        mode = fanuc_param.closed_loop_mode(prm_format.param_value(doc, loop_number),
                                            int(loop_bit), int(full_when))
        if mode not in ("フル", "セミ"):
            continue
        vals = {num: v for (num, v, _jp, _en) in prm_format.iter_params(doc) if v != ""}
        found.append({"name": name, "level": lv, "mode": mode, "values": vals})
        if (lv == 0 and sum(1 for e in found if e["level"] == 0 and e["mode"] == "フル") >= cap
                and sum(1 for e in found if e["level"] == 0 and e["mode"] == "セミ") >= cap):
            break
    if not found:
        return None
    semi_any = next((e for e in found if e["mode"] == "セミ"), None)
    ref = {"name": semi_any["name"], "values": semi_any["values"]} if semi_any else None
    for lv in (0, 1, 2):
        fulls = [e for e in found if e["mode"] == "フル" and e["level"] <= lv][:cap]
        semis = [e for e in found if e["mode"] == "セミ" and e["level"] <= lv][:cap]
        if fulls and semis:
            pairs = [(f, s) for s in semis for f in fulls][:MAX_PAIRS]
            taken, left = vote_rules([pair_rules(f["values"], s["values"]) for f, s in pairs])
            return {"level": lv, "n_pairs": len(pairs), "fulls": [e["name"] for e in fulls],
                    "semis": [e["name"] for e in semis], "rules": taken, "unapplied": left,
                    "semi_ref": ref}
    return {"level": None, "n_pairs": 0, "fulls": [], "semis": [], "rules": [], "unapplied": [],
            "semi_ref": ref}


def _rule_text(r):
    if r[0] == "drop":
        return f"{r[1]} 書かない"
    if r[0] == "bit":
        return f"{r[1]} #{r[2]}={r[3]}"
    return f"{r[1]}={r[2]}"


def convert(values, *, rules=None, **kw):
    """フルクロ → セミクロ。根拠の順番: (a) 同じ型式のフル/セミの組（rules＝find_pair_rules の結果）
    → (b) 計算式 → (c) どちらも無ければ変換しない（理由1行）。戻り値は convert_formula と同じ形に
    source（根拠）・compare（計算式との比べ）を足したもの。"""
    calc = convert_formula(values, **kw)
    if calc["mode"] != "フル":
        return calc
    if not rules or not rules.get("rules"):
        return calc
    lookup = kw.get("lookup")
    if lookup is None and kw.get("raw") and kw.get("axis"):
        raw, axis = kw["raw"], kw["axis"]
        lookup = lambda n: fanuc_param.value_on_axis(raw, n, axis)
    ed = _Edit(values, lookup)
    loop_number, loop_bit = kw.get("loop_number", "1815"), int(kw.get("loop_bit", 1))
    full_when = int(kw.get("full_when", 1))
    ed.set_bit(int(str(loop_number)), loop_bit, not bool(full_when), f"#{loop_bit} を 0 に", force=True)
    n_pairs = rules["n_pairs"]
    drops = []
    for r, c in rules["rules"]:
        n = r[1]
        outside = n not in KNOWN_NUMBERS
        note = f"同じ型式の実物の組 {c}/{n_pairs} 組" + ("。確かめた番号の外なので要確認" if outside else "")
        if r[0] == "val":
            num = to_number(r[2])
            if num is not None and r[2].lstrip("+-").replace(".", "", 1).isdigit():
                ed.set_num(n, num, note, review=outside)
            else:
                ed.set_text(n, r[2], note, review=outside)
        elif r[0] == "bit":
            ed.set_bit(n, r[2], r[3] == "1", f"#{r[2]} を {r[3]} に（{note}）", review=outside)
        else:
            drops.append(n)
    ed.drop(drops, "書かない（同じ型式のセミクロ実物に行が無い）")
    # 組の差に入らなくても、MPI/MPR 系・2018 は書かない（実データでセミは 0 件）
    ed.drop([n for n in DROP_NUMBERS if n not in drops and _find_key(ed.new, n) is not None],
            "書かない（セミクロには要らない番号）")
    for r, c in rules.get("unapplied", []):
        ed.changes.append({"num": r[1], "name": ed._name(r[1]), "full": "", "semi": "（入れない）",
                           "note": f"組の差に出たが {c}/{n_pairs} 組だけで揃わない（{_rule_text(r)}）。要確認",
                           "review": True, "unapplied": True})
    out = dict(calc)
    out.update(values=ed.new, changes=ed.changes, skipped=ed.skipped, stop=[], source="組",
               basis=(f"同じ型式の組 {n_pairs}組から（フル {'・'.join(rules['fulls'][:3])} ／ "
                      f"セミ {'・'.join(rules['semis'][:3])}）"))
    # 計算式との比べ（食い違う番号を出す）
    calc_vals = calc["values"] if not calc["stop"] else None
    if calc_vals is None:
        out["compare"] = "計算式は使えません: " + calc["stop"][0]
    else:
        nums = sorted({c["num"] for c in ed.changes if not c.get("unapplied")}
                      | {c["num"] for c in calc["changes"]} | {s["num"] for s in calc["skipped"]})
        diff = []
        for n in nums:
            a, b = _find_key(ed.new, n), _find_key(calc_vals, n)
            if not _same_value(ed.new[a] if a is not None else None, calc_vals[b] if b is not None else None):
                diff.append(n)
        out["compare"] = (f"計算式でも作って比べました: {len(nums) - len(diff)}/{len(nums)} 番号が一致"
                          + (f"。計算と実物で違う: {'、'.join(str(n) for n in diff)}" if diff else ""))
    return out


def _norm_gear(s):
    f = parse_gear(s)
    return f if f is not None else _clean(s)


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
