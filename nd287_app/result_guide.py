# -*- coding: utf-8 -*-
"""「結果を読む」の中身（Qt なし）。

測り終わった自分の値で、(1) 結果のことばを1枚ずつ教える、(2) 同じ型式の「いつもの値」と比べる、
(3) 次にやることを出す、の3つ。設計: docs/もっと良くする_案と進み具合_20261126.md。

守ること:
- **新しい合否の決め方は作らない。** 外れたかどうかは、画面の表と同じ規格（呼ぶ側が渡す limits）と同じ比べ方
  （絶対値が規格を超えたら外れ）だけで決める。規格が無い項目は「外れ」とも「合格」とも言わない。
- 値は画面の表と同じ計算（analysis の pp / single / adjacent / slope）を使う。別の計算を足さない。
- 「いつもの値」は、保存してある同じ型式の結果を読むだけ（report.search_inspection のレコード）。
  5 件たまるまでは「いつもの値」と言わない。
- 「次にやること」は、(a) 前に同じ外れ方が直ったやり方の記録 (b) 登録した手引き だけを出す。
  アプリが原因を言い当てることはしない（専門の中身は利用者が登録する）。
"""

import csv
import datetime
import io
from pathlib import Path

import numpy as np

from .analysis import adjacent_peak, single_peak, slope_corrected_pp

SERIES = (("wheel_cw", "ホイール", "時計回り（CW）"), ("wheel_ccw", "ホイール", "反時計回り（CCW）"),
          ("worm_cw", "ウォーム", "時計回り（CW）"), ("worm_ccw", "ウォーム", "反時計回り（CCW）"))
SERIES_NAME = {k: f"{g}・{d}" for k, g, d in SERIES}
SERIES_SHORT = {"wheel_cw": "ホイール CW", "wheel_ccw": "ホイール CCW", "worm_cw": "ウォーム CW", "worm_ccw": "ウォーム CCW"}
GROUP = {k: ("wheel" if k.startswith("wheel") else "worm") for k, _, _ in SERIES}
GROUP_NAME = {"wheel": "ホイール", "worm": "ウォーム"}
METRICS = ("pp", "single", "adjacent", "slope")
METRIC_NAME = {"pp": "精度", "single": "単一誤差", "adjacent": "隣接誤差", "slope": "傾き", "backlash": "総合バックラッシ"}

USUAL_MIN_COUNT = 5          # これより少ない件数では「いつもの値」と言わない
FIX_FILE = "結果の対処記録.csv"
FIX_HEADER = ("日時", "型式", "機番", "外れた項目", "やったこと", "結果")
TIP_FILE = "結果の手引き.csv"
TIP_HEADER = ("項目", "見ること・やること", "登録した人", "日付")
FIXED = "直った"
# 選ぶだけで答えられる「やったこと」。専門の中身を増やしすぎない（一言は自由に書ける）
FIX_CHOICES = ("何も変えずに、もう一度測った", "測定器を付け直した", "製品を調整した")
# はじめから入れておく手引き（利用者が教えてくれたことだけ。docs/FOCAS_実機確認と要件 §6 ②）
DEFAULT_TIPS = (("傾き", "一周しても、元の所にもどっていません。まず、測定器の取り付けを見てください。"),)


def _f(v):
    return f"{v:.1f}"


def _sg(v):
    return f"{v:+.1f}"


def _ang(a):
    a = float(a)
    return f"{a:g}°"


# ------------------------------------------------------------------ 1. 自分の値で教える
def metric_values(dev):
    """1つの系列の (精度, 単一誤差, 隣接誤差, 傾き)。画面の表と同じ計算。"""
    d = np.asarray(dev, dtype=float)
    if len(d) < 2:
        return dict(pp=0.0, single=0.0, adjacent=0.0, slope=0.0)
    sp, ap = single_peak(d), adjacent_peak(d)
    return dict(pp=float(d.max() - d.min()), single=sp[2] if sp else 0.0,
                adjacent=ap[1] if ap else 0.0, slope=float(d[-1] - d[0]))


def is_over(value, limit):
    """規格から外れたか。規格が無ければ None（外れとも合格とも言わない）。表の赤い字と同じ比べ方。"""
    if limit is None or not limit:
        return None
    return abs(float(value)) > float(limit)


