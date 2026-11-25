# -*- coding: utf-8 -*-
"""測定条件の「測定の種類」別の登録（専用の Excel。Qt なし）。

同じ型式でも、測定条件は最大3種類ある（2026-11-25 利用者）:
  セミ・モーター      … 歯車の歯数ぶんの等分。歯1枚を10等分する
  セミ・駆動装置      … 歯1枚飛ばしでよい（ルール。値は表に書く）
  フル・スケール付き  … 使っているスケールで決まる（スケール名の欄に書く）
ファイル: マスタ/測定条件_種類別.xlsx（シート「回転」「傾斜」「説明」）。アプリは読むだけ。
引く順: 型式＋種類が合う行 → 無ければ「種類が空」の行（今までの登録をそのまま写したもの）→ 無ければ None
（呼ぶ側は、今までの引き方 = ユーザー登録CSV → 測定条件.csv に進む）。黙って別の種類の値を使わない。
"""
from pathlib import Path

KINDS = ("セミ・モーター", "セミ・駆動装置", "フル・スケール付き")
FILE_NAME = "測定条件_種類別.xlsx"
SHEET_ROTARY, SHEET_TILT, SHEET_HELP, SHEET_PLAN = "回転", "傾斜", "説明", "測る予定"
PLAN_COLUMNS = ["型式", "回転/傾斜", "フルで測る", "セミで測る", "セミの種類", "メモ"]
PLAN_CHOICES = ("", "分割だけ", "再現性だけ", "両方")

ROTARY_COLUMNS = ["型式", "測定の種類", "スケール名", "歯数", "ホイール刻み", "ウォーム刻み", "ウォーム範囲", "ウォーム開始",
                  "ブロック数", "回数", "再現開始", "再現終了",
                  "MINA", "MINB", "MAXA", "MAXB", "傾きH", "傾きW", "メモ"]
# 傾斜の規格で今アプリが使うのは傾きH・傾きW だけ。MINA〜MAXB は登録画面とひな形に出さない（書いてあっても読めるが、判定には使わない）
TILT_COLUMNS = ["型式", "測定の種類", "スケール名", "開始角度", "終了角度", "刻み", "ウォーム刻み", "ウォーム範囲", "ウォーム開始",
                "ブロック数", "回数", "再現開始", "再現終了", "傾きH", "傾きW", "メモ"]
TILT_HIDDEN_COLUMNS = ["MINA", "MINB", "MAXA", "MAXB"]

HELP_LINES = [
    "測定条件を、測定の種類ごとに登録する表です。アプリはこの表を読みます（書き換えません）。",
    "測定の種類: セミ・モーター／セミ・駆動装置／フル・スケール付き。空欄は「種類を決めていない（今までの登録）」です。",
    "同じ型式で、種類だけ変えた行を最大3つ書けます。",
    "セミ・モーター: 歯数を書き、ホイール刻みを空にすると、360÷歯数÷10（歯1枚を10等分）で入ります。",
    "セミ・駆動装置: 歯1枚飛ばしでよいので、測る刻みの値を自分で書きます。",
    "フル・スケール付き: 使っているスケールの名前をスケール名に書き、刻みの値を書きます。",
    "再現性（ブロック数・回数・再現開始・再現終了）も種類ごとの行に書けます。3種類それぞれ別の値にできます。空なら、その種類の再現性は作りません（ほかの種類の値は使いません）。",
    "精度の合否（回転のみ）: MINA・MINB・MAXA・MAXB（温度で変わる精度の上限下限の式）と傾きH・傾きW（傾きの規格）。合否判定.csv と同じ意味です。種類ごとに別の値にできます。空欄なら、今までの合否判定.csv の型式の値を使います。",
    "傾斜シートの合否の欄は 傾きH・傾きW（傾きの規格）だけです。傾斜と回転の規格は全く違うので、回転の値・合否判定.csv は傾斜に引き継ぎません。空欄なら傾斜の傾きの合否は出さず「傾斜の規格が未登録」と出ます。傾斜のバックラッシ・割出精度・再現性の合否は出しません。",
    "規格の引き継ぎは、欄ごとではなくまとまりごとです（バックラッシの4つ＝MINA・MINB・MAXA・MAXB、傾きの2つ＝傾きH・傾きW）。まとまりが全部空のときだけ、種類なしの行 → （回転だけ）合否判定.csv の順に使います。数字でない字が入っているまとまりは使いません。",
    "はじめてモードの初期の行は、今までの登録（ユーザー条件CSV・測定条件.csv）を、種類を空にして写したものです。",
]


