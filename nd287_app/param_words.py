"""機械に書く値の意味（2026-11-26。Qt なし）。

機械に書く一覧の各行に「この番号は何を決める値か」を1行で出す（やりながら覚えるため）。
- 文は `マスタ/パラメータの意味.csv`（番号, 意味）で足したり直したりできる（CSV が先。無い番号は下の初めの文）。
- 下の初めの文は**案**（FANUC のパラメータの名前を言い換えたもの。専門の中身なので、利用者に直してもらう）。
  分からない番号には何も出さない（推測で書かない）。値の良し悪しは言わない（意味だけ）。
"""
from __future__ import annotations

import csv
from pathlib import Path

WORDS_FILE = "パラメータの意味.csv"
WORDS_HEADER = ("番号", "意味")

DEFAULT = {
    1005: "原点復帰のしかたなど、軸ごとの設定",
    1420: "早送りの速さ",
    1425: "原点復帰で、原点の近くをゆっくり動くときの速さ",
    1620: "早送りの、加速と減速にかける時間",
    1815: "位置を読む検出器の設定（別置の検出器を使うか・原点が決まっているか など）",
    1821: "レファレンスカウンタの大きさ（原点を決めるときの、1区切りの長さ）",
    1828: "動いているときに許す、指令と実際の位置の差の上限",
    2000: "サーボの基本の設定",
    2018: "別置の検出器の、信号の向き",
    2020: "モーター番号（付いているモーターの種類）",
    2022: "モーターの回る向き",
    2023: "速度パルス数",
    2024: "位置パルス数",
    2084: "送りの歯車比（フレキシブルフィードギヤ）の分子",
    2085: "送りの歯車比（フレキシブルフィードギヤ）の分母",
    2165: "アンプの最大電流（アンプの容量）",
    2209: "サーボの機能の設定",
}


def path(settings=None, app_dir=None):
    p = str((settings or {}).get("param_words_csv") or "").strip()
    if p:
        return Path(p)
    if app_dir is None:
        from . import settings as S
        app_dir = S.app_dir()
    return Path(app_dir) / "マスタ" / WORDS_FILE


def load(csv_path=None):
    """{番号: 意味}。初めの文に、CSV の行を重ねる（CSV が先）。CSV で意味を空にした番号は出さない。"""
    out = dict(DEFAULT)
    p = Path(csv_path) if csv_path else None
    if p is None or not p.exists():
        return out
    try:
        raw = p.read_bytes()
    except OSError:
        return out
    text = None
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return out
    for row in csv.reader(text.splitlines()):
        if len(row) < 2:
            continue
        no = number(row[0])
        if no is None:
            continue
        word = row[1].strip()
        if word:
            out[no] = word
        else:
            out.pop(no, None)
    return out


def number(text):
    """「N01815」「1815」「No.1815」→ 1815。読めなければ None。"""
    digits = "".join(ch for ch in str(text or "") if ch.isdigit())
    return int(digits) if digits else None


def word(words, no):
    """その番号の意味（無ければ ""）。"""
    try:
        return str((words or {}).get(int(no), ""))
    except (TypeError, ValueError):
        return ""


def template_rows(words=None):
    """CSV のひな形の行（見出し＋いまの文）。"""
    w = DEFAULT if words is None else words
    return [list(WORDS_HEADER)] + [[str(k), w[k]] for k in sorted(w)]
