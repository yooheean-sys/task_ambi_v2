# 第6フェーズ成果物の自己検証（D12-g の完了条件 (ii)〜(vii)）。outcome/design/phase6/ で
#   python3 p6_selfcheck.py            # (ii)〜(vii)
#   python3 p6_selfcheck.py --render   # 併せて mmdc で配置図を描画（(i)）
# 設計成果物の検査専用であり製品コードではない。uv run ではなく python3 直呼びで動く（PyYAML が無ければ最小パーサで代替）。
import csv, glob, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, 'p6_external_interface.csv')
MMD = os.path.join(HERE, 'p6_deployment.mmd')
YAML = os.path.join(HERE, 'openapi.yaml')
NOTES = os.path.join(HERE, 'p6_external_interface_notes.md')
CONTRACT = os.path.join(HERE, '..', 'phase4', 'p4_module_contract.csv')
OWNERSHIP = os.path.join(HERE, '..', 'phase3', 'p3_bc_ownership.csv')
P5 = os.path.join(HERE, '..', 'phase5')
MMDC = shutil.which('mmdc') or 'mmdc'

MODS = ['board', 'vault', 'settings', 'monitor', 'stages', 'executor', 'orchestrator', 'trigger']
MOD_VOCAB = set(MODS) | {'apps/api', 'apps/ui'}
HEADER = ['ext_if_id', '相手(EXT-*)', '方向', 'プロトコル', '利用するモジュール(MOD)', '認証方式', '失敗時の扱い', '乗り換え対象か(Y/N)', '対応するProtocol名']
# p5 シーケンスの外部実体レーン → EXT（notes §2 の写像。'internal' は app プロセス内）
LANE_TO_EXT = {
    'bd CLI': 'EXT-01', 'beads': 'EXT-01', 'Obsidian vault': 'EXT-02', 'vault FS': 'EXT-02', 'LLM API': 'EXT-03',
    'DL フォルダ': 'EXT-04', 'ファイル投下': 'EXT-04', '設定ファイル領域 A': 'EXT-05', '設定ファイル領域 B': 'EXT-06',
    '実行履歴 csv': 'EXT-07', '.env': 'EXT-08', 'config.json': 'EXT-09', '自分': 'EXT-11', '人間': 'EXT-11',
    'タイマ': 'internal', 'apps/api': 'internal', 'composition root': 'internal', 'apps/ui': 'EXT-10', 'beads-ui': 'internal',
    'OS': 'internal', 'systemd': 'internal',
}
STORE_TO_EXT = {'STORE-01': 'EXT-05', 'STORE-02': 'EXT-06', 'STORE-03': 'EXT-01', 'STORE-04': 'EXT-02', 'STORE-05': 'EXT-07', 'STORE-06': 'EXT-04'}
# 配置図に EXT が描かれていることを確かめる語（notes §5）
DEPLOY_KEYWORDS = {
    'EXT-01': '.beads', 'EXT-02': 'Obsidian vault', 'EXT-03': 'LLM API', 'EXT-04': 'DL フォルダ', 'EXT-05': '領域 A',
    'EXT-06': 'triggers.yaml', 'EXT-07': 'history/', 'EXT-08': '.env', 'EXT-09': 'config.json', 'EXT-10': 'apps/ui',
    'EXT-11': '自分', 'EXT-12': 'PowerShell', 'EXT-13': 'journald',
}

def load_csv(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))

def is_crlf(path):
    data = open(path, 'rb').read()
    return b'\r\n' in data and data.count(b'\n') == data.count(b'\r\n')

def render(errors):
    if not os.path.exists(MMDC):
        errors.append(f'(i) mmdc が見つからない: {MMDC}'); return
    out = os.path.join(tempfile.mkdtemp(prefix='p6chk_'), 'x.svg')
    r = subprocess.run([MMDC, '-i', MMD, '-o', out], capture_output=True, text=True)
    if r.returncode != 0:
        errors.append(f'(i) mmdc 失敗: {r.stderr.strip()[:300]}')

# ---------- (ii) csv の形 ----------
def check_csv_shape(rows, errors):
    with open(CSV, encoding='utf-8', newline='') as f:
        hdr = next(csv.reader(f))
    if hdr != HEADER:
        errors.append(f'(ii) 列が規定と違う: {hdr}')
    if not is_crlf(CSV):
        errors.append('(ii) csv が CRLF でない')
    for r in rows:
        for k, v in r.items():
            if not (v or '').strip():
                errors.append(f'(ii) {r["ext_if_id"]} の「{k}」が空')
    ids = [r['ext_if_id'] for r in rows]
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r'EIF-\d\d', i) for i in ids):
        errors.append(f'(ii) ext_if_id が EIF-nn でないか重複: {ids}')

