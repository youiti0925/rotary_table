# -*- coding: utf-8 -*-
"""受注伝票番号(Seiban)起点の「かんたん作成」ロジック（GUI非依存・単体試験可）。

流れ:
  1. Seiban から製品データフォルダの傾斜(T)・回転(R)ファイルを探す。
  2. 見つかった製品.prm の中身から、型式・必要容量・モーター等を読む。
  3. 必要容量から「作れる制御装置（号機）」を絞り、各軸の割り当てを提案する。
これを ParamWizardDialog から呼んで、Seiban→何を作るか→どの制御装置か→作成、を導く。
"""

import csv
import io
import os
import re
from pathlib import Path

from . import prm_format, fanuc_param

# 頭文字 → 種別（出力ファイル名と同じ規則: T=傾斜, R=回転）
# パラメータファイルの拡張子。機械へ渡すのは .DAT（実機が出力する形式）だが、
# 従来の .prm / .txt も引き続き探せるようにする。
PARAM_EXTS = (".prm", ".txt", ".dat")

PREFIX_KIND = {"T": "傾斜", "R": "回転"}
KIND_PREFIX = {"傾斜": "T", "回転": "R"}
_CAP_NUMS = ("20", "40", "80", "160")


def find_seiban_files(folder, seiban) -> list:
    """製品データフォルダから Seiban に対応する傾斜(T)/回転(R)ファイルを探す。

    ファイル名の頭文字 T/R＋Seiban で判別する（出力名 <頭文字><Seiban>.prm と同じ規則）。
    戻り値: [{"kind": 傾斜/回転, "prefix": T/R, "path": str, "name": str}, ...]（T,Rの順）。
    """
    s = str(seiban or "").strip()
    if not s or not folder or not Path(folder).is_dir():
        return []
    # 共有サーバのフォルダは1万件を超える（15,741件・2026-11-24）。1件ずつ stat して調べると
    # 約29秒かかるので、名前だけで先に絞る（os.scandir は名前を一覧と一緒に返す）。
    # 絞る条件は従来と同じ: 頭文字 T/R の「直後」に Seiban が続く名前・拡張子が PARAM_EXTS。
    want = s.upper()
    cands = []
    with os.scandir(folder) as it:
        for e in it:
            name = e.name
            stem, dot, ext = name.rpartition(".")
            if not dot:
                stem, ext = name, ""
            stem_u = stem.upper()
            if ("." + ext.lower()) not in PARAM_EXTS:
                continue
            if stem_u[:1] not in ("T", "R") or not stem_u[1:].startswith(want):
                continue
            cands.append((name, e))
    found = {}
    for name, e in sorted(cands, key=lambda t: t[0]):
        # 名前が合うものだけ、ファイルか確かめる（フォルダ名が .prm のとき等）
        try:
            if not e.is_file():
                continue
        except OSError:
            continue
        prefix = name[:1].upper()
        if prefix not in found:
            found[prefix] = {"kind": PREFIX_KIND[prefix], "prefix": prefix,
                             "path": str(Path(folder) / name), "name": name}
    return [found[k] for k in ("T", "R") if k in found]


def derive_capacity(amp_model: str = "", motor_model: str = "") -> str:
    """アンプ型式（必要ならモーター型式）から必要容量(20A/40A/80A/160A)を推定する。

    確実に読めたときだけ容量を返す（読めなければ "" ＝ 手で選ぶフォールバック）。
    例: 'αiSV 40' / 'βiSV 80' / 'SVM 160' / 単独の '160' → 容量。
    """
    for src in (amp_model, motor_model):
        s = str(src or "")
        if not s:
            continue
        # 単独トークンの 20/40/80/160（語境界）を最優先で拾う
        for n in sorted(_CAP_NUMS, key=len, reverse=True):
            if re.search(rf"(?<!\d){n}(?!\d)", s):
                return f"{n}A"
    return ""


