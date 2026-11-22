"""Verified, user-confirmed stock PAPER exits across every stock owner.

Original wallets and their native SELL accounting remain authoritative.
Neither a last trade price nor REST ka10001 is executable depth. Only genuinely
recent exchange 0D or official ka10004 best-bid+quantity may justify an
explicit PAPER price estimate. No broker orders, fabricated prints, schema
reset, background order retry or cross-strategy position transfer.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
import math
import re
import sqlite3
import threading
from uuid import UUID

KST=dt.timezone(dt.timedelta(hours=9))
REST_API="ka10004"
REST_PATH="/api/dostk/mrkcond"

def _num(value):
    if value is None or isinstance(value,bool):return 0
    try:return int(abs(Decimal(str(value).strip().replace(',',''))))
    except (ValueError,TypeError,OverflowError):return 0

def _pct(request):
    pct=Decimal(str(request['percent']))
    if not pct.is_finite() or pct<=0 or pct>100 or pct.as_tuple().exponent < -2:
        raise ValueError('INVALID_MANUAL_PERCENT')
    return pct

def _intended(holding,request):
    from d_engine import won
    held=int(holding)
    if held<=0:raise ValueError('STOCK_POSITION_NOT_OPEN')
    if held!=int(Decimal(str(request['expected_qty']))):
        raise ValueError('STOCK_QTY_CHANGED_RECONFIRM')
    return max(1,min(held,won(Decimal(held)*_pct(request)/Decimal(100))))

def _request_key(request):
    return 'MANUAL_PAPER_SELL:'+UUID(str(request['request_id'])).hex

AUTO_REASONS={'NIGHT_EWY_PREEMPTIVE_STOP'}

def _auto(request):
    """0.2.6: an automatic exit reuses this verified PAPER path but must NOT be
    recorded as the user's manual sell. Only whitelisted reasons are accepted."""
    value=str(request.get('auto_reason') or '')
    return value if value in AUTO_REASONS else ''

def _reason(request):
    auto=_auto(request)
    if auto:
        return 'AUTO_'+auto+':'+str(UUID(str(request['request_id'])))
    return 'USER_SIM_MANUAL:'+str(UUID(str(request['request_id'])))

def _krx_session(now):
    # ka10004's bid_req_base_tm is HHMMSS, without a calendar date. Never
    # convert yesterday's closing snapshot to a same-day PAPER transaction.
    if now.tzinfo is None or now.weekday()>=5:return False
    try:
        import holidays
        if now.date() in holidays.KR(years=[now.year]):return False
    except ImportError:pass
    return dt.time(9,0)<=now.time()<dt.time(15,30)

class VerifiedDepth:
    """One observed bid snapshot; reserve only proved capacity.

    When a WS book is live, use its SAME reservation ledger as other
    strategies. The REST snapshot instead has a local capacity counter.
    0.2.6: `venue` is the exchange the bid was observed on (KRX, or NXT for
    the automatic pre-market exit). Native sell owners call with the literal
    'KRX'; that label is mapped to THIS depth's own venue, never to another.
    """
    def __init__(self,code,bid,size,observed,shared=None,venue='KRX'):
        self.code=str(code);self.bid=int(bid);self.size=int(size)
        self.observed=observed;self.shared=shared;self.venue=str(venue)
        self.lock=threading.RLock();self.reserved=0

    def _same_venue(self,venue):
        return venue in ('KRX',self.venue)

    def available_sell(self,code,venue,now,bid,desired):
        if (str(code)!=self.code or not self._same_venue(venue) or int(bid)!=self.bid
            or not -.5<=(now-self.observed).total_seconds()<=3):
            return 0
        if self.shared is not None:
            with self.shared._lock:
                rec=self.shared._bid_depth.get((self.code,self.venue))
                if rec is None:return 0
                px,size,stamp=rec
                if (int(px)!=self.bid or stamp.tzinfo is None or
                    not -.5<=(now-stamp).total_seconds()<=3):return 0
                reserved=int(self.shared._bid_reserved.get((self.code,self.venue),0))
                return max(0,min(int(desired),int(size)-reserved))
        with self.lock:
            return max(0,min(int(desired),self.size-self.reserved))

    def reserve_sell(self,code,venue,now,bid,quantity):
        qty=int(quantity)
        if qty<=0:return False
        if self.shared is not None:
            with self.shared._lock:
                if self.available_sell(code,venue,now,bid,qty)<qty:return False
                key=(self.code,self.venue)
                self.shared._bid_reserved[key]=int(self.shared._bid_reserved.get(key,0))+qty
                return True
        with self.lock:
            if self.available_sell(code,venue,now,bid,qty)<qty:return False
            self.reserved+=qty
            return True

