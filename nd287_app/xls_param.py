# -*- coding: utf-8 -*-
"""特注パラメータの Excel シート(.xls/.xlsx)を読み、製品データ(.prm)と同じ {番号:値} に
落とし込む。MKPRM.xls で作れない特注（DDモーター等）を、かんたん作成で扱うための土台。

調査で判明した規則（全7系列を解析）:
  テンプレA「NC ATT.PARAMETER LIST」: 「番地/項目/設定値」表が左右2列(列0/2/3, 列5/7/8)、
    列1/6は "---" フィラー。見出し直下に CNC機種(31iM 等)。ヘッダに 型式/製番(受注伝票番号)/
    GEAR/AXIS/MOTOR MODEL/DETECTOR。
  テンプレB「NC PARAMETER LIST(31i)」(MH系): 軸別シート。NO./項目/値 が左右(列0/1/2, 3/4/5)。
  DD初期化シート: No./Value のサーボ定数。
規則の要点:
  - セル位置は注記行で下にずれる → 見出し文字列を探索して相対に読む（固定座標にしない）。
  - 番地は xlrd で float "1815.0" → 整数文字列 "1815"。値はビット列(* 不問・先頭0)・小数・整数。
  - 1シートに表が複数積まれる / 軸別シート がある。
  - DD判別: 番地2300があり「DD制御本体ビット(#2)=1」。補強: ギア1/1・モータ "Dis"・DD初期化シート。
Qt非依存。ローダ(.xls=xlrd / .xlsx=openpyxl)とグリッド解析を分離して単体試験可能にする。
"""

import csv
import io
import re
from pathlib import Path


# ---- 値の判定・正規化 -------------------------------------------------------
def is_addr(v) -> bool:
    """4桁(1000-9999)のパラメータ番地らしいか。float '1815.0' / 文字 '1815' 両対応。"""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return 1000 <= v <= 9999 and float(v) == int(v)
    s = str(v).strip()
    if not re.match(r"^\d{3,4}(\.0)?$", s):
        return False
    return 1000 <= int(float(s)) <= 9999


def norm_addr(v) -> str:
    """番地を整数文字列に。'1815.0'→'1815'、1815.0→'1815'。"""
    if isinstance(v, (int, float)):
        return str(int(v))
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def norm_value(v) -> str:
    """設定値を文字列に。整数floatは整数化、小数はそのまま、ビット列(*・先頭0)は保持。"""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v) == int(v) else ("%.6f" % v).rstrip("0").rstrip(".")
    return str(v).strip()


def _cell(grid, r, c):
    if 0 <= r < len(grid) and 0 <= c < len(grid[r]):
        v = grid[r][c]
        return "" if v is None else v
    return ""


# ---- 見出し行・表抽出 -------------------------------------------------------
def _header_pairs(row):
    """行が表見出しなら [(番地列, 設定値列), ...] を返す。違えば None。

    テンプレA: '番 地' と '設定値'。MH: 'NO.' と '値/VALUE'。DD: 'No.' と 'Value'。
    """
    vals = [str(x).strip() for x in row]
    pairs = []
    if any(v.replace(" ", "") == "番地" for v in vals) and "設定値" in vals:
        a = [c for c, v in enumerate(vals) if v.replace(" ", "") == "番地"]
        b = [c for c, v in enumerate(vals) if v == "設定値"]
    elif "NO." in vals and any(("値" in v or "VALUE" in v.upper()) for v in vals):
        a = [c for c, v in enumerate(vals) if v == "NO."]
        b = [c for c, v in enumerate(vals) if ("値" in v or "VALUE" in v.upper())]
    elif vals.count("No.") >= 1 and "Value" in vals:
        a = [c for c, v in enumerate(vals) if v == "No."]
        b = [c for c, v in enumerate(vals) if v == "Value"]
    else:
        return None
    for ac in a:
        vc = min([c for c in b if c > ac], default=None)
        if vc is not None:
            pairs.append((ac, vc))
    return pairs or None


def _extract_values(grid):
    """グリッド全体から {番地:設定値} と CNC機種トークンを取る（複数表・複数列対応）。"""
    out, cnc = {}, None
    r, n = 0, len(grid)
    while r < n:
        pairs = _header_pairs(grid[r])
        if pairs:
            tok = str(_cell(grid, r + 1, 0)).strip()        # 見出し直下のCNC機種
            if tok and tok != "---" and not is_addr(_cell(grid, r + 1, 0)):
                cnc = cnc or tok
            dr = r + 1
            while dr < n:
                if _header_pairs(grid[dr]):                 # 次の表は外側ループへ
                    break
                rowtxt = " ".join(str(x) for x in grid[dr])
                if "注意" in rowtxt or "CAUTION" in rowtxt.upper():
                    break
                for ac, vc in pairs:
                    av = _cell(grid, dr, ac)
                    if av == "" or not is_addr(av):
                        continue
                    vv = _cell(grid, dr, vc)
                    if vv == "":
                        continue
                    out[norm_addr(av)] = norm_value(vv)
                dr += 1
            r = dr
        else:
            r += 1
    return out, cnc


