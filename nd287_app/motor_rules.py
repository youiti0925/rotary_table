# -*- coding: utf-8 -*-
"""モーター型式の末尾（-D・-B など）と、使える制御装置の対応表（編集できる規則）。

`マスタ/モーター規則.csv`（UTF-8）。画面は 制御装置マスタ →「モーター末尾の規則…」。
列: 末尾, 見る列, 条件, 根拠, 説明
  末尾    … モーター型式の末尾（例 D → 「αiF12/4000-D」「αiF8/3000 HV-D」）。大文字小文字は区別しない
  見る列  … 制御装置マスタのどの列を見るか: 「-D駆動」「-Bなめらか補正」（条件 ○ なら○の号機だけ）／「CNCユニット」（条件は正規表現）
  条件    … ○ または正規表現
  根拠    … 利用者／マスタ／推測（推測は実機で外れる可能性。規則の結果記録に残る）
上から順に見て、最初に合った規則だけを使う（-D が -B より先）。DD(DiS)モーターは対象にしない。
CSV が無い・読めないときは下の DEFAULT_RULES を使う（試験と新規インストールのため）。
"""
import csv
import re
from pathlib import Path

FIELDS = ["末尾", "見る列", "条件", "根拠", "説明"]
FLAG_COLUMNS = {"-D駆動": "d_drive", "-Bなめらか補正": "b_corr"}
TEXT_COLUMNS = {"CNCユニット": "cnc"}
YES = ("○", "〇", "◯", "O", "o")

DEFAULT_RULES = [
    {"末尾": "D", "見る列": "-D駆動", "条件": "○", "根拠": "利用者",
     "説明": "末尾が -D のモーターは -D駆動が○の号機だけ（2026-11-24 利用者「-D付はD駆動の事」）"},
    {"末尾": "B", "見る列": "CNCユニット", "条件": r"^\s*(30|31|32|35)i", "根拠": "推測",
     "説明": "末尾が -B のモーターは 30i/31i/32i/35i 系の号機だけ（利用者「-Bは31iとか決まってる」。号機の範囲は推測）"},
]


def load_rules(path=None):
    """規則を読む。ファイルが無い・空なら DEFAULT_RULES。"""
    if path and Path(path).exists():
        for enc in ("utf-8-sig", "cp932"):
            try:
                with open(path, newline="", encoding=enc) as f:
                    rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]
                rows = [r for r in rows if r.get("末尾")]
                if rows:
                    return rows
                break
            except Exception:
                continue
    return [dict(r) for r in DEFAULT_RULES]


def save_rules(path, rules):
    """規則を書く（書く前の控え .bak は呼び出し側）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rules:
            if (r.get("末尾") or "").strip():
                w.writerow({k: (r.get(k) or "").strip() for k in FIELDS})


def _is_dd(model):
    norm = re.sub(r"[\s\-_]", "", str(model or "")).upper()
    return "DIS" in norm or norm.startswith("DD")


def rule_for_motor(model, rules):
    """モーター型式に合う規則（最初の1つ）。無ければ None。"""
    s = str(model or "").strip()
    if not s or _is_dd(s):
        return None
    for r in rules:
        suf = (r.get("末尾") or "").strip().lstrip("-")
        if suf and re.search(r"-\s*" + re.escape(suf) + r"$", s, re.I):
            return r
    return None


def controller_ok(ctl, rule):
    """この規則で、制御装置 ctl を使ってよいか。規則の列が分からなければ絞らない（True）。"""
    col, cond = (rule.get("見る列") or "").strip(), (rule.get("条件") or "").strip()
    if col in FLAG_COLUMNS:
        val = str(getattr(ctl, FLAG_COLUMNS[col], "") or "").strip()
        return val in YES if cond in YES or not cond else bool(re.search(cond, val))
    if col in TEXT_COLUMNS:
        try:
            return bool(re.search(cond, str(getattr(ctl, TEXT_COLUMNS[col], "") or ""), re.I))
        except re.error:
            return True
    return True


def apply(cands, models, rules):
    """cands [(controller, 軸...)] を、models（モーター型式の一覧）に合う規則で絞る。
    戻り値 (絞った cands, 使った規則のリスト)。複数のモーターは全部の規則を満たす号機だけ。"""
    used = []
    for m in models:
        r = rule_for_motor(m, rules)
        if r is not None and r not in used:
            used.append(r)
    out = cands
    for r in used:
        out = [ca for ca in out if controller_ok(ca[0], r)]
    return out, used
