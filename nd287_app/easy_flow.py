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

# 入り口（ホーム）から選ぶ流れ。「測る」以外も、絵と短い言葉で案内する（利用者 2026-11-24「他も全部イラストメインで」）。
# chapters: 画面の上に出す章。steps の fl（流れ名 → 何章目か）で、どの手順がどの流れに入るかを決める。
FLOWS = {
    "measure": dict(title="測る", sub="プログラムを入れるところから、保存まで全部", chapters=CHAPTERS, badge="はじめてはこれ"),
    "program": dict(title="プログラムだけ機械に入れる", sub="測りません。入れるだけ", chapters=("つなぐ", "機械に入れる", "おわり")),
    "results": dict(title="結果を見る・印刷", sub="保存した結果", chapters=("えらぶ", "見る", "出す")),
    "alarm": dict(title="アラームを調べる", sub="意味とやること", chapters=("調べる",)),
    "words": dict(title="使い方・ことばの説明", sub="結果の見方・用語（絵つき）", chapters=("見る",)),
    "cond": dict(title="測り方を決める・登録する", sub="分割・再現性。登録すると次から自動で入ります", chapters=("決める", "入れる", "登録")),
}

# 機械の状態の読み（focas_helper.STATUS_TEXT と同じ。実機で見たのは MDI(0) だけ）
AUT_MDI, AUT_MEM, AUT_EDIT, AUT_HANDLE, AUT_JOG, AUT_REF = 0, 1, 3, 4, 5, 9
RUN_RUNNING = 3
WHO = {"machine": "機械で", "pc": "PC で", "auto": "PC が自動で"}