def _find_meta(grid):
    """ヘッダから 型式/製番/モータ/ギア/軸/方向 を探す（ラベル探索・位置揺れに強い）。"""
    meta = {"model": "", "serial": "", "motor": "", "gear": "", "axis": "",
            "direction": ""}

    def right_of(r, c):
        for cc in range(c + 1, len(grid[r])):
            if str(_cell(grid, r, cc)).strip() != "":
                return str(_cell(grid, r, cc)).strip()
        return ""

    for r in range(min(len(grid), 16)):
        for c in range(len(grid[r])):
            cell = str(_cell(grid, r, c)).strip()
            if not cell:
                continue
            key = cell.replace(" ", "")
            if not meta["model"] and (key in ("型式", "MODEL") or cell.startswith("型")
                                      or cell.endswith(" MODEL")):
                if "MOTOR" not in cell.upper():     # MOTOR MODEL と取り違えない
                    meta["model"] = right_of(r, c)
            if not meta["serial"] and (("製" in cell and "番" in cell)
                                       or "受注伝票番号" in cell or "LOT NO" in cell.upper()):
                meta["serial"] = right_of(r, c).lstrip("#")
            if not meta["motor"] and "MOTOR MODEL" in cell.upper():
                meta["motor"] = right_of(r, c)
            if not meta["gear"] and ("GEAR" in cell.upper() or "減速比" in cell):
                meta["gear"] = right_of(r, c)
            if not meta["axis"] and (key == "AXIS" or "軸）" in cell or "AXIS（" in cell.upper()):
                meta["axis"] = right_of(r, c)
            if not meta["direction"] and "DIRECTION" in cell.upper() and "ZRN" not in cell.upper():
                meta["direction"] = right_of(r, c)
    return meta


def is_dd(values: dict, motor="", gear="") -> bool:
    """DDモーターか。番地2300のDD制御本体ビット(#2)=1 を主指標、モータ/ギアで補強。"""
    v = str(values.get("2300", "")).strip()
    if v and set(v) <= set("01*") and len(v) >= 3:
        s = v.zfill(8)
        if s[len(s) - 1 - 2] == "1":               # #2 = DDモータ制御本体
            return True
    m = re.sub(r"[\s\-]", "", str(motor or "")).upper()
    if "DIS" in m or m.startswith("DD"):
        return True
    return str(gear or "").strip() in ("1/1", "1:1")


def _kind_from(axis, sheet_name):
    s = (str(axis) + " " + str(sheet_name)).upper()
    if "TILT" in s or "傾斜" in s or "PIV" in s:
        return "傾斜"
    if "ROT" in s or "回転" in s or "A-AXIS" in s or "AAXIS" in s:
        return "回転"
    return ""


def parse_grid(grid, sheet_name="") -> dict:
    """1シート(グリッド) → 解析結果 dict。

    返す: sheet, model, serial, motor, gear, axis, direction, cnc, kind, dd, values{番号:値}。
    """
    values, cnc = _extract_values(grid)
    meta = _find_meta(grid)
    return {
        "sheet": sheet_name, "cnc": cnc, "values": values,
        "model": meta["model"], "serial": meta["serial"], "motor": meta["motor"],
        "gear": meta["gear"], "axis": meta["axis"], "direction": meta["direction"],
        "kind": _kind_from(meta["axis"], sheet_name),
        "dd": is_dd(values, meta["motor"], meta["gear"]),
    }