def _s(v):
    return "" if v is None else str(v).strip()


def _nfkc(t):
    import unicodedata
    return unicodedata.normalize("NFKC", _s(t))


def norm_model(text):
    """型式の比べ方を1つにそろえる: 全角→半角・小文字→大文字・空白を取る・長音やハイフンの種類を '-' にそろえる。"""
    t = _nfkc(text).upper()
    for ch in ("ー", "－", "‐", "―", "−", "–", "—", "ｰ"):
        t = t.replace(ch, "-")
    return "".join(t.split())


def _num(v, default=None):
    """数字として読む（全角・前後の空白は読む）。空・読めない字は default（読めない字は load が知らせ、行に _bad を付ける）。"""
    t = _nfkc(v)
    if not t:
        return default
    try:
        return float(t)
    except ValueError:
        return default


def normalize_kind(text):
    """表記ゆれ（中点・全角など）を吸収。合わなければ ''。"""
    t = _nfkc(text).replace("･", "・").replace("・", "").replace(" ", "").replace("　", "")
    for k in KINDS:
        if k.replace("・", "") == t:
            return k
    return ""


TEXT_COLS = ("型式", "測定の種類", "スケール名", "メモ")
ROW_CELL = "同じ表の中で"
KNOWN_COLS = {c: c for c in dict.fromkeys(ROTARY_COLUMNS + TILT_COLUMNS + TILT_HIDDEN_COLUMNS + PLAN_COLUMNS)}


def _canon_header(h):
    """見出しの打ち間違い（空白・全角半角の違い）を知っている名前にそろえる。無ければ元の字。"""
    t = "".join(_nfkc(h).split())
    for k in KNOWN_COLS:
        if "".join(_nfkc(k).split()) == t:
            return k
    return _s(h)


def _read_sheet(ws, columns, ws_f=None, label="", issues=None):
    """シート → 行の辞書の並び。見出しは知っている名前にそろえる。数字の欄に数字でない字・式だけで値の無い欄は、
    行の "_bad"（{欄: 元の字}）に入れて知らせる（黙って空欄にしない）。ws_f は式を見るための同じシート（data_only=False）。"""
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    frows = list(ws_f.iter_rows(values_only=True)) if ws_f is not None else []
    header = [_canon_header(h) for h in rows[0]]
    if issues is not None:
        seen_h = set()
        for h, raw in zip(header, rows[0]):
            if h and h not in KNOWN_COLS and h not in seen_h:
                seen_h.add(h)
                issues.append(("", f"{label}: 知らない列「{h}」（使いません。保存しても残します）"))
    out = []
    for i, r in enumerate(rows[1:], start=1):
        rec = {h: _s(r[j]) if j < len(r) else "" for j, h in enumerate(header) if h}
        if not rec.get("型式"):
            continue
        bad = {}
        for j, h in enumerate(header):
            if not h or h in TEXT_COLS or h not in KNOWN_COLS or j >= len(r):
                continue
            v = r[j]
            fv = frows[i][j] if i < len(frows) and j < len(frows[i]) else None
            if (v is None or _s(v) == "") and isinstance(fv, str) and fv.strip().startswith("="):
                bad[h] = "式のみ（Excelで一度開いて保存してください）"
            elif _s(v) and _num(v) is None and h not in ("回転/傾斜", "フルで測る", "セミで測る", "セミの種類"):
                bad[h] = _s(v)
        if bad:
            rec["_bad"] = bad
            for h, t in bad.items():
                issues.append((norm_model(rec["型式"]), f"{label}: 型式 {rec['型式']}・{normalize_kind(rec.get('測定の種類', '')) or '種類なし'} の「{h}」が数字として読めません（{t}）。この欄を含むまとまりは使いません"))
        out.append(rec)
    return out


