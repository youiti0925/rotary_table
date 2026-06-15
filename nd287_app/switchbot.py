# -*- coding: utf-8 -*-
"""SwitchBot API v1.1 から温湿度計の温度を取得する

settings.json に switchbot_token / switchbot_secret / switchbot_device を
設定すると、画面の「取得」ボタンで測定温度を自動入力できる。
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.request
import uuid

API_BASE = "https://api.switch-bot.com/v1.1"


def make_headers(token: str, secret: str, t: str = None, nonce: str = None) -> dict:
    """SwitchBot API v1.1 の署名付きヘッダを作る"""
    t = t or str(int(time.time() * 1000))
    nonce = nonce or str(uuid.uuid4())
    sign = base64.b64encode(
        hmac.new(secret.encode("utf-8"), (token + t + nonce).encode("utf-8"),
                 hashlib.sha256).digest()
    ).decode("utf-8")
    return {
        "Authorization": token,
        "sign": sign,
        "t": t,
        "nonce": nonce,
        "Content-Type": "application/json; charset=utf8",
    }


def fetch_temperature(token: str, secret: str, device_id: str,
                      timeout: float = 10.0) -> float:
    """温湿度計デバイスの現在温度[°C]を返す。失敗時は例外。"""
    if not (token and secret and device_id):
        raise ValueError("SwitchBotのtoken/secret/deviceが未設定です"
                         "（settings.jsonのswitchbot_token等）")
    url = f"{API_BASE}/devices/{device_id}/status"
    request = urllib.request.Request(url, headers=make_headers(token, secret))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("statusCode") != 100:
        raise RuntimeError(f"SwitchBot APIエラー: {payload.get('message')}")
    body = payload.get("body") or {}
    temperature = body.get("temperature")
    if temperature is None:
        raise RuntimeError("このデバイスは温度を返しません（温湿度計のIDを指定）")
    return float(temperature)


def list_devices(token: str, secret: str, timeout: float = 10.0) -> list:
    """デバイス一覧（deviceId/deviceName/deviceType）を返す。ID確認用。"""
    request = urllib.request.Request(f"{API_BASE}/devices",
                                     headers=make_headers(token, secret))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    body = payload.get("body") or {}
    return body.get("deviceList") or []


# ──────────────────────────────────────────────
#  SwitchBot Bot（物理ボタン押しロボット）の起動
#  XR20監視ツール（app526/xr20_tool）から移植
# ──────────────────────────────────────────────

# SwitchBot Bot の BLE GATT 定義（ハブ無し直結用）
BLE_WRITE_CHAR = "cba20002-224d-11e6-9fb9-0002a5d5c51b"
# SwitchBot のアドバタイズ サービスUUID（スキャンでの自動検出に使う）
SWITCHBOT_SERVICE_PREFIX = "0000fd3d"

DEFAULT_PATTERNS = {
    "1回押し": [0.0],
    "2回押し（5秒間隔）": [5.0, 0.0],
    "2回押し（3秒間隔）": [3.0, 0.0],
    "3回押し（3秒間隔）": [3.0, 3.0, 0.0],
}


def press_bot_cloud(token: str, secret: str, device_id: str,
                    timeout: float = 10.0) -> tuple:
    """SwitchBot API v1.1 で press コマンドを送る。(成功, メッセージ)"""
    if not (token and secret and device_id):
        return False, "Token / Secret / DeviceID が未設定"
    try:
        url = f"{API_BASE}/devices/{device_id}/commands"
        body = json.dumps({"command": "press", "parameter": "default",
                           "commandType": "command"}).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, method="POST", headers=make_headers(token, secret))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        if payload.get("statusCode") == 100:
            return True, payload.get("message", "OK")
        return False, f"statusCode={payload.get('statusCode')} {payload.get('message')}"
    except Exception as exc:
        return False, f"例外: {exc}"


def ble_press_payload(password: str = "") -> bytes:
    """Bot へのBLE pressコマンド（パスワード設定時はCRC32を埋め込む）"""
    password = (password or "").strip()
    if not password:
        return bytes([0x57, 0x01, 0x00])
    import binascii

    crc = binascii.crc32(password.encode("utf-8")) & 0xFFFFFFFF
    return bytes([0x57, 0x11]) + crc.to_bytes(4, "big") + bytes([0x00])


def _find_write_char(client):
    """既知UUID優先で書込先特性を探す。無ければSwitchBotベンダの書込可能特性"""
    for service in client.services:
        for char in service.characteristics:
            if char.uuid.lower() == BLE_WRITE_CHAR:
                return char
    for service in client.services:
        for char in service.characteristics:
            writable = any("write" in p.lower() for p in char.properties)
            if writable and char.uuid.lower().startswith("cba2"):
                return char
    return None


def _run_async(coro):
    """専用スレッドでasyncioコルーチンを実行（GUIスレッドのSTA問題回避）"""
    import asyncio
    import threading

    box = {}

    def worker():
        loop = asyncio.new_event_loop()
        try:
            box["value"] = loop.run_until_complete(coro)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def press_bot_ble(mac: str, password: str = "") -> tuple:
    """PCのBluetoothからBotへ直接 press を送る（ハブ不要）。(成功, メッセージ)"""
    mac = (mac or "").strip()
    if not mac:
        return False, "BLE MACアドレスが未設定"
    try:
        from bleak import BleakClient
    except Exception as exc:
        return False, f"bleak 未導入: {exc}（pip install bleak）"
    payload = ble_press_payload(password)

    async def _run():
        async with BleakClient(mac, timeout=15.0) as client:
            char = _find_write_char(client)
            if char is None:
                raise RuntimeError("書込可能なSwitchBot特性が見つかりません"
                                   "（選んだ機器がBotでない可能性）")
            await client.write_gatt_char(char, payload, response=True)
            return char.uuid

    try:
        used = _run_async(_run())
        return True, f"BLE press 送信OK ({mac} / {used})"
    except Exception as exc:
        return False, f"BLE例外: {exc}"


def scan_switchbot_ble(timeout: float = 6.0) -> tuple:
    """近くのBLE機器をスキャンし、SwitchBotを優先して返す。(成功, 一覧, メッセージ)

    旧アプリ xr20_tool と同じ検出方法（アドバタイズの SwitchBot サービスUUID
    0000fd3d… で判定）。一覧は SwitchBot 優先・電波(RSSI)が強い順。各要素は
    dict(mac, name, rssi, switchbot, model)。これで手入力なしにBotを見つけられる。
    """
    try:
        from bleak import BleakScanner
    except Exception as exc:
        return False, [], f"bleak 未導入: {exc}（pip install bleak）"

    async def _run():
        out = []
        results = await BleakScanner.discover(timeout=timeout, return_adv=True)
        for dev, adv in results.values():
            service_data = adv.service_data or {}
            is_sb = any(str(k).lower().startswith(SWITCHBOT_SERVICE_PREFIX)
                        for k in service_data)
            model = ""
            for key, value in service_data.items():
                if str(key).lower().startswith(SWITCHBOT_SERVICE_PREFIX) and value:
                    model = chr(value[0] & 0x7F)
            out.append(dict(
                mac=dev.address,
                name=adv.local_name or (dev.name or ""),
                rssi=adv.rssi if adv.rssi is not None else -999,
                switchbot=is_sb,
                model=model,
            ))
        return out

    try:
        devices = _run_async(_run())
    except Exception as exc:
        return False, [], f"スキャン失敗: {exc}"
    devices.sort(key=lambda d: (not d["switchbot"], -d["rssi"]))
    return True, devices, f"{len(devices)}台検出（SwitchBot優先・電波強い順）"


def press_bot(settings: dict) -> tuple:
    """設定に応じて BLE直結 または クラウドAPI で press。(成功, メッセージ)"""
    if settings.get("switchbot_use_ble"):
        return press_bot_ble(settings.get("switchbot_ble_mac", ""),
                             settings.get("switchbot_ble_password", ""))
    return press_bot_cloud(settings.get("switchbot_token", ""),
                           settings.get("switchbot_secret", ""),
                           settings.get("switchbot_device", ""))


def bot_configured(settings: dict) -> bool:
    if settings.get("switchbot_use_ble"):
        return bool(str(settings.get("switchbot_ble_mac") or "").strip())
    return all(str(settings.get(k) or "").strip()
               for k in ("switchbot_token", "switchbot_secret", "switchbot_device"))
