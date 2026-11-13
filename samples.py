from ledger import Store

INITIAL = [
    ['put', 'A', ['literal', [7, None, -2, 7]]],
    ['put', 'B', ['repeat', ['range', 'A', 1, 4], 1000000000000]],
    ['pin', 'before']
]
EDIT = [
    ['splice', 'A', 1, 2, ['range', 'A', 0, 3]],
    ['splice', 'B', 2, 3, ['range', 'A', 0, 5]]
]
EXPECTED_A = [7, 7, None, -2, 7]
EXPECTED_B_PREFIX = [None, -2, 7, 7, None, -2, 7, 7, None]

def seed(store):
    first = store.apply('load', INITIAL)
    second = store.apply('edit', EDIT)
    return first, second

def history(store, rounds=80):
    store.apply('history-start', [['put', 'H', ['literal', [0]]]])
    for i in range(rounds):
        store.apply('history-'+str(i), [['splice', 'H', i+1, 0, ['literal', [i+1]]]])

def build(path):
    store = Store(path)
    seed(store)
    store.close()

if __name__ == '__main__':
    import sys
    build(sys.argv[1])
