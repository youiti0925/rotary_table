# product-inspection（Webアプリ）連携

## 仕組み

分割測定アプリの**セーブ時**に、測定結果を product-inspection と同じ Firebase
（プロジェクト `inspection-time-c4fd3`）の Firestore へ自動送信する。

```
分割測定アプリ ──セーブ──▶ Firestore: artifacts/product-inspection-v1/public/data/rotaryMeasurements/{機番_日時}
                                          ▲
product-inspection（ブラウザ） ──リアルタイム購読──┘
```

- 認証は product-inspection と同じ**匿名認証**（firestore.rules で許可済みのパス
  なので、**Webアプリ側のルール変更は不要**）
- 送信内容: 型式・機番・測定者・日付・温度・モード・コメント・
  **全結果行**（精度・バックラッシ・判定など、画面の結果表と同じ）・
  **総合判定**（NGが1つでもあればNG）・**グラフ画像**（PNG、約20〜35KB）・
  保存先ファイルパス
- 実機のFirebaseに対して 書込→読出→削除 のE2E検証済み（2026-06-13）

## 分割測定アプリ側の設定（settings.json）

```json
"webapp_sync_enabled": true,
"webapp_api_key": "AIzaSyDiIS-TDH6MgXaLvG9T2VRioFDomQ_zQ9E",
"webapp_project_id": "inspection-time-c4fd3",
"webapp_data_id": "product-inspection-v1",
"webapp_collection": "rotaryMeasurements",
"webapp_send_png": true
```

`webapp_sync_enabled` を true にするだけで動く（他は product-inspection の値を
既定で同梱済み）。セーブするとステータスバーに「Webアプリへ送信OK: …」と出る。

## product-inspection 側: 閲覧パネルの追加

以下のコンポーネントを `src/RotaryMeasurements.jsx` として追加し、
App.jsx のメニュー/タブに `<RotaryMeasurementsPanel db={db} />` を1行足すだけ。
（dbはApp.jsx内で `getFirestore(app)` 済みのインスタンス）

