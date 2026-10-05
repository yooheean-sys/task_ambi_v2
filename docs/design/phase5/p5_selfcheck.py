# 第5フェーズ成果物の自己検証（D11-g の完了条件 (ii)〜(vii)）。outcome/design/phase5/ で
#   python3 p5_selfcheck.py            # 全 MOD＋フェーズ全体（P2 被覆）
#   python3 p5_selfcheck.py board      # 1 MOD のみ
#   python3 p5_selfcheck.py board --render   # 併せて mmdc で描画（(i)）
# 設計成果物の検査専用であり製品コードではない。uv run ではなく python3 直呼びで動く（標準ライブラリのみ）。
import csv, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, '..', 'phase4', 'p4_module_contract.csv')
P2 = os.path.join(HERE, '..', 'phase2', 'p2_feature_list.csv')
MMDC = shutil.which('mmdc') or 'mmdc'

MODS = ['board', 'vault', 'settings', 'monitor', 'stages', 'executor', 'orchestrator', 'trigger']
# 受け渡し型 22 件の置き場（p4 notes §2、DEC-002）。提供 MOD の data_model の namespace common_interfaces_<MOD>_py に定義される
PASS_TYPES = {
    'board': ['Issue', 'Comment', 'IssueSpec', 'ClaimFilter'],
    'settings': ['Project', 'ProjectFolders', 'Member', 'LLMProfile', 'LimitPolicy', 'Rules', 'HooksConfig', 'TaskTemplates'],  # TaskTemplates は P5-R1 D16-h（IF-50 の戻り型）
    'stages': ['StageSpec', 'PromptBundle', 'Proposal', 'GateResult'],
    'executor': ['CrewSpec', 'CrewOutcome', 'CrewEvent'],
    'monitor': ['RunVerdict', 'Measurement'],
    'orchestrator': ['ExecutionContext', 'KickoffResult'],
    'vault': ['FolderCheck', 'FileRef', 'WrittenPath'],  # 契約 csv IF-31〜37 の戻り型。P5D-012 で受け渡し型に追記
    'trigger': [],
}
# 憲章 §3 のファイル単位（自 MOD 内部レーンとして許すもの）
INTERNAL_FILES = ['__init__', 'router', 'ui', 'schemas', 'models', 'service', 'repository', 'dependencies', 'exceptions']
# 登録済み外部実体レーン（D11-c）。ここに無い外部を描くときは本表に足す（notes に理由を書く）
EXTERNALS = {
    'bd CLI', 'beads', 'beads-ui', 'Obsidian vault', 'vault FS', 'LLM API', '設定ファイル領域 A', '設定ファイル領域 B',
    '実行履歴 csv', 'DL フォルダ', '.env', 'config.json', '自分', '人間', 'apps/api', 'apps/ui', 'OS', 'systemd',
    'タイマ', 'ファイル投下', 'composition root',
}
PERSIST_VOCAB = ('Obsidian', 'beads', 'GitLab', 'ファイル', 'なし')

def load_csv(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))

def is_crlf(path):
    with open(path, 'rb') as f:
        data = f.read()
    return b'\r\n' in data and data.count(b'\n') == data.count(b'\r\n')

def render(path, errors):
    if not os.path.exists(MMDC):
        errors.append(f'(i) mmdc が見つからない: {MMDC}')
        return
    out = os.path.join(tempfile.mkdtemp(prefix='p5chk_'), 'x.svg')
    r = subprocess.run([MMDC, '-i', path, '-o', out], capture_output=True, text=True)
    if r.returncode != 0:
        errors.append(f'(i) mmdc 失敗 {os.path.basename(path)}: {r.stderr.strip()[:300]}')

