# -*- coding: utf-8 -*-
"""かんたん作成の段④「測定プログラムを送る」で、何を何番で何本作るかを決める（Qt なし）。

製品の型式から測定条件を引き、分け方（4択）・プログラム番号・本数を決めて、
fanuc.nc_bytes() を通した中身まで作る。画面は値を出し入れするだけ。

分け方:
  one       1本にまとめる（分割＋再現）   本体 O<main>（再現の繰り返し部分は別の O<sub>）
  two       別々に2本（分割用・再現性用）  分割 O<main>・再現性 O<main の次>（＋サブ O<sub>）
  division  分割だけ                       O<main>
  repeat    再現性だけ                     O<main>（＋サブ O<sub>）

「再現の繰り返し部分」は、再現性の測定を M98 で呼ぶ小さなプログラム（サブプログラム）。
1本にまとめても、実際の O 番号は本体とサブの 2 つになる（fanuc.generate の形そのまま）。
"""

import csv
import dataclasses
import hashlib
import time
from pathlib import Path

from . import batch_build, fanuc
from .masters import (condition_params, find_entry, user_condition_params)
from .sequence import tilt_blocks

CHOICES = (
    ("one", "1本にまとめる（分割＋再現）"),
    ("two", "別々に2本（分割用・再現性用）"),
    ("division", "分割だけ"),
    ("repeat", "再現性だけ"),
)
CHOICE_KEYS = tuple(k for k, _ in CHOICES)

# 画面に一行で出す「これから作るもの」
CHOICE_NOTE = {
    "one": "分割と再現性を1本にまとめて作ります。",
    "two": "分割と再現性を別々に2本で作ります。",
    "division": "分割だけを作ります。",
    "repeat": "再現性だけを作ります。",
}

# O8000〜O9999 は保護プログラム領域で、機械へ書けないことがある（settings.py のコメント）
PROTECTED = (8000, 9999)
MAX_NUMBER = 9999

ROLE_LABEL = {
    "one": {"main": "分割＋再現性（本体）", "sub": "再現性の繰り返し部分（サブ）"},
    "two": {"division": "分割用", "repeat": "再現性用（本体）", "sub": "再現性の繰り返し部分（サブ）"},
    "division": {"main": "分割用"},
    "repeat": {"main": "再現性用（本体）", "sub": "再現性の繰り返し部分（サブ）"},
}


# ---------------------------------------------------------------- 回転か傾斜か

def decide_kind(kinds):
    """製品の種別（"回転"／"傾斜" の並び）から (傾斜か, 画面に出す一行, 選び直しを出すか)。

    決められないとき（種別が無い・回転と傾斜の両方）は回転を既定にして、理由を一行で書く。
    選び直せるのは、このときだけ。"""
    ks = {str(k).strip() for k in (kinds or []) if str(k).strip()}
    if ks == {"傾斜"}:
        return True, "傾斜のテーブルとして作ります（製品が傾斜のため）。", False
    if ks == {"回転"}:
        return False, "回転のテーブルとして作ります（製品が回転のため）。", False
    if not ks:
        return False, "製品から回転か傾斜か分かりませんでした。回転として作ります。違うときは下で選び直してください。", True
    return False, ("回転と傾斜の両方が選ばれています。回転として作ります。"
                   "傾斜のプログラムにするときは下で選び直してください。"), True


# ---------------------------------------------------------------- 測定条件を引く

def _num(rec, field, default=0.0):
    try:
        return float(str(rec.get(field, "")).strip() or default)
    except ValueError:
        return default


def _find(entries, model):
    """型式で引く。製品データの型式は「RTV-510,WA」のようにカンマ以降が付くことがあるので、
    そのまま → カンマを詰めた形（RTV-510WA）→ カンマの前（RTV-510）の順に探す。"""
    text = str(model or "").strip()
    cands = [text]
    if "," in text:
        base, rest = (x.strip() for x in text.split(",", 1))
        cands += [base + rest, base]
    for c in cands:
        e = find_entry(entries or {}, c)
        if e:
            return e
    return None