def load(path):
    """表を読む。戻り値 {"rotary","tilt","plan","problems":[文…],"issues":[(型式(そろえた形),文)…]}。
    無い・読めないときは空（黙って直さない: problems に理由）。"""
    res = {"rotary": [], "tilt": [], "plan": [], "problems": [], "issues": []}
    p = Path(path)
    if not p.exists():
        return res
    issues = res["issues"]
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        wbf = openpyxl.load_workbook(str(p), read_only=True, data_only=False)
    except Exception as e:                     # 開けない: 理由を返す（今までの登録で続けられるように）
        res["problems"].append(f"{p.name} を開けません: {e}")
        return res
    try:
        for key, sheet in (("rotary", SHEET_ROTARY), ("tilt", SHEET_TILT)):
            if sheet in wb.sheetnames:
                res[key] = _read_sheet(wb[sheet], None, wbf[sheet], sheet, issues)
        if SHEET_PLAN in wb.sheetnames:
            res["plan"] = _read_sheet(wb[SHEET_PLAN], None, wbf[SHEET_PLAN], SHEET_PLAN, issues)
    finally:
        wb.close()
        wbf.close()
    seen = set()
    for key in ("rotary", "tilt"):
        for rec in res[key]:
            raw = rec.get("測定の種類", "")
            kind = normalize_kind(raw)
            if raw and not kind:
                issues.append((norm_model(rec["型式"]), f"{key}: 型式 {rec['型式']} の測定の種類「{raw}」が分かりません（空として扱います）"))
            rec["測定の種類"] = kind
            ident = (key, norm_model(rec["型式"]), kind, rec.get("スケール名", ""))
            if ident in seen:
                issues.append((norm_model(rec["型式"]), f"{key}: 型式 {rec['型式']}・{kind or '種類なし'} が重なっています（上の行を使います）"))
            seen.add(ident)
    res["problems"] = res["problems"] + [t for _m, t in issues]
    return res


def problems_for(table, model=None):
    """表の気になる所の文。model を渡すと、その型式に関係するもの（と、型式に関係しない表全体のもの）だけ。"""
    if model is None:
        return list((table or {}).get("problems") or [])
    m = norm_model(model)
    iss = (table or {}).get("issues")
    if iss is None:
        return list((table or {}).get("problems") or [])
    extra = [t for t in (table or {}).get("problems", []) if t not in {x[1] for x in iss}]
    return extra + [t for mm, t in iss if mm in ("", m)]


def _candidates(model):
    """型式を引く形の順: ①そのまま ②カンマを詰めた形 ③カンマの前だけ ④末尾の英字（クローズ記号）を外した形。"""
    import re
    m = norm_model(model)
    out = [(m, "")]
    if "," in m:
        base, rest = (x.strip() for x in m.split(",", 1))
        out += [(base + rest, "カンマを詰めた形"), (base, "カンマの前")]
    for c, _v in list(out):
        t = re.sub(r"(\d)[A-Z]+$", r"\1", c)
        if t != c and all(t != x for x, _ in out):
            out.append((t, "末尾の英字（クローズ記号）を外した形"))
    return out


def hit_rows(table, model, tilt):
    """その型式に当たる行の並びと、どの形で当たったか。(rows, via)。via は「そのまま」のとき ""。"""
    rows = table.get("tilt" if tilt else "rotary") or []
    for c, via in _candidates(model):
        hit = [r for r in rows if norm_model(r["型式"]) == c]
        if hit:
            return hit, (f"{c} の登録を使っています（{via}）" if via else "")
    return [], ""


def find_via(table, model, tilt, kind="", scale=""):
    """型式＋種類で1行を引く。(行, 種類の行か: bool, via)。無ければ「種類が空」の行（種類の行は False）。どちらも無ければ (None, False, "")。
    フル・スケール付きで複数あるときは scale（スケール名）で選ぶ。無ければ最初の行。"""
    hit, via = hit_rows(table, model, tilt)
    if not hit:
        return None, False, ""
    want = [r for r in hit if r["測定の種類"] == normalize_kind(kind)] if kind else []
    if want:
        if scale:
            sel = [r for r in want if r.get("スケール名", "") == scale]
            if sel:
                return sel[0], True, via
        return want[0], True, via
    blank = [r for r in hit if not r["測定の種類"]]
    if blank:
        return blank[0], False, via
    return None, False, via


