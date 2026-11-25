# -*- coding: utf-8 -*-
"""はじめてモード「機械に入れる」の中身（Qt なし。画面なしで試験できる）。

かんたん作成の窓を中に入れる代わりに、短い画面を順に出す（easy_machine_in_ui.py）。
決め方はここ（新しい決め方は作らない。今ある部品を順に呼ぶ）:
  順番       受注伝票番号 → 製品 → 号機（マスタで自動）→ つなぐ → 機械を読んで号機・軸を確かめる → 書く。
             どの制御装置かは製品が決まってから分かるので、つなぐのは号機が決まったあと
             （すでにつながっている機械があれば、先に読んで、その号機を使う＝例外の道）
  つなぐ     読むだけ（identify・各軸の 2020/2165・状態・アラーム）。号機は machine_decide.identify_unit
  決める     軸は machine_decide.choose_axes。モーターは「軸を決めたあと、その軸の 2020 が製品と同じか」
  控え・一覧 focas_backup.take_backup（読むだけ）→ cli_write と同じ plan_writes
  書く       書込の許可は SW0100 では判断しない。先に「書き試し」（今と同じ値を1件書いて読み戻す。人が押す）。
             機械が断ったら（戻り値 7）止める。ほかの点検（非常停止だけ・運転中でない・CNC ID・控え・許したアラーム）は全部残す。
  確かめる   電源の入れ直しのあと、読み戻す（N1815 の APZ は、原点復帰のあと機械が 1 に戻すので、そのときは「合っている」）
機械には、書く画面の「書く」「書き試し」を押したときだけ書く。ここの関数は、呼ばれたとき以外は何もしない。
"""
import re
import time

from . import (controllers, fanuc, focas_backup, focas_client, focas_find, focas_ops, machine_decide, masters,
               wizard_probe)
from . import wizard_programs as WP

AXES = controllers.AXES
BATTERY_CODES = {"DS0306": "切れています", "DS0307": "弱っています", "DS0308": "弱っています"}
POWER_TYPE_BIT = 0x40            # 型コードのこの印があれば、書いたあと電源の入れ直しが要る（利用者の指示。実機での意味は未確認）
ORIGIN_NO = 1815                 # 原点の設定（APZ＝bit4。「位置を決めた」印。機械が決めるビット）
APZ_BIT = 4

WRITE_REFUSED_TEXT = ("機械がパラメータ書込を断りました。機械のセッティング画面で『パラメータ書込』を 1 にしてください")


class EasyError(Exception):
    """利用者に出す一文（やさしい言葉）。"""


# ------------------------------------------------------------------ アラームの無視
def ignore_list(settings, unit):
    return focas_ops.ignored_codes(settings, unit)


def split_alarms(alarms, settings, unit):
    """(出すアラーム, 無視中のアラーム)。"""
    ign = set(ignore_list(settings, unit))
    shown, hidden = [], []
    for a in alarms or []:
        (hidden if str(a.get("code") or "").upper() in ign else shown).append(a)
    return shown, hidden


def add_ignore(settings, unit, code):
    """［この機械では無視する］。unit が空なら "*"（全部の機械）。もう入っていれば何もしない。"""
    key = str(unit) if str(unit or "").strip() else "*"
    d = dict(settings.get("alarm_ignore") or {})
    cur = [str(c).upper() for c in d.get(key) or []]
    if str(code).upper() not in cur:
        cur.append(str(code).upper())
    d[key] = cur
    settings["alarm_ignore"] = d
    return key


def remove_ignore(settings, unit, code):
    """［無視をやめる］。その号機の分と "*" の分の両方から外す。"""
    d = {k: [c for c in v if str(c).upper() != str(code).upper()]
         for k, v in dict(settings.get("alarm_ignore") or {}).items() if str(k) in (str(unit), "*")}
    full = dict(settings.get("alarm_ignore") or {})
    full.update(d)
    settings["alarm_ignore"] = {k: v for k, v in full.items() if v}
    return settings["alarm_ignore"]


def axis_letter(a):
    """アラームの軸番号（1 から）→ 軸の文字。無ければ ''。"""
    try:
        n = int((a or {}).get("axis") or 0)
    except (TypeError, ValueError):
        return ""
    return AXES[n - 1] if 1 <= n <= len(AXES) else ""


def battery_lines(alarms):
    """電池のアラーム（DS0306〜0308）→ 利用者向けの文。"""
    out = []
    for a in alarms or []:
        code = str(a.get("code") or "").upper()
        if code in BATTERY_CODES:
            ax = axis_letter(a)
            where = f"{ax}軸の位置を覚える電池" if ax else "位置を覚える電池"
            tail = "が切れています。すぐ交換してください" if code == "DS0306" else "が弱っています。早めに交換してください"
            out.append(f"{where}{tail}")
    return out


