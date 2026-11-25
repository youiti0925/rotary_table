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
TILT_COLUMNS = ["型式", "測定の種類", "スケール名", "開始角度", "終了角度", "刻み", "ウォーム刻み", "ウォーム範囲", "ウォーム開始",
                "ブロック数", "回数", "再現開始", "再現終了", "メモ"]

HELP_LINES = [
    "測定条件を、測定の種類ごとに登録する表です。アプリはこの表を読みます（書き換えません）。",
    "測定の種類: セミ・モーター／セミ・駆動装置／フル・スケール付き。空欄は「種類を決めていない（今までの登録）」です。",
    "同じ型式で、種類だけ変えた行を最大3つ書けます。",
    "セミ・モーター: 歯数を書き、ホイール刻みを空にすると、360÷歯数÷10（歯1枚を10等分）で入ります。",
    "セミ・駆動装置: 歯1枚飛ばしでよいので、測る刻みの値を自分で書きます。",
    "フル・スケール付き: 使っているスケールの名前をスケール名に書き、刻みの値を書きます。",
    "再現性（ブロック数・回数・再現開始・再現終了）も種類ごとの行に書けます。3種類それぞれ別の値にできます。空なら、その種類の再現性は作りません（ほかの種類の値は使いません）。",
    "精度の合否（回転のみ）: MINA・MINB・MAXA・MAXB（温度で変わる精度の上限下限の式）と傾きH・傾きW（傾きの規格）。合否判定.csv と同じ意味です。種類ごとに別の値にできます。空欄なら、今までの合否判定.csv の型式の値を使います。",
    "はじめてモードの初期の行は、今までの登録（ユーザー条件CSV・測定条件.csv）を、種類を空にして写したものです。",
]


def _s(v):
    return "" if v is None else str(v).strip()


def _num(v, default=None):
    t = _s(v)
    if not t:
        return default
    try:
        return float(t)
    except ValueError:
        return default


def normalize_kind(text):
    """表記ゆれ（中点・全角など）を吸収。合わなければ ''。"""
    t = _s(text).replace("･", "・").replace("・", "").replace(" ", "").replace("　", "")
    for k in KINDS:
        if k.replace("・", "") == t:
            return k
    return ""


