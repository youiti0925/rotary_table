# -*- coding: utf-8 -*-
"""FOCAS で機械から取った「控え（バックアップ）」。Qt 非依存。

設計: docs/設計_FOCAS基本.md §6.3。

決まり:
1. BASIC フォルダ（param_basic_dir）・製品データのフォルダ（param_product_dir）と同じ場所・
   その中には置かない（断る）。設定を保存するときと、控えを書く直前の2回見る。
2. ファイル名を `F<数字>BASIC…` にしない（controllers.basic_file_for_unit が拾わないように）。
3. 受け取ったバイトを変えない（LF のまま保存。LF CR CR に直さない）。
4. 出どころは中身の形ではなく 控え_情報.json（"source": "focas"）で持つ。
5. 取った直後に点検する。指摘があれば「不完全」にして、書込に進ませない。
6. 号機番号は機械から読めない。号機マスタで候補を出すだけで、決め打ちしない。
"""

import csv
import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import fanuc_param

SOURCE = "focas"
INFO_NAME = "控え_情報.json"
TABLE_NAME = "控え表.txt"
TYPES_NAME = "型情報.csv"
PROGRAM_DIR = "プログラム"

# (命令の種別, 保存するファイル名, 必須か)。ワーク原点オフセットは戻り値 6 で読めなかった【事実】
DUMPS = (
    ("param", "パラメータ.DAT", True),
    ("pitch", "ピッチ誤差補正.DAT", True),
    ("macro", "カスタムマクロ変数.DAT", True),
    ("tool", "工具補正.DAT", True),
    ("workofs", "ワーク原点オフセット.DAT", False),
)


class BackupError(Exception):
    """控えを取れない（取らない）理由。message は画面に出せる日本語。"""


def _norm(p):
    s = os.path.normcase(os.path.abspath(os.path.expanduser(str(p))))
    return s.rstrip("\\/") or s


def _inside(child, parent):
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p + os.sep)


def resolve_root(settings):
    """控えの置き場所。相対ならアプリのフォルダ基準（save_root と同じ決まり）。"""
    from . import settings as S
    root = str(settings.get("focas_backup_dir") or "機械バックアップ")
    p = Path(root)
    return p if p.is_absolute() else S.app_dir() / p


def _forbidden_dirs(settings):
    from . import settings as S
    out = []
    for key in ("param_basic_dir", "param_product_dir"):
        v = str(settings.get(key) or "").strip()
        if v:
            p = Path(v)
            out.append((key, p if p.is_absolute() else S.app_dir() / p))
    return out


LABELS = {"param_basic_dir": "BASIC フォルダ", "param_product_dir": "製品データのフォルダ"}


def check_destination(root, settings):
    """控えの置き場所が BASIC・製品データのフォルダと重なっていたら断る。"""
    if not str(root).strip():
        raise BackupError("控えを置くフォルダが決まっていません")
    for key, p in _forbidden_dirs(settings):
        if _inside(root, p):
            raise BackupError(
                f"控えの置き場所が{LABELS[key]}（{p}）と同じか、その中です。"
                "機械から取った控えを置くと『実機の番号の和集合』という前提が崩れるので、"
                "別のフォルダを選んでください")
    return Path(root)


def _clean(name):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(name)).strip("_") or "x"


def machine_dir_name(ident, unit=""):
    """<CNC ID の先頭8桁>_<機種>[_号機NN]。例 3C7B5D01_31i_号機23"""
    cid = (ident.get("cnc_id") or "").split("-")[0] or "UNKNOWN"
    typ = ident.get("cnc_type") or "?"
    name = f"{cid}_{typ}i"
    if unit:
        name += f"_号機{unit}"
    return _clean(name)


