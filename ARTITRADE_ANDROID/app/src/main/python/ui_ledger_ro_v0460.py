"""0.4.6 (#161): the app's own screen refresh held the trading ledger gate for 5+ seconds - UI snapshots now read without it.

Evidence (stock error export 2026-11-27 15:09, app 0.4.4, `stock/db_gate_long_hold_v0400.jsonl`, 60 holder stacks of a gate hold >= 5 s,
12:13-15:02): 36 `artitrade-fix14-higher-frame-rest`, **22 anonymous `Dummy-N` threads = the UI**
(`Runtime.hub_records -> _stock_at_ledger` 16, `_hub_records_v078 -> _stock_at_v19_full` 6), 2 start-up preload.
The UI functions did `with store.lock, store.connect() as c:` and then ran 7-10 SELECTs (10,000-row execution lists, signal joins) on a
250 MB database. The trading ledger gate is the only lock the tick pipeline needs, so every screen refresh blocked ticks for seconds,
from several UI threads in turn. This is the "anonymous Dummy-N holders" the 0.4.0 measurement was added to find.

Fix: the three UI snapshot functions read through a private READ-ONLY SQLite connection (`mode=ro`, `query_only`) and do not take the
gate. SQLite WAL readers never block the writer, and these functions only display (no write, no order, no ledger change). The output is
the same as before (checked against the original function on a copy of the real database).

Second small fix (fix14 holders, 3 of 36 stacks inside `datetime.strptime`): `reference_policy.parse_timestamp` is called for every
candle of a recovered frame under the gate; a pure fast path builds the `datetime` directly for a well-formed 14-digit stamp and
falls back to the original function for anything else (the original is lenient about shorter strings, so they are not touched).
"""
from __future__ import annotations

import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime

VERSION = "UI_LEDGER_RO_V0460"
READ_TIMEOUT = 10.0


@contextmanager
def ui_read(store):
    """Read-only connection to the stock ledger database; never takes the trading gate."""
    cx = sqlite3.connect("file:%s?mode=ro" % store.db, uri=True, timeout=READ_TIMEOUT)
    cx.row_factory = sqlite3.Row
    try:
        cx.execute("PRAGMA query_only=ON")
        yield cx
    finally:
        cx.close()


