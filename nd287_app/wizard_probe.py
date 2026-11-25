# -*- coding: utf-8 -*-
"""かんたん作成を開いたとき、つながっている機械を「読むだけ」で見て、製品かの仮かを決める（Qt なし）。

読むもの: CNC ID（台帳で号機を引く）と、各軸の 2020（モーター番号）・2165（アンプ最大電流 AMR）。
書かない・控えも取らない。LAN を探し回らない（台帳の IP と settings の focas_host に 8193 で届くときだけ）。
決め方（decide）は純関数。推測で埋めない: 確実に決まらない所は空のまま返す。
"""
import re
from collections import Counter

from . import focas_client, focas_find

MOTOR_ID_PARAM = 2020
AMR_PARAM = 2165
MAX_AXES = 6


def _clean(v):
    """読んだ値を文字にする。空・0 は「付いていない/読めない」として ''。"""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("", "0") else s


def candidate_hosts(settings, registry_path):
    """声をかける IP（台帳の新しい順に最大4つ＋設定の focas_host）。LAN は探さない。"""
    ips = list(focas_find.known_ips(registry_path)[:4])
    host = str(settings.get("focas_host") or "").strip()
    if host and host not in ips:
        ips.append(host)
    return ips


def basic_axis_caps(text):
    """BASIC（パラメータの控え）の軸ごとの容量を [(軸名, 容量)] で、軸スロット A1.. の順に返す（1020 が軸名の軸だけ・2165 を容量に）。
    容量が読めない軸は ''。"""
    from . import controllers, fanuc_param
    vm = {}
    for (num, label), v in fanuc_param.values_map(text).items():
        vm[(fanuc_param._norm_num(num), label)] = v
    n1020, n2165 = fanuc_param._norm_num("1020"), fanuc_param._norm_num("2165")
    out = []
    for i in range(1, 13):
        code = vm.get((n1020, f"A{i}"))
        if code is None or not str(code).strip().lstrip("-").isdigit() or int(code) not in controllers._AXIS_CODE:
            continue
        out.append((controllers._AXIS_CODE[int(code)], controllers.amr_to_capacity(vm.get((n2165, f"A{i}")))))
    return out


def axis_diff_lines(who, unit, have, mach, order):
    """軸ごとの食い違いの文。who は「マスタ」「BASIC」。have {軸名: 容量}（マスタ／BASIC にある軸）、
    mach {軸名: 容量}（機械で読めた軸）、order は機械の軸名の並び。"""
    lines = []
    for a in order:
        mc = mach.get(a, "")
        if a not in have:
            lines.append(f"{who}の{unit}号機に {a}軸はありません（機械は {mc or '容量が読めません'} です）")
        elif mc and have[a] != mc:
            lines.append(f"{who}の{unit}号機の {a}軸は {have[a] or '空'}、機械は {mc} です")
    for a in have:
        if a not in order:
            lines.append(f"機械に {a}軸はありません（{who}の{unit}号機は {have[a] or '容量なし'} です）")
    return lines


def machine_caps(axes, letter_of):
    """機械から読んだ {軸名: 容量}（読めた軸だけ）と、軸名の並び。"""
    from . import controllers
    mach, order = {}, []
    for k in sorted(axes or {}):
        letter = letter_of(k)
        cap = controllers.amr_to_capacity(((axes or {})[k] or {}).get("amr"))
        order.append(letter)
        if cap:
            mach[letter] = cap
    return mach, order


def master_diff(ctl, probe, letter_of):
    """機械から読んだ 2165 と、マスタのその号機の行の食い違い（machine_decide.mismatch を使う。赤い帯の文と、直す値）。

    戻り値 {"lines": 文のリスト（空なら一致）, "caps": {軸名: 容量}（機械で読めた軸）, "order": 機械の軸名の並び}。
    """
    from . import machine_decide
    mach, order = machine_caps((probe or {}).get("axes"), letter_of)
    lines = [f"マスタの{ctl.unit}号機の {a}軸は {mc or '空'}、機械は {hc} です"
             for a, mc, hc in machine_decide.mismatch(probe, ctl)]
    lines += [f"機械に {a}軸はありません（マスタの{ctl.unit}号機は {ctl.caps[a]} です）"
              for a in ctl.axes() if a not in order]
    return {"lines": lines, "caps": mach, "order": order}


def basic_diff(text, unit, axes, letter_of):
    """機械から読んだ容量と、その号機の BASIC（text）の食い違いの文のリスト（空なら一致）。BASIC は直さない。"""
    mach, order = machine_caps(axes, letter_of)
    return axis_diff_lines("BASIC", unit, dict(basic_axis_caps(text)), mach, order)


