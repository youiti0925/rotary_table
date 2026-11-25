"""測る前の点検（2026-11-26。Qt なし）。

取込開始を押す前に、画面の状態と、これまでの登録・記録から分かることを並べる。**止めない・勝手に直さない**
（取込開始を押せるかどうかは、今までどおり画面が決める）。書くのは見えている事実だけ。
level: "ok"（できている）/ "todo"（測る前にやること）/ "note"（知っておくとよいこと）。
"""
from __future__ import annotations

from . import live_guide as LG
from . import result_guide as RG


def checks(*, nd_ok, nd_dummy, missing, model, has_conditions, same_saved=None, last_saved_date="",
           fix_rows=(), time_rows=(), mode=""):
    """点検の行 [dict(level, text)]。

    missing: まだ入っていない欄の名前（画面の missing_required_fields と同じ）。
    same_saved: 同じ型式・同じ機番の保存した結果の件数（None＝まだ数えていない／数えられない → 何も言わない）。
    fix_rows: 記録帳（結果の対処記録）のうち、この型式の行。time_rows: かかった時間の記録（全部）。
    """
    out = []
    if nd_dummy:
        out.append(dict(level="note", text="表示器はダミーです（練習用。本物の値ではありません）。"))
    elif nd_ok:
        out.append(dict(level="ok", text="表示器とつながっています。"))
    else:
        out.append(dict(level="todo", text="表示器とつながっていません。上の帯の「表示器につなぎ直す」を押してください。"))
    missing = [str(m) for m in (missing or ())]
    if missing:
        out.append(dict(level="todo", text="まだ入っていません: " + "・".join(missing)))
    else:
        out.append(dict(level="ok", text="型式・機番・名前・温度が入っています。"))
    if str(model or "").strip():
        if has_conditions:
            out.append(dict(level="ok", text="測定条件は、この型式の登録から入りました。"))
        else:
            out.append(dict(level="note", text="この型式は、測定条件の登録がありません。画面に出ている刻みで測ります"
                                               "（登録すると、次から自動で入ります）。"))
    if same_saved:
        when = f"（一番新しいのは {last_saved_date}）" if last_saved_date else ""
        out.append(dict(level="note", text=f"この機番の結果は、もう {same_saved} 件 保存されています{when}。"
                                           "機番が合っているか見てください。測り直しなら、このままで大丈夫です。"))
    out.extend(_fix_notes(fix_rows))
    usual, n = LG.usual_seconds_any(time_rows, model, mode)
    if usual:
        out.append(dict(level="note", text=f"全部で {LG.minutes_text(usual)} の見込みです（この型式の {n} 回の記録）。"))
    return out


def _fix_notes(fix_rows, limit=2):
    """この型式で前に外れた項目と、そのとき直ったやり方（記録帳にあるものだけ）。多い順に limit 件。"""
    kinds = {}
    for r in fix_rows or ():
        for k in str(r.get("外れた項目") or "").split("／"):
            if k.strip():
                kinds[k.strip()] = kinds.get(k.strip(), 0) + 1
    notes = []
    for kind, _n in sorted(kinds.items(), key=lambda kv: -kv[1])[:limit]:
        stats, total = RG.fix_stats(list(fix_rows), kind)
        if not stats:
            continue
        did, cnt = stats[0]
        notes.append(dict(level="note", text=f"この型式は、前に「{kind}」が外れました。そのときは「{did}」で直りました"
                                             f"（{total} 回のうち {cnt} 回）。"))
    return notes


def summary(rows):
    """上に出す1行と、測る前にやることの数。"""
    todo = sum(1 for r in rows if r["level"] == "todo")
    if todo:
        return f"測る前に、PC でやることが {todo} つあります。", todo
    return "PC の用意はできています。次にやることは、上の帯のとおりです。", 0


MARK = {"ok": "✓", "todo": "！", "note": "・"}
