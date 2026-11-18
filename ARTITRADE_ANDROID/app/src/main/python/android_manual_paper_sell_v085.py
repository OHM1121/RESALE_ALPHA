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
import re
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
    # Prefer the identical upstream WS evidence used by automatic entries.
    # An incomplete or malformed WS book is not executable depth. Previously
    # it raised before the independently verified public-book fallback, so an
    # explicitly confirmed PAPER exit could fail despite a live public quote.
    # Do not convert a missing bid into a fill: try ONE fresh orderbook read.
    try:
        age=time.time()-float(b.get('timestamp',0))/1000
        if b.get('market')==code and -.5<=age<=3.0:
            _quant(b.get('bid',0))
            _quant(b.get('bid_size',0))
            return b
    except (ValueError,TypeError,ArithmeticError,OverflowError):
        pass

    # An existing holding may no longer be subscribed in the TOP50 WS feed.
    # A *user-confirmed* PAPER exit can read ONE fresh public Upbit orderbook
    # without opening another WebSocket, subscribing, or submitting an order.
    # Use the exchange's real bid/depth; no candle, ticker, or cached price is
    # ever substituted for the missing current best bid.
    try:
        import requests
        reply=requests.get('https://api.upbit.com/v1/orderbook',
            params={'markets':code,'count':1},
            headers={'accept':'application/json'},timeout=(1.0,2.0))
        reply.raise_for_status()
        payload=reply.json()
        if not isinstance(payload,list) or len(payload)!=1:
            raise ValueError('ORDERBOOK_RESPONSE_COUNT')
        item=payload[0]
        if not isinstance(item,dict) or item.get('market')!=code:
            raise ValueError('ORDERBOOK_RESPONSE_MARKET')
        stamp=int(item.get('timestamp',0))
        age=time.time()-stamp/1000
        if not -.5<=age<=3.0:
            raise ValueError('ORDERBOOK_EXCHANGE_TIMESTAMP_STALE')
        units=item.get('orderbook_units')
        if not isinstance(units,list) or not units or not isinstance(units[0],dict):
            raise ValueError('ORDERBOOK_TOP_LEVEL_MISSING')
        best=units[0]
        bid=_quant(best.get('bid_price',0))
        size=_quant(best.get('bid_size',0))
        ask=_quant(best.get('ask_price',0))
        ask_size=_quant(best.get('ask_size',0))
        if bid>ask:
            raise ValueError('ORDERBOOK_CROSSED_OR_INVALID')
        return {'market':code,'bid':str(bid),'bid_size':str(size),
                'ask':str(ask),'ask_size':str(ask_size),'timestamp':stamp}
    except Exception as exc:
        # Fail closed. No execution is created without independently fresh
        # exchange bid depth; return one stable, useful error to Android.
        raise ValueError('FRESH_COIN_ORDERBOOK_MISSING') from exc

def _stock_at(rt,req):
    # v086: genuine KRX 0D/ka10004 depth may prove a confirmed PAPER
    # user exit without any coincident 0B print. The original AT engine's
    # _sell remains the only cash/PnL/execution accountant.
    from stock_manual_v086 import manual_stock_at
    return manual_stock_at(rt,req)

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
    # Current and preserved historical MA epochs are separate original
    # wallets. Match the exact position and UUID before any new quote.
    from stock_manual_v086 import manual_stock_ma
    return manual_stock_ma(rt,req)


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
    if req.get('market')=='stock':
        from stock_manual_v086 import manual_stock_trend
        return manual_stock_trend(rt,req)
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
    from stock_manual_v086 import manual_stock_gmo
    return manual_stock_gmo(rt,req)


def _stock_close(rt,req):
    from stock_manual_v086 import manual_stock_close
    return manual_stock_close(rt,req)


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
        elif market=='stock' and strategy=='close':
            filled=_stock_close(rt,request)
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
    except ValueError as exc:
        # Surface the native stock/coin order and ledger blocker instead of
        # showing every verified no-fill as the generic "ValueError".
        # Never turn an exception into an executed PAPER SELL receipt.
        reason=str(exc).strip()
        if re.fullmatch(r'[A-Z][A-Z0-9_]{3,95}',reason):
            return _fail(reason,reason)
        return _fail('INVALID_MANUAL_VALUE',reason)
    except Exception as exc:
        return _fail(type(exc).__name__,str(exc))