def other_alarm_lines(alarms):
    """電池以外で出ているアラームの一行。"""
    return [f"{a.get('code')} {a.get('text', '')}".strip() for a in alarms or []
            if str(a.get("code") or "").upper() not in BATTERY_CODES]


# ------------------------------------------------------------------ 製品から号機を決める（機械につなぐ前）
def needs_line(products):
    """「回転 160A・傾斜 80A（200V）」。必要な容量を1行で。"""
    parts = [f"{p.get('kind') or ''} {p.get('cap') or '容量不明'}".strip() for p in products]
    volts = sorted({str(p.get("voltage") or "") for p in products if p.get("voltage")})
    return "・".join(parts) + (f"（{'・'.join(volts)}）" if volts else "")


def ledger_ip(settings, unit):
    """台帳にある、その号機の機械の IP（いちばん新しいもの）。無ければ ''。"""
    try:
        reg = focas_find.load_registry(focas_find.registry_path(settings))
    except Exception:
        return ""
    rows = sorted((r for r in reg.values() if str(r.get("unit")) == str(unit) and r.get("ip")),
                  key=lambda r: r.get("when", ""), reverse=True)
    return rows[0]["ip"] if rows else ""


def lan_guide(settings, unit):
    """機械側の LAN のつなぎ方（つなぐ画面の案内）。"""
    ip = ledger_ip(settings, unit)
    lines = [f"{unit}号機の機械の電源を入れ、LAN ケーブルを PC につないでください。",
             "機械の LAN（イーサネット）の設定は済んでいるものとします。つないだら［つなぐ］を押します（読むだけです。何も書きません）。"]
    if ip:
        lines.append(f"前につないだときの IP: {ip}")
    return lines


def fits_unit(idn, unit):
    """つないだ機械（identify の結果）が、決めた号機か。戻り値 "ok"（決めた号機）・"other"（別の号機）・"unknown"（登録がない）。"""
    if idn.get("unknown"):
        return "unknown"
    return "ok" if str(unit) in [str(u) for u in idn.get("units") or []] else "other"


# ------------------------------------------------------------------ つなぐ（読むだけ）
def read_machine(settings, client_factory, *, port_open=None, on_client=None):
    """機械を読む（書かない・控えも取らない）。戻り値 {"ok","error","probe","status","alarms","ident"}。
    probe は wizard_probe.read_machine と同じ形（号機は CNC ID が台帳にあるときだけ入る）。"""
    port_open = port_open or focas_find.port_open
    try:
        port = int(settings.get("focas_port", focas_find.DEFAULT_PORT))
    except (TypeError, ValueError):
        port = focas_find.DEFAULT_PORT
    reg = focas_find.registry_path(settings)
    try:
        host = next((ip for ip in wizard_probe.candidate_hosts(settings, reg) if port_open(ip, port, timeout=0.3)), "")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not host:
        return {"ok": False, "error": "機械が見つかりません（電源と LAN ケーブルを確かめてください）"}
    client = None
    try:
        client = client_factory(settings)
        if on_client:
            on_client(client)
        client.call("connect", {"host": host, "port": port, "timeout": focas_client.connect_timeout(settings),
                                "families": dict(settings.get("focas_families") or {})})
        ident = client.call("identify")
        names = ident.get("axis_names") or []
        n = max(1, min(len(names) or wizard_probe.MAX_AXES, wizard_probe.MAX_AXES))
        items = []
        for k in range(1, n + 1):
            items += [{"no": wizard_probe.MOTOR_ID_PARAM, "label": f"A{k}"},
                      {"no": wizard_probe.AMR_PARAM, "label": f"A{k}"}]
        res = client.call("param_read_many", {"items": items})
        axes = {k: {"motor_id": "", "amr": ""} for k in range(1, n + 1)}
        for it in res.get("items", []):
            if not it.get("ok"):
                continue
            k = int(str(it.get("label", "A0"))[1:] or 0)
            if k in axes:
                axes[k]["motor_id" if it.get("no") == wizard_probe.MOTOR_ID_PARAM else "amr"] = \
                    wizard_probe._clean(it.get("value"))
        status = client.call("status")
        try:
            alarms = list(client.call("alarms").get("alarms") or [])
        except Exception:
            alarms = None
        cnc_id = ident.get("cnc_id") or ""
        unit = focas_find.unit_for(reg, cnc_id) if cnc_id else ""
        probe = {"host": host, "port": port, "unit": unit, "unit_from": "registry" if unit else "", "unit_guess": [],
                 "cnc_type": str(ident.get("cnc_type") or "").strip(), "mt_type": str(ident.get("mt_type") or "").strip(),
                 "cnc_id": cnc_id, "axis_names": list(names), "axes": axes}
        return {"ok": True, "probe": probe, "status": status, "alarms": alarms, "ident": ident}
    except Exception as e:
        return {"ok": False, "error": f"機械を読めませんでした（{getattr(e, 'message', None) or e}）"}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def identify(probe, controllers_list):
    """号機を決める（machine_decide.identify_unit。IP は使わない）。系列（31i と 35i など）の違う号機は外す。
    戻り値 {"unit","units","how","unknown"}。"""
    idn = dict(machine_decide.identify_unit(probe, controllers_list, (probe or {}).get("unit") or ""))
    if idn["how"] in ("容量", "複数"):
        ty = re.sub(r"\D", "", str((probe or {}).get("cnc_type") or "")) or "0"

        def same_series(u):
            c = next((x for x in controllers_list if str(x.unit) == str(u)), None)
            m = re.match(r"\s*(\d+)", str(c.cnc or "")) if c is not None else None
            return not m or m.group(1).lstrip("0") == ty.lstrip("0")
        units = [u for u in idn["units"] if same_series(u)]
        idn["units"] = units
        idn["unit"] = units[0] if len(units) == 1 else ""
        if not units:
            idn.update(how="無し", unknown=True)
    return idn


