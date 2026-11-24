# -*- coding: utf-8 -*-
"""はじめてモードの手順（Qt なし。画面なしで試験できる）。

2026-11-24 利用者: 今の画面は「通常」として残す。別に、本当の初心者用を作る。
無駄なボタンを出さず、一方通行（もどる／次へ）で、制御装置の操作は絵で案内し、測定と保存まで進める。

ここで決めること:
  - 手順の並び（STEPS）と、章（CHAPTERS）
  - 機械の状態（読んだ値）から「この手順はもう済んでいる／飛ばす」「できた」を決める（純関数）
  - 画面に出す一行（いまの機械の様子）

機械の状態 st は machine_state() が作る {"online","emergency","aut","run","alarm","pw"}。
読めていない値は None（推測で埋めない。読めないときは「できた」にせず、人が押す小さなボタンを出す）。

実機で確かめたのは「状態の値が読めること（MDI・リセット・アラーム）」まで（docs/FOCAS_実機確認と要件_20261122.md）。
モードの数値の読み（メモリ=1・原点復帰=9 など）は仕様の知識で、実機では未確認。
"""

CHAPTERS = ("つなぐ", "製品を決める", "機械に入れる", "測る準備", "測る", "結果と保存")

# 機械の状態の読み（focas_helper.STATUS_TEXT と同じ。実機で見たのは MDI(0) だけ）
AUT_MDI, AUT_MEM, AUT_EDIT, AUT_HANDLE, AUT_JOG, AUT_REF = 0, 1, 3, 4, 5, 9
RUN_RUNNING = 3
WHO = {"machine": "機械で", "pc": "PC で", "auto": "PC が自動で"}


# ------------------------------------------------------------------ 手順
# kind: info=見るだけ / machine=人が機械でやる / pc=人がPCで1回押す / auto=PCが自動 / input=PCに入れる / run=測っている
# art: 絵の名前（easy_mode が easy_art から選ぶ）。zoom=拡大する絵、part=操作盤のどこか。
# detect: 機械の状態から「できた」を決めるときの名前（is_done）。無ければ、人が「やりました」を押す。
STEPS = [
    dict(id="intro", ch=0, kind="info", title="用意するもの",
         lines=["受注伝票と、LAN ケーブルを用意します。", "PC と機械、PC と角度の表示器を、ケーブルでつなぎます。"],
         art="scene", button="はじめる ▶"),
    dict(id="power", ch=0, kind="machine", title="機械の電源を入れる",
         lines=["操作盤の緑の「入」を押します。"], part="power", zoom="power", detect="online"),
    dict(id="estop", ch=0, kind="machine", title="非常停止をもどす",
         lines=["赤い大きなボタンを右に回すと、手前に出ます。"], part="estop", zoom="estop", detect="estop_off"),
    dict(id="dial_mdi", ch=1, kind="machine", title="つまみを MDI に合わせる",
         lines=["操作モードのつまみを回します。"], part="dial", zoom="dial", to="MDI", detect="aut_mdi"),
    dict(id="machine_in", ch=2, kind="pc", title="製品の設定と測定プログラムを機械に入れる",
         lines=["受注伝票番号を入れると、PC が機械を読んで自動で決めます。", "機械に書く前に、いまの中身を PC に控えます。"],
         art="product", button="受注伝票番号から入れる…", skip_button="もう機械に入っている（飛ばす）"),
    dict(id="cycle_power", ch=2, kind="machine", title="電源を入れ直す",
         lines=["「切」を押して、画面が消えたら「入」を押します。"], part="power", zoom="power_cycle", detect="cycled"),
    dict(id="dial_ref", ch=2, kind="machine", title="つまみを原点復帰に合わせる",
         lines=["電源を入れたあとは、最初に原点へもどします。"], part="dial", zoom="dial", to="原点復帰", detect="aut_ref"),
    dict(id="jog_origin", ch=2, kind="machine", title="「＋B」を押し続ける",
         lines=["テーブルが回って、原点で止まります。"], part="keys", zoom="jog"),
    dict(id="fields", ch=3, kind="input", title="機番・名前・温度を入れる",
         lines=["型式と測り方は、機械に入れたものに合わせて入っています。"], art="table"),
    dict(id="dial_mem", ch=3, kind="machine", title="つまみをメモリ運転に合わせる",
         lines=["測るときは、このモードです。"], part="dial", zoom="dial", to="メモリ運転", detect="aut_mem"),
    dict(id="prog_select", ch=3, kind="machine", title="プログラムを呼び出す",
         lines=["キーを ①→②→③ の順に押します。"], part="keys", zoom="keys"),
    dict(id="override", ch=3, kind="machine", title="早送りのつまみを 100% に上げる",
         lines=["低いままだと、測るのに時間がかかります。"], part="rapid", zoom="knob"),
    dict(id="start1", ch=4, kind="machine", title="緑の起動ボタンを押す",
         lines=["PC は、もう値を受け取る用意ができています。"], part="start", zoom="start", detect="running"),
    dict(id="counter0", ch=4, kind="machine", title="表示器を 0 にする",
         lines=["機械が一度止まりました。表示器の黄色のキーを押します。"], art="counter", zoom="counter"),
    dict(id="start2", ch=4, kind="machine", title="もう一度、緑の起動ボタンを押す",
         lines=["ここから測りはじめます。"], part="start", zoom="start_again", detect="running"),
    dict(id="measuring", ch=4, kind="run", title="測っています",
         lines=["終わるまで、機械にさわらないでください。"], art="measure"),
    dict(id="result", ch=5, kind="pc", title="測り終わりました",
         lines=[], art="verdict", button="保存する"),
    dict(id="finish", ch=5, kind="info", title="おつかれさまでした",
         lines=["保存しました。"], art="verdict_saved", button="終わる"),
]
INDEX = {s["id"]: i for i, s in enumerate(STEPS)}


