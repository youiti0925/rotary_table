# -*- coding: utf-8 -*-
"""FOCAS の業務の流れ（Qt 非依存）。書ける状態の判定・自己診断・止まっている所の診断・
パラメータを書く流れ（段4）。

設計: docs/設計_FOCAS基本.md §6.2・§7.1・§7.4〜§7.7・§12-A。
書く流れ: 書込の一覧を作る（plan_writes）→ 1件ずつ「読む → 書く → 読み戻す → アラームを見る」
（run_write）→ 全部吸い出して全行照合（verify_all）→ 元に戻す（run_undo）。
PW0000 は見つけて「電源を入れ直してください」と言うところまで。電源待ちと自動再接続は作っていない（段6）。
"""

import base64
import csv
import hashlib
import json
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import fanuc_param
from . import focas_backup as B
from . import focas_client as C
from . import focas_helper as H
from . import param_build

kind_label = H.kind_label       # 画面に出す型の名前（byte/none → バイト）
DEFAULT_ALLOWED_ALARMS = ("SW0100",)
PWE_ALARM = "SW0100"


# ---------------------------------------------------------------------------
# 書ける状態（§7.7）
# ---------------------------------------------------------------------------

def writable_state(status, alarms, *, allowed_alarms=DEFAULT_ALLOWED_ALARMS, backup_info=None,
                   cnc_id=None, for_params=True):
    """いま機械に書けるか。戻り値 {"ok", "reasons", "pwe"}。理由は全部並べる。

    - 運転: run==0（リセット）かつ motion==0。非常停止なし。
    - アラーム: 無い、または許したアラーム（既定は SW0100＝PWE が ON のしるし）だけ。
    - 控え: この回に取った控えが「完全」で、いまつながっている機械（機械ごとの番号 CNC ID）と同じ。
      CNC ID が取れない機械・控えに CNC ID が無いときは書かない（どの機械か確かめられないため）。
    - PWE: パラメータを書くときは SW0100 が出ていること（アプリは PWE を触らない）。
    モード（MDI・編集など）では止めない（どのモードで書けるかは確かめていないため、
    推測で禁止しない。戻り値 12＝モード違いが出たらそのとき案内する）。
    """
    reasons = []
    v = (status or {}).get("values") or {}
    t = (status or {}).get("text") or {}
    if not v:
        reasons.append("機械の状態を読めていません")
    elif (status or {}).get("interpreted") is False:
        reasons.append("この機種は状態の読み方が未確認なので、書けるかどうか判定できません")
    else:
        if v.get("run") != 0:
            reasons.append(f"運転中または停止中です（運転: {t.get('run')}・生の値 {v.get('run')}）。"
                           "リセットの状態にしてください")
        if v.get("motion") != 0:
            reasons.append(f"軸が動いています（動作: {t.get('motion')}・生の値 {v.get('motion')}）")
        if v.get("emergency") != 0:
            reasons.append(f"非常停止です（生の値 {v.get('emergency')}）")
    codes = [a.get("code") for a in (alarms or [])]
    others = [a for a in (alarms or []) if a.get("code") not in allowed_alarms]
    if others:
        reasons.append("許していないアラームが出ています: "
                       + "、".join(f"{a.get('code')} {a.get('text')}" for a in others))
    pwe = PWE_ALARM in codes
    if for_params and not pwe:
        reasons.append("パラメータ書込が許可されていません。機械のセッティング画面で"
                       "『パラメータ書込』を 1 にしてください（アプリは触りません）")
    if backup_info is None:
        reasons.append("この回の控えがありません。先に『控えを取る』を押してください")
    elif not backup_info.get("complete"):
        reasons.append("控えが不完全です（点検の指摘があります）")
    elif not cnc_id:
        reasons.append("機械ごとの番号（CNC ID）が読めていません。どの機械か確かめられないので、この機械には書きません")
    elif not backup_info.get("cnc_id"):
        reasons.append("控えに機械ごとの番号（CNC ID）がありません。どの機械の控えか確かめられないので書きません")
    elif backup_info.get("cnc_id") != cnc_id:
        reasons.append("控えは別の機械のものです（機械ごとの番号（CNC ID）が違います）")
    return {"ok": not reasons, "reasons": reasons, "pwe": pwe}


# ---------------------------------------------------------------------------
# 自己診断（読むだけ。§12-A）
# ---------------------------------------------------------------------------

SAMPLE_NUMBERS = (20, 21, 1020, 1320, 1420, 1815, 1821, 2165, 982)
L_PROBE_NUMBERS = (0, 1, 3202)


def _same(kind, a, b):
    if kind == "bit":
        return str(a) == str(b)
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return str(a) == str(b)


def _line(level, title, *details):
    return {"level": level, "title": title, "lines": [str(d) for d in details]}


def _backup_values(folder):
    """控えの パラメータ.DAT と 型情報.csv → ({(番号, ラベル): 値}, {番号: 型コード})。"""
    folder = Path(folder)
    text = (folder / "パラメータ.DAT").read_bytes().decode("cp932", errors="replace")
    vm = {}
    for (num, label), val in fanuc_param.values_map(text).items():
        vm[(int(num), label)] = val
    types = {}
    p = folder / B.TYPES_NAME
    if p.exists():
        for i, row in enumerate(p.read_text(encoding="utf-8-sig").splitlines()):
            if i == 0 or not row.strip():
                continue
            a, b = row.split(",")[:2]
            types[int(a)] = int(b)
    return vm, types


