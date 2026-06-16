# -*- coding: utf-8 -*-
"""生成した測定プログラム(.NC)を機械へ送る（カード不要・LAN前提）。

特別なFANUCライブラリ無しで、Python標準ライブラリだけで動く2方式:

- folder: ネットワーク共有フォルダ（機械のData Server共有や割当ドライブ）へ
          ファイルコピー。設定が一番簡単で対応機種も多い。
- ftp   : 機械のEthernet/Data Serverが持つFTPサーバへ ftplib でアップロード。

どちらも「送るだけ」。送った後、機械側で番号を選んで運転する（自動起動はしない）。
FANUCはASCII・CRLF前提なので、その形に整えて書き出す。
"""

import io
from ftplib import FTP, error_perm
from pathlib import Path


def nc_bytes(text: str) -> bytes:
    """FANUC向けに整形（改行をCRLF統一・ASCII化）したバイト列を返す。"""
    norm = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    # 日本語コメント等が混じっても落ちないよう非ASCIIは "?" に置換
    return norm.encode("ascii", "replace")


def default_filename(machine: str = "", main_number=None) -> str:
    """送信ファイル名。機番があれば <機番>.NC、無ければ O番号 or program.NC。"""
    machine = (machine or "").strip()
    if machine:
        return f"{machine}.NC"
    if main_number is not None:
        return f"O{int(main_number):04d}.NC"
    return "program.NC"


def send_to_folder(text: str, dest_dir, filename: str) -> str:
    """共有フォルダへ .NC を書き出す。書き出したフルパスを返す。

    フォルダが見えない/存在しない場合は分かりやすい例外を送出する。
    """
    dest = (str(dest_dir) if dest_dir is not None else "").strip()
    if not dest:
        raise ValueError("送信先フォルダが設定されていません")
    d = Path(dest)
    if not d.exists():
        raise FileNotFoundError(
            f"送信先フォルダが見つかりません（共有が見えていない可能性）: {d}")
    if not d.is_dir():
        raise NotADirectoryError(f"送信先がフォルダではありません: {d}")
    path = d / filename
    path.write_bytes(nc_bytes(text))
    return str(path)


def send_via_ftp(text: str, host: str, *, port: int = 21, user: str = "",
                 password: str = "", remote_dir: str = "",
                 filename: str = "program.NC", passive: bool = True,
                 timeout: float = 10.0) -> str:
    """FTPで .NC をアップロードする。送信先表記（ftp://…）を返す。"""
    host = (host or "").strip()
    if not host:
        raise ValueError("FTPのホスト（機械のIPアドレス）が設定されていません")
    data = nc_bytes(text)
    ftp = FTP()
    ftp.connect(host, int(port or 21), timeout=timeout)
    try:
        ftp.login(user or "anonymous", password or "")
        ftp.set_pasv(bool(passive))
        if remote_dir:
            _ftp_chdir(ftp, remote_dir)
        ftp.storbinary(f"STOR {filename}", io.BytesIO(data))
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
    base = remote_dir.strip("/\\")
    return f"ftp://{host}/{base + '/' if base else ''}{filename}"


def _ftp_chdir(ftp: FTP, remote_dir: str):
    """remote_dir まで cwd（無ければ作成を試みる）。"""
    for part in remote_dir.replace("\\", "/").split("/"):
        if not part:
            continue
        try:
            ftp.cwd(part)
        except error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def send(text: str, settings: dict, *, machine: str = "",
         main_number=None) -> str:
    """settings の方式（nc_send_method）に従って送信。送信先の表記を返す。"""
    settings = settings or {}
    method = (settings.get("nc_send_method") or "folder").lower()
    filename = default_filename(machine, main_number)
    if method == "ftp":
        return send_via_ftp(
            text, settings.get("nc_ftp_host", ""),
            port=int(settings.get("nc_ftp_port", 21) or 21),
            user=settings.get("nc_ftp_user", ""),
            password=settings.get("nc_ftp_password", ""),
            remote_dir=settings.get("nc_ftp_dir", ""),
            filename=filename,
            passive=bool(settings.get("nc_ftp_passive", True)),
        )
    return send_to_folder(text, settings.get("nc_send_folder", ""), filename)
