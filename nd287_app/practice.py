# -*- coding: utf-8 -*-
"""練習モード（2026-11-26）。本番の設定・マスタ・測定データ・機械を一切汚さずに、アプリを最後まで触れる。

起動: `nd287_bunkatsu.exe --practice`（デスクトップの「ND287分割測定（練習）」・「その他 ▾」→「練習モードで開く…」）。
- アプリの隣の `練習` フォルダを、アプリの場所（app_dir）として使う。設定・マスタ・写真は、初めて開いたとき本番から写す。
  そのあとの保存・登録・記録は全部 `練習` の中に入る（本番の settings.json・マスタ・測定データは開かない・書かない）。
- 表示器は疑似（ダミー）。機械（FOCAS）にはつながない（つなごうとすると断る）。検査表の保存先・送信もしない。
- 窓の題名と上の帯に「練習モード」と出す。
"""
import json
import os
import shutil
from pathlib import Path

ENV = "ND287_PRACTICE_DIR"
DIR_NAME = "練習"
COPY_DIRS = ("マスタ", "写真")

# 練習で必ず切るもの（本番の機械・共有の場所・外へ送るもの）
SAFE = dict(
    focas_host="", focas_auto_connect=False, focas_auto_find=False, nc_ftp_host="",
    save_root="測定データ", bs_save_root="", focas_backup_dir="機械バックアップ",
    webapp_sync_enabled=False, webapp_commands_enabled=False, web_enabled=False,
    switchbot_dry_run=True,
)


def active():
    """いま練習モードか。"""
    return bool(os.environ.get(ENV))


def practice_dir(real_dir):
    return Path(real_dir) / DIR_NAME


def _copy_missing(src, dst):
    n = 0
    if not Path(src).is_dir():
        return 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        for f in files:
            to = Path(dst) / rel / f
            if not to.exists():
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(root) / f, to)
                n += 1
    return n


def prepare(real_dir):
    """練習の場所を用意して、その場所を返す。本番のものは読むだけ（写すだけ）で、書き換えない。"""
    real_dir = Path(real_dir)
    pdir = practice_dir(real_dir)
    pdir.mkdir(parents=True, exist_ok=True)
    for d in COPY_DIRS:
        _copy_missing(real_dir / d, pdir / d)
    sp = pdir / "settings.json"
    data = {}
    if sp.is_file():
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    elif (real_dir / "settings.json").is_file():
        try:
            data = json.loads((real_dir / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    data.update(SAFE)                       # 毎回、切るものは切り直す（練習の中で入れても、次に開くと戻る）
    data["param_out_folder"] = str(pdir / "出力")       # かんたん作成で作るファイルも練習の場所へ（本番の Downloads に出さない）
    (pdir / "出力").mkdir(exist_ok=True)
    sp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.environ[ENV] = str(pdir)
    return pdir


def refuse_machine():
    """練習モードで機械につなごうとしたとき出す例外（focas_client から呼ぶ）。"""
    from .focas_client import FocasError
    return FocasError("refused", "練習モードでは機械につなぎません（本物の機械を守るため）。",
                      {"kind": "refused"}, "本番で使うときは、デスクトップの「ND287分割測定」から開いてください")


def launch(exe_or_python, args=()):
    """練習モードの窓を、別に開く（いまの窓はそのまま）。"""
    import subprocess
    import sys
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--practice", *args]
    else:
        cmd = [sys.executable, "-m", "nd287_app", "--practice", *args]
    env = {k: v for k, v in os.environ.items() if k != ENV}
    return subprocess.Popen(cmd, env=env, cwd=str(Path(exe_or_python).parent) if exe_or_python else None)