def _read_sheet(ws, columns):
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [_s(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        rec = {h: _s(r[i]) if i < len(r) else "" for i, h in enumerate(header) if h}
        if rec.get("型式"):
            out.append(rec)
    return out


def load(path):
    """表を読む。戻り値 {"rotary": [行…], "tilt": [行…], "problems": [文…]}。無い・読めないときは空（黙って直さない: problems に理由）。"""
    res = {"rotary": [], "tilt": [], "plan": [], "problems": []}
    p = Path(path)
    if not p.exists():
        return res
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    except Exception as e:                     # 開けない: 理由を返す（今までの登録で続けられるように）
        res["problems"].append(f"{p.name} を開けません: {e}")
        return res
    try:
        for key, sheet in (("rotary", SHEET_ROTARY), ("tilt", SHEET_TILT)):
            if sheet in wb.sheetnames:
                res[key] = _read_sheet(wb[sheet], None)
        if SHEET_PLAN in wb.sheetnames:
            res["plan"] = _read_sheet(wb[SHEET_PLAN], None)
    finally:
        wb.close()
    seen = set()
    for key in ("rotary", "tilt"):
        for rec in res[key]:
            raw = rec.get("測定の種類", "")
            kind = normalize_kind(raw)
            if raw and not kind:
                res["problems"].append(f"{key}: 型式 {rec['型式']} の測定の種類「{raw}」が分かりません（空として扱います）")
            rec["測定の種類"] = kind
            ident = (key, rec["型式"].upper(), kind, rec.get("スケール名", ""))
            if ident in seen:
                res["problems"].append(f"{key}: 型式 {rec['型式']}・{kind or '種類なし'} が重なっています（上の行を使います）")
            seen.add(ident)
    return res


def find(table, model, tilt, kind="", scale=""):
    """型式＋種類で1行を引く。無ければ「種類が空」の行。どちらも無ければ None。
    フル・スケール付きで複数あるときは scale（スケール名）で選ぶ。無ければ最初の行。"""
    rows = table.get("tilt" if tilt else "rotary") or []
    m = _s(model).upper()
    cands = [m]
    if "," in m:
        base, rest = (x.strip() for x in m.split(",", 1))
        cands += [base + rest, base]
    for c in cands:
        hit = [r for r in rows if r["型式"].upper() == c]
        if not hit:
            continue
        want = [r for r in hit if r["測定の種類"] == normalize_kind(kind)] if kind else []
        if want:
            if scale:
                s = [r for r in want if r.get("スケール名", "") == scale]
                if s:
                    return s[0]
            return want[0]
        blank = [r for r in hit if not r["測定の種類"]]
        if blank:
            return blank[0]
    return None


def kinds_of(table, model, tilt):
    """この型式で登録されている種類の一覧（画面で選ばせる用。種類が空の行は含めない）。"""
    rows = table.get("tilt" if tilt else "rotary") or []
    m = _s(model).upper()
    out = []
    for r in rows:
        if r["型式"].upper() == m and r["測定の種類"] and r["測定の種類"] not in out:
            out.append(r["測定の種類"])
    return out


def params(rec, tilt):
    """1行 → アプリの設定値（user_condition_params と同じ形）。セミ・モーターで刻みが空なら 360÷歯数÷10。"""
    pitch_key = "刻み" if tilt else "ホイール刻み"
    pitch = _num(rec.get(pitch_key))
    note = ""
    if pitch is None and rec.get("測定の種類") == "セミ・モーター":
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
    out["_ok"] = pitch is not None
    return out


JUDGE_COLS = (("MINA", "min_a"), ("MINB", "min_b"), ("MAXA", "max_a"), ("MAXB", "max_b"), ("傾きH", "slope_h"), ("傾きW", "slope_w"))


def judgement(rec, base=None):
    """種類の行の精度の合否（規格）を、合否判定.csv と同じ形（min_a…slope_w）で返す。
    行の欄が空のところは base（合否判定.csv の型式の値）を使う（引き継ぎ）。どちらも無ければ None。"""
    out = dict(base or {})
    got = False
    for col, key in JUDGE_COLS:
        v = _num((rec or {}).get(col))
        if v is not None:
            out[key] = v
            got = True
    if not out:
        return None
    if got:
        out["_from_kind"] = True
    return out


def repeat(rec):
    """1行の再現性。{"blocks_n","repeats","start","end"} か None（ブロック数・回数が空なら作らない）。"""
    n, reps = _num(rec.get("ブロック数")), _num(rec.get("回数"))
    if not n or not reps or n < 1 or reps < 1:
        return None
    return {"blocks_n": int(n), "repeats": int(reps),
            "start": _num(rec.get("再現開始"), 0.0), "end": _num(rec.get("再現終了"), 360.0)}


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
    m = _s(model).upper()
    rows = [r for r in (table.get("plan") or []) if r["型式"].upper() == m]
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


def save_model(path, model, rotary_by_kind=None, tilt_by_kind=None, plan=None):
    """1つの型式の、種類別の条件と測る予定を、まとめて1回で保存する（アプリの登録画面も、Excel に直接書くのも、同じファイル）。

    rotary_by_kind / tilt_by_kind: {種類: {列名: 値}}（値が全部空の種類は書かない＝その型式のその種類の行を消す）。
    plan: {"フルで測る","セミで測る","セミの種類","メモ"} か None（None なら予定は触らない）。
    その型式の「種類が空」の行（今までの登録の写し）と、ほかの型式の行・説明は、そのまま残す。
    保存前に、元のファイルを「.bak」に1世代残す。戻り値: 書いた行数。
    """
    import shutil
    import openpyxl
    p = Path(path)
    if not p.exists():
        build_template(p)
    shutil.copy2(str(p), str(p) + ".bak")
    wb = openpyxl.load_workbook(str(p))
    m = _s(model).upper()
    written = 0
    for sheet, cols, by_kind in ((SHEET_ROTARY, ROTARY_COLUMNS, rotary_by_kind), (SHEET_TILT, TILT_COLUMNS, tilt_by_kind)):
        if by_kind is None:
            continue
        ws = wb[sheet] if sheet in wb.sheetnames else wb.create_sheet(sheet)
        if ws.max_row < 1 or not [c.value for c in ws[1] if c.value]:
            ws.append(cols)
        header = [_s(c.value) for c in ws[1]]
        for col in cols:                                          # 以前の形式の表: 足りない列（合否など）を右に足す
            if col not in header:
                ws.cell(1, len(header) + 1).value = col
                header.append(col)
        for r in range(ws.max_row, 1, -1):                        # 下から消す（行番号がずれない）
            vals = {header[i]: _s(c.value) for i, c in enumerate(ws[r]) if i < len(header)}
            if vals.get("型式", "").upper() == m and normalize_kind(vals.get("測定の種類", "")):
                ws.delete_rows(r)
        for kind in KINDS:
            rec = dict(by_kind.get(kind) or {})
            if not any(_s(v) for k, v in rec.items() if k not in ("型式", "測定の種類")):
                continue
            rec["型式"], rec["測定の種類"] = _s(model), kind
            ws.append([rec.get(h, "") for h in header])
            written += 1
    if plan is not None:
        ws = wb[SHEET_PLAN] if SHEET_PLAN in wb.sheetnames else wb.create_sheet(SHEET_PLAN)
        if ws.max_row < 1 or not [c.value for c in ws[1] if c.value]:
            ws.append(PLAN_COLUMNS)
        header = [_s(c.value) for c in ws[1]]
        if "回転/傾斜" not in header:                              # 以前の形式（軸の列なし）: 列を足す。既存の行は「空＝両方」のまま
            ws.insert_cols(2)
            ws.cell(1, 2).value = "回転/傾斜"
            header = [_s(c.value) for c in ws[1]]
        for r in range(ws.max_row, 1, -1):
            if _s(ws.cell(r, 1).value).upper() == m:
                ws.delete_rows(r)
        # plan: {"回転": {...}, "傾斜": {...}}（軸ごと）か、{"フルで測る":…}（両方に同じ＝軸の列を空で1行）
        by_axis = plan if ("回転" in plan or "傾斜" in plan) else {"": plan}
        for axis in ("", "回転", "傾斜"):
            rec = dict(by_axis.get(axis) or {})
            if any(_s(rec.get(k)) for k in ("フルで測る", "セミで測る")):
                rec["型式"], rec["回転/傾斜"] = _s(model), axis
                ws.append([rec.get(h, "") for h in header])
                written += 1
    wb.save(str(p))
    return written
