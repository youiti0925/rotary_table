# -*- coding: utf-8 -*-
"""パラメータ閲覧・比較（番地ごとの値を一覧、2ファイルの差分強調、番号範囲/差分のみ絞り込み）。

FANUCネイティブ(.PRM: N#####Q1 A1P..A4P..) と ヘッダ＋CSV(.prm: "1815",..,"値",..) の
両方を、(番号, ラベル/軸, 値, 説明) の行に正規化する。Qt非依存なので単体試験できる。
"""

import re

from . import fanuc_param, prm_format


def _num_int(num) -> int:
    """番号を数値化（範囲フィルタ・並べ替え用）。数字が無ければ大きな値。"""
    s = re.sub(r"\D", "", str(num or ""))
    return int(s) if s else 10 ** 9


def read_rows(text: str) -> list:
    """テキスト → [{"num","label","value","desc"}]。

    ネイティブ: ラベルは A1/A2/L1/S1 等（軸ごとに1行）、説明は空。
    ヘッダ＋CSV: ラベルは ''（番号ごとに1行）、説明は和名。
    """
    rows = []
    if not text:
        return rows
    if fanuc_param.looks_like_fanuc_prm(text):
        for line in text.splitlines():
            num = fanuc_param.param_number(line)
            if num is None:
                continue
            segs = fanuc_param.segments(line) or []
            if segs:
                for (g, _t, v) in segs:
                    rows.append({"num": num, "label": g or "", "value": v, "desc": ""})
            else:
                rows.append({"num": num, "label": "", "value": "", "desc": ""})
    else:
        try:
            doc = prm_format.parse_prm(text)
            for (num, val, jp, _en) in prm_format.iter_params(doc):
                rows.append({"num": num, "label": "", "value": val, "desc": jp})
        except Exception:
            pass
    return rows


def compare(a_text: str, b_text: str) -> list:
    """2テキストを (番号, ラベル) で突き合わせ [{"num","label","a","b","desc","differ"}]。

    番号は5桁ゼロ詰めで照合（'1815' と '01815' を同一視）。片方に無ければ a/b は None。
    番号→ラベル順に並べる。
    """
    def keyed(text):
        d = {}
        for r in read_rows(text):
            d[(fanuc_param._norm_num(r["num"]), r["label"])] = r
        return d

    A, B = keyed(a_text), keyed(b_text)
    out = []
    for k in sorted(set(A) | set(B), key=lambda k: (_num_int(k[0]), k[1])):
        ra, rb = A.get(k), B.get(k)
        av = ra["value"] if ra else None
        bv = rb["value"] if rb else None
        base = ra or rb
        out.append({"num": base["num"], "label": k[1], "a": av, "b": bv,
                    "desc": base.get("desc", ""), "differ": av != bv})
    return out


def filter_rows(rows: list, lo=None, hi=None, diff_only=False) -> list:
    """番号範囲 [lo, hi]（含む）と「差分のみ」で絞る。lo/hi が None なら無制限。

    diff_only は compare() の行（"differ" を持つ）にだけ効く。
    """
    out = []
    for r in rows:
        n = _num_int(r["num"])
        if lo is not None and n < lo:
            continue
        if hi is not None and n > hi:
            continue
        if diff_only and not r.get("differ"):
            continue
        out.append(r)
    return out