# ------------------------------------------------------------------ 手順
# kind: info=見るだけ / machine=人が機械でやる / pc=人がPCで1回押す / auto=PCが自動 / input=PCに入れる / run=測っている
# art: 絵の名前（easy_mode が easy_art から選ぶ）。zoom=拡大する絵、part=操作盤のどこか。
# detect: 機械の状態から「できた」を決めるときの名前（is_done）。無ければ、人が「やりました」を押す。
STEPS = [
    dict(id="home", ch=None, kind="home", title="何をしますか？", lines=[], art="home"),
    dict(id="intro", ch=0, kind="info", title="用意するもの",
         lines=["受注伝票と LAN ケーブル。", "PC と機械・表示器をつなぐ。"],
         art="scene", button="はじめる ▶"),
    dict(id="prog_intro", ch=None, fl={"program": 0}, kind="info", title="プログラムを機械に入れる",
         lines=["測るための動きを作って、機械に入れます。"], art="scene", button="はじめる ▶"),
    dict(id="choose", ch=0, kind="choose", title="どうやって準備しますか？",
         lines=["パラメータとプログラムを、それぞれ選びます。", "選んだ流れだけを、順に案内します。"], art="choose"),
    dict(id="power", ch=0, fl={"program": 0}, kind="machine", title="機械の電源を入れる",
         lines=["操作盤の緑の「入」を押します。"], part="power", zoom="power", detect="online"),
    dict(id="estop", ch=0, fl={"program": 0}, kind="machine", title="非常停止をもどす",
         lines=["赤い大きなボタンを右に回すと、手前に出ます。"], part="estop", zoom="estop", detect="estop_off"),
    dict(id="dial_mdi", ch=1, fl={"program": 0}, kind="machine", title="つまみを MDI に合わせる",
         lines=["操作モードのつまみを回します。"], part="dial", zoom="dial", to="MDI", detect="aut_mdi"),
    dict(id="estop_press", ch=1, fl={"program": 0}, kind="machine", title="非常停止を押す",
         lines=["設定を書くあいだは、押したままにします。"], part="estop", zoom="estop_press", detect="estop_on"),
    dict(id="cond_check", ch=1, kind="pc", title="測り方を確かめる",
         lines=["型式を入れると、登録してある測り方が自動で入ります。"], art="cond_kind", button="このまま進む ▶"),
    dict(id="machine_in", ch=2, fl={"program": 1}, kind="pc", title="製品の設定と測定プログラムを機械に入れる",
         lines=["受注伝票番号を入れると、PC が機械を読んで自動で決めます。", "機械に書く前に、いまの中身を PC に控えます。"],
         art="product", button="受注伝票番号から入れる…", skip_button="もう機械に入っている（飛ばす）"),
    dict(id="params_manual", ch=2, kind="machine", title="機械で、パラメータを打つ",
         lines=["操作盤で、番号と値を1つずつ打ちます。", "打つ一覧は、下のボタンで見られます。"], art="params_manual",
         button="打つ一覧を見る…"),
    dict(id="cycle_power", ch=2, fl={"program": 1}, kind="machine", title="電源を入れ直す",
         lines=["「切」を押して、画面が消えたら「入」を押します。"], part="power", zoom="power_cycle", detect="cycled"),
    dict(id="estop_release", ch=2, fl={"program": 1}, kind="machine", title="非常停止をもどす",
         lines=["書き終わりました。赤いボタンを右に回します。"], part="estop", zoom="estop", detect="estop_off"),
    dict(id="prog_manual", ch=2, kind="machine", title="機械に、測定プログラムを用意する",
         lines=["操作盤で、プログラムを入れます。", "もう入っていれば、そのまま使えます。"], art="prog_manual"),
    dict(id="dial_ref", ch=2, kind="machine", title="つまみを原点復帰に合わせる",
         lines=["電源のあとは、原点へもどします。"], part="dial", zoom="dial", to="原点復帰", detect="aut_ref"),
    dict(id="jog_origin", ch=2, kind="machine", title="「＋B」を押し続ける",
         lines=["テーブルが回って、原点で止まります。"], part="keys", zoom="jog"),
    dict(id="prog_done", ch=None, fl={"program": 2}, kind="info", title="入りました",
         lines=["機械に入れ終わりました。"], art="auto_done", button="ホームへ"),
    dict(id="fields", ch=3, kind="input", title="機番・名前・温度を入れる",
         lines=["型式と測り方は、もう入っています。"], art="table"),
    dict(id="dial_mem", ch=3, kind="machine", title="つまみをメモリ運転に合わせる",
         lines=["測るときは、このモードです。"], part="dial", zoom="dial", to="メモリ運転", detect="aut_mem"),
    dict(id="prog_select", ch=3, kind="machine", title="プログラムを呼び出す",
         lines=["キーを ①→②→③ の順に押します。"], part="keys", zoom="keys"),
    dict(id="override", ch=3, kind="machine", title="早送りのつまみを 100% に上げる",
         lines=["低いと時間がかかります。"], part="rapid", zoom="knob"),
    dict(id="start1", ch=4, kind="machine", title="緑の起動ボタンを押す",
         lines=["PC は、もう受け取れます。"], part="start", zoom="start", detect="running"),
    dict(id="counter0", ch=4, kind="machine", title="表示器を 0 にする",
         lines=["機械が止まったら、黄色のキー。"], art="counter", zoom="counter"),
    dict(id="start2", ch=4, kind="machine", title="もう一度、緑の起動ボタンを押す",
         lines=["ここから測りはじめます。"], part="start", zoom="start_again", detect="running"),
    dict(id="measuring", ch=4, kind="run", title="測っています",
         lines=["終わるまで、機械にさわらないでください。"], art="measure"),
    dict(id="result", ch=5, kind="pc", title="測り終わりました",
         lines=[], art="verdict", button="保存する"),
    dict(id="finish", ch=5, kind="info", title="おつかれさまでした",
         lines=["保存しました。"], art="verdict_saved", button="ホームへ"),
    dict(id="open_past", ch=None, fl={"results": 0}, kind="pc", title="保存した結果をえらぶ",
         lines=["型式や機番でさがせます。"], art="folder", button="結果をさがす…"),
    dict(id="view_result", ch=None, fl={"results": 1}, kind="info", title="結果",
         lines=[], art="verdict", button="次へ ▶"),
    dict(id="output", ch=None, fl={"results": 2}, kind="pc", title="印刷・PDF",
         lines=["どちらも、検査記録と同じ内容です。"], art="printer", button="ホームへ"),
    dict(id="alarm_look", ch=None, fl={"alarm": 0}, kind="alarm", title="アラームを調べる",
         lines=[], art="alarm", button="ホームへ"),
    # 測り方（測定条件）を決めて登録する。値は通常の画面の欄とつなぐだけ。登録は条件編集と同じ中身（gui.condition_record）
    dict(id="cond_kind", ch=None, fl={"cond": 0}, kind="cond", title="何を測りますか？",
         lines=["測るものと、回転か傾斜かを選びます。"], art="cond_kind"),
    dict(id="cond_div", ch=None, fl={"cond": 1}, kind="cond", title="分割の条件", lines=[], art="cond_div"),
    dict(id="cond_rep", ch=None, fl={"cond": 1}, kind="cond", title="再現性の条件", lines=[], art="cond_rep"),
    dict(id="cond_reg", ch=None, fl={"cond": 2}, kind="cond", title="この型式で登録する",
         lines=["登録すると、次からこの型式を入れただけで、この値が自動で入ります。"], art="cond_reg"),
    dict(id="cond_done", ch=None, fl={"cond": 2}, kind="info", title="できました",
         lines=["次から、この型式を入れると自動で入ります。"], art="auto_done", button="ホームへ"),
]
for _s in STEPS:                      # 各手順が、どの流れの何章目か（測るの章は ch のまま）
    _s.setdefault("fl", {})
    if _s["ch"] is not None:
        _s["fl"]["measure"] = _s["ch"]
    if _s["id"] == "home":
        _s["fl"]["home"] = 0
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
        # None=読めていない（空の一覧=アラームなし とは別）
        "alarms": None if alarms is None else
        [{"code": str(a.get("code", "")), "text": str(a.get("text", "")),
          "type": a.get("type"), "no": a.get("no"), "axis": a.get("axis")} for a in alarms],
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
    if detect == "estop_on":
        return st["emergency"] == 1
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
    key = {"estop_off": "emergency", "estop_on": "emergency", "aut_mdi": "aut", "aut_mem": "aut", "aut_ref": "aut", "running": "run"}.get(detect)
    if key is None:
        return detect == "cycled"
    return st[key] is not None


