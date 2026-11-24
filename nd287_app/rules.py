# -*- coding: utf-8 -*-
"""今日決めた規則の一覧（どこで決めたか・根拠・コードのどこにあるか）と、機械へ書いたときの結果記録。

根拠の種類:
  利用者   … 利用者が口頭で決めた（2026-11-24）。
  マスタ   … マスタ/データの事実から決めた。
  推測     … 公開情報や推論から決めた。**実機で外れたら、結果記録に出る**。
結果記録 `規則の結果記録.csv`（アプリのフォルダ。マスタ外）: 機械へパラメータを書くたびに
  日時・モーター型式・号機・使った規則・結果（OK／アラーム番号／止まった）を1行足す。
  規則が間違っていたとき、ここを見れば「どの規則を使った書込で」「何が起きたか」が分かる。
"""
import csv
import time
from pathlib import Path

LOG_NAME = "規則の結果記録.csv"
LOG_FIELDS = ["日時", "受注番号", "モーター型式", "号機", "使った規則", "サーボ版", "結果", "アラーム", "控えフォルダ"]

# id: (内容, 根拠, 場所, 試験)
RULES = {
    "D_FILTER": ("末尾 -D のモーターは「-D駆動」が○の号機だけ", "利用者",
                 "motor_rules.py・マスタ/モーター規則.csv（画面で編集）・gui._update_candidates_body", "test_motor_rules"),
    "B_FILTER": ("末尾 -B のモーターは 30i/31i/32i/35i 系の号機だけ", "推測（利用者「-Bは31iとか決まってる」。号機の範囲は推測）",
                 "motor_rules.py・マスタ/モーター規則.csv（画面で編集）・gui._update_candidates_body", "test_motor_rules"),
    "AXIS_2165": ("軸は機械の各軸の 2165（アンプ容量）で決める。2020 では決めない", "利用者",
                  "wizard_probe.decide / capacity_axes / resolve_distinct", "test_wizard_probe・test_wizard_probe_two_axis"),
    "V400_X2": ("400V製品を200V機に付けるときは容量を2倍（40A→80A）", "利用者",
                "seiban_flow.equivalent_cap・wizard_probe.capacity_axes", "test_motor_table・test_wizard_probe"),
    "NAMING": ("機番＋測定条件の記号（人が入力）／拡張子で .BS 回転分割 .RS 回転再現 .KS 傾斜分割 .RSK 傾斜再現",
               "利用者（コード bs_format・rs_format・ks_format で確認）", "gui.py（.BS 保存・再現性保存の ext = .RSK if is_tilt else .RS・.KS 保存）・bs/rs/ks_format.py", "test_rule_registry"),
    "TEMP_MOTOR": ("仮モーターは 2020 を書かない（製品のモーターに替えるなら DGPR=0＋電源入れ直し）", "利用者",
                   "gui._machine_job・param_build.drop_param_numbers", "test_wizard_machine_write"),
    "SEMI": ("フルクロ→セミクロ変換（組があれば組の差、無ければ計算式、CMR は 2・20 だけ）", "マスタ（共有の実データ）＋推測",
             "semi_convert.convert", "test_semi_convert"),
}


def log_path(app_dir):
    return Path(app_dir) / LOG_NAME


def outcome_of(res):
    """FocasWriteDialog の結果 → (結果, アラーム)。res は focas_ops の結果オブジェクト。"""
    items = list(getattr(res, "items", []) or [])
    bad = [it for it in items if it.get("status") != "ok"]
    alarms = "・".join(str(a.get("code", a)) if isinstance(a, dict) else str(a)
                       for a in (getattr(res, "new_alarms", []) or []))
    if getattr(res, "state", "") == "done" and items and not bad:
        return ("OK（読み戻し一致）" if not alarms else "OK・アラームあり"), alarms
    if alarms:
        return "アラーム", alarms
    return "止まった／読み戻し違い" if items else "何も書かなかった", alarms


def append_result(path, ctx, res, now=None):
    """機械へ書いた結果を1行足す。足せなければ False（書込の流れは止めない）。"""
    try:
        result, alarms = outcome_of(res)
        path = Path(path)
        new = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if new:
                w.writeheader()
            w.writerow({"日時": now or time.strftime("%Y-%m-%d %H:%M:%S"),
                        "受注番号": ctx.get("seiban", ""), "モーター型式": ctx.get("motor", ""),
                        "号機": ctx.get("unit", ""), "使った規則": "・".join(ctx.get("rules", [])), "サーボ版": ctx.get("servo", ""),
                        "結果": result, "アラーム": alarms, "控えフォルダ": getattr(res, "folder", "") or ""})
        return True
    except Exception:
        return False
