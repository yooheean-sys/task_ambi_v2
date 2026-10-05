# 付2 成果物の自己検証（D14-g の完了条件 (i)〜(vii)）。outcome/design/x2/ で
#   python3 x2_selfcheck.py
# 設計成果物の検査専用であり製品コードではない。uv run ではなく python3 直呼びで動く（標準ライブラリのみ）。
import csv, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, 'x2_cluster_index.csv')
CONTRACT = os.path.join(HERE, '..', 'phase4', 'p4_module_contract.csv')
P6 = os.path.join(HERE, '..', 'phase6', 'p6_external_interface.csv')
UC = os.path.join(HERE, '..', 'phase1', '04_usecase_desc.csv')

MODS = ['board', 'vault', 'settings', 'monitor', 'stages', 'executor', 'orchestrator', 'trigger']
COMMON_EIF = {'EIF-08', 'EIF-09', 'EIF-13'}  # 全 MOD 共通（.env／config.json／journald）。D14-f
COLS = ['cluster_id', 'slug', 'name', '対象MOD', 'MOD数', '範囲外理由', '主検証IF', '通過IF', 'UC', 'EIF件数', 'EIF一覧',
        '実際に通すEIF', '外部模擬', 'HTTP入口代替', 'Protocolスタブ', '依存クラスタ', '文書']
HEADINGS = ['## 1. 対象モジュールと選定理由', '## 2. 検証する境界', '## 3. テスト対象のユースケース',
            '## 4. 必要なテストダブル', '## 5. 前提条件と実行順序上の依存クラスタ', '## 6. 検証観点']

def load_csv(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))

def is_crlf(path):
    data = open(path, 'rb').read()
    return b'\r\n' in data and data.count(b'\n') == data.count(b'\r\n')

def split(cell):
    cell = (cell or '').strip()
    if not cell or cell.startswith('—'):
        return []
    return [x.strip() for x in cell.split(';') if x.strip()]

def doubles(row):
    n = len(split(row['Protocolスタブ']))
    if row['外部模擬'].strip() and not row['外部模擬'].strip().startswith('—'):
        n += 1
    return n

def main():
    errors, notes = [], []
    rows = load_csv(INDEX)
    if list(rows[0].keys()) != COLS:
        errors.append(f'列が規定と違う: {list(rows[0].keys())}')
    if not is_crlf(INDEX):
        errors.append('(vi) x2_cluster_index.csv が CRLF でない')

    contract = load_csv(CONTRACT)
    if_info = {r['if_id']: (r['提供モジュール'], r['利用モジュール']) for r in contract}
    p6 = load_csv(P6)
    eif_mods = {r['ext_if_id']: set(x.strip() for x in r['利用するモジュール(MOD)'].split(';')) for r in p6}
    ucs = {r['uc_id'] for r in load_csv(UC)}
    ids = [r['cluster_id'] for r in rows]

    primary_seen = {}
    prev = None
    for r in rows:
        cid = r['cluster_id']
        mods = split(r['対象MOD'])
        # (ii) 語彙
        for m in mods:
            if m not in MODS:
                errors.append(f'{cid}: 対象MOD に未知の MOD {m}')
        for u in split(r['UC']):
            if u not in ucs:
                errors.append(f'{cid}: UC {u} が 04_usecase_desc.csv に無い')
        for d in split(r['依存クラスタ']):
            if d not in ids:
                errors.append(f'{cid}: 依存クラスタ {d} が存在しない')
        # (iii) MOD 数
        if str(len(mods)) != r['MOD数']:
            errors.append(f'{cid}: MOD数 {r["MOD数"]} が対象MOD の数 {len(mods)} と違う')
        if not (3 <= len(mods) <= 6) and not r['範囲外理由'].strip():
            errors.append(f'{cid}: MOD 数 {len(mods)} が 3〜6 の外なのに範囲外理由が空')
        # (i)(vii) IF
        for kind in ('主検証IF', '通過IF'):
            for i in split(r[kind]):
                if i not in if_info:
                    errors.append(f'{cid}: {kind} の {i} が契約 csv に無い'); continue
                prov, cons = if_info[i]
                if prov not in mods or cons not in mods:
                    errors.append(f'{cid}: {kind} の {i}（{prov}→{cons}）の両端がクラスタ内にない')
                if kind == '主検証IF':
                    if i in primary_seen:
                        errors.append(f'(i) {i} が主検証に重複: {primary_seen[i]} と {cid}')
                    primary_seen[i] = cid
        # (iv) EIF
        listed = split(r['EIF一覧'])
        calc = sorted(e for e, ms in eif_mods.items() if e not in COMMON_EIF and ms & set(mods))
        if sorted(listed) != calc:
            errors.append(f'{cid}: EIF一覧 {listed} が p6 csv からの再計算 {calc} と違う')
        if str(len(calc)) != r['EIF件数']:
            errors.append(f'{cid}: EIF件数 {r["EIF件数"]} が再計算 {len(calc)} と違う')
        for e in split(r['実際に通すEIF']):
            if e not in eif_mods:
                errors.append(f'{cid}: 実際に通すEIF の {e} が p6 csv に無い')
            elif e not in listed:
                errors.append(f'{cid}: 実際に通すEIF の {e} が EIF一覧に無い')
        key = (len(calc), doubles(r))
        if prev is not None and key < prev[1]:
            errors.append(f'(iv) 並び順: {cid} {key} が {prev[0]} {prev[1]} より前に来るべき（EIF 件数→ダブル数）')
        prev = (cid, key)
        # (v) md
        md = os.path.join(HERE, r['文書'])
        if not os.path.exists(md):
            errors.append(f'{cid}: 文書 {r["文書"]} が無い')
        else:
            text = open(md, encoding='utf-8').read()
            for h in HEADINGS:
                if h not in text:
                    errors.append(f'{cid}: {r["文書"]} に見出し「{h}」が無い')
            for i in split(r['主検証IF']):
                if i not in text:
                    errors.append(f'{cid}: {r["文書"]} に主検証 {i} が書かれていない')
        notes.append(f'{cid} {r["slug"]}: MOD {len(mods)}・主検証 IF {len(split(r["主検証IF"]))}・通過 {len(split(r["通過IF"]))}・EIF {len(calc)}・ダブル {doubles(r)}・依存 {r["依存クラスタ"] or "—"}')

    missing = [i for i in if_info if i not in primary_seen]
    if missing:
        errors.append(f'(i) 主検証クラスタの無い IF: {missing}')
    notes.append(f'IF 被覆: {len(primary_seen)}/{len(if_info)}')

    for n in notes:
        print(n)
    print('ERRORS:', 'none' if not errors else '')
    for e in errors:
        print('  -', e)
    return 1 if errors else 0

if __name__ == '__main__':
    sys.exit(main())