def _limit(limits, key, metric):
    return (limits or {}).get((GROUP[key], metric))


def _spec_line(over, limit):
    if over is None:
        return "この項目の規格は、画面に出ていません（合格とも外れとも言えません）。"
    return f"規格は {limit:g} 秒までなので、" + ("外れています。" if over else "規格の中です。")


def _lesson_pp(key, t, d, limit):
    i_max, i_min = int(np.argmax(d)), int(np.argmin(d))
    v = float(d[i_max] - d[i_min])
    over = is_over(v, limit)
    lines = [f"ずれが一番大きい所は {_ang(t[i_max])} の {_sg(d[i_max])} 秒、一番小さい所は {_ang(t[i_min])} の {_sg(d[i_min])} 秒です。",
             f"その差 {_f(v)} 秒が「精度」です。全体のばらつきの大きさを表します。"]
    if over is not None:
        lines.append(_spec_line(over, limit))
    return dict(metric="pp", series=key, value=v, limit=limit, over=over, lines=lines,
                marks=[(float(t[i_max]), float(d[i_max])), (float(t[i_min]), float(d[i_min]))], seg=[])


def _lesson_single(key, t, d, limit):
    sp = single_peak(d)
    if sp is None:
        return None
    a, b, v = sp
    over = is_over(v, limit)
    lines = [f"{_ang(t[a])} から {_ang(t[b])} へ動いたとき、ずれが {_sg(d[a])} 秒から {_sg(d[b])} 秒に変わりました。",
             f"この1歩ぶんの変化 {_f(v)} 秒が、「単一誤差」の一番大きい所です。", _spec_line(over, limit)]
    n_over = int(np.sum(np.abs(np.diff(d)) > limit)) if limit else 0
    if over:
        lines.append(f"規格を超えた1歩は、{len(d) - 1} 歩のうち {n_over} 歩です。")
    return dict(metric="single", series=key, value=v, limit=limit, over=over, lines=lines, n_over=n_over, n_steps=len(d) - 1,
                marks=[(float(t[a]), float(d[a])), (float(t[b]), float(d[b]))],
                seg=[(float(t[a]), float(d[a])), (float(t[b]), float(d[b]))])


def _lesson_adjacent(key, t, d, limit):
    ap = adjacent_peak(d)
    if ap is None:
        return None
    i, v = ap
    s1, s2 = float(d[i] - d[i - 1]), float(d[i + 1] - d[i])
    over = is_over(v, limit)
    lines = [f"{_ang(t[i - 1])}→{_ang(t[i])} の1歩で {_sg(s1)} 秒、次の {_ang(t[i])}→{_ang(t[i + 1])} の1歩で {_sg(s2)} 秒、ずれが変わりました。",
             f"この2歩の差 {_f(v)} 秒が「隣接誤差」です。{_ang(t[i])} の所が、でっぱり（または、へこみ）になっています。",
             _spec_line(over, limit)]
    pts = [(float(t[j]), float(d[j])) for j in (i - 1, i, i + 1)]
    n_over = int(np.sum(np.abs(np.diff(np.diff(d))) > limit)) if limit else 0
    return dict(metric="adjacent", series=key, value=v, limit=limit, over=over, lines=lines, n_over=n_over,
                marks=[pts[1]], seg=pts)


def _lesson_slope(key, t, d, limit, corrected):
    v = float(d[-1] - d[0])
    over = is_over(v, limit)
    lines = [f"最初（{_ang(t[0])}）のずれは {_sg(d[0])} 秒、最後（{_ang(t[-1])}）のずれは {_sg(d[-1])} 秒です。",
             f"最後 − 最初 ＝ {_sg(v)} 秒が「傾き」です。もどるはずの所に、どれだけもどらなかったかを表します。"]
    if corrected:
        lines.append("いまは「補正後」の表示なので、傾きは 0 になっています。「補正前」にすると、もとの値が見られます。")
    elif over is not None:
        lines.append(_spec_line(over, limit))
    return dict(metric="slope", series=key, value=v, limit=limit, over=over, lines=lines,
                marks=[(float(t[0]), float(d[0])), (float(t[-1]), float(d[-1]))],
                seg=[(float(t[0]), float(d[0])), (float(t[-1]), float(d[-1]))])