# ---------- (iii) EXT 被覆 ----------
PART_RE = re.compile(r'^\s*(participant|actor)\s+(\w+)(?:\s+as\s+(.+?))?\s*$')
def external_lanes(protocols):
    lanes = set()
    for path in glob.glob(os.path.join(P5, '*', 'p5_*sequence*.mmd')):
        mod = os.path.basename(os.path.dirname(path))
        for raw in open(path, encoding='utf-8'):
            m = PART_RE.match(raw.split('%%')[0].rstrip('\n'))
            if not m:
                continue
            label = m.group(3) or m.group(2)
            head = label.split('<br/>')[0].strip()
            if head.startswith(mod + '/'):
                continue
            if all(p.strip() in protocols for p in re.split(r'／|<br/>', label) if p.strip()):
                continue
            lanes.add(head)
    return lanes

def check_ext_coverage(rows, protocols, errors):
    ext_in_csv = set()
    for r in rows:
        m = re.match(r'(EXT-\d\d)\s', r['相手(EXT-*)'])
        if not m:
            errors.append(f'(iii) {r["ext_if_id"]} の相手が EXT-nn で始まらない'); continue
        ext_in_csv.add(m.group(1))
    notes = open(NOTES, encoding='utf-8').read()
    ext_in_notes = set(re.findall(r'^\| (EXT-\d\d) \|', notes, re.M))
    for e in sorted(ext_in_csv - ext_in_notes):
        errors.append(f'(iii) csv の {e} が notes §1 の EXT 表に無い')
    for e in sorted(ext_in_notes - ext_in_csv):
        errors.append(f'(iii) notes §1 の {e} が csv に現れない')
    for lane in sorted(external_lanes(protocols)):
        if lane not in LANE_TO_EXT:
            errors.append(f'(iii) p5 の外部レーン「{lane}」が LANE_TO_EXT に未登録（notes §2 に写像を書いて本表に足す）'); continue
        tgt = LANE_TO_EXT[lane]
        if tgt != 'internal' and tgt not in ext_in_csv:
            errors.append(f'(iii) p5 の外部レーン「{lane}」→{tgt} が csv に無い')
    for r in load_csv(OWNERSHIP):
        if r['種別'] == 'STORE':
            tgt = STORE_TO_EXT.get(r['id'])
            if not tgt:
                errors.append(f'(iii) p3 {r["id"]} の EXT 写像が未登録')
            elif tgt not in ext_in_csv:
                errors.append(f'(iii) p3 {r["id"]}→{tgt} が csv に無い')
    return ext_in_csv

# ---------- (iv) Y 行の Protocol ----------
def check_switch_rows(rows, protocols, errors):
    for r in rows:
        yn = r['乗り換え対象か(Y/N)'].strip()
        if yn not in ('Y', 'N'):
            errors.append(f'(iv) {r["ext_if_id"]} の乗り換え対象が Y/N でない: {yn}')
        if yn == 'Y':
            col = r['対応するProtocol名']
            found = [p for p in protocols if re.search(r'\b' + re.escape(p) + r'\b', col)]
            if not found:
                errors.append(f'(iv) {r["ext_if_id"]} は Y だが 14 Protocol のいずれも Protocol 列に無い: {col[:60]}')

# ---------- (v) MOD 語彙 ----------
def check_mod_vocab(rows, errors):
    for r in rows:
        for m in r['利用するモジュール(MOD)'].split(';'):
            if m.strip() not in MOD_VOCAB:
                errors.append(f'(v) {r["ext_if_id"]} の MOD「{m}」が語彙外（8 MOD ∪ apps/api）')

