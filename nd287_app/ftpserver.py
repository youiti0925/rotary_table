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

    # ---- 送受信の基本 ----
    def _send(self, line: str):
        try:
            self.conn.sendall(_encode(line) + b"\r\n")
        except OSError:
            pass
        self.server.log(f"→ {line}")

    def _abs(self, arg: str) -> Path:
        """FTP上のパスを実フォルダへ。公開フォルダの外へは絶対に出さない。"""
        p = (arg or "").strip().replace("\\", "/")
        base = self.cwd if not p.startswith("/") else "/"
        parts = []
        for seg in (base + "/" + p).split("/"):
            if seg in ("", "."):
                continue
            if seg == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(seg)
        return self.server.root.joinpath(*parts)

    def _ftp_path(self, real: Path) -> str:
        rel = real.relative_to(self.server.root).as_posix()
        return "/" + rel if rel != "." else "/"

    # ---- データ接続 ----
    def _open_data(self):
        if self.data_sock is not None:      # PASV
            self.data_sock.settimeout(30)
            try:
                sock, _a = self.data_sock.accept()
            finally:
                self.data_sock.close()
                self.data_sock = None
            return sock
        if self.data_addr:                  # PORT（アクティブ）
            sock = socket.create_connection(self.data_addr, timeout=30)
            self.data_addr = None
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
            try:
                self.conn.close()
            except OSError:
                pass
            self.server.log(f"切断 {self.addr[0]}")

    def _handle(self, line: str) -> bool:
        if not line:
            return True
        cmd, _s, arg = line.partition(" ")
        cmd, arg = cmd.upper(), arg.strip()
        self.server.log(f"← {cmd} {'***' if cmd == 'PASS' else arg}")
        if cmd == "USER":
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
        try:
            return self._command(cmd, arg)
        except FileNotFoundError:
            self._send("550 File not found")
        except PermissionError:
            self._send("550 Permission denied")
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
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((self.conn.getsockname()[0], 0))
            s.listen(1)
            self.data_sock = s
            host, port = s.getsockname()
            h = host.replace(".", ",")
            self._send(f"227 Entering Passive Mode ({h},{port >> 8},{port & 255})")
        elif cmd == "PORT":
            n = [int(x) for x in arg.split(",")]
            if len(n) != 6:
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
            self._abs(arg).unlink()
            self._send("250 Deleted")
        elif cmd == "MKD":
            real = self._abs(arg)
            real.mkdir(parents=True, exist_ok=True)
            self._send(f'257 "{self._ftp_path(real)}" created')
        elif cmd == "RMD":
            self._abs(arg).rmdir()
            self._send("250 Removed")
        elif cmd == "SIZE":
            self._send(f"213 {self._abs(arg).stat().st_size}")
        elif cmd == "MDTM":
            t = time.gmtime(self._abs(arg).stat().st_mtime)
            self._send("213 " + time.strftime("%Y%m%d%H%M%S", t))
        elif cmd == "REST":
            self.rest = int(arg or 0)
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
            self._send("226 Abort OK")
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
        self._send("150 Opening data connection")
        try:
            sock = self._open_data()
        except OSError as e:
            self._send(f"425 {e}")
            return
        try:
            with open(real, "rb") as f:
                if self.rest:
                    f.seek(self.rest)
                    self.rest = 0
                while True:
                    chunk = f.read(32768)
                    if not chunk:
                        break
                    sock.sendall(chunk)      # そのまま送る（改行を直さない）
        finally:
            sock.close()
        self.server.log(f"渡した {real.name}")
        self._send("226 Transfer complete")

    def _stor(self, arg: str, append: bool = False):
        real = self._abs(arg)
        real.parent.mkdir(parents=True, exist_ok=True)
        self._send("150 Opening data connection")
        try:
            sock = self._open_data()
        except OSError as e:
            self._send(f"425 {e}")
            return
        try:
            with open(real, "ab" if append else "wb") as f:
                while True:
                    chunk = sock.recv(32768)
                    if not chunk:
                        break
                    f.write(chunk)           # そのまま書く（改行を直さない）
        finally:
            sock.close()
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
        self.host, self.port = host, int(port)
        self.user, self.password = user or "", password or ""
        self.list_style = list_style if list_style in LIST_STYLES else "unix"
        self._log_func = log_func
        # 機械から受け取った直後に呼ばれる（受け取ったファイルの点検に使う）。
        # 壊れたバックアップ（F35 のような途中の %）をその場で見つけるため。
        self._on_stored = on_stored
        self.lines = []
        self.max_log = max_log
        self.stopping = False
        self._sock = None
        self._thread = None

    # ---- 状態 ----
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def address(self) -> str:
        ips = local_ip_addresses()
        return f"{ips[0] if ips else '?'}:{self.port}"

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
        s.settimeout(0.5)
        self._sock = s
        self.port = s.getsockname()[1]          # port=0 で自動割当のときのため
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
            _Session(self, conn, addr).start()

    def stop(self):
        self.stopping = True
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
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
