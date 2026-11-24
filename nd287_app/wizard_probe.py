# -*- coding: utf-8 -*-
"""かんたん作成を開いたとき、つながっている機械を「読むだけ」で見て、製品かの仮かを決める（Qt なし）。

読むもの: CNC ID（台帳で号機を引く）と、各軸の 2020（モーター番号）・2165（アンプ最大電流 AMR）。
書かない・控えも取らない。LAN を探し回らない（台帳の IP と settings の focas_host に 8193 で届くときだけ）。
決め方（decide）は純関数。推測で埋めない: 確実に決まらない所は空のまま返す。
"""
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


def read_machine(settings, client_factory, *, port_open=None, on_client=None):
    """機械を読む。つながらない・読めないときは None（静かに諦める）。

    戻り値 {"host","unit","cnc_id","axis_names","axes": {軸番号: {"motor_id","amr"}}}。
    unit は台帳に無ければ ''。
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
        unit = focas_find.unit_for(reg, cnc_id) or focas_find.unit_for_ip(reg, host)
        return {"host": host, "unit": unit, "cnc_id": cnc_id, "axis_names": list(names),
                "axes": axes}
    except Exception:
        return None
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def product_motor_id(files):
    """選んだ製品データの 2020。1件だけ選んだときだけ決める。分からなければ ''（＝不明）。"""
    if len(files) != 1:
        return ""
    f = files[0]
    mid = _clean((f.get("meta") or {}).get("motor_id"))
    if mid:
        return mid
    vals = f.get("values") or {}
    for k in (2020, "2020"):
        if k in vals:
            return _clean(vals[k])
    return ""


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
    head = "● 機械につながっています: " + (f"{unit}号機" if unit else "号機は台帳にありません")
    by_id = {}
    for k in sorted(probe.get("axes") or {}):
        mid = _clean(probe["axes"][k].get("motor_id"))
        if mid:
            by_id.setdefault(mid, []).append(axis_name(k))
    if not by_id:
        return head
    parts = [f"{'・'.join(ax)}軸に {mid}" for mid, ax in by_id.items()]
    return head + "　" + "、".join(parts) + " が付いています"
