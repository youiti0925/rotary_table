# -*- coding: utf-8 -*-
"""はじめてモードの「測っているあいだ」と「こういう結果でした」に出す中身（2026-11-26。Qt なし）。

利用者（2026-11-26）:「測定グラフも画面に出ない」「情報量が少なすぎる」「もっと相手に現状を伝えられる」
「終わった後は、こういう結果でしたって出る」。

ここは**読むだけ**。値も、外れ（赤い字）も、測定の画面（通常の画面）が出しているものをそのまま使う。
新しい合否の決め方・新しい計算は作らない（測定の画面とはじめてモードで、違うことを言わないため）。
"""
from __future__ import annotations

# 系列の色は、測定の画面のグラフと同じ（gui.update_legends）
SERIES_COLOR = {"wheel_cw": "#1f77b4", "wheel_ccw": "#d62728", "worm_cw": "#2ca02c", "worm_ccw": "#ff7f0e",
                "cw": "#1f77b4", "ccw": "#d62728"}
SERIES_SHORT = {"wheel_cw": "ホイール CW", "wheel_ccw": "ホイール CCW", "worm_cw": "ウォーム CW", "worm_ccw": "ウォーム CCW",
                "cw": "再現性 CW", "ccw": "再現性 CCW"}
RED = "#dc2626"          # 測定の画面の表の「規格から外れた」字の色（gui._flow_verdict と同じ見方）
GREEN = "#16a34a"

# 表の見出し → ことばのカード（easy_words.CARDS の名前）と、ひとことの意味（カードの1行目と同じ中身）
HEAD_CARD = {"精度PP": "pp", "傾き": "tilt", "単一誤差": "single", "隣接誤差": "adjacent",
             "総合バックラッシ": "backlash", "主点精度": "main", "再現性": "repeat", "CW範囲": "repeat", "CCW範囲": "repeat"}
HEAD_MEANING = {
    "精度PP": "ずれの一番大きい所と、一番小さい所の差",
    "傾き": "一周して、もどるはずの位置にもどらなかったぶん",
    "単一誤差": "となりの場所へ移ったときの、1歩ぶんのずれの最大",
    "隣接誤差": "となり合う2歩のずれが、逆向きに重なった「でっぱり」の最大",
    "総合バックラッシ": "同じ角度での、反時計回りと時計回りのずれの差",
    "主点精度": "等分した主な点だけで見た、ずれの差",
    "再現性": "同じ所へ何度も止めたときの、止まる位置のばらつき",
    "CW範囲": "時計回りで止めたときの、ばらつきの幅",
    "CCW範囲": "反時計回りで止めたときの、ばらつきの幅",
}


def dms(deg):
    """角度 → 12°34'56.7\"（表示器と同じ読み方）。"""
    sign = "-" if deg < 0 else ""
    t = round(abs(float(deg)) * 36000.0)
    d, rest = divmod(t, 36000)
    m, s10 = divmod(rest, 600)
    return f"{sign}{int(d)}°{int(m):02d}'{s10 / 10.0:04.1f}\""


def dev_sec(target, measured):
    """指令角度と測った角度の差[秒]（360°のまたぎをならす。live_guide と同じ）。"""
    return ((float(measured) - float(target) + 180.0) % 360.0 - 180.0) * 3600.0


# ------------------------------------------------------------------ いまの様子（どの手順でも上に出す帯）
def status_chips(c, st, ctx=None):
    """[(見出し, 中身, 調子)]。調子: ok / wait / stop / plain。読めていないものは「読めていません」と言う（推測で埋めない）。"""
    c, st, ctx = c or {}, st or {}, ctx or {}
    out = []
    model, serial = str(c.get("model") or "").strip(), str(c.get("serial") or "").strip()
    if model or serial:
        out.append(("製品", "　".join(x for x in (model, ("機番 " + serial) if serial else "") if x), "plain"))
    else:
        out.append(("製品", "まだ決まっていません", "wait"))
    mode = str(c.get("mode") or "").strip()
    kind = str(c.get("kind") or "").strip()
    if mode:
        out.append(("測り方", mode + (f"（{kind}）" if kind else ""), "plain"))
    # 機械
    unit = str(ctx.get("unit") or "").strip()
    axes = [(k, a) for k, a in (ctx.get("axes") or []) if a]
    head = (f"{unit}号機 " if unit else "")
    if not st.get("online"):
        out.append(("機械", head + "見えていません", "wait"))
    else:
        from . import easy_flow
        parts = []
        if st.get("aut") is not None:
            parts.append(easy_flow.aut_name(st["aut"]))
        if st.get("emergency") == 1:
            parts.append("非常停止中")
        if st.get("run") == easy_flow.RUN_RUNNING:
            parts.append("運転中")
        tone = "stop" if st.get("emergency") == 1 else "ok"
        out.append(("機械", head + "つながっています" + ("・" + "・".join(parts) if parts else ""), tone))
    if axes:
        out.append(("軸", "　".join(f"{k}→{a}軸" if k else f"{a}軸" for k, a in axes), "plain"))
    # 表示器
    if c.get("nd_dummy"):
        out.append(("表示器", "練習用（にせ物の値）", "wait"))
    elif c.get("nd_ok") is True:
        out.append(("表示器", "つながっています", "ok"))
    elif c.get("nd_ok") is False:
        out.append(("表示器", "つながっていません", "stop"))
    n = len(st.get("alarms") or [])
    if n:
        out.append(("アラーム", f"{n} 件", "stop"))
    return out