# ---------- (vi) openapi ----------
def load_openapi():
    try:
        import yaml
        return yaml.safe_load(open(YAML, encoding='utf-8')), 'pyyaml'
    except ImportError:
        pass
    # 最小パーサ: paths: 配下の 2 字下げキーと、その下の 4 字下げの method と responses の有無
    d = {'paths': {}}
    cur = None; method = None; in_paths = False
    for raw in open(YAML, encoding='utf-8'):
        line = raw.rstrip('\n')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if re.match(r'^\S', line):
            in_paths = line.startswith('paths:'); cur = None; continue
        if not in_paths:
            continue
        m = re.match(r'^  (/\S*):\s*$', line)
        if m:
            cur = m.group(1); d['paths'][cur] = {}; continue
        m = re.match(r'^    (get|post|put|patch|delete):\s*$', line)
        if m and cur:
            method = m.group(1); d['paths'][cur][method] = {}; continue
        if cur and method and re.match(r'^      responses:', line):
            d['paths'][cur][method]['responses'] = True
    return d, 'minimal'

PATH_RE = re.compile(r'(?:GET|POST|PUT|PATCH|DELETE|／|^|\s|・)(/[a-z][a-z\-]*(?:/(?:\{[a-z_]+\}|[a-z][a-z\-]*))*)')
def norm(p):
    return re.sub(r'\{[^}]*\}', '{}', p)

def router_paths_from_p5():
    found = set()
    for mod in MODS:
        for r in load_csv(os.path.join(P5, mod, f'p5_{mod}_feature_list.csv')):
            text = r['機能名'] + ' ' + r['概要']
            if 'router' in text or 'REST' in text or 'HTTP' in r['入力']:
                for m in PATH_RE.finditer(text):
                    p = m.group(1)
                    if p.startswith('/settings') or p.startswith('/kickoffs') or p.startswith('/triggers') or p in ('/history', '/watcher', '/hooks/file'):
                        found.add(norm(p))
    return found

def check_openapi(errors):
    try:
        d, how = load_openapi()
    except Exception as e:  # noqa
        errors.append(f'(vi) openapi.yaml が parse できない: {e}'); return
    paths = d.get('paths') or {}
    if not paths:
        errors.append('(vi) openapi.yaml に paths が無い'); return
    for p, ops in paths.items():
        methods = [m for m in ops if m in ('get', 'post', 'put', 'patch', 'delete')]
        if not methods:
            errors.append(f'(vi) {p} に operation が無い')
        for m in methods:
            if not (isinstance(ops[m], dict) and ops[m].get('responses')):
                errors.append(f'(vi) {m.upper()} {p} に responses が無い')
            if how == 'pyyaml' and not ops[m].get('x-mod'):
                errors.append(f'(vi) {m.upper()} {p} に x-mod が無い')
    normed = {norm(p) for p in paths}
    if '/health' not in normed:
        errors.append('(vi) /health が無い（憲章 §6・DIFF-27）')
    for rp in sorted(router_paths_from_p5()):
        if not any(n == rp or n.startswith(rp + '/') for n in normed):
            errors.append(f'(vi) p5 の router 行のパス {rp} が openapi の paths に（接頭辞としても）無い')
    return how

# ---------- (vii) 配置図 ----------
def check_deployment(ext_in_csv, errors):
    text = open(MMD, encoding='utf-8').read()
    for mod in MODS:
        if not re.search(r'\["' + mod + r'(<br/>|"| )', text):
            errors.append(f'(vii) 配置図に MOD ノード {mod} が無い')
    for kw in ('apps/api', 'apps/ui', 'packages/common'):
        if kw not in text:
            errors.append(f'(vii) 配置図に {kw} が無い')
    for e in sorted(ext_in_csv):
        kw = DEPLOY_KEYWORDS.get(e)
        if not kw:
            errors.append(f'(vii) {e} の配置図キーワードが未登録')
        elif kw not in text:
            errors.append(f'(vii) 配置図に {e}（{kw}）が無い')
    if not re.search(r'^flowchart\s+(TB|LR)', text, re.M):
        errors.append('(vii) 配置図が flowchart でない')

def main(argv):
    errors = []
    protocols = sorted({r['Protocol名'] for r in load_csv(CONTRACT)})
    rows = load_csv(CSV)
    check_csv_shape(rows, errors)
    ext_in_csv = check_ext_coverage(rows, protocols, errors)
    check_switch_rows(rows, protocols, errors)
    check_mod_vocab(rows, errors)
    how = check_openapi(errors)
    check_deployment(ext_in_csv, errors)
    if '--render' in argv:
        render(errors)
    print(f'csv rows={len(rows)} EXT={len(ext_in_csv)} protocols={len(protocols)} openapi parser={how}')
    print('ERRORS:', 'none' if not errors else len(errors))
    for e in errors:
        print(' -', e)
    return 1 if errors else 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
