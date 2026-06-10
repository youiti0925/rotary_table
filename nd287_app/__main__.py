# -*- coding: utf-8 -*-
"""エントリポイント

実行例:
    python -m nd287_app --dummy            # 実機なしで画面確認
    python -m nd287_app --port COM3        # 実機接続（9600 8E1）
    python -m nd287_app --port COM3 --baud 19200 --parity N
"""

import argparse

from .nd287 import ND287Device, DummyDevice


def main():
    ap = argparse.ArgumentParser(description="ND287 分割測定アプリ")
    ap.add_argument("--port", default="COM3", help="シリアルポート（例 COM3, /dev/ttyUSB0）")
    ap.add_argument("--baud", type=int, default=None, help="ボーレート（既定 9600）")
    ap.add_argument("--parity", default=None, choices=["N", "E", "O"], help="パリティ（既定 E）")
    ap.add_argument("--dummy", action="store_true", help="実機なしの疑似デバイスで起動")
    ap.add_argument("--wheel-pitch", type=float, default=10.0, help="ホイール: 何度ごとに取るか")
    ap.add_argument("--worm-pitch", type=float, default=0.5, help="ウォーム: 何度ごとに評価するか")
    ap.add_argument("--worm-range", type=float, default=5.0, help="ウォーム1回転ぶんの角度")
    ap.add_argument("--worm-start", type=float, default=0.0, help="ウォーム測定の開始角度")
    args = ap.parse_args()

    device = DummyDevice() if args.dummy else ND287Device(args.port, args.baud, args.parity)

    from .gui import run

    run(device, args.wheel_pitch, args.worm_pitch, args.worm_range, args.worm_start)


if __name__ == "__main__":
    main()
