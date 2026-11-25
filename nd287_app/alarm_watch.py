# -*- coding: utf-8 -*-
"""アラームを「読む → 意味とやること → 直ったか確かめる → 記録する」ための部品（Qt 非依存）。

機械からは読むだけ。PC からアラームを消す（リセットする）ことはしない。
一覧に無いアラームを、有るように見せない（lookup の known=False）。

設計: docs/はじめてモード_全体設計_20261125.md §2-5、docs/FOCAS_実機確認と要件_20261122.md §6 ③。
"""

import csv
import datetime
import io
from pathlib import Path

from . import fanuc_alarms, focas_helper, focas_ops

RECORD_FILE = "アラーム記録.csv"
RECORD_HEADER = ("日時", "機番", "CNC ID", "番号", "機械の文字", "やったこと", "結果")
RESULT_FIXED = "直った"

NORMAL_NOTE = "これは異常ではありません（設定を書けるようにしている印）"
NEAR_LABEL = "近い番号（種類が違います）"
UNKNOWN_NOTE = "アプリの一覧にありません"


def normalize(alarms):
    """機械から読んだ一覧を、同じ形の辞書の並びにそろえる（no/type/axis/text/code）。
    読めない要素・番号も文字も無い要素は捨てる。code が無ければ種類と番号から作る。"""
    out = []
    for a in alarms or []:
        if not isinstance(a, dict):
            continue
        typ, no = a.get("type"), a.get("no")
        code = str(a.get("code") or "").strip()
        if not code and typ is not None and no is not None:
            try:
                code = focas_helper.alarm_code(int(typ), int(no))
            except (TypeError, ValueError):
                code = ""
        text = str(a.get("text") or "").strip()
        if not code and not text:
            continue
        out.append({"no": no, "type": typ, "axis": a.get("axis", 0) or 0, "text": text, "code": code})
    return out


def is_normal(alarm, allowed_codes):
    """異常ではないアラームか（許しているアラーム＝既定 SW0100 など）。
    allowed_codes は focas_ops.allowed_codes(settings) の結果（無ければ既定）。"""
    allowed = tuple(allowed_codes) if allowed_codes else focas_ops.DEFAULT_ALLOWED_ALARMS
    code = str((alarm or {}).get("code") or "").strip().upper()
    return code in {str(c).strip().upper() for c in allowed}


def abnormal(alarms, allowed_codes):
    """異常なものだけ（許しているアラームを除く）。"""
    return [a for a in normalize(alarms) if not is_normal(a, allowed_codes)]


def lookup(alarms_db, code, machine_text=""):
    """アプリの一覧から意味を引く。
    戻り値 {"exact": [番号がぴったり同じ], "near": [数字だけ同じ別の種類], "known": bool, "machine_text": 機械の文字}。
    known=False のときは「アプリの一覧にありません」と言い、機械の文字を出す（有るように見せない）。"""
    code = str(code or "").strip()
    exact, near = [], []
    if code:
        for a in fanuc_alarms.search_alarms_ranked(alarms_db, code):
            if fanuc_alarms.is_exact_code(a, code):
                exact.append(a)
            elif _only_digits_match(a, code):
                near.append(a)
    return {"exact": exact, "near": near, "known": bool(exact), "machine_text": str(machine_text or "")}


def _only_digits_match(alarm, code):
    """数字（前ゼロ無視）は同じだが、文字（種類）が違う。"""
    ql, qn = fanuc_alarms._split_code(code)
    al, an = fanuc_alarms._split_code(alarm.get("code", ""))
    return qn is not None and an is not None and an == qn and al != ql


# ------------------------------------------------------------------ 直ったか比べる

def _complete(a):
    return a.get("type") is not None and a.get("no") is not None


def _key(a, by_number):
    if by_number:
        return focas_ops._alarm_key(a)           # 書いたあとに増えたアラームを見るのと同じ比べ方
    return (None, a.get("code"), a.get("axis", 0) or 0)


def alarm_diff(before, now):
    """前と今を比べる。{"gone": 消えた, "still": まだ出ている, "new": 新しく出た}。
    種類と番号が両方に揃っているときは focas_ops._alarm_key（種類・番号・軸）で、
    揃っていない一覧（番号と文字だけ）のときは番号の文字（PW0000 など）で比べる。"""
    b, n = normalize(before), normalize(now)
    by_number = all(_complete(a) for a in b + n)
    bk = {_key(a, by_number) for a in b}
    nk = {_key(a, by_number) for a in n}
    return {"gone": [a for a in b if _key(a, by_number) not in nk],
            "still": [a for a in n if _key(a, by_number) in bk],
            "new": [a for a in n if _key(a, by_number) not in bk]}


# ------------------------------------------------------------------ 記録

def append_record(path, row):
    """アラーム記録.csv に1行足す。row は 見出し（日時・機番・CNC ID・番号・機械の文字・やったこと・結果）の辞書。
    日時が無ければ今。ファイルが無ければ見出しから作る（utf-8-sig。足すときは BOM を重ねない）。"""
    p = Path(path)
    data = {k: str((row or {}).get(k, "") or "") for k in RECORD_HEADER}
    if not data["日時"]:
        data["日時"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new = not p.exists() or p.stat().st_size == 0
    p.parent.mkdir(parents=True, exist_ok=True)
    if not new:                                   # 最後が改行で終わっていなければ足す（行がくっつかないように）
        with open(p, "rb") as f:
            f.seek(-1, 2)
            last = f.read(1)
        if last not in (b"\n", b"\r"):
            with open(p, "ab") as g:
                g.write(b"\r\n")
    with open(p, "w" if new else "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(RECORD_HEADER)
        w.writerow([data[k] for k in RECORD_HEADER])


def read_records(path):
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = p.read_bytes()
    except Exception:
        return []
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    try:
        return [{k: (v or "") for k, v in dict(r).items() if k is not None}
                for r in csv.DictReader(io.StringIO(text, newline=""))]
    except csv.Error:
        return []


def last_fix(path, code):
    """同じ番号で、いちばん新しく「直った」と記録された「やったこと」（無ければ None）。"""
    want = fanuc_alarms._split_code(code)
    best = None
    for r in read_records(path):
        if want[1] is None or fanuc_alarms._split_code(r.get("番号", "")) != want:
            continue
        if (r.get("結果") or "").strip() != RESULT_FIXED or not (r.get("やったこと") or "").strip():
            continue
        best = r["やったこと"].strip()               # ファイルは後ろほど新しい
    return best


# ------------------------------------------------------------------ 絵

def remedy_art(code, machine_text=""):
    """やることが、いま easy_mode にある絵にできるときだけ、その名前（easy_flow の zoom と同じ名前）。
    PW（電源の入れ直し）・PS0000 → "power_cycle"。機械の文字に「非常停止」があれば "estop"（もどす絵）。
    それ以外は None（原点復帰・リセットなどは、合う絵が無いので出さない）。"""
    c = str(code or "").strip().upper()
    if c.startswith("PW") or c == "PS0000":
        return "power_cycle"
    t = str(machine_text or "").upper()
    if "非常停止" in t or "EMERGENCY" in t:
        return "estop"
    return None
