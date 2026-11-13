import json
import sqlite3
import sys
from ledger import Store
from samples import INITIAL, EDIT, EXPECTED_A, EXPECTED_B_PREFIX, seed, history
from vendor.page2 import Pages, encode, decode

def normal():
    s = Store(':memory:')
    first, second = seed(s)
    assert first == {'generation': 1, 'profiles': {'A': [4,3,12], 'B': [3000000000000,2000000000000,5000000000000]}}
    assert second == {'generation': 2, 'profiles': {'A': [5,4,19], 'B': [3000000000002,2000000000002,5000000000014]}}
    assert s.read('A', 0, 5) == EXPECTED_A
    assert s.read('B', 0, 9) == EXPECTED_B_PREFIX
    assert s.read('A', 0, 4, 'before') == [7,None,-2,7]
    assert s.measure('B', 0, 3000000000000, 'before') == [3000000000000,2000000000000,5000000000000]
    assert s.apply('load', INITIAL) == first
    saved = {table: s.db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in ('meta','nodes','heads','pins','receipts','commits')}
    try:
        s.apply('bad', [['splice','A',0,1,['literal',[1]]], ['splice','B',-1,0,['literal',[]]]])
    except ValueError as e:
        assert str(e) == 'BOUNDS'
    else:
        raise AssertionError('rollback missing')
    # SQLite may update physical header counters on rollback; compare logical rows.
    for table in ('meta','nodes','heads','pins','receipts','commits'):
        assert s.db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() == saved[table]
    try:
        s.apply('load', EDIT)
    except ValueError as e:
        assert str(e) == 'REQUEST_CONFLICT'
    else:
        raise AssertionError('request binding missing')
    s.apply('drop', [['drop','before'], ['delete','A']])
    assert s.measure('B',0,3000000000002) == [3000000000002,2000000000002,5000000000014]
    s.close()
    print('LEGACY_SEQUENCE_OK')

def pages():
    c = sqlite3.connect(':memory:')
    p = Pages(c)
    values = [None, -(1 << 63), 0, (1 << 63)-1]
    assert decode(encode(values)) == values
    leaf = p.leaf(values)
    root = p.branch([[leaf, 1000000000000]])
    assert p.info(root) == ['branch',4000000000000,3000000000000,-1000000000000,1]
    assert c.execute('SELECT length(payload) FROM p2_objects WHERE id=?',(root,)).fetchone()[0] == 0
    c.close()
    print('PAGE2_CODEC_OK')

def history_check():
    s = Store(':memory:')
    history(s)
    assert s.read('H',0,81) == list(range(81))
    assert s.measure('H',0,81) == [81,81,3240]
    root = s.db.execute("SELECT node FROM heads WHERE name='H'").fetchone()[0]
    depths = {}
    def depth(node):
        if node in depths:
            return depths[node]
        kind, body, _ = s._get(node)
        children = body if kind == 'concat' else body[:1] if kind in ('slice','repeat') else []
        depths[node] = max((depth(c)+1 for c in children), default=0)
        return depths[node]
    height = depth(root)
    assert height == 160
    print(json.dumps({'logical_length':81,'old_height':height,'old_nodes':s.db.execute('SELECT count(*) FROM nodes').fetchone()[0]}))
    s.close()

def verify():
    normal()
    pages()
    history_check()

def demo():
    s = Store(':memory:')
    seed(s)
    print(json.dumps({'current_A': s.read('A',0,5), 'current_B_prefix':s.read('B',0,9),
                      'pinned_A':s.read('A',0,4,'before'),
                      'B_measure':s.measure('B',0,3000000000002),
                      'physical_v1_nodes':s.db.execute('SELECT count(*) FROM nodes').fetchone()[0]}, ensure_ascii=False))
    s.close()

if __name__ == '__main__':
    {'normal':normal, 'pages':pages, 'history':history_check, 'verify':verify, 'demo':demo}[sys.argv[1]]()
