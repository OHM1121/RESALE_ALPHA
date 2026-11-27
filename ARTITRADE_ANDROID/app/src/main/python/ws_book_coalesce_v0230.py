"""0.2.3: coin order-book frames are coalesced to the LATEST frame per market.

Root cause (issue #137, CONFIRMED in the 2026-11-22 export + vendor source):
the vendor UpbitPaperRunner puts EVERY order-book frame into one 4,096-slot queue that a
single worker drains (two SQLite ledgers per frame). When the worker falls behind the queue
fills, `put_nowait` then drops the NEWEST frames and the worker keeps processing minutes-old
ones: 906,540 frames dropped, UPBIT_WS_EVENT_QUEUE_PRESSURE 161 times, held-exit gate
BOOK_STALE 9,806 times, order-book gaps of 2.5-5 minutes. The ticker path already keeps only the
latest frame per code (`_v084_accept_tick`); the book path did not.

This overlay applies the same rule to books: only the newest frame of a market is kept, one small
token per market is queued, the worker therefore always handles current data and the backlog
is bounded by the number of watched markets. It creates no order, fill, price or book: a frame
still has to pass the vendor's age check on arrival and the 3 s held-exit gate on use.
The vendor engine file is not modified.
"""
import queue
import threading
import time

_INSTALLED = False
TOKEN_KEY = '__ws_book_latest_token_v0230__'
LAG_ISSUE_SECONDS = 3.0
ISSUE_INTERVAL_SECONDS = 120.0
# 0.3.9 (#154): the book worker is saturated (lag 3-22 s in every hour of the 2026-11-21..27 exports, 98k
# held-exit BOOK_STALE rows) but nothing says WHERE one frame spends its time. Measure it: per-frame
# processing milliseconds in every BACKLOG row, plus a short cProfile window (top functions) at most every
# PROFILE_EVERY_SECONDS while lagging. Pure measurement: the dispatch result and arguments are untouched.
PROFILE_EVERY_SECONDS = 900.0
PROFILE_FRAMES = 25
PROFILE_TOP = 8


def _state(runner):
    state = getattr(runner, '_v0230_book_state', None)
    if state is None:
        state = {'lock': threading.Lock(), 'latest': {}, 'pending': set(),
                 'received': 0, 'coalesced': 0, 'dispatched': 0, 'queue_full': 0,
                 'max_lag': 0.0, 'issue_at': None}
        runner._v0230_book_state = state
    return state


def coalesce(runner, book, put):
    """Keep only the newest book per market. `put(token)` queues one token. Returns True if queued."""
    market = str(book.get('market') or '')
    if not market:
        return False
    st = _state(runner)
    with st['lock']:
        st['received'] += 1
        st['latest'][market] = (book, time.monotonic())
        if market in st['pending']:
            st['coalesced'] += 1
            return True
        st['pending'].add(market)
    try:
        put({TOKEN_KEY: True, 'market': market})
    except queue.Full:
        with st['lock']:
            st['pending'].discard(market)
            st['queue_full'] += 1
        return False
    return True


def take_latest(runner, token):
    """Newest book for the token's market (and the seconds it waited), or (None, 0)."""
    st = _state(runner)
    market = str(token.get('market') or '')
    with st['lock']:
        st['pending'].discard(market)
        item = st['latest'].pop(market, None)
        if item is None:
            return None, 0.0
        st['dispatched'] += 1
    book, queued_at = item
    lag = max(0.0, time.monotonic() - queued_at)
    with st['lock']:
        if lag > st['max_lag']:
            st['max_lag'] = lag
    return book, lag


def stats(runner):
    st = _state(runner)
    with st['lock']:
        return {k: st[k] for k in ('received', 'coalesced', 'dispatched', 'queue_full', 'max_lag')} | {
            'pending': len(st['pending']),
            'proc_avg_ms': round(1000.0 * st.get('proc_sum', 0.0) / st['proc_n'], 2) if st.get('proc_n') else None,
            'proc_max_ms': round(1000.0 * st.get('proc_max', 0.0), 1)}


def _arm_profile(runner, now):
    """Start a short profiling window (one at a time, at most every PROFILE_EVERY_SECONDS)."""
    st = _state(runner)
    try:
        if st.get('prof') is None and (st.get('prof_at') is None or now - st['prof_at'] > PROFILE_EVERY_SECONDS):
            import cProfile
            st['prof_at'] = now
            st['prof'] = cProfile.Profile()
            st['prof_left'] = PROFILE_FRAMES
            st['prof_ms'] = 0.0
    except Exception:
        st['prof'] = None