def unit_phrase(idn):
    """「21号機」「20号機か21号機（同じ仕様）」。決まらなければ ''。"""
    u = idn.get("units") or []
    if len(u) == 1:
        return f"{u[0]}号機"
    if len(u) > 1:
        return "か".join(f"{x}号機" for x in u) + "（同じ仕様）"
    return ""


def unit_candidates_for_register(idn, controllers_list, probe):
    """登録の欄に先に入れる号機の候補（容量が合うもの）。"""
    caps = machine_decide.machine_caps(probe)
    return [str(c.unit) for c in controllers_list if caps and {a: c.caps[a] for a in c.axes()} == caps]


def controller_of(controllers_list, unit):
    return next((c for c in controllers_list if str(c.unit) == str(unit)), None)


def mismatch_lines(probe, ctl):
    """機械の 2165 とマスタの行の食い違い（赤い帯）。戻り値 {"lines","caps","order"}（直せるのはマスタの行だけ）。"""
    if ctl is None:
        return {"lines": [], "caps": {}, "order": []}
    return wizard_probe.master_diff(ctl, probe, lambda k: AXES[k - 1] if 1 <= k <= len(AXES) else str(k))


def axes_summary(probe):
    """「X 85A／Y 165A…」"""
    caps = machine_decide.machine_caps(probe)
    return "　".join(f"{a} {c}" for a, c in caps.items())


# ------------------------------------------------------------------ 決める
def decide_rows(products, probe, ctl):
    """製品ごとの {"kind","axis","alternatives","why","motor","motor_id","pid"}。
    軸は choose_axes（400V×2 もそこ）。モーターは「決めた軸の 2020 が製品の 2020 と同じなら製品、違えば仮（機械のいまの番号）」。"""
    # マスタの電圧は「AC200V」、製品は「200V」。choose_axes は文字の一致で見るので、数字＋Vにそろえて渡す
    # （machine_decide のすき間。そろえないと、200V の製品でも読み替えを試して軸が決まらない）
    m = re.search(r"(\d+)\s*V", str(getattr(ctl, "voltage", "") or "").upper()) if ctl is not None else None
    voltage = f"{m.group(1)}V" if m else ""
    prods = [{"cap": p.get("cap") or "", "voltage": p.get("voltage") or "", "motor_id": p.get("motor_id") or "",
              "values": p.get("values") or {}} for p in products]
    chosen = machine_decide.choose_axes(prods, probe, voltage)
    rows = []
    for p, c in zip(products, chosen):
        rows.append({"kind": p.get("kind") or "", "axis": c["axis"], "alternatives": list(c["alternatives"]),
                     "why": c["why"], "pid": str(p.get("motor_id") or "").strip()})
    # 容量が合う軸が機械に無いとき（仮のモーターを付けている軸など）は、決められないので、残りの軸を選べるようにする
    used = {r["axis"] for r in rows if r["axis"]}
    free = [a for a in machine_decide.machine_caps(probe) if a not in used]
    for r in rows:
        if not r["axis"]:
            r["alternatives"] = list(free)
            r["why"] = "容量が合う軸が機械にありません。使う軸を選んでください"
    set_motors(rows, probe)
    return rows