def needed(step_id, st, ctx):
    """この手順を出すか。もう済んでいるもの・今回は要らないものは出さない（無駄な画面を出さない）。
    ctx: {"powered_on"（電源の手順を出した）,"cycled"（入れ直した）,"cycled_in_wizard","handoff","skip_machine","counter_reset","finished","saved","missing"}。"""
    ctx = ctx or {}
    s = step(step_id)
    d = s.get("detect")
    if step_id in ("home", "prog_intro", "prog_done", "open_past", "view_result", "output", "alarm_look", "choose",
                   "cond_kind", "cond_reg", "cond_done"):
        return True
    ck = ctx.get("cond_kind") or ""
    if step_id == "cond_div":                        # 分割だけ・両方のとき
        return ck in ("division", "one")
    if step_id == "cond_rep":                        # 再現性だけ・両方のとき
        return ck in ("repeat", "one")
    params, prog = ctx.get("params") or "auto", ctx.get("prog") or "auto"
    if step_id == "params_manual":                   # パラメータは手で打つ（アプリは番号と値の一覧を見せるだけ）
        return params == "manual"
    if step_id == "prog_manual":                     # プログラムは機械の側で用意する（アプリは送らず、測るだけ）
        return prog == "manual"
    if step_id == "power":
        return not st["online"]
    mode = ctx.get("param_estop", "estop_only")     # パラメータを書くときの非常停止（focas_ops.estop_mode）
    if step_id == "estop":                           # 先に解除する順（設定が released_only のときだけ）
        return mode == "released_only" and not (st["online"] and st["emergency"] == 0)
    if step_id == "estop_press":                     # パラメータは非常停止がかかっているときだけ書ける（利用者の決定 2026-11-24）
        return mode == "estop_only" and not (st["online"] and st["emergency"] == 1)
    if step_id == "estop_release":                   # 書き終えたあと、まだ非常停止なら解除する（プログラム送信・運転は解除してから）
        return bool(st["online"] and st["emergency"] == 1) and mode != "released_only"
    if step_id in ("dial_mdi", "dial_mem"):
        return not (st["online"] and is_done(d, st))
    if step_id == "machine_in":                      # 両方とも手のときは、アプリが機械に入れるものが無い
        return not (params == "manual" and prog == "manual")
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
    if step_id == "choose":
        return bool(ctx.get("params")) and bool(ctx.get("prog"))
    if step_id == "cond_check":
        return not ctx.get("cond_missing")
    if step_id == "cond_kind":
        return bool(ctx.get("cond_kind")) and ctx.get("cond_tilt") is not None and not ctx.get("cond_missing")
    if step_id in ("cond_div", "cond_rep"):
        return not ctx.get("cond_missing")
    if step_id == "cond_reg":
        return bool(ctx.get("cond_registered"))
    if step_id == "machine_in":
        return bool(ctx.get("handoff") or ctx.get("skip_machine"))
    if step_id == "fields":
        return not ctx.get("missing")
    if step_id == "measuring":
        return bool(ctx.get("finished"))
    if step_id == "result":
        return bool(ctx.get("saved"))
    if step_id == "open_past":
        return bool(ctx.get("has_data"))
    if k in ("alarm",) or step_id in ("output", "view_result"):
        return True
    return False


