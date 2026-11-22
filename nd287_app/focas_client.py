# -*- coding: utf-8 -*-
"""FOCAS ヘルパー（32bit Python の別プロセス）を起動して、命令を送るクライアント。

設計: docs/設計_FOCAS基本.md §4。Qt 非依存（単体試験できる）。

- 標準入出力に JSON を1行ずつ流す。命令は1つずつ（ロックで直列にする）。
- 返事が時間内に来なければ、ヘルパーを止める（DLL の中で固まっていると quit も届かない）。
  次の命令で起動し直す（機械との接続は切れているので、つなぎ直しは呼び出し側）。
- エラーは平易な日本語に直す。生の戻り値と関数名は必ず文に入れる（黙って丸めない）。
- ヘルパーの標準エラーは focas_helper.log に流す（診断用）。
"""

import base64
import collections
import glob
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

PROTO = 1

DEFAULT_TIMEOUTS = {
    "start": 10,            # 起動〜hello
    "connect": 15,
    "default": 5,           # 状態・アラーム・1件の読み書き
    "param_dump": 180,      # 控え（パラメータ全部）
    "prog_read": 60,
    "prog_list": 60,
    "param_info": 60,
    "param_read_many": 30,
    "quit": 3,
}

# 戻り値の意味（仕様の知識。実機で見たのは 0・6・10 だけ）
RC_MEANING = {
    3: "その番号は機械にありません",
    4: "軸や種別の指定が合いません（型の表が違うかもしれません）",
    5: "値が範囲外などで受け付けられません",
    6: "その機械にはこの機能（オプション）がありません",
    7: "書込が禁止されています（機械のセッティングの『パラメータ書込』が 0 かもしれません）",
    10: "機械が処理中です（少し待って同じ分をやり直す合図）",
    12: "機械のモードが合いません",
    13: "機械が受け付けません（運転中など）",
    15: "機械にアラームが出ています",
    -1: "機械が処理中です",
    -8: "機械との接続がありません",
    -16: "機械との通信が切れました",
}