def unit_candidates(ident, controllers):
    """機種・版・軸数で号機マスタを引く。号機番号は機械から読めないので候補だけ（決め打ちしない）。"""
    typ = str(ident.get("cnc_type") or "").strip()
    mt = str(ident.get("mt_type") or "").strip()
    ver = str(ident.get("version") or "").strip()
    axes = ident.get("axes")
    out = []
    for c in controllers or []:
        m = re.match(r"^\s*(\d+)i\s*-?\s*([A-Za-z]*)", str(c.cnc))
        if not m or m.group(1) != typ:
            continue
        if mt and m.group(2) and mt.upper() not in m.group(2).upper():
            continue
        if ver and str(c.ver).strip() and str(c.ver).strip() != ver:
            continue
        if axes and len(c.axes()) != axes:
            continue
        out.append(str(c.unit))
    return out


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _safe_rel(path):
    """機械のパス（//CNC_MEM/USER/PATH1/O901）→ 控えの中の相対パス。"""
    parts = [p for p in str(path).split("/") if p]
    return Path(PROGRAM_DIR, *[_clean(p) if not re.match(r"^[A-Za-z0-9_.\-]+$", p) else p
                               for p in parts])


class BackupResult:
    def __init__(self, folder, complete, problems, info):
        self.folder = folder
        self.complete = complete
        self.problems = problems
        self.info = info


def take_backup(client, root, settings, *, purpose="手動", unit="", host="", port=None,
                controllers=None, cancel=None, progress=None, now=None):
    """機械から控えを取って root の下に保存する。

    読むだけ（書込の命令は呼ばない）。戻り値は BackupResult。
    接続が切れたとき（lost）は例外のまま上へ出す（途中までの控えは「不完全」として残す）。
    """
    from . import focas_client as C
    prog = progress or (lambda done, total, note: None)
    stop = cancel or (lambda: False)
    check_destination(root, settings)

    ident = client.call("identify")
    stamp = (now or time.localtime())
    mdir = Path(root) / machine_dir_name(ident, unit)
    base = f"{time.strftime('%Y%m%d_%H%M%S', stamp)}_{_clean(purpose)}"
    folder, n = mdir / base, 1
    while folder.exists():
        n += 1
        folder = mdir / f"{base}_{n}"
    check_destination(folder, settings)            # 書く直前にもう一度（2回目）
    folder.mkdir(parents=True)

    files, unread, problems = {}, {}, []
    info = {
        "source": SOURCE, "purpose": purpose,
        "taken_at": time.strftime("%Y-%m-%d %H:%M:%S", stamp),
        "host": host, "port": port,
        "cnc": {k: ident.get(k) for k in ("cnc_type", "mt_type", "series", "version", "axes",
                                          "max_axis", "axis_names")},
        "cnc_id": ident.get("cnc_id"), "unit": unit,
        "unit_candidates": unit_candidates(ident, controllers),
    }

    def save(rel, data):
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)                         # 受け取ったバイトのまま
        files[str(Path(rel).as_posix())] = {"size": len(data), "sha256": _sha(data)}

    total_steps = 2 + len(DUMPS)
    step = 0

    def tick(note):
        nonlocal step
        step += 1
        prog(step, total_steps, note)

    cancelled = False
    try:
        info["program_info"] = client.call("prog_info")
        tick("プログラムの登録数")
        types = client.call("param_info")["types"]
        with open(folder / TYPES_NAME, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["番号", "型コード"])
            w.writerows(types)
        files[TYPES_NAME] = {"size": (folder / TYPES_NAME).stat().st_size,
                             "sha256": _sha((folder / TYPES_NAME).read_bytes())}
        info["types_count"] = len(types)
        tick("型情報")

        texts = {}
        for kind, fname, required in DUMPS:
            if stop():
                cancelled = True
                break
            try:
                res = client.call("param_dump", {"kind": kind})
                data = C.FocasClient.decode_data(res)
            except C.FocasError as e:
                if e.kind in ("lost", "not_connected", "timeout"):
                    raise
                unread[fname] = e.message
                if required:
                    problems.append(f"{fname} を読めませんでした: {e.message}")
                tick(fname)
                continue
            save(fname, data)
            texts[kind] = data
            if not data.rstrip(b"\0").endswith(b"%"):
                problems.append(f"{fname} の末尾が % ではありません（最後まで読めていません）")
            tick(fname)

        if "param" in texts:
            text = texts["param"].decode("cp932", errors="replace")
            nums = fanuc_param.numbers_in(text)
            info["numbers"] = len(nums)
            for p in fanuc_param.validate_prm(text):
                problems.append(f"パラメータ: {p}")
            if not nums:
                problems.append("パラメータの番号が1つも読めません")

        listed, saved = [], 0
        if not cancelled:
            tree = client.call("prog_list", {"path": "//CNC_MEM/", "recursive": True})["tree"]
            for t in tree:
                for pr in t["programs"]:
                    listed.append(t["path"] + pr["name"])
            for i, path in enumerate(listed):
                if stop():
                    cancelled = True
                    break
                try:
                    res = client.call("prog_read", {"path": path})
                    data = C.FocasClient.decode_data(res)
                except C.FocasError as e:
                    if e.kind in ("lost", "not_connected", "timeout"):
                        raise
                    unread[path] = e.message
                    problems.append(f"プログラム {path} を読めませんでした: {e.message}")
                    prog(step, total_steps + len(listed), path)
                    continue
                if not data.rstrip(b"\0").endswith(b"%"):
                    problems.append(f"プログラム {path} の末尾が % ではありません")
                save(_safe_rel(path), data)
                saved += 1
                prog(total_steps + i + 1, total_steps + len(listed), path)
        info["programs"] = {"listed": len(listed), "saved": saved, "paths": listed}
        if not cancelled and saved != len(listed):
            problems.append(f"プログラムの本数が合いません（一覧 {len(listed)} 本・保存 {saved} 本）")
    except C.FocasError as e:
        problems.append(f"途中で止まりました: {e.message}")
        info["aborted"] = e.message
        if e.kind not in ("lost", "timeout", "not_connected"):
            info["aborted_kind"] = e.kind
        _finish(folder, info, files, unread, problems, False)
        raise
    if cancelled:
        problems.append("途中で中止しました")
    complete = not problems
    _finish(folder, info, files, unread, problems, complete)
    return BackupResult(folder, complete, problems, info)


