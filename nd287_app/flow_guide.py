# -*- coding: utf-8 -*-
"""「いま何をすればよいか」を、画面の状態から1つだけ決める（Qt なし。画面なしで試験できる）。

2026-11-24 利用者: 「機能はあるが、初心者はどこから触るのか・どの順番かが分からない。
手順書ではなく、アプリが1つずつ案内すること」。

- main_flow():   測定の画面（最初に出る画面）の上の帯。機械の準備 → 測定の準備 → 測る → 結果 → 保存。
- writer_guide(): かんたん作成の段③（機械につないで送る）の帯。つなぐ → 控え → 書く → 電源 → 次へ。

どちらも {"kind", "text", ...} を返すだけ。kind の意味:
  do   = 人がやることがある（青）      wait = 待つだけ（灰）
  stop = 今は進めない。直すことがある（赤）  done = 済んだ（緑）
"""

STAGES = ("① 機械の準備", "② 測定の準備", "③ 測る", "④ 結果を見る", "⑤ 保存する")

# 画面の欄の名前 → 何を入れるか（初めての人向けの言い方）
FIELD_HELP = {
    "型式": "型式（例 RWE-200）",
    "機番": "機番（保存するファイルの名前になります）",
    "名前": "名前（測る人）",
    "測定温度": "温度（測定温度。例 23.5）",
}


def _o(numbers):
    return "・".join(f"O{int(n):04d}" for n in numbers or [])


def _main_flow_full(*, nd_ok=False, nd_dummy=False, machine_host="", machine_unit="",
              machine_done=False, missing=(), capturing=False, count=(0, 0),
              finished=False, ng=0, has_spec=False, saved=False, saved_path="",
              programs=(), next_mode="", mode="", model_unknown=False):
    """測定の画面の帯。戻り値 {"stage": 0〜4, "kind", "text", "button", "action", "skip"}。

    action は画面が実行するものの名前（open_wizard / reconnect / focus / start / save / next_mode / new）。
    skip=True のとき、「もう機械に入っている（飛ばす）」を小さく出す。
    """
    prog = _o(programs)
    if capturing:
        cur, total = count
        return dict(stage=2, kind="wait", button="", action="", skip=False, text=(
            f"測っています（{cur}／{total} 点）。機械の前で"
            + (f" {prog} を選んで" if prog else "、測定プログラムを選んで")
            + "起動（サイクルスタート）を押してください。機械が1か所止まるたびに、値が1つ入ります。"
              "入らないときは、表示器の PRINT キーを押すか、左の『手動取込』を押します。"))
    if finished and saved:
        if next_mode:
            return dict(stage=2, kind="do", button=f"次の測定（{next_mode}）に切り替える", action="next_mode",
                        skip=False, text=(f"保存しました。続けて「{next_mode}」を測ります。"
                                          "ボタンを押すと、測り方を切り替えます（今の結果は保存済みです）。"))
        return dict(stage=4, kind="done", button="次の製品を始める", action="new", skip=False,
                    text="保存しました" + (f"（{saved_path}）" if saved_path else "")
                    + "。この製品の測定は終わりです。印刷するときは上の『印刷』か『PDF成績書』を押します。")
    if finished:
        if ng:
            return dict(stage=3, kind="stop", button="セーブ（保存する）", action="save", skip=False, text=(
                f"測り終わりました。規格から外れた所が {ng} か所あります（右の表の赤い字）。"
                "測り直すときは『取込開始』（全部）か『部分再測定』（一部だけ）を押します。"
                "この結果を残すときは『セーブ』を押します。"))
        if has_spec:
            return dict(stage=4, kind="do", button="セーブ（保存する）", action="save", skip=False, text=(
                "測り終わりました。規格と比べた所に、外れ（赤い字）はありません。『セーブ』を押して保存してください。"))
        return dict(stage=4, kind="do", button="セーブ（保存する）", action="save", skip=False, text=(
            "測り終わりました。この測定には、合否の規格が画面に出ていません。右の表の値を確かめてから、"
            "『セーブ』を押して保存してください。"))
    if not machine_done:
        if machine_host:
            where = f"機械が見えています（{machine_host}" + (f"・前回 {machine_unit}号機" if machine_unit else "") + "）。"
        else:
            where = "まず、機械の電源を入れて、PC と機械を LAN ケーブルでつないでください。"
        return dict(stage=0, kind="do", button="機械に入れる（受注伝票番号から）▶", action="open_wizard", skip=True,
                    text=where + "次に『機械に入れる』を押して、受注伝票番号を入れます"
                    "（製品に合わせた設定と、測るための機械の動きを、順番に機械へ入れます）。")
    if not nd_ok and not nd_dummy:
        return dict(stage=1, kind="stop", button="表示器につなぎ直す", action="reconnect", skip=False, text=(
            "角度を読む表示器（ND287）が、PC につながっていません。表示器の電源と、PC とのケーブルを確かめてから、"
            "『表示器につなぎ直す』を押してください。"))
    if missing:
        names = "・".join(FIELD_HELP.get(m, m) for m in missing)
        return dict(stage=1, kind="do", button="入れる欄へ", action="focus", skip=False,
                    text=f"左上の「測定情報」に、次を入れてください: {names}。"
                    "型式を入れると、測り方（刻みなど）は自動で入ります。")
    note = "型式が測定条件の登録に無いので、刻みなどは自動で入っていません。左の値を確かめてください。" if model_unknown else ""
    return dict(stage=2, kind="do", button="取込開始", action="start", skip=False, text=(
        note + f"準備できました（測り方: {mode}）。『取込開始』を押してから、機械の前で"
        + (f" {prog} を選んで" if prog else "、測定プログラムを選んで")
        + "起動（サイクルスタート）を押してください。"))