def master_model(masters, model, tilt):
    """測定の画面の「型式」に入れる書き方 ＝ 測定条件の登録に載っている名前。
    製品データの型式（例「RTV-510,WA」）を、そのまま → カンマを詰めた形 → カンマの前 の順に、
    まず登録の名前とぴったり同じものを探す。無ければ find_entry と同じゆるい当て方（末尾の英字を外す）で
    当たった登録の名前を返す。どれも当たらなければ ""（測定の画面の型式は書き換えない）。"""
    import re
    masters = masters or {}
    text = str(model or "").strip()
    cands = [text]
    if "," in text:
        base, rest = (x.strip() for x in text.split(",", 1))
        cands += [base + rest, base]
    keys = (("user_tilt", "user_tilt_repeat") if tilt
            else ("user_rotary", "conditions", "user_rotary_repeat"))
    tables = [masters.get(k) or {} for k in keys]
    for c in cands:
        if c and any(c.upper() in t for t in tables):
            return c
    for c in cands:
        stripped = re.sub(r"(\d)[A-Za-z]+$", r"\1", c.upper())
        if c and any(stripped in t for t in tables):
            return stripped
    return ""


MODE_FOR = {  # 分け方 → 測定の画面の「モード」（回転, 傾斜）
    "one": ("回転分割+再現", "傾斜分割+再現"),
    "division": ("回転分割", "傾斜分割"),
    "repeat": ("回転再現性", "傾斜再現性"),
}


def handoff(masters, model, plan):
    """段④で機械に入れ終えたあと、測定の画面へ渡すもの。
    {"model", "steps": [{"mode": 測定の画面のモード, "programs": [機械で選ぶ番号]}…]}。
    「別々に2本」は、分割 → 再現性 の順に2回測る。"""
    tilt = bool(plan.get("tilt"))
    choice = plan.get("choice") or ""
    mains = [int(p["numbers"][0]) for p in plan.get("programs") or [] if p.get("numbers")]
    steps = []
    if choice == "two" and len(mains) >= 2:
        steps = [{"mode": MODE_FOR["division"][tilt], "programs": [mains[0]]},
                 {"mode": MODE_FOR["repeat"][tilt], "programs": [mains[1]]}]
    elif choice in MODE_FOR and mains:
        steps = [{"mode": MODE_FOR[choice][tilt], "programs": [mains[0]]}]
    return {"model": master_model(masters, model, tilt), "steps": steps}


def _kind_rec(kind_table, model, tilt, kind, scale):
    """種類（モーター／駆動装置／フルクロ）で引くとき: (使う: bool, 行, 種類名)。
    kind が空・表が無い・この型式に種類の登録が無いときは、使わない（今までどおり）。"""
    from . import kind_conditions as K
    if not kind or not kind_table or not K.kinds_of(kind_table, str(model or "").strip(), bool(tilt)):
        return False, None, ""
    name = K.normalize_kind(kind) or kind
    rec = K.find(kind_table, str(model).strip(), bool(tilt), kind, scale or "")
    if rec is not None and rec.get("測定の種類") != name:   # 種類の行が無くて「種類なし」の行が返った: 別の登録を黙って使わない
        rec = None
    return True, rec, name


def repeat_conditions(masters, model, tilt, kind="", scale="", kind_table=None):
    """型式の再現性の条件。{"blocks":[角度…], "repeats":回数, "source":説明} か None。

    ユーザー登録CSV（回転は user_rotary_repeat・傾斜は user_tilt_repeat。別ファイル）。
    測定条件のエントリ自体が blocks と repeats を持っていれば、それも使う。
    kind（種類）があり、その型式に種類の登録があるときは、種類別の表の再現性だけを使う
    （その種類に再現性が無ければ None。ほかの種類・今までの登録の値は使わない）。"""
    masters = masters or {}
    use, krec, kname = _kind_rec(kind_table, model, tilt, kind, scale)
    if use:
        from . import kind_conditions as K
        r = K.repeat(krec) if krec else None
        if not r:
            return None
        blocks = tilt_blocks(r["start"], r["end"], r["blocks_n"])
        return {"blocks": blocks, "repeats": r["repeats"],
                "source": f"種類別の登録（{kname}）の再現性（{r['blocks_n']}箇所×{r['repeats']}回）"}
    key = "user_tilt_repeat" if tilt else "user_rotary_repeat"
    rec = _find(masters.get(key), model)
    if rec:
        n = int(_num(rec, "ブロック数"))
        reps = int(_num(rec, "回数"))
        if n >= 1 and reps >= 1:
            blocks = tilt_blocks(_num(rec, "再現開始"), _num(rec, "再現終了"), n)
            return {"blocks": blocks, "repeats": reps,
                    "source": f"登録された再現性の条件（{n}箇所×{reps}回）"}
    if not tilt:
        cond = _find(masters.get("conditions"), model)
        if cond and cond.get("blocks") and cond.get("repeats"):
            return {"blocks": [float(b) for b in cond["blocks"]], "repeats": int(cond["repeats"]),
                    "source": "測定条件の再現性"}
    return None


