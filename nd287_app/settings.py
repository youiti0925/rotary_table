# -*- coding: utf-8 -*-
"""アプリ設定の保存・読み込み（settings.json）

設定ファイルはアプリ本体と同じフォルダに置く（exe化時はexeの隣）。
"""

import json
import sys
from pathlib import Path

DEFAULTS = dict(
    port="auto",       # "auto" = COMポート自動検出、または "COM3" 等の明示指定
    baudrate=9600,
    parity="E",        # N / E / O
    save_root="測定データ",  # セーブ先ルート（相対ならアプリフォルダ基準）
)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller製exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    return app_dir() / "settings.json"


def load_settings(path=None) -> dict:
    p = Path(path) if path else settings_path()
    settings = dict(DEFAULTS)
    try:
        settings.update(json.loads(p.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except Exception:
        pass  # 壊れたファイルでも既定値で起動できるようにする
    return settings


def save_settings(settings: dict, path=None):
    p = Path(path) if path else settings_path()
    p.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def resolve_save_root(settings: dict) -> Path:
    root = Path(settings.get("save_root") or DEFAULTS["save_root"])
    if not root.is_absolute():
        root = app_dir() / root
    return root
