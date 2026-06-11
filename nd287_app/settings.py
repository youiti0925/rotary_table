# -*- coding: utf-8 -*-
"""アプリ設定の保存・読み込み（settings.json）

設定ファイルはアプリ本体と同じフォルダに置く（exe化時はexeの隣）。
"""

import copy
import json
import sys
from pathlib import Path

DEFAULTS = dict(
    port="auto",       # "auto" = COMポート自動検出、または "COM3" 等の明示指定
    baudrate=9600,
    parity="E",        # N / E / O
    # 接続プロファイル: X32(USB直結) と X31(変換器経由) で別の設定を保持し、
    # 画面の接続先プルダウンで切り替える
    active_profile="X32",
    profiles=dict(
        X32=dict(port="auto", baudrate=9600, parity="E"),
        X31=dict(port="auto", baudrate=9600, parity="E"),
    ),
    save_root="測定データ",  # セーブ先ルート（相対ならアプリフォルダ基準）
    # 旧形式(.BS)の保存先。空にすると.BSを書かない。
    # 検査表システムが読むフォルダに <機番>.BS で保存される
    bs_save_root=r"K:\工機部内\共用\システム\検査表\データ",
    # 温度別の合否規格 [秒]。ホイールが合金製のため熱膨張で、ホイール・
    # ウォーム・総合（真の最大最小）のいずれも温度で変わり、温度帯ごとに
    # 規格が異なる。規格が空の項目は判定しない。
    # ※下記は仮の値。実際の規格に合わせて settings.json で書き換えること。
    judgement_spec=dict(
        wheel_backlash=[
            dict(temp_min=0.0, temp_max=15.0, min=0.0, max=30.0),
            dict(temp_min=15.0, temp_max=25.0, min=0.0, max=25.0),
            dict(temp_min=25.0, temp_max=40.0, min=0.0, max=20.0),
        ],
        worm_backlash=[
            dict(temp_min=0.0, temp_max=15.0, min=0.0, max=20.0),
            dict(temp_min=15.0, temp_max=25.0, min=0.0, max=15.0),
            dict(temp_min=25.0, temp_max=40.0, min=0.0, max=12.0),
        ],
        true=[
            dict(temp_min=0.0, temp_max=15.0, min=-30.0, max=30.0),
            dict(temp_min=15.0, temp_max=25.0, min=-25.0, max=25.0),
            dict(temp_min=25.0, temp_max=40.0, min=-20.0, max=20.0),
        ],
    ),
)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller製exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    return app_dir() / "settings.json"


PROFILE_LABELS = {"X32": "X32（USB直結）", "X31": "X31（変換器経由）"}

CONN_KEYS = ("port", "baudrate", "parity")


def apply_active_profile(settings: dict):
    """アクティブなプロファイルの接続設定をトップレベルに反映する。

    既存コードは settings["port"] 等を直接読むため、切り替え時はこれを呼ぶ。
    """
    profile = settings["profiles"][settings["active_profile"]]
    for key in CONN_KEYS:
        settings[key] = profile[key]


def load_settings(path=None) -> dict:
    p = Path(path) if path else settings_path()
    settings = copy.deepcopy(DEFAULTS)
    raw = {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        settings.update(raw)
    except FileNotFoundError:
        pass
    except Exception:
        pass  # 壊れたファイルでも既定値で起動できるようにする

    # プロファイルの補完（欠けたキーは既定値で埋める）
    profiles = settings.get("profiles") or {}
    for key in ("X32", "X31"):
        merged = dict(DEFAULTS["profiles"][key])
        merged.update(profiles.get(key) or {})
        profiles[key] = merged
    settings["profiles"] = profiles
    if settings.get("active_profile") not in PROFILE_LABELS:
        settings["active_profile"] = "X32"
    # 旧形式（プロファイル無しでトップレベルにport等だけある）からの移行
    if "profiles" not in raw and any(k in raw for k in CONN_KEYS):
        profiles[settings["active_profile"]].update(
            {k: raw[k] for k in CONN_KEYS if k in raw}
        )
    apply_active_profile(settings)
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