```jsx
// src/RotaryMeasurements.jsx
// 分割測定アプリから送信された測定結果の閲覧パネル
import { useEffect, useState } from 'react';
import { collection, onSnapshot, orderBy, query, limit } from 'firebase/firestore';

const APP_DATA_ID = 'product-inspection-v1';

export default function RotaryMeasurementsPanel({ db }) {
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    if (!db) return;
    const q = query(
      collection(db, 'artifacts', APP_DATA_ID, 'public', 'data', 'rotaryMeasurements'),
      orderBy('savedAtEpoch', 'desc'), limit(100)
    );
    return onSnapshot(q, (snap) =>
      setItems(snap.docs.map((d) => ({ ...d.data(), id: d.id }))));
  }, [db]);

  const badge = (j) => (
    <span style={{
      padding: '2px 8px', borderRadius: 6, color: '#fff', fontSize: 12,
      background: j === 'NG' ? '#dc2626' : j === 'OK' ? '#10b981' : '#9ca3af',
    }}>{j}</span>
  );

  return (
    <div style={{ display: 'flex', gap: 12 }}>
      <div style={{ width: 340, maxHeight: '80vh', overflow: 'auto' }}>
        {items.map((m) => (
          <div key={m.id} onClick={() => setSelected(m)}
            style={{ padding: 8, border: '1px solid #ddd', borderRadius: 8,
                     marginBottom: 6, cursor: 'pointer',
                     background: selected?.id === m.id ? '#eff6ff' : '#fff' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <b>{m.model} / {m.machine}</b>{badge(m.judgement)}
            </div>
            <div style={{ fontSize: 12, color: '#666' }}>
              {m.mode} ／ {m.operator} ／ {m.temperature}°C ／ {m.savedAt}
            </div>
          </div>
        ))}
        {items.length === 0 && <p>測定データはまだありません</p>}
      </div>
      {selected && (
        <div style={{ flex: 1, maxHeight: '80vh', overflow: 'auto' }}>
          <h3>{selected.model} ／ 機番 {selected.machine} {badge(selected.judgement)}</h3>
          {selected.comment && <p>コメント: {selected.comment}</p>}
          {selected.plotPng && (
            <img src={`data:image/png;base64,${selected.plotPng}`}
                 alt="グラフ" style={{ maxWidth: '100%', border: '1px solid #ccc' }} />
          )}
          <table style={{ borderCollapse: 'collapse', marginTop: 8 }}>
            <tbody>
              {(selected.results || []).map((r, i) => (
                <tr key={i}>
                  <td style={{ border: '1px solid #ccc', padding: '2px 8px' }}>{r.item}</td>
                  <td style={{ border: '1px solid #ccc', padding: '2px 8px',
                               color: String(r.value).startsWith('NG') ? '#dc2626' : '#111' }}>
                    {r.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

## 補足

- ドキュメントIDは `機番_yyyyMMdd-HHmmss`。同じ機番の再測定は別ドキュメントとして
  履歴が積み上がる
- 送信はセーブ後にバックグラウンドで行うため、ネットが無い現場でもセーブ自体は
  失敗しない（送信失敗はステータスバーに表示）
- 将来「Webから再測定指示」までやる場合は、Webモニタ（README参照）を併用するか、
  Firestoreにコマンド用コレクションを足して分割測定アプリ側で購読する拡張が可能

---

# 時間取り連動（Web→アプリ→Web）

product-inspection の **時間取り**と分割測定アプリを Firestore 経由で連動させる。
準備タイマー開始でアプリに条件をセット、測定タイマー開始で自動測定（無人なら
SwitchBotでNCスタート）、測定完了で測定タイマーを自動終了する。

## Firestore のコレクション

すべて `artifacts/product-inspection-v1/public/data/` 配下。

- **rotaryCommands**（Web→アプリ）: ドキュメント例
  ```
  { type: "prepare"|"start_capture", station: "PC-3", workId: "<相関ID>",
    model: "RWE-200", machine: "260976K", mode: "回転分割+再現",
    status: "pending" }
  ```
  アプリは自分の `webapp_station` と一致し `status=="pending"` の指令だけ実行し、
  実行後 `status` を `"done"` に更新する。
- **rotaryEvents**（アプリ→Web）: `{ workId, station, type: "ready"|"capturing"|"done"|"error", judgement }`
  Web は `type=="done"` を購読し、その `workId` の測定タイマーを終了する。

## アプリ側の設定（各PC）

```json
"webapp_commands_enabled": true,
"webapp_station": "PC-3",          // このPC（ステーション）のID
"webapp_command_collection": "rotaryCommands",
"webapp_event_collection": "rotaryEvents"
```

## product-inspection 側に足すもの

1. テンプレートの「測定」工程に **測定連動フラグ** と **既定ステーション**（任意）
2. 準備タイマー開始時に **ステーション選択**（プルダウン。既定＝その端末の所在
   ステーション or 型式の常用機）。選んだ station と workId/model/machine を
   rotaryCommands に `type:"prepare"` で書く
3. 測定タイマー開始時に `type:"start_capture"` を書く（station は準備で確定済み）
4. rotaryEvents を購読し、`type:"done"` を受けたら該当 workId の測定タイマーを終了

```js
// 指令を書く（準備）
import { doc, setDoc, serverTimestamp } from 'firebase/firestore';
const P = (col, id) => doc(db, 'artifacts', 'product-inspection-v1', 'public', 'data', col, id);
async function startPrepare(workId, station, model, machine, mode) {
  await setDoc(P('rotaryCommands', `${workId}_prepare`), {
    type: 'prepare', station, workId, model, machine, mode, status: 'pending',
    createdAt: serverTimestamp(),
  });
}
async function startCapture(workId, station) {
  await setDoc(P('rotaryCommands', `${workId}_start`), {
    type: 'start_capture', station, workId, status: 'pending',
    createdAt: serverTimestamp(),
  });
}
// 完了イベントを購読して測定タイマーを止める
import { collection, onSnapshot, query, where } from 'firebase/firestore';
function watchDone(onDone) {
  const q = query(
    collection(db, 'artifacts', 'product-inspection-v1', 'public', 'data', 'rotaryEvents'),
    where('type', '==', 'done'));
  return onSnapshot(q, (snap) => snap.docChanges().forEach((c) => {
    if (c.type === 'added') {
      const e = c.doc.data();
      onDone(e.workId, e.judgement);   // ここで該当 workId の測定タイマーを終了
    }
  }));
}
```

## 安全（無人運転）

準備（有人・ワーク段取り）→測定（無人・自動NCスタート）の区切りが安全窓。
準備タイマーを終える＝人が機械から離れてOK、という運用ルールにする。
