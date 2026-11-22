"""0.2.0: MA and MarketTrend buy at HALF the per-symbol limit while the market score is <= -10.

0.2.9: the owner's sheet 11.22 deletes the "<= -10 -> half" rule and replaces it with the market-regime
table (market_regime_v0290). The Factor expectations below follow that table; the stock clamp, wiring and
evidence tests are unchanged in intent (the clamp now reads size.decision)."""
import sqlite3
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
PY = BASE / 'app/src/main/python'
sys.path.insert(0, str(PY))
import market_score_size_v0200 as size
import ma_v067_stock_runtime as stock


def read(name):
    return (PY / name).read_text(encoding='utf-8')


class Factor(unittest.TestCase):
    SERIES = {'UP': [100.0 + i for i in range(80)], 'DOWN': [200.0 - i for i in range(80)],
              'SIDEWAYS': [100.0 + (i % 2) for i in range(80)], None: []}

    def _factor(self, score, regime='SIDEWAYS'):
        # 0.2.10: the regime is judged on the bought symbol's own bars.
        with patch('at_market_score_gate_v107.score_evidence',
                   return_value={'score': score, 'basis': 'X'}), \
             patch('market_regime_v0290.closes_for', return_value=self.SERIES[regime]):
            return size.evidence('/x', 'coin', 'KRW-X', '30m')

    def test_threshold_is_minus_ten_inclusive(self):
        # 0.2.9 SIDEWAYS: score >= -10 -> 50 %, below -10 -> 100 %.
        self.assertEqual(self._factor(-10.0)[1], Decimal('0.5'))
        self.assertEqual(self._factor(-10.01)[1], Decimal(1))
        self.assertEqual(self._factor(-55.0)[1], Decimal(1))
        self.assertEqual(self._factor(0.0)[1], Decimal('0.5'))
        # 0.2.9 DOWN: -20..-10 -> 70 %, < -20 -> 100 %.
        self.assertEqual(self._factor(-15.0, 'DOWN')[1], Decimal('0.7'))
        self.assertEqual(self._factor(-20.5, 'DOWN')[1], Decimal(1))
        # 0.2.9 UP: the former half rule is deleted.
        self.assertEqual(self._factor(-55.0, 'UP')[1], Decimal(1))
        self.assertEqual(self._factor(-10.0, 'UP')[1], Decimal(1))

    def test_unknown_or_stale_score_is_not_a_reduction(self):
        self.assertEqual(self._factor(None)[1], Decimal(1))
        self.assertEqual(self._factor(None, 'DOWN')[1], Decimal(1))
        with patch('at_market_score_gate_v107.score_evidence', side_effect=RuntimeError('x')):
            self.assertEqual(size.evidence('/x', 'coin'), (None, Decimal(1)))

    def test_owner_examples(self):
        # 2,000,000 seed, 10 positions -> 200,000 -> 100,000 ; 5 positions -> 400,000 -> 200,000
        self.assertEqual(size.limit_krw(2_000_000 // 10, Decimal('0.5')), 100_000)
        self.assertEqual(size.limit_krw(2_000_000 // 5, Decimal('0.5')), 200_000)
        self.assertEqual(size.limit_krw(2_000_000 // 5, Decimal(1)), 400_000)
        self.assertEqual(size.limit_krw(333_333, Decimal('0.5')), 166_666)   # floor

    def test_recovers_automatically(self):
        self.assertEqual(self._factor(-5.0, 'DOWN')[1], Decimal('0.5'))
        self.assertEqual(self._factor(-25.0, 'DOWN')[1], Decimal(1))   # same code path, next buy


class StockClamp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Path(self.dir.name) / 'ma_paper.sqlite3'
        c = sqlite3.connect(self.db)
        c.executescript("""CREATE TABLE accounts(tf TEXT PRIMARY KEY,seed INTEGER,cash INTEGER,
            realized INTEGER,max_positions INTEGER);
            CREATE TABLE candidates(id INTEGER PRIMARY KEY,tf TEXT,code TEXT,limit_price INTEGER,status TEXT);
            INSERT INTO accounts VALUES('5m',2000000,2000000,0,10);
            INSERT INTO candidates VALUES(1,'5m','005930',10100,'PENDING');""")
        c.commit(); c.close()
        self.service = SimpleNamespace(db=self.db, market_store=SimpleNamespace(root=Path(self.dir.name)))

        class Book:
            def available(self_, code, venue, now, limit, desired):
                return (10000, 20, 99, 0.1), 'VALID'
        self.book = Book()
        stock._install_score_size_clamp(self.book)

    def tearDown(self):
        self.dir.cleanup()

    def ask(self, code='005930'):
        return self.book.available(code, 'KRX', None, 10100, 20)

    def test_half_limit_caps_the_quantity_only_inside_the_ma_tick(self):
        with patch('market_score_size_v0200.decision', return_value={'score': -12.0, 'factor': Decimal('0.5'), 'block': None}):
            stock._SIZE_CTX.ctx = (self.service, '005930')
            try:
                proof, _ = self.ask()
            finally:
                stock._SIZE_CTX.ctx = None
            self.assertEqual(proof[0], 10000)
            self.assertEqual(proof[1], 9)       # floor(100,000 / (10,000*1.00015)); full limit would allow 20
            outside, _ = self.ask()             # AT / GMO / other threads never see the cap
            self.assertEqual(outside[1], 20)

    def test_full_limit_when_score_is_above_minus_ten(self):
        with patch('market_score_size_v0200.decision', return_value={'score': -3.0, 'factor': Decimal(1), 'block': None}):
            stock._SIZE_CTX.ctx = (self.service, '005930')
            try:
                proof, _ = self.ask()
            finally:
                stock._SIZE_CTX.ctx = None
        self.assertEqual(proof[1], 20)

    def test_other_symbols_and_threads_are_untouched(self):
        with patch('market_score_size_v0200.decision', return_value={'score': -30.0, 'factor': Decimal('0.5'), 'block': None}):
            stock._SIZE_CTX.ctx = (self.service, '005930')
            try:
                other, _ = self.ask('000660')
                seen = []
                t = threading.Thread(target=lambda: seen.append(self.ask()[0][1]))
                t.start(); t.join()
            finally:
                stock._SIZE_CTX.ctx = None
        self.assertEqual(other[1], 20)
        self.assertEqual(seen, [20])

    def test_below_one_share_means_no_buy_not_a_wrong_buy(self):
        c = sqlite3.connect(self.db); c.execute("UPDATE accounts SET seed=40000"); c.commit(); c.close()
        with patch('market_score_size_v0200.decision', return_value={'score': -30.0, 'factor': Decimal('0.5'), 'block': None}):
            stock._SIZE_CTX.ctx = (self.service, '005930')
            try:
                proof, why = self.ask()           # limit 2,000 KRW < one 10,000 KRW share
            finally:
                stock._SIZE_CTX.ctx = None
        self.assertIsNone(proof)
        self.assertEqual(why, 'MARKET_SCORE_HALF_LIMIT_BELOW_ONE_SHARE')


class WiringIsPinned(unittest.TestCase):
    def test_every_ma_and_trend_buy_and_top_up_uses_the_factor(self):
        self.assertIn('size.decision(self.root,market,symbol,frame)', read('market_trend_wallet_v054.py'))
        self.assertIn('size.limit_krw(', read('market_trend_wallet_v054.py'))
        self.assertIn('size.factor(wallet.root,market,symbol,pos.frame)', read('trend_partial_entry_v094.py'))
        self.assertIn('size.factor(self.co.rt.root,self.market,symbol,frame)', read('market_trend_preplan_v099.py'))
        coin = read('ma_v067_coin_runtime.py')
        self.assertIn('_buy_size_evidence(self,order)', coin)
        self.assertIn('/(Decimal(1)+FEE_RATE)', coin.replace(' ', ''))
        self.assertIn("self.account_variant=='ma'", read('coin_partial_entry_v094.py'))
        self.assertIn("size.factor(self.market_store.root,'stock',code,p['tf'])", read('ma_v067_stock_runtime.py'))

    def test_reference_bar_at_gmo_and_cw_are_not_touched(self):
        for name in ('at_market_score_gate_v107.py', 'gmo_partial_entry_v094.py'):
            self.assertNotIn('market_score_size_v0200', read(name))

    def test_trend_position_keeps_score_and_factor_as_evidence(self):
        src = read('market_trend_wallet_v054.py')
        self.assertIn("'market_score_at_buy':score_at_buy", src)
        self.assertIn("'buy_size_factor':str(size_factor)", src)


if __name__ == '__main__':
    unittest.main()