def main_flow(**kw):
    """測定の画面の帯。_main_flow_full と同じ戻り値に、帯に出す短い文 "short" を足す
    （2026-11-25 上の2段の作り直し。長い文 "text" は今までどおり残し、マウスを乗せると出る）。"""
    g = _main_flow_full(**kw)
    g["short"] = _short(g, kw)
    return g


def _short(g, kw):
    prog = _o(kw.get("programs", ()))
    what = f" {prog} を" if prog else "測定プログラムを"
    act = g["action"]
    if kw.get("capturing"):
        cur, total = kw.get("count", (0, 0))
        return f"測っています（{cur}／{total} 点）。機械の前で{what}起動してください。機械が止まるたびに値が1つ入ります。"
    if act == "next_mode":
        return f"保存しました。続けて「{kw.get('next_mode', '')}」を測ります。"
    if act == "new":
        return "保存しました。この製品の測定は終わりです。"
    if act == "save":
        if kw.get("ng"):
            return (f"測り終わりました。規格から外れた所が {kw['ng']} か所あります（右の表の赤い字）。"
                    "残すなら『セーブ』、測り直すなら『取込開始』か『部分再測定』。")
        if kw.get("has_spec"):
            return "測り終わりました。外れ（赤い字）はありません。『セーブ』を押して保存してください。"
        return "測り終わりました。規格が画面に出ていません。右の表の値を確かめてから『セーブ』を押してください。"
    if act == "open_wizard":
        if kw.get("machine_host"):
            return "機械が見えています。『機械に入れる』を押して、受注伝票番号を入れます。"
        return "機械の電源を入れ、PC と LAN ケーブルでつなぎます。つないだら『機械に入れる』を押します。"
    if act == "reconnect":
        return "角度を読む表示器が PC につながっていません。電源とケーブルを確かめて『表示器につなぎ直す』を押してください。"
    if act == "focus":
        return "左上の「測定情報」に入れてください: " + "・".join(kw.get("missing", ())) + "。"
    if act == "start":
        return f"準備できました（測り方: {kw.get('mode', '')}）。『取込開始』を押してから、機械の前で{what}起動します。"
    return g["text"]


# ------------------------------------------------------------------ かんたん作成 段③

CONNECT_CHECKS = ("確かめること: ① 機械の電源が入っているか ② PC と機械が LAN ケーブルでつながっているか"
                  "（PC 側の差し込みが抜けやすい）。直したら、下の『つなぐ』を押してください。")