def _machine_motor(probe, letter):
    if not letter:
        return ""
    ax = ((probe or {}).get("axes") or {}).get(AXES.index(letter) + 1) or {}
    return str(ax.get("motor_id") or "").strip()


def set_motors(rows, probe):
    for r in rows:
        mid = _machine_motor(probe, r["axis"])
        if r["pid"] and mid and r["pid"] == mid:
            r.update(motor="product", motor_id=mid)
        else:
            r.update(motor="temp", motor_id=mid)
    return rows


def change_axis(rows, idx, new_axis, probe):
    """［変える］: 同じ容量の軸にだけ替えられる。ほかの製品がその軸なら入れ替える（2つが同じ軸にならない）。"""
    r = rows[idx]
    if new_axis not in r["alternatives"]:
        raise EasyError("その軸は、容量が違うので選べません")
    out = [dict(x) for x in rows]
    old = out[idx]["axis"]
    for j, o in enumerate(out):
        if j != idx and o["axis"] == new_axis:
            if old not in o["alternatives"]:
                raise EasyError("入れ替えると、もう一つの製品の容量が合わなくなります")
            o["axis"] = old
    out[idx]["axis"] = new_axis
    out[idx]["why"] = "選んだ軸"
    return set_motors(out, probe)


def changeable(rows):
    """［変える］を出すか（同じ容量の軸が、ほかにもあるとき）。"""
    return any(len(r["alternatives"]) > 1 for r in rows)


def card_text(idn, rows):
    """「使う機械：21号機　回転→Y軸　傾斜→X軸　モーター：仮（315）／製品」"""
    parts = [f"使う機械：{unit_phrase(idn) or '（号機がまだ決まっていません）'}"]
    for r in rows:
        parts.append(f"{r['kind']}→{r['axis']}軸" if r["axis"] else f"{r['kind']}→（軸が決まりません）")
    mot = "／".join(f"仮（{r['motor_id']}）" if r["motor"] == "temp" and r["motor_id"] else
                    "仮" if r["motor"] == "temp" else "製品" for r in rows)
    parts.append(f"モーター：{mot}")
    return "　".join(parts)


def reason_line(rows):
    """理由を1行（全部の製品で同じなら1つ）。"""
    whys = []
    for r in rows:
        w = f"{r['kind']}は「{r['why']}」"
        whys.append(w)
    return "。".join(whys) if len(rows) > 1 else (rows[0]["why"] if rows else "")


# ------------------------------------------------------------------ 書く前に言うこと
def _bit(s, bit):
    s = str(s or "")
    return s[7 - bit] if len(s) == 8 and 0 <= bit <= 7 else ""


def label_axis(label):
    m = re.fullmatch(r"A(\d+)", str(label or ""))
    if m and 1 <= int(m.group(1)) <= len(AXES):
        return AXES[int(m.group(1)) - 1]
    return ""


def write_needs(items):
    """書いたあとに要ること。戻り値 {"power_cycle","ref_axes","lines"}。
    電源の入れ直し: 型コードの 0x40 の印がある行、または N1815 が変わる。
    原点の決め直し（レファレンス点復帰）: N1815 の APZ（bit4）が変わる軸。"""
    power, refs = False, []
    for it in items or []:
        if int(it.get("type_code") or 0) & POWER_TYPE_BIT or it["no"] == ORIGIN_NO:
            power = True
        if it["no"] == ORIGIN_NO and _bit(it.get("old"), APZ_BIT) != _bit(it.get("new"), APZ_BIT):
            ax = label_axis(it.get("label"))
            if ax not in refs:
                refs.append(ax)
    lines = []
    if power:
        lines.append("書いたあと、電源の入れ直しが要ります")
    if refs:
        names = "・".join(f"{a}軸" if a else "その軸" for a in refs)
        lines.append(f"{names}の原点の決め直し（レファレンス点復帰）が要ります")
    return {"power_cycle": power, "ref_axes": refs, "lines": lines}


def item_rows(items):
    """書く一覧の表示用 [(番号の文字, 名前, 今, 新)]。"""
    return [(f"N{it['no']:05d}", it.get("label") or "-", it["old"], it["new"]) for it in items]


def trial_count(job):
    """書き試しで書く件数（型の確認が要る型の数。無ければ 1）。"""
    return max(1, len(job.get("verify_keys") or []))