def _report_profile(runner, st):
    prof, st['prof'] = st.get('prof'), None
    try:
        import io
        import pstats
        out = io.StringIO()
        stats_ = pstats.Stats(prof, stream=out)
        rows = []
        for (filename, line, name), (cc, nc, tt, ct, callers) in stats_.stats.items():
            rows.append((ct, tt, nc, str(filename).replace('\\', '/').rsplit('/', 1)[-1], line, name))
        rows.sort(reverse=True)
        top = '; '.join('%s:%s:%s cum=%.0fms self=%.0fms n=%d' % (r[3], r[4], r[5], r[0] * 1000, r[1] * 1000, r[2])
                        for r in rows[:PROFILE_TOP])
        runner._safe_ws_issue('UPBIT_WS_BOOK_PROFILE_V0370',
            'frames=%d total_ms=%.0f top: %s' % (PROFILE_FRAMES, st.get('prof_ms', 0.0), top[:1500]))
    except Exception:
        pass


def _timed_dispatch(runner, previous_dispatch, latest):
    st = _state(runner)
    prof = st.get('prof')
    started = time.monotonic()
    try:
        if prof is not None:
            try:
                prof.enable()
            except Exception:
                prof = None
        return previous_dispatch(runner, latest)
    finally:
        elapsed = time.monotonic() - started
        try:
            if prof is not None:
                try:
                    prof.disable()
                except Exception:
                    pass
                st['prof_ms'] = st.get('prof_ms', 0.0) + elapsed * 1000.0
                st['prof_left'] = st.get('prof_left', 0) - 1
                if st['prof_left'] <= 0:
                    _report_profile(runner, st)
            with st['lock']:
                st['proc_n'] = st.get('proc_n', 0) + 1
                st['proc_sum'] = st.get('proc_sum', 0.0) + elapsed
                if elapsed > st.get('proc_max', 0.0):
                    st['proc_max'] = elapsed
        except Exception:
            pass


def install(d_upbit):
    """Wrap the runner's book enqueue/dispatch. Idempotent; other event kinds are untouched."""
    global _INSTALLED
    if _INSTALLED:
        return
    runner_type = d_upbit.UpbitPaperRunner
    previous_enqueue = runner_type._enqueue_ws_event
    previous_dispatch = runner_type._dispatch_public_book

    def enqueue(self, kind, payload=None, *args, **kwargs):
        if kind != 'book' or not isinstance(payload, dict) or payload.get(TOKEN_KEY):
            return previous_enqueue(self, kind, payload, *args, **kwargs)
        # Same preload rule as the existing wrapper: no side effects before the gate is ready.
        try:
            if not self.preload_gate.snapshot().get('ready', False):
                return False
        except Exception:
            return previous_enqueue(self, kind, payload, *args, **kwargs)
        return coalesce(self, payload, lambda token: self._ws_event_queue.put_nowait(('book', token)))

    def dispatch(self, book):
        if not (isinstance(book, dict) and book.get(TOKEN_KEY)):
            return previous_dispatch(self, book)
        latest, lag = take_latest(self, book)
        if latest is None:
            return None
        if lag > LAG_ISSUE_SECONDS:
            st = _state(self)
            now = time.monotonic()
            if st['issue_at'] is None or now - st['issue_at'] > ISSUE_INTERVAL_SECONDS:
                st['issue_at'] = now
                try:
                    s = stats(self)
                    self._safe_ws_issue('UPBIT_WS_BOOK_BACKLOG',
                        'book waited %.1fs before processing; received=%d coalesced=%d dispatched=%d queue_full=%d pending=%d proc_avg_ms=%s proc_max_ms=%s' % (
                            lag, s['received'], s['coalesced'], s['dispatched'], s['queue_full'], s['pending'],
                            s['proc_avg_ms'], s['proc_max_ms']))
                except Exception:
                    pass
            _arm_profile(self, now)
        return _timed_dispatch(self, previous_dispatch, latest)

    runner_type._enqueue_ws_event = enqueue
    runner_type._dispatch_public_book = dispatch
    _INSTALLED = True
