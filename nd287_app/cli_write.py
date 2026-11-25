# -*- coding: utf-8 -*-
"""機械へパラメータを書く、画面なしの固定の入口（2026-11-25 設計: docs/現場で速く書く_設計_20261125.md）。

朝の件（50014861 を23号機の Z 軸へ。25分かかって1件も書けなかった）の原因は、画面なしの入口が無く、
その場でスクリプトを作っていたことだった。ここは**今ある部品を順に呼ぶだけ**で、新しい決め方は作らない:
  作る中身 … かんたん作成と同じ（_machine_job → plan_fn → param_build.prepare）
  控え     … focas_backup.take_backup（読むだけ）
  一覧     … focas_ops.plan_writes
  型の確認・書込 … focas_ops.verify_kind / run_write（書く前の点検は全部そこでやる）

使い方（exe からは nd287_bunkatsu.exe --cli-write …）:
  plan  50014861 --unit 23 --axis Z --motor temp --motor-id 293   読むだけ。一覧と承認コードを出す
  write 50014861 --approve XXXXXXXX                               承認コードが合えば、型の確認 → 書込 → 読み戻し
  check                                                          電源を入れ直したあと、書いた件を読み戻す（読むだけ）
  status                                                         直近の進み具合の最後の行

守ること: plan は機械に書かない（書く命令を呼ぶと例外になる箱で包む）。write は承認コードが無ければ1件も書かない。
軸は推測しない（--axis を渡す。決まらないときは画面のかんたん作成でやる）。止まったら理由を1行出して終わる。
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from . import focas_backup, focas_client, focas_ops
from .nc_param import AXIS_NAMES

JOB_FILE = "書込の一覧.json"
LATEST = "最新の書込の一覧.json"
ESC = {"plan": "plan", "write": "write", "check": "check", "status": "status"}
WRITE_CMDS = ("param_write", "prog_write", "prog_delete", "prog_search", "param_write_many")


class CliError(Exception):
    """理由を1行で出して終わる。"""


# ------------------------------------------------------------------ 進み具合（1行＝1つの出来事）

class Progress:
    """標準出力と進み具合ファイルの両方へ、1行ずつ出す（exe は窓なしなので、ファイルが本体）。"""

    def __init__(self, folder, name=None, echo=True):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / (name or f"cli_{time.strftime('%Y%m%d_%H%M%S')}.log")
        self.echo = echo
        self.t0 = time.time()

    def __call__(self, stage, msg):
        line = f"{time.strftime('%H:%M:%S')} [{stage}] {msg}"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.echo:
            print(line, flush=True)
        return line

    def secs(self):
        return int(time.time() - self.t0)


def progress_dir(settings=None):
    from .settings import app_dir
    return Path(app_dir()) / "進み具合"


def last_lines(settings=None, n=8):
    d = progress_dir(settings)
    files = sorted(d.glob("cli_*.log"))
    if not files:
        return []
    return files[-1].read_text(encoding="utf-8").splitlines()[-n:]


# ------------------------------------------------------------------ 機械へ書かない箱（plan 用）

class ReadOnlyClient:
    """書く命令を呼んだら例外にする。plan が機械に書けない作りにするための箱。"""

    def __init__(self, inner):
        self._c = inner

    def call(self, cmd, args=None, *a, **kw):
        if cmd in WRITE_CMDS:
            raise CliError(f"plan は機械に書きません（{cmd} を呼ぼうとしました）")
        return self._c.call(cmd, args, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._c, name)


# ------------------------------------------------------------------ 承認コード

def approval_code(cnc_id, items, verify_keys):
    """一覧の中身（機械・番号・ラベル・今の値・書く値・先に確かめる型）から作る短い文字。一覧が変われば変わる。"""
    body = json.dumps(
        [cnc_id, [[it["no"], it.get("label"), it["old"], it["new"]] for it in items], sorted(verify_keys)],
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:8].upper()


def axis_number(text):
    """軸の名前（X/Y/Z/A/B/C）か番号（1〜6）→ 番号。"""
    t = str(text).strip().upper()
    if t in AXIS_NAMES:
        return AXIS_NAMES.index(t) + 1
    if t.isdigit() and 1 <= int(t) <= len(AXIS_NAMES):
        return int(t)
    raise CliError(f"軸 {text} が分かりません（{'・'.join(AXIS_NAMES)} か 1〜{len(AXIS_NAMES)}）")


# ------------------------------------------------------------------ 機械につなぐ

def connect(client, settings, host, port, emit):
    emit("つなぐ", f"{host}:{port} につないでいます")
    families = dict(settings.get("focas_families") or {})
    client.call("connect", {"host": host, "port": port,
                            "timeout": focas_client.connect_timeout(settings), "families": families})
    ident = client.call("identify")
    status, alarms = client.call("status"), client.call("alarms")["alarms"]
    v = (status or {}).get("values") or {}
    emit("つなぐ", f"つながりました。{ident.get('series')} {ident.get('version')}・非常停止={v.get('emergency')}・"
                  f"運転={v.get('run')}・アラーム={[a.get('code') for a in alarms or []]}")
    return ident, status, alarms


# ------------------------------------------------------------------ plan

def plan_job(client, settings, plan_fn, *, seiban, axis, keep_motor, unit, host, port, emit,
             root=None, state_dir=None):
    """読むだけ。つなぐ → 控え → 一覧 → 承認コード。機械には書かない。戻り値は job（dict）。"""
    ro = ReadOnlyClient(client)
    ident, status, alarms = connect(ro, settings, host, port, emit)
    if not ident.get("cnc_id"):
        raise CliError("機械ごとの番号（CNC ID）が読めないので、この機械には書きません")
    root = root or focas_backup.resolve_root({"focas_backup_dir": settings.get("focas_backup_dir", "")})
    emit("控え", "機械の全部の設定を読んでいます（読むだけ。1分ほど）")
    res = focas_backup.take_backup(ro, root, settings, purpose="書込前", unit=str(unit or ""), host=host, port=port)
    if not res.complete:
        raise CliError("控えが不完全です（この控えでは書けません）: " + "／".join(res.problems[:4]))
    emit("控え", f"取れました（{res.info.get('taken_at')}・{res.folder}）")
    folder = Path(res.folder)
    raw = (folder / "パラメータ.DAT").read_bytes().decode("cp932", errors="replace")
    types = focas_ops._backup_values(folder)[1]
    kw = {"keep_motor": True} if keep_motor else {}
    plan = plan_fn(raw, zero_scope="product", axis=axis, **kw)
    family = ident.get("family") or ""
    wplan = focas_ops.plan_writes(raw, plan, types, verified=focas_ops.verified_list(settings, family))
    focas_ops.save_plan_file(folder, plan["newtext"])
    items = wplan["items"]
    if not items:
        raise CliError("書くものがありません（機械はすでに同じ値です）")
    if not keep_motor and any(it["no"] == 2020 for it in items):
        raise CliError("モーター番号（2020）が機械と違います。2段階の書込（DGPR）は、まだこの入口ではできません。"
                       "画面のかんたん作成でやってください（仮モーターなら --motor temp）")
    verify_keys = sorted(wplan["unverified"])
    problems = [b for b in wplan["blockers"]
                if not (verify_keys and b.startswith("型 ") and "まだ書いたことがありません" in b)]
    emit("一覧", f"書く内容は {len(items)} 件です。先に確かめる型は {len(verify_keys)} 種類です")
    for it in items:
        emit("一覧", f"  N{it['no']:05d} {it['label'] or '-'}  {it['old']} → {it['new']}")
    if problems:
        raise CliError("書けない理由があります: " + "／".join(problems[:4]))
    code = approval_code(ident["cnc_id"], items, verify_keys)
    job = {
        "seiban": seiban, "unit": unit, "axis": axis, "keep_motor": bool(keep_motor),
        "host": host, "port": port, "family": family, "cnc_id": ident["cnc_id"],
        "backup_folder": str(folder), "backup_info": res.info, "items": items,
        "expected": wplan["expected"], "unwritable": wplan["unwritable"],
        "verify_keys": verify_keys, "code": code, "newtext": plan["newtext"],
        "made_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (folder / JOB_FILE).write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    sd = Path(state_dir) if state_dir else progress_dir(settings)
    sd.mkdir(parents=True, exist_ok=True)
    (sd / LATEST).write_text(json.dumps({"path": str(folder / JOB_FILE)}, ensure_ascii=False), encoding="utf-8")
    emit("承認", f"承認コード {code}。書くのは --approve {code} を付けた write だけです（{emit.secs()}秒）")
    return job


# ------------------------------------------------------------------ write

def load_job(settings, path=None, state_dir=None):
    if path is None:
        sd = Path(state_dir) if state_dir else progress_dir(settings)
        p = sd / LATEST
        if not p.is_file():
            raise CliError("書込の一覧がありません。先に plan をしてください")
        path = json.loads(p.read_text(encoding="utf-8"))["path"]
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wait_ready(client, job, settings, emit, wait=0, sleep=time.sleep):
    """書ける状態になるまで待つ（非常停止・アラーム・リセット）。足りない条件は変わったときだけ1行出す。"""
    end = time.time() + float(wait or 0)
    last = None
    while True:
        status, alarms = client.call("status"), client.call("alarms")["alarms"]
        w = focas_ops.writable_state(status, alarms, allowed_alarms=focas_ops.allowed_codes(settings),
                                     backup_info=job["backup_info"], cnc_id=job["cnc_id"])
        if w["ok"]:
            emit("確認", "書ける状態です（非常停止・リセット・アラームは SW0100 だけ・控えは完全）")
            return
        text = "／".join(w["reasons"][:3])
        if text != last:
            emit("待ち", "書けません: " + text)
            last = text
        if time.time() >= end:
            raise CliError("書ける状態になりませんでした。機械には何も書いていません")
        sleep(1.0)


def write_job(client, settings, job, approve, emit, *, wait=0, save=None, sleep=time.sleep):
    if str(approve or "").strip().upper() != job["code"]:
        raise CliError("承認コードが合いません。1件も書きません（plan の出した一覧のコードを付けてください）")
    items = [dict(it) for it in job["items"]]
    if approval_code(job["cnc_id"], items, job["verify_keys"]) != job["code"]:
        raise CliError("一覧の中身がコードと合いません。1件も書きません。plan をやり直してください")
    ident = client.call("identify")
    if ident.get("cnc_id") != job["cnc_id"]:
        raise CliError("別の機械につながっています（CNC ID が違う）。1件も書きません")
    family = job["family"] or ident.get("family") or ""
    info = job["backup_info"]
    ctx = {"unit": job.get("unit") or "", "seiban": job.get("seiban") or ""}
    log = settings.get("focas_write_log_csv") or None
    wait_ready(client, job, settings, emit, wait=wait, sleep=sleep)
    # 先に型の確認（同じ値を1件書いて読み戻す）。全部通ってから本番を書く
    keys = list(job["verify_keys"])
    for n, key in enumerate(keys, 1):
        it = next(x for x in items if x["key"] == key and not x["problems"])
        emit("型の確認", f"{n}/{len(keys)} {focas_ops.kind_label(key)}: N{it['no']:05d} に今と同じ値を書いて読み戻します")
        out = focas_ops.verify_kind(client, it, cnc_id=job["cnc_id"], backup_info=info, settings=settings,
                                    family=family, ctx=ctx, log_path=log)
        if not out["ok"]:
            raise CliError(f"型の確認が通りませんでした（{out['result']}）: {out['note']}。"
                           "本番の値は1件も書いていません"
                           + ("（確認の1件の値が変わったかもしれません。画面で確かめてください）" if out.get("touched") else ""))
        focas_ops.add_verified_kind(settings, family, key)
        if save:
            save(settings)
        emit("型の確認", f"通りました（{out['at']}）")
    emit("書込", f"{len(items)} 件を書きます")
    counter = {"n": 0}

    def on_item(i, it):
        counter["n"] += 1
        emit("書込", f"{counter['n']}/{len(items)} N{it['no']:05d} {it.get('label') or '-'} "
                    f"{it['old']} → {it['new']}  {it.get('status')}"
                    + (f"（{it.get('note')}）" if it.get("note") else ""))

    res = focas_ops.run_write(
        client, items, cnc_id=job["cnc_id"], backup_info=info, settings=settings, family=family, ctx=ctx,
        newtext=job["newtext"], types=focas_ops._backup_values(job["backup_folder"])[1],
        on_item=on_item, log_path=log, job_path=str(Path(job["backup_folder"]) / focas_ops.JOB_NAME),
        expected=job["expected"], unwritable=[tuple(x) for x in job["unwritable"]])
    done = [it for it in res.items if it["status"] == "ok"]
    emit("結果", f"{len(done)}/{len(items)} 件が、書けて読み戻しも合いました")
    if res.power_cycle:
        emit("結果", "電源を入れ直してください（PW0000）。そのあと check で読み戻します")
    for line in focas_ops.describe_result(res)[:6]:
        emit("結果", str(line.get("title") if isinstance(line, dict) else line))
    return res


# ------------------------------------------------------------------ check

def check_job(client, settings, job, host, port, emit):
    """電源を入れ直したあと。書いた件を読み戻して比べる（読むだけ）。"""
    ro = ReadOnlyClient(client)
    ident, _s, alarms = connect(ro, settings, host, port, emit)
    if ident.get("cnc_id") != job["cnc_id"]:
        raise CliError("別の機械につながっています（CNC ID が違う）")
    bad = 0
    for it in job["items"]:
        r = ro.call("param_read", {"no": it["no"], "label": it.get("label") or ""})
        same = focas_ops._same(it.get("kind"), r.get("value"), it["new"])
        bad += 0 if same else 1
        emit("読み戻し", f"N{it['no']:05d} {it.get('label') or '-'} 機械 {r.get('value')}／書く値 {it['new']} "
                       + ("一致" if same else "違います"))
    emit("結果", f"{len(job['items']) - bad}/{len(job['items'])} 件が一致。アラーム {[a.get('code') for a in alarms or []]}")
    return bad == 0


# ------------------------------------------------------------------ 画面なしで作る中身（かんたん作成と同じ）

class _Stop(Exception):
    pass


def build_plan_fn(settings, seiban, unit, motor, motor_id=""):
    """かんたん作成を画面なし（offscreen）で動かして、plan_fn と書く先の情報を取る。
    作る中身の決め方は画面と同じ（_machine_job）。ここでは何も決めない。"""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    from . import gui as G
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    said = []

    def say(*a, **k):
        said.append(str(a[2]) if len(a) > 2 else "")
        return QtWidgets.QMessageBox.Yes
    mb = QtWidgets.QMessageBox
    saved = {n: getattr(mb, n) for n in ("question", "warning", "information", "critical")}
    for n in saved:
        setattr(mb, n, staticmethod(say))
    try:
        w = G.ParamWizardDialog(None, settings)
        w.e_seiban.setText(str(seiban))
        w.search()
        if not w._files:
            raise CliError(f"受注番号 {seiban} の製品データが見つかりません")
        for f in w._files:
            f["chk"].setChecked(True)
        w.cmb_motor.setCurrentIndex(w.cmb_motor.findData(motor))
        if motor == "temp" and motor_id:
            w.e_motor_id.setText(str(motor_id))
        w._update_candidates()
        i = next((k for k, (c, _a) in enumerate(w._cand) if str(c.unit) == str(unit)), None)
        if i is None:
            raise CliError(f"{unit} 号機が候補にありません（候補 {[str(c.unit) for c, _ in w._cand]}）")
        w.cmb_ctrl.setCurrentIndex(w.cmb_ctrl.findData(i))
        job = w._machine_job()
        if job is None:
            raise CliError("作る中身を決められませんでした: " + "／".join(x for x in said if x)[:300])
        (_settings, plan_fn), kw = job
        return plan_fn, kw
    finally:
        for n, f in saved.items():
            setattr(mb, n, f)


# ------------------------------------------------------------------ コマンド

def main(argv=None):
    ap = argparse.ArgumentParser(prog="--cli-write", description="機械へパラメータを書く（画面なし）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="読むだけ。一覧と承認コードを出す")
    p.add_argument("seiban")
    p.add_argument("--unit", required=True)
    p.add_argument("--axis", required=True, help="書く軸（X/Y/Z/A/B/C）。推測しない")
    p.add_argument("--motor", choices=("product", "temp"), required=True)
    p.add_argument("--motor-id", default="")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    w = sub.add_parser("write", help="承認コードが合えば書く")
    w.add_argument("seiban")
    w.add_argument("--approve", required=True)
    w.add_argument("--wait-ready", type=int, default=180)
    c = sub.add_parser("check", help="書いた件を読み戻す（読むだけ）")
    c.add_argument("--host", default=None)
    c.add_argument("--port", type=int, default=None)
    sub.add_parser("status", help="直近の進み具合")
    args = ap.parse_args(argv)

    from .settings import load_settings, save_settings
    settings = load_settings()
    if args.cmd == "status":
        for ln in last_lines(settings) or ["進み具合はまだありません"]:
            print(ln)
        return 0
    emit = Progress(progress_dir(settings))
    client = None
    try:
        client = focas_client.make_client(settings)
        host = getattr(args, "host", None) or settings.get("focas_host") or "192.168.1.1"
        port = getattr(args, "port", None) or settings.get("focas_port") or 8193
        if args.cmd == "plan":
            emit("作る", f"受注番号 {args.seiban} の中身を、かんたん作成と同じ決め方で作ります")
            plan_fn, _kw = build_plan_fn(settings, args.seiban, args.unit, args.motor, args.motor_id)
            plan_job(client, settings, plan_fn, seiban=args.seiban, axis=axis_number(args.axis),
                     keep_motor=(args.motor == "temp"), unit=args.unit, host=host, port=port, emit=emit)
        elif args.cmd == "write":
            job = load_job(settings)
            if str(job["seiban"]) != str(args.seiban):
                raise CliError(f"直近の一覧は {job['seiban']} のものです（{args.seiban} ではありません）")
            connect(ReadOnlyClient(client), settings, job["host"], job["port"], emit)
            write_job(client, settings, job, args.approve, emit, wait=args.wait_ready, save=save_settings)
        elif args.cmd == "check":
            job = load_job(settings)
            check_job(client, settings, job, host, port, emit)
        return 0
    except CliError as e:
        emit("止まりました", str(e))
        return 2
    except Exception as e:                      # 落ちたときも1行残す（黙らない）
        emit("止まりました", f"{type(e).__name__}: {e}")
        return 3
    finally:
        try:
            if client is not None:
                client.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