def _live_ws_bid(runner,code,now):
    book=getattr(getattr(runner,'engine',None),'book',None)
    if book is None:return None
    # An old cached book from a disconnected Kiwoom socket is not live.
    if not getattr(runner,'_confirmed_ws_session',False):return None
    with book._lock:
        quote=book._items.get((code,'KRX'))
        depth=book._bid_depth.get((code,'KRX'))
    if not quote or not depth:return None
    ask,bid,ask_qty,quote_at=quote
    bid_at,bid_qty,depth_at=depth
    if (quote_at.tzinfo is None or depth_at.tzinfo is None
        or not -.5<=(now-quote_at).total_seconds()<=3
        or not -.5<=(now-depth_at).total_seconds()<=3
        or not 0<int(bid)<=int(ask)
        or int(bid_at)!=int(bid) or int(bid_qty)<=0):
        return None
    qty=int(bid_qty)
    return {'bid':int(bid),'bid_qty':qty,'at':now,'exchange_at':depth_at,
        'source':'KIWOOM_REAL_0D_KRX_PAPER','depth':VerifiedDepth(code,bid,qty,depth_at,book)}

def _rest_bid(runner,code,now):
    if not _krx_session(now):
        raise ValueError('FRESH_KRX_BOOK_REQUIRED_MARKET_CLOSED')
    owner_api=getattr(runner,'api',None)
    if owner_api is None:
        raise ValueError('KIWOOM_REST_NOT_AUTHENTICATED')
    from d_live import ReadOnlyMarketAPI
    from at_close_bridge import wait_market_rest_slot
    # ONE explicit read per confirmed user request. Never loop/subscribe or
    # let the REST result drive automatic strategy orders.
    wait_market_rest_slot()
    api=ReadOnlyMarketAPI(owner_api.key,owner_api.secret)
    data=api.post(REST_API,REST_PATH,{'stk_cd':code})
    if not isinstance(data,dict):raise ValueError('KA10004_RESPONSE_NOT_OBJECT')
    returned=str(data.get('stk_cd') or code).strip()
    if returned.startswith('A') and len(returned)==7:returned=returned[1:]
    if returned!=code:raise ValueError('KA10004_SYMBOL_MISMATCH')
    result=data.get('return_code')
    if result is not None and str(result) not in ('0','00'):
        raise ValueError('KA10004_REJECTED_'+str(result)[:12])
    raw_clock=str(data.get('bid_req_base_tm') or '').strip()
    if not re.fullmatch(r'\d{6}',raw_clock):
        raise ValueError('KA10004_EXCHANGE_TIME_MISSING')
    try:stamp=dt.datetime.combine(now.date(),dt.time(int(raw_clock[:2]),
        int(raw_clock[2:4]),int(raw_clock[4:6])),tzinfo=KST)
    except ValueError as exc:
        raise ValueError('KA10004_EXCHANGE_TIME_INVALID') from exc
    age=(dt.datetime.now(KST)-stamp).total_seconds()
    if not -.5<=age<=3:
        raise ValueError('KA10004_EXCHANGE_BOOK_STALE')
    bid=_num(data.get('buy_fpr_bid'))
    ask=_num(data.get('sel_fpr_bid'))
    qty=_num(data.get('buy_fpr_req'))
    ask_qty=_num(data.get('sel_fpr_req'))
    if bid<=0 or ask<bid or ask_qty<=0 or qty<=0:
        raise ValueError('KA10004_BID_DEPTH_INVALID')
    captured=dt.datetime.now(KST)
    return {'bid':bid,'bid_qty':qty,'at':captured,'exchange_at':stamp,
        'source':'KIWOOM_REST_KA10004_KRX_PAPER',
        'depth':VerifiedDepth(code,bid,qty,captured)}