def in_flow(step_id, flow):
    return flow in step(step_id)["fl"]


def next_index(i, st, ctx, flow="measure"):
    """i の次に出す手順の位置（その流れに入らないもの・要らないものは飛ばす）。最後なら None。"""
    for j in range(i + 1, len(STEPS)):
        if flow in STEPS[j]["fl"] and needed(STEPS[j]["id"], st, ctx):
            return j
    return None


def first_index(st, ctx, flow="measure"):
    """その流れの最初の手順。"""
    for j in range(len(STEPS)):
        if flow in STEPS[j]["fl"] and needed(STEPS[j]["id"], st, ctx):
            return j
    return None


def chapter_progress(i, flow="measure"):
    """章ごとの進み具合（0〜1）。画面上の棒に使う。i=いまの手順の位置。"""
    names = FLOWS.get(flow, FLOWS["measure"])["chapters"]
    cur = STEPS[i]["fl"].get(flow, 0)
    out = []
    for c in range(len(names)):
        ids = [x["id"] for x in STEPS if x["fl"].get(flow) == c]
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
    if d == "estop_on":
        return ("ok", "非常停止がかかりました") if done else ("wait", "いまは解除されています")
    if d in ("aut_mdi", "aut_mem", "aut_ref"):
        want = {"aut_mdi": "MDI", "aut_mem": "メモリ運転", "aut_ref": "原点復帰"}[d]
        if done:
            return ("ok", f"「{want}」になりました")
        if st["aut"] is None:
            return None
        return ("wait", f"機械は「{aut_name(st['aut'])}」と答えています（数字は {st['aut']}）。つまみが合っているのに進まないときは、下の小さいボタンを押します")
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


# ------------------------------------------------------------------ 中に入れたかんたん作成の案内（絵と短い言葉）
# かんたん作成（受注伝票番号→制御装置→機械に入れる→測定プログラム）の長い説明の代わりに、
# 絵1つ・見出し・短い一言だけを出す。長い文は、止まっているとき（stop）だけ小さく添える（理由が要るため）。
# pic: 絵の名前（easy_mode が easy_art から選ぶ）。kind: do / wait / stop / done

WIZ_TITLES = {1: "受注伝票番号を入れる", 2: "機械（号機）を決める", 3: "機械に入れる", 4: "測定の動きを入れる"}