def find(table, model, tilt, kind="", scale=""):
    return find_via(table, model, tilt, kind, scale)[0]


def kinds_of(table, model, tilt):
    """この型式で登録されている種類の一覧（画面で選ばせる用。種類が空の行は含めない）。"""
    hit, _via = hit_rows(table, model, tilt)
    out = []
    for r in hit:
        if r["測定の種類"] and r["測定の種類"] not in out:
            out.append(r["測定の種類"])
    return out


def params(rec, tilt):
    """1行 → アプリの設定値（user_condition_params と同じ形）。セミ・モーターで刻みが空なら 360÷歯数÷10。"""
    pitch_key = "刻み" if tilt else "ホイール刻み"
    bad = {c: t for c, t in (rec.get("_bad") or {}).items()
           if c in ("歯数", "刻み", "ホイール刻み", "ウォーム刻み", "ウォーム範囲", "ウォーム開始", "開始角度", "終了角度")}
    pitch = None if bad else _num(rec.get(pitch_key))
    note = ""
    if pitch is None and not bad and rec.get("測定の種類") == "セミ・モーター":
        teeth = _num(rec.get("歯数"))
        if teeth and teeth > 0:
            pitch = 360.0 / teeth / 10.0
            note = f"歯数 {teeth:g} → 歯1枚を10等分（{pitch:g}°）"
    out = {"wheel_pitch": pitch if pitch is not None else 10.0,
           "worm_pitch": _num(rec.get("ウォーム刻み"), 0.5),
           "worm_range": _num(rec.get("ウォーム範囲"), 5.0),
           "worm_start": _num(rec.get("ウォーム開始"), 0.0)}
    if tilt:
        out["wheel_start"] = _num(rec.get("開始角度"), -30.0)
        out["wheel_end"] = _num(rec.get("終了角度"), 110.0)
    out["_note"] = note
    out["_ok"] = pitch is not None and not bad
    out["_bad"] = bad
    return out


JUDGE_COLS = (("MINA", "min_a"), ("MINB", "min_b"), ("MAXA", "max_a"), ("MAXB", "max_b"), ("傾きH", "slope_h"), ("傾きW", "slope_w"))
# 引き継ぎは欄ごとではなく、まとまりごと（1つの規格の中に2つの表の値を混ぜない）
GROUPS = {"backlash": (("MINA", "min_a"), ("MINB", "min_b"), ("MAXA", "max_a"), ("MAXB", "max_b")),
          "slope": (("傾きH", "slope_h"), ("傾きW", "slope_w"))}
GROUP_LABEL = {"backlash": "バックラッシの規格", "slope": "傾きの規格"}


def group_values(rec, group):
    """1行の1つのまとまり → (状態, 値の辞書, 理由)。状態: empty（全部空）/ ok / bad（数字でない字）/ incomplete（書き足りない）。"""
    cols = GROUPS[group]
    bad = {c: t for c, t in ((rec or {}).get("_bad") or {}).items() if c in [c0 for c0, _k in cols]}
    if bad:
        return "bad", {}, "「" + "・".join(bad) + "」が数字として読めません"
    vals = {k: _num((rec or {}).get(c)) for c, k in cols}
    if all(v is None for v in vals.values()):
        return "empty", {}, ""
    if group == "backlash":
        if vals["max_b"] is None:
            return "incomplete", {}, "上限（MAXB）が書いてありません"
        return "ok", {k: (0.0 if v is None else v) for k, v in vals.items()}, ""
    return "ok", {k: v for k, v in vals.items() if v is not None}, ""