def plain_value(text):
    """表の値の書き方（9.9"・≧・≦・OK/NG）を、ことばに直す。数字は変えない。"""
    import re
    s = str(text or "")
    s = re.sub(r'≧\s*(-?[\d.]+)"?', r"\1 秒以上", s)
    s = re.sub(r'≦\s*(-?[\d.]+)"?', r"\1 秒以下", s)
    s = s.replace('"', " 秒").replace("NG", "→ 外れ").replace("OK", "→ 規格の中")
    return " ".join(s.split())


def backlash_lesson(rows):
    """総合バックラッシの1枚。rows は画面の表の行 [(項目, 値の文字)]（例「総合バックラッシ MIN」「-0.9"　規格 ≧9.9"　NG」）。
    値も合否も、表の文字をそのまま使う（ここでは計算しない）。"""
    rows = [(str(n), str(v)) for n, v in (rows or ()) if "総合バックラッシ" in str(n)]
    if not rows:
        return None
    has_spec = any("規格" in v for _n, v in rows)
    over = any("NG" in v for _n, v in rows) if has_spec else None
    lines = ["同じ角度に、時計回りで止めたときと、反時計回りで止めたときの、止まる位置の差です。",
             "グラフの2本の線のすきまが、それに当たります（総合の値は、ホイールとウォームを合わせて出しています）。"]
    for n, v in rows:
        lines.append(f"{n.replace('総合バックラッシ', '').strip() or n}: {plain_value(v)}")
    if not has_spec:
        lines.append("この項目の規格は、画面に出ていません（合格とも外れとも言えません）。")
    import re
    num = {}
    for n, v in rows:                    # 大きい字は MIN〜MAX（表の数字そのまま）。読めなければ出さない
        m = re.match(r'\s*([-+]?[\d.]+)"', v)
        for key in ("MIN", "MAX"):
            if m and key in n.upper():
                num[key] = m.group(1)
    value_text = f'{num["MIN"]} 〜 {num["MAX"]} 秒' if len(num) == 2 else ""
    return dict(metric="backlash", series="wheel_cw", value=None, limit=None, over=over, lines=lines, marks=[], seg=[],
                both=True, title="総合バックラッシ", kind="総合バックラッシ", others=[], value_text=value_text)


def facts(lesson):
    """外れ方について、見えること（事実だけ。原因は言わない）を1行で。無ければ ""。"""
    if not lesson or not lesson.get("over"):
        return ""
    if lesson["metric"] == "single":
        n = lesson.get("n_over", 0)
        total = lesson.get("n_steps", 0)
        if n <= 1:
            return f"規格を超えた1歩は、{total} 歩のうち 1 歩だけです（{_ang(lesson['marks'][0][0])} のあたり）。"
        return f"規格を超えた1歩は、{total} 歩のうち {n} 歩あります（1か所だけではありません）。"
    if lesson["metric"] == "adjacent":
        n = lesson.get("n_over", 0)
        if n <= 1:
            return f"規格を超えたでっぱりは 1 か所だけです（{_ang(lesson['marks'][0][0])}）。"
        return f"規格を超えたでっぱりは {n} か所あります（1か所だけではありません）。"
    if lesson["metric"] == "slope":
        return f"最初と最後のずれの差が {_sg(lesson['value'])} 秒です。"
    return ""


