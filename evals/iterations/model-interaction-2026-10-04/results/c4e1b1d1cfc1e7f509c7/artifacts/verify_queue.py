import math
import pathlib
import tempfile
import threading
from app import Queue

with tempfile.TemporaryDirectory() as d:
    path = pathlib.Path(d) / 'q.db'
    q1, q2 = Queue(path), Queue(path)
    original = {'items': [1, {'ok': True}]}
    q1.put('fractional', original)
    original['items'][1]['ok'] = False
    first = q1.claim('w', 10, 0.25)
    assert set(first) == {'payload', 'expires_at', 'receipt'}
    assert first['expires_at'] == 10.25
    first['payload']['items'].clear()
    assert q2.claim('w', 10.249, 1) is None
    assert q2.ack(first['receipt'], 10.25) is False
    replacement = q2.claim('w', 10.25, 0.5)
    assert replacement['payload'] == {'items': [1, {'ok': True}]}
    assert q1.ack(first['receipt'], 10.25) is False
    assert q1.ack(replacement['receipt'], 10.75) is False
    assert q2.claim('w', 10.75, 1) is not None

    q1.put('done', None)
    c = q2.claim('worker', 20, 1)
    assert q1.ack(c['receipt'], 20.5) is True
    assert q2.ack(c['receipt'], 20.5) is False
    try:
        q1.put('done', 'duplicate')
    except ValueError:
        pass
    else:
        raise AssertionError('completed ID was reusable')

    clean = Queue(pathlib.Path(d) / 'clean.db')
    for payload, error in [(float('nan'), ValueError), (object(), TypeError)]:
        try:
            clean.put('invalid', payload)
        except error:
            pass
        else:
            raise AssertionError('invalid payload accepted')
        assert clean.claim('w', 30, 1) is None

    for duration in [0, -1, float('inf'), float('nan'), True]:
        try:
            q1.claim('w', 30, duration)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid lease accepted')

    race1 = Queue(pathlib.Path(d) / 'race.db')
    race2 = Queue(pathlib.Path(d) / 'race.db')
    race1.put('race', {'n': 1})
    barrier = threading.Barrier(3)
    outcomes = []
    def run(queue, name):
        barrier.wait()
        outcomes.append(queue.claim(name, 40, 5))
    threads = [threading.Thread(target=run, args=(race1, 'a')),
               threading.Thread(target=run, args=(race2, 'b'))]
    for t in threads: t.start()
    barrier.wait()
    for t in threads: t.join()
    assert sum(result is not None for result in outcomes) == 1
print('queue verification passed')