def resolve_judgement(table, model, tilt, kind="", scale="", base=None):
    """精度の合否の規格を、まとまりごとに決める。規格を取る順（回転・傾斜とも。傾斜はシートをまたがない）:
    ①選んだ種類の行 ②同じシートの種類なしの行 ③回転だけ: base（合否判定.csv の型式の値）④無ければ判定しない。
    数字として読めない字・書き足りないまとまりは使わず（前の表にも落とさない）、理由を返す。
    傾斜で判定に使うのは傾き（傾きH・傾きW）だけ。傾斜のバックラッシは、書いてあっても使わず知らせる。
    戻り値 {"judge": 規格の辞書か None, "source": {まとまり: 出どころ}, "problems": [文], "via": 型式の当たり方}"""
    hit, via = hit_rows(table, model, tilt)
    k = normalize_kind(kind) if kind else ""
    kind_row = next((r for r in hit if k and r["測定の種類"] == k), None)
    blank_row = next((r for r in hit if not r["測定の種類"]), None)
    out, src, problems = {}, {}, []
    for g in ("backlash", "slope"):
        if tilt and g == "backlash":
            if any(group_values(r, g)[0] != "empty" for r in (kind_row, blank_row) if r is not None):
                problems.append("傾斜のバックラッシの規格は、まだ判定に使っていません")
            continue
        done = False
        for row, name in ((kind_row, "この種類の登録"), (blank_row, "種類なしの登録")):
            if row is None:
                continue
            st, vals, why = group_values(row, g)
            if st in ("bad", "incomplete"):
                problems.append(f"{GROUP_LABEL[g]}（{name}）: {why}。この規格は使いません")
                src[g] = "使いません（" + why + "）"
                done = True
                break
            if st == "ok":
                out.update(vals)
                src[g] = name
                done = True
                break
        if not done and not tilt and base:
            vals = {key: base[key] for _c, key in GROUPS[g] if base.get(key) is not None}
            if vals:
                out.update(vals)
                src[g] = "前の表（合否判定.csv）"
                done = True
        if not done:
            src[g] = "未登録"
    return {"judge": out or None, "source": src, "problems": problems, "via": via}


def judgement(rec, base=None):
    """1行の規格（まとまりごと。行のまとまりが全部空のときだけ base のまとまりを使う）。resolve_judgement の1行版。"""
    out = {}
    for g in GROUPS:
        st, vals, _w = group_values(rec, g)
        if st == "ok":
            out.update(vals)
        elif st == "empty" and base:
            out.update({k: base[k] for _c, k in GROUPS[g] if base.get(k) is not None})
    return out or None


def repeat(rec):
    """1行の再現性。{"blocks_n","repeats","start","end"} か None（ブロック数・回数が空なら作らない）。"""
    n, reps = _num(rec.get("ブロック数")), _num(rec.get("回数"))
    if not n or not reps or n < 1 or reps < 1:
        return None
    return {"blocks_n": int(n), "repeats": int(reps),
            "start": _num(rec.get("再現開始"), 0.0), "end": _num(rec.get("再現終了"), 360.0)}


def _add_validations(wb):
    """選ぶ欄に、Excel の「一覧から選ぶ」（入力規則）を付ける（手で打つ表記ゆれを減らす。すでに付いている列には足さない）。"""
    from openpyxl.worksheet.datavalidation import DataValidation
    spec = {SHEET_ROTARY: {"測定の種類": ",".join(KINDS)}, SHEET_TILT: {"測定の種類": ",".join(KINDS)},
            SHEET_PLAN: {"回転/傾斜": "回転,傾斜", "フルで測る": ",".join(c for c in PLAN_CHOICES if c),
                         "セミで測る": ",".join(c for c in PLAN_CHOICES if c),
                         "セミの種類": "セミ・モーター,セミ・駆動装置"}}
    from openpyxl.utils import get_column_letter
    for sheet, cols in spec.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        header = [_s(c.value) for c in ws[1]]
        have = " ".join(str(r.sqref) for r in ws.data_validations.dataValidation)
        for col, items in cols.items():
            if col not in header:
                continue
            L = get_column_letter(header.index(col) + 1)
            if f"{L}2" in have:
                continue
            dv = DataValidation(type="list", formula1=f'"{items}"', allow_blank=True, showErrorMessage=False)
            dv.add(f"{L}2:{L}1000")
            ws.add_data_validation(dv)


