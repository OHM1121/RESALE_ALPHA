"""v094 market-trend both-markets first-BUY underfilled top-up.

Only actual existing MT54 positions with original PAPER first BUY execution.
Never creates a signal, another position, or broker order. The first original
BUY fill fixes the +2 tick cap; position averages never expand it. 15-second
window preserves near-close trend entry, and original SQLite/quote reserve
remain the money and paper execution authority.
"""
from __future__ import annotations
import datetime as dt
import json
import math
from decimal import Decimal as D
from partial_entry_v094 import qualified_topup
from buy_execution_recovery_v082 import Intent
from market_trend_wallet_v043 import STOCK_FEE,COIN_FEE,MIN_COIN_KRW,_money,_coin_qty


def topup_trend(wallet,pos,quote,now):
    if pos is None or pos.strategy!='trend' or now.tzinfo is None:return 0
    if pos.execution_mode!='PAPER' or pos.qty<=0 or pos.stage!=0:return 0
    if pos.__dict__.get('bar_finalized') or pos.tp1_sold or pos.tp2_sold:
        return 0
    if not 0<(now-pos.entry_at).total_seconds()<=15:return 0
    if wallet._is_live(pos.market):
        return 0
    symbol=pos.symbol;market=pos.market
    try:
        ask=D(str(quote['ask']));ask_size=D(str(quote['ask_size']))
        if ask<=0 or ask_size<=0:return 0
        if market=='stock':
            runner=wallet.rt.stock_runner
            book=runner.engine.book
            with book._lock:
                current=book._items.get((symbol,'KRX'))
            if current is None:return 0
            if (int(current[0])!=int(ask) or not -.5<=(now-current[3]).total_seconds()<=3):
                return 0
            instrument_tick=None
        elif market=='coin':
            runner=wallet.rt.coin_runner
            with runner._lock:book=dict(runner.last_book.get(symbol) or {})
            if (book.get('market')!=symbol or
                abs(now.timestamp()-int(book['timestamp'])/1000)>3 or
                D(str(book.get('ask',0)))!=ask):
                return 0
            instrument_tick=book.get('sr_tick_size')
        else:return 0
    except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):
        return 0

    with wallet.order_lock:
        with wallet._connect(market) as c:
            row=c.execute("""SELECT * FROM positions WHERE id=? AND tf=? AND
                symbol=? AND status='TREND_OPEN' AND qty>0""",
                (pos.position_id,pos.frame,symbol)).fetchone()
            if row is None or str(row['execution_mode'])!='PAPER':return 0
            if abs(float(row['qty'])-float(row['qty0']))>1e-7:return 0
            if abs(float(row['qty'])-pos.qty)>1e-7:return 0
            if c.execute("""SELECT 1 FROM executions WHERE position_id=?
                AND side='SELL' LIMIT 1""",(pos.position_id,)).fetchone():return 0
            fills=c.execute("""SELECT qty,price,fees FROM executions WHERE
                position_id=? AND side='BUY' ORDER BY id""",
                (pos.position_id,)).fetchall()
            if not fills:return 0
            first=D(str(fills[0]['price']))
            spent=sum((D(str(x['qty']))*D(str(x['price']))+D(int(x['fees']))
                       for x in fills),D(0))
            account=c.execute('SELECT * FROM accounts WHERE tf=?',
                              (pos.frame,)).fetchone()
            if account is None:return 0
            import market_score_size_v0200 as size
            per_symbol=size.limit_krw(
                int(account['seed'])//max(1,int(account['max_positions'])),
                size.factor(wallet.root,market,symbol,pos.frame))
            if not qualified_topup(market,first,ask,spent,per_symbol,False,True,
                                   symbol=symbol,instrument_tick=instrument_tick):
                return 0
            budget=min(D(int(account['cash'])),D(per_symbol)-spent)
            if budget<=0:return 0
            # Scenario 2 only follows an ORIGINAL, proved first execution.
            # Scenario 3 waits for a renewed verified quote rather than
            # terminating the incomplete seed at a temporary book gap.
            verified_ms=(int(current[3].timestamp()*1000) if market=='stock'
                         else int(book['timestamp']))
            bid=(int(current[1]) if market=='stock' else D(str(book['bid'])))
            recovery=Intent('trend',market,symbol,
                str(pos.position_id)+':TOPUP',str(first),
                str(budget),str(STOCK_FEE if market=='stock' else COIN_FEE))
            recovery.attempts=1
            command=recovery.quote({
                'symbol':symbol,'exchange_ms':verified_ms,
                'ask':str(ask),'bid':str(bid),'ask_size':str(ask_size)
            },int(now.timestamp()*1000))
            if command.get('action')!='PLACE':return 0
            max_recovery_qty=D(command['qty'])
            if market=='stock':
                desired=int(budget/(ask*(D(1)+D(str(STOCK_FEE)))))
                proof,_=book.available(symbol,'KRX',now,int(ask),desired)
                if proof is None:return 0
                qty=min(desired,int(proof[1]),int(max_recovery_qty))
                fee_rate=STOCK_FEE
            else:
                qty=_coin_qty(min(ask_size*D('.5'),max_recovery_qty,
                    budget/(ask*(D(1)+D(str(COIN_FEE))))))
                fee_rate=COIN_FEE
            if qty<=0:return 0
            for _ in range(5):
                gross=_money(float(qty)*float(ask))
                fee=_money(gross*fee_rate)
                if gross+fee<=budget:break
                if market=='stock':qty-=1
                else:qty=_coin_qty(D(str(qty))-D('.00000001'))
            else:return 0
            if qty<=0 or (market=='coin' and qty*ask<D(MIN_COIN_KRW)):
                return 0
            stamp=now.isoformat()
            event='MT54:BUY:TOPUP:'+':'.join((
                market,str(pos.position_id),str(int(now.timestamp()*1000))))
            if c.execute('SELECT 1 FROM executions WHERE event_key=?',(event,)).fetchone():
                return 0
            if market=='stock' and not book.reserve(
                symbol,'KRX',now,int(ask),int(qty)):
                return 0
            import copy
            new=copy.copy(pos)
            initial=D(str(pos.original_qty))
            expanded=initial+D(str(qty))
            new.entry=float((D(str(pos.entry))*initial+ask*D(str(qty)))/expanded)
            new.original_qty=float(expanded)
            new.qty=float(D(str(pos.qty))+D(str(qty)))
            new.peak=max(float(pos.peak),float(ask))
            new.last_price=float(ask)
            new.__dict__['last_topup_at']=stamp
            c.execute('UPDATE accounts SET cash=cash-? WHERE tf=?',
                      (gross+fee,pos.frame))
            c.execute("""UPDATE positions SET entry=?,qty0=?,qty=?,entry_fee=?,
                mark=?,payload=?,updated_at=? WHERE id=?""",
                (new.entry,new.original_qty,new.qty,int(row['entry_fee'])+fee,
                 float(ask),json.dumps(new.to_dict(),ensure_ascii=False),
                 stamp,pos.position_id))
            c.execute("""INSERT INTO executions(event_key,position_id,tf,symbol,at,
                side,qty,price,fees,tax,net_pnl,cash_after,execution_mode,
                broker_order_id,reason)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event,pos.position_id,pos.frame,symbol,stamp,'BUY',float(qty),
                 float(ask),fee,0,0,int(account['cash'])-(gross+fee),
                 'PAPER','',
                 'MARKET_TREND_V094_PARTIAL_TWO_TICKS'))
        pos.__dict__.update(new.__dict__)
        return float(qty)


def install():
    """Attach to market trend hold observer after its original quote safety."""
    import market_trend_runtime_v043 as trend
    prior=trend.Coordinator._process_holds
    if getattr(prior,'_v094_topup',False):
        return
    def observed(self,market,now):
        # Original SELL first. A stopped/trailing position may never top up.
        result=prior(self,market,now)
        try:
            for key,p in list(self._positions.items()):
                if p.market!=market or p.strategy!='trend' or p.qty<=0:
                    continue
                q=self._quote(market,p.symbol)
                if q is not None:
                    topup_trend(self.standalone_wallet,p,q,now)
        except Exception as exc:
            try:self._last_fetch_error[market]='V094_PARTIAL:'+type(exc).__name__
            except Exception:pass
        return result
    observed._v094_topup=True
    trend.Coordinator._process_holds=observed
