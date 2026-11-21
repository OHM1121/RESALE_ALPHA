"""0.1.7 (#128): PIEVERSE export - 1,557 worthless dust SELL fills and 1,589 rejected real SELLs.

Boundary under test: the MA V067 coin exit loop (`_process_v067_exits`) against a REAL SQLite wallet
and the PAPER resting plan (`new_legacy_1118_resting`).

Defects reproduced from the export (position qty0 46.9483641 after a partial top-up BUY, resting plan
still sized from the first fill 23.47418205):
 A. The plan was never resized, so once its small stage-1 quantity was used every normal-sized SELL
    raised NL1118_RESTING_SELL_MISMATCH (rolling back the whole SELL, 1,589 times).
 B. Only 50% of the BEST bid level was used. When that level was dust (0.0000002 coin) the engine
    sold the same dust on every book frame (1,557 fills, 0 KRW each) and never finished the stage.
"""
import datetime as dt
import json
import sqlite3
import sys
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'app/src/main/python'))
import ma_v067_policy as policy
import ma_v067_coin_runtime as coin
import new_legacy_1118_resting as resting

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 21, 2, 24, 8, tzinfo=KST)
ENTRY = '3574'
QTY0 = '46.9483641'


def make_db():
    c = sqlite3.connect(':memory:', check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,name TEXT,ref_at TEXT,
        entry_at TEXT,entry TEXT,qty0 TEXT,qty TEXT,entry_fee INTEGER,target TEXT,peak TEXT,mark TEXT,
        first_qty TEXT,first_sold_qty TEXT DEFAULT '0',trailing INTEGER DEFAULT 0,
        recovery INTEGER DEFAULT 0,status TEXT,hold_until TEXT);
    CREATE TABLE ma_v067_positions(position_id INTEGER PRIMARY KEY,kind TEXT,frame TEXT,code TEXT,
        state_json TEXT,created_at TEXT,updated_at TEXT);
    CREATE TABLE executions(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,position_id INTEGER,
        tf TEXT,market TEXT,at TEXT,side TEXT,qty TEXT,price TEXT,fees INTEGER,tax INTEGER,
        net_pnl INTEGER,reason TEXT,cash_after INTEGER);
    """)
    state = policy.initial_up_exit_state('5m')
    state['entry_at'] = '2026-11-21T00:34:57.339492+09:00'
    state['coin_qty_step'] = '0.00000001'
    state['nl1120'] = True
    first_goal, _ = policy.up_stage_quantities(state, QTY0)
    c.execute("""INSERT INTO positions VALUES(50,'5m','KRW-PIEVERSE','KRW-PIEVERSE','r','2026-11-21T00:34:57+09:00',
        ?,?,?,42,'3645.48',?,?,?,'0',0,0,'OPEN','2026-11-23T00:34:57+09:00')""",
        (ENTRY, QTY0, QTY0, ENTRY, ENTRY, str(first_goal)))
    c.execute("INSERT INTO ma_v067_positions VALUES(50,'REVERSAL_UP','5m','KRW-PIEVERSE',?,?,?)",
              (json.dumps(state), 'x', 'x'))
    # Plan written from the FIRST fill only (23.47418205), exactly as in the export.
    one, two = policy.up_targets(ENTRY, '5m')
    resting.place(c, 50, 'coin', 'KRW-PIEVERSE', '5m', '2026-11-21T00:34:57+09:00',
                  Decimal('23.47418205'), one, two,
                  Decimal('7.04225461'), Decimal('8.21596372'))
    return c, first_goal


class Ledger:
    lock = threading.RLock()

    def __init__(self, db):
        self.db = db
        self.sells = []

    def connect(self):
        return self.db

    def _sell(self, c, p, qty, price, at, reason, event_key=None):
        qty = Decimal(str(qty)); price = Decimal(str(price))
        left = Decimal(p['qty']) - qty
        c.execute("""INSERT INTO executions(event_key,position_id,tf,market,at,side,qty,price,fees,tax,
            net_pnl,reason,cash_after) VALUES(?,?,?,?,?,'SELL',?,?,0,0,0,?,0)""",
                  (event_key, p['id'], p['tf'], p['market'], at, format(qty, 'f'), str(price), reason))
        c.execute("UPDATE positions SET qty=?,status=? WHERE id=?",
                  (format(left, 'f'), 'CLOSED' if left <= 0 else 'OPEN', p['id']))
        self.sells.append((qty, price, reason))
        return True


def run(db, book):
    ledger = Ledger(db)
    with patch.object(coin, '_recent_trade', return_value=None), \
         patch.object(coin, '_table', return_value=None), \
         patch.object(coin, '_prior_verified_closes', return_value=[]), \
         patch.object(coin, '_rev1_coin_closed_bar_state', side_effect=lambda s, p, n: s), \
         patch.object(coin, '_v014_exit_block', return_value=None):
        coin._process_v067_exits(ledger, book, NOW)
    return ledger


def frame(levels):
    top = levels[0]
    return {'market': 'KRW-PIEVERSE', 'timestamp': int(NOW.timestamp() * 1000),
            'bid': top[0], 'bid_size': top[1],
            'bid_levels': [{'price': p, 'size': s} for p, s in levels]}


class PieverseReplay(unittest.TestCase):
    def test_dust_top_level_no_longer_blocks_the_stage(self):
        db, goal = make_db()
        # Export situation: best bid is a 0.00000044-coin dust level; real depth sits just below.
        ledger = run(db, frame([('3767', '0.00000044'), ('3700', '120'), ('3680', '120')]))
        first = [s for s in ledger.sells if s[2] == 'MA_V1118_UP_FIRST_30PCT']
        self.assertEqual(len(first), 1, 'stage 1 must complete in ONE sale, not in dust pieces')
        self.assertEqual(first[0][0], goal)
        self.assertGreaterEqual(first[0][1], Decimal(policy.up_targets(ENTRY, '5m')[0]),
                                'fill price may never be below the stage target')
        self.assertLessEqual(len(ledger.sells), 2)
        state = json.loads(db.execute('SELECT state_json FROM ma_v067_positions').fetchone()[0])
        self.assertEqual(Decimal(state['first_sold']), goal)
        row = [r for r in resting.rows(db, 50) if r['stage'] == 1][0]
        self.assertEqual(Decimal(row['filled_qty']), goal)
        self.assertEqual(row['status'], 'PAPER_FILLED')

    def test_plan_follows_the_policy_goal_after_a_top_up(self):
        db, goal = make_db()
        before = [r for r in resting.rows(db, 50) if r['stage'] == 1][0]
        self.assertEqual(Decimal(before['order_qty']), Decimal('7.04225461'))
        run(db, frame([('3767', '500')]))
        after = [r for r in resting.rows(db, 50) if r['stage'] == 1][0]
        self.assertGreaterEqual(Decimal(after['order_qty']), goal)
        self.assertEqual(Decimal(after['original_qty']), Decimal(QTY0))
        self.assertEqual(db.execute("SELECT count(*) FROM nl1118_resting_faults").fetchone()[0], 0)

    def test_only_dust_liquidity_is_not_sold_and_not_logged_as_fills(self):
        db, _ = make_db()
        for _ in range(50):   # 50 book frames with only a dust level visible
            run(db, frame([('3767', '0.00000044')]))
        self.assertEqual(db.execute("SELECT count(*) FROM executions").fetchone()[0], 0,
                         'dust below the minimum order value must never create fills')
        self.assertEqual(Decimal(db.execute("SELECT qty FROM positions").fetchone()[0]), Decimal(QTY0))

    def test_levels_below_the_target_are_never_used_for_a_target_stage(self):
        db, _ = make_db()
        # Best bid above target but all real depth below the 3645.48 target price.
        ledger = run(db, frame([('3767', '0.00000044'), ('3600', '500'), ('3590', '500')]))
        self.assertEqual([s for s in ledger.sells if s[2] == 'MA_V1118_UP_FIRST_30PCT'], [])

    def test_completing_a_tiny_stage_remainder_is_allowed(self):
        db, goal = make_db()
        state = json.loads(db.execute('SELECT state_json FROM ma_v067_positions').fetchone()[0])
        state['first_sold'] = str(goal - Decimal('0.01'))      # 0.01 coin (~37 KRW) still to sell
        db.execute('UPDATE ma_v067_positions SET state_json=?', (json.dumps(state),))
        ledger = run(db, frame([('3767', '100')]))
        first = [s for s in ledger.sells if s[2] == 'MA_V1118_UP_FIRST_30PCT']
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][0], Decimal('0.01'))


class PlanSidecarNeverBlocksASale(unittest.TestCase):
    def setUp(self):
        self.db, _ = make_db()

    def _sell_row(self, qty):
        cur = self.db.execute("""INSERT INTO executions(event_key,position_id,side,qty,price)
            VALUES(?,?,?,?,?)""", ('k%s' % qty, 50, 'SELL', qty, '3700'))
        return cur.lastrowid

    def test_oversize_fill_grows_the_plan_and_records_it(self):
        row_id = self._sell_row('9.5')           # plan is only 7.04225461
        self.assertTrue(resting.confirmed_fill(self.db, 50, 1, row_id))
        row = [r for r in resting.rows(self.db, 50) if r['stage'] == 1][0]
        self.assertEqual(Decimal(row['filled_qty']), Decimal('9.5'))
        self.assertEqual(self.db.execute("SELECT count(*) FROM nl1118_resting_faults").fetchone()[0], 1)

    def test_resize_never_drops_below_filled_and_never_revives_cancelled(self):
        row_id = self._sell_row('5')
        resting.confirmed_fill(self.db, 50, 1, row_id)
        one, two = policy.up_targets(ENTRY, '5m')
        resting.place(self.db, 50, 'coin', 'KRW-PIEVERSE', '5m', 'x', Decimal('10'), one, two,
                      Decimal('3'), Decimal('3'))    # goal smaller than what was filled
        row = [r for r in resting.rows(self.db, 50) if r['stage'] == 1][0]
        self.assertEqual(Decimal(row['order_qty']), Decimal('5'))
        self.assertEqual(row['status'], 'PAPER_FILLED')
        resting.cancel_remaining(self.db, 50, 'STOP')
        resting.place(self.db, 50, 'coin', 'KRW-PIEVERSE', '5m', 'x', Decimal('100'), one, two,
                      Decimal('30'), Decimal('35'))
        rows = {r['stage']: r for r in resting.rows(self.db, 50)}
        self.assertTrue(rows[2]['status'].startswith('PAPER_CANCELLED'),
                        'a cancelled plan is never revived by a later resize')
        self.assertEqual(rows[1]['status'], 'PAPER_PARTIAL')   # unfinished stage follows the policy goal
        self.assertEqual(Decimal(rows[1]['order_qty']), Decimal('30'))
        self.assertEqual(Decimal(rows[1]['filled_qty']), Decimal('5'))

    def test_missing_native_receipt_still_refuses(self):
        with self.assertRaises(ValueError):
            resting.confirmed_fill(self.db, 50, 1, 99999)


class ShadowResearchCannotAbortTheBook(unittest.TestCase):
    def test_incomplete_v067_shadow_trial_is_quarantined_not_raised(self):
        c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row
        c.execute("""CREATE TABLE coin_shadow_trials(candidate_id INTEGER PRIMARY KEY,market TEXT,state TEXT,
            features_json TEXT,updated_at TEXT)""")
        v067 = {'ma_v067_kind': 'REVERSAL_UP', 'v24_first_fraction': '.10', 'source': 'UPBIT_V079'}
        complete = {'high': '110', 'low': '90', 'v24_first_fraction': '.30'}
        c.execute("INSERT INTO coin_shadow_trials VALUES(1,'KRW-BTC','WAITING_QUOTE',?,'')", (json.dumps(v067),))
        c.execute("INSERT INTO coin_shadow_trials VALUES(2,'KRW-BTC','WAITING_QUOTE',?,'')", (json.dumps(complete),))
        c.execute("INSERT INTO coin_shadow_trials VALUES(3,'KRW-ETH','WAITING_QUOTE',?,'')", (json.dumps(v067),))
        self.assertEqual(coin._quarantine_incomplete_shadow_trials(c, 'KRW-BTC'), 1)
        states = dict(c.execute('SELECT candidate_id,state FROM coin_shadow_trials').fetchall())
        self.assertEqual(states[1], coin.SHADOW_INCOMPLETE_STATE)
        self.assertEqual(states[2], 'WAITING_QUOTE')        # complete schema untouched
        self.assertEqual(states[3], 'WAITING_QUOTE')        # other market untouched

    def test_guard_is_installed_on_the_vendor_entry_point(self):
        src = (BASE / 'app/src/main/python/ma_v067_coin_runtime.py').read_text(encoding='utf-8')
        self.assertIn('d_upbit.shadow_observe_book=guarded', src)
        self.assertIn('_install_shadow_guard(d_upbit)', src)


class StockUsesTheSameContract(unittest.TestCase):
    def test_stock_exit_follows_goal_and_never_lets_the_sidecar_block_a_sale(self):
        src = (BASE / 'app/src/main/python/ma_v067_stock_runtime.py').read_text(encoding='utf-8')
        body = src[src.index('def _exit_policy('):src.index('def _topup_partial_stock')]
        self.assertIn("resting.place(c,int(p['id']),'stock'", body)
        self.assertIn('resting.record_fault(', body)
        self.assertIn('except (ValueError,sqlite3.Error) as sidecar', body)


if __name__ == '__main__':
    unittest.main()
