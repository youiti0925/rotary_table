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