# ------------------------------------------------------------------ 測っているあいだ
def parts(seq):
    """取込の箱を (分割の箱, 再現性の箱) に分ける。無い方は None。分割＋再現（合体）は両方。"""
    if seq is None:
        return None, None
    if hasattr(seq, "division") and hasattr(seq, "repeat"):
        return seq.division, seq.repeat
    if hasattr(seq, "points") and hasattr(seq, "repeats"):
        return None, seq
    return seq, None


def last_value(seq):
    """いま入った1点。{"key","label","target","measured","dev"}。まだ無ければ None。
    分割は data[系列]=(指令, 測った値)、再現性は data[(向き, ブロック)]=[値…]（sequence.py）。"""
    div, rep = parts(seq)
    try:
        if rep is not None and rep.idx > 0:
            dirn, block, _r, target, _s = rep.steps[rep.idx - 1]
            measured = rep.data[(dirn, block)][-1]
            key = dirn
        elif div is not None and div.idx > 0:
            key, target, _d = div.steps[div.idx - 1][:3]
            measured = div.data[key][1][-1]
        else:
            return None
    except Exception:
        return None
    return dict(key=key, label=SERIES_SHORT.get(key, str(key)), target=float(target), measured=float(measured),
                dev=dev_sec(target, measured))


def last_value_text(v):
    """いま入った値の2行（上: 指令と測った値、下: ずれ）。"""
    if not v:
        return ("まだ値が入っていません", "")
    return (f"指令 {v['target']:g}° → 測った値 {dms(v['measured'])}", f"ずれ {v['dev']:+.1f} 秒")


def series_progress(seq):
    """[(名前, 入った数, 要る数, いま測っている所か)]。"""
    try:
        counts = list(seq.counts())
    except Exception:
        return []
    names = set()
    try:
        cur = seq.current()
        key = str(cur[0]) if cur else ""
        d = key.rsplit("_", 1)[-1].upper()                      # cw / ccw
        if key in SERIES_SHORT:
            names = {SERIES_SHORT[key], d, "再現 " + d, "再現性 " + d} if key in ("cw", "ccw") else {SERIES_SHORT[key]}
        elif key:
            names = {"再現 " + d, "再現性 " + d, d}
    except Exception:
        names = set()
    names = {n.replace(" ", "") for n in names}
    out = []
    for label, cur_n, req in counts:
        now = cur_n < req and str(label).replace(" ", "") in names
        out.append((str(label), int(cur_n), int(req), bool(now)))
    if sum(1 for x in out if x[3]) > 1:               # 「CW」が2行に当たったとき（分割＋再現）: 再現の行を残す
        hit = [i for i, x in enumerate(out) if x[3]]
        for i in hit[:-1]:
            out[i] = out[i][:3] + (False,)
    if out and not any(x[3] for x in out):            # 名前で当てられなかったとき: まだ入り切っていない最初の行
        for i, x in enumerate(out):
            if x[1] < x[2]:
                out[i] = (x[0], x[1], x[2], True)
                break
    return out


def x_range(seq, prefix="wheel"):
    """その系列の指令角度の (小, 大)。グラフの横の幅を、はじめから測る範囲にしておく（線が伸びていくのが見える）。
    prefix="rep" は再現性の止める角度。"""
    div, rep = parts(seq)
    try:
        if prefix == "rep":
            xs = [float(p) for p in rep.points] if rep is not None else []
        else:
            xs = [float(s[1]) for s in div.steps if str(s[0]).startswith(prefix)] if div is not None else []
    except Exception:
        xs = []
    if not xs:
        return None
    lo, hi = min(xs), max(xs)
    return (lo, hi) if hi > lo else (lo - 1.0, lo + 1.0)


def repeat_points(seq, rep_unwrap=None):
    """再現性の点 {"cw": ([角度…], [ずれ秒…]), "ccw": (…)}（測定の画面のグラフと同じ出し方）。"""
    _div, rep = parts(seq)
    out = {"cw": ([], []), "ccw": ([], [])}
    if rep is None:
        return out
    for (dirn, i), vals in list(rep.data.items()):
        angle = rep.points[i]
        for v in vals:
            out[dirn][0].append(angle)
            out[dirn][1].append(dev_sec(angle, v))
    return out


