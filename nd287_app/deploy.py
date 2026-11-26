# -*- coding: utf-8 -*-
"""配布セット（各PCにダブルクリックで入れるためのフォルダ）を作る。

作るのは「配布セットを作る.bat」から（exe を作ったあとに呼ばれる）。手で呼ぶなら
    .venv\\Scripts\\python -m nd287_app.deploy  [出力先]

できるフォルダ（出力先。既定は リポジトリ\\配布\\ND287分割測定）:
    インストール.bat          … 各PCでこれをダブルクリック
    はじめにお読みください.txt
    app\\
        nd287_bunkatsu.exe    … アプリ本体（Python 不要）
        focas_helper.py       … 機械と話す小さなプログラム（FOCAS）
        focas\\python32\\       … FOCAS 用の 32bit Python（このPCに入っているものを写す）
        focas\\Fwlib32.dll ほか … FANUC のライブラリ（fwlib*.dll をまとめて写す）
        マスタ\\               … 型式・号機などのマスタ（各PCに無いものだけ入れる）

32bit Python と Fwlib32.dll は「このPC（配布セットを作るPC）」に入っているものを写す。
FOCAS を使わない配布にしたいときは --no-focas。
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import focas_client
from . import settings as S

EXE_NAME = "nd287_bunkatsu.exe"
# 配布セットの一番上に置くもの（入れる処理は exe 自身の --install。PowerShell は使わない）
INSTALLER_PARTS = ("インストール.bat", "はじめにお読みください.txt")
INSTALL_DIR_NAME = "ND287分割測定"
# 32bit Python を写すとき入れないもの（大きいだけで FOCAS には要らない）
PY_SKIP = {"Doc", "Tools", "tcl", "Scripts", "include", "libs", "__pycache__"}
PY_SKIP_LIB = {"site-packages", "test", "idlelib", "tkinter", "turtledemo", "ensurepip",
               "__pycache__", "lib2to3", "pydoc_data"}


class DeployError(Exception):
    pass


def repo_dir():
    return Path(__file__).resolve().parent.parent


def find_python32_dir(settings):
    """FOCAS 用 32bit Python のフォルダ（python.exe のあるところ）。無ければ None。"""
    exe = focas_client.resolve_python32(str(settings.get("focas_python32") or ""))
    return Path(exe).parent if exe else None


def find_fwlib_dir(settings):
    """Fwlib32.dll のあるフォルダ。設定 → SERVO GUIDE → SysWOW64（ヘルパーと同じ順）。無ければ None。"""
    from .focas_helper import DLL_NAME, DLL_SEARCH
    d = str(settings.get("focas_dll_dir") or "")
    for cand in ([d] if d else []) + list(DLL_SEARCH):
        if cand and (Path(cand) / DLL_NAME).is_file():
            return Path(cand)
    return None


def fwlib_files(folder):
    """フォルダの中の FANUC ライブラリ（fwlib*.dll。大文字小文字は問わない）。"""
    folder = Path(folder)
    out = [p for p in folder.iterdir()
           if p.is_file() and p.name.lower().startswith("fwlib") and p.suffix.lower() == ".dll"]
    return sorted(out, key=lambda p: p.name.lower())


def copy_python32(src, dst):
    """32bit Python のフォルダを写す（要らないものは除く）。写したファイル数。"""
    src, dst = Path(src), Path(dst)
    if not (src / "python.exe").is_file():
        raise DeployError(f"32bit Python が見つかりません: {src}")
    n = 0
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        if rel == Path("."):
            dirs[:] = [d for d in dirs if d not in PY_SKIP]
        elif rel.parts[0] == "Lib" and len(rel.parts) == 1:
            dirs[:] = [d for d in dirs if d not in PY_SKIP_LIB]
        else:
            dirs[:] = [d for d in dirs if d != "__pycache__"]
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            if f.endswith(".pyc"):
                continue
            shutil.copy2(Path(root) / f, dst / rel / f)
            n += 1
    return n


def find_basic_dir(settings):
    """BASIC（F10BASIC.dat など）のフォルダ。このPCの設定 param_basic_dir。無ければ None。"""
    d = str(settings.get("param_basic_dir") or "")
    if d and Path(d).is_dir() and any(p.is_file() for p in Path(d).iterdir()):
        return Path(d)
    return None


def build_bundle(out, *, exe, helper, masters, py32_dir=None, fwlib_dir=None, installer_dir=None,
                 basic_dir=None):
    """配布セットを out に作る。out の中は作り直す。戻り値は報告の行のリスト。"""
    out = Path(out)
    exe, helper, masters = Path(exe), Path(helper), Path(masters)
    installer_dir = Path(installer_dir or (repo_dir() / "installer"))
    if not exe.is_file():
        raise DeployError(f"exe がありません: {exe}（先に exe を作ってください）")
    if not helper.is_file():
        raise DeployError(f"focas_helper.py がありません: {helper}")
    for name in INSTALLER_PARTS:
        if not (installer_dir / name).is_file():
            raise DeployError(f"インストーラの部品がありません: {installer_dir / name}")
    if out.exists():
        shutil.rmtree(out)
    app = out / "app"
    app.mkdir(parents=True)
    lines = []
    shutil.copy2(exe, app / EXE_NAME)
    shutil.copy2(helper, app / "focas_helper.py")
    lines.append(f"アプリ本体: {exe.name}")
    if masters.is_dir():
        shutil.copytree(masters, app / "マスタ",
                        ignore=shutil.ignore_patterns("ユーザー*.csv", "FOCAS*ログ.csv", "*ログ.csv",
                                                      "FOCAS機械台帳.csv", "__pycache__"))
        lines.append("マスタ: " + "・".join(sorted(p.name for p in (app / "マスタ").iterdir())))
    photos = repo_dir() / "写真"                  # はじめてモードの写真（23号機の操作盤など。無ければ絵のまま）
    if photos.is_dir() and any(photos.iterdir()):
        shutil.copytree(photos, app / "写真")
        lines.append("写真: " + "・".join(sorted(p.stem for p in (app / "写真").iterdir())))
    if basic_dir is not None:
        shutil.copytree(basic_dir, app / "BASIC")
        nb = sum(1 for p in (app / "BASIC").rglob("*") if p.is_file())
        lines.append(f"BASIC: {basic_dir}（{nb} ファイル。入れるとき場所も自動で登録）")
    else:
        lines.append("BASIC: 入れていません（各PCで「場所」欄に BASIC のフォルダを入れてください）")
    if py32_dir is not None:
        n = copy_python32(py32_dir, app / "focas" / "python32")
        lines.append(f"FOCAS 用 32bit Python: {py32_dir}（{n} ファイル）")
    else:
        lines.append("FOCAS 用 32bit Python: 入れていません（FOCAS は使えません）")
    if fwlib_dir is not None:
        files = fwlib_files(fwlib_dir)
        for p in files:
            shutil.copy2(p, app / "focas" / p.name)
        lines.append(f"FANUC ライブラリ: {fwlib_dir}（" + "・".join(p.name for p in files) + "）")
    else:
        lines.append("FANUC ライブラリ（Fwlib32.dll）: 入れていません（FOCAS は使えません）")
    for name in INSTALLER_PARTS:
        shutil.copy2(installer_dir / name, out / name)
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="各PCに入れる配布セットを作る")
    ap.add_argument("out", nargs="?", default=str(repo_dir() / "配布" / INSTALL_DIR_NAME))
    ap.add_argument("--exe", default=str(repo_dir() / "dist" / EXE_NAME))
    ap.add_argument("--no-focas", action="store_true", help="32bit Python と Fwlib32.dll を入れない")
    args = ap.parse_args(argv)
    settings = S.load_settings()
    py32 = fw = None
    if not args.no_focas:
        py32 = find_python32_dir(settings)
        fw = find_fwlib_dir(settings)
        missing = []
        if py32 is None:
            missing.append("32bit Python（FOCAS 用）")
        if fw is None:
            missing.append("Fwlib32.dll")
        if missing:
            print("[エラー] このPCに " + "・".join(missing) + " が見つかりません。")
            print("  FOCAS の画面の「はじめての設定」で場所を選んでから、もう一度作ってください。")
            print("  FOCAS 無しで配る場合は --no-focas を付けてください。")
            return 2
    try:
        lines = build_bundle(args.out, exe=args.exe,
                             helper=repo_dir() / "nd287_app" / "focas_helper.py",
                             masters=repo_dir() / "マスタ", py32_dir=py32, fwlib_dir=fw,
                             basic_dir=find_basic_dir(settings))
    except DeployError as e:
        print(f"[エラー] {e}")
        return 1
    print(f"配布セットを作りました: {args.out}")
    for ln in lines:
        print("  " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