def stock_at_ledger_ro(self):
    """The vendor `Runtime._stock_at_ledger` (legacy AT ledger for the hub), reading without the gate. Output identical."""
    if not self.stock_runner:
        return {}
    store = self.stock_runner.store
    with ui_read(store) as c:
        positions = []
        for row in c.execute("SELECT * FROM positions WHERE status='OPEN' ORDER BY id DESC LIMIT 300").fetchall():
            d = dict(row)
            d['strategy'] = 'at'
            d['paper_pnl'] = int(d.get('qty') or 0) * (int(d.get('last_price') or 0) - int(d.get('entry_price') or 0))
            positions.append(d)
        pending = []
        for row in c.execute("""SELECT p.*,s.name,s.market,s.target,s.decision FROM pending p
                LEFT JOIN signals s ON s.id=p.signal_id ORDER BY p.score DESC""").fetchall():
            d = dict(row)
            d['strategy'] = 'at'
            d['limit_won'] = d.get('price')
            d['first_target_won'] = d.get('target')
            d['reason'] = '기준봉 조건 충족 · 지정가 대기'
            pending.append(d)
        executions = []
        for row in c.execute("""SELECT e.*,p.entry_price,p.entry_qty,p.entry_fee FROM executions e
                LEFT JOIN positions p ON p.id=e.position_id ORDER BY e.id DESC LIMIT 350""").fetchall():
            d = dict(row)
            d['strategy'] = 'at'
            if str(d.get('side')) == 'SELL':
                try:
                    alloc = round(int(d.get('entry_fee') or 0) * int(d.get('qty') or 0) / max(1, int(d.get('entry_qty') or 1)))
                    d['net_profit'] = (int(d.get('gross') or 0) - int(d.get('qty') or 0) * int(d.get('entry_price') or 0)
                                       - int(d.get('fees') or 0) - int(d.get('tax') or 0) - alloc)
                except Exception:
                    d['net_profit'] = None
            else:
                d['net_profit'] = None
            executions.append(d)
        accounts = []
        for row in c.execute('SELECT * FROM accounts ORDER BY tf').fetchall():
            a = dict(row)
            tf = a.get('tf')
            held = sum((x['paper_pnl'] for x in positions if x.get('tf') == tf))
            accounts.append({'tf': tf, 'strategy': 'at', 'cash': a.get('cash'), 'realized_pnl': a.get('realized_pnl', 0),
                             'unrealized_pnl': held})
        signals = [dict(x) for x in c.execute("""SELECT s.id,s.ts,s.tf,s.code,s.name,s.market,s.ref_ts,s.buy_price,s.target,s.score,s.decision,
                cf.first_limit_touch_ts,cf.first_target_after_touch_ts,cf.post_touch_min,cf.post_touch_max
                FROM signals s LEFT JOIN candidate_forward cf ON cf.signal_id=s.id
                ORDER BY s.id DESC LIMIT 350""").fetchall()]
        issues = [dict(x) for x in c.execute(
            'SELECT seen_at AS at,code,message,details,severity FROM issues ORDER BY id DESC LIMIT 120').fetchall()]
        runtime = [dict(x) for x in c.execute(
            'SELECT last_seen AS at,context AS stage,error_type AS type,message,occurrences AS count,sample_traceback AS stack '
            'FROM runtime_error_summary ORDER BY last_seen DESC LIMIT 80').fetchall()]
    for r in signals:
        r['strategy'] = 'at'
        r['target_hit'] = bool(r.get('first_target_after_touch_ts'))
        r['entry_touched'] = bool(r.get('first_limit_touch_ts'))
    realized = sum(float(a.get('realized_pnl') or 0) for a in accounts)
    holding = sum(float(a.get('unrealized_pnl') or 0) for a in accounts)
    return {'positions': positions, 'pending': pending, 'executions': executions, 'accounts': accounts,
            'realized_pnl': realized, 'unrealized_pnl': holding, 'total_pnl': realized + holding,
            '_research_items': signals, '_errors': issues + runtime}


# ---------------------------------------------------------------------------------------- fast timestamp parse

def make_fast_parse(original):
    """`parse_timestamp(ts)` with a direct path for well-formed digit strings; everything else goes to the original."""

    def parse_timestamp(ts):
        # Only the canonical 14-digit bar stamp takes the direct path. Shorter strings keep the original's lenient
        # `strptime` behaviour (a 12-digit stamp is parsed by the 14-digit format first, e.g. '202401010037' -> 00:03:07).
        try:
            if len(ts) >= 14 and ts[:14].isdigit():
                return datetime(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]), int(ts[8:10]), int(ts[10:12]), int(ts[12:14]))
        except (ValueError, TypeError, AttributeError):
            pass
        return original(ts)

    parse_timestamp._v0460_fast = True
    parse_timestamp.__wrapped__ = original
    return parse_timestamp


def install_fast_parse():
    """Replace `reference_policy.parse_timestamp` everywhere it is bound by name. Returns the number of modules changed."""
    import reference_policy
    original = reference_policy.parse_timestamp
    if getattr(original, "_v0460_fast", False):
        return 0
    fast = make_fast_parse(original)
    changed = 0
    for module in list(sys.modules.values()):
        try:
            if getattr(module, "parse_timestamp", None) is original:
                module.parse_timestamp = fast
                changed += 1
        except Exception:
            continue
    return changed


def benchmark(function, rounds=3):
    best = None
    for _ in range(rounds):
        started = time.perf_counter()
        function()
        took = time.perf_counter() - started
        best = took if best is None else min(best, took)
    return best
