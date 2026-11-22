# -*- coding: utf-8 -*-
"""偽物の機械を差し込んでヘルパーを起動する入口（試験専用）。

本番のヘルパー（nd287_app/focas_helper.py）には偽物を読み込む引数が無い。
偽物を差し込むのはこのファイルだけ。実機にはつながない。

使い方（試験 B）:
    focas_client が  <python> -I -u tests/fake_focas_helper.py  で起動する。
    振る舞いは環境変数 FAKE_FOCAS_SCENARIO（JSON）で決める。FakeFwlib の scenario に加えて:
      bits           hello で名乗るビット数（既定 32）
      proto          hello の proto
      no_dll         hello で dll_ok=false を名乗って終わる
      no_hello       hello を出さずに黙る
      garbage_hello  hello の代わりに JSON でない行を出す
      die_at         N 番目の返事を出さずに突然終わる
      garbage_at     N 番目の返事の代わりに JSON でない行を出す
      blank_at       N 番目の返事の代わりに空行を出す
      partial_at     N 番目の返事を途中まで出して終わる
      stale_before   N 番目の返事の前に、別の id の古い返事を出す
      calls_log      DLL 関数の呼び出しを書き出すファイル
"""

import json
import os
import platform
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from nd287_app import focas_helper as H          # noqa: E402
from fake_fwlib import FakeFwlib                  # noqa: E402


class ScriptedOut:
    """返事の本数を数えて、決めた番号で行儀の悪いことをする。"""

    def __init__(self, raw, sc):
        self.raw, self.sc, self.n = raw, sc, 0

    def write(self, data):
        sc = self.sc
        if b'"ok"' in data:                        # 返事（途中経過 event は数えない）
            self.n += 1
            n = self.n
            if sc.get("stale_before") == n:
                self.raw.write(b'{"id":999,"ok":true,"result":{"stale":true}}\n')
            if sc.get("die_at") == n:
                self.raw.flush()
                os._exit(7)
            if sc.get("garbage_at") == n:
                self.raw.write(b"this is not json\n")
                return
            if sc.get("blank_at") == n:
                self.raw.write(b"\n")
                return
            if sc.get("partial_at") == n:
                self.raw.write(data[:12])
                self.raw.flush()
                os._exit(0)
        self.raw.write(data)

    def flush(self):
        self.raw.flush()


def main():
    sc = json.loads(os.environ.get("FAKE_FOCAS_SCENARIO") or "{}")
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    if sc.get("no_hello"):
        time.sleep(3600)
    if sc.get("garbage_hello"):
        out.write(b"garbage\n")
        out.flush()
        time.sleep(3600)
    hello = {"hello": 1, "proto": sc.get("proto", H.PROTO), "bits": sc.get("bits", 32),
             "python": platform.python_version(), "dll": "FAKE", "dll_ok": not sc.get("no_dll")}
    if sc.get("no_dll"):
        hello["dll_error"] = "Fwlib32.dll が見つかりません"
        H.emit_line(out, hello)
        return 3
    lib = FakeFwlib(scenario=sc, calls_log=sc.get("calls_log"))
    return H.serve(lib, sys.stdin.buffer, ScriptedOut(out, sc), hello=hello,
                   sleep=lambda s: None)


if __name__ == "__main__":
    sys.exit(main())
