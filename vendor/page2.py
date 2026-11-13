import sqlite3
import struct

DDL = """
CREATE TABLE IF NOT EXISTS p2_objects(
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('leaf','branch')),
 payload BLOB NOT NULL, length INTEGER NOT NULL,
 present INTEGER NOT NULL, total TEXT NOT NULL, height INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS p2_edges(
 parent INTEGER NOT NULL REFERENCES p2_objects(id),
 slot INTEGER NOT NULL CHECK(slot IN (0,1)),
 child INTEGER NOT NULL REFERENCES p2_objects(id),
 copies INTEGER NOT NULL CHECK(copies>0),
 PRIMARY KEY(parent,slot));
CREATE INDEX IF NOT EXISTS p2_incoming ON p2_edges(child);
"""

def encode(values):
    if type(values) is not list or len(values) > 64:
        raise ValueError('PAGE_SIZE')
    mask = 0
    data = bytearray()
    for i, value in enumerate(values):
        if value is None:
            data.extend(b'\0'*8)
        else:
            if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
                raise ValueError('INT64')
            mask |= 1 << i
            data.extend(struct.pack('<q', value))
    return struct.pack('<BQ', len(values), mask) + data

def decode(blob):
    if len(blob) < 9:
        raise ValueError('PAGE_TRUNCATED')
    count, mask = struct.unpack('<BQ', blob[:9])
    if count > 64 or len(blob) != 9 + 8*count or mask >> count:
        raise ValueError('PAGE_INVALID')
    values = []
    for i in range(count):
        raw = blob[9+8*i:17+8*i]
        if mask >> i & 1:
            values.append(struct.unpack('<q', raw)[0])
        else:
            if raw != b'\0'*8:
                raise ValueError('NULL_PAYLOAD')
            values.append(None)
    return values

class Pages:
    def __init__(self, connection):
        self.db = connection
        self.db.executescript(DDL)

    def info(self, node):
        row = self.db.execute('SELECT kind,length,present,total,height FROM p2_objects WHERE id=?', (node,)).fetchone()
        if row is None:
            raise ValueError('P2_MISSING')
        return [row[0], row[1], row[2], int(row[3]), row[4]]

    def leaf(self, values):
        payload = encode(values)
        cur = self.db.execute("INSERT INTO p2_objects(kind,payload,length,present,total,height) VALUES('leaf',?,?,?,?,0)",
                              (payload, len(values), sum(x is not None for x in values), str(sum(x for x in values if x is not None))))
        return cur.lastrowid

    def branch(self, entries):
        if type(entries) is not list or not 1 <= len(entries) <= 2:
            raise ValueError('EDGES')
        measures = []
        for child, copies in entries:
            if type(copies) is not int or not 0 < copies < (1 << 63):
                raise ValueError('COPIES')
            info = self.info(child)
            if info[1] == 0:
                raise ValueError('EMPTY_CHILD')
            measures.append((info[1]*copies, info[2]*copies, info[3]*copies, info[4]+1))
        length = sum(m[0] for m in measures)
        if length >= (1 << 63):
            raise ValueError('LENGTH')
        cur = self.db.execute("INSERT INTO p2_objects(kind,payload,length,present,total,height) VALUES('branch',X'',?,?,?,?)",
                              (length, sum(m[1] for m in measures), str(sum(m[2] for m in measures)), max(m[3] for m in measures)))
        parent = cur.lastrowid
        for slot, (child, copies) in enumerate(entries):
            self.db.execute('INSERT INTO p2_edges VALUES(?,?,?,?)', (parent, slot, child, copies))
        return parent
