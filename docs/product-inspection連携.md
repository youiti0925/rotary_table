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
