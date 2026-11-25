# -*- coding: utf-8 -*-
"""初めての制御装置を、機械の控えから「その号機の BASIC」として登録する（Qt なし）。

台帳にも制御装置マスタにも無く、BASIC も無い機械でも使えるようにする。
**人が「登録する」を押したときだけ**呼ぶ（自動では登録しない）。号機の番号は人が入れる。

やること（register_first_unit）:
  1. 控え（FOCAS で取った パラメータ.DAT＝実機が出したもの）を、BASIC フォルダの
     **外**の「機械から取ったBASIC」フォルダへ F<号機>BASIC.dat としてそのままコピーする。
     CLAUDE.md の約束「BASIC フォルダにアプリの出力を置かない」を守る。
  2. 制御装置マスタに、読み取れる範囲で仮の行を足す（読めない項目は空欄。人が後で直す）。
     既に同じ号機があれば上書きしない。
  3. 台帳に CNC ID と号機を組で残す。
"""

import json
import re
import shutil
from pathlib import Path

from . import controllers, focas_backup, focas_find

DIR_NAME = "機械から取ったBASIC"
PARAM_DAT = "パラメータ.DAT"


class FirstUnitError(Exception):
    pass


def machine_basic_dir(settings, app_dir=None):
    """保存先。settings の basic_from_machine_dir、無ければ param_basic_dir の隣の「機械から取ったBASIC」。"""
    s = str((settings or {}).get("basic_from_machine_dir") or "").strip()
    if s:
        return Path(s)
    b = str((settings or {}).get("param_basic_dir") or "").strip()
    if b:
        return Path(b).parent / DIR_NAME
    if app_dir is None:
        from . import settings as S
        app_dir = S.app_dir()
    return Path(app_dir) / DIR_NAME


def search_dirs(folder):
    """basic_file_for_unit が BASIC フォルダの次に探す場所（重複なし）。"""
    out = []
    try:
        from . import settings as S
        raw = json.loads(S.settings_path().read_text(encoding="utf-8"))
        s = str(raw.get("basic_from_machine_dir") or "").strip()
        if s:
            out.append(Path(s))
    except Exception:
        pass
    if folder:
        out.append(Path(folder).parent / DIR_NAME)
    seen, res = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            res.append(p)
    return res


def clean_unit(unit):
    u = str(unit or "").strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if not re.fullmatch(r"\d{1,6}", u):
        raise FirstUnitError("号機は数字で入れてください（例 12）")
    return str(int(u))


def _dat_path(folder):
    p = Path(folder) / PARAM_DAT
    if not p.is_file():
        raise FirstUnitError(f"控えに {PARAM_DAT} がありません")
    return p


def provisional_controller(folder, info, unit):
    """控えから読める範囲だけで Controller を作る。読めない項目（電圧・SERVO・容量が決まらない軸）は空欄。"""
    text = _dat_path(folder).read_bytes().decode("cp932", errors="replace")
    ctl = controllers.controller_from_basic_text(text, unit)
    cnc = (info or {}).get("cnc") or {}
    typ = str(cnc.get("cnc_type") or "").strip()
    mt = str(cnc.get("mt_type") or "").strip()
    ctl.cnc = (f"{typ}i-{mt}" if mt else f"{typ}i") if typ else ""
    ctl.ver = str(cnc.get("version") or "").strip()
    return ctl


def add_provisional_to_master(master_path, ctl):
    """仮の行を足す。同じ号機があれば何もしない。'added' / 'exists' / 'nopath'。"""
    if not master_path:
        return "nopath"
    existing = controllers.load_controllers(master_path) if Path(master_path).exists() else []
    if any(str(c.unit).strip() == ctl.unit for c in existing):
        return "exists"
    controllers.append_units_to_master(master_path, [ctl])
    return "added"


def register_first_unit(settings, folder, info, unit, *, app_dir=None):
    """控え(folder, info)を号機 unit の BASIC として登録する。戻り値は結果の dict。

    既に同じ号機の BASIC（別フォルダ含む）か制御装置マスタの行があれば、何も書かず
    FirstUnitError（「もうあります」）。
    """
    u = clean_unit(unit)
    cnc_id = (info or {}).get("cnc_id")
    if not cnc_id:
        raise FirstUnitError("機械ごとの番号（CNC ID）が読めていないので登録できません")
    if not (info or {}).get("complete"):
        raise FirstUnitError("控えが完全ではないので登録できません")
    src = _dat_path(folder)
    dest_dir = machine_basic_dir(settings, app_dir)
    basic_dir = str((settings or {}).get("param_basic_dir") or "").strip()
    if basic_dir and focas_backup._inside(dest_dir, basic_dir):
        raise FirstUnitError("保存先が BASIC フォルダの中です（BASIC フォルダにはアプリの出力を置きません）。"
                             "設定の basic_from_machine_dir を別の場所にしてください")
    master = str((settings or {}).get("controller_master_csv") or "").strip()
    if controllers.basic_file_for_unit(basic_dir, u) or controllers.basic_file_for_unit(str(dest_dir), u):
        raise FirstUnitError(f"号機{u}の BASIC はもうあります（上書きしません）")
    ctl = provisional_controller(folder, info, u)
    if master and Path(master).exists() and any(
            str(c.unit).strip() == u for c in controllers.load_controllers(master)):
        raise FirstUnitError(f"号機{u}は制御装置マスタにもうあります（上書きしません）")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"F{u}BASIC.dat"
    shutil.copyfile(src, dest)                      # 実機が出したバイトのまま
    master_state = add_provisional_to_master(master, ctl)
    focas_find.remember(focas_find.registry_path(settings, app_dir), cnc_id, unit=u)
    blank = [n for n, v in (("電圧", ctl.voltage), ("SERVO", ctl.servo)) if not v]
    blank += [f"{a}軸の容量" for a in controllers.AXES if ctl.amps.get(a) and not ctl.caps.get(a)]
    return {"unit": u, "file": str(dest), "master": master_state, "controller": ctl, "blank": blank}