def verified_stock_bid(rt,code):
    if not (isinstance(code,str) and len(code)==6 and code.isdigit()):
        raise ValueError('STOCK_SYMBOL_INVALID')
    runner=getattr(rt,'stock_runner',None)
    if runner is None:raise ValueError('STOCK_FEED_NOT_READY')
    now=dt.datetime.now(KST)
    if not _krx_session(now):
        raise ValueError('FRESH_KRX_BOOK_REQUIRED_MARKET_CLOSED')
    live=_live_ws_bid(runner,code,now)
    if live:return live
    return _rest_bid(runner,code,now)

def _nxt_ws_bid(runner,code,now):
    """0.2.6: live NXT pre-market 0D bid (08:00-08:30 in this engine), WS only."""
    from d_v19_portfolio import live_session
    if not live_session(now,'NXT'):return None
    book=getattr(getattr(runner,'engine',None),'book',None)
    if book is None or not getattr(runner,'_confirmed_ws_session',False):return None
    with book._lock:
        quote=book._items.get((code,'NXT'))
        depth=book._bid_depth.get((code,'NXT'))
    if not quote or not depth:return None
    ask,bid,ask_qty,quote_at=quote
    bid_at,bid_qty,depth_at=depth
    if (quote_at.tzinfo is None or depth_at.tzinfo is None
        or not -.5<=(now-quote_at).total_seconds()<=3
        or not -.5<=(now-depth_at).total_seconds()<=3
        or not 0<int(bid)<=int(ask)
        or int(bid_at)!=int(bid) or int(bid_qty)<=0):
        return None
    return {'bid':int(bid),'bid_qty':int(bid_qty),'at':now,'exchange_at':depth_at,
        'source':'KIWOOM_REAL_0D_NXT_PREMARKET_PAPER',
        'depth':VerifiedDepth(code,bid,int(bid_qty),depth_at,book,venue='NXT')}

def _bid_for(rt,request):
    """KRX for every user sell; NXT only for the automatic pre-market exit."""
    if str(request.get('venue') or 'KRX')=='NXT':
        if not _auto(request):raise ValueError('NXT_MANUAL_EXIT_NOT_AUTHORIZED')
        runner=getattr(rt,'stock_runner',None)
        if runner is None:raise ValueError('STOCK_FEED_NOT_READY')
        live=_nxt_ws_bid(runner,request['code'],dt.datetime.now(KST))
        if live is None:raise ValueError('FRESH_NXT_PREMARKET_BOOK_MISSING')
        return live
    return verified_stock_bid(rt,request['code'])

