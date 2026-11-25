# -*- coding: utf-8 -*-
"""つないだ機械から、号機・軸を自動で決める（画面に触らない。Qt なし）。

決め方は1本だけ（かんたん作成もはじめてモードも同じものを呼ぶ）:
  号機   台帳の CNC ID → 無ければ、機械の全軸の 2165 の並びをマスタと照らす
         （IP は使わない。同じ仕様が複数なら全部返して止めずに進む／1台も無ければ「登録の無い機械」）
  軸     機械の 2165（400V 品を 200V 機なら容量×2）→ 製品どうし同じ軸にしない →
         モーター番号 → 値の一致数 → それでも残れば若い順に仮決めして［変える］の候補を返す
  食い違い 機械の 2165 とマスタの行を比べる（直すのは人が押したときだけ）
"""
from . import controllers, seiban_flow

AXES = controllers.AXES


def machine_caps(probe):
    """機械から読んだ 2165 → {軸の文字: 容量}。読めない軸は入れない。"""
    out = {}
    for k, ax in sorted(((probe or {}).get("axes") or {}).items()):
        if 1 <= int(k) <= len(AXES):
            cap = controllers.amr_to_capacity((ax or {}).get("amr"))
            if cap:
                out[AXES[int(k) - 1]] = cap
    return out


def identify_unit(probe, controllers_list, ledger_unit=""):
    """号機を決める。戻り値 {"unit","units","how","unknown"}。

    unit: 1台に決まれば号機（決まらなければ ''）／units: 合う号機すべて／
    how: "台帳"・"容量"・"複数"・"無し"／unknown: 登録の無い機械（BASICを取って登録を案内する）。
    """
    caps = machine_caps(probe)
    if ledger_unit and any(str(c.unit) == str(ledger_unit) for c in controllers_list):
        return {"unit": str(ledger_unit), "units": [str(ledger_unit)], "how": "台帳", "unknown": False}
    if not caps:
        return {"unit": "", "units": [], "how": "無し", "unknown": True}
    hit = [str(c.unit) for c in controllers_list
           if {a: c.caps[a] for a in c.axes()} == caps]
    if len(hit) == 1:
        return {"unit": hit[0], "units": hit, "how": "容量", "unknown": False}
    if hit:
        return {"unit": "", "units": hit, "how": "複数", "unknown": False}
    return {"unit": "", "units": [], "how": "無し", "unknown": True}


def mismatch(probe, controller):
    """機械の 2165 とマスタの行の食い違い [(軸, マスタの容量, 機械の容量)]。機械が読めない軸は比べない。"""
    caps = machine_caps(probe)
    return [(a, controller.caps.get(a, ""), hc) for a, hc in caps.items()
            if controller.caps.get(a, "") != hc]


def choose_axes(products, probe, machine_voltage=""):
    """製品ごとに軸を決める。products: [{"cap","voltage","motor_id","values"}]（values は {番号:値}）。

    戻り値: [{"axis": 軸の文字 or "", "alternatives": 同じ容量の軸の文字リスト, "why": 1行}]。
    2つの製品が同じ軸にならない。人に選ばせるのは、容量が分からない／その容量の軸が機械に無いときだけ。
    """
    caps = machine_caps(probe)
    used, out = set(), []
    axes_info = (probe or {}).get("axes") or {}

    def motor_of(letter):
        return str((axes_info.get(AXES.index(letter) + 1) or {}).get("motor_id") or "")

    for p in products:
        need = p.get("cap") or ""
        pv, mv = p.get("voltage") or "", machine_voltage
        eq = seiban_flow.equivalent_cap(need, pv, mv) if (need and pv and mv and pv != mv) else need
        cands = [a for a, c in caps.items() if c == eq] if eq else []
        alt = list(cands)
        cands = [a for a in cands if a not in used]
        why = f"機械の容量が {eq} の軸" if eq else "製品の容量が分かりません"
        if len(cands) > 1 and p.get("motor_id"):
            m = [a for a in cands if motor_of(a) == str(p["motor_id"])]
            if m:
                cands, why = m, why + "のうち、モーター番号が同じ軸"
        if len(cands) > 1:
            vals = p.get("values") or {}
            def hits(a):
                ax = axes_info.get(AXES.index(a) + 1) or {}
                return sum(1 for k, v in vals.items()
                           if str(k) == "2020" and str(ax.get("motor_id")) == str(v))
            best = max(hits(a) for a in cands)
            cands = [a for a in cands if hits(a) == best]
        if cands:
            pick = cands[0] if len(cands) == 1 else cands[0]     # 残れば若い順に仮決め（［変える］で替えられる）
            used.add(pick)
            out.append({"axis": pick, "alternatives": alt, "why": why})
        else:
            out.append({"axis": "", "alternatives": alt, "why": why + "が決まりません"})
    return out