def read_machine(settings, client_factory, *, port_open=None, on_client=None):
    """機械を読む。つながらない・読めないときは None（静かに諦める）。

    戻り値 {"host","unit","unit_from","unit_guess","cnc_type","cnc_id","axis_names","axes": {軸番号: {"motor_id","amr"}}}。
    unit は CNC ID が台帳にあるときだけ入る（unit_from "registry"）。IP から号機は決めない。
    台帳に無いときの号機は、呼ぶ側が machine_decide.identify_unit（2165 をマスタと照らす）で決める。
    """
    port_open = port_open or focas_find.port_open
    try:
        port = int(settings.get("focas_port", focas_find.DEFAULT_PORT))
    except (TypeError, ValueError):
        port = focas_find.DEFAULT_PORT
    try:
        reg = focas_find.registry_path(settings)
        host = next((ip for ip in candidate_hosts(settings, reg) if port_open(ip, port, timeout=0.3)), "")
    except Exception:
        return None
    if not host:
        return None
    client = None
    try:
        client = client_factory(settings)
        if on_client:
            on_client(client)
        client.call("connect", {"host": host, "port": port,
                                "timeout": focas_client.connect_timeout(settings),
                                "families": dict(settings.get("focas_families") or {})})
        ident = client.call("identify")
        names = ident.get("axis_names") or []
        n = max(1, min(len(names) or MAX_AXES, MAX_AXES))
        items = []
        for k in range(1, n + 1):
            items.append({"no": MOTOR_ID_PARAM, "label": f"A{k}"})
            items.append({"no": AMR_PARAM, "label": f"A{k}"})
        res = client.call("param_read_many", {"items": items})
        axes = {k: {"motor_id": "", "amr": ""} for k in range(1, n + 1)}
        for it in res.get("items", []):
            if not it.get("ok"):
                continue
            k = int(str(it.get("label", "A0"))[1:] or 0)
            if k in axes:
                axes[k]["motor_id" if it.get("no") == MOTOR_ID_PARAM else "amr"] = _clean(it.get("value"))
        cnc_id = ident.get("cnc_id") or ""
        unit, unit_from = "", ""
        if cnc_id:
            unit = focas_find.unit_for(reg, cnc_id)
            unit_from = "registry" if unit else ""
        guess = []
        return {"host": host, "unit": unit, "unit_from": unit_from, "unit_guess": guess,
                "cnc_type": str(ident.get("cnc_type") or "").strip(),
                "mt_type": str(ident.get("mt_type") or "").strip(), "cnc_id": cnc_id,
                "axis_names": list(names), "axes": axes}
    except Exception:
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def file_motor_id(f):
    """製品データ1つの 2020。分からなければ ''。"""
    mid = _clean((f.get("meta") or {}).get("motor_id"))
    if mid:
        return mid
    vals = f.get("values") or {}
    for k in (2020, "2020"):
        if k in vals:
            return _clean(vals[k])
    return ""


def product_motor_id(files):
    """選んだ製品データの 2020。1件だけ選んだときだけ決める。分からなければ ''（＝不明）。"""
    if len(files) != 1:
        return ""
    return file_motor_id(files[0])


def product_motor_ids(files):
    """選んだ製品データごとの 2020（2軸テーブルは選んだ順に2つ）。分からないものは ''。"""
    return [file_motor_id(f) for f in files]


def resolve_distinct(cands, decided=None):
    """製品ごとの候補軸 cands（[[軸番号...], ...]）から、**同じ軸にならない**ように軸を決める（純関数）。

    decided: すでに決まっている軸（0 は未定）。候補が1つになった製品はその軸に決め、ほかの製品の候補から除く
    （片方が決まったらもう片方から除く）。2つの製品が同じ1つの軸しか候補に無い／最初から同じ軸に決まっている
    ときは、どちらも決めず conflict に入れる（人が選ぶ）。戻り値 (決めた軸のリスト, 残った候補, conflictの位置)。
    """
    n = len(cands)
    dec = list(decided or [])[:n]
    dec += [0] * (n - len(dec))
    rest = [list(c) for c in cands]
    conflict = set()
    firsts = [d for d in dec if d]
    for i in range(n):
        if dec[i] and firsts.count(dec[i]) > 1:           # 最初から同じ軸に決まっている → どちらも決めない
            conflict.add(i)
            rest[i] = [dec[i]]
    dec = [0 if i in conflict else d for i, d in enumerate(dec)]
    changed = True
    while changed:
        changed = False
        taken = {d for d in dec if d}
        for i in range(n):
            if not dec[i] and i not in conflict:
                rest[i] = [k for k in rest[i] if k not in taken]
        singles = {}
        for i in range(n):
            if not dec[i] and len(rest[i]) == 1:
                singles.setdefault(rest[i][0], []).append(i)
        for ax, who in singles.items():
            if len(who) > 1:
                conflict.update(who)                      # 同じ1つの軸を取り合う → 決めない
            elif who[0] not in conflict:
                dec[who[0]] = ax
                changed = True
    return dec, rest, sorted(conflict)