def division_conditions(masters, model, tilt, kind="", scale="", kind_table=None):
    """型式の分割の条件。{"params":{…}, "cond":測定条件1件か None, "source":説明} か None。

    回転: ユーザー登録の回転条件 → 無ければ 測定条件.csv（アプリの測定画面と同じ順）。
    傾斜: ユーザー登録の傾斜条件だけ（回転の条件は傾斜に使わない）。
    kind（種類）があり、その型式に種類の登録があるときは、種類別の表の値だけを使う。
    刻みが決まらないとき（_ok が False）は、10° などへ黙って落とさず、
    {"params": None, "cond": None, "source": "", "error": 理由} を返す（プログラムは作らない）。"""
    masters = masters or {}
    use, krec, kname = _kind_rec(kind_table, model, tilt, kind, scale)
    if use:
        from . import kind_conditions as K
        if krec is None:
            return {"params": None, "cond": None, "source": "",
                    "error": f"{kname}の測り方が登録されていません"}
        p = K.params(krec, bool(tilt))
        if not p.get("_ok"):
            return {"params": None, "cond": None, "source": "",
                    "error": f"{kname}の刻みが決まりません（登録の「{'刻み' if tilt else 'ホイール刻み'}」"
                             "が空で、歯数からも決められません）"}
        if not tilt:
            p.update(wheel_start=0.0, wheel_end=360.0)
        src = f"種類別の登録（{kname}）"
        if p.get("_note"):
            src += "：" + p["_note"]
        return {"params": p, "cond": None, "source": src}
    if tilt:
        rec = _find(masters.get("user_tilt"), model)
        if not rec:
            return None
        p = user_condition_params(rec, tilt=True)
        return {"params": p, "cond": None, "source": "登録された傾斜の条件"}
    rec = _find(masters.get("user_rotary"), model)
    if rec:
        p = user_condition_params(rec, tilt=False)
        p.update(wheel_start=0.0, wheel_end=360.0)
        return {"params": p, "cond": None, "source": "登録された回転の条件"}
    cond = _find(masters.get("conditions"), model)
    if cond:
        p = condition_params(cond)
        if p.get("wheel_pitch"):
            p.update(wheel_start=0.0, wheel_end=360.0, worm_start=0.0)
            return {"params": p, "cond": cond, "source": "測定条件マスタ"}
    return None


# ---------------------------------------------------------------- 分け方の自動判定

def default_choice(has_division, has_repeat, last=""):
    """(選べる分け方の集合, 既定の分け方, 理由の一行)。

    再現性の条件が無い → 分割だけ（理由を出す）。分割の条件が無い → 再現性だけ。
    両方ある → 前回の選び方（設定）。無ければ「1本にまとめる」。"""
    if has_division and has_repeat:
        allowed = set(CHOICE_KEYS)
        choice = last if last in allowed else "one"
        return allowed, choice, CHOICE_NOTE[choice]
    if has_division:
        return {"division"}, "division", "この型式には再現性の条件が登録されていないので、分割だけを作ります。"
    if has_repeat:
        return {"repeat"}, "repeat", "この型式の分割の測定条件が見つからないので、再現性だけを作ります。"
    return set(), "", "この型式の測定条件が見つかりません。測定プログラムは作れません。"


# ---------------------------------------------------------------- プログラム番号

def repeat_number(main, rep_sub):
    """「別々に2本」の再現性用の本体の番号 = 分割用の次の番号。
    再現性サブの番号とぶつかるとき・保護領域（O8000〜O9999）に入るときは、次の空きへ進める。
    9999 を超えるなら ValueError（黙って決めない）。"""
    n = int(main) + 1
    while n == int(rep_sub) or n == int(main) or PROTECTED[0] <= n <= PROTECTED[1]:
        n += 1
    if n > MAX_NUMBER:
        raise ValueError(f"再現性用の番号を決められません（O{main} の次に空きがありません）")
    return n


