# -*- coding: utf-8 -*-
"""ND287 シリアル通信層

通信仕様（HEIDENHAIN ND287, X31 RS-232C 経由）:
    - 現在値要求 = CTRL B (0x02) を送信
    - ND287 はコマンド正常受信時に ACK (0x06) を返すことがある
    - 測定値は ASCII 1行（CR/LF 終端）で返る
    - 角度表示は本体設定により DEG（十進度）または度分秒（DMS）
ボーレート等は本体の INSTALLATION SETUP に合わせること（標準: 9600 8E1）。
"""

import re
import time

REQUEST_CMD = b"\x02"  # CTRL B = 現在値要求
ACK = b"\x06"
TERM = b"\r\n"

SERIAL_DEFAULTS = dict(baudrate=9600, bytesize=8, parity="E", stopbits=1, timeout=1.0)


def parse_angle(raw: bytes):
    """ND287 の応答バイト列を角度[度]に変換する。

    DEG（十進度）と度分秒（DMS）の両対応。単位記号やゴミ文字が混じっても
    符号付き数値だけを頑健に拾う。解釈できなければ None。
    """
    if not raw:
        return None
    # latin-1 は全バイトを1:1で文字化する。ascii+ignore だと度記号(0xB0等)が
    # 消えて「123°45」→「12345」のように桁が合体する事故が起きるため不可。
    text = raw.replace(ACK, b"").decode("latin-1").strip()
    if not text:
        return None
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    if not nums:
        return None
    # 度分秒: ° ' " の記号があるか、数値ブロックが3つ以上並ぶ場合
    if re.search(r"[°'\"]", text) or len(nums) >= 3:
        sign = -1.0 if nums[0].lstrip().startswith("-") else 1.0
        v = [float(x) for x in nums[:3]]
        d = abs(v[0])
        m = v[1] if len(v) > 1 else 0.0
        s = v[2] if len(v) > 2 else 0.0
        return sign * (d + m / 60.0 + s / 3600.0)
    return float(nums[0])


def deg_to_dms(deg: float) -> str:
    """十進度 → 度分秒表記（例 +123°45'06.7"）"""
    sign = "-" if deg < 0 else "+"
    a = abs(deg)
    d = int(a)
    mf = (a - d) * 60.0
    m = int(mf)
    s = (mf - m) * 60.0
    if s >= 59.95:  # 丸めで 60.0" にならないよう繰り上げ
        s = 0.0
        m += 1
        if m >= 60:
            m = 0
            d += 1
    return f"{sign}{d}°{m:02d}'{s:04.1f}\""


def extract_angles(buf: bytes):
    """受信バッファから完結した行を取り出して角度に変換する。

    (角度のリスト, 未完の残りバッファ) を返す。
    """
    angles = []
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        angle = parse_angle(line.rstrip(b"\r"))
        if angle is not None:
            angles.append(angle)
    return angles, buf


class ND287Device:
    """pyserial ラッパ。各割出ポイントで静止後に read_angle() で1点取得する。

    port を None または "auto" にすると、open() 時に全シリアルポートへ
    CTRL B を送って応答するポートを探す（自動検出）。
    """

    def __init__(self, port: str = None, baudrate: int = None, parity: str = None):
        self.port = port
        self._auto = port in (None, "", "auto")
        self.baudrate = baudrate or SERIAL_DEFAULTS["baudrate"]
        self.parity = parity or SERIAL_DEFAULTS["parity"]
        self.ser = None
        self._rxbuf = b""

    @property
    def dummy(self):
        return False

    def open(self):
        import serial

        if self._auto:
            found = find_nd287_port(self.baudrate, self.parity)
            if found is None:
                raise RuntimeError(
                    "ND287が見つかりません（ケーブル・電源・本体のボーレート設定を確認）"
                )
            self.port = found

        parity_map = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
        }
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=parity_map[self.parity.upper()],
            stopbits=serial.STOPBITS_ONE,
            timeout=SERIAL_DEFAULTS["timeout"],
        )

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None

    def prepare_point(self, target_deg: float, direction: int):
        """実機では何もしない（ダミーデバイスとのインターフェース合わせ）"""

    def flush_input(self):
        """受信バッファを空にする（取込開始時に古いデータを捨てる）"""
        self._rxbuf = b""
        if self.ser:
            self.ser.reset_input_buffer()

    def poll_received(self):
        """ND287側から送られてきた値を非ブロッキングで回収する。

        本体のPRINTキーやX41トリガで送信された行を、届いた順の角度リストで返す。
        """
        if not self.ser or not self.ser.in_waiting:
            return []
        self._rxbuf += self.ser.read(self.ser.in_waiting)
        angles, self._rxbuf = extract_angles(self._rxbuf)
        return angles

    def read_angle(self):
        """CTRL B を送り、表示値1点を読む。"""
        self.ser.reset_input_buffer()
        self.ser.write(REQUEST_CMD)
        return parse_angle(self.ser.read_until(TERM))


def list_serial_ports():
    """PC上のシリアルポート名一覧（COM番号順）"""
    from serial.tools import list_ports

    return [p.device for p in sorted(list_ports.comports(), key=lambda p: p.device)]


def probe_port(port: str, baudrate: int = None, parity: str = None) -> bool:
    """ポートに CTRL B を送り、角度として解釈できる応答が返れば True。"""
    dev = ND287Device(port, baudrate, parity)
    try:
        dev.open()
        try:
            return dev.read_angle() is not None
        finally:
            dev.close()
    except Exception:
        return False


def find_nd287_port(baudrate: int = None, parity: str = None, ports=None):
    """全ポートを順に試し、ND287 が応答したポート名を返す。無ければ None。

    注意: 探索中、各ポートに CTRL B (0x02) を1バイト送信する。ND287以外の
    シリアル機器が同じPCに接続されている場合は --port で明示指定すること。
    """
    if ports is None:
        ports = list_serial_ports()
    for port in ports:
        if probe_port(port, baudrate, parity):
            return port
    return None


class DummyDevice(ND287Device):
    """実機なしで画面と一連の流れを確認するための疑似デバイス。

    指令角度に「ランダム誤差＋周期成分＋CCW時のバックラッシ」を乗せて返す。
    """

    def __init__(self):
        super().__init__(port="(dummy)")
        import numpy as np

        self._np = np
        self._rng = np.random.default_rng(1)
        self._target = 0.0
        self._direction = +1

    @property
    def dummy(self):
        return True

    def open(self):
        pass

    def close(self):
        pass

    def prepare_point(self, target_deg: float, direction: int):
        self._target = target_deg
        self._direction = direction

    def read_angle(self):
        np = self._np
        time.sleep(0.02)
        err = self._rng.normal(0.0, 2.0) / 3600.0
        wob = 3.0 / 3600.0 * np.sin(np.radians(self._target * 8.0))
        backlash = (1.5 / 3600.0) if self._direction < 0 else 0.0
        return self._target + err + wob + backlash