def self_check(client, backup_folder=None, *, full=False, progress=None):
    """読むだけの確認を順に行い、結果の行（{"level", "title", "lines"}）を返す。

    level: ok / ng / warn / info。接続が切れたときは例外（FocasError）のまま上へ出す。
    backup_folder があれば、パラメータの値を控えと比べる（A1・A2・A4）。
    full=True なら控えにある全部の番号を読んで比べる（時間がかかる）。
    """
    prog = progress or (lambda d, t, n: None)
    out = []

    ident = client.call("identify")
    out.append(_line("ok", "機種・つないだ機械",
                     f"機種 {ident['cnc_type']}i {ident['mt_type']}  系列 {ident['series']}  "
                     f"版 {ident['version']}  軸数 {ident['axes']}",
                     f"軸名 {' '.join(ident.get('axis_names') or []) or '読めず'}",
                     f"機械ごとの番号（CNC ID）{ident.get('cnc_id')}",
                     f"手順の割り当て: {ident.get('family') or 'なし'}"
                     + ("" if ident.get("supported") else "（この機種はまだ対応していません）")))
    if not ident.get("supported"):
        out.append(_line("warn", "ここで止めました",
                         "この機種は読む手順を作っていないので、状態の確認だけにしました"))
    st = client.call("status")
    v, t = st["values"], st.get("text") or {}
    if st.get("interpreted", True) and t:
        out.append(_line(
            "info", "状態の数値（A3: 運転中・一時停止・非常停止の数値は未確認）",
            "  ".join(f"{k}={v[k]}" for k in v),
            f"読み: モード {t['aut']} / 運転 {t['run']} / 動作 {t['motion']} / 非常停止 {t['emergency']}"
            f" / アラーム {t['alarm']}",
            "機械をメモリ運転中・一時停止・非常停止・各モードにして、そのつど読み直すと表が確かめられます"))
    else:
        out.append(_line(
            "info", "状態の数値（この機種は読み方の表が未確認なので、生の数値だけ）",
            "  ".join(f"{k}={v[k]}" for k in v)))
    al = client.call("alarms")
    out.append(_line("info", f"アラーム {al['count']} 件" + ("（10件以上あるかもしれません）" if al["truncated"] else ""),
                     *[f"{a['code']}  種類 {a['type']}・番号 {a['no']}・軸 {a['axis']}  {a['text']}"
                       for a in al["alarms"]]))
    if not ident.get("supported"):
        return out
    pi = client.call("prog_info")
    out.append(_line("info", "プログラムの登録", f"登録 {pi['registered']} / 残り {pi['remaining']}"
                     f" / 使用メモリ {pi['used']:,} / 空き {pi['unused']:,}",
                     *(["残りが 0 です。空きメモリがあっても1本も入りません"] if pi["remaining"] == 0 else [])))
    types = client.call("param_info")["types"]
    machine_types = {no: ty for no, ty in types}
    out.append(_line("info", f"型情報 {len(machine_types)} 番号"))

    if not backup_folder:
        out.append(_line("warn", "パラメータの値の確認（A1）をしていません",
                         "控えが無いので比べる相手がありません。先に『控えを取る』を押してください"))
        return out
    vm, bt = _backup_values(backup_folder)
    dat_nums = {no for no, _ in vm}
    only_machine = sorted(set(machine_types) - dat_nums)
    only_dat = sorted(dat_nums - set(machine_types))
    out.append(_line(
        "info" if not only_machine and not only_dat else "warn",
        f"型情報と控えの番号の差（A4）: 型情報にだけある {len(only_machine)} 個・控えにだけある {len(only_dat)} 個",
        "型情報にだけ: " + (" ".join(map(str, only_machine[:30])) or "なし")
        + (" …" if len(only_machine) > 30 else ""),
        "控えにだけ: " + (" ".join(map(str, only_dat[:30])) or "なし")))

    # A1: 1つずつ読んだ値と、丸ごと吸い出した控えの値を比べる
    if full:
        items = [(no, lab) for (no, lab) in sorted(vm) if not lab.startswith(("L", "T"))]
    else:
        items = [(no, lab) for (no, lab) in sorted(vm)
                 if no in SAMPLE_NUMBERS and not lab.startswith(("L", "T"))
                 and (not lab.startswith(("A", "S")) or int(lab[1:] or 0) <= 6)]
    bad, unread, n_ok, done = [], [], 0, 0
    for i in range(0, len(items), 200):
        part = items[i:i + 200]
        res = client.call("param_read_many", {"items": [{"no": n, "label": l} for n, l in part]})
        for (no, lab), r in zip(part, res["items"]):
            if not r.get("ok"):
                unread.append((no, lab, r.get("error", {}).get("rc")))
                continue
            if _same(r["kind"], vm[(no, lab)], r["value"]):
                n_ok += 1
            else:
                bad.append((no, lab, r["type_code"], vm[(no, lab)], r["value"]))
        done += len(part)
        prog(done, len(items), "パラメータを1つずつ読んで比べています")
    # 1つでも「読めず」があれば OK にしない。全部読めなければ NG、一部なら「一部読めず」（注意）
    if bad:
        level, verdict = "ng", ""
    elif not items:
        level, verdict = "warn", "（比べる番号がありません）"
    elif n_ok == 0:
        level, verdict = "ng", "（全部読めませんでした）"
    elif unread:
        level, verdict = "warn", "（一部読めず）"
    else:
        level, verdict = "ok", ""
    out.append(_line(
        level, f"パラメータの値の確認（A1・{'全部' if full else '代表'}）{verdict}: {len(items)} 個中 一致 {n_ok}・"
               f"違う {len(bad)}・読めず {len(unread)}",
        *[f"違う: N{no:05d} {lab or '-'}  型コード {ty}  控え={a}  1つずつ読むと={b}"
          for no, lab, ty, a, b in bad[:40]],
        *([f"…ほか {len(bad) - 40} 個"] if len(bad) > 40 else []),
        *[f"読めず: N{no:05d} {lab or '-'}  戻り値 {rc}" for no, lab, rc in unread[:20]],
        *(["バイト型の符号・実数の『値と桁数』・軸型/主軸型の軸の欄の読み方が、"
           + ("読めた" if unread else "") + f"{n_ok} 個については控えと合っています"]
          if n_ok and not bad else [])))

    # A2: 系統型 L1・L2 は、軸の欄を 0・1・2 に替えて読み、控えの値と比べる
    probe = []
    for no in L_PROBE_NUMBERS:
        labs = {lab: vm[(no, lab)] for (n, lab) in vm if n == no and lab.startswith("L")}
        if len(labs) < 2:
            continue
        row = [f"N{no:05d} 控え " + " ".join(f"{k}={val}" for k, val in sorted(labs.items()))]
        for ax in (0, 1, 2):
            try:
                r = client.call("param_read", {"no": no, "label": "L1", "axis": ax})
                match = [k for k, val in labs.items() if _same(r["kind"], val, r["value"])]
                row.append(f"  軸の欄 {ax} → {r['value']}（一致: {'/'.join(match) or 'なし'}）")
            except C.FocasError as e:
                if e.kind in ("lost", "timeout", "not_connected"):
                    raise
                row.append(f"  軸の欄 {ax} → 読めず（戻り値 {e.detail.get('rc')}）")
        probe.append(row)
    if probe:
        out.append(_line("info", "系統型 L1・L2 の指し方（A2）。値が違う番号で見比べてください",
                         *[l for row in probe for l in row],
                         "分かるまで系統型は書きません"))
    return out


def format_report(lines):
    mark = {"ok": "OK ", "ng": "NG ", "warn": "注意", "info": "    "}
    out = []
    for l in lines:
        out.append(f"[{mark.get(l['level'], '    ')}] {l['title']}")
        out += [f"      {x}" for x in l["lines"] if x]
    return "\n".join(out) + "\n"


def report_has_ng(lines):
    return any(l["level"] == "ng" for l in lines)


# ---------------------------------------------------------------------------
# いまどこで止まっている？
# ---------------------------------------------------------------------------

def diagnose(client, settings, last_error=None):
    """つながらないときの手がかり。(見出し, 次に見るところ) を返す。"""
    todo = []
    py = C.resolve_python32(str(settings.get("focas_python32") or ""))
    helper = C.resolve_helper(str(settings.get("focas_helper_path") or ""))
    if not py:
        return ("FOCAS 用の Python（32bit）が見つかりません。",
                ["『はじめての設定』で 32bit の python.exe を選ぶ"])
    if not helper:
        return (f"{C.HELPER_NAME}（focas_helper.py）が見つかりません。",
                ["アプリのフォルダ（exe の隣）に focas_helper.py があるか確認する"])
    if client is None or not client.alive:
        head = "機械と話す小さなプログラムは、まだ起動していません。"
        todo.append("『つなぐ』を押す")
    elif not client.connected:
        head = "機械と話す小さなプログラムは起動していますが、機械にはつながっていません。"
        todo += ["IP アドレスとポート番号を確かめる（既定 8193）",
                 "LAN ケーブル・機械の電源を確かめる",
                 "SERVO GUIDE など別の FOCAS ソフトが同時につないでいないか確かめる（同時接続の動きは未確認）"]
    else:
        head = "機械につながっています。"
    if last_error is not None:
        head += f" 直近のエラー: {last_error}"
    tail = [l for l in (list(client.stderr_tail)[-5:] if client is not None else []) if l]
    if tail:
        todo.append("機械と話す小さなプログラムの出力: " + " / ".join(tail))
    return head, todo


# ---------------------------------------------------------------------------
# パラメータを書く（段4）
# ---------------------------------------------------------------------------

POWER_ALARM = "PW0000"          # 電源を入れ直す要求。書込の途中は出てよい（設計 §7.7）
JOB_NAME = "書込ジョブ.json"
PLAN_NAME = "書込後の予定.DAT"
LOG_HEADER = ["日時", "CNC ID", "号機", "製番", "番号", "ラベル", "旧値", "新値",
              "戻り値", "読み戻し", "結果"]
LOST_KINDS = ("lost", "timeout", "not_connected", "aborted", "protocol")
RESULT_TEXT = {"ok": "書けた・読み戻しOK", "alarm": "書けた・アラームで停止",
               "stale": "止めた(書く前の値が違う)", "failed": "書けなかった",
               "mismatch": "読み戻しが違う", "unknown": "不明", "refused": "断った"}


class WriteRefused(Exception):
    """書く前の確認で断った（機械には何も書いていない）。"""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__(" / ".join(self.reasons))


def verified_list(settings, family):
    """この機種で書いたことがある型（focas_verified_kinds）。例 ["byte/none"]。"""
    d = (settings or {}).get("focas_verified_kinds") or {}
    return [str(x) for x in (d.get(family) or [])]


def add_verified_kind(settings, family, key):
    """同じ値を書く確認が通った型を表に足す（呼ぶのは確認が通ったときだけ）。"""
    d = dict((settings or {}).get("focas_verified_kinds") or {})
    cur = [str(x) for x in (d.get(family) or [])]
    if key not in cur:
        cur.append(key)
    d[family] = cur
    settings["focas_verified_kinds"] = d
    return cur


def allowed_codes(settings):
    return tuple((settings or {}).get("focas_allowed_alarms") or DEFAULT_ALLOWED_ALARMS)