def build_template(path, rotary_rows=(), tilt_rows=()):
    """表（空の見出し＋説明。rotary_rows / tilt_rows に辞書があれば写す）を書く。既にあるファイルは上書きしない（FileExistsError）。"""
    p = Path(path)
    if p.exists():
        raise FileExistsError(str(p))
    import openpyxl
    wb = openpyxl.Workbook()
    for ws_name, cols, rows in ((SHEET_ROTARY, ROTARY_COLUMNS, rotary_rows), (SHEET_TILT, TILT_COLUMNS, tilt_rows)):
        ws = wb.active if ws_name == SHEET_ROTARY else wb.create_sheet(ws_name)
        ws.title = ws_name
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c, "") for c in cols])
        ws.freeze_panes = "B2"
    ps = wb.create_sheet(SHEET_PLAN)
    ps.append(PLAN_COLUMNS)
    hs = wb.create_sheet(SHEET_HELP)
    for line in HELP_LINES:
        hs.append([line])
    hs.column_dimensions["A"].width = 100
    _add_validations(wb)
    wb.save(str(p))


def rows_from_existing(masters):
    """今までの登録（ユーザー条件・測定条件.csv）を、種類を空にして写す行。masters は masters.load_all 相当の辞書。"""
    rot, tilt = [], []
    seen_r = set()
    for key, rec in (masters.get("user_rotary") or {}).items():
        mr = str(rec.get("型式", key)).upper()
        if mr in seen_r:
            continue
        seen_r.add(mr)
        rot.append({"型式": rec.get("型式", key), "測定の種類": "", "ホイール刻み": rec.get("ホイール刻み", ""),
                    "ウォーム刻み": rec.get("ウォーム刻み", ""), "ウォーム範囲": rec.get("ウォーム範囲", ""),
                    "ウォーム開始": rec.get("ウォーム開始", ""), "メモ": "今までの登録（ユーザー回転条件）"})
    have = {r["型式"].upper() for r in rot}
    from .masters import condition_params
    for key, cond in (masters.get("conditions") or {}).items():
        model = cond.get("model", "")
        if model.upper() in have:
            continue
        p = condition_params(cond)
        if p.get("wheel_pitch"):
            have.add(model.upper())                      # 同じ型式の条件が複数あっても1行だけ（重なりの警告を作らない）
            rot.append({"型式": model, "測定の種類": "", "ホイール刻み": round(p["wheel_pitch"], 6),
                        "ウォーム刻み": round(p.get("worm_pitch", 0) or 0, 6), "ウォーム範囲": round(p.get("worm_range", 0) or 0, 6),
                        "ウォーム開始": 0, "メモ": "今までの登録（測定条件.csv）"})
    seen_t = set()
    for key, rec in (masters.get("user_tilt") or {}).items():
        mt = str(rec.get("型式", key)).upper()
        if mt in seen_t:
            continue
        seen_t.add(mt)
        tilt.append({"型式": rec.get("型式", key), "測定の種類": "", "開始角度": rec.get("開始角度", ""),
                     "終了角度": rec.get("終了角度", ""), "刻み": rec.get("刻み", ""), "ウォーム刻み": rec.get("ウォーム刻み", ""),
                     "ウォーム範囲": rec.get("ウォーム範囲", ""), "ウォーム開始": rec.get("ウォーム開始", ""),
                     "メモ": "今までの登録（ユーザー傾斜条件）"})
    from .masters import find_entry
    for r in rot:                                          # 今までの合否判定.csv の規格を、種類なしの行へ写す（種類の行は空なら引き継ぐ）
        j = find_entry(masters.get("judgement") or {}, str(r.get("型式", "")))
        if j:
            for col, key in JUDGE_COLS:
                if j.get(key) is not None and not r.get(col):
                    r[col] = j[key]
    return rot, tilt