def step(step_id):
    return STEPS[INDEX[step_id]]


# ------------------------------------------------------------------ 機械の状態

def machine_state(status=None, alarms=None, *, online=False):
    """読んだ値から st を作る。status は cnc_statinfo の {"values":{…}}、alarms は [{"code":…}]。
    読めていないものは None。"""
    vals = (status or {}).get("values") or {}
    codes = [str(a.get("code", "")) for a in (alarms or [])]
    return {
        "online": bool(online and status is not None),
        "emergency": vals.get("emergency"),
        "aut": vals.get("aut"),
        "run": vals.get("run"),
        "alarm": vals.get("alarm"),
        "pw": any(c.startswith("PW") for c in codes),   # PW0000（電源を入れ直してください）など
        "codes": codes,
    }


OFFLINE = machine_state(None, None, online=False)


def is_done(detect, st, track=None):
    """機械の状態から「できた」か。None=読めない（人が押す）。track は手順に入ってからの記録 {"offline":bool, "ran":bool}。"""
    track = track or {}
    if detect == "online":
        return bool(st["online"])
    if not st["online"]:
        return False
    if detect == "estop_off":
        return st["emergency"] == 0
    if detect == "aut_mdi":
        return st["aut"] == AUT_MDI
    if detect == "aut_mem":
        return st["aut"] == AUT_MEM
    if detect == "aut_ref":
        return st["aut"] == AUT_REF
    if detect == "running":
        return st["run"] == RUN_RUNNING
    if detect == "cycled":
        return bool(track.get("offline")) and not st["pw"]
    return False


def can_detect(detect, st):
    """この状態で「できた」を機械から読めるか。読めない値のとき、人が押す小さなボタンを出す。"""
    if not st["online"]:
        return detect == "online"
    key = {"estop_off": "emergency", "aut_mdi": "aut", "aut_mem": "aut", "aut_ref": "aut", "running": "run"}.get(detect)
    if key is None:
        return detect == "cycled"
    return st[key] is not None


