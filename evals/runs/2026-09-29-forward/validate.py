from collections import defaultdict
import platform
from unittest.mock import Mock


class SessionsOriginal:
    def __init__(self):
        self.rows = defaultdict(dict)

    def lookup(self, session, key):
        return self.rows[session].get(key)


class SessionsFixed(SessionsOriginal):
    def lookup(self, session, key):
        return self.rows.get(session, {}).get(key)


class Cache:
    def __init__(self, loader):
        self.rows = {}
        self.loader = loader

    def get(self, key):
        if key not in self.rows:
            self.rows[key] = self.loader(key)
        return self.rows[key]


def snapshot(rows):
    return {session: dict(values) for session, values in rows.items()}


def main():
    print('Python:', platform.python_version())
    original = SessionsOriginal()
    before = snapshot(original.rows)
    result = original.lookup('absent', 'answer')
    after = snapshot(original.rows)
    assert result is None
    assert before == {} and after == {'absent': {}}
    print(f'A original: result={result!r}, before={before!r}, after={after!r}')
    print('CONFIRMED: missing lookup creates a session and violates the contract')

    fixed = SessionsFixed()
    before = snapshot(fixed.rows)
    assert fixed.lookup('absent', 'answer') is None
    assert fixed.lookup('absent', 'answer') is None
    assert snapshot(fixed.rows) == before
    print('PASS A fixed: repeated missing-session probes return None without mutation')

    for cls in (SessionsOriginal, SessionsFixed):
        sessions = cls()
        saved = {'text': 'existing answer'}
        sessions.rows['existing']['answer'] = saved
        before = snapshot(sessions.rows)
        assert sessions.lookup('existing', 'answer') is saved
        assert sessions.lookup('existing', 'missing') is None
        assert snapshot(sessions.rows) == before
    print('PASS A compatibility: existing answers remain readable; missing keys preserve rows')

    # The wrapped computation is pure; Mock observes calls without changing its result.
    loader = Mock(wraps=lambda key: None if key == 'none' else key.upper())
    cache = Cache(loader)
    assert cache.get('a') == 'A'
    assert cache.rows == {'a': 'A'}
    assert loader.call_count == 1
    assert cache.get('a') == 'A'
    assert loader.call_count == 1
    assert cache.get('b') == 'B'
    assert loader.call_count == 2
    assert cache.get('a') == 'A'
    assert loader.call_count == 2
    assert cache.get('none') is None
    assert cache.get('none') is None
    assert loader.call_count == 3
    assert cache.rows == {'a': 'A', 'b': 'B', 'none': None}
    print(f'PASS B: first queries populate cache; repeats reuse results; rows={cache.rows!r}')
    print('B loader calls:', loader.call_args_list)
    print('ALL CHECKS PASSED (including expected original A contract violation)')


if __name__ == '__main__':
    main()
