"""0.2.3: order-book frames are coalesced per market so the WS event queue cannot fill with stale frames."""
import queue
import sys
import threading
import time
import types
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'app/src/main/python'))
import ws_book_coalesce_v0230 as wc


class FakeGate:
    ready = True
    def snapshot(self):
        return {'ready': self.ready}


class FakeRunner:
    """Mimics the vendor runner surface the overlay touches."""
    def __init__(self, maxsize=4096):
        self._ws_event_queue = queue.Queue(maxsize=maxsize)
        self.preload_gate = FakeGate()
        self.dispatched = []
        self.other = []
        self.issues = []
        self.vendor_dropped = 0
    def _enqueue_ws_event(self, kind, payload=None):
        try:
            self._ws_event_queue.put_nowait((kind, payload))
        except queue.Full:
            self.vendor_dropped += 1
    def _dispatch_public_book(self, book):
        self.dispatched.append(book)
    def _safe_ws_issue(self, kind, detail, severity='WARN'):
        self.issues.append((kind, detail))
    def drain(self):
        while True:
            try:
                kind, payload = self._ws_event_queue.get_nowait()
            except queue.Empty:
                return
            if kind == 'book':
                self._dispatch_public_book(payload)
            else:
                self.other.append((kind, payload))


def _module():
    mod = types.SimpleNamespace(UpbitPaperRunner=type('R', (FakeRunner,), {}))
    wc._INSTALLED = False
    wc.install(mod)
    wc._INSTALLED = False
    return mod.UpbitPaperRunner


def _book(market, n):
    return {'market': market, 'bid': str(n), 'timestamp': 1000 + n}


class Coalesce(unittest.TestCase):
    def test_flood_is_bounded_and_the_newest_frame_wins(self):
        r = _module()()
        for n in range(20000):                        # far more than the 4,096 queue slots
            r._enqueue_ws_event('book', _book('KRW-A', n))
            r._enqueue_ws_event('book', _book('KRW-B', n))
        self.assertEqual(r.vendor_dropped, 0)         # the queue never filled
        self.assertLessEqual(r._ws_event_queue.qsize(), 2)   # one token per market
        r.drain()
        self.assertEqual([b['bid'] for b in r.dispatched], ['19999', '19999'])
        s = wc.stats(r)
        self.assertEqual(s['received'], 40000)
        self.assertEqual(s['dispatched'], 2)
        self.assertEqual(s['coalesced'], 39998)

    def test_after_dispatch_the_next_frame_is_queued_again(self):
        r = _module()()
        r._enqueue_ws_event('book', _book('KRW-A', 1)); r.drain()
        r._enqueue_ws_event('book', _book('KRW-A', 2)); r.drain()
        self.assertEqual([b['bid'] for b in r.dispatched], ['1', '2'])

    def test_marks_and_other_kinds_pass_through(self):
        r = _module()()
        r._enqueue_ws_event('mark', ('KRW-A', 10, 'now'))
        r.drain()
        self.assertEqual(r.other, [('mark', ('KRW-A', 10, 'now'))])
        self.assertEqual(r.dispatched, [])

    def test_nothing_is_queued_before_preload_is_ready(self):
        r = _module()()
        r.preload_gate.ready = False
        self.assertFalse(r._enqueue_ws_event('book', _book('KRW-A', 1)))
        self.assertEqual(r._ws_event_queue.qsize(), 0)

    def test_full_queue_does_not_block_the_market_forever(self):
        r = _module()(maxsize=1)
        r._ws_event_queue.put_nowait(('mark', 1))     # queue is full
        self.assertFalse(r._enqueue_ws_event('book', _book('KRW-A', 1)))
        r.drain()
        r._enqueue_ws_event('book', _book('KRW-A', 2))   # must work again: pending flag was released
        r.drain()
        self.assertEqual([b['bid'] for b in r.dispatched], ['2'])
        self.assertEqual(wc.stats(r)['queue_full'], 1)

    def test_direct_real_book_dispatch_is_untouched(self):
        r = _module()()
        real = _book('KRW-A', 5)
        r._dispatch_public_book(real)
        self.assertEqual(r.dispatched, [real])

    def test_orphan_token_is_ignored(self):
        r = _module()()
        r._dispatch_public_book({wc.TOKEN_KEY: True, 'market': 'KRW-Z'})
        self.assertEqual(r.dispatched, [])

    def test_first_backlog_report_does_not_depend_on_monotonic_clock_origin(self):
        # A freshly booted runner/phone has a tiny time.monotonic(); the first report must still be written.
        real = wc.time.monotonic
        try:
            wc.time.monotonic = lambda: 5.0
            r = _module()()
            r._enqueue_ws_event('book', _book('KRW-A', 1))
            book, _ = wc._state(r)['latest']['KRW-A']
            wc._state(r)['latest']['KRW-A'] = (book, 5.0 - 10)
            r.drain()
            self.assertEqual(len(r.issues), 1)
        finally:
            wc.time.monotonic = real

    def test_backlog_is_reported_once_per_interval(self):
        r = _module()()
        for n in (1, 2):
            r._enqueue_ws_event('book', _book('KRW-A', n))
            book, _ = wc._state(r)['latest']['KRW-A']
            wc._state(r)['latest']['KRW-A'] = (book, time.monotonic() - 10)   # waited 10 s
            r.drain()
        self.assertEqual(len(r.issues), 1)
        self.assertEqual(r.issues[0][0], 'UPBIT_WS_BOOK_BACKLOG')

    def test_threaded_producers_never_lose_the_latest(self):
        r = _module()()
        def produce(market):
            for n in range(5000):
                r._enqueue_ws_event('book', _book(market, n))
        threads = [threading.Thread(target=produce, args=('KRW-%d' % i,)) for i in range(6)]
        [t.start() for t in threads]; [t.join() for t in threads]
        r.drain()
        latest = {b['market']: b['bid'] for b in r.dispatched}
        self.assertEqual(len(latest), 6)
        self.assertTrue(all(v == '4999' for v in latest.values()))


class Wiring(unittest.TestCase):
    def test_runtime_installs_the_overlay_after_the_preload_wrapper(self):
        src = (BASE / 'app/src/main/python/android_runtime_v078.py').read_text(encoding='utf-8')
        a = src.index('_v081_upbit.UpbitPaperRunner._enqueue_ws_event=_v081_coin_verified_ws_prime_before_orders')
        b = src.index('_install_ws_book_coalesce_v0230(_v081_upbit)')
        self.assertLess(a, b)

    def test_overlay_cannot_create_orders_or_prices(self):
        src = (BASE / 'app/src/main/python/ws_book_coalesce_v0230.py').read_text(encoding='utf-8')
        for word in ('buy(', 'sell(', 'write_execution', 'latest_prices', 'last_book'):
            self.assertNotIn(word, src)


if __name__ == '__main__':
    unittest.main()