def same_value(kind, a, b):
    """機械の値と控えの値の比べ。ビットは文字、ほかは10進の数として（20000.0 と 20000 は同じ）。"""
    return _same(kind, a, b)


def _kind_of_text(types, no, text):
    code = types.get(no) if types else None
    if code is not None:
        return H.decode_type(code)["kind"]
    return "bit" if re.fullmatch(r"[01]{8}", str(text or "")) else "dword"


def plan_writes(raw, plan, types, *, verified):
    """書込の一覧を作る。raw は土台（FOCAS の控えのパラメータ）、plan は
    param_build.prepare(raw, …, reference="") の結果。**機械に書くのは raw と newtext の差だけ**。

    戻り値:
      items       書く行 [{"no","label","old","new","key","kind","type_code","verified","problems"}]
      missing     機械に無い番号の行（プレビューで「BASICに無い」）。一覧から外した件数を出す
      unwritable  差はあるが一覧に入れられない行（片側にしか無い・番号が読めない）
      expected    プレビューの「変わる行」の数（機械に無い番号を除く）
      mismatch    プレビューの件数と一覧の件数が合わないときの説明（None なら合っている）
      blockers    書く前に止める理由（形式が違う・型が未確認・書けない値・件数のずれ）
      unverified  {型の名前: [items の添字…]}
    """
    if plan.get("fmt") != "fanuc":
        return {"items": [], "missing": [], "unwritable": [], "expected": 0, "mismatch": None,
                "unverified": {},
                "blockers": ["この形式のパラメータは機械へ直接書けません（N 形式だけ）"]}
    items, unwritable, blockers = [], [], []
    rows = plan.get("rows") or []
    missing = [r for r in rows if r[4] == param_build.KIND_MISSING]
    expected = sum(1 for r in rows if r[5] and r[4] != param_build.KIND_MISSING)
    ok_types = set(verified)
    for number, label, old, new in fanuc_param.diff(raw, plan["newtext"]):
        try:
            no = int(number)
        except (TypeError, ValueError):
            unwritable.append((number, label, old, new))
            continue
        if old is None or new is None:
            unwritable.append((number, label, old, new))
            continue
        code = (types or {}).get(no)
        it = {"no": no, "label": label, "old": old, "new": new, "type_code": code,
              "key": None, "kind": None, "verified": False, "problems": [], "unknown_bits": []}
        if code is None:
            it["problems"].append("型情報にありません")
        else:
            it["key"] = H.kind_key(code, label)
            it["kind"] = H.decode_type(code)["kind"]
            it["unknown_bits"] = list(H.decode_type(code)["unknown_bits"])
            it["verified"] = it["key"] in ok_types
            mis = H.label_type_problem(code, label)
            if mis:
                it["problems"].append(mis)
            # 書く前に全件点検する（途中の1件で断られて、半分だけ書いた状態にしない）。
            # 実数の小数点以下の桁数は、機械の値の書き方（控え）から決める。書くときにヘルパーが
            # 機械から読んだ桁数で、もう一度同じ点検をする
            try:
                H.encode_param_value(it["kind"], new,
                                     H.real_decimals(old) if it["kind"] == "real" else None)
            except H.FocasError as e:
                it["problems"].append(e.detail.get("message", "書けない値です"))
        lk = H.label_kind(label)
        if lk in ("L", "T"):
            it["problems"].append("系統型（L・T）の軸の指し方は未確認なので書けません")
        elif lk == "?":
            it["problems"].append(f"ラベル {label} の種類が分かりません")
        items.append(it)
    mismatch = None
    if len(items) + len(unwritable) != expected:
        mismatch = (f"プレビューの『変わる行』{expected} 件と、書込の一覧 {len(items) + len(unwritable)} 件が"
                    "合いません。書かずに止めます（画面と中身がずれています）")
        blockers.append(mismatch)
    if unwritable:
        blockers.append(f"片側にしか無い番号があります（{len(unwritable)} 件。例 N{unwritable[0][0]} "
                        f"{unwritable[0][1] or '-'}）。機械に無い番号は書けません")
    unverified = {}
    for i, it in enumerate(items):
        if it["problems"]:
            blockers.append(f"N{it['no']:05d} {it['label'] or '-'}: " + "／".join(it["problems"]))
        elif not it["verified"]:
            unverified.setdefault(it["key"], []).append(i)
    for key, idx in unverified.items():
        ex = items[idx[0]]
        blockers.append(
            f"型 {H.kind_label(key)} はこの機種ではまだ書いたことがありません（{len(idx)} 件。例 N{ex['no']:05d} "
            f"{ex['label'] or '-'}）。先に『同じ値を書く確認』をしてください")
    return {"items": items, "missing": missing, "unwritable": unwritable, "expected": expected,
            "mismatch": mismatch, "blockers": blockers, "unverified": unverified}


def _alarm_key(a):
    return (a.get("type"), a.get("no"), a.get("axis", 0))


def is_power_alarm(a):
    """電源の入れ直しを求めるアラーム。種類1・番号0（PW0000）。実機での見え方は未確認【仕様】。"""
    return a.get("code") == POWER_ALARM or (a.get("type") == 1 and a.get("no") == 0)


def _read_value(client, it):
    r = client.call("param_read", {"no": it["no"], "label": it["label"]})
    return r["value"], r["kind"]


def _gate(client, *, cnc_id, backup_info, allowed):
    """書く直前の確認（確認ボタンの瞬間と、1件ごとの直前）。戻り値 (理由の並び, アラーム)。
    つながっている機械の機械ごとの番号（CNC ID）が控えのものと同じこと（取れない機械には書かない）・リセット・非常停止なし・アラームは許したものだけ
    （SW0100＝PWE が出ていること）・控えが完全なこと。"""
    ident = client.call("identify")
    st = client.call("status")
    al = client.call("alarms")["alarms"]
    reasons = []
    if not cnc_id or not ident.get("cnc_id"):
        reasons.append("機械ごとの番号（CNC ID）が読めていません。どの機械か確かめられないので、この機械には書きません")
    elif ident.get("cnc_id") != cnc_id:
        reasons.append("つながっている機械が控えを取ったときと違います（機械ごとの番号（CNC ID）が違います）")
    if not ident.get("supported"):
        reasons.append("この機種はまだ対応していません")
    w = writable_state(st, al, allowed_alarms=allowed, backup_info=backup_info,
                       cnc_id=ident.get("cnc_id"))
    reasons += [r for r in w["reasons"] if r not in reasons]
    return reasons, al


class WriteResult:
    """書込の結果。items の status:
      pending     まだ書いていない
      ok          書いて、読み戻しも合った
      stale       書く前の機械の値が土台と違う（書かずに止めた）
      failed      書けなかった（機械が断った）
      mismatch    戻り値は 0 だが読み戻しが違う
      unknown     時間切れ・接続切れで、書けたかどうか分からない
      refused     書ける状態でなくなっていた・ヘルパーが断った
    touched=True は「機械の値が変わったかもしれない」（元に戻す対象）。"""

    def __init__(self, items, cnc_id=None):
        self.items = [dict(it, status="pending", touched=False, before=None, after=None,
                           rc=None, note="") for it in items]
        self.cnc_id = cnc_id
        self.state = "ready"          # ready / running / done / stopped / cancelled
        self.stop_reason = None
        self.note = ""
        self.alarms_before = []
        self.alarms_after = []
        self.new_alarms = []
        self.power_cycle = False
        self.verify = None
        self.folder = None            # この結果（書込ジョブ.json）がある控えのフォルダ
        # 書込ジョブ.json から探して戻した結果のとき（find_pending_job）だけ入る
        self.job_kind = None          # "completed"（最後まで成功）/ "interrupted"（途中で止まった・不明）
        self.job_time = None          # 書いた日時の文字列
        self.job_age_days = None
        self.job_old = False          # OLD_JOB_DAYS 日以上前
        self.started_at = None
        self.finished_at = None

    def written(self):
        return [it for it in self.items if it["touched"]]

    def to_dict(self):
        d = dict(self.__dict__)
        d["items"] = [{k: v for k, v in it.items() if k != "undo_of"} for it in self.items]
        return d


