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
    # 型式マスタ（相対ならアプリフォルダ基準）
    conditions_csv=r"マスタ/測定条件.csv",
    judgement_csv=r"マスタ/合否判定.csv",
    # ユーザー登録の測定条件（アプリから登録/編集。回転と傾斜を別ファイルに分離）
    user_rotary_csv=r"マスタ/ユーザー回転条件.csv",
    user_tilt_csv=r"マスタ/ユーザー傾斜条件.csv",
    # 再現性の条件（型式ごと。回転・傾斜を別ファイルに分離）
    user_rotary_repeat_csv=r"マスタ/ユーザー回転再現条件.csv",
    user_tilt_repeat_csv=r"マスタ/ユーザー傾斜再現条件.csv",
    # 過去データ閲覧の既定件数
    recent_count=10,
    # 画面のテーマ（見た目）。themes.THEME_NAMES の名前。"システム"=OS既定
    ui_theme="ネイビー・コーポレート",
    # 画面の基準文字サイズ[pt]（7〜22）
    ui_font_pt=7,
    # 結果表に ホイール/ウォーム単品のバックラッシも出すか（既定は総合のみ）
    show_component_backlash=False,
    # P補正（ピッチエラー補正表）の既定値
    p_interval=100000,   # 補正間隔（0.0001°単位。100000=10°）
    p_unit=0.001,        # 補正単位[°]
    # SwitchBot（測定温度の自動取得＝温湿度計のID / 自動測定＝Botで機械の
    # 起動ボタンを物理押し）。トークン等はSwitchBotアプリの開発者向け
    # オプションから取得して設定する
    switchbot_token="",
    switchbot_secret="",
    switchbot_device="",        # 温湿度計 or Bot のデバイスID（クラウドAPI用）
    switchbot_use_ble=False,    # True: PCのBluetoothからBotを直接操作（ハブ不要）
    switchbot_ble_mac="",
    switchbot_ble_password="",
    switchbot_patterns={
        "1回押し": [0.0],
        "2回押し（5秒間隔）": [5.0, 0.0],
        "2回押し（3秒間隔）": [3.0, 0.0],
        "3回押し（3秒間隔）": [3.0, 3.0, 0.0],
    },
    switchbot_pattern_name="1回押し",
    switchbot_dry_run=False,     # True: 空打ち（実際には押さずにリハーサル）
    # 自動測定（XR20監視ツールの状態機械を移植）:
    # 取込開始→SwitchBotで機械起動→完了→傾き/精度判定→NGなら自動で再測定
    auto_max_retries=2,            # 傾きNG時の自動再測定の上限回数
    auto_max_precision_retries=1,  # 精度NG（単一/隣接規格超え）時の上限回数
    auto_wait_before_press=1.0,  # 取込開始からSwitchBot押下までの待ち[秒]
    # 生データ編集（管理者モード）のパスワード
    admin_password="0925",
    # FANUC測定プログラム生成
    fanuc_axis="X",
    fanuc_preswing=10.0,        # 測定点の前振り量[°]（バックラッシュ消し）
    fanuc_reset_swing=10.0,     # カウンターリセットの振り量[°]（前振りとは別に設定可）
    fanuc_swing_dwell_sec=1.0,  # 振り後のドゥエル[秒]（バックラッシュ消し後・測定無関係＝小さめ）
    fanuc_dwell_sec=1.0,        # 測定ドゥエル[秒]（測定点で静止・読取前。G04 X… 1.0〜5.0）
    fanuc_mcode="M80",          # 完了信号Mコード（カウンターへ送る）
    fanuc_use_subprogram=True,  # True: 再現をサブプロ / False: 1本に展開
    fanuc_main_number=100,
    fanuc_rep_sub_number=9001,
    fanuc_return_to_start=True,
    fanuc_counter_reset=True,   # 先頭にカウンターリセット（M00）を入れる
    # クランプ分割: 各測定点でクランプ→読取→アンクランプ（軸ロックして測る）
    fanuc_clamp_enabled=False,
    fanuc_clamp_mcode="M10",      # クランプ信号Mコード（軸ごとに決まる。例 4軸 M10）
    fanuc_unclamp_mcode="M11",    # アンクランプ信号Mコード（例 4軸 M11）
    fanuc_clamp_dwell_sec=1.0,    # クランプ信号後のドゥエル[秒]（すぐ締まらないので待つ）
    fanuc_unclamp_dwell_sec=1.0,  # アンクランプ信号後のドゥエル[秒]（次の動き前の緩み待ち）
    # 測定プログラムの機械への送信（カード不要・LAN）。"folder"=共有フォルダ / "ftp"
    nc_send_method="folder",
    nc_send_folder="",   # 共有フォルダのパス（例 \\<機械IP>\nc や Z:\NC）
    nc_ftp_host="",      # 機械のIPアドレス（FTP方式）
    nc_ftp_port=21,
    nc_ftp_user="",
    nc_ftp_password="",
    nc_ftp_dir="",       # アップロード先ディレクトリ（空=ルート）
    nc_ftp_passive=True,
    # FANUCアラーム検索: 内蔵辞書に追加で読むユーザー/メーカー固有CSV（任意）
    fanuc_alarm_csv=r"マスタ/FANUCアラーム.csv",
    # Webモニタ（離れたPCのブラウザから閲覧・再測定指示）。社内LAN内での利用前提
    web_enabled=False,
    web_port=8765,
    web_token="",   # 設定すると ?token=… 付きアクセスのみ許可
    # product-inspection（Firebase）連携: セーブ時に測定結果を
    # rotaryMeasurements コレクションへ送信する
    webapp_sync_enabled=False,
    webapp_api_key="AIzaSyDiIS-TDH6MgXaLvG9T2VRioFDomQ_zQ9E",  # product-inspectionと同じ
    webapp_project_id="inspection-time-c4fd3",
    webapp_data_id="product-inspection-v1",
    webapp_collection="rotaryMeasurements",
    webapp_send_png=True,  # グラフ画像も送る（1通あたり約20〜30KB）
    # Webアプリ連動（時間取り→測定→時間取り終了）。Firestoreを指令バスにする
    webapp_commands_enabled=False,   # 指令の監視を有効化
    webapp_station="",               # このPCのステーションID（例 "PC-3" や機械名）
    webapp_command_collection="rotaryCommands",  # Web→アプリの指令
    webapp_event_collection="rotaryEvents",      # アプリ→Webのイベント
    webapp_command_poll_sec=3.0,
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