# ------------------------------------------------------------------ 控え → 一覧
class Emit:
    """進み具合の一文（画面の下に出す）。"""

    def __init__(self, sink=None):
        self.sink, self.t0 = sink, time.time()

    def __call__(self, stage, msg):
        if self.sink:
            self.sink(f"{stage}: {msg}")

    def secs(self):
        return int(time.time() - self.t0)


def backup_step(client, settings, *, unit, host, port, controllers_list=None, progress=None, root=None):
    """読むだけ。つなぐ → 控え。戻り値 {"ident","res","raw","types","folder"}（失敗は EasyError）。"""
    client.call("connect", {"host": host, "port": port, "timeout": focas_client.connect_timeout(settings),
                            "families": dict(settings.get("focas_families") or {})})
    ident = client.call("identify")
    if not ident.get("cnc_id"):
        raise EasyError("機械ごとの番号（CNC ID）が読めないので、この機械には書きません")
    root = root or focas_backup.resolve_root({"focas_backup_dir": settings.get("focas_backup_dir", "")})
    res = focas_backup.take_backup(client, root, settings, purpose="書込前", unit=str(unit or ""), host=host,
                                   port=port, controllers=controllers_list, progress=progress)
    if not res.complete:
        raise EasyError("控えが不完全です（この控えでは書けません）: " + "／".join(res.problems[:3]))
    from pathlib import Path
    folder = Path(res.folder)
    raw = (folder / "パラメータ.DAT").read_bytes().decode("cp932", errors="replace")
    types = focas_ops._backup_values(folder)[1]
    return {"ident": ident, "res": res, "raw": raw, "types": types, "folder": folder}


def semi_basis(r):
    """変換の根拠を短く（semi_convert.convert の結果から）。「同じ型式の組 3組」「計算式」。"""
    if r and r.get("source") == "組":
        m = re.search(r"同じ型式の組\s*(\d+)\s*組", str(r.get("basis") or ""))
        return f"同じ型式の組 {m.group(1)}組" if m else "同じ型式の組"
    return "計算式"


def semi_lines(report):
    """決まったこと・書く画面に出す文（製品ごと）。report は [{"kind","applied","stop","basis"}]（選ばなかった製品は入れない）。"""
    out = []
    for e in report or []:
        if e.get("applied"):
            out.append(f"{e['kind']}：セミクロに変換して書きます（根拠: {e['basis']}）")
        else:
            out.append(f"{e['kind']}：セミクロにはできません（{e['stop']}）。製品の値のまま書きます")
    return out


def plan_step(bk, plan_fn, settings, *, seiban, unit, axes, keep_motor, host, port, semi_name=""):
    """控えと plan_fn（かんたん作成と同じ中身）から、書く一覧を作る。機械には触らない。戻り値は job（cli_write と同じ形）。
    semi_name を渡すと（セミクロに変換したとき）、書いたあとの機械の姿を、その名前（SEMI 入り）で控えのフォルダに残す。"""
    ident, res, raw, types, folder = bk["ident"], bk["res"], bk["raw"], bk["types"], bk["folder"]
    kw = {"keep_motor": True} if keep_motor else {}
    plan = plan_fn(raw, zero_scope="product", axis=None, **kw)
    family = ident.get("family") or ""
    wplan = focas_ops.plan_writes(raw, plan, types, verified=focas_ops.verified_list(settings, family))
    if semi_name:                                   # 変換した値の予定は、客先へ出すものと取り違えない名前で残す
        (folder / semi_name).write_bytes(plan["newtext"].encode("cp932", errors="replace"))
    else:
        focas_ops.save_plan_file(folder, plan["newtext"])
    items = wplan["items"]
    if not items:
        raise EasyError("書くものがありません（機械はすでに同じ値です）")
    if not keep_motor and any(it["no"] == 2020 for it in items):
        raise EasyError("モーター番号が機械と違います。この画面では、仮のモーターにして書きます（通常の画面のかんたん作成なら、製品のモーターも書けます）")
    verify_keys = sorted(wplan["unverified"])
    problems = [b for b in wplan["blockers"] if not (verify_keys and b.startswith("型 ") and "まだ書いたことがありません" in b)]
    if problems:
        raise EasyError("書けない理由があります: " + "／".join(problems[:3]))
    return {"seiban": seiban, "unit": unit, "axes": list(axes), "keep_motor": bool(keep_motor), "host": host, "port": port,
            "family": family, "cnc_id": ident["cnc_id"], "backup_folder": str(folder), "backup_info": res.info,
            "items": items, "expected": wplan["expected"], "unwritable": wplan["unwritable"],
            "verify_keys": verify_keys, "newtext": plan["newtext"], "semi": bool(semi_name), "plan_file": semi_name,
            "made_at": time.strftime("%Y-%m-%d %H:%M:%S")}


