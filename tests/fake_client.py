# -*- coding: utf-8 -*-
"""プロセスを起こさずに、偽物の機械へ直接つなぐクライアント（試験専用）。

focas_client.FocasClient と同じ call() の形で、返事は JSON を通して（本番と同じ形に）して返す。
focas_backup・focas_ops の試験が速く回るようにするためのもの。実機にはつながない。
"""

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nd287_app import focas_client as C       # noqa: E402
from nd287_app import focas_helper as H       # noqa: E402
from fake_fwlib import FakeFwlib              # noqa: E402


class DirectClient:
    decode_data = staticmethod(C.FocasClient.decode_data)

    def __init__(self, scenario=None, lib=None):
        self.lib = lib or FakeFwlib(scenario=scenario)
        self.hp = H.Helper(self.lib, sleep=lambda s: None)
        self.stderr_tail = collections.deque(maxlen=10)
        self.alive = True
        self.connected = False
        self.calls = []
        self._n = 0

    @property
    def machine(self):
        return self.lib.m

    def connect(self):
        self.call("connect", {"host": "127.0.0.1", "port": 8193, "timeout": 1})
        return self

    def call(self, cmd, args=None, timeout=None, on_event=None):
        self._n += 1
        self.calls.append(cmd)
        reply = self.hp.handle({"id": self._n, "cmd": cmd, "args": args or {}},
                               lambda ev: on_event(json.loads(json.dumps(ev))) if on_event else None)
        reply = json.loads(json.dumps(reply))
        if reply["ok"]:
            if cmd == "connect":
                self.connected = True
            return reply["result"]
        raise C._from_dict(reply["error"])

    def close(self):
        self.hp.close()