def find_plan(table, model, tilt=None):
    """測る予定 1行 {"フルで測る","セミで測る","セミの種類"}。無ければ None（予定なし＝今までどおり1回測る）。

    予定は 型式×回転/傾斜 で1行ずつ（2軸製品は回転と傾斜で予定が違うことがある）。
    tilt=True（傾斜）で、傾斜の行が無い・空のときは、回転の行（無ければ「回転/傾斜」が空の行）と同じ。
    tilt=False（回転）は、回転の行 → 「回転/傾斜」が空の行（以前の形式の行）。tilt=None は、どの行でも最初の1つ。"""
    m = norm_model(model)
    rows = [r for r in (table.get("plan") or []) if norm_model(r["型式"]) == m]
    if not rows:
        return None

    def pick(axis):
        for r in rows:
            if _s(r.get("回転/傾斜")) == axis and (_s(r.get("フルで測る")) or _s(r.get("セミで測る"))):
                return r
        return None
    if tilt is None:
        return rows[0]
    first = pick("傾斜") if tilt else pick("回転")
    return first or (pick("回転") if tilt else None) or pick("")


BACKUP_DIR = "控え"
BACKUP_KEEP = 10


def _backup(p):
    """保存前の控えを「控え/測定条件_種類別_日付_時分.xlsx」に残す（10個まで。古いものから消す）。"""
    import shutil
    import time
    d = p.parent / BACKUP_DIR
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = d / f"{p.stem}_{stamp}{p.suffix}"
    n = 1
    while dst.exists():                                       # 同じ秒に続けて保存しても、控えを上書きしない
        n += 1
        dst = d / f"{p.stem}_{stamp}_{n}{p.suffix}"
    shutil.copy2(str(p), str(dst))
    olds = sorted(d.glob(f"{p.stem}_*{p.suffix}"), key=lambda x: x.stat().st_mtime_ns)
    for old in olds[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return dst


def save_model(path, model, rotary_by_kind=None, tilt_by_kind=None, plan=None, confirm_delete=None):
    """1つの型式の、種類別の条件と測る予定を、まとめて1回で保存する（アプリの登録画面も、Excel に直接書くのも、同じファイル）。

    rotary_by_kind / tilt_by_kind: {種類: {列名: 値}}。同じ 型式＋種類（＋スケール名）の行があれば、その行の画面にある欄だけをその場で書き換える
    （人が並べた順・色・足した列・式は残す）。無ければ末尾に足す。値が全部空の種類は、その型式のその種類の行を消す
    （消す前に confirm_delete(文の並び) を呼び、False が返れば何も書かずに止める）。
    plan: {"フルで測る","セミで測る","セミの種類","メモ"} か None（None なら予定は触らない）。
    その型式の「種類が空」の行（今までの登録の写し）と、ほかの型式の行・説明は、そのまま残す。
    保存前に、元のファイルを「控え/」に日付つきで残す（10個まで）。Excel で開いたままなら、日本語で理由を言って止める。
    戻り値: 書いた行数（足した行＋書き換えた行）。
    """
    import openpyxl
    p = Path(path)
    if not p.exists():
        build_template(p)
    try:
        _backup(p)
    except PermissionError:
        raise RuntimeError("Excel で開いています。閉じてからもう一度保存してください")
    wb = openpyxl.load_workbook(str(p))
    m = norm_model(model)
    written = 0
    deletes = []
    for sheet, cols, by_kind in ((SHEET_ROTARY, ROTARY_COLUMNS, rotary_by_kind), (SHEET_TILT, TILT_COLUMNS, tilt_by_kind)):
        if by_kind is None:
            continue
        ws = wb[sheet] if sheet in wb.sheetnames else wb.create_sheet(sheet)
        if ws.max_row < 1 or not [c.value for c in ws[1] if c.value]:
            ws.append(cols)
        header = [_canon_header(c.value) for c in ws[1]]
        for i, h in enumerate(header):                            # 見出しの打ち間違いは知っている名前に直す（人が足した列はそのまま）
            if h and h != _s(ws.cell(1, i + 1).value):
                ws.cell(1, i + 1).value = h
        for col in cols:                                          # 以前の形式の表: 足りない列（合否など）を右に足す
            if col not in header:
                ws.cell(1, len(header) + 1).value = col
                header.append(col)

        def row_info(r):
            vals = {header[i]: ws.cell(r, i + 1).value for i in range(len(header)) if header[i]}
            return norm_model(vals.get("型式")), normalize_kind(vals.get("測定の種類")), _s(vals.get("スケール名"))
        for kind in KINDS:
            rec = dict(by_kind.get(kind) or {})
            empty = not any(_s(v) for k, v in rec.items() if k not in ("型式", "測定の種類"))
            rows = [r for r in range(2, ws.max_row + 1) if row_info(r)[:2] == (m, kind)]
            if empty:
                if rows:
                    deletes.append((sheet, kind, rows))
                continue
            want_scale = _s(rec.get("スケール名"))
            hit = next((r for r in rows if row_info(r)[2] == want_scale), None) or (rows[0] if rows else None)
            if hit is None:
                rec["型式"], rec["測定の種類"] = _s(model), kind
                ws.append([rec.get(h, "") for h in header])
            else:
                for i, h in enumerate(header):
                    if not h or h in ("型式", "測定の種類") or h not in rec:
                        continue
                    cell = ws.cell(hit, i + 1)
                    new = _s(rec.get(h))
                    if new == "" and isinstance(cell.value, str) and cell.value.strip().startswith("="):
                        continue                                  # 式は消さない（画面は式の値を持たないので空に見える）
                    if _s(cell.value) != new and not (isinstance(cell.value, (int, float)) and _num(new) == float(cell.value)):
                        cell.value = (_num(new) if _num(new) is not None and h not in TEXT_COLS else new)
            written += 1
    if plan is not None:
        ws = wb[SHEET_PLAN] if SHEET_PLAN in wb.sheetnames else wb.create_sheet(SHEET_PLAN)
        if ws.max_row < 1 or not [c.value for c in ws[1] if c.value]:
            ws.append(PLAN_COLUMNS)
        header = [_canon_header(c.value) for c in ws[1]]
        if "回転/傾斜" not in header:                              # 以前の形式（軸の列なし）: 列を足す。既存の行は「空＝両方」のまま
            ws.insert_cols(2)
            ws.cell(1, 2).value = "回転/傾斜"
            header = [_canon_header(c.value) for c in ws[1]]
        col = {h: i + 1 for i, h in enumerate(header) if h}
        # plan: {"回転": {...}, "傾斜": {...}}（軸ごと）か、{"フルで測る":…}（両方に同じ＝軸の列を空で1行。回転・傾斜の行は無くす）
        by_axis = plan if ("回転" in plan or "傾斜" in plan) else {"": plan}
        for axis in ("", "回転", "傾斜"):
            rec = dict(by_axis.get(axis) or {})
            rows = [r for r in range(2, ws.max_row + 1)
                    if norm_model(ws.cell(r, col["型式"]).value) == m and _s(ws.cell(r, col["回転/傾斜"]).value) == axis]
            if any(_s(rec.get(k)) for k in ("フルで測る", "セミで測る")):
                if rows:
                    for h, v in rec.items():                      # 同じ型式×軸の行は、その場で書き換える
                        if h in col and h not in ("型式", "回転/傾斜"):
                            ws.cell(rows[0], col[h]).value = _s(v)
                else:
                    rec["型式"], rec["回転/傾斜"] = _s(model), axis
                    ws.append([rec.get(h, "") for h in header])
                written += 1
            elif rows:
                deletes.append((SHEET_PLAN, axis, rows))
    if deletes:
        names = [(f"測る予定シートの {'回転' if k == '回転' else '傾斜' if k == '傾斜' else '（回転・傾斜とも）'} の行 {len(rs)} 行を消します"
                  if sh == SHEET_PLAN else f"{sh}シートの {KR_LABEL.get(k, k)} の行 {len(rs)} 行を消します") for sh, k, rs in deletes]
        if confirm_delete is not None and not confirm_delete(names):
            raise RuntimeError("行を消さないため、保存を止めました")
        per_sheet = {}
        for sh, _k, rs in deletes:
            per_sheet.setdefault(sh, []).extend(rs)
        for sh, rs in per_sheet.items():
            for r in sorted(set(rs), reverse=True):               # 下から消す（行番号がずれない）
                wb[sh].delete_rows(r)
    _add_validations(wb)
    try:
        wb.save(str(p))
    except PermissionError:
        raise RuntimeError("Excel で開いています。閉じてからもう一度保存してください")
    return written


KR_LABEL = {"セミ・モーター": "モーター", "セミ・駆動装置": "駆動装置", "フル・スケール付き": "フルクロ"}
