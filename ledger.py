import json
import sqlite3

MAXLEN = (1 << 63) - 1

def pack(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def integer(x):
    return type(x) is int

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(single INTEGER PRIMARY KEY CHECK(single=1),format INTEGER NOT NULL,generation INTEGER NOT NULL);
        INSERT OR IGNORE INTO meta VALUES(1,1,0);
        CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,body TEXT NOT NULL,n INTEGER NOT NULL,present INTEGER NOT NULL,total TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS heads(name TEXT PRIMARY KEY,node INTEGER NOT NULL REFERENCES nodes(id));
        CREATE TABLE IF NOT EXISTS pins(sid TEXT PRIMARY KEY,roots TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS receipts(rid TEXT PRIMARY KEY,request TEXT NOT NULL,reply TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS commits(generation INTEGER PRIMARY KEY,changes TEXT NOT NULL,rid TEXT NOT NULL,request TEXT NOT NULL,reply TEXT NOT NULL);
        """)
        for table in ('nodes', 'heads', 'pins', 'receipts', 'commits'):
            for event in ('INSERT', 'UPDATE', 'DELETE'):
                self.db.execute("CREATE TRIGGER IF NOT EXISTS fence_%s_%s BEFORE %s ON %s WHEN (SELECT format FROM meta WHERE single=1)<>1 BEGIN SELECT RAISE(ABORT,'LEGACY_FENCED'); END" % (table, event, event, table))
        self.db.execute("CREATE TRIGGER IF NOT EXISTS fence_generation BEFORE UPDATE OF generation ON meta WHEN OLD.format<>1 BEGIN SELECT RAISE(ABORT,'LEGACY_FENCED'); END")

    def close(self):
        self.db.close()

    def _node(self, kind, body, measure):
        n, present, total = measure
        if n > MAXLEN:
            raise ValueError('LENGTH')
        cur = self.db.execute('INSERT INTO nodes(kind,body,n,present,total) VALUES(?,?,?,?,?)', (kind, pack(body), n, present, str(total)))
        return cur.lastrowid

    def _get(self, node):
        row = self.db.execute('SELECT kind,body,n,present,total FROM nodes WHERE id=?', (node,)).fetchone()
        if row is None:
            raise ValueError('MISSING_NODE')
        return row[0], json.loads(row[1]), (row[2], row[3], int(row[4]))

    def _measure(self, node, start, stop):
        kind, body, m = self._get(node)
        if start == stop:
            return (0, 0, 0)
        if start == 0 and stop == m[0]:
            return m
        if kind == 'literal':
            vals = body[start:stop]
            return (len(vals), sum(v is not None for v in vals), sum(v for v in vals if v is not None))
        if kind == 'slice':
            return self._measure(body[0], body[1] + start, body[1] + stop)
        if kind == 'concat':
            parts = []
            pos = 0
            for child in body:
                size = self._get(child)[2][0]
                a, b = max(start, pos), min(stop, pos + size)
                if a < b:
                    parts.append(self._measure(child, a-pos, b-pos))
                pos += size
            return tuple(sum(p[i] for p in parts) for i in range(3))
        child, times = body
        size = self._get(child)[2][0]
        q1, r1 = divmod(start, size)
        q2, r2 = divmod(stop, size)
        if q1 == q2:
            return self._measure(child, r1, r2)
        parts = [self._measure(child, r1, size), self._measure(child, 0, r2)]
        whole = self._get(child)[2]
        middle = q2-q1-1
        return tuple(parts[0][i]+parts[1][i]+middle*whole[i] for i in range(3))

    def _slice(self, node, start, stop):
        return self._node('slice', [node, start, stop], self._measure(node, start, stop))

    def _root(self, name, roots=None):
        if type(name) is not str or not name:
            raise ValueError('NAME')
        if roots is not None:
            if name not in roots:
                raise ValueError('UNKNOWN_PROFILE')
            return roots[name]
        row = self.db.execute('SELECT node FROM heads WHERE name=?', (name,)).fetchone()
        if row is None:
            raise ValueError('UNKNOWN_PROFILE')
        return row[0]

    def _bounds(self, node, start, stop):
        n = self._get(node)[2][0]
        if not integer(start) or not integer(stop) or not 0 <= start <= stop <= n:
            raise ValueError('BOUNDS')

    def _source(self, expr):
        if type(expr) is not list or not expr:
            raise ValueError('SOURCE')
        if expr[0] == 'literal' and len(expr) == 2:
            vals = expr[1]
            if type(vals) is not list or len(vals) > 64 or any(v is not None and (not integer(v) or not -(1 << 63) <= v < (1 << 63)) for v in vals):
                raise ValueError('VALUES')
            return self._node('literal', vals, (len(vals), sum(v is not None for v in vals), sum(v for v in vals if v is not None)))
        if expr[0] == 'range' and len(expr) == 4:
            node = self._root(expr[1])
            self._bounds(node, expr[2], expr[3])
            return self._slice(node, expr[2], expr[3])
        if expr[0] == 'repeat' and len(expr) == 3 and integer(expr[2]) and expr[2] >= 0:
            child = self._source(expr[1])
            m = self._get(child)[2]
            if not m[0] or not expr[2]:
                return self._node('literal', [], (0, 0, 0))
            return self._node('repeat', [child, expr[2]], tuple(x*expr[2] for x in m))
        raise ValueError('SOURCE')

    def apply(self, rid, ops):
        if type(rid) is not str or not rid or type(ops) is not list:
            raise ValueError('REQUEST')
        request = pack(ops)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.db.execute('SELECT request,reply FROM receipts WHERE rid=?', (rid,)).fetchone()
            if old:
                if old[0] != request:
                    raise ValueError('REQUEST_CONFLICT')
                self.db.execute('COMMIT')
                return json.loads(old[1])
            fmt, gen = self.db.execute('SELECT format,generation FROM meta WHERE single=1').fetchone()
            if fmt != 1:
                raise ValueError('LEGACY_FENCED')
            changes = []
            for op in ops:
                if type(op) is not list or not op:
                    raise ValueError('OP')
                if op[0] == 'put' and len(op) == 3:
                    name = op[1]
                    if type(name) is not str or not name:
                        raise ValueError('NAME')
                    root = self._source(op[2])
                    self.db.execute('INSERT OR REPLACE INTO heads VALUES(?,?)', (name, root))
                    changes.append(['head', name, root])
                elif op[0] == 'splice' and len(op) == 5:
                    name, at, delete, expr = op[1:]
                    root = self._root(name)
                    if not integer(at) or not integer(delete) or delete < 0:
                        raise ValueError('BOUNDS')
                    self._bounds(root, at, at+delete)
                    inserted = self._source(expr)
                    n = self._get(root)[2][0]
                    children = [self._slice(root, 0, at), inserted, self._slice(root, at+delete, n)]
                    ms = [self._get(c)[2] for c in children]
                    new = self._node('concat', children, tuple(sum(m[i] for m in ms) for i in range(3)))
                    self.db.execute('UPDATE heads SET node=? WHERE name=?', (new, name))
                    changes.append(['head', name, new])
                elif op[0] == 'delete' and len(op) == 2:
                    self._root(op[1])
                    self.db.execute('DELETE FROM heads WHERE name=?', (op[1],))
                    changes.append(['head', op[1], None])
                elif op[0] == 'pin' and len(op) == 2:
                    sid = op[1]
                    if type(sid) is not str or not sid:
                        raise ValueError('SNAPSHOT')
                    roots = dict(self.db.execute('SELECT name,node FROM heads ORDER BY name'))
                    if self.db.execute('SELECT 1 FROM pins WHERE sid=?', (sid,)).fetchone():
                        raise ValueError('SNAPSHOT_EXISTS')
                    self.db.execute('INSERT INTO pins VALUES(?,?)', (sid, pack(roots)))
                    changes.append(['pin', sid, roots])
                elif op[0] == 'drop' and len(op) == 2:
                    if type(op[1]) is not str or not self.db.execute('DELETE FROM pins WHERE sid=?', (op[1],)).rowcount:
                        raise ValueError('UNKNOWN_SNAPSHOT')
                    changes.append(['pin', op[1], None])
                else:
                    raise ValueError('OP')
            reply = {'generation': gen+1, 'profiles': {name: list(self._get(node)[2]) for name, node in self.db.execute('SELECT name,node FROM heads ORDER BY name')}}
            encoded = pack(reply)
            self.db.execute('INSERT INTO receipts VALUES(?,?,?)', (rid, request, encoded))
            self.db.execute('INSERT INTO commits VALUES(?,?,?,?,?)', (gen+1, pack(changes), rid, request, encoded))
            self.db.execute('UPDATE meta SET generation=? WHERE single=1', (gen+1,))
            self.db.execute('COMMIT')
            return reply
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def _view(self, name, snapshot):
        roots = None
        if snapshot is not None:
            row = self.db.execute('SELECT roots FROM pins WHERE sid=?', (snapshot,)).fetchone()
            if row is None:
                raise ValueError('UNKNOWN_SNAPSHOT')
            roots = json.loads(row[0])
        return self._root(name, roots)

    def measure(self, name, start, stop, snapshot=None):
        self.db.execute('BEGIN')
        try:
            node = self._view(name, snapshot)
            self._bounds(node, start, stop)
            return list(self._measure(node, start, stop))
        finally:
            self.db.execute('ROLLBACK')

    def read(self, name, start, stop, snapshot=None):
        self.db.execute('BEGIN')
        try:
            node = self._view(name, snapshot)
            self._bounds(node, start, stop)
            if stop-start > 4096:
                raise ValueError('READ_LIMIT')
            return [self._measure(node, i, i+1)[2] if self._measure(node, i, i+1)[1] else None for i in range(start, stop)]
        finally:
            self.db.execute('ROLLBACK')