def writer_guide(*, connected=False, busy_mode="", state_err="", estop="", has_backup=False,
                 backup_problem="", plan_ready=False, plan_problem="", n_items=0, reasons=(),
                 rewrite_blocked=False, result_state="", result_all_ok=False, power_cycle=False,
                 stage1=False, stage1_done=False, unverified=0, no_cnc_id=False):
    """段③の帯。戻り値 {"kind", "text", "next"}。next=True のとき「続けてプログラムを送る」を出してよい。"""
    def r(kind, text, nxt=False):
        return {"kind": kind, "text": text, "next": bool(nxt)}

    if busy_mode == "connect":
        return r("wait", "機械につないでいます。何も押さずに待ってください。")
    if busy_mode == "backup":
        return r("wait", "機械の今の中身を、PC に控えています（読むだけ。機械は何も変わりません）。"
                         "下の棒が右端まで進むまで、何も押さずに待ってください。")
    if busy_mode == "write":
        return r("wait", "機械に書いています（1件ずつ、書いて、読み戻して確かめています）。"
                         "終わるまで、機械と LAN ケーブルに触らないでください。")
    if busy_mode == "undo":
        return r("wait", "書いた分を元の値に戻しています。終わるまで待ってください。")
    if busy_mode in ("verify", "resolve", "disconnect"):
        return r("wait", "機械と話しています。待ってください。")
    if stage1_done:
        return r("do", "1回目が書けました。機械の電源を切って、入れ直してください。"
                       "入れ直して機械の画面が出たら、下の『つなぎ直して続ける』を押します（残りを書きます）。")
    if not connected:
        if state_err:
            return r("stop", f"機械につながりません（{state_err}）。" + CONNECT_CHECKS)
        return r("do", "下の『つなぐ』を押してください。機械を探してつなぎます（つなぐだけでは、機械は何も変わりません）。")
    if no_cnc_id:
        return r("stop", "つながりましたが、どの機械かを確かめる番号が読めないので、この機械には書けません。")
    if estop:
        return r("stop", estop)
    if result_state:
        if result_all_ok and power_cycle:
            return r("do", "機械に書けました（読み戻して確かめました）。機械の画面に「電源を切ってください」のアラーム"
                           "（PW0000）が出ています。機械の電源を切って、入れ直してください。"
                           "機械の画面が出たら、右下の『続けてプログラムを送る』を押します。", True)
        if result_all_ok:
            return r("done", "機械に書けました（読み戻して確かめました）。右下の『続けてプログラムを送る』を押してください。", True)
        return r("stop", "書けなかった所があります。下の「結果」に理由が出ています。"
                         "元の値に戻すときは『書いた分を元に戻す』を押します。分からないときは、ここで止めて詳しい人を呼んでください。")
    if not has_backup:
        if backup_problem:
            return r("stop", "機械の中身を控えられませんでした: " + backup_problem
                     + "　下の『控えを取る』をもう一度押してください。")
        return r("do", "下の『控えを取る』を押してください（機械の今の中身を PC に写します。読むだけです）。")
    if not plan_ready:
        return r("stop", plan_problem or "書く中身を決められませんでした。下の説明を見てください。")
    rs = [x for x in reasons if x]
    nothing = [x for x in rs if x.startswith("書くものがありません")]
    others = [x for x in rs if not x.startswith("書くものがありません")]
    if not n_items and nothing and not others and not rewrite_blocked:
        return r("done", "機械には、もう同じ値が入っています。書くものはありません。"
                         "右下の『続けてプログラムを送る』を押してください。", True)
    if not rs:
        return r("do", f"準備できました。下の一覧が、機械の今の値と違う所です（{n_items} 件）。"
                       f"下の『この {n_items} 件を機械に書く』を押すと、機械に書きます"
                       + ("（今回は1回目。モーターの番号だけ先に書きます）。" if stage1 else "。"))
    text = "今は機械に書けません。理由: " + "　／　".join(others or rs)
    if unverified and any("同じ値を書く確認" in x for x in rs):
        text += "　→ 下の『同じ値を書く確認』を押すと、進めるようになります（今と同じ値を書いて確かめるだけです）。"
    return r("stop", text)


# ------------------------------------------------------------------ かんたん作成 段④

def program_guide(*, connected=False, busy=False, state_text="", estop="", has_backup=False,
                  problems="", done_text="", mismatch=False, differs=0, sent_bad=False):
    """段④（測定プログラムを送る）の帯。戻り値 {"kind", "text"}。"""
    def r(kind, text):
        return {"kind": kind, "text": text}

    if done_text:
        return r("done", done_text)
    if problems:
        return r("stop", "測定プログラムを作れません: " + problems)
    if busy:
        return r("wait", "機械と話しています（つなぐ・機械の中を確かめる・書く）。何も押さずに待ってください。")
    if not connected:
        if state_text and state_text not in ("つながっていません", "切りました"):
            return r("stop", f"機械につながりません（{state_text}）。" + CONNECT_CHECKS)
        return r("do", "下の『つなぐ』を押してください。機械を探してつなぎます。")
    if estop:
        return r("stop", estop)
    if mismatch:
        return r("stop", "機械に入れたものを読み戻したら、送ったものと違いました。"
                         "下の『うまく入らなかったとき: 区切りを LF のみにして送り直す』を押してください"
                         "（同じ番号を消して、入れ直します）。")
    if sent_bad:
        return r("stop", "機械に入らなかったプログラムがあります。上の一覧の右端と、下の結果に理由が出ています。")
    if not has_backup:
        return r("wait", "機械の今の中身を、PC に控えています（読むだけ）。待ってください。")
    if differs:
        return r("do", f"機械に、同じ番号で中身の違うプログラムが {differs} 本あります（上の一覧）。"
                       "入れ替えるなら、その行の「入れ替える」にチェックを入れてから『送る』を押します。"
                       "チェックしない行は、機械のものを残して送りません。")
    return r("do", "下の『送る』を押すと、上の一覧の測定プログラムを機械に入れます（機械は動きません）。")