# ------------------------------------------------------------------ こういう結果でした
def read_tables(host):
    """測定の画面の右の表（精度・単一/隣接・バックラッシ）を、そのまま読む。
    [{"headers":[…], "rows":[[(文字, 色) か None, …], …]}]。見えていない表は読まない（_flow_verdict と同じ）。"""
    out = []
    for name in ("table_series", "table_series2", "table_misc"):
        t = getattr(host, name, None)
        if t is None:
            continue
        try:
            if not t.isVisibleTo(host):
                continue
            heads = [t.horizontalHeaderItem(j).text() if t.horizontalHeaderItem(j) else "" for j in range(t.columnCount())]
            rows = []
            for i in range(t.rowCount()):
                row = []
                for j in range(t.columnCount()):
                    it = t.item(i, j)
                    row.append(None if it is None else (it.text(), it.foreground().color().name().lower()))
                rows.append(row)
            out.append({"headers": heads, "rows": rows})
        except Exception:
            continue
    return out


def merge_tables(tables):
    """同じ「1列目の名前」の行を横につなげて、1枚の表にする（上下2枚に分かれた表を、はじめての人が1回で読めるように）。

    戻り値 {"headers":[…], "rows":[[(文字, 色)…]…], "notes":[(文字, 色)…]}。
    - 値が2列以上ある表（系列／ブロックの表）だけをつなげる。1列目の見出しが違う表は、別の表にせず notes へ回さない（そのまま足す）。
    - 1つの欄だけの行（「総合バックラッシ MIN …」「― 主点精度 ―」など）は notes に入れる（文字と色はそのまま）。
    """
    headers, order, cells, notes = [], [], {}, []
    first_head = None
    for t in tables or []:
        heads = list(t.get("headers") or [])
        grid = [r for r in t.get("rows") or [] if r and sum(1 for x in r if x is not None) >= 2
                and not (len(r) >= 3 and r[2] is None and str(r[0][0]).startswith("主点"))]
        singles = [r for r in t.get("rows") or [] if r not in grid]
        if grid:
            if first_head is None:
                first_head = heads[0] if heads else ""
            if (heads[0] if heads else "") == first_head:
                for h in heads[1:]:
                    if h not in headers:
                        headers.append(h)
                for r in grid:
                    name = r[0][0] if r[0] else ""
                    if name not in cells:
                        cells[name] = {}
                        order.append(name)
                    for h, cell in zip(heads[1:], r[1:]):
                        if cell is not None:
                            cells[name][h] = cell
            else:                                    # 1列目の見出しが違う表: 行をそのまま1行の文にする
                for r in grid:
                    txt = "　".join(f"{h} {c[0]}" if (k and h) else c[0] for k, (h, c) in enumerate(zip(heads, r)) if c)
                    color = RED if any(c and c[1] == RED for c in r) else "#000000"
                    notes.append((txt, color))
        for r in singles:
            got = [c for c in r if c is not None]
            if not got:
                continue
            txt = "　".join(c[0] for c in got if c[0])
            if not txt.strip("―— "):
                continue
            color = next((c[1] for c in got if c[1] in (RED, GREEN)), "#000000")
            notes.append((txt, color))
    rows = [[(name, "#000000")] + [cells[name].get(h, ("", "#000000")) for h in headers] for name in order]
    return {"headers": [first_head or ""] + headers, "rows": rows, "notes": notes}


def verdict(ng, has_spec, ng_items=()):
    """(調子, 大きい字, 小さい字)。数え方は測定の画面と同じ（赤い字の数）。規格が画面に無いときは「合格」と言わない。"""
    if ng:
        kinds = []
        for n, _v in ng_items or ():
            for h in HEAD_MEANING:
                if h in str(n) and h not in kinds:
                    kinds.append(h)
        sub = ("外れた項目: " + "・".join(kinds)) if kinds else ""
        return ("stop", f"規格から外れた所が {ng} か所あります", sub)
    if has_spec:
        return ("ok", "ぜんぶ規格の中です", "規格と比べた所は、どれも入っています。")
    return ("plain", "測り終わりました（規格は登録されていません）", "規格が無いので、アプリは合否を言いません。値を見てください。")


def plan_lines(seq_counts, usual_text=""):
    """これから測る中身の行（測る前に見せる）。seq_counts=[(名前, 0, 要る数)]。"""
    rows = [f"{label}: {req} 点" for label, _c, req in seq_counts or []]
    total = sum(req for _l, _c, req in seq_counts or [])
    out = []
    if rows:
        out.append("　".join(rows))
        out.append(f"全部で {total} 点" + (f"（{usual_text}）" if usual_text else ""))
    return out