def assign_numbers(choice, main, rep_sub, use_sub=True):
    """分け方ごとの番号の割り当て。戻り値 [{"role","number","label"}]（機械へ入る順）。
    role: main / division / repeat / sub。"""
    labels = ROLE_LABEL[choice]
    main, rep_sub = int(main), int(rep_sub)
    slots = []
    if choice == "one":
        slots.append(("main", main))
        if use_sub:
            slots.append(("sub", rep_sub))
    elif choice == "two":
        slots.append(("division", main))
        slots.append(("repeat", repeat_number(main, rep_sub)))
        if use_sub:
            slots.append(("sub", rep_sub))
    elif choice == "division":
        slots.append(("main", main))
    elif choice == "repeat":
        slots.append(("main", main))
        if use_sub:
            slots.append(("sub", rep_sub))
    else:
        raise ValueError(f"分け方が分かりません: {choice}")
    return [{"role": r, "number": n, "label": labels[r]} for r, n in slots]


def check_numbers(slots):
    """番号の点検。戻り値 (問題の文の一覧, 注意の文の一覧)。問題があれば送らない。"""
    problems, notes, seen = [], [], {}
    for s in slots:
        n = s["number"]
        if not 1 <= n <= MAX_NUMBER:
            problems.append(f"{s['label']}の番号 O{n} は使えません（1〜{MAX_NUMBER}）")
        if n in seen:
            problems.append(f"O{n} が {seen[n]} と {s['label']} で重なっています")
        seen[n] = s["label"]
        if PROTECTED[0] <= n <= PROTECTED[1]:
            notes.append(f"O{n} は保護されている番号の範囲です。機械が書き込みを断ることがあります")
    return problems, notes


# ---------------------------------------------------------------- プログラムを作る

def _cfg(settings, main, sub):
    cfg = fanuc.FanucConfig.from_settings(settings)
    return dataclasses.replace(cfg, main_number=int(main), rep_sub_number=int(sub))


def _generate(div, rep, cfg, *, tilt, include_division, include_repeat, title):
    blocks = rep["blocks"] if include_repeat else None
    repeats = rep["repeats"] if include_repeat else 0
    if div and div.get("cond") is not None and not tilt:
        return batch_build.program_for_model(
            div["cond"], cfg, rotary=True, repeats=repeats, blocks=blocks, title=title,
            include_division=include_division)
    p = (div or {}).get("params") or {}
    return fanuc.generate(
        cfg, rotary=not tilt, title=title,
        wheel_pitch=p.get("wheel_pitch") or 10.0, wheel_start=p.get("wheel_start", 0.0),
        wheel_end=p.get("wheel_end", 360.0), worm_pitch=p.get("worm_pitch") or 0.0,
        worm_range=p.get("worm_range") or 0.0, worm_start=p.get("worm_start", 0.0),
        blocks=list(blocks or []), repeats=int(repeats or 0),
        include_division=bool(include_division),
        include_repeat=bool(include_repeat and blocks and repeats))


def summarize(number, *, tilt, params, rep, include_division, include_repeat, return_to_start=True):
    """プログラム1本を、Gコードでなく言葉の1行にする。例「O0100：回転 10°ごとに 36点を測って、0°に戻る」。"""
    kind = "傾斜" if tilt else "回転"
    parts = []
    if include_division:
        pitch = params.get("wheel_pitch") or 10.0
        n = round((params.get("wheel_end", 360.0) - params.get("wheel_start", 0.0)) / pitch) + 1
        parts.append(f"{kind} {pitch:g}°ごとに {n}点を（行きと帰りで）測って")
        wp, wr = params.get("worm_pitch") or 0.0, params.get("worm_range") or 0.0
        if wp and wr:
            parts.append(f"ウォームを {wp:g}°ごとに {round(wr / wp) + 1}点測って")
    if include_repeat and rep:
        parts.append(f"{len(rep['blocks'])}か所で、それぞれ {rep['repeats']}回くり返して測って")
    if not return_to_start and parts:
        parts[-1] = parts[-1][:-1] if parts[-1].endswith("て") else parts[-1]
        parts[-1] += "る"
    if return_to_start:
        parts.append("0°に戻る")
    return f"O{int(number):04d}：" + "、".join(parts)