def _normkey(s) -> str:
    """対応表の照合キー正規化（大文字・記号/空白除去）。"""
    return re.sub(r"[\s\-_/.]", "", str(s or "")).upper()


_MCAP_ALIASES = {
    "model": ("モーター型式", "モータ型式", "モーター", "モータ", "motor model", "motor"),
    "number": ("モーター番号", "モータ番号", "motor number", "motor no", "number"),
    "motor_id": ("モーターid", "モータid", "2020", "motor id", "id"),
    "capacity": ("容量", "アンプ容量", "必要容量", "capacity", "amp"),
}


def _mcap_header(name):
    key = (name or "").strip().lower()
    for canon, aliases in _MCAP_ALIASES.items():
        if key in (a.lower() for a in aliases):
            return canon
    return key


def parse_motor_caps(text: str) -> dict:
    """モーター→容量 対応表CSV → {"by_no":{}, "by_model":{}, "by_id":{}}（正規化キー）。

    列: モーター型式 / モーター番号 / モーターID(2020) / 容量。空容量の行は無視。
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {"by_no": {}, "by_model": {}, "by_id": {}}
    header = [_mcap_header(c) for c in rows[0]]
    idx = {n: header.index(n) for n in set(header) if n in _MCAP_ALIASES}
    out = {"by_no": {}, "by_model": {}, "by_id": {}}

    def cell(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        cap = _norm_cap_text(cell(row, "capacity"))
        if not cap:
            continue
        if cell(row, "number"):
            out["by_no"][_normkey(cell(row, "number"))] = cap
        if cell(row, "model"):
            out["by_model"][_normkey(cell(row, "model"))] = cap
        if cell(row, "motor_id"):
            out["by_id"][_normkey(cell(row, "motor_id"))] = cap
    return out


def _norm_cap_text(cap) -> str:
    s = str(cap or "").strip().upper().replace("Ａ", "A")
    if not s:
        return ""
    if s.endswith("A"):
        s = s[:-1]
    s = s.strip()
    return f"{s}A" if s else ""


def load_motor_caps(path) -> dict:
    """モーター→容量 対応表を読む（cp932/UTF-8両対応）。無ければ空の辞書。"""
    if not path or not Path(path).exists():
        return {"by_no": {}, "by_model": {}, "by_id": {}}
    raw = Path(path).read_bytes()
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            t = parse_motor_caps(raw.decode(enc, errors="strict"))
        except Exception:
            continue
        if any(t.values()):
            return t
    return parse_motor_caps(raw.decode("cp932", errors="replace"))


def load_motor_table(path) -> dict:
    """モーター番号表（マスタ/モーター番号表.csv）→ {番号(2020の値): {"model","cap","alias"}}。無ければ空。

    列: モーター番号,モーター型式,容量,別名,出典件数（製品データから作った表。人が直してよい）。
    """
    out = {}
    if not path or not Path(path).exists():
        return out
    raw = Path(path).read_bytes()
    text = None
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc, errors="strict")
            break
        except Exception:
            continue
    if text is None:
        return out
    for row in csv.DictReader(io.StringIO(text)):
        no = (row.get("モーター番号") or "").strip()
        if not no or no.startswith("#"):
            continue
        out[no.lstrip("0") or "0"] = {
            "model": (row.get("モーター型式") or "").strip(),
            "cap": _norm_cap_text((row.get("容量") or "").strip()) if (row.get("容量") or "").strip() else "",
            "alias": (row.get("別名") or "").strip()}
    _overlay_alpha_id(out, Path(path).with_name(ALPHA_ID_CSV))
    return out


ALPHA_ID_CSV = "αiD_モーターID_2165.csv"            # サーボ説明書 B-65270JA（p.12〜58）のモーターごとの標準 2165
_AMP_BY_2165 = {"10": "10A", "25": "20A", "45": "40A", "85": "80A", "165": "160A"}   # 設定 amp_current_map の逆（10 はデータ）


def _model_key(m):
    return re.sub(r"[\s\-_]", "", str(m or "")).upper().replace("Α", "A")


def _overlay_alpha_id(table, path):
    """αi-D のモーターの容量を、サーボ説明書の標準 2165 で直す（番号表の容量が説明書と食い違う -D が74型式あった）。
    ・同じ番号で型式が一致する行: 容量を説明書の 2165 から決める（"cap_src" に出どころを入れる）。
    ・番号表に無い番号: 説明書の行を足す。番号が重なる（古いモーターと同じ番号）行は、番号表の型式を優先して触らない。
    2165 が 10・25・45・85・165 以外（例 4・365）の行は容量にしない。"""
    try:
        if not Path(path).exists():
            return
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return
    for r in rows:
        no = (r.get("モーターID(2020)") or "").strip().lstrip("0")
        cap = _AMP_BY_2165.get((r.get("2165") or "").strip(), "")
        if not no:
            continue
        cur = table.get(no)
        if cur is None:
            table[no] = {"model": (r.get("型式") or "").strip(), "cap": cap, "alias": "", "cap_src": "B-65270JA"}
        elif _model_key(cur.get("model")) == _model_key(r.get("型式")) and cap:
            cur["cap"] = cap
            cur["cap_src"] = "B-65270JA"


def motor_info(table: dict, motor_id) -> dict:
    """モーター番号（2020 の値）→ {"model","cap","alias"}。表に無ければ {}。"""
    key = str(motor_id or "").strip()
    return dict((table or {}).get(key.lstrip("0") or "0") or {}) if key else {}


def capacity_for_motor(table: dict, motor_no="", motor_model="", motor_id="") -> str:
    """モーター→容量 対応表から容量を引く（番号→ID→型式の順で照合）。無ければ ""。"""
    if not table:
        return ""
    for key, val in (("by_no", motor_no), ("by_id", motor_id), ("by_model", motor_model)):
        if val:
            cap = table.get(key, {}).get(_normkey(val))
            if cap:
                return cap
    return ""


def is_hv(motor_model: str) -> bool:
    """モーター型式に HV（High Voltage＝400V）の記載があれば True。"""
    return "HV" in str(motor_model or "").upper()


def is_d_motor(motor_model: str) -> bool:
    """型式の末尾が -D（-D駆動のモーター。例 αiF12/4000-D・αiF8/3000 HV-D）なら True。
    DD(DiS)モーターは別物なので False。2026-11-24 利用者「-D付はD駆動の事」。"""
    s = str(motor_model or "").strip()
    return bool(re.search(r"-\s*D$", s, re.I)) and not is_dd_motor(s)


def is_b_motor(motor_model: str) -> bool:
    """型式の末尾が -B（αi-B世代のモーター。HV-B 含む）なら True。-Dは別（is_d_motor）。
    番号表では -B は無印と同じ番号の別名だが、型式に -B と**明示**されたときだけ判定する。"""
    return bool(re.search(r"-\s*B$", str(motor_model or "").strip(), re.I))


def only_ib_generation(cands: list) -> list:
    """CNCユニットが 30i/31i/32i/35i 系（31i-MA・35i-MB など）の号機だけに絞る。
    0i-MD・18i は入らない。2026-11-24 利用者「-Bは31iとか決まってる」。"""
    pat = re.compile(r"^\s*(30|31|32|35)i", re.I)
    return [ca for ca in cands if pat.search(str(getattr(ca[0], "cnc", "") or ""))]


def only_d_drive(cands: list) -> list:
    """制御装置マスタの「-D駆動」が ○ の号機だけに絞る（cands は [(controller, 軸...)]）。"""
    return [ca for ca in cands if str(getattr(ca[0], "d_drive", "") or "").strip() in ("○", "〇", "◯", "O", "o")]


def is_dd_motor(motor_model: str) -> bool:
    """DDモーター(直駆動 DiS系)なら True。番手＝トルクで αiS の出力番手とは別物。"""
    norm = re.sub(r"[\s\-_]", "", str(motor_model or "")).upper()
    return "DIS" in norm or norm.startswith("DD")


def detect_system(text: str, motor: str = "", amp: str = "") -> str:
    """製品データの制御系統を推定する: 'FANUC' / '三菱' / '安川/TPC' / '不明'。

    モーター型式・アンプ型式・パラメータ番号から判定。今はFANUCだけ作成対応なので、
    非FANUCを早めに弾く（FANUCのBASICへ適用しないため）の判定に使う。
    """
    t = text or ""
    if not motor:
        mm = re.search(r"^Motor Model=(.*)$", t, re.M)
        motor = mm.group(1).strip() if mm else ""
    if not amp:
        am = re.search(r"^Servo Amp Model=(.*)$", t, re.M)
        amp = am.group(1).strip() if am else ""
    mu = str(motor or "").upper()
    au = str(amp or "").upper()
    # 三菱(MELDAS): アンプ MDS〜 / モーター HG・HF・HC・HA〜
    if au.startswith("MDS") or mu.startswith(("HG", "HF", "HC", "HA")):
        return "三菱"
    # 安川/TPC: モーター SGM〜・TPC / Pナンバー(P100/P110等)パラメータ
    if mu.startswith("SGM") or "TPC" in mu or re.search(r'"P1[0-9]0"', t):
        return "安川/TPC"
    # FANUC: αi/α モーター、または代表パラメータ番号(1815/2020/2165)を持つ
    if ("α" in str(motor or "")) or re.search(r'"0?(?:1815|2020|2165|1825)"', t):
        return "FANUC"
    # 補助: ヘッダ PrintForm（1/5=FANUC, 20=三菱, 33/34=安川TPC）
    pf = re.search(r"^PrintForm=(\d+)", t, re.M)
    if pf:
        n = int(pf.group(1))
        if n == 20:
            return "三菱"
        if n in (33, 34):
            return "安川/TPC"
        if n in (1, 5):
            return "FANUC"
    return "不明"


def is_fanuc_product(text: str, motor: str = "", amp: str = "") -> bool:
    return detect_system(text, motor, amp) == "FANUC"


def motor_voltage(motor_model: str) -> str:
    """モーター型式から電源電圧を返す。HV記載→'400V'、型式があれば'200V'、無ければ''。"""
    s = str(motor_model or "").strip()
    if not s:
        return ""
    return "400V" if is_hv(s) else "200V"


# 番手→アンプ容量の標準組合せ（200V）。400V(HV)は電力同じでも電流が約半分なので半額コード。
_STD_BANDS_200 = [(4, "20A"), (12, "40A"), (30, "80A"), (50, "160A")]
_STD_BANDS_400 = [(4, "10A"), (12, "20A"), (30, "40A"), (50, "80A")]


_ALPHA_D_CAPS = None


def alpha_d_capacity(motor_model: str, csv_path=None):
    """末尾 -D のモーター型式 → サーボ説明書（B-65270JA）の標準 2165 から出したアンプ容量。
    「(40A)」などアンプ区分の付いた行は除き、標準の行の 2165 が1つに決まらないとき・載っていないときは None（呼び出し側が従来の番手の表を使う）。"""
    global _ALPHA_D_CAPS
    if csv_path is not None or _ALPHA_D_CAPS is None:
        caps = {}
        try:
            path = Path(csv_path) if csv_path else _default_alpha_d_csv()
            with open(path, newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if (r.get("アンプ区分") or "").strip():     # 「(40A)」などの付いた行は、別のアンプ用の番号。標準の行だけ使う
                        continue
                    k = _model_key(r.get("型式"))
                    c = _AMP_BY_2165.get((r.get("2165") or "").strip(), "")
                    caps.setdefault(k, set()).add(c)
        except Exception:
            caps = {}
        if csv_path is not None:
            return _alpha_d_lookup(caps, motor_model)
        _ALPHA_D_CAPS = caps
    return _alpha_d_lookup(_ALPHA_D_CAPS, motor_model)


def _alpha_d_lookup(caps, motor_model):
    got = caps.get(_model_key(motor_model))
    if got and len(got) == 1 and "" not in got:
        return next(iter(got))
    return None


def _default_alpha_d_csv():
    from . import settings as _st
    p = _st.app_dir() / "マスタ" / ALPHA_ID_CSV
    return p if p.exists() else Path(__file__).resolve().parent.parent / "マスタ" / ALPHA_ID_CSV


def standard_capacity(motor_model: str) -> str:
    """αiS/αiF モーター型式の「番手」から標準組合せのアンプ容量を返す。

    200V: 2,4→20A ／ 8,12→40A ／ 22,30→80A ／ 40,50→160A。
    400V(HV): 2,4→10A ／ 8,12→20A ／ 22,30→40A ／ 40,50→80A（電流が約半分）。
    例: αiS2/5000→20A, αiS22/4000→80A, αiS22/4000HV→40A。

    対象は αiS/αiF（番手/回転数 形式）に限る。DDモーター(DiS系)や他形式は番手＝トルクで
    αiS の番手とは別物なので推定しない（"" を返し、モーター容量マスタ/手入力に回す）。
    """
    s = str(motor_model or "")
    if is_dd_motor(s):                              # DiS等は番手→容量が当てはまらない
        return ""
    if is_d_motor(s):                               # -D は説明書の標準 2165 が先（番手の表と食い違う型式が多い）
        cap_d = alpha_d_capacity(s)
        if cap_d:
            return cap_d
    # αiS/αiF の「i + S/F + 番手 + /」形だけを対象（誤ヒット防止のため '/' 必須）
    m = re.search(r"[iIＩ]\s*[SsFf]\s*(\d+)\s*/", s) \
        or re.search(r"[αＡ]\s*[iI]?\s*[SsFf]\s*(\d+)\s*/", s)       # 大文字の αIF8/3000 も読む（実データ183件）
    if not m:
        return ""
    size = int(m.group(1))
    bands = _STD_BANDS_400 if is_hv(s) else _STD_BANDS_200
    for lim, cap in bands:
        if size <= lim:
            return cap
    return ""                                        # 範囲外(αiS100等)はマスタ外


def read_product_meta(text: str, *, kind="", motor_caps=None) -> dict:
    """製品データ(.prm)の付加情報を読む。ヘッダ＋CSV形式（System Version=…）専用。

    戻り値 dict: model, kind(傾斜/回転), capacity(推定), amp_model, motor, motor_no,
    gear, direction, sep_detector, mode_hint(フル/セミ/'')。
    N形式（実機ネイティブ）はヘッダが無いので最小限（kind は引数のものを使う）。
    """
    meta = {"model": "", "kind": kind, "capacity": "", "capacity_src": "",
            "voltage": "", "system": "", "amp_model": "", "motor": "", "motor_no": "",
            "motor_id": "", "gear": "", "direction": "", "sep_detector": "",
            "mode_hint": ""}
    if not text:
        return meta
    if fanuc_param.looks_like_fanuc_prm(text):
        # N#####Q1…はFANUC実機ネイティブ形式そのもの。ヘッダが無く型式・モーター
        # は不明だが、系統は FANUC として扱う（""のままだと かんたん作成が
        # 「FANUC以外」と誤ブロックしてしまう）。容量は空＝手動選択になる。
        meta["system"] = "FANUC"
        return meta
    try:
        doc = prm_format.parse_prm(text)
    except Exception:
        return meta
    g = lambda k: prm_format.header_get(doc, k, "").strip()
    meta["model"] = g("Model")
    axis = g("Axis").upper()
    if axis in PREFIX_KIND:
        meta["kind"] = PREFIX_KIND[axis]      # 中身の Axis 欄を優先
    meta["amp_model"] = g("Servo Amp Model")
    meta["motor"] = g("Motor Model")
    meta["motor_no"] = g("Motor Number")
    meta["gear"] = g("Gear Rate").lstrip("'")
    meta["direction"] = g("Direction")
    meta["sep_detector"] = g("Separate Detector")
    try:                                       # パラメータ2020＝モーター型式ID
        meta["motor_id"] = (prm_format.param_value(doc, "2020") or "").strip()
    except Exception:
        meta["motor_id"] = ""
    # 容量の決め方（優先順）: ①Servo Amp Model（データに明記）→ ②モーター→容量 対応表
    # （ユーザー上書き・例外用）→ ③モーター型式の番手から標準組合せで自動判定。
    cap = derive_capacity(meta["amp_model"])
    if cap:
        meta["capacity_src"] = "アンプ"
    if not cap and motor_caps:
        cap = capacity_for_motor(motor_caps, meta["motor_no"], meta["motor"],
                                 meta["motor_id"])
        if cap:
            meta["capacity_src"] = "対応表"
    if not cap:
        cap = standard_capacity(meta["motor"])
        if cap:
            meta["capacity_src"] = "標準"
    meta["capacity"] = cap
    meta["voltage"] = motor_voltage(meta["motor"])    # HV記載→400V
    meta["system"] = detect_system(text, meta["motor"], meta["amp_model"])
    # 別置検出器が入っていればフルクロの手がかり（最終判定は 1815）
    meta["mode_hint"] = "フル" if meta["sep_detector"] else ""
    return meta


def assign_axes(controller, needs: list):
    """1つの制御装置で needs（容量のリスト）に軸を割り当てられるか試す。

    needs: 作りたい軸ぶんの容量文字列 ["160A","160A"] 等（不明は ""）。
    戻り値: 割り当て [軸文字, ...]（needs と同順）。割り当て不可なら None。
    容量指定のある need を先に確定し、残り（"")は空いている軸へ。
    """
    avail = list(controller.axes())          # 実装されている軸 [X,Y,...]
    order = sorted(range(len(needs)), key=lambda i: 0 if needs[i] else 1)
    out = [None] * len(needs)
    for i in order:
        cap = needs[i]
        pick = None
        for a in avail:
            if (not cap) or controller.caps.get(a) == cap:
                pick = a
                break
        if pick is None:
            return None
        out[i] = pick
        avail.remove(pick)
    return out


def controller_voltage(controller) -> str:
    """制御装置マスタの電圧表記を正規化（'AV400V'→'400V', 'AC200V'→'200V'）。不明は ''。"""
    v = str(getattr(controller, "voltage", "") or "").upper()
    if "400" in v:
        return "400V"
    if "200" in v:
        return "200V"
    return ""


def capable_controllers(controllers, needs: list, voltage: str = "") -> list:
    """needs（作りたい軸ぶんの容量）を満たせる制御装置と軸割り当てを返す。

    voltage を指定すると、電圧の合う号機だけに絞る（400V製品は400V号機のみ等）。
    号機の電圧が不明（空欄）なら除外しない。戻り値 [(controller,[軸文字,...]),...]。
    needs が空なら（電圧フィルタは効かせつつ）全件。
    """
    pool = controllers
    if voltage:
        pool = [c for c in controllers
                if controller_voltage(c) in ("", voltage)]
    if not needs:
        return [(c, []) for c in pool]
    out = []
    for c in pool:
        asg = assign_axes(c, needs)
        if asg is not None:
            out.append((c, asg))
    return out



def equivalent_cap(cap: str, from_v: str, to_v: str) -> str:
    """電力が同じ400V⇔200Vのアンプ容量の読み替え（400Vの電流は約半分: 40A(400V)＝80A(200V)）。変えられなければ ''。"""
    import re
    m = re.fullmatch(r"\s*(\d+)\s*A\s*", str(cap or ""), re.I)
    if not m or from_v == to_v:
        return str(cap or "") if from_v == to_v else ""
    n = int(m.group(1))
    if from_v == "400V" and to_v == "200V":
        return f"{n * 2}A"
    if from_v == "200V" and to_v == "400V":
        return f"{n // 2}A" if n % 2 == 0 else ""
    return ""
