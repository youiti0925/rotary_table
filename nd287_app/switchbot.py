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
