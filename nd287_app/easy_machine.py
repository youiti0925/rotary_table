# -*- coding: utf-8 -*-
"""はじめてモード: 機械の様子（非常停止・操作モード・運転中・アラーム・つながっているか）を1秒ごとに読むだけ（Qt なし）。

- 書かない・控えも取らない。LAN を探し回らない（台帳の IP と設定の focas_host に 8193 で声をかけるだけ）。
- つなぎ直し（電源の入れ直し）も、切れたら「見えない」、戻ったら「見える」として自動で追う。
- かんたん作成の窓が機械に書いている間は pause()（同じ機械に2本つながないため）。終わったら resume()。
- 読めなかった値は None のまま渡す（推測で埋めない）。
"""
import threading
import time

from . import easy_flow, focas_client, focas_find, wizard_probe


class MachinePoller:
    def __init__(self, settings, client_factory=None, *, port_open=None, interval=1.0):
        self.settings = settings
        self.factory = client_factory or (lambda st: focas_client.make_client(st))
        self.port_open = port_open or focas_find.port_open
        self.interval = float(interval)
        self._lock = threading.Lock()
        self._st = easy_flow.OFFLINE
        self._host = ""
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._client = None
        self.last_error = ""

    # --- 画面から使う
    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        c = self._client
        if c is not None:
            try:
                c.abort()
            except Exception:
                pass
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(3)

    def pause(self):
        """窓が機械に書いている間。つないでいたら切る（同じ機械へ2本つながない）。"""
        self._paused.set()
        deadline = time.time() + 5
        while self._client is not None and time.time() < deadline:
            time.sleep(0.05)
        self._set(easy_flow.OFFLINE)

    def resume(self):
        self._paused.clear()

    def state(self):
        with self._lock:
            return dict(self._st)

    def host(self):
        return self._host

    # --- 中身
    def _set(self, st):
        with self._lock:
            self._st = st

    def _drop(self):
        c, self._client = self._client, None
        if c is not None:
            try:
                c.call("disconnect")
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass

    def _connect(self):
        try:
            port = int(self.settings.get("focas_port", focas_find.DEFAULT_PORT))
        except (TypeError, ValueError):
            port = focas_find.DEFAULT_PORT
        reg = focas_find.registry_path(self.settings)
        host = next((ip for ip in wizard_probe.candidate_hosts(self.settings, reg)
                     if self.port_open(ip, port, timeout=0.3)), "")
        if not host:
            return False
        client = self.factory(self.settings)
        self._client = client
        client.call("connect", {"host": host, "port": port,
                                "timeout": focas_client.connect_timeout(self.settings),
                                "families": dict(self.settings.get("focas_families") or {})})
        self._host = host
        return True

    def poll_once(self):
        """1回読む。つながっていなければつなぐ。戻り値: いまの状態。"""
        if self._client is None and not self._connect():
            self._set(easy_flow.OFFLINE)
            return self.state()
        status = self._client.call("status")
        try:
            alarms = (self._client.call("alarms") or {}).get("alarms") or []
        except Exception:
            alarms = None                 # 読めなかった（「アラームなし」とは別。推測で空にしない）
        self._set(easy_flow.machine_state(status, alarms, online=True))
        return self.state()

    def _run(self):
        while not self._stop.is_set():
            if self._paused.is_set():
                self._drop()
                self._stop.wait(0.2)
                continue
            try:
                self.poll_once()
            except Exception as e:           # 切れた・読めない → 見えない扱いにして、次の回でつなぎ直す
                self.last_error = str(e)
                self._drop()
                self._set(easy_flow.OFFLINE)
            self._stop.wait(self.interval)
        self._drop()