# (文に含まれる言葉, 短い一言)。上から順に最初に合ったもの。
_MISSING_SHORT = (
    ("受注伝票番号を入れて", "伝票の番号を入れて「探す」を押す"),
    ("『探す』を押して", "「探す」を押す"),
    ("傾斜／回転", "傾斜か回転に ✓ を入れる"),
    ("FANUC 以外", "この製品は作れません"),
    ("モーターを選んで", "付いているモーターを選ぶ"),
    ("セミクロにできません", "セミクロにできません"),
    ("制御装置（号機）を選んで", "号機を選ぶ"),
    ("BASIC が見つかりません", "元の設定ファイルが見つかりません"),
)
_GUIDE_SHORT = (
    ("非常停止にして書きます", "非常停止を押す"),          # パラメータを書くには、押す（社内の手順）
    ("非常停止ボタンを押して", "非常停止を押す"),
    ("非常停止", "非常停止がかかっています。もどす"),      # プログラムを送る・運転するときは、もどす
    ("機械につながりません", "機械につながりません。電源とケーブルを見る"),
    ("機械につないでいます", "機械につないでいます…"),
    ("控えています", "機械の中身を写しています…"),
    ("機械に書いています", "機械に書いています。さわらない"),
    ("元の値に戻しています", "元にもどしています…"),
    ("機械と話しています", "機械と話しています…"),
    ("電源を切って", "電源を入れ直す"),
    ("電源を入れ直して", "電源を入れ直す"),
    ("つなぎ直して続ける", "電源を入れ直して、「つなぎ直して続ける」"),
    ("読み戻したら、送ったものと違", "読み戻すと違いました。「LF のみで送り直す」を押す"),
    ("機械に入らなかった", "入らなかったものがあります"),
    ("もう入っています", "もう入っています ✓"),
    ("機械に、同じ番号で中身の違う", "同じ番号で中身が違うものがあります"),
    ("書けませんでした", "書けませんでした"),
    ("書けなかった所", "書けなかった所があります"),
    ("機械には、もう同じ値", "もう同じ値が入っています ✓"),
    ("機械に書けました", "入りました ✓"),
    ("『この", "「機械に書く」を押す"),
    ("『控えを取る』", "「控えを取る」を押す"),
    ("『つなぐ』を押して", "「つなぐ」を押す"),
    ("『送る』を押す", "「送る」を押す"),
    ("測定プログラムを作れません", "測定プログラムを作れません"),
    ("今は機械に書けません", "今は書けません"),
    ("機械の様子を読めて", "機械の様子を読めません"),
)


def wizard_brief(step, kind="", text="", missing=(), done=False):
    """かんたん作成の今の様子 → {"title","say","pic","kind","detail"}。
    step=1〜4。kind/text は③④の帯（長い文）。missing は①②の足りないもの。done=機械に入れ終えた（測定へ進める）。"""
    title = WIZ_TITLES.get(step, "")
    pic = {1: "product", 2: "table", 3: "auto", 4: "auto"}.get(step, "auto")
    if done:
        return dict(title="入りました", say="下の「測定へ進む」を押す", pic="auto_done", kind="done", detail="")
    if step in (1, 2):
        m = list(missing or [])
        if not m:
            return dict(title=title, say="そろいました。「次へ」を押す", pic=pic, kind="done", detail="")
        say = next((s for key, s in _MISSING_SHORT if key in m[0]), "画面の下の説明を見る")
        return dict(title=title, say=say, pic=pic, kind="do", detail="")
    t = text or ""
    say = next((s for key, s in _GUIDE_SHORT if key in t), "")
    if not say:
        say = {"wait": "待っています…", "stop": "止まっています", "done": "できました ✓", "do": "つぎの操作をする"}.get(kind, "")
    if "電源" in say and "入れ直" in say:
        pic = "power"
    elif "ケーブル" in say or "つなが" in say:
        pic = "scene"
    elif "非常停止を押す" in say:
        pic = "estop_press"
    elif "非常停止" in say:
        pic = "estop"
    return dict(title=title, say=say, pic=pic, kind=kind or "wait", detail=(t if kind == "stop" else ""))