def manual_stock_at(rt,request):
    runner=rt.stock_runner
    engine=getattr(runner,'engine',None)
    if engine is None:raise ValueError('STOCK_AT_NOT_READY')
    key=_request_key(request)
    from d_engine import charge,SELL_FEE,SELL_TAX
    with engine.store.lock,engine.store.connect() as c:
        prior=c.execute('SELECT * FROM executions WHERE event_key=?',(key,)).fetchone()
        if prior:
            if int(prior['position_id'])!=request['position_id'] or prior['code']!=request['code']:
                raise ValueError('STOCK_MANUAL_UUID_POSITION_MISMATCH')
            return {'already_executed':True,'qty':int(prior['qty']),
                    'price':int(prior['price']),'quote_source':str(prior['fill_method']),
                    'status':'FILLED'}
        p=c.execute('SELECT * FROM positions WHERE id=?',
                    (request['position_id'],)).fetchone()
        if p is None or p['status']!='OPEN' or int(p['qty'])<=0 or p['code']!=request['code']:
            raise ValueError('STOCK_AT_POSITION_NOT_OPEN_OR_MISMATCH')
        _intended(p['qty'],request)
    # Never hold the trading engine's SQLite lock while doing HTTP.
    book=_bid_for(rt,request)
    with engine.store.lock,engine.store.connect() as c:
        prior=c.execute('SELECT * FROM executions WHERE event_key=?',(key,)).fetchone()
        if prior:
            if int(prior['position_id'])!=request['position_id'] or prior['code']!=request['code']:
                raise ValueError('STOCK_MANUAL_UUID_POSITION_MISMATCH')
            return {'already_executed':True,'qty':int(prior['qty']),
                    'price':int(prior['price']),'status':'FILLED'}
        p=c.execute("SELECT * FROM positions WHERE id=? AND code=? AND status='OPEN'",
                    (request['position_id'],request['code'])).fetchone()
        if p is None:raise ValueError('STOCK_AT_POSITION_CHANGED_RECONFIRM')
        requested=_intended(p['qty'],request)
        now=dt.datetime.now(KST)
        qty=min(requested,book['depth'].available_sell(request['code'],'KRX',
                     now,book['bid'],requested))
        if qty<1 or not book['depth'].reserve_sell(request['code'],'KRX',now,book['bid'],qty):
            raise ValueError('STOCK_MANUAL_BID_DEPTH_MISSING')
        at=now.strftime('%Y%m%d%H%M%S')
        original=getattr(engine._execution_context,'fill_method','LOCAL_1M_PRICE_TOUCH_ESTIMATE')
        engine._execution_context.fill_method='MANUAL_VERIFIED_KRX_0D_OR_KA10004_PAPER_ESTIMATE'
        try:
            label=('AUTO_'+_auto(request) if _auto(request) else
                   '사용자 가상 강제매도 '+str(_pct(request))+'%')
            after=engine._sell(c,p,at,qty,book['bid'],label,event_key=key)
        finally:engine._execution_context.fill_method=original
        proof=c.execute('SELECT * FROM executions WHERE event_key=?',(key,)).fetchone()
        if proof is None or int(proof['position_id'])!=int(p['id']):
            raise ValueError('STOCK_AT_MANUAL_SELL_NOT_RECORDED')
        if after['status']=='OPEN' and not p['first_sold']:
            from d_engine import split_quantities
            first=split_quantities(int(after['qty']))[0]
            c.execute('UPDATE positions SET first_sell_qty=? WHERE id=?',(first,p['id']))
        if after['status']=='CLOSED':
            engine.store.set_metadata('manual_last_closed_minute:'+p['tf'],at[:12],c)
        engine.store.audit(c,key,at,p['tf'],p['code'],'MANUAL_PAPER_SELL', {
            'position_id':p['id'],'request_id':request['request_id'],'qty':qty,
            'price':book['bid'],'source':book['source'],'exchange_at':book['exchange_at'].isoformat(),
            'remaining':int(after['qty']),'paper_only':True,'broker_order':False})
        return {'qty':int(proof['qty']),'requested_qty':requested,'price':int(proof['price']),
                'remaining_qty':int(after['qty']),
                'quote_source':book['source'],'status':'FILLED' if qty==requested else 'PARTIAL',
                'paper_only':True}

def _ma_databases(rt):
    # Mandatory first-boot PAPER reset removes every historic v061/v062 MA
    # wallet. The ONLY sellable MA owner is the current original v067 wallet.
    current=rt.stock_runner.ma
    if current is None:
        return []
    return [(Path(current.db),current.lock,current)]

