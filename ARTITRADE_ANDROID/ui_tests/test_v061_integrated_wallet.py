"""Use actual immutable EXE CoinLedger/MA wallet against v061 signal/fill overlay.

Deterministic public quote PAPER fixtures only. No private Upbit order is made.
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import ma_v061_coin_runtime as coin
import ma_v061_fast_entry as fast
from ma_v061_rules import candidate_from_closed
from market_trend_v043 import KST

def pivot_history(tf='5m'):
    unit={'5m':300,'30m':1800,'60m':3600,'day':86400}[tf]
    closes=[Decimal(200-i*100/63) for i in range(63)]
    closes += [Decimal(92+j*.2) for j in range(57)]
    closes += [Decimal(90),Decimal(105)]
    start=dt.datetime(2026,10,25,0,0,tzinfo=dt.timezone.utc)
    bars=[]
    for i,close in enumerate(closes):
        opening=close-Decimal('.30')
        bars.append({'start':start+dt.timedelta(seconds=unit*i),
            'open':opening,'high':close+Decimal('.3'),
            'low':opening-Decimal('.2'),'close':close,'volume':Decimal(100)})
    assert candidate_from_closed(bars,tf)['kind']=='TURNPOINT'
    return bars


def book(at,bid,ask,code='KRW-NCT'):
    return {'market':code,'timestamp':int(at.timestamp()*1000),
        'bid':str(bid),'ask':str(ask),'bid_size':'90000',
        'ask_size':'90000'}


class RealWalletPaperV061(unittest.TestCase):
    def setUp(self):
        from coin_ma_engine import MACoinLedger
        coin.install()
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wallet=MACoinLedger(Path(self.tmp.name)/'ma')
        self.symbol='KRW-NCT'

    def _p(self):
        with self.wallet.connect() as c:
            rows=c.execute("SELECT * FROM positions ORDER BY id").fetchall()
            return rows[-1] if rows else None

    def _state(self):
        p=self._p()
        if p is None:return None
        with self.wallet.connect() as c:
            row=c.execute("SELECT * FROM ma_v061_positions WHERE position_id=?",
                          (p['id'],)).fetchone()
            return json.loads(row['state_json']) if row else None

    def _open(self,tf='5m'):
        import coin_ma_engine as ma
        history=pivot_history(tf)
        finish=history[-1]['start']+dt.timedelta(seconds={'5m':300,
                     '30m':1800,'60m':3600,'day':86400}[tf])
        at=finish.astimezone(KST)+dt.timedelta(seconds=1)
        diag,candidate=ma.ma_reference_decision(
            history,tf,self.symbol,Decimal('.01'),at)
        self.assertEqual(diag['status'],'QUALIFIED')
        self.assertEqual(candidate['features']['ma_v061_kind'],'TURNPOINT')
        self.assertTrue(self.wallet.record_candidate(candidate,'폴리스웜',at))
        with self.wallet.connect() as c:
            row=c.execute("SELECT * FROM orders WHERE status='PENDING'").fetchone()
            deadline=dt.datetime.fromisoformat(row['expires_at'])
            self.assertEqual((deadline-at).total_seconds(),4)
        filled=self.wallet.process_book(book(at+dt.timedelta(seconds=1),104,105),
                                        at=at+dt.timedelta(seconds=1))
        self.assertIsNotNone(self._p())
        self.assertEqual(self._state()['stage'],0)
        return at

    def test_pivot_open_5sec_initial_20pct_then_half_remaining_then_peak_minus4(self):
        start=self._open('5m')
        p=self._p()
        first=int(Decimal(p['qty0'])*Decimal('.2'))
        at=start+dt.timedelta(seconds=2)
        self.wallet.process_book(book(at,107,107.1),at)
        state=self._state()
        self.assertEqual(state['stage'],1)
        self.assertGreater(Decimal(state['first_sold']),0)
        with self.wallet.connect() as c:
            fills=c.execute("SELECT reason FROM executions WHERE side='SELL'").fetchall()
            self.assertTrue(any('MA_V061_FIRST_20PCT' in x['reason'] for x in fills))
        at+=dt.timedelta(seconds=2)
        self.wallet.process_book(book(at,110.30,110.35),at)
        state=self._state()
        self.assertEqual(state['stage'],2)
        self.assertGreater(Decimal(state['second_sold']),0)
        at+=dt.timedelta(seconds=2)
        self.wallet.process_book(book(at,114,114.1),at)
        at+=dt.timedelta(seconds=2)
        self.wallet.process_book(book(at,109.40,109.45),at)
        with self.wallet.connect() as c:
            reasons=[x['reason'] for x in c.execute(
                "SELECT reason FROM executions WHERE side='SELL'")]
            self.assertTrue(any('MA_V061_THIRD_PEAK_MINUS4' in x for x in reasons))
            self.assertEqual(c.execute(
                "SELECT status FROM positions ORDER BY id DESC LIMIT 1").fetchone()['status'],
                'CLOSED')

    def test_after_first_target_entry_price_protects_remaining(self):
        start=self._open('30m')
        at=start+dt.timedelta(seconds=2)
        self.wallet.process_book(book(at,108,108.1),at)
        self.assertEqual(self._state()['stage'],1)
        at+=dt.timedelta(seconds=2)
        self.wallet.process_book(book(at,104,104.1),at)
        with self.wallet.connect() as c:
            reasons=[r['reason'] for r in c.execute(
                "SELECT reason FROM executions WHERE side='SELL'")]
            self.assertIn('MA_V061_AFTER_FIRST_ENTRY_GUARD',reasons)
            self.assertEqual(c.execute(
                "SELECT status FROM positions ORDER BY id DESC LIMIT 1").fetchone()['status'],
                'CLOSED')

    def test_no_fill_after_five_second_deadline(self):
        import coin_ma_engine as ma
        history=pivot_history('5m')
        end=history[-1]['start']+dt.timedelta(minutes=5)
        late=end.astimezone(KST)+dt.timedelta(seconds=6)
        _,candidate=ma.ma_reference_decision(
            history,'5m',self.symbol,Decimal('.01'),late)
        self.assertFalse(self.wallet.record_candidate(candidate,'폴리스웜',late))
        with self.wallet.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],0)

    def test_fast_shortlist_is_not_a_trade_or_fabricated_ohlcv(self):
        h=pivot_history('5m')
        snapshot=[dict(x) for x in h[:-1]]
        self.assertTrue(fast._shortlist(snapshot,Decimal(105)))
        self.assertFalse(fast._shortlist(snapshot[:20],Decimal(105)))
        self.assertEqual(len(snapshot),121)

if __name__=='__main__':unittest.main()
