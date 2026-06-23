# -*- coding: utf-8 -*-
"""FANUCパラメータの「製品ごとの変更（差分）」を管理し、確認表と差分パラメータ
ファイルを作る。

運用イメージ:
  マスタパラメータ（共通の基準）に対し、製品/仕様ごとに一部のパラメータだけを
  変更する。従来は紙の「パラメータ表」を見て手入力していた。これを:
    1. 紙の表を CSV（型式, 番号, 軸, 変更値, メモ）に一度だけ起こす。
    2. 型式を選ぶと、その製品の変更分だけを
        - 確認表（番号 / 旧値→新値 / メモ）と
        - 差分パラメータファイル（変更する番号だけ）
       として出力する。
    3. カード/LANで機械へ渡し、機械側で PWE=1 にして「入力」する（人が実施）。

安全方針:
  - このモジュールは「ファイルを作るだけ」。機械へ直接書き込みはしない。
  - 差分方式: 変更する番号だけを書く。FANUCは入力したファイルに載っている
    パラメータだけ書き換わる（載っていないものは現状維持）ので、他を壊さない。
  - ★パラメータファイルの厳密なバイト書式は機種で異なる。実機のバックアップ
    1個で必ず検証してから機械へ投入すること（未検証のまま投入しない）。
"""

import csv
import io
import re

# 取り込みCSVの列名ゆらぎを吸収する（紙の表→CSV化を人がやる前提）
_COL_ALIASES = {
    "id": ("id", "番号id", "entry", "エントリ", "登録id"),
    "model": ("型式", "機種", "品種", "製品", "model"),
    "kind": ("種別", "回転傾斜", "傾斜回転", "kind", "type"),
    "controller": ("制御", "制御装置", "nc", "版", "cnc", "controller"),
    "mode": ("モード", "ループ", "クローズド", "semi/full", "mode", "loop"),
    "motor": ("モーター", "モータ", "モータ型式", "motor model", "motor"),
    "motor_no": ("モーター番号", "モータ番号", "motor number", "motor no"),
    "direction": ("方向", "回転方向", "direction"),
    "gear": ("ギア比", "ギヤ比", "減速比", "gear rate", "gear"),
    "basic": ("使用basic", "basic", "ベース", "使用ベーシック"),
    "number": ("番号", "パラメータ", "パラメータ番号", "param", "no", "number"),
    "axis": ("軸", "軸番号", "axis"),
    "value": ("変更値", "値", "設定値", "value", "data"),
    "note": ("メモ", "備考", "説明", "項目", "note", "memo"),
    "seiban": ("seiban", "受注伝票番号", "受注伝票", "伝票番号", "製番"),
    "date": ("登録日", "日付", "更新日", "date"),
}


def _norm_header(name: str) -> str:
    key = (name or "").strip().lower()
    for canon, aliases in _COL_ALIASES.items():
        if key in (a.lower() for a in aliases):
            return canon
    return key


def normalize_model(text: str) -> str:
    """型式照合用に正規化（大文字化・記号/空白除去）。masters.find_entry と同方針。"""
    return re.sub(r"[\s\-_./]", "", str(text or "")).upper()


class ParamChange:
    """1件の変更。controller=制御(MELDAS-60/FANUC等。無ければ全制御共通),
    mode=クローズドループ種別(フル/セミ。1815等で判別。同番号でもセミ/フルで別物),
    number=パラメータ番号, axis=軸(無ければ ""), value=変更後の値。
    seiban/date は由来（登録元の受注伝票番号・登録日）でメタ情報。"""

    __slots__ = ("controller", "mode", "number", "axis", "value", "note",
                 "seiban", "date")

    def __init__(self, number, axis="", value="", note="", controller="",
                 mode="", seiban="", date=""):
        self.controller = str(controller or "").strip()
        self.mode = str(mode or "").strip()
        self.number = str(number).strip()
        self.axis = str(axis or "").strip()
        self.value = str(value).strip()
        self.note = str(note or "").strip()
        self.seiban = str(seiban or "").strip()
        self.date = str(date or "").strip()

    def __eq__(self, other):
        return isinstance(other, ParamChange) and (
            self.controller, self.mode, self.number, self.axis, self.value,
            self.note, self.seiban, self.date
        ) == (other.controller, other.mode, other.number, other.axis,
              other.value, other.note, other.seiban, other.date)

    def __repr__(self):
        return (f"ParamChange({self.number!r},{self.axis!r},{self.value!r},"
                f"{self.note!r},ctrl={self.controller!r},mode={self.mode!r})")

    def key(self):
        """同一パラメータ判定キー（制御＋モード＋番号＋軸）。

        モードを含めるのが要点: セミクロとフルクロは同じ型式・同じ番号でも
        値が違う別物なので、混ざらないよう別キーにする。"""
        return (self.controller, self.mode, self.number, self.axis)


