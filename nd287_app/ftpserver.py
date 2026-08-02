# -*- coding: utf-8 -*-
"""アプリの中で動く、かんたんFTPサーバー。

なぜ要るか:
  FANUCの組込みイーサネットの「FTP転送」は、<b>制御装置がPCへ取りに行く</b>作りに
  なっている（画面が「接続先」「ホスト名」「ユーザー名」「パスワード」を聞く＝客側）。
  つまりPC側にFTPサーバーが要る。別のソフトを入れなくて済むよう、アプリに入れる。

  これがあると、メモリカードを持ち歩かずに
    ・測定プログラム(.NC)を制御装置から直接読む
    ・パラメータ(.DAT)を制御装置から直接読む
    ・機械のバックアップをPCへ書き戻す（STOR）
  ができる。

方針:
  ・追加ライブラリなし（標準ライブラリだけ）。Webモニタと同じ作り。
  ・社内LAN・機械と1対1の配線での利用が前提。FTPは暗号化しない仕組みなので、
    外につながる回線では使わない。
  ・<b>中身は一切変換しない</b>。ASCIIモード(TYPE A)でも改行を書き換えない。
    測定プログラムやパラメータの区切りは LF CR CR で、ここを普通のFTPサーバーの
    ように LF→CRLF と直すと制御装置が読めないファイルになるため。
"""

import os
import socket
import threading
import time
from pathlib import Path

DEFAULT_PORT = 21
DEFAULT_USER = "cnc"
DEFAULT_PASSWORD = "cnc"
# 一覧の書き方。制御装置によって読める形が違うので切り替えられるようにする
LIST_STYLES = ("unix", "dos")


def local_ip_addresses() -> list:
    """このPCのIPアドレスを列挙する（制御装置の「ホスト名」に入れる値）。

    127.0.0.1 は除く。1つも取れなければ空リスト。
    """
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in out:
                out.append(ip)
    except OSError:
        pass
    if not out:                      # ホスト名で引けない環境の保険
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.168.1.1", 1))     # 実際には送らない
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                out.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    return out


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _encode(text: str) -> bytes:
    for enc in ("utf-8", "cp932"):
        try:
            return text.encode(enc)
        except UnicodeEncodeError:
            continue
    return text.encode("latin-1", "replace")


def _list_line(path: Path, style: str = "unix") -> str:
    """LIST の1行。style='unix' は ls -l 風、'dos' は MS-DOS 風。"""
    try:
        st = path.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, time.time()
    is_dir = path.is_dir()
    t = time.localtime(mtime)
    if style == "dos":
        kind = "<DIR>         " if is_dir else f"{size:>14d}"
        return (f"{t.tm_mon:02d}-{t.tm_mday:02d}-{t.tm_year % 100:02d}  "
                f"{t.tm_hour:02d}:{t.tm_min:02d}    {kind} {path.name}")
    mon = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")[t.tm_mon - 1]
    perm = "drwxrwxrwx" if is_dir else "-rw-rw-rw-"
    when = (f"{mon} {t.tm_mday:2d} {t.tm_hour:02d}:{t.tm_min:02d}"
            if abs(time.time() - mtime) < 180 * 86400
            else f"{mon} {t.tm_mday:2d}  {t.tm_year}")
    return f"{perm} 1 cnc cnc {size:>12d} {when} {path.name}"


