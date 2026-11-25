"""登録の育ち具合（2026-11-26。Qt なし）。

「登録すればするほど楽になる」を目に見えるようにする: いま何が何件登録されていて、それで何が自動になっているか、
次に何を登録すると一番楽になるか。**数えるだけ**（登録の中身は変えない。新しい登録の仕組みも作らない）。
数えられなかったもの（ファイルが開けない等）は 0 と言わず「数えられませんでした」と出す。
"""
from __future__ import annotations

USUAL_MIN = 5            # result_guide.USUAL_MIN_COUNT と同じ（いつもの値と比べられる件数）


def _norm(model):
    return "".join(str(model or "").upper().split())


def item(key, name, count, unit, gives, grow):
    """count が None のときは「数えられませんでした」。"""
    return dict(key=key, name=name, count=count, unit=unit, gives=gives, grow=grow)


def count_text(it):
    if it["count"] is None:
        return "数えられませんでした"
    return f'{it["count"]} {it["unit"]}'


def items(counts):
    """counts: {鍵: 件数 または None}。鍵は下の並び。画面に出す並び（測る順）で返す。"""
    c = dict(counts or {})
    g = c.get
    return [
        item("conditions", "測定条件（型式ごと）", g("conditions"), "型式",
             "型式を入れるだけで、刻み・測定の順番・規格が入ります。",
             "「その他 ▾」→「測定条件を種類別に登録…」"),
        item("machines", "機械の台帳", g("machines"), "台",
             "つないだ機械が、選んだ号機と合っているかを、アプリが確かめます。",
             "「機械に入れる」で、一度つないで号機を選ぶと残ります。"),
        item("programs", "送った測定プログラムの記録", g("programs"), "回",
             "同じ型式で同じ中身なら「前回と同じ」と出ます。",
             "「機械に入れる」で測定プログラムを送ると残ります。"),
        item("times", "測定にかかった時間", g("times"), "回",
             "測り始める前から、全部で何分かかるかを出します。",
             "最後まで測り終わるたびに、自動で残ります。"),
        item("fixes", "外れたときに直ったやり方（記録帳）", g("fixes"), "件",
             "同じ外れ方のとき、「結果を読む」が、前に直ったやり方を最初に出します。",
             "外れた所が次の測定で入ったとき、「結果を読む」で聞かれるので、やったことを押します。"),
        item("tips", "結果の手引き", g("tips"), "件",
             "外れた項目ごとに、まず見る所を出します。",
             "「結果を読む」の ③ →「手引きを足す…」"),
        item("alarms", "アラームの記録", g("alarms"), "件",
             "同じアラームが出たとき、「前はこれで直りました」と出ます。",
             "アラームが消えたとき、やったことを聞かれるので押します。"),
    ]


def for_model(model, *, has_conditions, saved, times, fixes):
    """いまの型式の育ち具合: [dict(name, state, ok, next)]。saved 等が None なら数えられなかった。"""
    if not _norm(model):
        return []
    rows = []
    rows.append(dict(name="測定条件", ok=bool(has_conditions),
                     state="登録あり（型式を入れるだけで条件が入ります）" if has_conditions else "まだ登録がありません",
                     next="" if has_conditions else "測定条件を登録すると、次から型式を入れるだけで、刻み・順番・規格が入ります。"))
    if saved is None:
        rows.append(dict(name="保存した結果", ok=False, state="数えられませんでした（保存先を見られません）", next=""))
    elif saved >= USUAL_MIN:
        rows.append(dict(name="保存した結果", ok=True, state=f"{saved} 件（いつもの値と比べられます）", next=""))
    else:
        rows.append(dict(name="保存した結果", ok=False, state=f"{saved} 件",
                         next=f"あと {USUAL_MIN - saved} 件 保存すると、「結果を読む」で、この型式のいつもの値と比べられます。"))
    rows.append(dict(name="かかった時間", ok=times > 0,
                     state=f"{times} 回の記録（測る前から、何分かかるかを出せます）" if times else "まだ記録がありません",
                     next="" if times else "最後まで1回測ると、次から、測る前に何分かかるかを出します。"))
    rows.append(dict(name="直ったやり方", ok=fixes > 0,
                     state=f"{fixes} 件の記録" if fixes else "まだ記録がありません",
                     next=""))                       # 外れなければ増えないので、「次にやること」には出さない
    return rows


def next_best(model_rows):
    """いまの型式で、次にやると一番楽になること（1つだけ）。無ければ ""。"""
    for r in model_rows or ():
        if r.get("next"):
            return r["next"]
    return ""


def count_models(kind_table, user_rotary=None):
    """測定条件が登録してある型式の数（種類別の表の回転・傾斜と、アプリから登録した回転条件。重なりは1つ）。"""
    seen = set()
    for key in ("rotary", "tilt"):
        for rec in (kind_table or {}).get(key) or ():
            m = _norm(rec.get("型式"))
            if m:
                seen.add(m)
    for k in (user_rotary or {}):
        if _norm(k):
            seen.add(_norm(k))
    return len(seen)


def rows_for_model(rows, model, field="型式"):
    m = _norm(model)
    return [r for r in rows or () if m and _norm(r.get(field)) == m]
