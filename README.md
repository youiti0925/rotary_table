# ND287 分割測定アプリ

HEIDENHAIN ND287（デジタル表示器）からRS-232Cで角度を読み取り、回転テーブルの
分割精度（割出精度）を測定・表示するデスクトップアプリ。
LabVIEW製「IK220分割測定」系アプリのPython移植版。

## 測定内容

- **ホイール**: 一周360°を指定ピッチ（例 10°ごと＝36点）で割り出し、CW一周→CCW一周
- **ウォーム**: ウォーム1回転ぶんの角度範囲を指定ピッチで刻み、CW→CCW
- 各ポイントは「割り出して静止 → 取込ボタンで1点取得」のトリガ方式（時間ポーリングはしない）

### 画面に出る結果

| 項目 | 内容 |
|---|---|
| ホイールCW / CCW 精度PP | 偏差（測定値−指令値）の最大−最小 [秒] |
| ホイールCW / CCW 隣接 | 隣接ポイント間の偏差差の最大値 [秒] |
| ウォームCW / CCW 精度PP・隣接 | 同上（ウォーム1回転ぶん） |
| バックラッシ MIN / MAX | 同一指令角度での CCW偏差−CW偏差（ホイール・ウォーム各） |
| 真の最大・最小 | ホイール偏差とウォーム偏差が最悪方向に重なった合成値（仮実装、下記参照） |

## インストールと実行

```
pip install -r requirements.txt

python -m nd287_app --dummy                  # 実機なしで画面・流れを確認
python -m nd287_app --port COM3              # 実機接続（9600 8E1）
python -m nd287_app --port COM3 --baud 19200 --parity N
python -m nd287_app --port COM3 --wheel-pitch 10 --worm-pitch 0.5 --worm-range 5
```

ホイール刻み・ウォーム刻み・ウォーム範囲・ウォーム開始角度は画面上でも変更できる。
測定完了後「CSV保存」で生データと結果サマリをCSV（Shift-JIS、Excelでそのまま開ける）に保存。

## ND287 実機接続前のチェック

1. ND287本体の INSTALLATION SETUP でボーレート／パリティを確認し、`--baud` `--parity` をPC側で合わせる（既定 9600 / E）
2. 角度表示は DEG（十進度）/ 度分秒（DMS）どちらでも受信可（自動判別）
3. ケーブルは X31（RS-232C）に接続

通信仕様: 現在値要求 = CTRL B (0x02) 送信 → ASCII 1行（CR/LF終端）で表示値が返る。
ACK (0x06) は受信時に自動除去。

## 構成

```
nd287_app/
├── nd287.py      … 通信層・パース層（実機対応で直すのは parse_angle() とSERIAL_DEFAULTSだけ）
├── sequence.py   … 測定シーケンス（ホイールCW→CCW→ウォームCW→CCW、1点戻る対応）
├── analysis.py   … 計算層（偏差・精度PP・隣接・バックラッシ・真の最大最小）
├── export.py     … 結果のCSV保存
├── gui.py        … 測定画面（PySide6 + pyqtgraph）
└── __main__.py   … エントリポイント
tests/            … 計算・パース・シーケンスの単体テスト
```

## テスト

GUIとシリアルが無い環境でも計算層・パース層は検証できる:

```
python -m unittest discover -s tests -v
```

## 未確定事項（実機・現場での裏取りが必要）

- **真の最大最小の合成式**: 現在は「ホイール偏差の最大＋ウォーム偏差の最大」（最小も同様）の
  単純合成。現場の計算と違う場合は `analysis.py` の `true_min_max()` だけ差し替える。
- **シリアル設定値**: ND287本体のメニュー設定値に合わせて `--baud` `--parity` で調整。
- **応答フォーマットの最終確認**: 本体出力設定によって桁数・単位記号が変わるため、
  実機で1行受信して `parse_angle()` の結果を確認するのが確実。
- **合否判定マスタ（AAA合否判定.csv 等）との連動**: 元アプリのCSVマスタを
  リポジトリに入れてもらえれば判定機能を追加できる。

## exe化（検査PC配布用）

```
pip install pyinstaller
pyinstaller --onefile --windowed --name nd287_bunkatsu -p . nd287_app/__main__.py
```