def manual_stock_ma(rt,request):
    from stock_ma_engine import _connect,StockMAService
    reason=_reason(request)
    # New clean epoch only: scan current original v067 MA ledger for exact
    # UUID and current position; no historical epoch fallback is allowed.
    matches=[]
    for path,lock,owner in _ma_databases(rt):
        if not path.is_file():continue
        with lock,_connect(path) as c:
            prior=c.execute("""SELECT e.qty,e.price,p.code FROM executions e
                JOIN positions p ON p.id=e.position_id
                WHERE p.id=? AND p.code=? AND e.reason=? AND e.side='SELL'""",
                (request['position_id'],request['code'],reason)).fetchone()
            if prior:return {'already_executed':True,'qty':int(prior['qty']),
                             'price':int(prior['price']),'status':'FILLED'}
            p=c.execute("""SELECT id FROM positions WHERE id=? AND code=? AND
                status='OPEN' AND qty>0""",
                (request['position_id'],request['code'])).fetchone()
            if p is not None:matches.append((path,lock,owner))
    if not matches:raise ValueError('STOCK_MA_POSITION_NOT_OPEN_IN_ANY_EPOCH')
    if len(matches)>1:raise ValueError('STOCK_MA_AMBIGUOUS_EPOCH_OWNER')
    path,lock,owner=matches[0]
    book=_bid_for(rt,request)
    if owner is None:
        raise ValueError("STOCK_MA_FRESH_EPOCH_OWNER_MISSING")
    actual_owner=owner
    with lock,_connect(path) as c:
        prior=c.execute("""SELECT qty,price FROM executions WHERE
            position_id=? AND code=? AND reason=? AND side='SELL'""",
            (request['position_id'],request['code'],reason)).fetchone()
        if prior:return {'already_executed':True,'qty':int(prior['qty']),
                         'price':int(prior['price']),'status':'FILLED'}
        p=c.execute("""SELECT * FROM positions WHERE id=? AND code=?
            AND status='OPEN' AND qty>0""",(request['position_id'],request['code'])).fetchone()
        if p is None:raise ValueError('STOCK_MA_POSITION_CHANGED_RECONFIRM')
        wanted=_intended(p['qty'],request)
        now=dt.datetime.now(KST)
        sold=actual_owner._sell(c,p,wanted,book['bid'],now,reason,book['depth'])
        if not sold:raise ValueError('STOCK_MA_VERIFIED_BID_CAPACITY_MISSING')
        proof=c.execute("""SELECT qty,price FROM executions WHERE position_id=?
            AND code=? AND reason=? AND side='SELL'""",
            (request['position_id'],request['code'],reason)).fetchone()
        if proof is None:raise ValueError('STOCK_MA_SELL_FILL_NOT_RECORDED')
        return {'qty':int(proof['qty']),'price':int(proof['price']),
                'requested_qty':wanted,'remaining_qty':int(p['qty'])-int(proof['qty']),
                'quote_source':book['source'],
                'status':'FILLED' if int(proof['qty'])==wanted else 'PARTIAL',
                'paper_only':True}

def manual_stock_close(rt,request):
    from at_close_bridge import close_runner
    runner=close_runner()
    if runner is None:raise ValueError('STOCK_CLOSE_RUNNER_NOT_READY')
    reason=_reason(request)
    store=runner.store
    with store.lock,store.connect() as c:
        prior=c.execute("""SELECT qty,price FROM executions WHERE
            position_id=? AND code=? AND strategy='CLOSE' AND side='SELL' AND reason=?""",
            (request['position_id'],request['code'],reason)).fetchone()
        if prior:return {'already_executed':True,'qty':prior['qty'],
                         'price':prior['price'],'status':'FILLED'}
        p=c.execute("""SELECT * FROM positions WHERE id=? AND code=?
            AND strategy='CLOSE' AND status='OPEN'""",
            (request['position_id'],request['code'])).fetchone()
        if p is None:raise ValueError('STOCK_CLOSE_POSITION_NOT_OPEN')
        _intended(p['qty'],request)
    book=verified_stock_bid(rt,request['code'])
    with store.lock,store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        prior=c.execute("""SELECT qty,price FROM executions WHERE
            position_id=? AND code=? AND strategy='CLOSE' AND side='SELL' AND reason=?""",
            (request['position_id'],request['code'],reason)).fetchone()
        if prior:
            c.rollback()
            return {'already_executed':True,'qty':int(prior['qty']),
                    'price':int(prior['price']),'status':'FILLED'}
        p=c.execute("""SELECT * FROM positions WHERE id=? AND code=?
            AND strategy='CLOSE' AND status='OPEN'""",
            (request['position_id'],request['code'])).fetchone()
        if p is None:raise ValueError('STOCK_CLOSE_POSITION_CHANGED_RECONFIRM')
        wanted=_intended(p['qty'],request)
        now=dt.datetime.now(KST)
        qty=min(wanted,book['depth'].available_sell(
            request['code'],'KRX',now,book['bid'],wanted))
        if qty<1 or not book['depth'].reserve_sell(
                request['code'],'KRX',now,book['bid'],qty):
            raise ValueError('STOCK_CLOSE_VERIFIED_BID_CAPACITY_MISSING')
        runner.engine._sell(c,p,qty,book['bid'],reason)
        proof=c.execute("""SELECT qty,price FROM executions WHERE
            position_id=? AND code=? AND strategy='CLOSE' AND side='SELL' AND reason=?""",
            (request['position_id'],request['code'],reason)).fetchone()
        if proof is None:raise ValueError('STOCK_CLOSE_SELL_FILL_NOT_RECORDED')
        c.commit()
        return {'qty':int(proof['qty']),'price':int(proof['price']),
                'requested_qty':wanted,'remaining_qty':int(p['qty'])-int(proof['qty']),
                'quote_source':book['source'],
                'status':'FILLED' if int(proof['qty'])==wanted else 'PARTIAL',
                'paper_only':True}