def decide(axes, product_id):
    """機械の各軸の 2020 と製品の 2020 から、製品か仮かを決める。

    axes: {軸番号: {"motor_id": str, ...}}。戻り値 dict:
      kind    "product"（機械のどれかの軸が製品の 2020 と同じ）/ "temp"（同じ軸が無い）/ ""（決めない）
      axes    kind=="product" のとき、同じだった軸番号（昇順）
      axis    kind=="temp" で、機械のどの軸が製品の軸か確実に決まるときその軸番号。決められなければ 0
      temp_id kind=="temp" で、axis が決まるとき、その軸の 2020。決められなければ ''
    製品の 2020 が不明、機械の 2020 が1つも読めないときは kind ""。
    """
    out = {"kind": "", "axes": [], "axis": 0, "temp_id": ""}
    pid = _clean(product_id)
    ids = {int(k): _clean(v.get("motor_id")) for k, v in (axes or {}).items()}
    ids = {k: v for k, v in ids.items() if v}
    if not pid or not ids:
        return out
    same = sorted(k for k, v in ids.items() if v == pid)
    if same:
        out.update(kind="product", axes=same)
        return out
    out["kind"] = "temp"
    # 確実に1つに決まるとき: 軸が1つだけ／ほかの軸が全部同じ番号で、1軸だけ違う（3軸以上）
    if len(ids) == 1:
        k, v = next(iter(ids.items()))
        out.update(axis=k, temp_id=v)
    elif len(ids) >= 3:
        cnt = Counter(ids.values())
        if len(cnt) == 2:
            odd = [k for k, v in ids.items() if cnt[v] == 1]
            rest = [v for v in ids.values() if cnt[v] > 1]
            if len(odd) == 1 and rest:
                out.update(axis=odd[0], temp_id=ids[odd[0]])
    return out


def conn_text(probe, axis_name):
    """画面最上段の接続行に出す、読んだ事実だけの文。axis_name(軸番号) → 'B' など。"""
    unit = probe.get("unit") or ""
    guess = list(probe.get("unit_guess") or [])
    if unit and probe.get("unit_from") == "guess":
        head = f"● 機械につながっています: {unit}号機（容量から推定）"
    elif unit:
        head = f"● 機械につながっています: {unit}号機"
    elif guess:
        head = ("● 機械につながっています: 号機は台帳にありません（" + "か".join(f"{g}号機" for g in guess)
                + ("（同じ仕様）" if len(guess) > 1 else "（容量から推定）") + "）")
    else:
        head = "● 機械につながっています: 号機は台帳にありません"
    by_id = {}
    for k in sorted(probe.get("axes") or {}):
        mid = _clean(probe["axes"][k].get("motor_id"))
        if mid:
            by_id.setdefault(mid, []).append(axis_name(k))
    if not by_id:
        return head
    parts = [f"{'・'.join(ax)}軸に {mid}" for mid, ax in by_id.items()]
    return head + "　" + "、".join(parts) + " が付いています"


# ----- 製品の軸を、軸別パラメータの一致数で決める（読むだけ） -----
# 製品データの値のうち BASIC と違う「製品らしい値」を、機械の各軸の今の値と照らし合わせる。
# 一致が最大で、2位より明確に多い軸だけを「製品の軸」とする。決まらなければ決めない（人が選ぶ）。
MAX_AXIS_NUMBERS = 40          # 読む番号の上限（×軸数ぶん読む）
MIN_AXIS_HITS = 3              # これ未満の一致では決めない
MIN_AXIS_LEAD = 2              # 2位よりこれ以上多いときだけ決める
_NOT_DISTINCTIVE = (MOTOR_ID_PARAM, AMR_PARAM)   # 2020 は決める対象、2165 は機械のアンプで製品の値ではない


def param_int(n):
    """番号 '02020' / 'N02020' / 2020 → 2020。数字でなければ None。"""
    s = str(n).strip().lstrip("Nn")
    return int(s) if s.isdigit() else None