# ---- ローダ（.xls=xlrd / .xlsx=openpyxl）-----------------------------------
def _grids_from_xls(data: bytes):
    import xlrd
    wb = xlrd.open_workbook(file_contents=data, formatting_info=False)
    out = []
    for sh in wb.sheets():
        if sh.nrows == 0:
            continue
        grid = [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
        out.append((sh.name, grid))
    return out


def _grids_from_xlsx(data: bytes):
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        grid = [[("" if v is None else v) for v in row]
                for row in ws.iter_rows(values_only=True)]
        if grid:
            out.append((ws.title, grid))
    return out


def read_sheets(path) -> list:
    """Excelファイル → [parse_grid 結果]（シートごと）。.xls/.xlsx 両対応。"""
    p = Path(path)
    data = p.read_bytes()
    ext = p.suffix.lower()
    try:
        grids = _grids_from_xlsx(data) if ext == ".xlsx" else _grids_from_xls(data)
    except Exception:
        return []
    return [parse_grid(g, name) for name, g in grids]


def sheets_with_params(path) -> list:
    """値(番地)を1件以上持つシートだけ返す（DD初期化のみ等の空シートを除く運用に）。"""
    return [s for s in read_sheets(path) if s.get("values")]


# ---- 特注フォルダから 製番 で探す ------------------------------------------
def sheet_values(path, sheet_name="") -> dict:
    """指定ファイル・シートの解析結果（{番号:値}入り）を返す。作成時に1枚だけ読む用。"""
    for sh in read_sheets(path):
        if sh.get("values") and (not sheet_name or sh.get("sheet") == sheet_name):
            return sh
    return None


# ===== 特注パラ索引（事前登録＝毎回walkせず型式で引く） =====
INDEX_HEADER = ["型式", "製番", "種別", "モーター", "DD", "番地数", "シート",
                "ファイル名", "パス", "更新"]


def _normmodel(s) -> str:
    """型式照合キー（大文字・記号/空白/カンマ除去）。'RTT-135,BA'→'RTT135BA'。"""
    return re.sub(r"[\s\-_/.,]", "", str(s or "")).upper()


def index_records(custom_dir, progress=None) -> list:
    """特注パラフォルダを再帰走査して索引レコード（値を持つシート単位）を作る（遅い＝随時更新）。

    progress(name) を渡すと進捗通知。OLD配下は除外。戻り値は dict のリスト。
    """
    base = Path(custom_dir)
    out = []
    if not custom_dir or not base.is_dir():
        return out
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".xls", ".xlsx"):
            continue
        if "/OLD/" in (str(p).replace("\\", "/") + "/").upper():
            continue
        try:
            mtime = int(p.stat().st_mtime)
        except Exception:
            mtime = 0
        try:
            sheets = read_sheets(str(p))
        except Exception:
            sheets = []
        for sh in sheets:
            if not sh.get("values"):
                continue
            out.append({"model": sh.get("model", ""), "serial": sh.get("serial", ""),
                        "kind": sh.get("kind", ""), "motor": sh.get("motor", ""),
                        "dd": "1" if sh.get("dd") else "", "naddr": len(sh["values"]),
                        "sheet": sh.get("sheet", ""), "name": p.name, "path": str(p),
                        "mtime": mtime})
        if progress:
            progress(p.name)
    return out


def write_index(path, records):
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(INDEX_HEADER)
        for r in records:
            w.writerow([r.get("model", ""), r.get("serial", ""), r.get("kind", ""),
                        r.get("motor", ""), r.get("dd", ""), r.get("naddr", ""),
                        r.get("sheet", ""), r.get("name", ""), r.get("path", ""),
                        r.get("mtime", "")])


def read_index(path) -> list:
    if not path or not Path(path).exists():
        return []
    raw = Path(path).read_bytes()
    text = raw.decode("cp932", errors="replace")
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    keys = ["model", "serial", "kind", "motor", "dd", "naddr", "sheet", "name",
            "path", "mtime"]
    out = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        out.append({k: (row[i] if i < len(row) else "") for i, k in enumerate(keys)})
    return out


def search_index(records, model="", seiban="") -> list:
    """索引を型式（前方一致を含む）・製番（部分一致）で絞る。両方空なら空を返す。"""
    nm = _normmodel(model)
    sb = str(seiban or "").strip()
    if not nm and not sb:
        return []
    out = []
    for r in records:
        if nm:
            rm = _normmodel(r.get("model", ""))
            if not (rm and (rm == nm or rm.startswith(nm) or nm.startswith(rm))):
                continue
        if sb:
            if sb not in (r.get("serial", "") or "") and sb not in (r.get("name", "") or ""):
                continue
        out.append(r)
    return out


def find_custom_files(custom_dir, seiban) -> list:
    """特注パラフォルダ(再帰)から、製番に対応する Excel を探す。

    まずファイル名に製番を含むものを対象にし、解析して「値を持つシート」を返す。
    戻り値: [{"path","name","sheets":[parse_grid結果...]}]。
    """
    s = str(seiban or "").strip()
    if not s or not custom_dir or not Path(custom_dir).is_dir():
        return []
    out = []
    for p in sorted(Path(custom_dir).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".xls", ".xlsx"):
            continue
        if "/OLD/" in (str(p).replace("\\", "/") + "/").upper():
            continue                               # 旧版アーカイブは除外
        if s not in p.name:                        # まずファイル名で高速一致
            continue
        sheets = [sh for sh in read_sheets(str(p)) if sh.get("values")]
        if sheets:
            out.append({"path": str(p), "name": p.name, "sheets": sheets})
    return out