def manual_stock_trend(rt,request):
    co=getattr(rt,'market_trend',None)
    if co is None:raise ValueError('STOCK_TREND_RUNTIME_NOT_READY')
    reason=_reason(request)
    with co.standalone_wallet._connect('stock') as c:
        prior=c.execute("""SELECT qty,price FROM executions WHERE
            position_id=? AND symbol=? AND reason=? AND side='SELL'""",
            (request['position_id'],request['code'],reason)).fetchone()
    if prior:return {'already_executed':True,'qty':prior['qty'],
                     'price':prior['price'],'status':'FILLED'}
    with co.lock:
        matches=[p for p in co._positions.values()
            if p.market=='stock' and p.strategy=='trend' and
            p.symbol==request['code'] and
            int(getattr(p,'position_id',-1))==request['position_id'] and p.qty>0]
    if not matches:
        matches=[p for p in co.standalone_wallet.open_positions('stock') if
            p.symbol==request['code'] and p.position_id==request['position_id'] and p.qty>0]
    if len(matches)!=1:raise ValueError('STOCK_TREND_POSITION_NOT_OPEN_OR_AMBIGUOUS')
    pos=matches[0]
    if pos.execution_mode!='PAPER':
        raise ValueError('STOCK_TREND_NON_SIMULATED_POSITION')
    if Decimal(str(pos.qty))!=Decimal(str(request['expected_qty'])):
        raise ValueError('STOCK_TREND_QTY_CHANGED_RECONFIRM')
    book=_bid_for(rt,request)
    now=dt.datetime.now(KST)
    fraction=float(_pct(request)/Decimal(100))
    target=int(pos.qty) if fraction>=.999 else max(1,int(round(float(pos.qty)*fraction)))
    allocation=book['depth'].available_sell(request['code'],'KRX',now,book['bid'],target)
    if allocation<=0 or not book['depth'].reserve_sell(
            request['code'],'KRX',now,book['bid'],allocation):
        raise ValueError('STOCK_TREND_VERIFIED_BID_DEPTH_MISSING')
    # Use the original MT54 order lock and recheck the authoritative
    # persisted position AFTER network IO, before calling the only
    # native order/ledger owner. Never rely only on a UI/in-memory qty.
    with co.standalone_wallet.order_lock:
        with co.standalone_wallet._connect('stock') as c:
            current=c.execute("""SELECT qty,execution_mode FROM positions
                WHERE id=? AND symbol=? AND status='TREND_OPEN'""",
                (request['position_id'],request['code'])).fetchone()
            if (current is None or current['execution_mode']!='PAPER'
                or Decimal(str(current['qty']))!=
                   Decimal(str(request['expected_qty']))):
                raise ValueError('STOCK_TREND_POSITION_CHANGED_RECONFIRM')
        sold,result=co.standalone_wallet.sell(pos,book['bid'],now,
            reason,bid_size=allocation,manual_fraction=fraction,
            verified_manual_bid_qty=allocation)
    if not sold:raise ValueError('STOCK_TREND_MANUAL_SELL_'+str(result))
    with co.standalone_wallet._connect('stock') as c:
        proof=c.execute("""SELECT qty,price FROM executions WHERE position_id=?
            AND symbol=? AND reason=? AND side='SELL'""",
            (request['position_id'],request['code'],reason)).fetchone()
    if proof is None:raise ValueError('STOCK_TREND_MANUAL_SELL_UNVERIFIED')
    return {'qty':proof['qty'],'price':proof['price'],
            'quote_source':book['source'],'status':'FILLED','paper_only':True}