def same_value(a, b):
    """値が同じか（数値なら数で比べる。'255' と '255.0' は同じ）。どちらかが空なら False。"""
    a, b = str(a if a is not None else "").strip(), str(b if b is not None else "").strip()
    if not a or not b:
        return False
    try:
        return float(a) == float(b)
    except ValueError:
        return a == b


def pick_numbers(product, basic_axes, limit=MAX_AXIS_NUMBERS):
    """読む番号を選ぶ。product: {番号: 値}（製品の変更値）、basic_axes: {番号: [BASICの各軸の値]}。

    軸別パラメータ（BASIC に軸ごとの値がある番号）で、製品の値が BASIC のどの軸の値とも違うものだけ。
    番号の小さい順に limit 個まで。"""
    prod = {}
    for k, v in product.items():
        no = param_int(k)
        if no is not None and str(v if v is not None else "").strip():
            prod.setdefault(no, v)
    out = []
    for no in sorted(prod):
        if no in _NOT_DISTINCTIVE:
            continue
        vals = basic_axes.get(no)
        if not vals or any(same_value(prod[no], b) for b in vals):
            continue
        out.append(no)
        if len(out) >= limit:
            break
    return out


def count_matches(product, machine):
    """製品の値と機械の各軸の今の値が一致した個数。machine: {軸番号: {番号: 値}}。
    読めなかった番号（machine に無い・空）は数えない。戻り値 {軸番号: 個数}。"""
    prod = {param_int(k): v for k, v in product.items() if param_int(k) is not None}
    out = {}
    for ax, vals in (machine or {}).items():
        out[int(ax)] = sum(1 for no, v in (vals or {}).items() if no in prod and same_value(prod[no], v))
    return out


def break_tie(cands, counts=None):
    """値の一致数で決めた残りを、若い順に仮決めする（machine_decide.choose_axes と同じ規則。非同期で読んだ一致数を使うため残してある）。
    ①製品らしい値の一致数が最も多い軸 → ②番号が最も小さい軸。戻り値 (選んだ軸, 同条件の軸のリスト, 規則の説明)。"""
    pool = sorted(int(k) for k in (cands or []))
    if not pool:
        return 0, [], ""
    how = "番号が最も小さい（若い）軸を選ぶ規則"
    if counts:
        best = max(counts.get(k, 0) for k in pool)
        top = [k for k in pool if counts.get(k, 0) == best]
        if len(top) < len(pool):
            pool, how = top, "製品らしい値の一致が最も多い軸を選ぶ規則"
    if len(pool) > 1:
        how = "同じ条件のうち番号が最も小さい（若い）軸を選ぶ規則"
    return pool[0], pool, how


def common_voltage(ctls, units):
    """号機の候補（同じ仕様の複数）の電圧が全部同じならそれ（'200V'/'400V'）。違う・不明は ''。"""
    from . import seiban_flow
    vs = {seiban_flow.controller_voltage(c) for c in (ctls or []) if str(c.unit) in {str(u) for u in (units or [])}}
    return vs.pop() if len(vs) == 1 else ""


def decide_axis(counts, min_hits=MIN_AXIS_HITS, min_lead=MIN_AXIS_LEAD):
    """一致数 {軸: 個数} から製品の軸を決める。最大が min_hits 以上で、2位より min_lead 以上多いときだけ。
    同数・一致なし・軸が無いときは 0（決めない）。"""
    if not counts:
        return 0
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    best_ax, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if best < min_hits or best - second < min_lead:
        return 0
    return best_ax


def axis_choices(axes, axis_name, only=None):
    """人が1回選ぶための一覧: [(表示, 軸番号, 2020)]。例 'X軸（255）'。2020 が読めた軸だけ。
    only を渡すと、その軸（候補）だけ。"""
    out = []
    for k in sorted(axes or {}):
        if only is not None and k not in only:
            continue
        mid = _clean(axes[k].get("motor_id"))
        if mid:
            out.append((f"{axis_name(k)}軸（{mid}）", int(k), mid))
    return out