def lessons(devs, limits=None, corrected=False, backlash_rows=None):
    """自分の値で教える「1枚ずつ」の並び。

    devs: {系列: (指令角度の並び, ずれ[秒]の並び)}（画面の表と同じもの＝display_series_devs）。
    limits: {("wheel"|"worm", "pp"|"single"|"adjacent"|"slope"): 規格の上限[秒] または None}。
    項目ごとに、いちばん値の大きい系列を1枚にする（外れた系列があれば、外れた中でいちばん大きいもの）。
    並びは 外れた項目が先。同じなら 精度 → 単一誤差 → 隣接誤差 → 傾き、ホイール → ウォーム。
    """
    out = []
    for grp in ("wheel", "worm"):
        keys = [k for k, _, _ in SERIES if GROUP[k] == grp and k in (devs or {}) and len(devs[k][1]) >= 2]
        if not keys:
            continue
        for metric in METRICS:
            cands = []
            for k in keys:
                t = np.asarray(devs[k][0], dtype=float)
                d = np.asarray(devs[k][1], dtype=float)
                lim = _limit(limits, k, metric)
                if metric == "pp":
                    le = _lesson_pp(k, t, d, lim)
                elif metric == "single":
                    le = _lesson_single(k, t, d, lim)
                elif metric == "adjacent":
                    le = _lesson_adjacent(k, t, d, lim) if len(d) >= 3 else None
                else:
                    le = _lesson_slope(k, t, d, lim, corrected)
                if le is not None:
                    cands.append(le)
            if not cands:
                continue
            best = max(cands, key=lambda le: (bool(le["over"]), abs(le["value"])))
            best["others"] = [(SERIES_SHORT[c["series"]], c["value"], c["over"]) for c in cands if c is not best]
            best["title"] = f"{METRIC_NAME[metric]}　{SERIES_NAME[best['series']]}"
            best["kind"] = f"{GROUP_NAME[grp]} {METRIC_NAME[metric]}"
            out.append(best)
    bl = backlash_lesson(backlash_rows)
    if bl is not None and out:
        out.append(bl)
    order = {m: i for i, m in enumerate(METRICS + ("backlash",))}
    out.sort(key=lambda le: (0 if le["over"] else 1, 0 if GROUP[le["series"]] == "wheel" else 1, order[le["metric"]]))
    return out


def over_kinds(lesson_list, extra_items=()):
    """外れた項目の名前の並び（記録帳の鍵。例「ホイール 単一誤差」）。

    extra_items は画面の表の赤い字 [(項目名, 値)]。表にしか無い項目（総合バックラッシなど）を足す。
    """
    kinds = [le["kind"] for le in lesson_list if le["over"]]
    for name, v in extra_items or ():
        k = kind_of_item(name) or kind_of_item(v)
        if k and k not in kinds:
            kinds.append(k)
    return kinds


def kind_of_item(name):
    """表の項目名（例「ホイール CW 単一誤差」「総合バックラッシ MIN …」）→ 記録帳の鍵。分からなければ ""。"""
    s = str(name or "")
    if "バックラッシ" in s:
        return "総合バックラッシ"
    grp = "ホイール" if "ホイール" in s else ("ウォーム" if "ウォーム" in s else "")
    for label in ("単一誤差", "隣接誤差", "傾き"):
        if label in s:
            return f"{grp} {label}".strip()
    if "精度" in s and grp:
        return f"{grp} 精度"
    if "再現" in s:
        return "再現性"
    return ""


def same_place(devs, metric, pitch_tol=1.01):
    """ホイールの CW と CCW で、いちばん外れた所が同じ角度かどうかを1行で（事実だけ。原因は言わない）。"""
    cw, ccw = (devs or {}).get("wheel_cw"), (devs or {}).get("wheel_ccw")
    if not cw or not ccw or len(cw[1]) < 3 or len(ccw[1]) < 3:
        return ""

    def where(t, d):
        d = np.asarray(d, dtype=float)
        if metric == "single":
            p = single_peak(d)
            return float(t[p[1]]) if p else None
        p = adjacent_peak(d)
        return float(t[p[0]]) if p else None

    a, b = where(*cw), where(*ccw)
    if a is None or b is None:
        return ""
    pitch = abs(float(cw[0][1]) - float(cw[0][0])) or 1.0
    if abs(a - b) <= pitch * pitch_tol:
        return f"時計回りと反時計回りで、ほぼ同じ所（{_ang(a)} と {_ang(b)}）が一番大きくなっています。"
    return f"時計回り（{_ang(a)}）と反時計回り（{_ang(b)}）で、一番大きい所が違います。"


# ------------------------------------------------------------------ 2. いつもの値と比べる
PAST_KEY = {"wheel_cw": "ホイール CW", "wheel_ccw": "ホイール CCW", "worm_cw": "ウォーム CW", "worm_ccw": "ウォーム CCW"}