def manual_stock_gmo(rt,request):
    service=rt.stock_runner.gmo
    if service is None:raise ValueError('STOCK_GMO_NOT_READY')
    reason=_reason(request)
    with service._lock,service.engine._lock:
        prior=next((o for o in service.engine.orders.values() if
            o.side=='SELL' and o.code==request['code'] and
            o.purpose==reason),None)
        if prior is not None:
            # A confirmed UUID belongs to its ORIGINAL intent even after
            # a partial or full fill. Never quote or submit it twice.
            records=[f for f in service.engine.fills
                     if f.get('order_id')==prior.id]
            verified=sum(int(f.get('qty',0)) for f in records)
            if verified!=int(prior.filled):
                raise ValueError('STOCK_GMO_EXISTING_FILL_LEDGER_MISMATCH')
            status=('FILLED' if prior.state=='FILLED' else
                    'PARTIAL_PENDING' if verified>0 else
                    'PENDING_REAL_KRX_BOOK')
            response={'already_executed':True,'qty':verified,
                'status':status,'order_id':prior.id,'paper_only':True}
            if records:response['price']=records[-1]['price']
            return response
        p=service.engine.positions.get(request['code'])
        if p is None or p.qty<=0:raise ValueError('STOCK_GMO_POSITION_NOT_OPEN')
        if int(p.qty)!=int(Decimal(str(request['expected_qty']))):
            raise ValueError('STOCK_GMO_QTY_CHANGED_RECONFIRM')
        if p.pending_sell_id and (prior is None or p.pending_sell_id!=prior.id):
            raise ValueError('STOCK_GMO_EXISTING_SELL_PENDING')
    book=verified_stock_bid(rt,request['code'])
    with service._lock,service.engine._lock:
        p=service.engine.positions.get(request['code'])
        prior=next((o for o in service.engine.orders.values() if
            o.side=='SELL' and o.code==request['code'] and
            o.purpose==reason),None)
        if prior is None:
            if p is None or p.qty<=0:raise ValueError('STOCK_GMO_POSITION_CHANGED_RECONFIRM')
            if int(p.qty)!=int(Decimal(str(request['expected_qty']))):
                raise ValueError('STOCK_GMO_QTY_CHANGED_RECONFIRM')
            if p.pending_sell_id:raise ValueError('STOCK_GMO_EXISTING_SELL_PENDING')
            from projectgmo.prices import down_tick
            wanted=_intended(p.qty,request)
            prior=service.engine._exit(p,reason,wanted,book['bid'],dt.datetime.now(KST).isoformat())
            if prior is None:raise ValueError('STOCK_GMO_MANUAL_INTENT_REJECTED')
        outstanding=int(prior.requested)-int(prior.filled)
        now=dt.datetime.now(KST)
        qty=min(outstanding,book['depth'].available_sell(
            request['code'],'KRX',now,book['bid'],outstanding))
        if qty<=0:
            service._persist(True)
            return {'status':'PENDING_REAL_KRX_BOOK','qty_requested':outstanding,
                    'order_id':prior.id,'paper_only':True}
        if not book['depth'].reserve_sell(request['code'],'KRX',now,book['bid'],qty):
            service._persist(True)
            return {'status':'PENDING_REAL_KRX_BOOK','order_id':prior.id,'paper_only':True}
        # The original GMO engine and wallet calculate fees, tax, cost basis
        # and partial/FILLED status. This is explicitly a PAPER best-bid
        # depth estimate, NEVER an actual Kiwoom exchange SELL execution.
        service.engine.fill(prior.id,qty,book['bid'],now.isoformat())
        service._persist(True)
        recorded=next((r for r in reversed(service.engine.fills)
            if r.get('order_id')==prior.id),None)
        if recorded is None:raise ValueError('STOCK_GMO_MANUAL_FILL_NOT_RECORDED')
        return {'qty':qty,'price':recorded['price'],'order_id':prior.id,
                'quote_source':book['source'],
                'status':'FILLED' if prior.state=='FILLED' else 'PARTIAL_PENDING',
                'paper_only':True}