def needed(step_id, st, ctx):
    """この手順を出すか。もう済んでいるもの・今回は要らないものは出さない（無駄な画面を出さない）。
    ctx: {"powered_on"（電源の手順を出した）,"cycled"（入れ直した）,"cycled_in_wizard","handoff","skip_machine","counter_reset","finished","saved","missing"}。"""
    ctx = ctx or {}
    s = step(step_id)
    d = s.get("detect")
    if step_id == "power":
        return not st["online"]
    if step_id == "estop":
        return not (st["online"] and st["emergency"] == 0)
    if step_id in ("dial_mdi", "dial_mem"):
        return not (st["online"] and is_done(d, st))
    if step_id == "machine_in":
        return True
    origin = bool(ctx.get("powered_on") or ctx.get("cycled") or ctx.get("cycled_in_wizard"))
    if step_id == "cycle_power":
        return bool(st["pw"] or ctx.get("cycled_in_wizard")) and not ctx.get("cycled")
    if step_id == "dial_ref":
        return origin and not (st["online"] and is_done(d, st))
    if step_id == "jog_origin":
        return origin
    if step_id in ("counter0", "start2"):
        return bool(ctx.get("counter_reset", True))
    if step_id == "finish":
        return True
    return True


def done_now(step_id, st, ctx, track=None):
    """この手順の「次へ」を押せる状態か（自動で決められるものは機械から、決められないものは人が押す）。"""
    ctx = ctx or {}
    s = step(step_id)
    k = s["kind"]
    if k == "info":
        return True
    if k == "machine":
        d = s.get("detect")
        return bool(d) and is_done(d, st, track)
    if step_id == "machine_in":
        return bool(ctx.get("handoff") or ctx.get("skip_machine"))
    if step_id == "fields":
        return not ctx.get("missing")
    if step_id == "measuring":
        return bool(ctx.get("finished"))
    if step_id == "result":
        return bool(ctx.get("saved"))
    return False


def next_index(i, st, ctx):
    """i の次に出す手順の位置（要らないものは飛ばす）。最後なら None。"""
    for j in range(i + 1, len(STEPS)):
        if needed(STEPS[j]["id"], st, ctx):
            return j
    return None


def first_index(st, ctx):
    """最初に出す手順（用意するもの）。"""
    return 0


def chapter_progress(i, total_done_ids=()):
    """章ごとの進み具合（0〜1）。画面上の棒に使う。i=いまの手順の位置。"""
    cur = STEPS[i]["ch"]
    out = []
    for c in range(len(CHAPTERS)):
        ids = [s["id"] for s in STEPS if s["ch"] == c]
        if c < cur:
            out.append(1.0)
        elif c > cur:
            out.append(0.0)
        else:
            n = sum(1 for k in ids if INDEX[k] < i)
            out.append(n / max(1, len(ids)))
    return out


# ------------------------------------------------------------------ 画面に出す一行（いまの機械の様子）

def watch_line(step_id, st, ctx=None, track=None):
    """(kind, 文) か None。kind: wait / ok / stop。機械から読めるものだけ。読めなければ None。"""
    s = step(step_id)
    d = s.get("detect")
    if s["kind"] != "machine" or not d:
        return None
    done = is_done(d, st, track)
    if d == "online":
        return ("ok", "機械が見つかりました") if done else ("wait", "機械をさがしています…")
    if not st["online"]:
        if d == "cycled":
            return ("wait", "電源が切れたのを待っています…" if not (track or {}).get("offline") else "電源が入るのを待っています…")
        return ("wait", "機械が見えません。LAN ケーブルと電源を確かめてください")
    if d == "estop_off":
        return ("ok", "非常停止がもどりました") if done else ("stop", "非常停止がかかっています")
    if d in ("aut_mdi", "aut_mem", "aut_ref"):
        want = {"aut_mdi": "MDI", "aut_mem": "メモリ運転", "aut_ref": "原点復帰"}[d]
        if done:
            return ("ok", f"「{want}」になりました")
        if st["aut"] is None:
            return None
        return ("wait", f"いまは「{aut_name(st['aut'])}」です")
    if d == "running":
        return ("ok", "機械が動き出しました") if done else ("wait", "PC は待っています")
    if d == "cycled":
        if done:
            return ("ok", "機械がもどってきました")
        if st["pw"] and not (track or {}).get("offline"):
            return ("wait", "機械の画面に「電源を切ってください」(PW0000)が出ています")
        return ("wait", "電源が入るのを待っています…")
    return None


def aut_name(v):
    return {0: "MDI", 1: "メモリ運転", 3: "記憶編集", 4: "ハンドル", 5: "手動送り", 9: "原点復帰", 10: "リモート"}.get(v, f"その他（{v}）")
