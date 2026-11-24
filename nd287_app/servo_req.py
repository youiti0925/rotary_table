# -*- coding: utf-8 -*-
"""モーターの種類ごとに必要な「サーボソフトの系列・版数」の点検（Qt なし）。

根拠: サーボ制御ソフト改版のお知らせ B-65270JA-11/002（2023-10）p.11
  αi-D モーターは「90J0 28.0 版以降（30i/31i/32i/35i -B）・90J5 13.0 版以降（0i-F）・90JP 12.0 版以降」。
書き方（モーター規則.csv の「必要サーボ」列）: 「90J0>=28.0|90J5>=13.0|90JP>=12.0」。空なら点検しない。
機械から読んだ版数（FOCAS）なら、足りないとき書込を止める。マスタの版数（SERVO 欄）は参考で、止めず注意だけ出す。
"""
import re

_SV = re.compile(r"(9[0-9A-Z]{3})\s*[/ ]\s*(\d+(?:\.\d+)?)", re.I)


def parse_servo(text):
    """'90J0/26.0'・'90J0 26.0'・'90J0/0014' → ('90J0', 26.0)。読めなければ None。"""
    m = _SV.search(str(text or ""))
    return (m.group(1).upper(), float(m.group(2))) if m else None


def parse_requirement(text):
    """'90J0>=28.0|90J5>=13.0' → {'90J0': 28.0, '90J5': 13.0}。"""
    out = {}
    for part in str(text or "").split("|"):
        m = re.match(r"\s*(9[0-9A-Z]{3})\s*>=\s*(\d+(?:\.\d+)?)\s*$", part, re.I)
        if m:
            out[m.group(1).upper()] = float(m.group(2))
    return out


def check(servo_text, requirement):
    """(結果, 文)。結果: True=足りる / False=足りない / None=点検できない（系列が表に無い・読めない）。"""
    req = parse_requirement(requirement)
    if not req:
        return None, ""
    sv = parse_servo(servo_text)
    if sv is None:
        return None, "サーボソフトの版数を読めていないので、このモーターが動くか点検できません"
    series, ver = sv
    need = "・".join(f"{s} {v:g} 版以降" for s, v in req.items())
    if series not in req:
        return None, f"サーボソフト {series}/{ver:g} は、資料の対応表（{need}）に載っていないので、点検できません"
    if ver < req[series]:
        return False, (f"この機械のサーボソフト {series}/{ver:g} は、このモーターに必要な {series} {req[series]:g} 版以降より古いので、"
                       "動きません（サーボソフトの更新が要ります）")
    return True, f"サーボソフト {series}/{ver:g} は {series} {req[series]:g} 版以降を満たしています"
