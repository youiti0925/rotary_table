# -*- coding: utf-8 -*-
"""はじめてモードの「ことばの説明」カード（Qt なし）。

1枚＝絵1つ＋3行まで。「使い方・ことばの説明」のタイルと、各手順の「？ことば」が同じカードを出す。
**文は案**（専門の中身なので、利用者に直してもらう。docs/はじめてモード_全体設計_20261125.md §2-6）。
定義の元は help_text.RESULTS / MEASURE_FLOW（言い換えるだけで、定義は変えない）。
合否は画面の赤い字を数えるだけ（新しい合否の決め方は作らない）。規格が画面に無いときは、合格と言わない。
art は easy_art の絵の名前。
"""

CARDS = {
    "capture": dict(title="取込開始とは", art="scene",
                    lines=["PC が、表示器の値を受け取る用意をすることです。", "このとき、機械は動きません。",
                           "取込開始が先、機械の起動があとです。"]),
    "value_in": dict(title="値はいつ入るか", art="measure_demo",
                     lines=["機械が1か所で止まるたびに、表示器の値が1つ PC に入ります。",
                            "入らないときは、表示器の PRINT キーを確かめます。", "全部の場所が入ると、測り終わりです。"]),
    "accuracy": dict(title="分割の精度とは", art="graph",
                     lines=["「ここまで回れ」と言った角度と、本当に回った角度のずれです。",
                            "単位は秒（1度の 3600 分の1）です。", "ずれが小さいほど、よく割り出せています。"]),
    "graph": dict(title="グラフの見方", art="graph",
                  lines=["横が角度、縦がずれです。", "線が上下に広いほど、ずれが大きいです。", "規格の線の中に入っていれば合格です。"]),
    "pp": dict(title="精度（PP）", art="graph",
               lines=["ずれの一番大きいところと、一番小さいところの差です。", "全体のばらつきの目安になります。"]),
    "single": dict(title="単一誤差", art="graph",
                   lines=["となりの場所へ移ったときの、1歩ぶんのずれです。", "その一番大きいものを出します。"]),
    "adjacent": dict(title="隣接誤差", art="graph",
                     lines=["となり合う2歩のずれが、逆向きに重なった「でっぱり」です。", "その一番大きいものを出します。"]),
    "tilt": dict(title="傾き", art="graph",
                 lines=["一周して、もどるはずの位置にもどらなかったぶんです。", "最後のずれ − 最初のずれ で出します。"]),
    "tilt_big": dict(title="傾きが大きいとき", art="graph",
                     lines=["一周しても、元の所にもどっていません。", "まず、測定器の取り付けを見てください。"]),
    "main": dict(title="主点評価", art="graph",
                 lines=["等分する数（1/N）を入れると、その主な点だけの PP を出します。", "型式の一覧に1/Nがあれば、自動で入ります。"]),
    "backlash": dict(title="総合バックラッシ", art="graph",
                     lines=["同じ指令角度での、反時計回りと時計回りのずれの差です。", "回転は、測った温度の規格と並べて出します。"]),
    "repeat": dict(title="再現性とは", art="repeat",
                   lines=["同じ所へ何度も止めたときの、止まる位置のばらつきです。", "ばらつきが小さいほど、毎回同じ所に止まれます。"]),
    "verdict": dict(title="合否の見方", art="verdict",
                    lines=["右の表の赤い字が、規格から外れた所です。", "規格は、型式と温度で決まります。",
                           "規格が画面に無いときは、アプリは「合格」と言いません。"]),
    "saved": dict(title="保存すると", art="folder",
                  lines=["測った値が、機番の名前のファイルになります。", "あとで「結果を見る」から開けます。", "印刷や PDF にもできます。"]),
}

# 手順 → その手順で見るカード（先頭が最初に出る）
FOR_STEP = {
    "start1": ("capture",), "counter0": ("capture",), "start2": ("capture", "value_in"),
    "measuring": ("value_in", "capture"),
    "result": ("verdict", "graph", "accuracy", "pp", "single", "adjacent", "tilt", "main", "backlash", "repeat"),
    "view_result": ("verdict", "graph", "accuracy", "tilt_big"),
    "finish": ("saved",), "output": ("saved",),
    "cond_div": ("accuracy", "main"), "cond_rep": ("repeat",),
}
ALL = tuple(CARDS)


def cards_for(step_id):
    """その手順で見るカード（無ければ、全部）。"""
    ids = FOR_STEP.get(step_id)
    return tuple(ids) if ids else ALL


def card(card_id):
    return CARDS[card_id]


def ng_lines(items, limit=8):
    """規格から外れた所の一覧（[(項目, 値)]）を、画面に出す行にする。多いときは先の分だけ。"""
    rows = [f"・{n}　{v}" for n, v in items[:limit]]
    if len(items) > limit:
        rows.append(f"…ほか {len(items) - limit} か所")
    return rows