def _finish(folder, info, files, unread, problems, complete):
    info["files"] = files
    info["unread"] = unread
    info["problems"] = problems
    info["complete"] = bool(complete)
    (folder / INFO_NAME).write_text(json.dumps(info, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (folder / TABLE_NAME).write_text(table_text(info), encoding="utf-8")


def table_text(info):
    """人が読む一覧（控え表.txt）。"""
    c = info.get("cnc") or {}
    lines = [
        "機械から FOCAS で取った控え",
        f"取った日時: {info.get('taken_at')}",
        f"接続先: {info.get('host')}:{info.get('port')}",
        f"機種: {c.get('cnc_type')}i {c.get('mt_type')}  系列 {c.get('series')}  版 {c.get('version')}"
        f"  軸数 {c.get('axes')}",
        f"CNC ID: {info.get('cnc_id')}",
        f"号機: {info.get('unit') or '（決めていません）'}"
        f"  候補: {', '.join(info.get('unit_candidates') or []) or 'なし'}",
        f"目的: {info.get('purpose')}",
        f"プログラム: 一覧 {(info.get('programs') or {}).get('listed')} 本"
        f" / 保存 {(info.get('programs') or {}).get('saved')} 本",
        f"結果: {'完全' if info.get('complete') else '不完全'}",
        "",
    ]
    if info.get("problems"):
        lines.append("点検の指摘:")
        lines += [f"  ・{p}" for p in info["problems"]]
        lines.append("")
    if info.get("unread"):
        lines.append("読めなかったもの:")
        lines += [f"  ・{k}: {v}" for k, v in info["unread"].items()]
        lines.append("")
    lines.append("ファイル:")
    for k, v in (info.get("files") or {}).items():
        lines.append(f"  {k}  {v['size']:,} バイト  {v['sha256'][:12]}…")
    return "\n".join(lines) + "\n"


def load_info(folder):
    try:
        return json.loads((Path(folder) / INFO_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def latest_backup(root, cnc_id=None):
    """root の下の最新の控え（フォルダ, 情報）。cnc_id を渡すとその機械のものだけ。無ければ None。"""
    root = Path(root)
    if not root.is_dir():
        return None
    best = None
    for mdir in root.iterdir():
        if not mdir.is_dir():
            continue
        for b in mdir.iterdir():
            info = load_info(b) if b.is_dir() else None
            if not info or info.get("source") != SOURCE:
                continue
            if cnc_id and info.get("cnc_id") != cnc_id:
                continue
            key = (info.get("taken_at") or "", b.name)
            if best is None or key > best[0]:
                best = (key, b, info)
    return (best[1], best[2]) if best else None
