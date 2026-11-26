# -*- coding: utf-8 -*-
"""はじめてモードの写真（Qt なし。名前 → ファイルを探すだけ）。

置き場所: アプリの隣の `写真` フォルダ（settings.json と同じ場所。更新しても消さない）。
ファイル名は下の NAMES の名前（`power.jpg` など。jpg / jpeg / png）。**置くだけで出る。無い所は絵のまま。** 設定は要らない。
docs/はじめてモード_全体設計_20261125.md §2-8。
"""
from pathlib import Path

EXTS = (".jpg", ".jpeg", ".png")

# 写真の名前 → 説明（利用者に撮ってもらう一覧）
NAMES = {
    "scene": "全体（PC・機械・表示器・LAN ケーブルの差し口）",
    "panel": "操作盤の全体",
    "power": "電源の「入」「切」",
    "estop": "非常停止のボタン",
    "dial": "操作モードのつまみ（文字が読める近さ）",
    "knob": "早送りの倍率のつまみ",
    "start": "起動のボタンと、送り停止のボタン",
    "keys": "キー（プログラムの呼び出しに使うキー・リセット）",
    "jog": "軸選択のつまみと「＋送り」「－送り」のボタン",
    "detach": "「軸取り外し」のスイッチの列（X〜C 軸）",
    "param_sheet": "パラメータ表（紙）",
    "counter": "表示器と、0 にするキー",
    "table": "回転テーブルと測定器の取り付け",
    "alarm": "機械の画面の、アラームが出る所",
}

# 絵の名前（easy_art の zoom / art）→ 写真の名前
FOR_ART = {
    "power": "power", "power_cycle": "power", "estop": "estop", "estop_press": "estop", "dial": "dial",
    "knob": "knob", "start": "start", "start_again": "start", "keys": "keys", "jog": "jog",
    "counter": "counter", "scene": "scene", "table": "table", "measure": "table", "alarm": "alarm",
    "params_manual": "param_sheet", "axis_detach": "detach",
}


def photo_dir(base):
    return Path(base) / "写真"


def find(name, base):
    """写真の名前から、置いてあるファイルを探す。無ければ None。"""
    d = photo_dir(base)
    for ext in EXTS:
        for cand in (d / (name + ext), d / (name + ext.upper())):
            if cand.is_file():
                return cand
    return None


def name_for(art_or_zoom):
    return FOR_ART.get(art_or_zoom)


def step_photo(step, base):
    """手順（easy_flow の1つ）に合う写真のファイル。無ければ None。"""
    key = step.get("zoom") or step.get("art")
    name = name_for(key) if key else None
    return find(name, base) if name else None


def missing(base):
    """まだ置かれていない写真の名前の一覧（利用者に「あと何を撮るか」を見せる）。"""
    return [n for n in NAMES if find(n, base) is None]