def usual(values):
    """過去の値の並び → いつもの値。5 件たまるまでは range を出さない。

    返り値: dict(n, lo, hi, vmin, vmax, med)。lo〜hi は「10 件のうち 8 件が入る幅」（10%〜90%）。"""
    vals = sorted(float(v) for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return dict(n=0)
    out = dict(n=n, vmin=vals[0], vmax=vals[-1], med=float(np.median(vals)))
    if n >= USUAL_MIN_COUNT:
        out["lo"] = float(np.percentile(vals, 10))
        out["hi"] = float(np.percentile(vals, 90))
    return out


def compare(value, u, name="値"):
    """今回の値をいつもの値と比べた (段階, 1行の文)。段階: few / same / high / highest / low。"""
    n = (u or {}).get("n", 0)
    if n < USUAL_MIN_COUNT:
        return "few", (f"同じ型式の保存した結果は、まだ {n} 件です。{USUAL_MIN_COUNT} 件たまると、いつもの値と比べられます。"
                       if n else "同じ型式の保存した結果が、まだありません。保存するほど、いつもの値と比べられるようになります。")
    v = abs(float(value))
    span = f"{_f(u['lo'])}〜{_f(u['hi'])} 秒"
    head = f"この型式のいつもの{name}は {span}（保存した {n} 件のうち、8割がこの中）。今回は {_f(v)} 秒で、"
    if v > u["vmax"]:
        return "highest", head + f"これまでで一番大きいです（これまでの最大 {_f(u['vmax'])} 秒）。"
    if v > u["hi"]:
        return "high", head + "いつもより大きいです。"
    if v < u["lo"]:
        return "low", head + "いつもより小さいです。"
    return "same", head + "いつもどおりです。"


def past_values(records, key, *, exclude_machine=""):
    """保存した結果のレコード（report.search_inspection）から、metrics[key] の値を集める。
    いま測っている機番のファイルは入れない（自分と比べない）。傾きは大きさ（絶対値）で比べる。"""
    ex = str(exclude_machine or "").strip().upper()
    out = []
    for r in records or ():
        if ex and _same_machine(r.get("機番"), ex):
            continue
        v = (r.get("metrics") or {}).get(key)
        if v is None:
            continue
        out.append(abs(float(v)) if "傾き" in key else float(v))
    return out


def _same_machine(file_no, machine):
    """保存した名前（機番＋末尾の記号。例 12345KRC・12345-X1）が、その機番のものか。123456 は 12345 と別。"""
    s = str(file_no or "").strip().upper()
    m = str(machine or "").strip().upper()
    if not m or not s.startswith(m):
        return False
    rest = s[len(m):]
    return not rest[:1].isdigit()


def usual_rows(raw_devs, records, *, exclude_machine=""):
    """「いつもの値と比べる」の行: [dict(name, value, stage, text, u)]。

    保存した結果に入っているのは 精度（傾き補正後と補正前の小さいほう）と 傾き なので、今回の値も同じ計算で出す
    （raw_devs ＝ 補正前のずれ。current_series_devs）。"""
    rows = []
    for k, _, _ in SERIES:
        if k not in (raw_devs or {}) or len(raw_devs[k][1]) < 2:
            continue
        d = np.asarray(raw_devs[k][1], dtype=float)
        for label, val, name in ((f"{PAST_KEY[k]} 精度PP", slope_corrected_pp(d), "精度"),
                                 (f"{PAST_KEY[k]} 傾き", abs(float(d[-1] - d[0])), "傾き")):
            u = usual(past_values(records, label, exclude_machine=exclude_machine))
            stage, text = compare(val, u, name)
            rows.append(dict(name=f"{SERIES_SHORT[k]} {name}", value=float(val), stage=stage, text=text, u=u))
    return rows


def usual_summary(rows):
    """比べた行 → 画面の上に出す1行。"""
    if not rows:
        return ""
    if all(r["stage"] == "few" for r in rows):
        return rows[0]["text"]
    high = [r["name"] for r in rows if r["stage"] in ("high", "highest")]
    n = max(r["u"].get("n", 0) for r in rows)
    if not high:
        return f"同じ型式の保存した結果 {n} 件と比べて、どの値も、いつもどおりか、いつもより小さいです。"
    return f"同じ型式の保存した結果 {n} 件と比べて、いつもより大きい値があります: " + "、".join(high) + "。"


# ------------------------------------------------------------------ 3. 次にやること（記録帳と手引き）
def _read_csv(path):
    p = Path(path)
    if not p.exists():
        return []
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(p, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
        except OSError:
            return []
    return []


def _append_csv(path, header, row):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with open(p, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow([row.get(h, "") for h in header])


def load_fixes(path):
    return _read_csv(path)


def add_fix(path, *, model, machine, kinds, did, result=FIXED, now=None):
    """記録帳に1行足す（外れた項目・やったこと・結果）。"""
    now = now or datetime.datetime.now()
    _append_csv(path, FIX_HEADER, {"日時": now.strftime("%Y/%m/%d %H:%M"), "型式": model, "機番": machine,
                                   "外れた項目": "／".join(kinds), "やったこと": str(did).strip(), "結果": result})


def fix_stats(rows, kind):
    """その外れ方が、前に何で直ったか: [(やったこと, 直った回数)] を回数の多い順で。合わせて全部の回数。"""
    count = {}
    for r in rows or ():
        if r.get("結果") != FIXED:
            continue
        if kind not in str(r.get("外れた項目") or "").split("／"):
            continue
        did = str(r.get("やったこと") or "").strip()
        if did:
            count[did] = count.get(did, 0) + 1
    ranked = sorted(count.items(), key=lambda x: (-x[1], x[0]))
    return ranked, sum(count.values())


def load_tips(path):
    """登録した手引き [(項目, 文)]。ファイルが無ければ、はじめから入れてある分だけ。"""
    rows = _read_csv(path)
    tips = [(str(r.get("項目") or "").strip(), str(r.get("見ること・やること") or "").strip()) for r in rows]
    tips = [(k, t) for k, t in tips if k and t]
    have = {(k, t) for k, t in tips}
    return [x for x in DEFAULT_TIPS if x not in have] + tips


def add_tip(path, kind, text, who="", now=None):
    now = now or datetime.datetime.now()
    _append_csv(path, TIP_HEADER, {"項目": kind, "見ること・やること": str(text).strip(), "登録した人": who,
                                   "日付": now.strftime("%Y/%m/%d")})


def tips_for(tips, kind):
    """その外れ方に当たる手引き。項目は「傾き」のように短く書いてもよい（含まれていれば当たる）。"""
    return [t for k, t in tips or () if k and (k == kind or k in kind)]


def next_steps(kinds, fixes, tips, lesson_list=()):
    """外れた項目ごとの「次にやること」: [dict(kind, fact, before=[(やったこと, 回数)], total, tips=[文])]。"""
    out = []
    by_kind = {le["kind"]: le for le in lesson_list or ()}
    for k in kinds:
        ranked, total = fix_stats(fixes, k)
        out.append(dict(kind=k, fact=facts(by_kind.get(k)), before=ranked, total=total, tips=tips_for(tips, k)))
    return out


def step_lines(step):
    """次にやること 1項目ぶんを、画面に出す行にする。"""
    lines = []
    if step.get("fact"):
        lines.append(f"見えること: {step['fact']}")
    for did, n in step["before"][:3]:
        lines.append(f"前はこれで直りました: {did}（{step['total']} 回のうち {n} 回）")
    for t in step["tips"]:
        lines.append(f"手引き: {t}")
    if not step["before"] and not step["tips"]:
        lines.append("この外れ方の記録と手引きは、まだありません。直ったら、やったことを残すと、次の人に出ます。")
    return lines


class Tracker:
    """「外れた → 次に測ったら入った」を見つける係（機番ごと。アプリを開いている間だけ覚える）。

    observe(機番, 外れた項目) を、測り終えるたびに呼ぶ。前に外れていて、今回それが無くなった項目があれば返す
    （→ 画面が1回だけ「何をして直りましたか」と聞く）。"""

    def __init__(self):
        self._last = {}

    def observe(self, machine, kinds):
        key = base_machine(machine)
        if not key:
            return []
        before = self._last.get(key, [])
        now = list(kinds or [])
        self._last[key] = now
        return [k for k in before if k not in now]

    def pending(self, machine):
        return list(self._last.get(base_machine(machine), []))


def base_machine(machine):
    """機番から、測り直しの印（-X1 など）を外した形。大文字にそろえる。"""
    s = str(machine or "").strip().upper()
    i = s.find("-X")
    return s[:i] if i > 0 else s


def csv_text(header, rows):
    """試験・書き出し用: 見出しと行を CSV の文字にする。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([r.get(h, "") for h in header])
    return buf.getvalue()