def parse_changes(text: str) -> dict:
    """CSVテキスト →  {型式: [ParamChange, ...]}。列名はゆらぎを吸収する。

    番号が空の行（区切り・見出し）は読み飛ばす。同じ型式・同じ番号(＋軸)が複数
    あれば後勝ち（最後の値を採用）。
    """
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {}
    header = [_norm_header(c) for c in rows[0]]
    idx = {name: header.index(name) for name in set(header) if name in _COL_ALIASES}

    def cell(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    out = {}
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        number = cell(row, "number")
        if not number:
            continue
        model = cell(row, "model")
        change = ParamChange(number, cell(row, "axis"),
                             cell(row, "value"), cell(row, "note"),
                             controller=cell(row, "controller"),
                             mode=cell(row, "mode"),
                             seiban=cell(row, "seiban"), date=cell(row, "date"))
        bucket = out.setdefault(model, [])
        # 同じ番号(＋軸)は後勝ちで置き換え
        for i, ex in enumerate(bucket):
            if ex.key() == change.key():
                bucket[i] = change
                break
        else:
            bucket.append(change)
    return out


def load_changes(path) -> dict:
    with open(path, "r", encoding="cp932", errors="replace", newline="") as f:
        return parse_changes(f.read())


def write_changes(path, changes: dict):
    """{型式: [ParamChange]} を CSV に保存（編集UIからの書き戻し用）。"""
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(["型式", "制御", "モード", "番号", "軸", "変更値", "メモ",
                    "Seiban", "登録日"])
        for model, items in changes.items():
            for c in items:
                w.writerow([model, c.controller, c.mode, c.number, c.axis,
                            c.value, c.note, c.seiban, c.date])


# ===== パラメータ作成データベース（エントリ単位） =====
# 1エントリ＝1つの登録済み構成: 型式・種別(傾斜/回転)・モード(フル/セミ)・モーター・
# 制御・軸・Seiban・登録日・使用BASIC ＋ 変更パラメータ一覧[(番号,値,メモ)]。
# 製品データ差分／吸い出し差分／手入力 のどれからでも登録でき、検索・編集・削除・
# リピート作成ができる。制御/軸/Seiban が違えば別エントリ＝作成履歴になる。

ENTRY_HEADER = ["ID", "型式", "種別", "モード", "モーター", "モーター番号", "方向",
                "ギア比", "制御", "軸", "Seiban", "登録日", "使用BASIC",
                "番号", "変更値", "メモ"]


def _s(x) -> str:
    return str(x if x is not None else "").strip()


class ParamEntry:
    """データベースの1件（登録済み構成）。items は [(番号, 値, メモ)]。"""

    __slots__ = ("id", "model", "kind", "mode", "motor", "motor_no", "direction",
                 "gear", "controller", "axis", "seiban", "date", "basic", "items")

    def __init__(self, model="", kind="", mode="", motor="", controller="",
                 axis="", seiban="", date="", basic="", items=None, id="",
                 motor_no="", direction="", gear=""):
        self.id = _s(id)
        self.model = _s(model)
        self.kind = _s(kind)
        self.mode = _s(mode)
        self.motor = _s(motor)
        self.motor_no = _s(motor_no)
        self.direction = _s(direction)
        self.gear = _s(gear)
        self.controller = _s(controller)
        self.axis = _s(axis)
        self.seiban = _s(seiban)
        self.date = _s(date)
        self.basic = _s(basic)
        self.items = [(_s(n), _s(v), _s(m)) for (n, v, m) in (items or []) if _s(n)]

    def count(self):
        return len(self.items)

    def values(self):
        """{番号: 値}（作成に使う変更値）。"""
        return {n: v for (n, v, _m) in self.items if n and v != ""}

    def config_key(self):
        """重複判定キー。制御・軸・Seiban が違えば別エントリ（履歴として残す）。"""
        return (normalize_model(self.model), self.mode, self.controller,
                self.axis, self.seiban)

    def _meta(self):
        return (self.model, self.kind, self.mode, self.motor, self.motor_no,
                self.direction, self.gear, self.controller, self.axis,
                self.seiban, self.date, self.basic, self.items)


def parse_entries(text: str) -> list:
    """CSVテキスト → [ParamEntry]。ID列があればIDで束ね、無ければ旧CSVとみなして
    (型式,制御,モード) で1エントリにまとめて移行する。"""
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    header = [_norm_header(c) for c in rows[0]]
    idx = {name: header.index(name) for name in set(header) if name in _COL_ALIASES}
    has_id = "id" in idx

    def cell(row, name):
        i = idx.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    entries, order = {}, []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        model = cell(row, "model")
        if has_id and cell(row, "id"):
            ekey = ("id", cell(row, "id"))
        else:                                  # 旧CSV: 型式×制御×モードで1件に集約
            ekey = ("auto", normalize_model(model),
                    cell(row, "controller"), cell(row, "mode"))
        e = entries.get(ekey)
        if e is None:
            e = ParamEntry(model=model, kind=cell(row, "kind"), mode=cell(row, "mode"),
                           motor=cell(row, "motor"), motor_no=cell(row, "motor_no"),
                           direction=cell(row, "direction"), gear=cell(row, "gear"),
                           controller=cell(row, "controller"),
                           axis=cell(row, "axis"), seiban=cell(row, "seiban"),
                           date=cell(row, "date"), basic=cell(row, "basic"),
                           id=cell(row, "id") if has_id else "")
            entries[ekey] = e
            order.append(ekey)
        num = cell(row, "number")
        if num:
            e.items.append((num, cell(row, "value"), cell(row, "note")))
    return [entries[k] for k in order]


def _ensure_ids(entries: list) -> list:
    """ID が無いエントリ（旧CSV移行ぶん）に連番IDを振る。"""
    used = {e.id for e in entries if e.id}
    n = 0
    for e in entries:
        if not e.id:
            n += 1
            while f"{n:04d}" in used:
                n += 1
            e.id = f"{n:04d}"
            used.add(e.id)
    return entries


def _next_id(entries: list) -> str:
    mx = 0
    for e in entries:
        if e.id.isdigit():
            mx = max(mx, int(e.id))
    return f"{mx + 1:04d}"


def write_entries(path, entries: list):
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        w.writerow(ENTRY_HEADER)
        for e in entries:
            meta = [e.id, e.model, e.kind, e.mode, e.motor, e.motor_no, e.direction,
                    e.gear, e.controller, e.axis, e.seiban, e.date, e.basic]
            if e.items:
                for (num, val, memo) in e.items:
                    w.writerow(meta + [num, val, memo])
            else:
                w.writerow(meta + ["", "", ""])


def load_entries(path) -> list:
    from pathlib import Path
    if not path or not Path(path).exists():
        return []
    with open(path, "r", encoding="cp932", errors="replace", newline="") as f:
        entries = parse_entries(f.read())
    return _ensure_ids(entries)


def save_entries(path, entries: list):
    write_entries(path, entries)


def get_entry(entries: list, entry_id: str):
    for e in entries:
        if e.id == entry_id:
            return e
    return None


def upsert_entry(path, entry: ParamEntry) -> tuple:
    """エントリを登録（upsert）。同一構成(config_key)があれば更新、無ければ追加。

    戻り値: (id, 'added'/'updated'/'unchanged')。変化が無ければファイルを書かない。
    """
    if not path or not entry or not entry.items:
        return ("", "unchanged")
    entries = load_entries(path)
    for e in entries:
        if e.config_key() == entry.config_key():
            entry.id = e.id
            same = e._meta() == entry._meta()
            if same:
                return (e.id, "unchanged")
            e.model, e.kind, e.mode, e.motor = (entry.model, entry.kind,
                                                entry.mode, entry.motor)
            e.motor_no, e.direction, e.gear = (entry.motor_no, entry.direction,
                                               entry.gear)
            e.controller, e.axis, e.seiban = (entry.controller, entry.axis,
                                              entry.seiban)
            e.date, e.basic, e.items = entry.date, entry.basic, entry.items
            save_entries(path, entries)
            return (e.id, "updated")
    entry.id = _next_id(entries)
    entries.append(entry)
    save_entries(path, entries)
    return (entry.id, "added")


def update_entry(path, entry: ParamEntry) -> bool:
    """ID 指定でエントリを丸ごと置き換える（編集用）。"""
    entries = load_entries(path)
    for i, e in enumerate(entries):
        if e.id == entry.id:
            entries[i] = entry
            save_entries(path, entries)
            return True
    return False


def delete_entry(path, entry_id: str) -> bool:
    """ID 指定でエントリを削除する。削除できたら True。"""
    entries = load_entries(path)
    kept = [e for e in entries if e.id != entry_id]
    if len(kept) != len(entries):
        save_entries(path, kept)
        return True
    return False


def search_entries(entries: list, query: str = "", model: str = "",
                   controller: str = "", mode: str = "", kind: str = "") -> list:
    """エントリ一覧を検索・フィルタ。query は横断部分一致、他は一致で絞る（空は無視）。"""
    q = (query or "").strip().lower()
    mdl = normalize_model(model) if model else ""
    out = []
    for e in entries:
        if mdl and normalize_model(e.model) != mdl:
            continue
        if controller and e.controller != controller:
            continue
        if mode and e.mode != mode:
            continue
        if kind and e.kind != kind:
            continue
        if q:
            hay = " ".join([e.model, e.kind, e.mode, e.motor, e.controller,
                            e.axis, e.seiban, e.date, e.basic]
                           + [n for (n, _v, _m) in e.items]
                           + [m for (_n, _v, m) in e.items]).lower()
            if q not in hay:
                continue
        out.append(e)
    return out


# 軸の表示名（第1〜6軸 = X,Y,Z,A,B,C）。FANUCネイティブ.PRM 内部のセグメント名は
# A1〜A4（A＝軸の意味＋軸番号）だが、画面表示・DBは X/Y/Z/A/B/C を使う。
AXIS_NAMES = ["X", "Y", "Z", "A", "B", "C"]


def axis_name(num) -> str:
    """軸番号(1〜6) → 表示名(X/Y/Z/A/B/C)。範囲外はそのまま文字列化。"""
    try:
        n = int(num)
    except (TypeError, ValueError):
        return _s(num)
    return AXIS_NAMES[n - 1] if 1 <= n <= len(AXIS_NAMES) else str(num)


def axis_number(name) -> int:
    """表示名(X/Y/Z/A/B/C) または 'A4'/'4' → 軸番号(1〜6)。不明なら 0。"""
    s = _s(name).upper()
    if s in AXIS_NAMES:
        return AXIS_NAMES.index(s) + 1
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


# ===== 作成ログ（追記式の厳密な履歴） =====
LOG_HEADER = ["日時", "型式", "種別", "モード", "モーター", "制御", "軸",
              "Seiban", "使用BASIC", "出力ファイル", "反映", "件数"]


def append_log(path, fields: dict):
    """作成ログに1行追記する（上書きしない厳密な履歴）。fields は LOG_HEADER のキー。"""
    from pathlib import Path
    if not path:
        return
    p = Path(path)
    new = not p.exists()
    if p.parent and not p.parent.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    with open(p, "a", encoding="cp932", errors="replace", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOG_HEADER)
        w.writerow([_s(fields.get(k, "")) for k in LOG_HEADER])


def read_log(path) -> list:
    """作成ログを [行リスト] で返す（先頭はヘッダ）。無ければ空。"""
    from pathlib import Path
    if not path or not Path(path).exists():
        return []
    with open(path, "r", encoding="cp932", errors="replace", newline="") as f:
        return list(csv.reader(f))


def kind_from_prefix(prefix: str) -> str:
    """頭文字 T/R → 種別 傾斜/回転。"""
    p = _s(prefix).upper()
    return {"T": "傾斜", "R": "回転"}.get(p, "")


def controller_from_basic(basic_path) -> str:
    """BASICファイル名から制御装置名を推定（'F30BASIC.PRM' → 'F30'）。"""
    from pathlib import Path
    stem = Path(str(basic_path)).stem.upper()
    if stem.endswith("BASIC"):
        stem = stem[:-len("BASIC")]
    return stem.strip(" _-") or _s(basic_path)



def _model_items(changes: dict, model: str) -> list:
    """型式（ゆらぎ吸収して照合）に対応する変更リスト。無ければ空。"""
    target = normalize_model(model)
    if not target:
        return []
    for key, items in changes.items():       # 完全一致（正規化）優先
        if normalize_model(key) == target:
            return list(items)
    for key, items in changes.items():       # 次に前方一致
        nk = normalize_model(key)
        if nk and (target.startswith(nk) or nk.startswith(target)):
            return list(items)
    return []


def changes_for_model(changes: dict, model: str, controller=None) -> list:
    """型式（＋指定があれば制御）に対応する変更リストを返す。

    controller を指定した場合、制御が一致するもの＋制御欄が空（全制御共通）のものを
    返す。指定なしなら全制御ぶんを返す。
    """
    items = _model_items(changes, model)
    if controller:
        c = normalize_model(controller)
        items = [x for x in items if not x.controller or normalize_model(x.controller) == c]
    return items


def controllers_for_model(changes: dict, model: str) -> list:
    """その型式で使われている制御名（重複なし・出現順）。UIの選択肢用。"""
    seen = []
    for x in _model_items(changes, model):
        if x.controller and x.controller not in seen:
            seen.append(x.controller)
    return seen


def parse_param_backup(text: str) -> dict:
    """マスタ/バックアップのパラメータ値を {(番号,軸): 値} に読む（旧値表示用）。

    機種で書式が違うので“緩く”読む: 各行から「番号」と「値」を見つける。
    軸つきは number/axis を拾えた範囲で。読めない行は無視する。
    ★この読み取りも実サンプルで要検証。
    """
    table = {}
    for line in text.splitlines():
        s = line.strip().strip(";")
        if not s or s in ("%",):
            continue
        m = re.match(r"[Nn]?\s*(\d{1,5})\s*(?:[Aa]\s*(\d+))?\s+([-+]?\d+)", s)
        if not m:
            continue
        number, axis, value = m.group(1), (m.group(2) or ""), m.group(3)
        table[(number, axis)] = value
    return table


def checklist_rows(changes: list, master: dict = None) -> list:
    """確認表の行 (番号, 軸, 旧値, 新値, メモ) を返す。master があれば旧値を埋める。"""
    master = master or {}
    rows = []
    for c in changes:
        old = master.get((c.number, c.axis), master.get((c.number, ""), ""))
        rows.append((c.number, c.axis, old, c.value, c.note))
    return rows


def format_checklist(model: str, changes: list, master: dict = None,
                     date: str = "", operator: str = "", controller: str = "") -> str:
    """機械側での照合・入力用の確認表（テキスト）。これは書式リスクなし＝確実。"""
    rows = checklist_rows(changes, master)
    lines = [
        "パラメータ変更 確認表",
        f"型式: {model}    制御: {controller or '―'}    日付: {date}    担当: {operator}",
        "※ 機械側で書込許可にして該当番号だけ入力。一部は電源再投入が必要。",
        "-" * 56,
        f'{"番号":<8}{"軸":<4}{"旧値":>10}  →{"新値":>10}   メモ',
        "-" * 56,
    ]
    for number, axis, old, new, note in rows:
        lines.append(f'{number:<8}{axis:<4}{old:>10}  →{new:>10}   {note}')
    lines.append("-" * 56)
    lines.append(f"変更点数: {len(rows)}")
    return "\n".join(lines)


# ★ここから下（機械が読み込むファイルの書式）は制御(FANUC/MELDAS)・機種で異なる。
#   実サンプルで要検証。確定するまでは確認表（人が手入力）を使うこと。
def format_param_file(changes: list, *, controller: str = "",
                      newline: str = "\r\n") -> str:
    """差分パラメータファイル（変更する番号だけ）。汎用テキスト形（要検証）。

    現状は「N<番号> P<値>」の一般形＋制御名のコメントで出力する。FANUC と MELDAS
    で受け付ける厳密な書式は異なるため、各制御の実機バックアップ1個で確認してから
    投入すること（未確認のまま投入しない）。
    """
    out = ["%"]
    if controller:
        out.append(f"(CONTROLLER {controller} - VERIFY FORMAT WITH REAL BACKUP)")
    for c in changes:
        axis = f" A{c.axis}" if c.axis else ""
        out.append(f"N{c.number}{axis} P{c.value}")
    out.append("%")
    return newline.join(out) + newline


def save_param_file(path, changes: list, controller: str = ""):
    with open(path, "w", encoding="ascii", errors="replace", newline="") as f:
        f.write(format_param_file(changes, controller=controller))


def save_checklist(path, text: str):
    with open(path, "w", encoding="cp932", errors="replace", newline="") as f:
        f.write(text)
