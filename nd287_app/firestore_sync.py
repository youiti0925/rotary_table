# -*- coding: utf-8 -*-
"""product-inspection（Firebase/Firestore）への測定結果送信

product-inspection は Firestore の
    artifacts/{APP_DATA_ID}/public/data/{collection}/{docId}
にデータを置き、匿名認証ユーザーに読み書きを許可している
（firestore.rules で確認済み）。本モジュールは同じ場所の
rotaryMeasurements コレクションへ、セーブ時に測定結果を書き込む。

- 認証: Identity Toolkit REST の匿名サインアップ → idToken（期限内は再利用、
  refreshToken で更新）
- 書き込み: Firestore REST の PATCH（ドキュメントIDを指定して作成/上書き）
- 依存: 標準ライブラリのみ
"""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

IDENTITY_BASE = "https://identitytoolkit.googleapis.com/v1"
SECURE_TOKEN_BASE = "https://securetoken.googleapis.com/v1"
FIRESTORE_BASE = "https://firestore.googleapis.com/v1"


# ──────────────────────────────────────────────
#  Firestore の値エンコード
# ──────────────────────────────────────────────

def to_firestore_value(value):
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, bytes):
        return {"stringValue": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [to_firestore_value(v) for v in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": to_firestore_fields(value)}}
    return {"stringValue": str(value)}


def to_firestore_fields(data: dict) -> dict:
    return {str(k): to_firestore_value(v) for k, v in data.items()}


def from_firestore_value(value: dict):
    if "nullValue" in value:
        return None
    if "booleanValue" in value:
        return value["booleanValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "stringValue" in value:
        return value["stringValue"]
    if "timestampValue" in value:
        return value["timestampValue"]
    if "arrayValue" in value:
        return [from_firestore_value(v)
                for v in value["arrayValue"].get("values", [])]
    if "mapValue" in value:
        return from_firestore_fields(value["mapValue"].get("fields", {}))
    return None


def from_firestore_fields(fields: dict) -> dict:
    return {k: from_firestore_value(v) for k, v in (fields or {}).items()}


# ──────────────────────────────────────────────
#  匿名認証
# ──────────────────────────────────────────────

def anonymous_sign_in(api_key: str, timeout: float = 15.0) -> dict:
    """匿名ユーザーでサインインし {idToken, refreshToken, expiresIn} を返す"""
    url = f"{IDENTITY_BASE}/accounts:signUp?key={api_key}"
    body = json.dumps({"returnSecureToken": True}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def refresh_id_token(api_key: str, refresh_token: str,
                     timeout: float = 15.0) -> dict:
    url = f"{SECURE_TOKEN_BASE}/token?key={api_key}"
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": refresh_token,
    }).encode("utf-8")
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return {"idToken": payload.get("id_token"),
            "refreshToken": payload.get("refresh_token"),
            "expiresIn": payload.get("expires_in", "3600")}


# ──────────────────────────────────────────────
#  送信クライアント
# ──────────────────────────────────────────────

class FirestoreSync:
    def __init__(self, api_key: str, project_id: str, app_data_id: str,
                 collection: str = "rotaryMeasurements"):
        self.api_key = api_key
        self.project_id = project_id
        self.app_data_id = app_data_id
        self.collection = collection
        self._id_token = None
        self._refresh_token = None
        self._token_expiry = 0.0

    def configured(self) -> bool:
        return bool(self.api_key and self.project_id and self.app_data_id)

    def _ensure_token(self):
        now = time.time()
        if self._id_token and now < self._token_expiry - 60:
            return
        if self._refresh_token:
            try:
                payload = refresh_id_token(self.api_key, self._refresh_token)
                self._id_token = payload["idToken"]
                self._refresh_token = payload.get("refreshToken", self._refresh_token)
                self._token_expiry = now + float(payload.get("expiresIn") or 3600)
                return
            except Exception:
                pass  # リフレッシュ失敗時は匿名サインインし直す
        payload = anonymous_sign_in(self.api_key)
        self._id_token = payload["idToken"]
        self._refresh_token = payload.get("refreshToken")
        self._token_expiry = now + float(payload.get("expiresIn") or 3600)

    def _document_url(self, doc_id: str, collection: str = None) -> str:
        col = collection or self.collection
        path = (f"projects/{self.project_id}/databases/(default)/documents/"
                f"artifacts/{self.app_data_id}/public/data/"
                f"{urllib.parse.quote(col)}/{urllib.parse.quote(doc_id)}")
        return f"{FIRESTORE_BASE}/{path}"

    def push_document(self, doc_id: str, data: dict,
                      collection: str = None, timeout: float = 20.0):
        """ドキュメントを作成/上書きする。(成功, メッセージ)"""
        if not self.configured():
            return False, "Web連携が未設定（webapp_api_key等）"
        try:
            self._ensure_token()
            body = json.dumps({"fields": to_firestore_fields(data)}).encode("utf-8")
            request = urllib.request.Request(
                self._document_url(doc_id, collection), data=body, method="PATCH",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self._id_token}"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                json.loads(response.read().decode("utf-8"))
            return True, f"Webアプリへ送信OK: {self.collection}/{doc_id}"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            return False, f"送信失敗 HTTP {exc.code}: {detail}"
        except Exception as exc:
            return False, f"送信失敗: {exc}"

    def _collection_url(self, collection: str = None) -> str:
        col = collection or self.collection
        return (f"{FIRESTORE_BASE}/projects/{self.project_id}/databases/(default)/"
                f"documents/artifacts/{self.app_data_id}/public/data/"
                f"{urllib.parse.quote(col)}")

    def list_documents(self, collection: str = None, timeout: float = 20.0):
        """コレクション内の全ドキュメントを [(doc_id, fields_dict), …] で返す。"""
        if not self.configured():
            return []
        self._ensure_token()
        url = self._collection_url(collection) + "?pageSize=300"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._id_token}"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        out = []
        for doc in payload.get("documents", []):
            doc_id = doc["name"].rsplit("/", 1)[1]
            out.append((doc_id, from_firestore_fields(doc.get("fields", {}))))
        return out

    def update_fields(self, doc_id: str, fields: dict, collection: str = None,
                      timeout: float = 20.0):
        """指定フィールドだけ更新する（updateMaskで部分更新）。(成功, メッセージ)"""
        try:
            self._ensure_token()
            mask = "&".join(f"updateMask.fieldPaths={urllib.parse.quote(k)}"
                            for k in fields)
            url = self._document_url(doc_id, collection) + "?" + mask
            body = json.dumps({"fields": to_firestore_fields(fields)}).encode("utf-8")
            request = urllib.request.Request(
                url, data=body, method="PATCH",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self._id_token}"})
            with urllib.request.urlopen(request, timeout=timeout):
                pass
            return True, "更新OK"
        except Exception as exc:
            return False, f"更新失敗: {exc}"

    def delete_document(self, doc_id: str, collection: str = None,
                        timeout: float = 20.0):
        """テスト用: ドキュメントを削除する。(成功, メッセージ)"""
        try:
            self._ensure_token()
            request = urllib.request.Request(
                self._document_url(doc_id, collection), method="DELETE",
                headers={"Authorization": f"Bearer {self._id_token}"})
            with urllib.request.urlopen(request, timeout=timeout):
                pass
            return True, "削除OK"
        except Exception as exc:
            return False, f"削除失敗: {exc}"


def overall_judgement(results) -> str:
    """結果行（[項目, 値]の列）から総合判定を出す。NGが1つでもあればNG"""
    values = [str(v) for _, v in results]
    if any(v.startswith("NG") for v in values):
        return "NG"
    if any(v.startswith("OK") for v in values):
        return "OK"
    return "判定なし"


def build_measurement_doc(*, model, machine, operator, date, temperature, mode,
                          results, comment="", plot_png: bytes = None,
                          saved_files=None) -> dict:
    """セーブ時にFirestoreへ送るドキュメントを組み立てる（純関数）"""
    doc = dict(
        source="nd287_app",
        model=model, machine=machine, operator=operator,
        date=date, temperature=temperature, mode=mode,
        comment=comment,
        judgement=overall_judgement(results),
        results=[{"item": k, "value": str(v)} for k, v in results],
        savedAt=time.strftime("%Y-%m-%dT%H:%M:%S"),
        savedAtEpoch=int(time.time() * 1000),
    )
    if saved_files:
        doc["savedFiles"] = [str(p) for p in saved_files]
    if plot_png:
        doc["plotPng"] = base64.b64encode(plot_png).decode("ascii")
    return doc
