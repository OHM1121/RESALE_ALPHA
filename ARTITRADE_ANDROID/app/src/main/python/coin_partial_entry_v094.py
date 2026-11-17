"""v094 strict first-purchase +2 Upbit ticks when original coin seed underfunded.

REAL original Upbit book only. Existing original CoinLedger._write_fill is
called inside its real SQLite transaction. This creates no new positions,
does not change the original candidate signal, and cannot run after any SELL
or after initial partial-entry window. Original websocket/strategy service
and public-paper wallet are reused, never a second API connection.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import time
from decimal import Decimal as D
from pathlib import Path
from partial_entry_v094 import qualified_topup
_INSTALLED=False


def topup_verified_coin_book(self,book,now):
    from d_upbit import qty_floor,dec,money,krw_fee,fmt_qty,MIN_VIRTUAL_ORDER_KRW
    if not isinstance(book,dict) or now.tzinfo is None:return 0
    symbol=str(book.get('market') or '')
    if not symbol.startswith('KRW-'):return 0
    try:
        ask=dec(book['ask']);size=dec(book['ask_size'])
        stamp_ms=int(book['timestamp'])
        if (ask<=0 or size<=0 or
            abs(now.timestamp()-stamp_ms/1000)>3):return 0
        # A current book must have legal, noncrossed top BUY/SELL quotes.
        bid=dec(book['bid'])
        if bid<=0 or bid>ask:return 0
    except (TypeError,KeyError,ValueError,ArithmeticError):
        return 0
    try:
        import android_live_bridge
        rt=getattr(android_live_bridge,'_RUNTIME',None)
        if rt is not None and rt.live_enabled('coin'):
            return 0
    except (ImportError,AttributeError):
        return 0

    with self.lock,self.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        positions=c.execute("""SELECT * FROM positions WHERE market=?
            AND status='OPEN' AND qty!='0' ORDER BY id""",(symbol,)).fetchall()
        for p in positions:
            try:
                if dec(p['qty'])!=dec(p['qty0']) or dec(p['first_sold_qty'])>0:
                    continue
                entered=dt.datetime.fromisoformat(str(p['entry_at']))
                if entered.tzinfo is None:return 0
                # Keep v067 upward 15-second execution rule; other original
                # reference buys can finish an underfilled slot within 30s.
                window=15 if self.account_variant=='ma' else 30
                if not 0<(now-entered).total_seconds()<=window:
                    continue
                if c.execute("""SELECT 1 FROM executions WHERE position_id=?
                    AND side='SELL' LIMIT 1""",(p['id'],)).fetchone():continue
                fills=c.execute("""SELECT qty,price,fees FROM executions WHERE
                    position_id=? AND side='BUY' ORDER BY id""",(p['id'],)).fetchall()
                if not fills:continue
                first=dec(fills[0]['price'])
                spent=sum((dec(x['qty'])*dec(x['price'])+D(int(x['fees'])) for x in fills),D(0))
                setup=self.settings.snapshot()['frames'][p['tf']]
                max_symbols=max(1,int(setup['max_positions']))
                cap=D(int(setup['allocated_krw'])//max_symbols)
                instrument_tick=(book.get('sr_tick_size') or
                                 self.sr_instrument_ticks.get(symbol))
                if not qualified_topup('coin',first,ask,spent,cap,
                    False,True,symbol=symbol,instrument_tick=instrument_tick):
                    continue
                wallet=c.execute("SELECT cash FROM wallets WHERE tf=?",(p['tf'],)).fetchone()
                if wallet is None:continue
                available=min(D(int(wallet['cash'])),cap-spent)
                if available<D(MIN_VIRTUAL_ORDER_KRW):continue
                qty=min(qty_floor(size*D('.5')),qty_floor(available/ask))
                while qty>0 and (money(qty*ask)+krw_fee(qty*ask)>available):
                    qty=qty_floor(qty-D('0.00000001'))
                if qty<=0 or qty*ask<D(MIN_VIRTUAL_ORDER_KRW):continue
                key='COIN_V094_PARTIAL:'+str(p['id'])+':'+str(stamp_ms)
                if c.execute("SELECT 1 FROM executions WHERE event_key=?",
                             (key,)).fetchone():continue
                gross=money(qty*ask);fee=krw_fee(qty*ask)
                cost=gross+fee
                if cost>available:continue
                oldqty=dec(p['qty0'])
                total=oldqty+qty
                prior_fraction=(dec(p['first_qty'])/oldqty if oldqty>0 else D('.1'))
                first_qty=qty_floor(total*prior_fraction)
                average=(dec(p['entry'])*oldqty+qty*ask)/total
                target=p['target']
                state_row=None
                try:
                    state_row=c.execute("""SELECT state_json FROM ma_v067_positions
                        WHERE position_id=?""",(p['id'],)).fetchone()
                except Exception:
                    pass
                if state_row:
                    try:
                        st=json.loads(state_row['state_json'])
                        if st.get('policy')=='MA_V065_REVERSAL_UP':
                            from ma_v067_policy import up_targets
                            target=str(up_targets(average,p['tf'])[0])
                    except (TypeError,ValueError,KeyError):
                        continue
                stamp=now.isoformat()
                c.execute('UPDATE wallets SET cash=cash-? WHERE tf=?',(cost,p['tf']))
                c.execute("""UPDATE positions SET entry=?,qty0=?,qty=?,entry_fee=?,
                    first_qty=?,target=?,mark=? WHERE id=? AND status='OPEN'""",
                    (str(average),fmt_qty(total),fmt_qty(dec(p['qty'])+qty),
                     int(p['entry_fee'])+fee,fmt_qty(first_qty),target,
                     str(bid),p['id']))
                # Use original ledger write and portfolio/AI execution audit;
                # no original existing SELL is erased or reclassified.
                self._write_fill(c,key,p['id'],p['tf'],symbol,stamp,
                    'BUY',qty,ask,fee,0,'COIN_V094_PARTIAL_TWO_TICKS',
                    int(wallet['cash'])-cost)
                c.commit()
                return float(qty)
            except (TypeError,ValueError,KeyError,ArithmeticError,OverflowError):
                continue
    return 0


def install():
    global _INSTALLED
    if _INSTALLED:return
    import d_upbit
    # Called only after ma_v067_coin_runtime.install, so its existing
    # original late entry/SELL wrappers remain active and untouched.
    previous=d_upbit.CoinLedger.process_book

    def book(self,raw,at=None):
        completed=previous(self,raw,at)
        try:
            now=at if isinstance(at,dt.datetime) else d_upbit.kst_now()
            topup_verified_coin_book(self,raw,now)
        except Exception as exc:
            # Native price/exit processing must never be interrupted by a
            # new optional partial top-up policy.
            try:
                self._v094_topup_error=type(exc).__name__+':'+str(exc)[:100]
            except Exception:pass
        return completed

    d_upbit.CoinLedger.process_book=book
    _INSTALLED=True
