#!/usr/bin/env python3
"""付3（設定の 3 層仕分け）成果物の機械検査。D15-k の完了条件 (i)〜(vii)。

使い方: python3 x3_selfcheck.py   → 最後に "ERRORS: none" なら通過。
検査:
 (i)   x3_config_catalog.csv: 指定 10 列・CRLF・CFG-nn 連番・配置先∈{a,b,c1,c2}・秘密 Y⇔a・
       必須/任意・既定値・上書き階層が非空・MOD 名が許可集合内
 (ii)  .env.example のキー集合 ＝ 台帳 (a)(b) のキー集合（パターン行 <…> は除く）、(a) の値はダミー
 (iii) c1 のキー集合 ＝ p5 data_model の *Config フィールド集合（MOD ごと。付3 で新設した 3 キーは例外表）
 (iv)  p5 data_model の *Env フィールド（大文字キー）が台帳 (b)/(a) に在る（apps/api・ui のキーは例外表）
 (v)   ルート config.json に憲章 §5 の最低限（app_name・env_label・log.*・enabled_modules・defaults.*）が在る
 (vi)  c1／c2 のキー名に秘密を示す語（api_key／token／secret／password）が無い（D15-i (5)）
 (vii) c2 の行に <SETTINGS_ROOT>/ か <TRIGGER_ROOT>/ の接頭がある
依存: 標準ライブラリのみ。設計成果物の検査であり PoC・LLM・bd は実行しない。
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
P5 = HERE.parent / "phase5"
CATALOG = HERE / "x3_config_catalog.csv"
ENV_EXAMPLE = HERE / ".env.example"

HDR = ["cfg_id", "キー名", "配置先(a/b/c)", "利用モジュール(MOD)", "型", "既定値", "必須/任意",
       "秘密情報か(Y/N)", "説明", "上書き可能な階層"]
MODS = ["board", "vault", "settings", "monitor", "stages", "executor", "orchestrator", "trigger"]
MOD_ALLOWED = set(MODS) | {"apps/api", "ui", "common", "—"}
LAYERS = {"a", "b", "c1", "c2"}
# 付3 で新設した c1 キーの p5 data_model への転記は P5-R1（D16）で完了 → 例外表は空
NEW_C1 = set()
# data_model を持たない読み手（apps/api・ui）の (b) キー
NO_DM_ENV = {"APP_HOST", "APP_PORT", "APP_BASE_URL", "UI_PORT"}
ROOT_MIN = {"root.app_name", "root.env_label", "root.log.level", "root.log.format",
            "root.log.correlation_header", "root.enabled_modules"}
SECRET_WORDS = re.compile(r"api_key|secret|password|(?<![a-z0-9_])token(?![a-z0-9_])", re.I)

errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def read_catalog() -> list[dict[str, str]]:
    raw = CATALOG.read_bytes()
    if b"\r\n" not in raw:
        err("catalog: CRLF ではない")
    if raw.count(b"\n") != raw.count(b"\r\n"):
        err("catalog: LF 混在")
    with CATALOG.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if rows[0] != HDR:
        err(f"catalog: 列名が指定と違う: {rows[0]}")
    return [dict(zip(HDR, r)) for r in rows[1:]]


def mod_tokens(col: str) -> list[str]:
    out = []
    for tok in col.split(";"):
        tok = re.sub(r"（.*?）", "", tok).strip()
        if tok:
            out.append(tok)
    return out


def check_catalog(rows: list[dict[str, str]]) -> None:
    for i, r in enumerate(rows, 1):
        cid = r["cfg_id"]
        if cid != f"CFG-{i:02d}":
            err(f"{cid}: 連番が {i} 番目と一致しない")
        lay = r["配置先(a/b/c)"]
        if lay not in LAYERS:
            err(f"{cid}: 配置先 {lay!r} が {sorted(LAYERS)} にない")
        sec = r["秘密情報か(Y/N)"]
        if sec not in {"Y", "N"}:
            err(f"{cid}: 秘密列 {sec!r}")
        if (sec == "Y") != (lay == "a"):
            err(f"{cid}: 秘密 Y と配置先 a が一致しない（{sec}/{lay}）")
        for col in ("キー名", "型", "既定値", "必須/任意", "説明", "上書き可能な階層"):
            if not r[col].strip():
                err(f"{cid}: {col} が空")
        for tok in mod_tokens(r["利用モジュール(MOD)"]):
            if tok not in MOD_ALLOWED:
                err(f"{cid}: MOD {tok!r} が許可集合にない")
        key = r["キー名"]
        if lay in {"c1", "c2"} and SECRET_WORDS.search(key):
            err(f"{cid}: c1/c2 のキー名に秘密語: {key}")
        if lay == "c2" and not (key.startswith("<SETTINGS_ROOT>/") or key.startswith("<TRIGGER_ROOT>/")):
            err(f"{cid}: c2 のキー名にルート接頭がない: {key}")
        if lay == "c1" and not re.match(r"^(root|" + "|".join(MODS) + r")\.[a-z_][a-z0-9_.]*$", key):
            err(f"{cid}: c1 のキー名の形（<scope>.<key>）でない: {key}")
        if lay in {"a", "b"} and "<" not in key and not re.match(r"^[A-Z][A-Z0-9_]*$", key):
            err(f"{cid}: .env キー名の形でない: {key}")
    ids = [r["cfg_id"] for r in rows]
    if len(ids) != len(set(ids)):
        err("catalog: cfg_id 重複")
    keys = [r["キー名"] for r in rows]
    if len(keys) != len(set(keys)):
        err("catalog: キー名重複")


def check_env_example(rows: list[dict[str, str]]) -> None:
    if not ENV_EXAMPLE.exists():
        err(".env.example が無い")
        return
    env_keys: dict[str, str] = {}
    for ln in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", s)
        if not m:
            err(f".env.example: 行の形が KEY=value でない: {ln!r}")
            continue
        if m.group(1) in env_keys:
            err(f".env.example: キー重複 {m.group(1)}")
        env_keys[m.group(1)] = m.group(2)
    cat = {r["キー名"]: r for r in rows if r["配置先(a/b/c)"] in {"a", "b"} and "<" not in r["キー名"]}
    for k in cat.keys() - env_keys.keys():
        err(f".env.example に台帳キーが無い: {k}")
    for k in env_keys.keys() - cat.keys():
        err(f".env.example のキーが台帳に無い: {k}")
    for k, r in cat.items():
        if r["配置先(a/b/c)"] == "a":
            v = env_keys.get(k, "")
            if v and "REPLACE_ME" not in v and "DUMMY" not in v:
                err(f".env.example: 秘密 {k} の値がダミーに見えない")


def extract_classes(mmd: Path) -> dict[str, list[str]]:
    """classDiagram の class ブロック名→ '+' で始まるフィールド名の一覧。"""
    out: dict[str, list[str]] = {}
    cur = None
    for ln in mmd.read_text(encoding="utf-8").splitlines():
        m = re.match(r'\s*class\s+([A-Za-z_]\w*)(?:\["[^"]*"\])?\s*\{', ln)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        if cur and re.match(r"\s*\}\s*$", ln):
            cur = None
            continue
        if cur:
            mm = re.match(r"\s*\+([A-Za-z_<][\w<>]*)(.*)$", ln)
            if mm:
                out[cur].append(mm.group(1))
                rest = mm.group(2)
                # "+max_iter / max_rpm（…）" 形: 直後が " / <識別子>" なら 2 つ目も拾う
                m2 = re.match(r"\s*/\s*([A-Za-z_]\w*)", rest)
                if m2:
                    out[cur].append(m2.group(1))
    return out


def check_p5(rows: list[dict[str, str]]) -> None:
    c1 = {r["キー名"] for r in rows if r["配置先(a/b/c)"] == "c1"}
    envk = {r["キー名"] for r in rows if r["配置先(a/b/c)"] in {"a", "b"}}
    for mod in MODS:
        mmd = P5 / mod / f"p5_{mod}_data_model.mmd"
        if not mmd.exists():
            err(f"p5 data_model が無い: {mmd}")
            continue
        classes = extract_classes(mmd)
        cfg_fields: set[str] = set()
        env_fields: set[str] = set()
        for name, fields in classes.items():
            if name.endswith("Config") and name not in {"HooksConfig"}:
                cfg_fields |= set(fields)
            if name.endswith("Env"):
                env_fields |= set(fields)
        want = {f"{mod}.{f}" for f in cfg_fields}
        have = {k for k in c1 if k.startswith(mod + ".")}
        for k in sorted(want - have):
            err(f"{mod}: data_model の *Config フィールドが台帳に無い: {k}")
        for k in sorted(have - want - NEW_C1):
            err(f"{mod}: 台帳の c1 キーが data_model に無い: {k}")
        for f in sorted(env_fields):
            if f.startswith("<"):
                continue
            if f not in envk:
                err(f"{mod}: data_model の *Env フィールドが台帳 (a)(b) に無い: {f}")
    for k in sorted(NO_DM_ENV - envk):
        err(f"台帳 (b) に {k} が無い")
    root = {k for k in c1 if k.startswith("root.")}
    for k in sorted(ROOT_MIN - root):
        err(f"ルート config.json の最低限キーが台帳に無い: {k}")
    if not any(k.startswith("root.defaults.") for k in root):
        err("ルート config.json に defaults.* が無い")


def main() -> int:
    rows = read_catalog()
    check_catalog(rows)
    check_env_example(rows)
    check_p5(rows)
    from collections import Counter
    c = Counter(r["配置先(a/b/c)"] for r in rows)
    print(f"catalog rows={len(rows)} a={c['a']} b={c['b']} c1={c['c1']} c2={c['c2']}")
    if errors:
        print("ERRORS:")
        for e in errors:
            print("  -", e)
        return 1
    print("ERRORS: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
