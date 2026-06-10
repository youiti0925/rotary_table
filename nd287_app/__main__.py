# -*- coding: utf-8 -*-
"""エントリポイント

通信・保存先の設定は settings.json（設定画面から変更可）。
コマンドライン引数を指定した場合はそちらが優先される（settings.jsonは書き換えない）。

実行例:
    python -m nd287_app                    # 実機接続（設定: 既定はCOM自動検出、9600 8E1）
    python -m nd287_app --dummy            # 実機なしで画面確認
    python -m nd287_app --port COM3 --baud 19200 --parity N
"""

import argparse

from .nd287 import ND287Device, DummyDevice
from .settings import load_settings


def main():
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

    device = DummyDevice() if args.dummy else ND287Device(port, baud, parity)

    from .gui import run

    run(device, args.wheel_pitch, args.worm_pitch, args.worm_range, args.worm_start, settings)


if __name__ == "__main__":
    main()