def append_write_log(path, rows):
    """書込1件ごとに FOCAS書込ログ.csv へ追記する（rows は LOG_HEADER の順の並び）。"""
    if not path:
        return
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        new = not p.exists() or p.stat().st_size == 0
        with open(p, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(LOG_HEADER)
            w.writerows(rows)
    except OSError:
        pass            # 記録の失敗で書込を止めない（画面にも結果は出る）


def _save_job(path, res, ctx):
    if not path:
        return
    d = {"cnc_id": res.cnc_id, "state": res.state, "stop_reason": res.stop_reason,
         "started_at": res.started_at, "finished_at": res.finished_at,
         "power_cycle": res.power_cycle, "context": ctx,
         "verify": _verify_for_job(res.verify),
         "items": [{k: it.get(k) for k in ("no", "label", "old", "new", "key", "status",
                                           "touched", "before", "after", "rc", "note")}
                   for it in res.items]}
    try:
        Path(path).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _verify_for_job(v):
    """全行照合の結果を、書込ジョブ.json に残す形にする（None = 照合していない）。
    書いていないのに変わった番号（unexpected）は多いことがあるので、先頭 200 件と総数だけ残す。"""
    if not isinstance(v, dict):
        return None
    out = dict(v)
    un = list(out.get("unexpected") or [])
    out["unexpected"] = un[:200]
    out["unexpected_count"] = len(un)
    return out


def save_plan_file(folder, newtext):
    """書いたあとの機械の姿（newtext）を控えのフォルダに残す。区切りは LF のまま。"""
    p = Path(folder) / PLAN_NAME
    p.write_bytes(newtext.encode("cp932", errors="replace"))
    return p


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log_item(log_path, ctx, cnc_id, it, result_text):
    append_write_log(log_path, [[
        _now(), cnc_id or "", ctx.get("unit", ""), ctx.get("seiban", ""),
        f"{it['no']:05d}", it["label"], it["old"], it["new"],
        "" if it["rc"] is None else it["rc"], "" if it["after"] is None else it["after"],
        result_text]])


def _write_one(client, it, value, *, verified, expect_before, allowed, cnc_id, backup_info,
               base_alarms):
    """1件の流れ。戻り値 (結果, 説明, 増えたアラーム)。結果は ok / alarm / stale / failed /
    mismatch / unknown / refused。it に before / after / rc / touched を書き込む。

      1. 書ける状態か（毎回。CNC ID・リセット・非常停止・アラーム・PWE）
      2. 読む   今の値 == expect_before か（違えば書かずに止める）
      3. 書く   cnc_wrparam（戻り値が 0 以外なら止める）
      4. 読む   今の値 == value か
      5. アラームを読む  許した以外（と PW0000 以外）が増えたら止める
    """
    # 書込の途中は PW0000（電源の入れ直しの要求）が出てよい（設計 §7.7）。ほかは許したものだけ
    reasons, _al = _gate(client, cnc_id=cnc_id, backup_info=backup_info,
                         allowed=tuple(allowed) + (POWER_ALARM,))
    if reasons:
        return "refused", "書ける状態ではありません: " + " ／ ".join(reasons), []
    base = {_alarm_key(a) for a in base_alarms}
    cur, kind = _read_value(client, it)
    it["before"] = cur
    if not _same(kind, cur, expect_before):
        return "stale", (f"書く前の機械の値が土台と違います（機械 {cur}・控え {expect_before}）。"
                         "控えを取ったあとで機械の値が変わっています"), []
    try:
        res = client.call("param_write", {"no": it["no"], "label": it["label"], "value": value,
                                          "verified": verified})
        it["rc"] = res.get("rc", 0)
    except C.FocasError as e:
        it["rc"] = e.detail.get("rc")
        if e.kind in LOST_KINDS:
            it["touched"] = True                       # 書けたかもしれない
            return "unknown", (f"{e.message} 書けたかどうかは不明です。つなぎ直して、読み戻した値で"
                               "確かめてください（自動ではやり直しません）"), []
        if e.kind == "refused":                        # ヘルパーが書く前に断った
            return "refused", e.message, []
        note = e.message
        try:                                           # 断られても、値が変わっていないか見る
            now, k2 = _read_value(client, it)
            it["after"] = now
            if not _same(k2, now, expect_before):
                it["touched"] = True
                note += f" ただし機械の値が変わっています（今の値 {now}）"
        except C.FocasError:
            it["touched"] = True
            note += " 機械の値が確かめられません"
        return "failed", note, []
    it["touched"] = True
    try:
        now, k2 = _read_value(client, it)
    except C.FocasError as e:
        if e.kind in LOST_KINDS:
            return "unknown", f"書いたあとの読み戻しができませんでした。{e.message}", []
        return "mismatch", f"書いたあとの読み戻しに失敗しました。{e.message}", []
    it["after"] = now
    if not _same(k2, now, value):
        return "mismatch", f"読み戻しが書いた値と違います（書いた {value}・読み戻し {now}）", []
    new = [a for a in client.call("alarms")["alarms"] if _alarm_key(a) not in base]
    bad = [a for a in new if a.get("code") not in tuple(allowed) + (POWER_ALARM,)]
    if bad:
        return "alarm", ("許していないアラームが増えました: "
                         + "、".join(f"{a['code']}（種類 {a['type']}・番号 {a['no']}）{a['text']}"
                                    for a in bad)), new
    return "ok", "", new


def _safe_write_one(client, it, value, **kw):
    """_write_one の呼び出し。途中で接続が切れた・時間切れのときは、例外にせず結果にする
    （どこまで書いたかの記録を失わないため）。書く前に切れたなら断った扱い、書いたあとなら不明。"""
    try:
        return _write_one(client, it, value, **kw)
    except C.FocasError as e:
        if it.get("touched"):
            return "unknown", (f"{e.message} 書けたかどうかは不明です。つなぎ直して、読み戻した値で"
                               "確かめてください（自動ではやり直しません）"), []
        return "refused", f"書く前に機械との通信が止まりました。{e.message}", []


def _add_new(seen, new):
    have = {_alarm_key(x) for x in seen}
    for a in new:
        if _alarm_key(a) not in have:
            seen.append(a)
            have.add(_alarm_key(a))


def _finish_alarms(client, res):
    try:
        al_end = client.call("alarms")["alarms"]
    except C.FocasError as e:
        res.note = f"最後のアラーム確認ができませんでした: {e.message}"
        return
    res.alarms_after = list(al_end)
    _add_new(res.new_alarms, [a for a in al_end
                              if _alarm_key(a) not in {_alarm_key(x) for x in res.alarms_before}])


def run_write(client, items, *, cnc_id, backup_info, settings, family, ctx=None, newtext=None,
              types=None, cancel=None, progress=None, on_item=None, log_path=None, job_path=None,
              full_check=True, expected=None, unwritable=None):
    """書込の一覧 items（plan_writes の items）を1件ずつ書く。戻り値は WriteResult。

    書く前に、確認済みでない型・書けない値・書ける状態でないことがあれば、機械に1度も書かずに
    WriteRefused。1件でも止まったら残りは書かない。中止（cancel）は次の1件の前で止まる。
    expected（プレビューの『変わる行』の数）と unwritable（片側にしか無い番号）を渡すと、plan_writes の
    点検をここでもう一度する（件数がずれていたら、片側にしか無い番号があれば、1件も書かない）。
    全部書けたら、パラメータを丸ごと吸い出して newtext と全行比べる（full_check）。
    PW0000 が出たら power_cycle=True（電源を入れ直してください、と言うのは呼ぶ側。待たない）。
    """
    ctx = dict(ctx or {})
    prog = progress or (lambda d, t, n: None)
    stop = cancel or (lambda: False)
    ver = verified_list(settings, family)
    allowed = allowed_codes(settings)
    if not items:
        raise WriteRefused(["書くものがありません"])
    bad = []
    if expected is not None and len(items) + len(unwritable or ()) != expected:
        bad.append(f"プレビューの『変わる行』{expected} 件と、書く一覧 {len(items) + len(unwritable or ())} 件が"
                   "合いません（画面と中身がずれています）")
    if unwritable:
        bad.append(f"片側にしか無い番号があります（{len(unwritable)} 件）。機械に無い番号は書けません")
    for it in items:
        name = f"N{it['no']:05d} {it.get('label') or '-'}"
        if it.get("problems"):
            bad.append(f"{name}: " + "／".join(it["problems"]))
        elif it.get("key") not in ver:
            bad.append(f"{name} の型 {H.kind_label(it.get('key'))} は、この機種ではまだ書いたことがありません"
                       "（先に『同じ値を書く確認』をしてください）")
    if bad:
        raise WriteRefused(bad)
    reasons, al0 = _gate(client, cnc_id=cnc_id, backup_info=backup_info, allowed=allowed)
    if reasons:
        raise WriteRefused(reasons)

    res = WriteResult(items, cnc_id)
    res.state, res.started_at = "running", _now()
    res.alarms_before = list(al0)
    total = len(res.items)
    for i, it in enumerate(res.items):
        if stop():
            res.state, res.stop_reason = "cancelled", "中止しました（次の1件の前で止まりました）"
            break
        prog(i, total, f"N{it['no']:05d} {it['label'] or ''}")
        result, note, new = _safe_write_one(
            client, it, it["new"], verified=ver, expect_before=it["old"], allowed=allowed,
            cnc_id=cnc_id, backup_info=backup_info, base_alarms=res.alarms_before)
        _add_new(res.new_alarms, new)
        it["status"] = "ok" if result == "alarm" else result
        it["note"] = note
        _log_item(log_path, ctx, cnc_id, it, RESULT_TEXT.get(result, result))
        if on_item:
            on_item(i, it)
        if result != "ok":
            res.state, res.stop_reason = "stopped", f"N{it['no']:05d} {it['label'] or ''}: {note}"
            break
        _save_job(job_path, res, ctx)
    else:
        res.state = "done"
    _finish_alarms(client, res)
    res.power_cycle = any(is_power_alarm(a) for a in res.new_alarms)
    if res.state == "done" and full_check and newtext is not None:
        try:
            res.verify = verify_all(client, newtext, types or {}, res.items, progress=progress)
        except C.FocasError as e:
            res.verify = {"ok": False, "error": e.message}
    res.finished_at = _now()
    _save_job(job_path, res, ctx)
    return res


def verify_all(client, newtext, types, items, *, progress=None):
    """パラメータを丸ごと吸い出して newtext（書いたあとの予定）と全行比べる。

    書いた所が合っていることに加え、**書いていないのに変わった番号**を一覧で出す
    （積算カウンタ 6753・6754 のように動いて当然のものもあるが、隠さず出す。黙って除外しない）。
    """
    if progress:
        progress(0, 1, "パラメータを丸ごと読んで確かめています")
    data = C.FocasClient.decode_data(client.call("param_dump", {"kind": "param"}))
    now = fanuc_param.values_map(data.decode("cp932", errors="replace"))
    want = fanuc_param.values_map(newtext)
    wrote = {(f"{it['no']:05d}", it["label"]) for it in items if it.get("touched")}
    wrote_ng, unexpected, n_ok = [], [], 0
    keys = sorted(set(now) | set(want))
    for key in keys:
        a, b = want.get(key), now.get(key)
        try:
            no = int(key[0])
        except ValueError:
            no = -1
        kind = _kind_of_text(types, no, a if a is not None else b)
        eq = a is not None and b is not None and _same(kind, a, b)
        if key in wrote:
            if eq:
                n_ok += 1
            else:
                wrote_ng.append((key[0], key[1], a, b))
        elif not eq:
            unexpected.append((key[0], key[1], a, b))
    return {"ok": not wrote_ng, "checked": len(keys), "written_ok": n_ok,
            "written_ng": wrote_ng, "unexpected": unexpected}


def run_undo(client, result, *, cnc_id, backup_info, settings, family, ctx=None, cancel=None,
             progress=None, on_item=None, log_path=None, job_path=None, verified_extra=()):
    """書いた分（touched）を、逆の順で旧値に書き戻す。自動では呼ばない（ボタンから）。

    各件の直前の値が「書いた値」（または読み戻した値）でも「元の値」でもなければ止める
    （人が機械で変えたかもしれない）。もう元の値になっていれば何もしない。
    戻り値は新しい WriteResult（items は元に戻す対象。status が ok なら戻せた）。
    """
    ctx = dict(ctx or {})
    prog = progress or (lambda d, t, n: None)
    stop = cancel or (lambda: False)
    # 前回の、最後まで成功した書込（書込ジョブ.json から探した結果）は戻さない。
    # 今の画面で書いた直後の結果（job_kind が無い）だけ戻せる
    if getattr(result, "job_kind", None) == "completed":
        raise WriteRefused(["前回の、最後まで成功した書込は、ここからは戻しません。"
                            "そのあとに機械の値が変わっているかもしれないので、何も戻していません"])
    # verified_extra: 同じ値を書く確認が通らなかった型を、その1件だけ元に戻すときに渡す
    ver = verified_list(settings, family) + [k for k in verified_extra if k]
    allowed = allowed_codes(settings)
    targets = [it for it in reversed(result.items) if it.get("touched")]
    if not targets:
        raise WriteRefused(["元に戻すものがありません"])
    # 別の機械の結果を、今の機械に使わない（値が偶然合っていても、別の機械の書込は戻さない）
    if not getattr(result, "cnc_id", None) or result.cnc_id != cnc_id:
        raise WriteRefused(["この結果は、いまつながっている機械のものではありません"
                            "（結果の機械ごとの番号（CNC ID）が違う、または分かりません）。何も戻していません"])
    # 書いたあとに PW0000（電源の入れ直しの要求）が出ているのは普通。出ていても元に戻せる
    # （1件ごとの _write_one も同じ。ここだけ断ると、書いた値が機械に残ったままになる）
    reasons, al0 = _gate(client, cnc_id=cnc_id, backup_info=backup_info,
                         allowed=tuple(allowed) + (POWER_ALARM,))
    if reasons:
        raise WriteRefused(reasons)
    out = WriteResult([dict(it, old=it["new"], new=it["old"], undo_of=it) for it in targets], cnc_id)
    out.state, out.started_at = "running", _now()
    out.alarms_before = list(al0)
    total = len(out.items)
    uctx = dict(ctx, undo=True)

    def done_item(i, it, status, note):
        it["status"], it["note"] = status, note
        if on_item:
            on_item(i, it)

    for i, it in enumerate(out.items):
        if stop():
            out.state, out.stop_reason = "cancelled", "中止しました（次の1件の前で止まりました）"
            break
        src = it["undo_of"]
        name = f"N{it['no']:05d} {it['label'] or ''}"
        prog(i, total, name + "（元に戻す）")
        try:
            cur, kind = _read_value(client, it)
        except C.FocasError as e:
            done_item(i, it, "unknown", e.message)
            out.state, out.stop_reason = "stopped", f"{name}: {e.message}"
            break
        if _same(kind, cur, src["old"]):
            it["before"], it["after"] = cur, cur
            done_item(i, it, "ok", "もう元の値です")
            continue
        wrote_vals = [v for v in (src["new"], src.get("after")) if v is not None]
        if not any(_same(kind, cur, v) for v in wrote_vals):
            note = (f"今の機械の値（{cur}）が、書いた値でも元の値（{src['old']}）でもありません。"
                    "人が機械で変えたかもしれないので止めます")
            done_item(i, it, "stale", note)
            out.state, out.stop_reason = "stopped", f"{name}: {note}"
            break
        result_, note, new = _safe_write_one(
            client, it, it["new"], verified=ver, expect_before=cur, allowed=allowed, cnc_id=cnc_id,
            backup_info=backup_info, base_alarms=out.alarms_before)
        _add_new(out.new_alarms, new)
        _log_item(log_path, ctx, cnc_id, it,
                  "元に戻した" if result_ == "ok" else f"元に戻せない({result_})")
        done_item(i, it, "ok" if result_ == "alarm" else result_, note)
        if result_ != "ok":
            out.state, out.stop_reason = "stopped", f"{name}: {note}"
            break
        _save_job(job_path, out, uctx)
    else:
        out.state = "done"
    _finish_alarms(client, out)
    out.power_cycle = any(is_power_alarm(a) for a in list(out.new_alarms) + list(out.alarms_after))
    out.finished_at = _now()
    _save_job(job_path, out, uctx)
    return out


def verify_kind(client, it, *, cnc_id, backup_info, settings, family, ctx=None, log_path=None):
    """同じ値を書く確認。it の番号の今の値を、そのまま書いて、読み戻して、アラームが増えないことを見る。

    値は変わらない。ただし書込なので、呼ぶ側は利用者の確認ボタンを通すこと。通ったら
    add_verified_kind で表に足す（ここでは足さない）。戻り値 {"ok","key","note",...}。
    """
    ctx = dict(ctx or {})
    allowed = allowed_codes(settings)
    key = it.get("key")
    if not key or it.get("problems"):
        raise WriteRefused(["この番号は確かめられません（型が分からない・書けない行です）"])
    reasons, al0 = _gate(client, cnc_id=cnc_id, backup_info=backup_info, allowed=allowed)
    if reasons:
        raise WriteRefused(reasons)
    cur, kind = _read_value(client, it)
    probe = dict(it, before=None, after=None, rc=None, touched=False, old=cur, new=cur)
    base = {"key": key, "no": it["no"], "label": it["label"], "value": cur, "at": _now(),
            "cnc_id": cnc_id, "item": probe, "changed": False, "original": cur, "now": None}
    # 書く前に「いま読んだ値 == 控えの値」を比べる。違えば書かずに止める（控えのあとで機械の値が
    # 変わっている。『今と同じ値を書く』つもりが、別の値を確定させてしまうのを防ぐ）
    if it.get("old") is not None and not _same(kind, cur, it["old"]):
        note = (f"書く前に読んだ機械の値（{cur}）が、控えの値（{it['old']}）と違います。"
                "控えのあとで機械の値が変わっています。何も書かずに止めました。控えを取り直してください")
        probe["before"] = cur
        _log_item(log_path, ctx, cnc_id, probe, f"同値書込の確認 {key}: 通らない(stale)")
        return dict(base, ok=False, result="stale", note=note)
    result, note, _new = _safe_write_one(
        client, probe, cur, verified=verified_list(settings, family) + [key], expect_before=cur,
        allowed=allowed, cnc_id=cnc_id, backup_info=backup_info, base_alarms=al0)
    ok = result == "ok"
    probe["status"] = "ok" if ok else result
    probe["note"] = note
    _log_item(log_path, ctx, cnc_id, probe,
              f"同値書込の確認 {key}: " + ("通った" if ok else f"通らない({result})"))
    after = probe.get("after")
    changed = bool(probe["touched"] and after is not None and not _same(kind, after, cur))
    return dict(base, ok=ok, result=result, note=note, touched=probe["touched"], changed=changed,
                now=after)


def verify_undo_result(out, cnc_id=None):
    """同じ値を書く確認で、機械の値が変わった（かもしれない）ときの、その1件を元に戻す用の結果。
    run_undo にそのまま渡せる（元の値 = 確認の前に読んだ値）。"""
    src = out["item"]
    res = WriteResult([src], cnc_id)
    res.items[0].update(touched=True, status="mismatch", before=src.get("before"),
                        after=src.get("after"), rc=src.get("rc"))
    res.state = "stopped"
    return res


def resolve_unknown(client, it, connect_args, *, cnc_id):
    """書けたかどうかが不明なとき（時間切れ・接続切れ）。つなぎ直して、読み戻した値で決める。

    戻り値 ("not_written" 元の値のまま / "written" 書く値になっている / "other" どちらでもない
    （止めて人に見せる）, 読み戻した値)。CNC ID が違う機械につながったら WriteRefused。
    自動でやり直しはしない。
    """
    client.call("connect", connect_args)
    ident = client.call("identify")
    if not cnc_id or ident.get("cnc_id") != cnc_id:
        raise WriteRefused(["つなぎ直した機械が、控えを取った機械と同じか確かめられません"
                            "（機械ごとの番号（CNC ID）が違う、または読めません）"])
    cur, kind = _read_value(client, it)
    if _same(kind, cur, it["new"]):
        return "written", cur
    if _same(kind, cur, it["old"]):
        return "not_written", cur
    return "other", cur


def load_job(path):
    """書込ジョブ.json を読み戻して WriteResult にする（画面を閉じたあとでも、元に戻せるように）。
    読めなければ None。"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        res = WriteResult([], d.get("cnc_id"))
        for it in d.get("items") or []:
            base = {"problems": [], "type_code": None, "kind": None, "verified": True,
                    "status": "pending", "touched": False, "before": None, "after": None,
                    "rc": None, "note": ""}
            base.update(it)
            res.items.append(base)
        res.state, res.stop_reason = d.get("state") or "stopped", d.get("stop_reason")
        res.started_at, res.finished_at = d.get("started_at"), d.get("finished_at")
        res.power_cycle = bool(d.get("power_cycle"))
        v = d.get("verify")
        res.verify = v if isinstance(v, dict) else None
        res.folder = str(Path(path).parent)
        return res
    except (OSError, ValueError, TypeError, AttributeError):
        return None


OLD_JOB_DAYS = 7      # これより古い途中のジョブは、元に戻す前に確認を1回はさむ


def _job_time(res, mtime):
    t = res.started_at or res.finished_at
    if t:
        return str(t)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))


def _job_epoch(res, mtime):
    """そのジョブの時刻（秒）。記録の finished_at を先に使い、無い・読めないときは started_at、
    それも読めないときだけファイルの更新時刻。（ファイルの更新時刻は、コピー・同期で新しくなる）"""
    for t in (res.finished_at, res.started_at):
        if t:
            try:
                return time.mktime(time.strptime(str(t), "%Y-%m-%d %H:%M:%S"))
            except (ValueError, OverflowError):
                pass
    return mtime


def _verify_failed(res):
    v = getattr(res, "verify", None)
    return isinstance(v, dict) and not v.get("ok")


def find_pending_job(root, cnc_id, alarms=None, now=None):
    """控えの置き場（root）の下から、この機械（CNC ID）の一番新しい、機械に書いたジョブを探す。
    1件も書かなかったジョブ（書く前に止まった）と、もう元に戻し済みのジョブは飛ばして、
    その前のジョブを探す。機械に書いたものが残っていて、まだ元に戻していなければ WriteResult、無ければ None。
    CNC ID が空のときは探さない（どの機械か分からない）。新しさは記録の時刻（_job_epoch）で決める。

    戻した結果には job_kind（completed = 最後まで成功した書込・interrupted = 途中で止まった/不明、
    または元に戻す途中・undo_unreadable = 元に戻した記録があるのに読めない）・job_time（書いた日時）・
    job_age_days・job_old を付ける。全行照合で不一致だった書込は completed にしない。
    alarms（今の機械のアラーム）を渡すと、PW0000 が今出ていないのに古い記録が power_cycle のときは
    power_cycle を False にする（古い『電源を入れ直して』を出さない）。"""
    if not cnc_id or not root or not Path(root).is_dir():
        return None
    jobs = []
    for p in Path(root).rglob(JOB_NAME):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        res = load_job(p)
        if res is None or res.cnc_id != cnc_id:
            continue
        jobs.append((_job_epoch(res, mtime), mtime, p, res))
    jobs.sort(key=lambda t: (t[0], t[1], str(t[2])), reverse=True)
    for epoch, mtime, p, res in jobs:
        if not res.written():
            continue                  # 1件も書いていない。その前のジョブを探す
        upath = p.parent / ("元に戻す_" + JOB_NAME)
        undo = load_job(upath)
        if undo is not None and undo.state == "done":
            continue                  # 元に戻し済み。その前の途中のジョブを探し続ける
        unreadable = undo is None and upath.exists()
        completed = (res.state == "done" and undo is None and not unreadable
                     and not _verify_failed(res)
                     and all(it["status"] == "ok" for it in res.written()))
        res.job_kind = ("completed" if completed
                        else "undo_unreadable" if unreadable else "interrupted")
        res.job_time = _job_time(res, mtime)
        age = ((now if now is not None else time.time()) - epoch) / 86400.0
        res.job_age_days = max(0, int(age))
        res.job_old = age >= OLD_JOB_DAYS
        if (alarms is not None and res.power_cycle
                and not any(is_power_alarm(a) for a in alarms)):
            res.power_cycle = False
        return res
    return None


def describe_pending_job(res):
    """find_pending_job の結果を、画面に出す前置きの文にする。"""
    n = len(res.written())
    when = res.job_time or "日時不明"
    if res.job_kind == "completed":
        return (f"前回（{when}）に {n} 件を書きました。最後まで書けています。"
                "そのあとの機械の値は確かめていません。")
    if res.job_kind == "undo_unreadable":
        text = (f"前回（{when}）の書込 {n} 件について、元に戻したかどうかは分かりません"
                "（元に戻した記録が読めません）。機械の今の値も確かめていません。"
                "［書いた分を元に戻す］で戻せます（今の値が書いた値でも元の値でもない番号は、止めて戻しません）。")
    elif _verify_failed(res):
        text = (f"前回（{when}）の書込 {n} 件は、書いたあとの全行の照合で合わない所がありました"
                "（最後まで成功とは言えません）。機械に書いたままかもしれません。［書いた分を元に戻す］で戻せます。")
    else:
        text = (f"前回（{when}）の書込のうち {n} 件が、機械に書いたままです（途中で止まったため、元の値には戻っていません）。"
                "［書いた分を元に戻す］で戻せます。")
    if res.job_old:
        text += (f"\n※ {res.job_age_days} 日以上前の記録です。そのあとに機械の値が変わっているかもしれないので、"
                 "元に戻す前に確認を出します。")
    return text


def reread_verify_undo(client, res, connect_args, *, cnc_id):
    """同じ値を書く確認で、読み戻しができなかった（通信切れ）1件。つなぎ直して今の値を読み、
    確認の前の値のままなら "unchanged"、違えば今の値を after に入れて "changed"（元に戻せる）にする。
    CNC ID が違う機械なら WriteRefused。書かない。"""
    if not getattr(res, "cnc_id", None) or res.cnc_id != cnc_id:
        raise WriteRefused(["この結果は、いまつながっている機械のものではありません"])
    client.call("connect", connect_args)
    ident = client.call("identify")
    if not cnc_id or ident.get("cnc_id") != cnc_id:
        raise WriteRefused(["つなぎ直した機械が、控えを取った機械と同じか確かめられません"])
    it = res.items[0]
    cur, kind = _read_value(client, it)
    if _same(kind, cur, it["old"]):
        return "unchanged", cur
    it["after"] = cur
    return "changed", cur


def basic_leftovers(raw, basic_text, plan, types, axes):
    """今回は書かない番号のうち、製品の軸で「機械の値」と「BASIC の値」が違うもの。
    製品の値が BASIC と同じ番号は、製品の値としては出てこないので書かれない。機械が前の製品の値の
    ままかもしれない、という注意のため。書く内容は変えない（土台は今の機械）。
    今回の一覧（プレビューの行）に出ている番号は除く。戻り値 [(番号, ラベル, 機械の値, BASICの値)]。"""
    if not basic_text or not axes:
        return []
    labels = {f"A{int(a)}" for a in axes}
    handled = set()
    for r in (plan or {}).get("rows") or []:
        try:
            handled.add(int(str(r[0]).split("(")[0]))
        except ValueError:
            pass
    machine = fanuc_param.values_map(raw)
    basic = fanuc_param.values_map(basic_text)
    out = []
    for (num, label) in sorted(basic):
        if label not in labels or (num, label) not in machine:
            continue
        try:
            no = int(num)
        except ValueError:
            continue
        if no in handled:
            continue
        kind = _kind_of_text(types, no, machine[(num, label)])
        if not _same(kind, machine[(num, label)], basic[(num, label)]):
            out.append((no, label, machine[(num, label)], basic[(num, label)]))
    return out


STATUS_TEXT = {"pending": "（まだ書いていません）", "ok": "書けた・読み戻しOK", "stale": "書かずに止めた",
               "failed": "書けなかった", "mismatch": "読み戻しが違う", "unknown": "不明",
               "refused": "断った"}


def describe_result(res, *, undo=False):
    """書込（または元に戻す）の結果を、画面に出す文の並びにする（Qt 非依存）。

    書込の成否とアラームは分けて言う（電源を入れ直したから出るアラームを、書込の失敗とは言わない）。
    書いていないのに変わった番号は隠さず出す。
    """
    what = "元に戻す" if undo else "書込"
    n_ok = sum(1 for it in res.items if it["status"] == "ok")
    total = len(res.items)
    state = {"done": "最後までできました", "stopped": "途中で止まりました",
             "cancelled": "中止しました", "running": "実行中", "ready": "まだ始めていません"}
    lines = [f"{what}: 全 {total} 件中 {n_ok} 件が「書けた・読み戻しOK」です（{state.get(res.state, res.state)}）。"]
    if res.stop_reason:
        lines.append(f"止まった理由: {res.stop_reason}")
    unknown = [it for it in res.items if it["status"] == "unknown"]
    if unknown:
        lines.append("書けたかどうかが不明な番号があります: "
                     + "、".join(f"N{it['no']:05d} {it['label'] or '-'}" for it in unknown)
                     + "。『つなぎ直して確かめる』を押すと、読み戻した値で決めます（自動ではやり直しません）")
    pending = sum(1 for it in res.items if it["status"] == "pending")
    if pending and res.state != "done":
        lines.append(f"書いていない番号が {pending} 件残っています（途中で止めたため）。")
    v = res.verify
    if v is not None and not undo:
        if v.get("error"):
            lines.append(f"全行の照合ができませんでした: {v['error']}")
        else:
            lines.append(f"パラメータを丸ごと読み直して {v['checked']} 番号を照合: 書いた {v['written_ok']} 件は"
                         + ("すべて合っています。" if v["ok"] else
                            f" {len(v['written_ng'])} 件が合っていません（NG）。"))
            for no, lab, want, got in v["written_ng"][:20]:
                lines.append(f"  NG N{no} {lab or '-'}: 予定 {want}・機械 {got}")
            if v["unexpected"]:
                lines.append(f"書いていないのに変わっていた番号: {len(v['unexpected'])} 件"
                             "（積算カウンタのように動いて当然の番号もあります。隠さず出しています）")
                for no, lab, want, got in v["unexpected"][:30]:
                    lines.append(f"  N{no} {lab or '-'}: 控え {want}・機械 {got}")
                if len(v["unexpected"]) > 30:
                    lines.append(f"  …ほか {len(v['unexpected']) - 30} 件")
            else:
                lines.append("書いていないのに変わった番号は、ありませんでした。")
    if res.new_alarms:
        lines.append(f"{what}のあとに増えたアラームが {len(res.new_alarms)} 件あります"
                     "（書込が合っているかどうかとは別の話です）:")
        for a in res.new_alarms:
            lines.append(f"  {a.get('code')}（種類 {a.get('type')}・番号 {a.get('no')}）{a.get('text', '')}")
    elif res.alarms_after is not None and not res.note:
        lines.append(f"{what}のあとに増えたアラームは、ありません。")
    if res.note:
        lines.append(res.note)
    if res.power_cycle:
        lines.append("PW0000 が出ています。機械の電源を入れ直してください。 入れ直したあとは、"
                     "控えを取り直して確かめてください（電源を待って自動で確かめる機能は、まだありません）。")
    # 書いたあとの「PWE を 0 に戻してください」は出さない（2026-11-24 利用者の決定:
    # 現場ではパラメータ書込は ON のままがふつうで、毎回出ると邪魔）。
    # 書けないとき（SW0100 が無い＝PWE が OFF）は writable_state が理由として出す。
    return lines


# ---------------------------------------------------------------------------
# 測定プログラムを機械へ直接書く（FOCAS。2026-11-24）
# ---------------------------------------------------------------------------
# 手順（2026-11-22 実機で確認した書込・削除。docs/FOCAS_実機確認と要件_20261122.md §3）:
#   控えを取る（機械のプログラム一覧ごと）→ 点検（状態・同じ番号・容量）→ 人が承認 → 書く → 読み戻して照合。
# 同じ番号が機械にあれば止まる。上書きは人が承認した（overwrite=True）ときだけ。
# 書く中身は fanuc.nc_bytes() の出力（LF CR CR 区切りなど）のまま。1バイトも変えない。
# 実機で通したのは LF 区切りの形だけ。LF CR CR のまま FOCAS で書いて入るかは未確認。

DEFAULT_PROG_FOLDER = "//CNC_MEM/USER/PATH1/"


class ProgramSplitError(ValueError):
    pass


def split_programs(nc, eob):
    """nc_bytes の出力（先頭・末尾が %）を O 番号ごとに分ける。戻り値 [{number, wire, expect}]。

    wire は機械へ渡す形（実機で通した形: 先頭が区切り・各ブロックは区切りで終わる・末尾が %）。
    expect は読み戻しで返るはずの形（実機で「% ＋ 送ったもの」が 1 バイト一致した）。
    各ブロックの中身は 1 バイトも変えない（分けるだけ）。
    """
    eob = eob.encode("ascii") if isinstance(eob, str) else bytes(eob)
    nc = bytes(nc)
    if not eob or not nc.startswith(b"%") or not nc.endswith(b"%" + eob):
        raise ProgramSplitError("先頭と末尾が % のプログラムではありません")
    body = nc[1:-len(eob) - 1]                       # 先頭の % と、末尾の「% ＋ 区切り」を除く
    blocks = body.split(eob)
    if blocks and blocks[0] == b"":
        blocks = blocks[1:]
    if blocks and blocks[-1] == b"":
        blocks = blocks[:-1]
    progs, cur = [], None
    for b in blocks:
        m = re.match(rb"O(\d{1,8})(?!\d)", b)
        if m:
            cur = {"number": int(m.group(1)), "blocks": []}
            progs.append(cur)
        if cur is None:
            raise ProgramSplitError("O番号（プログラム番号）より前に行があります")
        cur["blocks"].append(b)
    if not progs:
        raise ProgramSplitError("O番号（プログラム番号）の行がありません")
    seen = set()
    out = []
    for p in progs:
        if p["number"] in seen:
            raise ProgramSplitError(f"O{p['number']} が2回出てきます")
        seen.add(p["number"])
        wire = eob + b"".join(b + eob for b in p["blocks"]) + b"%"
        out.append({"number": p["number"], "wire": wire, "expect": b"%" + wire})
    return out


def _find_existing(client, folder, number):
    tree = client.call("prog_list", {"path": folder})
    if tree.get("ok") is False or tree.get("rc_all") != H.ALLDIR_END_RC:
        raise WriteRefused(["機械のプログラム一覧を最後まで読めませんでした。同じ番号があるか確かめられないので書きません"])
    for p in tree.get("programs", []):
        m = re.fullmatch(r"[Oo]0*(\d+)", p["name"])
        if m and int(m.group(1)) == number:
            return p
    return None


def check_program_send(client, nc, eob, *, folder=DEFAULT_PROG_FOLDER, cnc_id=None, backup_info=None,
                       allowed=DEFAULT_ALLOWED_ALARMS):
    """書く前の点検（読むだけ）。戻り値 dict:
      programs: [{number, name, size, exists, existing}]  機械に同じ番号があるか
      reasons:  書けない理由（状態・控え・容量・機械の取り違え）。空なら書ける
      overwrite_needed: 同じ番号があって、上書きの承認が要る
    同じ番号があること自体は「書けない理由」にはしない（人が上書きを承認すれば書ける）。"""
    progs = split_programs(nc, eob)
    ident = client.call("identify")
    st = client.call("status")
    al = client.call("alarms")["alarms"]
    reasons = []
    if not cnc_id or not ident.get("cnc_id"):
        reasons.append("機械ごとの番号（CNC ID）が読めていません。どの機械か確かめられないので、この機械には書きません")
    elif ident.get("cnc_id") != cnc_id:
        reasons.append("つながっている機械が控えを取ったときと違います（機械ごとの番号（CNC ID）が違います）")
    if not ident.get("supported"):
        reasons.append("この機種はまだ対応していません")
    w = writable_state(st, al, allowed_alarms=allowed, backup_info=backup_info,
                       cnc_id=ident.get("cnc_id"), for_params=False)
    reasons += [r for r in w["reasons"] if r not in reasons]
    info = client.call("prog_info")
    rows = []
    for p in progs:
        ex = _find_existing(client, folder, p["number"])
        rows.append({"number": p["number"], "name": f"O{p['number']}", "size": len(p["wire"]),
                     "exists": ex is not None, "existing": ex})
    new_count = sum(1 for r in rows if not r["exists"])
    if new_count > info.get("remaining", 0):
        reasons.append(f"登録できる残り本数が足りません（残り {info.get('remaining')} 本・新しく入れるのは {new_count} 本）。"
                       "空きメモリがあっても、本数の枠が 0 だと 1 本も入りません")
    need = sum(r["size"] for r in rows if not r["exists"])
    if need > info.get("unused", 0):
        reasons.append(f"空きメモリが足りません（空き {info.get('unused')}・必要 {need}）")
    return {"programs": rows, "reasons": reasons, "overwrite_needed": any(r["exists"] for r in rows),
            "folder": folder, "cnc_id": ident.get("cnc_id"), "ident": ident}


def compare_readback(sent, got):
    """読み戻しの比べ方（設計 §8.4）。(区分, 説明)。区分: exact / padded / differs"""
    if got == sent:
        return "exact", "完全に一致"
    sa, sb = sent.split(b"\n"), got.split(b"\n")
    a = [x.rstrip(b" ") for x in sa]
    b = [x.rstrip(b" ") for x in sb]
    if a == b:
        n = sum(1 for x, y in zip(sa, sb) if x != y)
        return "padded", f"空白の詰め物を除いて一致（{n}か所）"
    for i, (x, y) in enumerate(zip(a, b), 1):
        if x != y:
            return "differs", f"違います（最初の違いは {i} 行目）"
    return "differs", f"違います（長さが違います: 送った {len(sent)}・読み戻し {len(got)}）"


def run_program_send(client, nc, eob, *, approved, overwrite=False, folder=DEFAULT_PROG_FOLDER,
                     cnc_id=None, backup_info=None, allowed=DEFAULT_ALLOWED_ALARMS, progress=None):
    """人が承認したあとに書く。approved が True でなければ何も書かない（WriteRefused）。
    書く直前にもう一度点検する。同じ番号があって overwrite でなければ書かない。
    1本ずつ「書く → 読み戻して照合」。戻り値 {"items": [...], "ok": bool}。"""
    if approved is not True:
        raise WriteRefused(["人の承認がありません。書きません"])
    chk = check_program_send(client, nc, eob, folder=folder, cnc_id=cnc_id, backup_info=backup_info,
                             allowed=allowed)
    if chk["reasons"]:
        raise WriteRefused(chk["reasons"])
    if chk["overwrite_needed"] and overwrite is not True:
        raise WriteRefused(["機械に同じ番号があります: "
                            + "・".join(r["name"] for r in chk["programs"] if r["exists"])
                            + "。上書きの承認がありません。書きません"])
    progs = split_programs(nc, eob)
    items = []
    for i, p in enumerate(progs):
        if progress:
            progress(i, len(progs), f"O{p['number']}")
        row = chk["programs"][i]
        it = {"number": p["number"], "name": row["name"], "existed": row["exists"], "written": False,
              "deleted": False, "match": None, "match_text": "", "note": ""}
        items.append(it)
        try:
            res = client.call("prog_write", {
                "folder": folder, "number": p["number"], "overwrite": bool(row["exists"]),
                "data": base64.b64encode(p["wire"]).decode("ascii"),
                "sha256": hashlib.sha256(p["wire"]).hexdigest()})
        except C.FocasError as e:
            it["note"] = f"書けませんでした: {e.message}"
            if e.kind in ("lost", "timeout", "not_connected"):
                it["note"] += "（途中で止まりました。機械に入ったかは、つなぎ直して一覧で確かめてください）"
            return {"items": items, "ok": False, "folder": folder}
        if not res.get("written"):
            it["note"] = "書きませんでした（機械に同じ番号があります）"
            return {"items": items, "ok": False, "folder": folder}
        it["written"], it["deleted"] = True, bool(res.get("deleted"))
        try:
            back = C.FocasClient.decode_data(client.call("prog_read", {"path": res["path"]}))
        except C.FocasError as e:
            it["match_text"] = f"読み戻せませんでした: {e.message}"
            continue
        kind, text = compare_readback(p["expect"], back)
        it["match"], it["match_text"] = kind != "differs", text
    if progress:
        progress(len(progs), len(progs), "終わりました")
    ok = len(items) == len(progs) and all(x["written"] and x["match"] for x in items)
    return {"items": items, "ok": ok, "folder": folder}


def describe_program_send(res):
    out = []
    for it in res["items"]:
        if it["written"]:
            head = f"{it['name']}: 書きました" + ("（同じ番号を消して入れ替えました）" if it.get("deleted") else "")
            out.append(head + "・読み戻し: " + (it["match_text"] or "（未確認）"))
        else:
            out.append(f"{it['name']}: {it['note']}")
    return out
