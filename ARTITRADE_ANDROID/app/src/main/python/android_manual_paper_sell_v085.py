"""Android v085 ONLY: explicit verified-paper manual exits.

"실거래" is the USER-FACING name for a simulated-account fill based on real
quotes. This module NEVER calls a broker order adapter. The historical core's
internal LIVE flag means *broker orders* and MUST remain OFF here.

All requests carry an exact position owner, original position id/symbol and
quantity, a UUID and an explicit user confirmation. Original strategy wallets
remain responsible for atomic debits, realized P&L and durable fills.
"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal
from uuid import UUID
import json
import math
import time
from pathlib import Path

KST=dt.timezone(dt.timedelta(hours=9))

def _fail(code,detail=""):
    return json.dumps({'ok':False,'error':code,'detail':str(detail)[:220]},
                      ensure_ascii=False,default=str)

def _quant(value):
    v=Decimal(str(value))
    if not v.is_finite() or v<=0:raise ValueError('INVALID_POSITIVE_QTY')
    return v

def _stock_tick(rt,code,venue='KRX'):
    runner=rt.stock_runner
    if runner is None:raise ValueError('STOCK_FEED_NOT_READY')
    tick=(runner.latest_krx_trade(code) if venue=='KRX' else runner.latest_trade(code))
    if not tick or len(tick)<9 or str(tick[0])!=code:
        raise ValueError('FRESH_STOCK_TRADE_MISSING')
    if venue is not None and str(tick[7])!=venue:
        raise ValueError('WRONG_STOCK_VENUE')
    from d_minute_repair_fast import exchange_stamp_is_fresh
    if not exchange_stamp_is_fresh(tick[8],tick[6]):
        raise ValueError('STOCK_TRADE_STALE')
    return tick

def _coin_book(rt,code):
    runner=rt.coin_runner
    if runner is None:raise ValueError('COIN_FEED_NOT_READY')
    with runner._lock:
        b=dict(runner.last_book.get(code) or {})
    if b.get('market')!=code or abs(time.time()-float(b.get('timestamp',0))/1000)>3:
        raise ValueError('FRESH_COIN_ORDERBOOK_MISSING')
    if _quant(b.get('bid',0))<=0 or _quant(b.get('bid_size',0))<=0:
        raise ValueError('NO_COIN_BID_DEPTH')
    return b

def _stock_at(rt,req):
    tick=_stock_tick(rt,req['code'],None)
    # Verify native owner, source market, quantity and idempotency in V19 engine.
    return rt.stock_runner.engine.manual_sell(
        req['position_id'],req['percent'],req['request_id'],tick,
        expected_qty=int(req['expected_qty']))

class _VerifiedManualStockBid:
    """Paper manual exits: borrow only displayed fresh KRX best-bid depth.

    Automatic strategies retain the original conservative 50%-participation
    rule. A user's explicitly confirmed manual PAPER sell may take up to the
    current displayed bid quantity; original reservations remain shared.
    This is not broker execution and it never fabricates a market print.
    """
    def __init__(self,raw):
        self.raw=raw

    def available_sell(self,code,venue,now,bid,desired):
        with self.raw._lock:
            key=(str(code),str(venue))
            row=self.raw._bid_depth.get(key)
            if not row:return 0
            px,amount,when=row
            age=(now-when).total_seconds()
            if (px!=int(bid) or not -.5<=age<=3 or int(amount)<=0):
                return 0
            reserved=int(self.raw._bid_reserved.get(key,0))
            return max(0,min(int(desired),int(amount)-reserved))

    def reserve_sell(self,code,venue,now,bid,quantity):
        with self.raw._lock:
            if int(quantity)<=0 or self.available_sell(
                    code,venue,now,bid,quantity)<int(quantity):
                return False
            key=(str(code),str(venue))
            self.raw._bid_reserved[key]=int(self.raw._bid_reserved.get(key,0))+int(quantity)
            return True


def _stock_verified_bid_book(rt,code):
    runner=rt.stock_runner
    if runner is None or getattr(runner,'engine',None) is None:
        raise ValueError('STOCK_BOOK_NOT_READY')
    book=getattr(runner.engine,'book',None)
    if book is None:raise ValueError('STOCK_BOOK_NOT_READY')
    now=dt.datetime.now(KST)
    with book._lock:
        quote=book._items.get((code,'KRX'))
        depth=book._bid_depth.get((code,'KRX'))
    if quote is None or depth is None:
        raise ValueError('FRESH_KRX_0D_BID_MISSING')
    ask,bid,ask_qty,quoted_at=quote
    actual_bid,bid_qty,depth_at=depth
    if (bid<=0 or ask<bid or actual_bid!=bid or
        bid_qty<=0 or quoted_at.tzinfo is None or depth_at.tzinfo is None or
        not -.5<=(now-quoted_at).total_seconds()<=3 or
        not -.5<=(now-depth_at).total_seconds()<=3):
        raise ValueError('FRESH_KRX_0D_BID_INVALID_OR_STALE')
    return int(bid),now,_VerifiedManualStockBid(book)


def _stock_ma(rt,req):
    from stock_ma_engine import _connect
    service=rt.stock_runner.ma
    if service is None:raise ValueError('STOCK_MA_NOT_READY')
    reason='USER_SIM_MANUAL:'+req['request_id']
    with service.lock,_connect(service.db) as c:
        # UUID repeat must remain idempotent even after the first request
        # has fully CLOSED the position and no current quote is available.
        old=c.execute("""SELECT e.qty,e.price FROM executions e
            JOIN positions p ON p.id=e.position_id
            WHERE p.id=? AND p.code=? AND e.reason=? AND e.side='SELL'""",
            (req['position_id'],req['code'],reason)).fetchone()
        if old:return {'already_executed':True,'qty':old['qty'],'price':old['price']}
        row=c.execute("SELECT * FROM positions WHERE id=? AND code=? AND status='OPEN' AND qty>0",
                      (req['position_id'],req['code'])).fetchone()
        if row is None:raise ValueError('MA_POSITION_NOT_OPEN_OR_MISMATCH')
        if int(row['qty'])!=int(req['expected_qty']):
            raise ValueError('MA_QTY_CHANGED_RECONFIRM')
        # A real exchange 0D update remains valid for a manual PAPER exit
        # even without a coincident 0B trade. Use the ACTUAL best bid,
        # never the unrelated last-trade tick price.
        bid,now,verified_book=_stock_verified_bid_book(rt,req['code'])
        qty=max(1,min(int(row['qty']),int(Decimal(str(row['qty']))*
                      Decimal(str(req['percent']))/Decimal('100'))))
        sold=service._sell(c,row,qty,bid,now,reason,verified_book)
        if not sold:raise ValueError('FRESH_MA_BID_DEPTH_MISSING')
        execution=c.execute('SELECT qty,price FROM executions WHERE position_id=? AND reason=?',
                            (req['position_id'],reason)).fetchone()
        if not execution:raise ValueError('MA_SELL_FILL_UNVERIFIED')
        return {'qty':int(execution['qty']),'price':int(execution['price']),
                'requested_qty':int(qty),'remaining_qty':max(0,int(row['qty'])-int(execution['qty'])),
                'quote_source':'LIVE_KRX_0D_BID_PAPER_ONLY'}

def _coin(rt,req,which):
    ledger=(rt.coin_runner.ma_ledger if which=='ma' else rt.coin_runner.ledger)
    if ledger is None:raise ValueError('COIN_LEDGER_NOT_READY')
    key='USER:'+req['request_id']

    def existing_fill(c):
        # A retried UUID belongs to the ORIGINAL position, even after its
        # balance became zero or its quote turned stale. Consult durable
        # executions first: never re-sell or report an old fill as a failure.
        row=c.execute("""SELECT qty,price,position_id,market FROM executions
            WHERE event_key=? AND side='SELL'""",(key,)).fetchone()
        if row is None:return None
        if (int(row['position_id'])!=int(req['position_id']) or
                str(row['market'])!=req['code']):
            raise ValueError('COIN_MANUAL_UUID_POSITION_MISMATCH')
        return {'qty':row['qty'],'price':row['price'],'already_executed':True}

    with ledger.lock,ledger.connect() as c:
        prior=existing_fill(c)
        if prior:return prior
    book=_coin_book(rt,req['code'])
    with ledger.lock,ledger.connect() as c:
        prior=existing_fill(c)
        if prior:return prior
        row=c.execute("SELECT market,qty FROM positions WHERE id=? AND status='OPEN'",
                      (req['position_id'],)).fetchone()
        if row is None or str(row['market'])!=req['code']:
            raise ValueError('COIN_POSITION_NOT_OPEN_OR_MISMATCH')
        if _quant(row['qty'])!=_quant(req['expected_qty']):
            raise ValueError('COIN_QTY_CHANGED_RECONFIRM')
    did=ledger.manual_sell(req['position_id'],req['percent'],book,request_id=req['request_id'])
    with ledger.lock,ledger.connect() as c:
        filled=existing_fill(c)
    if filled:
        filled['already_executed']=not bool(did)
        return filled
    # No execution evidence: NEVER claim a PAPER fill or repeat the order.
    raise ValueError('COIN_MANUAL_FILL_NOT_RECORDED')

def _trend(rt,req):
    co=getattr(rt,'market_trend',None)
    if co is None:raise ValueError('TREND_RUNTIME_NOT_READY')
    with co.lock:
        position=next((p for p in co._positions.values()
                       if (p.market==req['market'] and p.strategy=='trend'
                           and p.symbol==req['code']
                           and int(getattr(p,'position_id',-1))==req['position_id'])),None)
    if position is None or position.qty<=0:
        raise ValueError('TREND_POSITION_NOT_OPEN_OR_MISMATCH')
    if position.execution_mode!='PAPER':
        raise ValueError('TREND_NON_SIMULATED_OR_UNVERIFIED_POSITION')
    if abs(Decimal(str(position.qty))-_quant(req['expected_qty']))>Decimal('0.0000001'):
        raise ValueError('TREND_QTY_CHANGED_RECONFIRM')
    quote=co._quote(req['market'],req['code'])
    if not quote or float(quote['bid'])<=0 or float(quote['bid_size'])<=0:
        raise ValueError('TREND_FRESH_BID_MISSING')
    sold,reason=co.standalone_wallet.sell(
        position,quote['bid'],dt.datetime.now(KST),
        'USER_SIM_MANUAL:'+req['request_id'],bid_size=quote['bid_size'],
        manual_fraction=req['percent']/100.0)
    if not sold:raise ValueError('TREND_MANUAL_SELL_'+str(reason))
    return {'qty':sold,'price':quote['bid']}

def _gmo(rt,req):
    service=rt.stock_runner.gmo
    if service is None:raise ValueError('GMO_SERVICE_NOT_READY')
    # A verified user-initiated PAPER SELL may place its original GMO
    # pending intent on genuine 0D bid depth alone. A new 0B trade is
    # required later by the ORIGINAL paper matcher to mark it FILLED;
    # never create a synthetic print simply to bypass a quiet book.
    at=dt.datetime.now(KST)
    if not (dt.time(9,0)<=at.time()<dt.time(15,30) or
            dt.time(16,0)<=at.time()<dt.time(20,0)):
        raise ValueError('GMO_MANUAL_KRX_SESSION_CLOSED')
    with service._lock,service.engine._lock:
        p=service.engine.positions.get(req['code'])
        if p is None or p.qty<=0:raise ValueError('GMO_POSITION_NOT_OPEN')
        if int(p.qty)!=int(req['expected_qty']):raise ValueError('GMO_QTY_CHANGED_RECONFIRM')
        book=service.last_book.get(req['code'])
        if (not book or len(book)<5
            or book[4].tzinfo is None
            or not -.5<=(at-book[4]).total_seconds()<=3
            or int(book[1])<=0 or int(book[3])<=0):
            raise ValueError('GMO_FRESH_KRX_BID_MISSING')
        reason='USER_SIM_MANUAL:'+req['request_id']
        existing=next((o for o in service.engine.orders.values()
            if o.code==req['code'] and o.side=='SELL' and o.purpose==reason),None)
        if existing:
            return {'status':'PENDING' if existing.state not in ('FILLED','CANCELED') else existing.state,
                    'already_executed':True,'order_id':existing.id}
        if p.pending_sell_id:
            raise ValueError('GMO_EXISTING_SELL_PENDING')
        qty=max(1,min(p.qty,int(Decimal(str(p.qty))*Decimal(str(req['percent']))/100)))
        intent=service.engine._exit(p,reason,qty,int(book[1]),at.isoformat())
        if intent is None:raise ValueError('GMO_MANUAL_SELL_INTENT_BLOCKED')
        service._persist(True)
        # This is NOT a fill; the original matcher waits for the NEXT genuine
        # KRX 0B with eligible depth. Never turn stored book/REST into a fill.
        return {'status':'PENDING_REAL_KRX_PRINT','order_id':intent.id,'qty_requested':qty}

def perform(raw):
    try:
        import android_live_bridge as bridge
        req=json.loads(raw) if isinstance(raw,str) else raw
        if not isinstance(req,dict):return _fail('INVALID_REQUEST')
        market=req.get('market');strategy=req.get('strategy')
        code=str(req.get('code') or '')
        if market not in ('stock','coin'):return _fail('INVALID_MARKET')
        if strategy not in ('at','ma','general','trend','gmo','close'):
            return _fail('INVALID_STRATEGY')
        if market=='stock' and strategy=='general':return _fail('WRONG_OWNER')
        if market=='coin' and strategy in ('at','gmo','close'):return _fail('WRONG_OWNER')
        if market=='stock' and not (code.isdigit() and len(code)==6):
            return _fail('INVALID_STOCK_SYMBOL')
        if market=='coin' and not code.startswith('KRW-'):return _fail('INVALID_COIN_SYMBOL')
        request_id=str(UUID(str(req.get('request_id') or '')))
        percent=float(req.get('percent'))
        if not math.isfinite(percent) or not 0<percent<=100:
            return _fail('INVALID_SELL_PERCENT')
        position_id=int(req.get('position_id'))
        if position_id<0 and strategy!='gmo':return _fail('INVALID_POSITION_ID')
        expected_qty=str(_quant(req.get('expected_qty')))
        request=dict(req,code=code,market=market,strategy=strategy,
                     percent=percent,position_id=position_id,request_id=request_id,
                     expected_qty=expected_qty)
        rt=getattr(bridge,'_RUNTIME',None)
        if rt is None or not rt.started:return _fail('STRATEGY_NOT_RUNNING')
        # This APK NEVER sends real brokerage orders, regardless of the UI label.
        if rt.live_enabled('stock') or rt.live_enabled('coin'):
            return _fail('BROKER_MODE_ARMED_DISALLOWED')
        from android_live_position_guard_v049 import (
            refuse_paper_sell_if_live_open)
        refuse_paper_sell_if_live_open(rt.root,market,code)
        if market=='stock' and strategy=='at':
            filled=_stock_at(rt,request)
        elif market=='stock' and strategy=='ma':
            filled=_stock_ma(rt,request)
        elif market=='stock' and strategy=='gmo':
            filled=_gmo(rt,request)
        elif strategy=='trend':
            filled=_trend(rt,request)
        elif market=='coin' and strategy in ('general','ma'):
            filled=_coin(rt,request,strategy)
        else:
            return _fail('STRATEGY_MANUAL_SELL_NOT_YET_SUPPORTED')
        if not isinstance(filled,dict):filled={'filled':bool(filled)}
        status=filled.get('status','FILLED')
        return json.dumps({'ok':True,'market':market,'strategy':strategy,'code':code,
            'request_id':request_id,'status':status,**filled},
            ensure_ascii=False,default=str)
    except Exception as exc:
        return _fail(type(exc).__name__,str(exc))