class FocasError(Exception):
    """命令の失敗。message は画面に出せる日本語、detail は生の情報。"""

    def __init__(self, kind, message, detail=None, hint=""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = dict(detail or {})
        self.hint = hint

    def __str__(self):
        return self.message + (f"（{self.hint}）" if self.hint else "")


def describe(err):
    """ヘルパー（またはクライアント）のエラーの辞書 → (日本語の文, 次に見るところ)。
    関数名と戻り値があれば必ず文に入れる。"""
    kind = err.get("kind", "")
    func = err.get("func", "")
    rc = err.get("rc")
    name = err.get("name", "")
    where = f"{func}、" if func else ""
    rcs = f"戻り値 {rc}" + (f" {name}" if name and name != "?" else "") if rc is not None else ""
    if kind == "connect":
        return (f"機械につながりません（{where}{rcs}）。",
                "LAN ケーブル・IP アドレス・機械の電源を確認してください")
    if kind == "lost":
        return (f"機械との接続が切れました（{where}{rcs}）。", "もう一度つないでください")
    if kind == "focas":
        mean = RC_MEANING.get(rc, "")
        return (f"機械が {func} を受け付けませんでした（{rcs}）。" + (mean + "。" if mean else ""),
                "")
    if kind == "timeout":
        sec = err.get("seconds")
        s = f"{sec:g}秒" if isinstance(sec, (int, float)) else "決めた時間"
        return (f"機械から返事がありません。{s}待って打ち切りました。",
                "機械の状態と LAN を確認して、もう一度つないでください")
    if kind == "refused":
        return (err.get("message") or "この操作は断りました。", "")
    if kind == "unsupported":
        return (err.get("message") or "この機種はまだ対応していません。", "")
    if kind == "not_connected":
        return ("機械につながっていません。", "先に『つなぐ』を押してください")
    if kind == "bad_args":
        return ("内部エラー（命令の引数が正しくありません）。" + (err.get("message") or ""),
                "focas_helper.log を見てください")
    if kind == "internal":
        return ("内部エラー（ヘルパーで想定外の例外）。" + (err.get("message") or ""),
                "focas_helper.log を見てください")
    if kind == "protocol":
        return ("内部エラー（ヘルパーの返事が読めません）。" + (err.get("message") or ""),
                "focas_helper.log を見てください")
    if kind == "no_python":
        return ("FOCAS 用の Python（32bit）が見つかりません。", "設定で場所を選んでください")
    if kind == "no_helper":
        return ("FOCAS ヘルパー（focas_helper.py）が見つかりません。",
                "アプリのフォルダ（exe の隣）に置いてあるか確認してください")
    if kind == "not_32bit":
        return ("これは 64bit の Python です。FANUC のライブラリは 32bit でしか動きません。",
                "32bit 版の Python を選んでください")
    if kind == "no_dll":
        return ("FANUC のライブラリ（Fwlib32.dll）が見つかりません。",
                "SERVO GUIDE が入っているか確認してください")
    if kind == "helper_old":
        return ("FOCAS ヘルパーの版がアプリと合いません。", "ヘルパーを新しいものに置き換えてください")
    return (f"想定外のエラーです（{kind}）。", "")


def _from_dict(err):
    msg, hint = describe(err)
    return FocasError(err.get("kind", "?"), msg, err, hint)


# ---------------------------------------------------------------------------
# 設定からの場所の解決
# ---------------------------------------------------------------------------

def resolve_python32(setting=""):
    """32bit Python の場所。設定 → LOCALAPPDATA\\Python\\*-32 → C:\\Python*-32 の順。無ければ ''。"""
    if setting:
        return setting if os.path.isfile(setting) else ""
    pats = []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        pats.append(os.path.join(la, "Python", "pythoncore-*-32", "python.exe"))
        pats.append(os.path.join(la, "Programs", "Python", "Python*-32", "python.exe"))
    pats.append(r"C:\Python*-32\python.exe")
    for pat in pats:
        found = sorted(glob.glob(pat))
        if found:
            return found[-1]
    return ""


def resolve_helper(setting=""):
    """focas_helper.py の場所。設定 → exe の隣 → パッケージのフォルダ（開発時）。無ければ ''。"""
    if setting:
        return setting if os.path.isfile(setting) else ""
    from . import settings as S
    for p in (S.app_dir() / "focas_helper.py", Path(__file__).resolve().parent / "focas_helper.py"):
        if p.is_file():
            return str(p)
    return ""


# ---------------------------------------------------------------------------
# クライアント
# ---------------------------------------------------------------------------

class FocasClient:
    def __init__(self, python_path, helper_path, dll_dir="", timeouts=None, expect_bits=32,
                 extra_args=(), log_path=None, env=None):
        self.python_path = python_path
        self.helper_path = helper_path
        self.dll_dir = dll_dir
        self.timeouts = dict(DEFAULT_TIMEOUTS)
        self.timeouts.update(timeouts or {})
        self.expect_bits = expect_bits
        self.extra_args = list(extra_args)
        self.log_path = log_path
        self.env = env
        self.on_log = None            # 画面の「やり取りの記録」用: on_log(文字)
        self.hello = None
        self.connected = False
        self.stderr_tail = collections.deque(maxlen=200)
        self._proc = None
        self._q = None
        self._lock = threading.RLock()
        self._next_id = 0
        self._threads = []

    # --- 起動・終了 ----------------------------------------------------------

    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self):
        return self._proc.pid if self._proc is not None else None

    def start(self):
        with self._lock:
            if self.alive:
                return self.hello
            self._cleanup()
            if not self.python_path or not os.path.isfile(self.python_path):
                raise _from_dict({"kind": "no_python"})
            if not self.helper_path or not os.path.isfile(self.helper_path):
                raise _from_dict({"kind": "no_helper"})
            cmd = [self.python_path, "-I", "-u", self.helper_path]
            if self.dll_dir:
                cmd += ["--dll-dir", self.dll_dir]
            cmd += self.extra_args
            env = None
            if self.env:
                env = dict(os.environ)
                env.update(self.env)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)        # 黒い窓を出さない
            try:
                self._proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, creationflags=flags, env=env)
            except OSError as e:
                self._proc = None
                raise FocasError("no_python", describe({"kind": "no_python"})[0],
                                 {"kind": "no_python", "message": str(e)},
                                 describe({"kind": "no_python"})[1])
            self._q = queue.Queue()
            proc, q = self._proc, self._q
            self._threads = [
                threading.Thread(target=self._read_stdout, args=(proc, q), daemon=True),
                threading.Thread(target=self._read_stderr, args=(proc,), daemon=True),
            ]
            for t in self._threads:
                t.start()
            self.hello = None
            try:
                self.hello = self._read_hello()
            except FocasError:
                self._kill()
                raise
            return self.hello

    def _read_stdout(self, proc, q):
        try:
            for line in iter(proc.stdout.readline, b""):
                q.put(("line" if line.endswith(b"\n") else "partial", line))
        except (OSError, ValueError):
            pass
        q.put(("eof", b""))

    def _read_stderr(self, proc):
        try:
            for line in iter(proc.stderr.readline, b""):
                text = line.decode("utf-8", "replace").rstrip("\r\n")
                self.stderr_tail.append(text)
                if self.log_path:
                    try:
                        with open(self.log_path, "a", encoding="utf-8") as f:
                            f.write(text + "\n")
                    except OSError:
                        pass
        except (OSError, ValueError):
            pass

    def _read_hello(self):
        t = self.timeouts["start"]
        item = self._get(t)
        if item is None:
            raise FocasError("timeout", describe({"kind": "timeout", "seconds": t})[0],
                             {"kind": "timeout", "seconds": t, "cmd": "start"},
                             describe({"kind": "timeout"})[1])
        kind, data = item
        if kind != "line":
            raise _from_dict({"kind": "protocol",
                              "message": "ヘルパーが起動できませんでした。" + self._tail_text()})
        try:
            hello = json.loads(data.decode("utf-8"))
            if not isinstance(hello, dict) or hello.get("hello") != 1:
                raise ValueError("hello ではない")
        except ValueError:
            raise _from_dict({"kind": "protocol", "message": "起動時の返事が読めません。"
                              + self._tail_text()})
        if hello.get("proto") != PROTO:
            raise _from_dict({"kind": "helper_old", "proto": hello.get("proto")})
        if self.expect_bits and hello.get("bits") != self.expect_bits:
            raise _from_dict({"kind": "not_32bit", "bits": hello.get("bits")})
        if not hello.get("dll_ok"):
            err = _from_dict({"kind": "no_dll", "message": hello.get("dll_error", "")})
            raise err
        return hello

    def _tail_text(self):
        tail = [l for l in list(self.stderr_tail)[-3:] if l]
        return ("（ヘルパーの出力: " + " / ".join(tail) + "）") if tail else ""

    def _get(self, timeout):
        try:
            return self._q.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def _kill(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        self._cleanup()

    def _cleanup(self):
        proc, self._proc = self._proc, None
        self.connected = False
        if proc is not None:
            for p in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if p:
                        p.close()
                except Exception:
                    pass
        for t in self._threads:
            t.join(timeout=1)
        self._threads = []

    def close(self):
        """画面を閉じるとき。quit を送り、終わらなければ止める。何度呼んでもよい。"""
        got = self._lock.acquire(timeout=self.timeouts["quit"] + 3)
        try:
            proc = self._proc
            if proc is None:
                return
            if proc.poll() is None and got:
                try:
                    self._send({"id": self._new_id(), "cmd": "quit", "args": {}})
                    proc.wait(timeout=self.timeouts["quit"])
                except Exception:
                    pass
            self._kill()
        finally:
            if got:
                self._lock.release()

    def abort(self):
        """ロックを待たずに止める（命令の最中に画面を閉じるとき）。呼び出し中の命令は lost で終わる。"""
        self._kill()

    # --- 命令 ----------------------------------------------------------------

    def _note(self, text):
        cb = self.on_log
        if cb is not None:
            try:
                cb(text)
            except Exception:
                pass

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def _send(self, msg):
        line = (json.dumps(msg, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def call(self, cmd, args=None, timeout=None, on_event=None):
        """命令を1つ送って、返事の result を返す。失敗は FocasError。
        途中経過（event）は on_event(dict) に渡す。"""
        with self._lock:
            if not self.alive:
                self.start()                      # 切れていたら起動し直す
            mid = self._new_id()
            self._note(f"→ {cmd}")
            t = timeout if timeout is not None else self.timeouts.get(cmd, self.timeouts["default"])
            try:
                self._send({"id": mid, "cmd": cmd, "args": args or {}})
            except (OSError, ValueError):
                self._kill()
                raise _from_dict({"kind": "lost", "message": "ヘルパーに送れませんでした"})
            deadline = time.monotonic() + t
            while True:
                item = self._get(deadline - time.monotonic())
                if item is None:
                    self._kill()                   # DLL の中で固まっているかもしれない
                    raise _from_dict({"kind": "timeout", "seconds": t, "cmd": cmd})
                kind, data = item
                if kind == "eof":
                    self._kill()
                    raise _from_dict({"kind": "lost",
                                      "message": "ヘルパーが突然終わりました。" + self._tail_text()})
                if kind == "partial":
                    self._kill()
                    raise _from_dict({"kind": "protocol", "message": "返事が途中で切れました"})
                try:
                    msg = json.loads(data.decode("utf-8"))
                    if not isinstance(msg, dict):
                        raise ValueError("辞書ではない")
                except ValueError:
                    self._kill()                   # 同期が取れなくなるので止める
                    raise _from_dict({"kind": "protocol",
                                      "message": "JSON ではない返事を受け取りました"})
                if "event" in msg:
                    if msg.get("id") == mid and on_event is not None:
                        on_event(msg)
                    continue
                if msg.get("id") != mid:
                    continue                       # 前の命令の古い返事は捨てる
                if msg.get("ok"):
                    self._note(f"← {cmd} OK")
                    if cmd == "connect":
                        self.connected = True
                    elif cmd == "disconnect":
                        self.connected = False
                    return msg.get("result")
                err = msg.get("error") or {"kind": "protocol", "message": "error がありません"}
                self._note(f"× {cmd} {err.get('kind')} {err.get('func', '')} {err.get('rc', '')}".rstrip())
                if err.get("kind") in ("lost", "connect"):
                    self.connected = False
                raise _from_dict(err)

    @staticmethod
    def decode_data(result):
        """prog_read / param_dump の result から中身のバイトを取り出す（SHA-256 を確かめる）。"""
        import hashlib
        data = base64.b64decode(result["data"])
        if hashlib.sha256(data).hexdigest() != result.get("sha256") or len(data) != result.get("size"):
            raise _from_dict({"kind": "protocol", "message": "受け取った中身が壊れています（SHA-256 不一致）"})
        return data


def make_client(settings, log_path=None):
    """設定から FocasClient を作る（起動はしない）。"""
    return FocasClient(
        resolve_python32(str(settings.get("focas_python32") or "")),
        resolve_helper(str(settings.get("focas_helper_path") or "")),
        dll_dir=str(settings.get("focas_dll_dir") or ""),
        log_path=log_path,
        env=None,
    )
