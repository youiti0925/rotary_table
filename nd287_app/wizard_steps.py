# -*- coding: utf-8 -*-
"""かんたん作成（段階表示）で「いまどの段か・次へ進めるか・足りないものは何か」を決める（Qt なし）。

画面の部品は触らず、値だけから決めるので、画面なしで試験できる。
段: 1=受注番号とモーター  2=制御装置  3=機械につないで送る  4=測定プログラムを送る
"""

STEP_TITLES = {1: "① 受注番号とモーター", 2: "② 制御装置", 3: "③ 機械につないで送る",
               4: "④ 測定プログラムを送る"}
STEPS = (1, 2, 3, 4)


def step_state(step, *, found=False, selected=0, non_fanuc=False, motor="", seiban="",
               ctrl_chosen=False, basic_ok=False, semi_stop=""):
    """戻り値 {"can_next": 次へ進めるか, "missing": [足りないものの文…]}。"""
    missing = []
    if step == 1:
        if not seiban.strip():
            missing.append("受注伝票番号を入れて『探す』を押してください")
        elif not found:
            missing.append("『探す』を押して、製品データを見つけてください")
        elif not selected:
            missing.append("作るもの（傾斜／回転）にチェックを入れてください")
        elif non_fanuc:
            missing.append("FANUC 以外の製品は作れません。FANUC の製品だけにチェックしてください")
        if found and selected and not non_fanuc and not motor:
            missing.append("機械に付いているモーターを選んでください（製品のモーター／社内の仮モーター）")
        if found and selected and not non_fanuc and semi_stop:
            missing.append("セミクロにできません: " + semi_stop)
    elif step == 2:
        if not ctrl_chosen:
            missing.append("制御装置（号機）を選んでください")
        elif not basic_ok:
            missing.append("その号機の BASIC が見つかりません。『使うBASIC』を指定してください")
    # 段③・④は「次へ」で進む段ではない（③は書き終えたあとの案内、④は最後の段）
    return {"can_next": not missing, "missing": missing}


def program_offer(step, *, params_ok=False):
    """段④（測定プログラムを送る）への案内ボタンを出すか。(出すか, ボタンの文字)。
    段③: パラメータの書き込みと読み戻しが成功したあとだけ。
    段②: パラメータは送らず、プログラムだけ送る人のため（いつでも出す）。"""
    if step == 3 and params_ok:
        return True, "続けてプログラムを送る ▶"
    if step == 2:
        return True, "パラメータは送らず、プログラムだけ送る ▶"
    return False, ""


def summary(*, seiban="", model="", kinds="", motor="", motor_id="", ctrl="", semi=False):
    """『決めたこと』の1行。決めたものだけ並べる。"""
    parts = []
    if seiban:
        parts.append(seiban)
    if model:
        parts.append(model)
    if kinds:
        parts.append(kinds)
    if motor == "product":
        parts.append("製品のモーター")
    elif motor == "temp":
        parts.append("仮モーター" + (f"（{motor_id}）" if motor_id else ""))
    if semi:
        parts.append("セミクロに変換")
    if ctrl:
        parts.append(ctrl)
    return " ／ ".join(parts)
