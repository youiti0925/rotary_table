# -*- coding: utf-8 -*-
"""FOCAS の機械を LAN の中から探す・機械ごとの番号（CNC ID）から号機を引く。

ここは**読むだけ**の部品。機械には何も書かない。

1. 探す（`find_machines`）
   PC の LAN の口（IPv4 アドレス）を調べ、同じ網（/24、例 192.168.1.1〜254）の中で
   FOCAS の窓口（ポート 8193）が開いている相手を探す。やるのは「窓口に声をかけて、
   すぐ切る」（TCP の接続だけ）で、FOCAS の命令は送らない。
   前につながった IP・設定の IP を先に試すので、ふだんは 1 回目で見つかる。

2. 号機を引く（台帳 `マスタ/FOCAS機械台帳.csv`）
   号機番号は機械から読めない【事実・設計_FOCAS基本 §6】。そこで、**人が一度選んだ号機**を
   CNC ID（機械ごとに違う番号）と組にして台帳に残し、次からは CNC ID で引く。
   最初の1回は今までどおり人が選ぶ（号機マスタの候補から）。黙って決めない。
"""

import csv
import ipaddress
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_PORT = 8193
REGISTRY_HEADER = ["CNC ID", "号機", "IP", "最後につないだ日時"]


# ---------------------------------------------------------------------------
# PC の LAN の口
# ---------------------------------------------------------------------------

def local_ipv4s():
    """この PC の IPv4 アドレス（127.* と 169.254.*（つながっていない口）は除く）。"""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for a in found:
        if a.startswith("127.") or a.startswith("169.254.") or a in out:
            continue
        out.append(a)
    return out


def subnet_hosts(ip, prefix=24):
    """ip と同じ網（既定 /24）の相手の IP（自分は除く）。"""
    try:
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError:
        return []
    return [str(h) for h in net.hosts() if str(h) != ip]


def candidate_hosts(first=(), local_ips=None, prefix=24):
    """試す順の IP。first（設定・台帳の IP）→ 各網の .1 → その網の残り。重複なし。

    first は PC の網に無くても試す（固定の IP を入れている人もいるため）。
    """
    local_ips = local_ipv4s() if local_ips is None else list(local_ips)
    own = set(local_ips)
    out, seen = [], set()

    def add(h):
        h = str(h or "").strip()
        if h and h not in seen and h not in own:
            seen.add(h)
            out.append(h)

    for h in first:
        add(h)
    nets = [subnet_hosts(ip, prefix) for ip in local_ips]
    for hosts in nets:                 # 機械は .1 のことが多い（号機23 も 192.168.1.1）
        if hosts:
            add(hosts[0])
    for hosts in nets:
        for h in hosts:
            add(h)
    return out


# ---------------------------------------------------------------------------
# 窓口が開いているか
# ---------------------------------------------------------------------------

def port_open(host, port=DEFAULT_PORT, timeout=0.3):
    """host:port に TCP でつながるか（つながったらすぐ切る。FOCAS の命令は送らない）。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def find_machines(hosts, port=DEFAULT_PORT, timeout=0.3, workers=64, probe=None,
                  first_only=False):
    """hosts のうち窓口が開いているもの（hosts の順のまま）。

    first_only: 先頭のほうから順に見て、見つかった時点で返す（ふだんの「つなぐ」用。速い）。
                先頭の数件（設定・台帳の IP）を1件ずつ試し、無ければ残りをまとめて調べる。
    """
    probe = probe or port_open
    hosts = list(hosts)
    if not hosts:
        return []
    if first_only:
        head, rest = hosts[:3], hosts[3:]
        for h in head:
            if probe(h, port, timeout):
                return [h]
        hosts = rest
        if not hosts:
            return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(hosts)))) as ex:
        ok = list(ex.map(lambda h: probe(h, port, timeout), hosts))
    found = [h for h, o in zip(hosts, ok) if o]
    return found[:1] if first_only else found


# ---------------------------------------------------------------------------
# 台帳（CNC ID → 号機・IP）
# ---------------------------------------------------------------------------

def registry_path(settings, app_dir=None):
    p = str(settings.get("focas_registry_csv") or r"マスタ/FOCAS機械台帳.csv")
    path = Path(p)
    if not path.is_absolute():
        if app_dir is None:
            from . import settings as S
            app_dir = S.app_dir()
        path = Path(app_dir) / path
    return path


def load_registry(path):
    """{CNC ID: {"unit", "ip", "when"}}。ファイルが無ければ空。"""
    out = {}
    path = Path(path)
    if not path.is_file():
        return out
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            cid = (row.get("CNC ID") or "").strip()
            if cid:
                out[cid] = {"unit": (row.get("号機") or "").strip(),
                            "ip": (row.get("IP") or "").strip(),
                            "when": (row.get("最後につないだ日時") or "").strip()}
    return out


def save_registry(path, reg):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(REGISTRY_HEADER)
        for cid in sorted(reg):
            r = reg[cid]
            w.writerow([cid, r.get("unit", ""), r.get("ip", ""), r.get("when", "")])
    os.replace(tmp, path)


def remember(path, cnc_id, *, ip=None, unit=None, now=None):
    """台帳に残す。unit=None なら号機は前のまま（IP と日時だけ更新）。CNC ID が無ければ何もしない。"""
    if not cnc_id:
        return None
    reg = load_registry(path)
    r = dict(reg.get(cnc_id) or {"unit": "", "ip": "", "when": ""})
    if ip:
        r["ip"] = ip
    if unit is not None:
        r["unit"] = str(unit)
    r["when"] = now or time.strftime("%Y-%m-%d %H:%M:%S")
    reg[cnc_id] = r
    save_registry(path, reg)
    return r


def unit_for(path, cnc_id):
    """台帳にある号機（人が前に選んだもの）。無ければ ''。"""
    if not cnc_id:
        return ""
    return (load_registry(path).get(cnc_id) or {}).get("unit", "")


def known_ips(path):
    """台帳の IP（新しくつないだ順）。探すときに先に試す。"""
    reg = load_registry(path)
    rows = sorted(reg.values(), key=lambda r: r.get("when", ""), reverse=True)
    out = []
    for r in rows:
        if r.get("ip") and r["ip"] not in out:
            out.append(r["ip"])
    return out


def unit_for_ip(path, ip):
    """台帳で、その IP に最後につないだ機械の号機（探しただけで、まだ CNC ID を読む前の目安）。"""
    reg = load_registry(path)
    rows = sorted((r for r in reg.values() if r.get("ip") == ip),
                  key=lambda r: r.get("when", ""), reverse=True)
    return rows[0].get("unit", "") if rows else ""