def register_from_machine(settings, client, unit, *, host, port, controllers_list=None, cancel=None, progress=None,
                          app_dir=None):
    """つないだ機械の控え（BASIC）を読むだけで取り、号機 unit の BASIC として登録し、台帳に CNC ID と号機を残す。

    機械には何も書かない。client は make_client の戻り（呼ぶ側が最後に close する）。
    その号機の BASIC が既にあれば（BASIC フォルダ・機械から取ったBASIC のどちらでも）上書きしない: 控えは取らず、
    台帳にだけ残す（result["existing"] にその BASIC のパス）。戻り値は register_first_unit と同じ dict に
    "cnc_id" と "existing" を足したもの。失敗は FirstUnitError（控えが不完全・同じ号機がマスタにある 等）。
    """
    u = clean_unit(unit)
    client.call("connect", {"host": host, "port": port, "timeout": focas_client_timeout(settings),
                            "families": dict(settings.get("focas_families") or {})})
    reg = focas_find.registry_path(settings, app_dir)
    basic_dir = str((settings or {}).get("param_basic_dir") or "").strip()
    have = (controllers.basic_file_for_unit(basic_dir, u)
            or controllers.basic_file_for_unit(str(machine_basic_dir(settings, app_dir)), u))
    if have:
        cnc_id = client.call("identify").get("cnc_id")
        if not cnc_id:
            raise FirstUnitError("機械ごとの番号（CNC ID）が読めていないので登録できません")
        focas_find.remember(reg, cnc_id, ip=host, unit=u)
        return {"unit": u, "file": "", "existing": str(have), "master": "exists", "controller": None,
                "blank": [], "cnc_id": cnc_id}
    res = focas_backup.take_backup(
        client, focas_backup.resolve_root(settings), settings, purpose="BASIC登録", unit=u, host=host,
        port=port, controllers=controllers_list, cancel=cancel, progress=progress)
    if not res.complete:
        raise FirstUnitError("控えが完全ではないので登録できません: " + "／".join(list(res.problems)[:3]))
    out = register_first_unit(settings, res.folder, res.info, u, app_dir=app_dir)
    cnc_id = (res.info or {}).get("cnc_id")
    focas_find.remember(reg, cnc_id, ip=host, unit=u)
    out.update({"cnc_id": cnc_id, "existing": ""})
    return out


def focas_client_timeout(settings):
    from . import focas_client
    return focas_client.connect_timeout(settings)


def apply_master_fix(master_path, unit, caps, cnc="", now=None, order=None):
    """マスタの号機 unit の行を、機械の値に直す。直す前に、同じフォルダへ <名前>.bak_<日時> の控えを残す。

    caps は {軸名: 容量}（機械で読めた軸）。order は機械の軸名の並び（省略時は caps の軸）。機械に無い軸の容量とアンプ型式は空にする。
    アンプ・SERVO・電圧など、他の列はそのまま。cnc が空でなければ CNCユニット列も直す。戻り値は控えのパス。
    """
    import time
    p = Path(master_path)
    ctl = next((c for c in controllers.load_controllers(str(p)) if str(c.unit).strip() == str(unit).strip()), None)
    if ctl is None:
        raise FirstUnitError(f"号機{unit}はマスタにありません")
    stamp = time.strftime("%Y%m%d_%H%M%S", now or time.localtime())
    bak, n = p.with_name(f"{p.name}.bak_{stamp}"), 1
    while bak.exists():
        n += 1
        bak = p.with_name(f"{p.name}.bak_{stamp}_{n}")
    shutil.copy2(p, bak)
    have = set(order if order is not None else caps)
    for a in controllers.AXES:
        if a in caps:
            ctl.caps[a] = controllers._norm_cap(caps[a])
        elif a not in have:
            ctl.caps[a], ctl.amps[a] = "", ""
    if cnc:
        ctl.cnc = cnc
    controllers.upsert_controller(str(p), ctl)
    return str(bak)
