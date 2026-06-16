# -*- coding: utf-8 -*-
"""FANUC アラーム（エラー番号・メッセージ）の意味と対処の目安。

番号やキーワードから引けるよう、代表的なアラームを内蔵する。さらに
ユーザー/機械メーカー固有のアラームを CSV で追加できる（PMCアラームや
マクロ#3000系、主軸アンプ番号などは機種依存のため）。

⚠ 注意: ここに載るのは「代表的なアラームの目安」。正確・最新の内容と
処置は必ず FANUC 純正の保守説明書／機械メーカーの資料に従うこと。
番号体系は制御機種（0i / 30i 等）で表記が異なることがある。
"""

import csv
from pathlib import Path

DISCLAIMER = (
    "⚠ これは代表的なアラームの目安です。正確・最新の内容と処置は、必ず"
    "FANUC純正の保守説明書／機械メーカーの資料に従ってください。番号の表記は"
    "制御機種（0i/30i等）で異なる場合があります。"
)

# 各要素: code, group, title, cause, remedy, keywords
# keywords には旧番号・英語名・和名などを入れて検索に効かせる。
BUILTIN_ALARMS = [
    dict(code="PS0000", group="P/S（プログラム・設定）", title="電源を切ってください",
         cause="パラメータ設定の変更で電源の再投入が必要。",
         remedy="いったん電源を切って入れ直す。",
         keywords="000 PW power off 電源 再投入 パラメータ"),
    dict(code="PS0010", group="P/S（プログラム・設定）", title="使用できないGコード",
         cause="指令したGコードが無効、またはそのオプションが無い。",
         remedy="プログラムのGコードを見直す（オプション搭載の有無も確認）。",
         keywords="010 improper g code Gコード 不正"),
    dict(code="PS0011", group="P/S（プログラム・設定）", title="送り速度の指令なし",
         cause="切削送りなのに送り速度Fが指令されていない／0。",
         remedy="F（送り速度）を指令する。",
         keywords="011 no feedrate F 送り"),
    dict(code="PS0070", group="P/S（プログラム・設定）", title="メモリ容量不足",
         cause="プログラム記憶の容量が足りない。",
         remedy="不要なプログラムを削除し、空き容量を確保する。",
         keywords="070 no program space memory 容量 メモリ"),
    dict(code="PS0071", group="P/S（プログラム・設定）", title="データが見つからない",
         cause="指定したプログラム番号(O)／シーケンス番号(N)が無い。",
         remedy="呼び出す番号を確認する。",
         keywords="071 data not found プログラム番号 検索 サーチ"),
    dict(code="PS0085", group="I/O・通信", title="通信エラー（オーバーラン等）",
         cause="RS-232の受信エラー（オーバーラン／フレーミング／パリティ）。",
         remedy="ボーレート・パリティ・ストップビット・ケーブルを機器と合わせる。",
         keywords="085 communication overrun framing parity RS232 入出力 転送 DNC"),
    dict(code="PS0086", group="I/O・通信", title="DR信号オフ",
         cause="入出力機器のDR（データセットレディ）がオフ。",
         remedy="I/O機器の電源・結線・通信設定を確認する。",
         keywords="086 DR off リーダ パンチャ I/O 入出力"),
    dict(code="PS0087", group="I/O・通信", title="バッファオーバーフロー",
         cause="受信を止められずバッファがあふれた。",
         remedy="フロー制御・通信速度を見直す。",
         keywords="087 buffer overflow 通信 オーバーフロー"),
    dict(code="PS0090", group="P/S（プログラム・設定）", title="原点復帰未完了",
         cause="原点復帰が完了できない（原点に近すぎる／速度不足）。",
         remedy="軸を十分離してから低速で原点復帰する。",
         keywords="090 reference return 原点 復帰 リファレンス"),
    dict(code="PS0100", group="P/S（プログラム・設定）", title="パラメータ書込許可中（PWE=1）",
         cause="パラメータ書込が許可されたまま（PWE=1）。",
         remedy="設定(SETTING)画面でPWE=0に戻す。",
         keywords="100 parameter write enable PWE 書込許可 設定 セッティング"),
    dict(code="PS0101", group="P/S（プログラム・設定）", title="メモリ要クリア",
         cause="書込中の電源断などでメモリ内容が不整合。",
         remedy="指示に従いメモリ操作を行う（必ずバックアップし慎重に）。",
         keywords="101 clear memory メモリ クリア"),
    dict(code="PS0224", group="P/S（プログラム・設定）", title="原点復帰してください",
         cause="原点未確立のまま自動運転しようとした。",
         remedy="自動運転の前に各軸の原点復帰を行う。",
         keywords="224 return to reference 原点 自動運転"),
    dict(code="PS5010", group="P/S（プログラム・設定）", title="ブロック終端(EOR)異常",
         cause="プログラム終端 %(EOR) を不正に検出。",
         remedy="プログラムの終端や転送内容を確認する。",
         keywords="5010 end of record EOR % 終端 転送"),
    dict(code="OT0500", group="OT（オーバートラベル）", title="＋側オーバートラベル（ハード）",
         cause="＋方向の移動端（ハードリミット）を超えた。",
         remedy="手動で−側へ戻し、移動量や座標系を確認する。",
         keywords="500 over travel + オーバートラベル ハード リミット ストローク"),
    dict(code="OT0501", group="OT（オーバートラベル）", title="−側オーバートラベル（ハード）",
         cause="−方向の移動端（ハードリミット）を超えた。",
         remedy="手動で＋側へ戻し、原因を確認する。",
         keywords="501 over travel - オーバートラベル ハード"),
    dict(code="OT0510", group="OT（オーバートラベル）", title="＋側ストロークリミット（ソフト）",
         cause="記憶式ストロークリミット1の＋側を超える指令。",
         remedy="指令座標・前振り量を見直す（測定の前振りが移動端を越えていないか）。",
         keywords="510 stroke limit soft + ソフト リミット 前振り 測定"),
    dict(code="OT0511", group="OT（オーバートラベル）", title="−側ストロークリミット（ソフト）",
         cause="記憶式ストロークリミット1の−側を超える指令。",
         remedy="指令座標・前振り量を見直す。",
         keywords="511 stroke limit soft - ソフト リミット 前振り"),
    dict(code="SV0401", group="SV（サーボ）", title="サーボ準備未完了（VRDY OFF）",
         cause="サーボアンプの準備ができていない。",
         remedy="アンプ電源・結線・非常停止・MCC（電磁接触器）を確認する。",
         keywords="401 servo v ready off サーボ アンプ 準備"),
    dict(code="SV0410", group="SV（サーボ）", title="停止中の位置偏差過大",
         cause="停止しているのに位置偏差が大きすぎる。",
         remedy="機械の引っ掛かり・サーボ・パラメータを確認する。",
         keywords="410 excess error stop 偏差 サーボ 停止"),
    dict(code="SV0411", group="SV（サーボ）", title="移動中の位置偏差過大",
         cause="移動中に位置偏差が大きすぎる。",
         remedy="負荷・送り速度・サーボ調整を確認する。",
         keywords="411 excess error move 偏差 移動"),
    dict(code="OH0700", group="OH（過熱）", title="制御部過熱",
         cause="制御装置内の温度が高い。",
         remedy="冷却ファン・フィルタ・盤内／周囲温度を確認する。",
         keywords="700 overheat 過熱 ファン 温度 オーバーヒート"),
    dict(code="OH0701", group="OH（過熱）", title="ファンモータ異常",
         cause="冷却ファンの異常。",
         remedy="ファンモータを点検／交換する。",
         keywords="701 fan motor ファン モータ"),
    dict(code="DS0300", group="APC/DS（位置検出）", title="原点確立要求（要原点復帰）",
         cause="アブソリュート位置の確立が必要。",
         remedy="該当軸の原点復帰を行う。",
         keywords="300 apc need reference アブソ 原点 ZRN リファレンス"),
    dict(code="APC（電池電圧低下）", group="APC/DS（位置検出）", title="アブソ電池の電圧低下（警告）",
         cause="アブソリュートパルスコーダのバックアップ電池の電圧低下。",
         remedy="電源を入れたまま早めに電池交換する（切ると位置を失う）。",
         keywords="apc battery 電池 アブソ バックアップ 警告 電圧低下"),
    dict(code="APC（電池切れ）", group="APC/DS（位置検出）", title="アブソ電池切れ（位置喪失）",
         cause="バックアップ電池切れでアブソ位置を喪失。",
         remedy="電池交換後、各軸の原点を再確立する。",
         keywords="apc battery zero 電池切れ 位置喪失 原点 アブソ"),
    dict(code="SP（主軸）", group="SP（主軸）", title="主軸アンプ／主軸系アラーム",
         cause="主軸アンプ・主軸モータ系の異常（具体番号は機種依存）。",
         remedy="主軸アンプの表示番号と機械メーカー資料で内容を確認する。",
         keywords="SP spindle 主軸 アンプ モータ"),
    dict(code="PW0000", group="電源", title="電源の再投入が必要",
         cause="パラメータ変更などで電源OFFが必要。",
         remedy="電源を切って入れ直す。",
         keywords="PW power off 電源 再投入"),
    dict(code="#3000系", group="マクロ／外部", title="マクロ／外部アラーム（カスタムメッセージ）",
         cause="カスタムマクロ(#3000)やPMCが出すメッセージ（機械固有）。",
         remedy="表示されたメッセージと機械メーカー資料で確認する。CSVに登録しておくと次回から引ける。",
         keywords="3000 macro 外部 PMC メッセージ カスタム 機械固有"),
    dict(code="I/O・ファイル", group="I/O・通信", title="入出力／ファイル操作エラー",
         cause="入出力装置やファイル操作の異常。",
         remedy="機器・接続・空き容量を確認する。",
         keywords="io 入出力 file ファイル メモリカード usb"),
]