# ------------------------------------------------------------------ 書く
def ready_reasons(client, job, settings, ignore_unit):
    """いま書けるか。書込の許可（SW0100）では判断しない。ほかの点検は focas_ops.writable_state のまま。"""
    ident = client.call("identify")
    if ident.get("cnc_id") != job["cnc_id"]:
        return ["別の機械につながっています（機械ごとの番号が、控えを取ったときと違います）"]
    st = client.call("status")
    al = client.call("alarms")["alarms"]
    w = focas_ops.writable_state(st, al, allowed_alarms=focas_ops.allowed_codes(settings, ignore_unit),
                                 backup_info=job["backup_info"], cnc_id=ident.get("cnc_id"), pwe_by_alarm=False)
    return list(w["reasons"])


def trial_targets(job):
    """書き試しで書く行。型の確認が要る型ごとに1件。要らなければ、問題の無い先頭の1件。"""
    items = job["items"]
    out = []
    for key in job.get("verify_keys") or []:
        it = next((x for x in items if x.get("key") == key and not x["problems"]), None)
        if it is not None:
            out.append(it)
    if not out:
        it = next((x for x in items if not x["problems"]), None)
        if it is not None:
            out.append(it)
    return out


def run_trial(client, settings, job, *, ignore_unit, save=None, log_path=None):
    """書き試し（人が押したときだけ）。今と同じ値を書いて読み戻す。戻り値 {"state","text","reasons"}:
    ok / protected（機械が断った）/ not_ready / failed。"""
    reasons = ready_reasons(client, job, settings, ignore_unit)
    if reasons:
        return {"state": "not_ready", "reasons": reasons, "text": "いまは書けません: " + "／".join(reasons[:3])}
    ctx = {"unit": job.get("unit") or "", "seiban": job.get("seiban") or ""}
    done = 0
    for it in trial_targets(job):
        try:
            out = focas_ops.write_trial(client, it, cnc_id=job["cnc_id"], backup_info=job["backup_info"], settings=settings,
                                        family=job["family"], ctx=ctx, log_path=log_path, ignore_unit=ignore_unit)
        except focas_ops.WriteRefused as e:
            return {"state": "not_ready", "reasons": e.reasons, "text": "いまは書けません: " + "／".join(e.reasons[:3])}
        if out.get("protected"):
            return {"state": "protected", "text": WRITE_REFUSED_TEXT, "reasons": []}
        if not out["ok"]:
            extra = "（確認の1件の値が変わったかもしれません。通常の画面で確かめてください）" if out.get("touched") and out.get("changed") else ""
            return {"state": "failed", "text": f"書き試しが通りませんでした: {out['note']}{extra}", "reasons": []}
        if it.get("key"):
            focas_ops.add_verified_kind(settings, job["family"], it["key"])
            if save:
                save(settings)
        done += 1
    return {"state": "ok", "n": done, "text": f"書けました（今と同じ値を {done} 件書いて、読み戻しも合いました）", "reasons": []}


def run_write(client, settings, job, *, ignore_unit, on_item=None, log_path=None, cancel=None):
    """本番の書込（人が「書く」を押したときだけ）。戻り値 {"state","text","res"}:
    done / protected / stopped / not_ready。power_cycle・ref_return は res から。"""
    reasons = ready_reasons(client, job, settings, ignore_unit)
    if reasons:
        return {"state": "not_ready", "text": "いまは書けません: " + "／".join(reasons[:3]), "res": None}
    ctx = {"unit": job.get("unit") or "", "seiban": job.get("seiban") or ""}
    try:
        res = focas_ops.run_write(
            client, job["items"], cnc_id=job["cnc_id"], backup_info=job["backup_info"], settings=settings,
            family=job["family"], ctx=ctx, newtext=job["newtext"],
            types=focas_ops._backup_values(job["backup_folder"])[1], on_item=on_item, log_path=log_path,
            job_path=str(__import__("pathlib").Path(job["backup_folder"]) / focas_ops.JOB_NAME), cancel=cancel,
            expected=job["expected"], unwritable=[tuple(x) for x in job["unwritable"]],
            allow_own_power_alarms=True, pwe_by_alarm=False, ignore_unit=ignore_unit)
    except focas_ops.WriteRefused as e:
        return {"state": "not_ready", "text": "書けません: " + "／".join(e.reasons[:3]), "res": None}
    if res.protected:
        return {"state": "protected", "text": WRITE_REFUSED_TEXT, "res": res}
    if res.state == "done":
        return {"state": "done", "text": "", "res": res}
    return {"state": "stopped", "text": res.stop_reason or "途中で止まりました", "res": res}


