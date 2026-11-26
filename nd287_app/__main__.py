# -*- coding: utf-8 -*-
"""エントリポイント

通信・保存先の設定は settings.json（設定画面から変更可）。
コマンドライン引数を指定した場合はそちらが優先される（settings.jsonは書き換えない）。

実行例:
    python -m nd287_app                    # 実機接続（設定: 既定はCOM自動検出、9600 8E1）
    python -m nd287_app --dummy            # 実機なしで画面確認
    python -m nd287_app --port COM3 --baud 19200 --parity N
    nd287_bunkatsu.exe --install [--quiet]  # このPCに入れる（配布セットの インストール.bat から）
    nd287_bunkatsu.exe --self-shots <フォルダ>   # 自分の画面を写して閉じる（練習の場所。self_shots.py）
    nd287_bunkatsu.exe --cli-write plan|write|check|status ...   # 機械へ書く（画面なし。cli_write.py）
"""

import argparse
import sys

from .nd287 import ND287Device, DummyDevice
from .settings import load_settings


def main():
    # 配布セットの「インストール.bat」から: このPCに入れる（install.py。PowerShell は使わない）
    if "--install" in sys.argv[1:]:
        from .install import main as install_main
        sys.exit(install_main(sys.argv[1:]))
    # 機械へ書く、画面なしの固定の入口（cli_write.py。進み具合を1行ずつ出す）
    if "--cli-write" in sys.argv[1:]:
        from .cli_write import main as cli_main
        rest = [a for a in sys.argv[1:] if a != "--cli-write"]
        sys.exit(cli_main(rest))
    # 入れたアプリ自身に、自分の画面を写させる（self_shots.py。練習の場所で動く。本番を書かない・機械につながない）
    if "--self-shots" in sys.argv[1:]:
        i = sys.argv.index("--self-shots")
        out = sys.argv[i + 1] if len(sys.argv) > i + 1 else "画面の写真"
        from .practice import prepare
        from .settings import app_dir as _real_dir
        prepare(_real_dir())
        from .self_shots import run as shots_run
        sys.exit(shots_run(out, load_settings()))
    practice = "--practice" in sys.argv[1:]
    if practice:                    # 練習モード: 本番の設定・マスタ・測定データ・機械を汚さない（practice.py）
        sys.argv = [a for a in sys.argv if a != "--practice"]
        from .practice import prepare
        from .settings import app_dir as _real_dir
        prepare(_real_dir())
    ap = argparse.ArgumentParser(description="ND287 分割測定アプリ")
    ap.add_argument(
        "--port",
        default=None,
        help="シリアルポート（auto=自動検出、COM3等で明示指定。省略時はsettings.json）",
    )
    ap.add_argument("--baud", type=int, default=None, help="ボーレート（省略時はsettings.json）")
    ap.add_argument(
        "--parity", default=None, choices=["N", "E", "O"], help="パリティ（省略時はsettings.json）"
    )
    ap.add_argument("--dummy", action="store_true", help="実機なしの疑似デバイスで起動")
    ap.add_argument("--wheel-pitch", type=float, default=10.0, help="ホイール: 何度ごとに取るか")
    ap.add_argument("--worm-pitch", type=float, default=0.5, help="ウォーム: 何度ごとに評価するか")
    ap.add_argument("--worm-range", type=float, default=5.0, help="ウォーム1回転ぶんの角度")
    ap.add_argument("--worm-start", type=float, default=0.0, help="ウォーム測定の開始角度")
    args = ap.parse_args()

    settings = load_settings()
    port = args.port or settings["port"]
    baud = args.baud or settings["baudrate"]
    parity = args.parity or settings["parity"]

    device = DummyDevice() if (args.dummy or practice) else ND287Device(port, baud, parity)

    from .gui import run

    run(device, args.wheel_pitch, args.worm_pitch, args.worm_range, args.worm_start, settings)


if __name__ == "__main__":
    main()
