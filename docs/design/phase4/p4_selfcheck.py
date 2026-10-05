# 第4フェーズ成果物の自己検証（D09-h の完了条件 (ii)〜(iv)）。outcome/design/phase4/ で `python3 p4_selfcheck.py` を実行する。設計成果物の検査専用であり製品コードではない。
import re, csv, sys, tomllib
mmd = open('p4_module_dependency.mmd', encoding='utf-8').read()
FEATURES = {'trigger','orchestrator','stages','executor','settings','board','vault','monitor'}
APPS = {'API','UI'}
EXT = {'BEADS','VAULTFS','CFGA','CFGB','LLM'}
IFACE = {'IF_ORCH','IF_STAGES','IF_EXEC','IF_SETTINGS','IF_BOARD','IF_VAULT','IF_MONITOR'}
SUB = {'FEATURES','APPS','COMMON','IFACES'}
edges = []
for line in mmd.splitlines():
    s = line.strip()
    if s.startswith('%%') or not s: continue
    m = re.match(r'^(\w+)\s*(==>|-\.->|-->)\s*(?:\|[^|]*\|)?\s*(\w+)\s*$', s)
    if m: edges.append((m.group(1), m.group(2), m.group(3)))
print('edges', len(edges))
err = []
# (ii) no feature->feature
ff = [e for e in edges if e[0] in FEATURES and e[2] in FEATURES]
print('feature->feature edges:', len(ff), ff)
if ff: err.append('feature->feature')
# feature outgoing edges: only to IFACE / KERNEL / EXT
for a,k,b in edges:
    if a in FEATURES and b not in IFACE|{'KERNEL'}|EXT:
        err.append(f'bad feature edge {a}->{b}')
    if a in FEATURES and k=='==>' and b not in IFACE:
        err.append(f'implements to non-iface {a}->{b}')
# each IFACE has exactly one implements
for i in IFACE:
    n = [e for e in edges if e[1]=='==>' and e[2]==i]
    if len(n)!=1: err.append(f'{i} implements count {len(n)}')
# apps edges only to FEATURES / API / subgraph
for a,k,b in edges:
    if a in APPS and b not in FEATURES|APPS|SUB: err.append(f'bad app edge {a}->{b}')
# ext nodes never source
for a,k,b in edges:
    if a in EXT: err.append(f'ext as source {a}')
# csv checks
rows = list(csv.DictReader(open('p4_module_contract.csv', encoding='utf-8')))
print('csv rows', len(rows))
IMPL = {'board':'IF_BOARD','settings':'IF_SETTINGS','vault':'IF_VAULT','monitor':'IF_MONITOR','stages':'IF_STAGES','executor':'IF_EXEC','orchestrator':'IF_ORCH'}
# labels of iface nodes
labels = dict(re.findall(r'^\s*(IF_\w+)\["([^"]*)"\]', mmd, re.M))
protos_in_mmd = {}
for k,v in labels.items():
    protos_in_mmd[k] = set(re.findall(r'[A-Z][A-Za-z]+', v.replace('<br/>',' ')))
users_by_iface = {}
for a,k,b in edges:
    if k=='-.->' and a in FEATURES and b in IFACE: users_by_iface.setdefault(b,set()).add(a)
for r in rows:
    p,u,pn = r['提供モジュール'], r['利用モジュール'], r['Protocol名']
    if p not in FEATURES or u not in FEATURES: err.append(f"csv mod {p}/{u}")
    if p==u: err.append(f'self use {r["if_id"]}')
    node = IMPL[p]
    if pn not in protos_in_mmd[node]: err.append(f'{r["if_id"]} protocol {pn} not in {node} label')
    if u not in users_by_iface.get(node,set()): err.append(f'{r["if_id"]} user {u} has no uses-edge to {node}')
# every uses-edge has at least one csv row
for i,us in users_by_iface.items():
    for u in us:
        if not any(IMPL[r['提供モジュール']]==i and r['利用モジュール']==u for r in rows):
            err.append(f'uses edge {u}->{i} without csv row')
# REL coverage
need = {'REL-06-03','REL-03-01','REL-03-02','REL-03-04','REL-03-05','REL-05-02','REL-01-02','REL-01-04','REL-03-03a','REL-03-03b'}
have = set()
for r in rows: have |= set(re.findall(r'REL-[0-9a-z-]+', r['source_id(rel_id)']))
print('REL missing:', need-have)
if need-have: err.append('REL missing')
# 同期/非同期 filled
for r in rows:
    if not r['同期/非同期'].startswith(('同期','非同期')): err.append(f'{r["if_id"]} sync col')
# linter
t = tomllib.load(open('p4_importlinter.toml','rb'))
rp = set(t['tool']['importlinter']['root_packages'])
if not FEATURES <= rp: err.append(f'linter root_packages missing {FEATURES-rp}')
for c in t['tool']['importlinter']['contracts']:
    if c['type']=='independence' and set(c['modules'])!=FEATURES: err.append('independence modules != FEATURES')
    if c['type']=='layers':
        # feature 層 = 先頭行と末尾行を除く行（apps/api/tests/foundation の REG-4 と同じ読み方）。
        # 016（D23）で足した 2 段の ui-over-api（ui / api）には中間行が無いので対象外になる。
        # 旧実装は layers[1] を無条件に中間層とみなしており、2 段契約では最下層を見て誤検知した
        # （016 design-sync B-20）。
        middle = c['layers'][1:-1]
        if middle:
            mid = set(x.strip() for row in middle for x in row.split('|'))
            if mid!=FEATURES: err.append('layers middle != FEATURES')
print('protocol sets:', {k:sorted(v) for k,v in protos_in_mmd.items()})
print('ERRORS:', err if err else 'none')
sys.exit(1 if err else 0)