# ------------------------------------------------------------------ 書いたあとの確かめ
def apz_tolerant_same(it, now):
    """N1815 の APZ（bit4）は、原点復帰のあと機械が 1 に戻す。書いた値と APZ だけ違い、機械の APZ が 1 のときは「合っている」。"""
    if it["no"] != ORIGIN_NO:
        return False
    a, b = str(it["new"]), str(now)
    if len(a) != 8 or len(b) != 8:
        return False
    i = 7 - APZ_BIT
    return a[:i] + a[i + 1:] == b[:i] + b[i + 1:] and b[i] == "1"


def check_readback(client, job):
    """電源を入れ直したあと、書いた件を読み戻す（読むだけ）。戻り値 [{"no","label","want","now","ok","note"}]。"""
    out = []
    for it in job["items"]:
        r = client.call("param_read", {"no": it["no"], "label": it.get("label") or ""})
        now = r.get("value")
        ok = focas_ops.same_value(it.get("kind"), now, it["new"])
        note = ""
        if not ok and apz_tolerant_same(it, now):
            ok = True
            note = "原点を決め直したので、機械が原点のビットを 1 に戻しています（これで正しい）"
        out.append({"no": it["no"], "label": it.get("label") or "", "want": it["new"], "now": now, "ok": ok, "note": note})
    return out


def check_step(settings, client_factory, job):
    """電源を入れ直したあと、つなぎ直して読み戻す（読むだけ）。戻り値 {"ok","error","rows","alarms"}。"""
    client = None
    try:
        client = client_factory(settings)
        client.call("connect", {"host": job["host"], "port": job["port"], "timeout": focas_client.connect_timeout(settings),
                                "families": dict(settings.get("focas_families") or {})})
        ident = client.call("identify")
        if ident.get("cnc_id") != job["cnc_id"]:
            return {"ok": False, "error": "別の機械につながっています", "rows": [], "alarms": []}
        rows = check_readback(client, job)
        alarms = list(client.call("alarms").get("alarms") or [])
        return {"ok": True, "error": "", "rows": rows, "alarms": alarms}
    except Exception as e:
        return {"ok": False, "error": f"まだ機械につながりません。電源が入ったら、もう一度押してください（{getattr(e, 'message', None) or e}）",
                "rows": [], "alarms": []}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


# ------------------------------------------------------------------ 測定プログラムを送る（かんたん作成の段④と同じ中身）
# 作るもの・送り方・安全の決まりは、かんたん作成の段④（wizard_programs / focas_ops.run_programs_send）をそのまま使う。
#   プログラムは非常停止をもどしてから（for_params=False の点検は check_program_send の中）。
#   同じ番号で中身が違うときは、人が「入れ替える」を選んだ番号だけ入れ替える（選ばなければ書かずに飛ばす）。
#   1本ずつ書いて読み戻して比べる（run_program_send）。機械に書くのは、prog_send を呼んだときだけ。

def prog_plan(settings, model, kinds, *, tilt=None, choice=None, numbers=None, masters_dict=None):
    """送るプログラムを決める（機械には触らない）。製品の種別 kinds が無ければ、回転か傾斜かを人が選べる（plan["ambiguous"]）。"""
    m = masters_dict if masters_dict is not None else masters.load_masters(settings)
    return WP.make_plan(settings, m, model, list(kinds or []), tilt=tilt, choice=choice, numbers=numbers)


def prog_nc(plan):
    """送る中身（nc）。作れないとき・問題があるときは空。"""
    if not plan or not plan["programs"] or plan["problems"]:
        return b""
    progs = []
    for p in plan["programs"]:
        progs += focas_ops.split_programs(p["nc"], fanuc.DEFAULT_EOB)
    return focas_ops.join_programs(progs, fanuc.DEFAULT_EOB)


def prog_numbers(plan):
    """送る番号の一覧（本体とサブ）。"""
    return [int(n) for p in (plan or {}).get("programs") or [] for n in p["numbers"]]


def prog_labels(plan):
    """番号 → 画面に出す名前。"""
    lines = {}
    for p in (plan or {}).get("programs") or []:
        lines.update(p.get("lines") or {})
    out = {}
    for s in (plan or {}).get("slots") or []:
        out[int(s["number"])] = lines.get(s["number"]) or s["label"]
    return out