def build_programs(choice, slots, *, settings, div, rep, model, tilt, eob=None):
    """分け方に合わせて測定プログラムを作る。戻り値 [{"key","label","text","nc","numbers"}]。
    nc は fanuc.nc_bytes(text, eob) の出力そのまま（区切りは既定で LF CR CR・1バイトも変えない）。"""
    eob = eob or fanuc.DEFAULT_EOB
    num = {s["role"]: s["number"] for s in slots}
    sub = num.get("sub", int(settings.get("fanuc_rep_sub_number", 1000)))
    name = str(model or "").strip()
    out = []

    def add(key, label, main, include_division, include_repeat, tag):
        cfg = _cfg(settings, main, sub)
        text = _generate(div, rep, cfg, tilt=tilt, include_division=include_division,
                         include_repeat=include_repeat, title=f"{name} {tag}".strip())
        has_sub = bool(include_repeat and cfg.use_subprogram and rep and rep["blocks"] and rep["repeats"])
        nums = [main] + ([sub] if has_sub else [])
        lines = {main: summarize(main, tilt=tilt, params=(div or {}).get("params") or {}, rep=rep,
                                 include_division=include_division, include_repeat=include_repeat,
                                 return_to_start=cfg.return_to_start)}
        if has_sub:
            lines[sub] = f"O{sub:04d}：再現性の繰り返し部分（本体から呼ばれます。本体といっしょに送ります）"
        out.append({"key": key, "label": label, "text": text, "nc": fanuc.nc_bytes(text, eob),
                    "numbers": nums, "lines": lines})

    if choice == "one":
        add("one", "分割＋再現性", num["main"], True, True, "BUNKATSU SAIGEN")
    elif choice == "two":
        add("division", "分割用", num["division"], True, False, "BUNKATSU")
        add("repeat", "再現性用", num["repeat"], False, True, "SAIGEN")
    elif choice == "division":
        add("division", "分割用", num["main"], True, False, "BUNKATSU")
    elif choice == "repeat":
        add("repeat", "再現性用", num["main"], False, True, "SAIGEN")
    else:
        raise ValueError(f"分け方が分かりません: {choice}")
    return out


def make_plan(settings, masters, model, kinds, *, tilt=None, choice=None, numbers=None, eob=None,
              kind="", scale="", kind_table=None):
    """段④の中身を全部決める。画面は、この結果を出すだけ。

    tilt    … None なら製品の種別（kinds）から自動。True/False は人が選び直したとき。
    choice  … None なら自動（前回の選び方 settings["wizard_prog_choice"]）。
    numbers … {role: 番号}。人が番号を変えたとき。
    戻り値 dict: tilt / kind_note / ambiguous / has_division / has_repeat / allowed / choice /
                 why / slots / programs / problems / notes / div_source / rep_source
    """
    settings = settings or {}
    auto_tilt, kind_note, ambiguous = decide_kind(kinds)
    if tilt is None:
        tilt = auto_tilt
    div = division_conditions(masters, model, tilt, kind, scale, kind_table)
    rep = repeat_conditions(masters, model, tilt, kind, scale, kind_table)
    if div and div.get("error"):                          # 種類の条件が決まらない: 作らない・理由を出す（10° に落とさない）
        return dict(tilt=tilt, kind_note=kind_note, ambiguous=ambiguous, has_division=False,
                    has_repeat=rep is not None, allowed=[], choice="", why=div["error"], slots=[],
                    programs=[], problems=[div["error"]], notes=[], div_source="",
                    rep_source=(rep or {}).get("source", ""))
    allowed, auto_choice, why = default_choice(
        div is not None, rep is not None, str(settings.get("wizard_prog_choice", "") or ""))
    if choice in allowed:
        why = CHOICE_NOTE[choice]
    else:
        choice = auto_choice
    plan = dict(tilt=tilt, kind_note=kind_note, ambiguous=ambiguous, has_division=div is not None,
                has_repeat=rep is not None, allowed=allowed, choice=choice, why=why, slots=[],
                programs=[], problems=[], notes=[], div_source=(div or {}).get("source", ""),
                rep_source=(rep or {}).get("source", ""))
    if not choice:
        plan["problems"].append(why)
        return plan
    use_sub = bool(settings.get("fanuc_use_subprogram", True))
    try:
        slots = assign_numbers(choice, int(settings.get("fanuc_main_number", 100)),
                               int(settings.get("fanuc_rep_sub_number", 1000)), use_sub)
    except ValueError as e:
        plan["problems"].append(str(e))
        return plan
    for s in slots:
        if numbers and s["role"] in numbers:
            s["number"] = int(numbers[s["role"]])
    plan["slots"] = slots
    problems, notes = check_numbers(slots)
    plan["notes"] = notes
    if problems:
        plan["problems"] += problems
        return plan
    try:
        plan["programs"] = build_programs(choice, slots, settings=settings, div=div, rep=rep,
                                          model=model, tilt=tilt, eob=eob)
    except Exception as e:
        plan["problems"].append(f"測定プログラムを作れませんでした: {e}")
    # サブの番号は、再現性が無い（サブが要らない）ときは出さない
    return plan