def _norm(text: str) -> str:
    """検索用に空白除去＋大文字化（日本語はそのまま）。"""
    return "".join((text or "").split()).upper()


def _digits(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch.isdigit())


def _haystack(alarm: dict) -> str:
    return " ".join(str(alarm.get(k, "")) for k in
                    ("code", "group", "title", "cause", "remedy", "keywords"))


def search_alarms(alarms, query: str):
    """番号 or キーワードでアラームを絞り込む。空クエリは全件。"""
    q = (query or "").strip()
    if not q:
        return list(alarms)
    qn = _norm(q)
    qd = _digits(q)
    out = []
    for a in alarms:
        if qn and qn in _norm(_haystack(a)):
            out.append(a)
            continue
        if qd:
            cd = _digits(a.get("code", ""))
            if cd and cd.lstrip("0") == qd.lstrip("0"):
                out.append(a)
                continue
            # keywords に旧番号がある場合も拾う
            if any(tok and tok.lstrip("0") == qd.lstrip("0")
                   for tok in (_digits(w) for w in str(a.get("keywords", "")).split())):
                out.append(a)
    return out


_HEADER_MAP = {
    "code": "code", "コード": "code", "番号": "code", "アラーム": "code",
    "group": "group", "分類": "group", "種別": "group",
    "title": "title", "名称": "title", "メッセージ": "title", "内容": "title",
    "cause": "cause", "原因": "cause",
    "remedy": "remedy", "対処": "remedy", "処置": "remedy",
    "keywords": "keywords", "キーワード": "keywords",
}


def _read_user_csv(path: Path):
    """ユーザー/メーカー固有アラームCSVを読む。見出しは日英どちらでも可。"""
    out = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        try:
            text = path.read_text(encoding="cp932")
        except Exception:
            return out
    reader = csv.DictReader(text.splitlines())
    for raw in reader:
        entry = {"code": "", "group": "（ユーザー登録）", "title": "",
                 "cause": "", "remedy": "", "keywords": ""}
        for key, value in raw.items():
            mapped = _HEADER_MAP.get((key or "").strip().lower()) \
                or _HEADER_MAP.get((key or "").strip())
            if mapped:
                entry[mapped] = (value or "").strip()
        if entry["code"] or entry["title"]:
            out.append(entry)
    return out


def load_alarms(user_csv=None):
    """内蔵アラーム＋（あれば）ユーザーCSVを結合して返す。"""
    alarms = [dict(a) for a in BUILTIN_ALARMS]
    if user_csv:
        p = Path(user_csv)
        if p.exists():
            alarms += _read_user_csv(p)
    return alarms