def read_axis_params(settings, client_factory, host, numbers, axes, *, on_client=None):
    """機械の各軸の、指定した番号の今の値を読む（読むだけ。書かない・控えを取らない）。

    読めない番号は飛ばす。つながらない・失敗したときは None。戻り値 {軸番号: {番号: 値}}。
    """
    numbers = list(numbers)[:MAX_AXIS_NUMBERS]
    axes = list(range(1, axes + 1)) if isinstance(axes, int) else [int(k) for k in axes]
    if not host or not numbers or not axes:
        return None
    try:
        port = int(settings.get("focas_port", focas_find.DEFAULT_PORT))
    except (TypeError, ValueError):
        port = focas_find.DEFAULT_PORT
    client = None
    try:
        client = client_factory(settings)
        if on_client:
            on_client(client)
        client.call("connect", {"host": host, "port": port,
                                "timeout": focas_client.connect_timeout(settings),
                                "families": dict(settings.get("focas_families") or {})})
        items = [{"no": no, "label": f"A{k}"} for k in axes for no in numbers]
        res = client.call("param_read_many", {"items": items})
        out = {k: {} for k in axes}
        for it in res.get("items", []):
            if not it.get("ok"):
                continue
            v = it.get("value")
            if v is None or str(v).strip() == "":
                continue
            try:
                k = int(str(it.get("label", "A0"))[1:] or 0)
            except ValueError:
                continue
            if k in out:
                out[k][int(it.get("no"))] = str(v).strip()
        return out
    except Exception:
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


# ----- 軸の決め方の順序: ①製品の必要容量 → ②入っている仮モーターの番号 → ③製品の値の一致数 → ④候補だけ人が選ぶ -----
# モーター番号だけから軸を決めることはしない（①で候補が絞れているとき、その中から選ぶためにだけ使う）。
def norm_cap(cap):
    """'40a'/' 40A '/'40' → '40A'。空は ''。"""
    s = str(cap or "").strip().upper().replace("Ａ", "A")
    s = s[:-1].strip() if s.endswith("A") else s
    return f"{s}A" if s else ""


def norm_voltage(v):
    """'AC400V'/'HV'/'400V' → '400V'、'AC200V'/'200V' → '200V'。不明は ''。"""
    s = str(v or "").upper()
    return "400V" if "400" in s else ("200V" if "200" in s else "")


def product_amr(product):
    """製品データ自身の N2165（アンプ最大電流）。あれば値の文字、無ければ ''。"""
    for k, v in (product or {}).items():
        if param_int(k) == AMR_PARAM and str(v if v is not None else "").strip():
            return str(v).strip()
    return ""


def amr_axes(amr, axes):
    """機械の各軸の 2165 が amr と同じ軸の番号（昇順）。axes: {軸番号: {"amr": ...}}。"""
    return [k for k in sorted(axes or {}) if same_value(amr, (axes[k] or {}).get("amr"))]


def capacity_axes(cap, product_v, machine_v, axes):
    """製品の必要容量(+電圧)を機械の電圧に読み替え、機械から読んだ各軸の 2165 の容量が同じ軸を出す。

    容量と1対1に対応するのは 2165（調査記録 §7: AMR = 容量+5）。号機マスタの軸容量は使わない。
    400V の 40A は 200V の機械では 80A（seiban_flow.equivalent_cap）。電圧が分からないときは読み替えずに
    比べ、そのことを note に書く。戻り値 {"cap","axes","note"}。容量が分からなければ axes は []。
    """
    from . import controllers, seiban_flow
    c = norm_cap(cap)
    if not c:
        return {"cap": "", "axes": [], "note": "製品の必要容量が分かりません"}
    pv, mv = norm_voltage(product_v), norm_voltage(machine_v)
    eq, how = c, f"製品は{(pv + ' の ') if pv else ''}{c}"
    if pv and mv and pv != mv:
        eq = norm_cap(seiban_flow.equivalent_cap(c, pv, mv))
        if not eq:
            return {"cap": "", "axes": [], "note": f"{how}（{mv}の機械へ読み替えられません）"}
        how += f" → この機械は {mv} なので {eq}"
    elif not (pv and mv):
        how += "（電圧が分からないので読み替えていません）"
    hit = [k for k in sorted(axes or {})
           if controllers.amr_to_capacity((axes[k] or {}).get("amr")) == eq]
    return {"cap": eq, "axes": hit, "note": how}


def narrow_by_motor_id(cands, axes, typed_id):
    """候補が複数のとき、機械の今の 2020 が、画面に入っている仮モーターの番号と同じ軸に絞る。
    入力が空・候補が1つ以下・同じ軸が無いときは絞らない。戻り値 (候補, 絞ったか)。"""
    tid = _clean(typed_id)
    if len(cands) <= 1 or not tid:
        return list(cands), False
    hit = [k for k in cands if _clean(((axes or {}).get(k) or {}).get("motor_id")) == tid]
    return (hit, True) if hit else (list(cands), False)