class _Session(threading.Thread):
    """1本の接続を相手にするスレッド。"""

    def __init__(self, server, conn, addr):
        super().__init__(daemon=True)
        self.server, self.conn, self.addr = server, conn, addr
        self.cwd = "/"                      # 公開フォルダを / とした位置
        self.authed = not server.password and not server.user
        self.user_ok = False
        self.data_sock = None               # PASV の待ち受け
        self.data_addr = None               # PORT の接続先
        self.rest = 0
        self.rename_from = None
        self.aborting = False               # ABOR を受けた（転送ループが見る）
        self.busy = False                   # データ転送中か（停止時の確認に使う）

    # ---- 送受信の基本 ----
    def _send(self, line: str):
        try:
            self.conn.sendall(_encode(line) + b"\r\n")
        except OSError:
            pass
        self.server.log(f"→ {line}")

    def _abs(self, arg: str) -> Path:
        """FTP上のパスを実フォルダへ。公開フォルダの外へは絶対に出さない。

        文字列で ".." を潰すだけでは足りない。フォルダ内にシンボリックリンク
        （Windowsのジャンクション）があるとその先＝フォルダ外を読み書きできるし、
        Windows では "D:" のようなドライブ文字が joinpath で効いてしまう。
        最後に実体へ解決して、公開フォルダの下かを必ず確かめる。
        """
        p = (arg or "").strip().replace("\\", "/")
        if "\x00" in p:
            raise ValueError("使えない文字が入っています")
        base = self.cwd if not p.startswith("/") else "/"
        parts = []
        for seg in (base + "/" + p).split("/"):
            if seg in ("", "."):
                continue
            if seg == "..":
                if parts:
                    parts.pop()
                continue
            if ":" in seg:               # "D:" 等のドライブ指定は受け付けない
                raise PermissionError("公開フォルダの外は使えません")
            parts.append(seg)
        real = self.server.root.joinpath(*parts)
        try:
            resolved = real.resolve()
        except OSError:
            return real
        root = self.server.resolved_root   # 公開フォルダ自体がリンクでも通るよう解決済み
        if resolved != root and root not in resolved.parents:
            raise PermissionError("公開フォルダの外です")
        return real

    def _need_arg(self, arg: str) -> Path:
        """引数が要るコマンド用。空だと公開フォルダ自身を指してしまうので弾く。"""
        if not (arg or "").strip():
            raise ValueError("ファイル名がありません")
        return self._abs(arg)

    def _ftp_path(self, real: Path) -> str:
        try:
            rel = real.relative_to(self.server.root).as_posix()
        except ValueError:
            return "/"
        return "/" + rel if rel != "." else "/"

    # ---- データ接続 ----
    DATA_TIMEOUT = 60          # 黙り込んだ相手でスレッドとfdが残り続けないように

    def _clear_data(self):
        """待っている PASV/PORT の予約を捨てる（後から来た指定を有効にする）。"""
        if self.data_sock is not None:
            try:
                self.data_sock.close()
            except OSError:
                pass
            self.data_sock = None
        self.data_addr = None

    def _open_data(self):
        if self.data_sock is not None:      # PASV
            self.data_sock.settimeout(self.DATA_TIMEOUT)
            try:
                sock, _a = self.data_sock.accept()
            finally:
                self.data_sock.close()
                self.data_sock = None
            # accept が返すソケットは必ずブロッキングなので、ここで必ず入れ直す
            # （入れないと黙った相手でスレッド1本＋fdが永久に残る）
            sock.settimeout(self.DATA_TIMEOUT)
            return sock
        if self.data_addr:                  # PORT（アクティブ）
            sock = socket.create_connection(self.data_addr, timeout=self.DATA_TIMEOUT)
            self.data_addr = None
            sock.settimeout(self.DATA_TIMEOUT)
            return sock
        raise OSError("データ接続がありません")

    # ---- 本体 ----
    def run(self):
        self.server.log(f"接続 {self.addr[0]}")
        self._send("220 ND287 FTP ready")
        buf = b""
        try:
            self.conn.settimeout(300)
            while not self.server.stopping:
                data = self.conn.recv(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, _n, buf = buf.partition(b"\n")
                    if not self._handle(_decode(line).strip()):
                        return
        except (OSError, ValueError):
            pass
        finally:
            self._clear_data()
            try:
                self.conn.close()
            except OSError:
                pass
            self.server.forget(self)
            self.server.log(f"切断 {self.addr[0]}")

    def _handle(self, line: str) -> bool:
        if not line:
            return True
        cmd, _s, arg = line.partition(" ")
        cmd, arg = cmd.upper(), arg.strip()
        self.server.log(f"← {cmd} {'***' if cmd == 'PASS' else arg}")
        if cmd == "USER":
            self.authed = False              # ログイン済みでも送り直しでやり直す
            self.user_ok = (not self.server.user) or arg == self.server.user
            if not self.server.password:
                self.authed = self.user_ok
                self._send("230 Login successful" if self.authed
                           else "530 Login incorrect")
            else:
                self._send("331 Password required")
            return True
        if cmd == "PASS":
            self.authed = self.user_ok and arg == self.server.password
            self._send("230 Login successful" if self.authed
                       else "530 Login incorrect")
            return True
        if cmd == "QUIT":
            self._send("221 Goodbye")
            return False
        if not self.authed:
            self._send("530 Please login")
            return True
        if self.server.stopping:
            self._send("421 Service closing")     # 停止後に1コマンド処理してしまわない
            return False
        if cmd not in ("RETR", "STOR", "APPE", "REST"):
            self.rest = 0        # 直前の REST は次の転送だけに効く（持ち越さない）
        try:
            return self._command(cmd, arg)
        except FileNotFoundError:
            self._send("550 File not found")
        except PermissionError:
            self._send("550 Permission denied")
        except (ValueError, TypeError, OverflowError) as e:
            # 変な引数でも必ず何か返す。黙って切ると「繋がらない」の切り分けができない
            self._send(f"501 {e}")
        except OSError as e:
            self._send(f"550 {e}")
        return True

    def _command(self, cmd: str, arg: str) -> bool:
        if cmd in ("NOOP",):
            self._send("200 OK")
        elif cmd == "SYST":
            self._send("215 UNIX Type: L8")
        elif cmd == "FEAT":
            self._send("211-Features:")
            self._send(" SIZE")
            self._send(" MDTM")
            self._send("211 End")
        elif cmd == "TYPE":
            # 中身は変換しない（LF CR CR を壊さないため）。受け付けるだけ
            self._send("200 Type set")
        elif cmd in ("MODE", "STRU", "ALLO", "OPTS"):
            self._send("200 OK")
        elif cmd in ("PWD", "XPWD"):
            self._send(f'257 "{self.cwd}" is current directory')
        elif cmd in ("CWD", "XCWD"):
            real = self._abs(arg)
            if not real.is_dir():
                self._send("550 No such directory")
            else:
                self.cwd = self._ftp_path(real)
                self._send("250 Directory changed")
        elif cmd == "CDUP":
            return self._command("CWD", "..")
        elif cmd == "PASV":
            self._clear_data()          # 直前の PORT/PASV は捨てる（後勝ち）
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((self.conn.getsockname()[0], 0))
            s.listen(1)
            self.data_sock = s
            host, port = s.getsockname()
            h = host.replace(".", ",")
            self._send(f"227 Entering Passive Mode ({h},{port >> 8},{port & 255})")
        elif cmd == "PORT":
            self._clear_data()          # 直前の PASV は捨てる（後勝ち）
            try:
                n = [int(x) for x in arg.split(",")]
            except ValueError:
                n = []
            if len(n) != 6 or any(not 0 <= x <= 255 for x in n):
                self._send("501 Bad PORT")
            else:
                self.data_addr = (".".join(str(x) for x in n[:4]), n[4] * 256 + n[5])
                self._send("200 PORT OK")
        elif cmd in ("LIST", "NLST"):
            self._transfer_list(cmd, arg)
        elif cmd == "RETR":
            self._retr(arg)
        elif cmd in ("STOR", "APPE"):
            self._stor(arg, append=(cmd == "APPE"))
        elif cmd == "DELE":
            self._need_arg(arg).unlink()
            self._send("250 Deleted")
        elif cmd in ("MKD", "XMKD"):
            real = self._need_arg(arg)
            real.mkdir(parents=True, exist_ok=True)
            self._send(f'257 "{self._ftp_path(real)}" created')
        elif cmd in ("RMD", "XRMD"):
            self._need_arg(arg).rmdir()
            self._send("250 Removed")
        elif cmd == "SIZE":
            real = self._need_arg(arg)
            if not real.is_file():
                self._send("550 Not a plain file")
            else:
                self._send(f"213 {real.stat().st_size}")
        elif cmd == "MDTM":
            real = self._need_arg(arg)
            if not real.is_file():
                self._send("550 Not a plain file")
            else:
                t = time.gmtime(real.stat().st_mtime)
                self._send("213 " + time.strftime("%Y%m%d%H%M%S", t))
        elif cmd == "REST":
            try:
                pos = int(arg or 0)
            except ValueError:
                pos = -1
            if pos < 0:
                self._send("501 Bad restart position")
            else:
                self.rest = pos
                self._send("350 Restart position accepted")
        elif cmd == "RNFR":
            self.rename_from = self._abs(arg)
            self._send("350 Ready for RNTO")
        elif cmd == "RNTO":
            if not self.rename_from:
                self._send("503 RNFR first")
            else:
                self.rename_from.rename(self._abs(arg))
                self.rename_from = None
                self._send("250 Renamed")
        elif cmd == "ABOR":
            if self.busy:
                self.aborting = True         # 転送ループが見て止まる
                self._send("226 ABOR command successful")
            else:
                self._send("226 ABOR command successful")
        else:
            self._send("502 Command not implemented")
        return True

    def _transfer_list(self, cmd: str, arg: str):
        arg = " ".join(a for a in arg.split() if not a.startswith("-"))  # -l 等は無視
        real = self._abs(arg)
        if real.is_dir():
            items = sorted(real.iterdir(), key=lambda p: p.name.lower())
        elif real.exists():
            items = [real]
        else:
            self._send("550 No such file or directory")
            return
        if cmd == "NLST":
            body = "".join(p.name + "\r\n" for p in items)
        else:
            body = "".join(_list_line(p, self.server.list_style) + "\r\n"
                           for p in items)
        self._send("150 Opening data connection")
        try:
            sock = self._open_data()
        except OSError as e:
            self._send(f"425 {e}")
            return
        try:
            sock.sendall(_encode(body))
        finally:
            sock.close()
        self._send("226 Transfer complete")

    def _retr(self, arg: str):
        real = self._abs(arg)
        if not real.is_file():
            self._send("550 No such file")
            return
        start, self.rest = self.rest, 0      # 位置は次へ持ち越さない
        if start and start > real.stat().st_size:
            self._send("554 Restart position too large")
            return
        self._send("150 Opening data connection")
        try:
            sock = self._open_data()
        except OSError as e:
            self._send(f"425 {e}")
            return
        self.busy, self.aborting = True, False
        try:
            with open(real, "rb") as f:
                if start:
                    f.seek(start)
                while not (self.aborting or self.server.stopping):
                    chunk = f.read(32768)
                    if not chunk:
                        break
                    sock.sendall(chunk)      # そのまま送る（改行を直さない）
        finally:
            self.busy = False
            sock.close()
        if self.aborting or self.server.stopping:
            self.aborting = False
            self._send("426 Transfer aborted")
            return
        self.server.log(f"渡した {real.name}")
        self._send("226 Transfer complete")

    def _stor(self, arg: str, append: bool = False):
        real = self._need_arg(arg)
        real.parent.mkdir(parents=True, exist_ok=True)
        start, self.rest = self.rest, 0
        self._send("150 Opening data connection")
        try:
            sock = self._open_data()
        except OSError as e:
            self._send(f"425 {e}")
            return
        self.busy, self.aborting = True, False
        try:
            # REST の後は続きから書く。頭から上書きすると、機械が「続きを送った」
            # つもりのバックアップが、途中からだけの短いファイルに化ける
            mode = "ab" if append else ("r+b" if start and real.exists() else "wb")
            with open(real, mode) as f:
                if start and mode == "r+b":
                    f.seek(start)
                while not (self.aborting or self.server.stopping):
                    chunk = sock.recv(32768)
                    if not chunk:
                        break
                    f.write(chunk)           # そのまま書く（改行を直さない）
        finally:
            self.busy = False
            sock.close()
        if self.aborting or self.server.stopping:
            self.aborting = False
            self._send("426 Transfer aborted")
            return
        self.server.log(f"受け取った {real.name}")
        self.server.stored(real)          # 受け取った中身をその場で点検する
        self._send("226 Transfer complete")


class FtpServer:
    """公開フォルダを1つだけ見せる、小さなFTPサーバー。

    start() で待ち受け開始、stop() で終了。log_func に1行ずつ状況を渡す。
    """

    def __init__(self, root, *, host="0.0.0.0", port=DEFAULT_PORT,
                 user=DEFAULT_USER, password=DEFAULT_PASSWORD,
                 list_style="unix", log_func=None, max_log=200,
                 on_stored=None):
        self.root = Path(root).resolve()
        # 公開フォルダ自体がリンクのこともあるので、閉じ込め判定用に実体も持つ
        self.resolved_root = self.root
        self.host, self.port = host, int(port)
        self.user, self.password = user or "", password or ""
        self.list_style = list_style if list_style in LIST_STYLES else "unix"
        self._log_func = log_func
        # 機械から受け取った直後に呼ばれる（受け取ったファイルの点検に使う）。
        # 壊れたバックアップ（F35 のような途中の %）をその場で見つけるため。
        self._on_stored = on_stored
        self.lines = []
        self.max_log = max(1, int(max_log or 200))    # 0 だと1行も消えず伸び続ける
        self.stopping = False
        self._sock = None
        self._thread = None
        self._sessions = set()          # 生きている接続（stop() で確実に閉じる）
        self._lock = threading.Lock()
        self.max_sessions = 8           # 積み上がりの上限

    # ---- 状態 ----
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def address(self) -> str:
        ips = local_ip_addresses()
        return f"{ips[0] if ips else '?'}:{self.port}"

    def busy_count(self) -> int:
        """いま転送中の接続の数（停止してよいかの判断に使う）。"""
        with self._lock:
            return sum(1 for x in self._sessions if getattr(x, "busy", False))

    def forget(self, session):
        with self._lock:
            self._sessions.discard(session)

    def log(self, line: str):
        stamp = time.strftime("%H:%M:%S")
        self.lines.append(f"{stamp} {line}")
        del self.lines[:-self.max_log]
        if self._log_func:
            try:
                self._log_func(f"{stamp} {line}")
            except Exception:
                pass

    def stored(self, path):
        """機械から受け取ったファイルを、登録された点検にかける。

        点検で落ちてもファイル受信は成功扱いにする（受け取ること自体は済んでいる）。
        """
        if not self._on_stored:
            return
        try:
            self._on_stored(Path(path))
        except Exception as e:
            self.log(f"点検できません: {e}")

    # ---- 開始/終了 ----
    def start(self):
        if self.running:
            return
        if not self.root.is_dir():
            raise OSError(f"公開フォルダがありません: {self.root}")
        self.stopping = False
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(5)
        s.settimeout(0.1)      # stop() の待ち時間になるので短くする
        self._sock = s
        self.port = s.getsockname()[1]          # port=0 で自動割当のときのため
        self.resolved_root = self.root.resolve()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.log(f"待ち受け開始 {self.address()} 公開フォルダ {self.root}")

    def _serve(self):
        while not self.stopping:
            try:
                conn, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                over = len(self._sessions) >= self.max_sessions
            if over:
                # 上限を超えたら断る（放置された接続が積み上がらないように）
                try:
                    conn.sendall(b"421 Too many connections\r\n")
                    conn.close()
                except OSError:
                    pass
                self.log(f"接続を断りました（同時 {self.max_sessions} 本まで）")
                continue
            sess = _Session(self, conn, addr)
            with self._lock:
                self._sessions.add(sess)
            sess.start()

    def stop(self):
        """待ち受けを閉じ、<b>つながっている接続も切る</b>。

        待ち受けだけ閉じると、画面は「停止中」なのに転送は最後まで通ってしまう
        （20MBのRETRが完走し、STORはファイルが完成して 226 まで返っていた）。
        利用者は止まったと思っているので、これは直さないと危ない。
        """
        self.stopping = True
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        with self._lock:
            sessions = list(self._sessions)
        for sess in sessions:
            for sock in (getattr(sess, "data_sock", None), sess.conn):
                if sock is None:
                    continue
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        with self._lock:
            self._sessions.clear()
        self.log("停止")

    def cnc_settings(self, host_ip: str = "") -> list:
        """制御装置の「FTP転送→接続先1」に入れる値（画面に出す早見表）。"""
        ips = local_ip_addresses()
        ip = host_ip or (ips[0] if ips else "（PCのIP）")
        return [("ホスト名(IPアドレス)", ip),
                ("ポート番号", str(self.port)),
                ("ユーザー名", self.user or "（空欄）"),
                ("パスワード", self.password or "（空欄）"),
                ("ログインフォルダ", "（空欄のまま）")]