# ---------------------------------------------------------------- 結果の表示用

def row_status(item):
    """送った結果の1行 → (表示, 区分)。区分: ok / skip / bad / none。"""
    if not item:
        return "まだ送っていません", "none"
    if item.get("skipped"):
        return item.get("note") or "もうあります（書いていません）", "skip"
    if not item.get("written"):
        return "書けませんでした", "bad"
    if item.get("match") is True:
        return "OK", "ok"
    if item.get("match") is False:
        return "ここが違う", "bad"
    return "読み戻せませんでした", "bad"


STATE_TEXT = {
    "new": "送ります",
    "same": "もう入っています（送らなくて大丈夫）",
    "differs": "もうあります。中身が違います（入れ替えますか？）",
    "unknown": "もうあります。中身を読めませんでした（入れ替えますか？）",
}


def state_text(state):
    """送る前に機械の中を確かめた結果（focas_ops.inspect_programs）の、1行の言葉。"""
    return STATE_TEXT.get(state, "")


def groups_of(programs):
    """必ずセットで送る番号の組（本体とサブ）。1つのプログラムから出た番号は組になる。"""
    return [list(p["numbers"]) for p in programs]


def mismatched_numbers(items):
    """書いたのに読み戻しが違った番号。「LF のみで送り直す」の対象。"""
    return [it["number"] for it in items or [] if it.get("written") and it.get("match") is False]


def lf_resend_nc(programs, numbers):
    """読み戻しが違った番号だけを、区切りを LF のみにして作り直した nc。
    プログラムの文字（text）から fanuc.nc_bytes(text, EOB_LF) で作る（人が押したときだけ使う）。"""
    from . import focas_ops
    want = set(numbers)
    picked = []
    for p in programs:
        for q in focas_ops.split_programs(fanuc.nc_bytes(p["text"], fanuc.EOB_LF), fanuc.EOB_LF):
            if q["number"] in want:
                picked.append(q)
    if not picked:
        return b""
    return focas_ops.join_programs(picked, fanuc.EOB_LF)


# ---------------------------------------------------------------- 送ったものの記録（PC側ファイル・マスタ外）

LOG_NAME = "送ったプログラム記録.csv"
LOG_FIELDS = ["日時", "型式", "号機", "分け方", "番号", "指紋", "結果"]


def fingerprints(programs):
    """{番号: 中身の指紋}。機械へ渡す形（wire）の SHA-256 の先頭12桁。"""
    from . import focas_ops
    out = {}
    for p in programs:
        for q in focas_ops.split_programs(p["nc"], fanuc.DEFAULT_EOB):
            out[q["number"]] = hashlib.sha256(q["wire"]).hexdigest()[:12]
    return out


def read_log(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def append_log(path, model, unit, choice, items, fps, now=None):
    """送って書けたもの（OK・ここが違う）を記録に足す。飛ばしたものは書かない。足した行数を返す。"""
    when = now or time.strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for it in items:
        if it.get("written"):
            rows.append({"日時": when, "型式": model, "号機": unit or "", "分け方": choice,
                         "番号": it["number"], "指紋": fps.get(it["number"], ""),
                         "結果": row_status(it)[0]})
    if not rows:
        return 0
    path = Path(path)
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)
    return len(rows)


def same_as_last(rows, model, fps):
    """同じ型式の、前回送ったもの（OK だった一番新しい日時の組）が、いま作るものと同じ中身なら、その1行を返す。
    違えば None。「前回と同じプログラムです」の判定。"""
    mine = [r for r in rows or [] if r.get("型式") == model and r.get("結果") == "OK"]
    if not mine:
        return None
    last = max(r["日時"] for r in mine)
    batch = [r for r in mine if r["日時"] == last]
    for r in batch:
        try:
            n = int(r["番号"])
        except ValueError:
            return None
        if fps.get(n) != r.get("指紋"):
            return None
    return batch[0]