def prog_folder(settings):
    return str(settings.get("focas_prog_folder") or focas_ops.DEFAULT_PROG_FOLDER)


def prog_allowed(settings, unit):
    """許すアラーム。SW0100 など標準のものと、この機械で「無視する」と人が選んだもの（パラメータを書くときと同じ）。"""
    return focas_ops.allowed_codes(settings, str(unit or ""))


def prog_read(client, settings, plan, *, host, port, unit="", controllers_list=None, progress=None, root=None):
    """読むだけ。つなぐ → 控え（機械のプログラム一覧ごと）→ 送れるかの点検 → 機械の中の確認。
    戻り値 {"info","cnc_id","chk","states","folder"}。控えが不完全・CNC ID が読めないときは EasyError。"""
    nc = prog_nc(plan)
    if not nc:
        raise EasyError("送るプログラムを作れません: " + "／".join((plan or {}).get("problems") or ["中身がありません"]))
    client.call("connect", {"host": host, "port": port, "timeout": focas_client.connect_timeout(settings),
                            "families": dict(settings.get("focas_families") or {})})
    ident = client.call("identify")
    if not ident.get("cnc_id"):
        raise EasyError("機械ごとの番号（CNC ID）が読めないので、この機械には書きません")
    root = root or focas_backup.resolve_root({"focas_backup_dir": settings.get("focas_backup_dir", "")})
    res = focas_backup.take_backup(client, root, settings, purpose="測定プログラム書込前", unit=str(unit or ""), host=host,
                                   port=port, controllers=controllers_list, progress=progress)
    info = dict(res.info, complete=bool(res.complete))
    if not res.complete:
        raise EasyError("控えが不完全です（この控えでは書けません）: " + "／".join(res.problems[:3]))
    folder = prog_folder(settings)
    chk = focas_ops.check_program_send(client, nc, fanuc.DEFAULT_EOB, folder=folder, cnc_id=ident["cnc_id"], backup_info=info,
                                       allowed=prog_allowed(settings, unit))
    states = focas_ops.inspect_programs(client, nc, fanuc.DEFAULT_EOB, folder=folder) if not chk["reasons"] else {}
    return {"info": info, "cnc_id": ident["cnc_id"], "chk": chk, "states": states, "folder": folder}


def prog_recheck(client, settings, plan, rd, *, host, port, unit=""):
    """非常停止をもどしたあとなど、もう一度だけ点検する（読むだけ。控えは取り直さない）。"""
    nc = prog_nc(plan)
    client.call("connect", {"host": host, "port": port, "timeout": focas_client.connect_timeout(settings),
                            "families": dict(settings.get("focas_families") or {})})
    chk = focas_ops.check_program_send(client, nc, fanuc.DEFAULT_EOB, folder=rd["folder"], cnc_id=rd["cnc_id"],
                                       backup_info=rd["info"], allowed=prog_allowed(settings, unit))
    states = focas_ops.inspect_programs(client, nc, fanuc.DEFAULT_EOB, folder=rd["folder"]) if not chk["reasons"] else {}
    return dict(rd, chk=chk, states=states)


def prog_send(client, settings, plan, rd, *, host, port, unit="", overwrite_numbers=(), progress=None):
    """送る（人が［送る］を押したときだけ）。かんたん作成の段④と同じ run_programs_send。戻り値はその結果 dict。"""
    client.call("connect", {"host": host, "port": port, "timeout": focas_client.connect_timeout(settings),
                            "families": dict(settings.get("focas_families") or {})})
    return focas_ops.run_programs_send(
        client, prog_nc(plan), fanuc.DEFAULT_EOB, approved=True,
        overwrite_numbers={int(n) for n in overwrite_numbers}, groups=WP.groups_of(plan["programs"]),
        folder=rd["folder"], cnc_id=rd["cnc_id"], backup_info=rd["info"], allowed=prog_allowed(settings, unit),
        progress=progress)


def prog_all_in(plan, states, results):
    """全部の番号が、もう入っている（送った・読み戻しが合った・中身が同じで送らなかった）か。"""
    nums = prog_numbers(plan)
    if not nums:
        return False
    for n in nums:
        it = (results or {}).get(n)
        if it is not None:
            if not (WP.row_status(it)[1] == "ok" or it.get("skip_reason") == "same"):
                return False
        elif (states or {}).get(n) != "same":
            return False
    return not WP.mismatched_numbers(list((results or {}).values()))