# ---------- (ii) シーケンス図のレーン ----------
PART_RE = re.compile(r'^\s*(participant|actor)\s+(\w+)(?:\s+as\s+(.+?))?\s*$')
MSG_RE = re.compile(r'^\s*(\w+)\s*-{1,2}(?:>>|>|x|\))\s*(\w+)\s*:')
def check_sequence(mod, path, protocols, errors):
    ids = {}
    used = set()
    for raw in open(path, encoding='utf-8'):
        line = raw.split('%%')[0].rstrip('\n')
        m = PART_RE.match(line)
        if m:
            ids[m.group(2)] = m.group(3) or m.group(2)
            continue
        m = MSG_RE.match(line)
        if m:
            used.add(m.group(1)); used.add(m.group(2))
    for u in sorted(used):
        if u not in ids:
            errors.append(f'(ii) {os.path.basename(path)}: 未宣言の participant {u}（暗黙生成を禁止）')
    others = [m for m in MODS if m != mod]
    for pid, label in ids.items():
        head = label.split('<br/>')[0].strip()
        ok = False
        if head.startswith(mod + '/'):
            tail = head[len(mod) + 1:]
            # 憲章 §3 のファイル、ライブラリ（lib:*）、パッケージ内分割のサブパッケージ（<name>/。P5D-029）
            if tail.startswith('lib:') or re.fullmatch(r'(%s)\.py' % '|'.join(INTERNAL_FILES), tail) or re.fullmatch(r'[a-z_]+/', tail):
                ok = True
        if not ok and all(p.strip() in protocols for p in re.split(r'／|<br/>', label) if p.strip()):
            ok = True
        if not ok and head in EXTERNALS:
            ok = True
        if not ok:
            errors.append(f'(ii) {os.path.basename(path)}: レーン「{label}」は 自 MOD 内部（{mod}/<file>.py｜{mod}/lib:*）∪ Protocol 名 ∪ 登録済み外部実体 のいずれでもない')
        for o in others:
            if re.search(r'(?<![A-Za-z_])%s(?![A-Za-z_])' % o, label):
                errors.append(f'(ii) {os.path.basename(path)}: レーン「{label}」に他 MOD 名 {o} が出ている（Protocol 名で描く）')

# ---------- (iii)(iv)(vi) feature_list ----------
COLS = ['fn_id', '機能名', '概要', 'source_id(P2のfn_id)', '入力', '出力', '依存する if_id(;区切り)', '永続化先', 'エラー時の振る舞い']
def check_feature_list(mod, path, contract, p2_ids, errors, covered):
    if not is_crlf(path):
        errors.append(f'(vi) {os.path.basename(path)}: CRLF でない')
    with open(path, encoding='utf-8', newline='') as f:
        header = next(csv.reader(f))
    if header != COLS:
        errors.append(f'(vi) {os.path.basename(path)}: 列が規定と違う {header}')
        return
    rows = load_csv(path)
    use_rows = {r['if_id']: r for r in contract if r['利用モジュール'] == mod}
    prov = {(r['Protocol名'], r['メソッド名']) for r in contract if r['提供モジュール'] == mod}
    seen_if = set()
    text_all = ''
    for r in rows:
        fid = r['fn_id']
        if not re.fullmatch(r'%s-F\d{2,}' % mod, fid):
            errors.append(f'(v) {fid}: fn_id が <MOD>-Fnn 形式でない')
        if not r['エラー時の振る舞い'].strip():
            errors.append(f'(vi) {fid}: エラー時の振る舞いが空')
        for tok in [t.strip() for t in r['依存する if_id(;区切り)'].split(';') if t.strip() and t.strip() != '—']:
            if tok not in use_rows:
                errors.append(f'(iii) {fid}: if_id {tok} は契約 csv の「利用 MOD＝{mod}」行にない')
            seen_if.add(tok)
        for tok in [t.strip() for t in r['source_id(P2のfn_id)'].split(';') if t.strip() and t.strip() != '—']:
            if tok not in p2_ids:
                errors.append(f'(iv) {fid}: source_id {tok} は p2 fn_id にない')
            covered.add(tok)
        text_all += r['機能名'] + ' ' + r['概要'] + ' '
    for k in use_rows:
        if k not in seen_if:
            errors.append(f'(iii) 契約 {k}（{use_rows[k]["Protocol名"]}.{use_rows[k]["メソッド名"]}）を使う fn がない')
    for (p, m) in sorted(prov):
        if f'{p}.{m}' not in text_all:
            errors.append(f'(iii) 提供 Protocol {p}.{m} を実装する fn がない（機能名か概要に「{p}.{m}」を書く）')

