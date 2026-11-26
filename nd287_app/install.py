# -*- coding: utf-8 -*-
"""各PCに入れる（配布セットの「インストール.bat」から、アプリの exe 自身が `--install` で動く）。

PowerShell・VBScript は使わない。会社のPCではセキュリティソフト（Cylance）が
PowerShell の実行を止めるため（2026-11-23 CR0579 で確認: "Cylance Script Control has blocked
PowerShell from running."）。

すること（%LOCALAPPDATA%\\ND287分割測定 に入れる。管理者の権限は要らない）:
1. 初めて入れるときは、前のやり方（ND287分割測定.bat・exe を置いたフォルダ）で使っていた
   設定（settings.json）・測定データ・機械の控え・マスタを探して写す（元は消さない・上書きしない）。
2. 配布セットの app\\ を写す（マスタ以外は新しいもので置き換え。settings.json・測定データ等には触らない）。
3. マスタ\\ は「このPCに無いファイルだけ」入れる。
4. デスクトップとスタートメニューにショートカットを作る。
結果は窓で知らせ、入れた先の install.log にも残す。
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

INSTALL_DIR_NAME = "ND287分割測定"
EXE_NAME = "nd287_bunkatsu.exe"
SHORTCUT_NAME = "ND287分割測定.lnk"
PRACTICE_SHORTCUT_NAME = "ND287分割測定（練習）.lnk"
MASTER = "マスタ"
BASIC = "BASIC"                       # 配布セットに入れる BASIC（号機ごとの基本パラメータ）
# app\\ から写すとき別扱いにするもの（無いファイルだけ入れる。このPCで直したものを上書きしない）
KEEP_DIRS = (MASTER, BASIC, "写真")    # 写真: このPCで撮り直したものは上書きしない
# 前のやり方から引き継ぐフォルダ（無いファイルだけ写す）
CARRY_DIRS = ("測定データ", "機械バックアップ", MASTER)
# 前のフォルダを探すときに入らないフォルダ
SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", "node_modules", "AppData", "dist", "build",
             "配布", "測定データ", "機械バックアップ"}


class InstallError(Exception):
    pass


def default_dest():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / INSTALL_DIR_NAME


# ---------------------------------------------------------------------------
# 写す
# ---------------------------------------------------------------------------

def copy_missing(src, dst):
    """src の下のファイルを dst へ。dst に同じものがあれば写さない（上書きしない）。写した数。"""
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        return 0
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        for f in files:
            to = dst / rel / f
            if not to.exists():
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(root) / f, to)
                n += 1
    return n


def copy_app(src, dst):
    """app\\ の中身をマスタ以外すべて写す（同じ名前は新しいもので置き換え）。写したファイル数。"""
    src, dst = Path(src), Path(dst)
    n = 0
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        if rel == Path("."):
            dirs[:] = [d for d in dirs if d not in KEEP_DIRS]
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            to = dst / rel / f
            try:
                shutil.copy2(Path(root) / f, to)
            except PermissionError:
                if f.lower() == EXE_NAME:
                    raise InstallError("ND287 分割測定アプリが開いています。閉じてから、もう一度入れてください。")
                raise
            n += 1
    return n


# ---------------------------------------------------------------------------
# 設定（settings.json）への登録
# ---------------------------------------------------------------------------

def _has_files(d):
    try:
        return Path(d).is_dir() and any(p.is_file() for p in Path(d).iterdir())
    except OSError:
        return False


# 特注パラ(Excel)の置き場の候補（Google ドライブ）。このPCに無ければ登録しない。2026-11-24 RC が実在を確認
CUSTOM_DIR_CANDIDATES = [r"G:\マイドライブ\特注パラメータ"]


def register_locations(dst):
    """BASIC の場所と製品データの場所を、設定に登録する。報告の行を返す。

    - param_basic_dir: 未設定、または存在しないとき、入れた BASIC（dst\\BASIC）にする。
      すでに存在する場所が入っていれば、そのまま（このPCで決めた場所を勝手に変えない）。
    - param_product_dir: 未設定のとき、会社の共有サーバの既定値にする。
    settings.json が無いPCでは、この2つだけ書いた設定を作る（足りない項目は起動時に既定値で補われる）。
    """
    from . import settings as S
    dst = Path(dst)
    sp = dst / "settings.json"
    data, existed = {}, sp.is_file()
    if existed:
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ["設定（settings.json）を読めなかったので、BASIC・製品データの場所は登録していません。"]
    lines, changed = [], False
    bd = str(data.get("param_basic_dir") or "")
    if (not bd or not Path(bd).is_dir()) and _has_files(dst / BASIC):
        data["param_basic_dir"] = str(dst / BASIC)
        lines.append(f"BASICの場所を登録しました: {dst / BASIC}")
        changed = True
    pd = str(data.get("param_product_dir") or "")
    if not pd:
        data["param_product_dir"] = S.DEFAULTS["param_product_dir"]
        lines.append(f"製品データの場所を登録しました: {data['param_product_dir']}")
        changed = True
    cd = str(data.get("param_custom_dir") or "")
    if not cd:                                   # 特注パラ(Excel。DDなど)の置き場。見つかったときだけ登録する
        for cand in CUSTOM_DIR_CANDIDATES:
            if Path(cand).is_dir():
                data["param_custom_dir"] = cand
                lines.append(f"特注パラの場所を登録しました: {cand}")
                changed = True
                break
    if changed:
        if existed:
            shutil.copy2(sp, dst / "settings.json.bak_install")
        tmp = sp.with_name(sp.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, sp)
    return lines


# ---------------------------------------------------------------------------
# 前のやり方で使っていたフォルダ
# ---------------------------------------------------------------------------

def is_old_install(d):
    d = Path(d)
    return (d / "settings.json").is_file() and (
        (d / "ND287分割測定.bat").is_file() or (d / "nd287_app").is_dir() or (d / EXE_NAME).is_file())


def default_roots():
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    roots = [(home / "Desktop", 4), (home / "Documents", 4), (home / "Downloads", 4)]
    od = os.environ.get("OneDrive")
    if od:
        roots += [(Path(od) / "Desktop", 4), (Path(od) / "Documents", 4)]
    roots.append((home, 1))
    return roots


def find_old_install(roots=None, exclude=()):
    """前に使っていたフォルダ。いくつもあれば settings.json が一番新しいもの。無ければ None。"""
    roots = default_roots() if roots is None else roots
    excl = {os.path.normcase(os.path.abspath(str(e))) for e in exclude if e}
    found = {}
    for root, depth in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        base = len(root.parts)
        for cur, dirs, _files in os.walk(root):
            curp = Path(cur)
            if len(curp.parts) - base >= depth:
                dirs[:] = []
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            key = os.path.normcase(os.path.abspath(cur))
            if key in excl or key in found:
                continue
            if is_old_install(curp):
                try:
                    found[key] = (curp, (curp / "settings.json").stat().st_mtime)
                except OSError:
                    pass
    if not found:
        return None
    return max(found.values(), key=lambda t: t[1])[0]


def migrate(old, dst):
    """前のフォルダから引き継ぐ（新しい場所に設定がまだ無いときだけ）。報告の行。"""
    old, dst = Path(old), Path(dst)
    if (dst / "settings.json").exists():
        return []
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(old / "settings.json", dst / "settings.json")
    parts = ["設定（settings.json）"]
    for sub in CARRY_DIRS:
        c = copy_missing(old / sub, dst / sub)
        if c:
            parts.append(f"{sub}（{c} 件）")
    return [f"前の場所から引き継ぎました: {'・'.join(parts)}",
            f"  前の場所: {old}（元のファイルは消していません）"]


# ---------------------------------------------------------------------------
# ショートカット（Windows の IShellLink を ctypes で呼ぶ。PowerShell を使わない）
# ---------------------------------------------------------------------------

def create_shortcut(lnk, target, workdir="", desc="", args=""):
    import ctypes
    from ctypes import HRESULT, POINTER, WINFUNCTYPE, byref, c_int, c_void_p, c_wchar_p

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort),
                    ("d4", ctypes.c_ubyte * 8)]

    ole32 = ctypes.OleDLL("ole32")

    def guid(s):
        g = GUID()
        ole32.CLSIDFromString(c_wchar_p(s), byref(g))
        return g

    def method(obj, index, *argtypes):
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        fn = WINFUNCTYPE(HRESULT, c_void_p, *argtypes)(vtbl[index])
        return lambda *a: fn(obj, *a)

    try:
        ole32.CoInitialize(None)
    except OSError:
        pass                                # すでに初期化されている（Qt など）
    link = c_void_p()
    ole32.CoCreateInstance(byref(guid("{00021401-0000-0000-C000-000000000046}")), None, 1,
                           byref(guid("{000214F9-0000-0000-C000-000000000046}")), byref(link))
    try:
        method(link, 20, c_wchar_p)(str(target))                 # SetPath
        if workdir:
            method(link, 9, c_wchar_p)(str(workdir))             # SetWorkingDirectory
        if desc:
            method(link, 7, c_wchar_p)(str(desc))                # SetDescription
        if args:
            method(link, 11, c_wchar_p)(str(args))               # SetArguments
        pf = c_void_p()
        iid_pf = guid("{0000010B-0000-0000-C000-000000000046}")          # IPersistFile
        method(link, 0, c_void_p, POINTER(c_void_p))(ctypes.addressof(iid_pf), byref(pf))  # QueryInterface
        try:
            Path(lnk).parent.mkdir(parents=True, exist_ok=True)
            method(pf, 6, c_wchar_p, c_int)(str(lnk), 1)          # IPersistFile::Save
        finally:
            method(pf, 2)()                                      # Release
    finally:
        method(link, 2)()                                        # Release


def shortcut_places():
    out = []
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    desktop = home / "Desktop"
    od = os.environ.get("OneDrive")
    if not desktop.is_dir() and od and (Path(od) / "Desktop").is_dir():
        desktop = Path(od) / "Desktop"
    out.append(desktop)
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return out


# ---------------------------------------------------------------------------
# 全体
# ---------------------------------------------------------------------------

def install(src, dst=None, *, roots=None, shortcuts=True, make_shortcut=None):
    """src（配布セットの app\\）を dst に入れる。報告の行を返す。失敗は InstallError。"""
    src = Path(src)
    dst = Path(dst) if dst else default_dest()
    if not (src / EXE_NAME).is_file():
        raise InstallError(f"入れるもの（{EXE_NAME}）が {src} にありません。配布セットのフォルダごとコピーしてください。")
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        raise InstallError("入れたあとのアプリからは入れられません。配布セットの「インストール.bat」を使ってください。")
    update = (dst / EXE_NAME).is_file()
    dst.mkdir(parents=True, exist_ok=True)
    lines = []
    if not (dst / "settings.json").exists():
        old = find_old_install(roots, exclude=(dst, src, src.parent))
        if old is not None:
            lines += migrate(old, dst)
    copy_app(src, dst)
    added = copy_missing(src / MASTER, dst / MASTER)
    lines.append(f"マスタ: 新しく入れたもの {added} 件" if added else "マスタ: このPCのものをそのまま使います")
    if (src / BASIC).is_dir():
        nb = copy_missing(src / BASIC, dst / BASIC)
        lines.append(f"BASIC: 新しく入れたもの {nb} 件" if nb else "BASIC: このPCのものをそのまま使います")
    if (src / "写真").is_dir():                        # はじめてモードの写真（このPCで差し替えたものは残す）
        npic = copy_missing(src / "写真", dst / "写真")
        lines.append(f"写真: 新しく入れたもの {npic} 枚" if npic else "写真: このPCのものをそのまま使います")
    lines += register_locations(dst)
    focas = (dst / "focas" / "python32" / "python.exe").is_file() and (dst / "focas" / "Fwlib32.dll").is_file()
    lines.append("FOCAS（機械と直接つなぐ）: 使えます（32bit Python と Fwlib32.dll も入れました）" if focas
                 else "FOCAS（機械と直接つなぐ）: この配布セットには入っていません")
    if shortcuts:
        mk = make_shortcut or create_shortcut
        made = []
        for place in shortcut_places():
            try:
                mk(place / SHORTCUT_NAME, dst / EXE_NAME, dst, "ND287 分割測定アプリ")
                mk(place / PRACTICE_SHORTCUT_NAME, dst / EXE_NAME, dst,
                   "ND287 分割測定アプリ（練習モード。本番の設定・データ・機械を汚さない）", "--practice")
                made.append(str(place))
            except Exception as e:                       # ショートカットが作れなくてもアプリは入っている
                lines.append(f"ショートカットを作れませんでした（{place}）: {e}")
        if made:
            lines.append("ショートカット: デスクトップ・スタートメニューの「ND287分割測定」と「ND287分割測定（練習）」")
    head = f"ND287 分割測定アプリを{'更新しました' if update else '入れました'}。\n場所: {dst}"
    return [head] + lines


def write_log(dst, text):
    try:
        dst.mkdir(parents=True, exist_ok=True)
        with open(dst / "install.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text + "\n\n")
    except OSError:
        pass


def main(argv=None):
    """`nd287_bunkatsu.exe --install [--quiet] [--dest フォルダ]`。終了コード 0=入れた / 1=失敗。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in argv
    dst = default_dest()
    if "--dest" in argv:
        i = argv.index("--dest")
        if i + 1 < len(argv):
            dst = Path(argv[i + 1])
    from . import settings as S
    src = S.app_dir()
    try:
        text, ok = "\n".join(install(src, dst)), True
    except InstallError as e:
        text, ok = f"入れられませんでした。\n\n{e}", False
    except Exception as e:                               # 想定外も、窓で知らせる（黙って落ちない）
        text, ok = f"入れられませんでした。\n\n{type(e).__name__}: {e}", False
    write_log(dst, text)
    if quiet:
        print(text)
    else:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        box = QtWidgets.QMessageBox.information if ok else QtWidgets.QMessageBox.critical
        box(None, "ND287 分割測定アプリ インストール", text)
        del app
    return 0 if ok else 1
