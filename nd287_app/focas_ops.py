# -*- coding: utf-8 -*-
"""FOCAS の業務の流れ（Qt 非依存）。段3では「書ける状態の判定」「自己診断」「止まっている所の診断」まで。

設計: docs/設計_FOCAS基本.md §6.2・§7.7・§12-A。書込は作っていない（段4以降）。
"""

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import fanuc_param
from . import focas_backup as B
from . import focas_client as C

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
    - 控え: この回に取った控えが「完全」で、いまつながっている機械（CNC ID）と同じ。
    - PWE: パラメータを書くときは SW0100 が出ていること（アプリは PWE を触らない）。
    モード（MDI・編集など）では止めない（どのモードで書けるかは確かめていないため、
    推測で禁止しない。戻り値 12＝モード違いが出たらそのとき案内する）。
    """
    reasons = []
    v = (status or {}).get("values") or {}
    t = (status or {}).get("text") or {}
    if not v:
        reasons.append("機械の状態を読めていません")
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
    elif cnc_id and backup_info.get("cnc_id") != cnc_id:
        reasons.append("控えは別の機械のものです（CNC ID が違います）")
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
                     f"CNC ID {ident.get('cnc_id')}",
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