# ---------- (v) data_model ----------
CLASS_RE = re.compile(r'^\s*class\s+(\w+)')
NOTE_RE = re.compile(r'^\s*note for\s+(\w+)\s+"(.*)"')
NS_RE = re.compile(r'^\s*namespace\s+(\w+)\s*\{')
def check_data_model(mod, path, errors, want_types):
    classes, notes, ns_of = [], {}, {}
    cur_ns = None; depth = 0
    for raw in open(path, encoding='utf-8'):
        line = raw.split('%%')[0]
        m = NS_RE.match(line)
        if m:
            cur_ns = m.group(1); depth = 1; continue
        if cur_ns:
            depth += line.count('{') - line.count('}')
            if depth <= 0:
                cur_ns = None
        m = CLASS_RE.match(line)
        if m:
            classes.append(m.group(1)); ns_of[m.group(1)] = cur_ns
        m = NOTE_RE.match(line)
        if m:
            notes[m.group(1)] = m.group(2)
    for c in classes:
        n = notes.get(c)
        if n is None:
            errors.append(f'(v) {os.path.basename(path)}: class {c} に note for がない')
        elif not re.match(r'永続化先:\s*(%s)' % '|'.join(PERSIST_VOCAB), n):
            errors.append(f'(v) {os.path.basename(path)}: class {c} の note が「永続化先: <Obsidian|beads|GitLab|ファイル|なし>…」で始まらない: {n[:40]}')
    if mod in MODS:
        ns_name = f'common_interfaces_{mod}_py'
        for t in want_types:
            if t not in classes:
                errors.append(f'(v) {os.path.basename(path)}: 受け渡し型 {t} が定義されていない')
            elif ns_of.get(t) != ns_name:
                errors.append(f'(v) {os.path.basename(path)}: 受け渡し型 {t} は namespace {ns_name} に置く')

def check_mod(mod, contract, protocols, p2_ids, errors, covered, do_render):
    d = os.path.join(HERE, mod)
    if not os.path.isdir(d):
        errors.append(f'{mod}: フォルダがない'); return
    seqs = sorted(f for f in os.listdir(d) if re.fullmatch(r'p5_%s_sequence(_.+)?\.mmd' % mod, f))
    if not seqs:
        errors.append(f'{mod}: p5_{mod}_sequence.mmd がない')
    for f in seqs:
        check_sequence(mod, os.path.join(d, f), protocols, errors)
        if do_render: render(os.path.join(d, f), errors)
    dm = os.path.join(d, f'p5_{mod}_data_model.mmd')
    if os.path.exists(dm):
        check_data_model(mod, dm, errors, PASS_TYPES.get(mod, []))
        if do_render: render(dm, errors)
    else:
        errors.append(f'{mod}: data_model がない')
    fl = os.path.join(d, f'p5_{mod}_feature_list.csv')
    if os.path.exists(fl):
        check_feature_list(mod, fl, contract, p2_ids, errors, covered)
    else:
        errors.append(f'{mod}: feature_list がない')
    if not os.path.exists(os.path.join(d, f'p5_{mod}_notes.md')):
        errors.append(f'{mod}: notes がない')

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    do_render = '--render' in sys.argv
    contract = load_csv(CONTRACT)
    protocols = {r['Protocol名'] for r in contract}
    p2 = load_csv(P2)
    p2_ids = {r['fn_id'] for r in p2}
    errors, covered = [], set()
    targets = args or (['common'] + MODS)
    for t in targets:
        if t == 'common':
            dm = os.path.join(HERE, 'common', 'p5_common_data_model.mmd')
            if os.path.exists(dm):
                check_data_model('common', dm, errors, [])
                if do_render: render(dm, errors)
            else:
                errors.append('common: p5_common_data_model.mmd がない')
        else:
            check_mod(t, contract, protocols, p2_ids, errors, covered, do_render)
    if not args:  # フェーズ全体 (vii)
        target_fns = [r['fn_id'] for r in p2
                      if '廃止' not in r['status'] and r['優先度'] != '据え置き' and r['kind'] != 'プロンプト' and r['fn_id'] != 'FN-41']
        missing = [f for f in target_fns if f not in covered]
        if missing:
            errors.append(f'(vii) P2 被覆漏れ {len(missing)} 件: ' + ' '.join(missing))
        print(f'P2 被覆: 対象 {len(target_fns)} 件中 {len(target_fns) - len(missing)} 件')
    print('ERRORS: none' if not errors else 'ERRORS:')
    for e in errors:
        print(' -', e)
    sys.exit(1 if errors else 0)

if __name__ == '__main__':
    main()
