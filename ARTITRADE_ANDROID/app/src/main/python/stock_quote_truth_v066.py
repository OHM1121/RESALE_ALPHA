"""v066 display-only Kiwoom stock quote provenance.

This module never changes an order decision, orderbook depth, positions,
SQLite or performance. It passively observes already-subscribed 0B stock
trade prints on the ONE original Kiwoom socket, with their source/venue/time.
Only a fresh, same-symbol, correctly timestamped trade may be called
'현재가'. An old ledger value must never be silently shown as live.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from typing import Any

KST=dt.timezone(dt.timedelta(hours=9))
FRESH_SECONDS=10.0
MAX_EXCHANGE_TO_RECEIPT_SECONDS=3.0  # trading freshness stays with original broker
DISPLAY_MAX_RECEIPT_LAG_SECONDS=120.0  # genuine delayed packets are display-only
REST_REFRESH_SECONDS=35.0
REST_DISPLAY_FRESH_SECONDS=60.0
MAX_TRACKED=650
MAX_AGE_FOR_AUDIT_SECONDS=86400.0
_VALID_STOCK_STATES=frozenset(('OPEN','HOLD','HOLDING','TREND_OPEN','ACTIVE'))


def _aware(value):
    if isinstance(value,dt.datetime):
        return value if value.tzinfo is not None else None
    try:
        parsed=dt.datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError,ValueError,OverflowError):
        return None


def _stock_code(raw):
    code=str(raw or '').strip()
    if len(code)==7 and code.startswith('A') and code[1:].isdigit():
        code=code[1:]
    return code if len(code)==6 and code.isdigit() else ''


def _verify_event(tick, now):
    """Validate a genuine parsed 0B receipt. No price inference or fake ticks."""
    if not isinstance(tick,(tuple,list)) or len(tick)<9:
        return None
    code=_stock_code(tick[0])
    if not code:return None
    venue=str(tick[7])
    if venue not in ('KRX','NXT'):return None
    value=tick[4]
    if isinstance(value,bool):return None
    try:
        price=int(value)
        if price<=0:return None
    except (ValueError,TypeError,OverflowError):
        return None
    received=_aware(tick[6])
    exchange=_aware(tick[8])
    if received is None or exchange is None or now.tzinfo is None:
        return None
    recv_age=(now-received).total_seconds()
    exchange_age=(now-exchange).total_seconds()
    delivery_lag=(received-exchange).total_seconds()
    if (recv_age < -0.5 or exchange_age < -0.5
        or delivery_lag < -0.5
        or delivery_lag > DISPLAY_MAX_RECEIPT_LAG_SECONDS):
        return None
    if recv_age > MAX_AGE_FOR_AUDIT_SECONDS or exchange_age > MAX_AGE_FOR_AUDIT_SECONDS:
        return None
    end=min(received.timestamp(),exchange.timestamp())+FRESH_SECONDS
    return {'code':code,'venue':venue,'price':price,
            'received_at':received.isoformat(timespec='seconds'),
            'exchange_at':exchange.isoformat(timespec='seconds'),
            'received_ms':int(received.timestamp()*1000),
            'exchange_ms':int(exchange.timestamp()*1000),
            'expires_ms':int(end*1000),
            'is_fresh':(max(recv_age,exchange_age)<=FRESH_SECONDS
                        and delivery_lag<=MAX_EXCHANGE_TO_RECEIPT_SECONDS)}


def _observe(runner,tick):
    now=dt.datetime.now(KST)
    event=_verify_event(tick,now)
    if event is None:return
    lock=getattr(runner,'_v066_quote_lock',None)
    if lock is None:return
    with lock:
        cache=runner._v066_quote_by_venue
        key=(event['code'],event['venue'])
        previous=cache.get(key)
        # Out-of-order or replayed exchange ticks must not replace a newer
        # proven print, even if the packet was received more recently.
        if previous is None or event['exchange_ms']>previous['exchange_ms'] or (
            event['exchange_ms']==previous['exchange_ms']
            and event['received_ms']>previous['received_ms']):
            cache[key]=event
        if len(cache)>MAX_TRACKED:
            keep=sorted(cache.items(),key=lambda pair:pair[1]['exchange_ms'],reverse=True)[:MAX_TRACKED-50]
            runner._v066_quote_by_venue=dict(keep)


def install_ui_quote_tap(runner):
    """Idempotent, low-latency observer. Always calls ORIGINAL strategy tick."""
    if runner is None:return False
    if getattr(runner,'_v066_quote_tap_enabled',False):
        return True
    lock=threading.RLock()
    runner._v066_quote_lock=lock
    runner._v066_quote_by_venue={}
    # Test/migration stubs can expose a snapshot without an active Kiwoom
    # 0B receiver. A display-only observer must not fail the whole strategy.
    original=getattr(runner,'_new_tick',None)
    if not callable(original):return False

    def quote_only_then_native(tick):
        try:
            _observe(runner,tick)
        except (AttributeError,TypeError,ValueError,OverflowError):
            pass
        return original(tick)

    runner._new_tick=quote_only_then_native
    runner._v066_quote_tap_enabled=True
    # Start source-separated price retrieval at runner creation, not only when
    # the UI eventually polls its large consolidated ledger snapshot.
    _ensure_rest_lookup_started(runner)
    return True


def _choose_for_code(by_venue,code,now,feed_connected):
    items=[]
    for venue in ('KRX','NXT'):
        event=by_venue.get((code,venue))
        if not isinstance(event,dict):continue
        updated=_aware(event.get('received_at'))
        exchanged=_aware(event.get('exchange_at'))
        if updated is None or exchanged is None:continue
        lag=(updated-exchanged).total_seconds()
        recv_age=(now-updated).total_seconds()
        exchange_age=(now-exchanged).total_seconds()
        if (lag<-0.5 or lag>DISPLAY_MAX_RECEIPT_LAG_SECONDS
            or recv_age<-0.5 or exchange_age<-0.5):
            continue
        point=dict(event)
        point['is_fresh']=(bool(feed_connected)
                            and lag<=MAX_EXCHANGE_TO_RECEIPT_SECONDS
                            and max(recv_age,exchange_age)<=FRESH_SECONDS)
        items.append(point)
    if not items:return None,None
    valid=[e for e in items if e['is_fresh']]
    # During KRX trading, prefer its exact official trade over a possible
    # NXT source. Never silently construct a cross-venue blended price.
    valid.sort(key=lambda e:(e['venue']=='KRX',e['exchange_ms']),reverse=True)
    stale=max(items,key=lambda e:e['exchange_ms'])
    return valid[0] if valid else None,stale


def _rest_positive_price(value):
    # Kiwoom cur_prc can be signed; sign denotes direction, not negative price.
    if value is None or isinstance(value,bool):return None
    try:
        p=int(abs(float(str(value).strip().replace(',',''))))
    except (TypeError,ValueError,OverflowError):return None
    return p if p>0 else None


def _held_rest_display_codes(runner):
    """Read-only actual holdings. Research watchlists never consume REST slots."""
    held=set()
    def collect(items):
        for item in items or ():
            code=_stock_code(item.get('code') if isinstance(item,dict) else item)
            if code:held.add(code)
    try:collect(getattr(runner,'_paper_interest_held',()) or ())
    except (AttributeError,ValueError,TypeError):pass
    try:
        with runner.store.connect() as c:
            collect([r[0] for r in c.execute(
                "SELECT DISTINCT code FROM positions WHERE status='OPEN' AND qty>0")])
    except Exception:pass
    try:collect(runner.ma.pinned_stocks())
    except Exception:pass
    try:collect(runner.gmo.holding_codes())
    except Exception:pass
    try:
        from at_close_bridge import close_runner
        close=close_runner()
        if close is not None:collect(close.store.open_positions('CLOSE'))
    except Exception:pass
    try:
        import android_live_bridge as core
        runtime=getattr(core,'_RUNTIME',None)
        trend=getattr(runtime,'market_trend',None)
        if trend is not None:
            with trend.lock:
                collect([p.symbol for p in trend._positions.values()
                         if p.market=='stock' and p.qty>0])
    except Exception:pass
    return held


def _rest_quote_loop(runner):
    """Display-only prefetch: restore held symbols BEFORE first UI poll.

    Uses an independent HTTP Session/lock (but the original shared REST rate
    limiter and token), so historical OHLCV downloads cannot monopolize the
    screen's official quote fetch. NEVER supplies the trading decision path.
    """
    lock=runner._v066_quote_lock
    display_api=None
    last_pin_scan=0.0
    while not runner.stop_event.is_set():
        if time.monotonic()-last_pin_scan>2.0:
            last_pin_scan=time.monotonic()
            try:
                held=_held_rest_display_codes(runner)
                with lock:
                    runner._v073_current_pins=held
            except (AttributeError,TypeError,RuntimeError,ValueError):
                pass
        with lock:
            codes=sorted(set(getattr(runner,'_v072_quote_requested',set())) |
                         set(getattr(runner,'_v073_current_pins',set())))
            attempted=runner._v072_quote_attempts
            now=time.monotonic()
            code=next((c for c in codes
                       if now-attempted.get(c,0)>=REST_REFRESH_SECONDS),None)
            if code is not None:attempted[code]=now
        if code is None:
            runner.stop_event.wait(.6)
            continue
        try:
            owner_api=getattr(runner,'api',None)
            if owner_api is None:
                # Runner was created before Kiwoom credentials/API startup.
                # Do not impose the normal 35s retry on a price lookup that
                # has not even attempted an HTTP request yet.
                with lock:runner._v072_quote_attempts.pop(code,None)
                runner.stop_event.wait(.4)
                continue
            if display_api is None:
                factory=getattr(runner,'_v073_display_api_factory',None)
                if factory is not None:
                    display_api=factory(owner_api)  # synthetic integration tests
                else:
                    from d_live import ReadOnlyMarketAPI
                    display_api=ReadOnlyMarketAPI(owner_api.key,owner_api.secret)
            reply=display_api.post('ka10001','/api/dostk/stkinfo',{'stk_cd':code})
            if not isinstance(reply,dict):raise ValueError('KIWOOM_REST_PAYLOAD_INVALID')
            returned=_stock_code(reply.get('stk_cd'))
            if returned and returned!=code:raise ValueError('KIWOOM_REST_CODE_MISMATCH')
            price=_rest_positive_price(reply.get('cur_prc'))
            if price is None:raise ValueError('KIWOOM_REST_PRICE_INVALID')
            observed=dt.datetime.now(KST)
            with lock:
                runner._v072_rest_quotes[code]={
                    'price':price,'received_at':observed.isoformat(timespec='seconds'),
                    'received_ms':int(observed.timestamp()*1000),
                    'source':'KIWOOM_REST_KA10001'}
                runner._v072_rest_errors.pop(code,None)
        except Exception as exc:
            with lock:
                runner._v072_rest_errors[code]=type(exc).__name__+': '+str(exc)[:100]
        runner.stop_event.wait(2.0)


def _ensure_rest_lookup_started(runner):
    if runner is None or not hasattr(runner,'stop_event'):return
    if getattr(runner,'_v072_rest_lookup_started',False):return
    lock=getattr(runner,'_v066_quote_lock',None)
    if lock is None:return
    with lock:
        if getattr(runner,'_v072_rest_lookup_started',False):return
        runner._v072_quote_requested=set()
        runner._v073_current_pins=set()
        runner._v072_quote_attempts={}
        runner._v072_rest_quotes={}
        runner._v072_rest_errors={}
        runner._v072_rest_lookup_started=True
        runner._v072_rest_thread=threading.Thread(
            target=_rest_quote_loop,args=(runner,),daemon=True,
            name='ARTITRADE_V072_HOLDINGS_REST_DISPLAY_ONLY')
        runner._v072_rest_thread.start()


def live_quote_snapshot(runner):
    """Small read-only market-price plane separate from multi-MB SQLite UI.

    0B/0D trading queues must never wait for a large dashboard or REST lookup.
    Every quoted symbol is an existing held/pinned code.  Source/time remain
    explicit: REST 35-second polling is NOT an exchange streaming trade.
    """
    if runner is None:return {'quotes':{},'revision':''}
    lock=getattr(runner,'_v066_quote_lock',None)
    if lock is None:return {'quotes':{},'revision':''}
    with lock:
        events=dict(getattr(runner,'_v066_quote_by_venue',{}) or {})
        rest=dict(getattr(runner,'_v072_rest_quotes',{}) or {})
        pins=set(getattr(runner,'_v073_current_pins',set()) or ())
    now=dt.datetime.now(KST)
    connected=bool(getattr(runner,'_confirmed_ws_session',False))
    quotes={}
    for code in sorted(pins)[:200]:
        code=_stock_code(code)
        if not code:continue
        fresh,last=_choose_for_code(events,code,now,connected)
        item=None
        if fresh is not None:
            item={'price':fresh['price'],'status':'FRESH',
                  'source':'KIWOOM_REAL_0B',
                  'venue':fresh['venue'],
                  'exchange_at':fresh['exchange_at'],
                  'received_at':fresh['received_at'],
                  'expires_ms':fresh['expires_ms']}
        else:
            snapshot=rest.get(code)
            rest_price=(_rest_positive_price(snapshot.get('price'))
                        if isinstance(snapshot,dict) else None)
            rest_at=(_aware(snapshot.get('received_at'))
                     if isinstance(snapshot,dict) else None)
            age=(now-rest_at).total_seconds() if rest_at else None
            if rest_price is not None and age is not None and -.5<=age<=REST_DISPLAY_FRESH_SECONDS:
                item={'price':rest_price,'status':'REST_FRESH',
                      'source':'KIWOOM_REST_KA10001','venue':'',
                      'exchange_at':'','received_at':snapshot['received_at'],
                      'expires_ms':0}
            elif last is not None:
                item={'price':last['price'],'status':'LAST_0B',
                      'source':'KIWOOM_REAL_0B_LAST','venue':last['venue'],
                      'exchange_at':last['exchange_at'],
                      'received_at':last['received_at'],'expires_ms':0}
            elif rest_price is not None and age is not None and age>=0:
                item={'price':rest_price,'status':'LAST_REST',
                      'source':'KIWOOM_REST_KA10001_LAST','venue':'',
                      'exchange_at':'','received_at':snapshot['received_at'],
                      'expires_ms':0}
        if item is not None:
            quotes[code]=item
    # Signature changes for a new REAL price, a REST refresh or a freshness
    # status transition, not merely because the heartbeat timer advanced.
    signature='|'.join(code+':'+str(v['price'])+':'+v['status']+':'+
                       v['received_at'] for code,v in sorted(quotes.items()))
    return {'quotes':quotes,'revision':signature,
            'source':'ORIGINAL_KIWOOM_0B_OR_OFFICIAL_KA10001',
            'updated_ms':int(now.timestamp()*1000),
            'active_source_count':len(quotes)}


def _purge_stale_fields(row):
    # Only the returned UI row changes; the original SQLite and broker
    # event history are untouched. Retain the last ledger valuation separately.
    for field in ('last_price','mark','current_price','market_price',
                  'mark_price','paper_pnl','unrealized_pnl','net_pnl','pnl'):
        if field in row:
            row.setdefault('ledger_'+field,row[field])
            row.pop(field,None)


def _annotate_position(row,quotes,now,connected,rest_cache=None,needed_codes=None):
    if not isinstance(row,dict):
        return row
    state=str(row.get('status') or 'OPEN').upper()
    if state not in _VALID_STOCK_STATES:
        return row
    code=_stock_code(row.get('code') or row.get('symbol'))
    if code and needed_codes is not None:needed_codes.add(code)
    row['stock_quote_checked_v066']=True
    row['stock_quote_code']=code
    if not code:
        _purge_stale_fields(row)
        row['stock_quote_status']='SYMBOL_MISSING'
        row['stock_quote_source']='NO_VERIFIED_0B'
        return row
    fresh,last=_choose_for_code(quotes,code,now,connected)
    rest=(rest_cache or {}).get(code)
    rest_price=_rest_positive_price(rest.get('price')) if isinstance(rest,dict) else None
    rest_at=_aware(rest.get('received_at')) if isinstance(rest,dict) else None
    rest_age=(now-rest_at).total_seconds() if rest_at is not None else None
    if rest_age is not None and rest_age<-0.5:rest_price=None
    _purge_stale_fields(row)
    if fresh is not None:
        row['last_price']=fresh['price']
        row['current_price']=fresh['price']
        row['stock_quote_status']='FRESH'
        row['stock_quote_display_price']=fresh['price']
        row['stock_quote_display_source']='LIVE_0B'
        row['stock_quote_source']='KIWOOM_REAL_0B'
        row['stock_quote_venue']=fresh['venue']
        row['stock_quote_exchange_at']=fresh['exchange_at']
        row['stock_quote_received_at']=fresh['received_at']
        row['stock_quote_expires_ms']=fresh['expires_ms']
        # Do not claim the old strategy ledger's PnL is refreshed; present
        # a separately named market-value estimate, without broker fees.
        try:
            entry=float(row.get('entry_price') or row.get('entry'))
            qty=float(row.get('qty') or row.get('quantity'))
            if entry>0 and qty>0:
                row['stock_quote_unrealized_estimate']=(fresh['price']-entry)*qty
        except (TypeError,ValueError,OverflowError):
            pass
    else:
        # A quiet stock is not a dead connection. Present the last verified
        # exchange/official REST price with provenance, NEVER as fresh WS.
        row['stock_quote_expires_ms']=0
        if rest_price is not None and rest_age is not None and rest_age<=REST_DISPLAY_FRESH_SECONDS:
            row['stock_quote_status']='REST_FRESH'
            row['stock_quote_source']='KIWOOM_REST_KA10001'
            row['stock_quote_display_source']='REST_KA10001'
            row['stock_quote_display_price']=rest_price
            row['stock_quote_received_at']=rest['received_at']
            row['stock_quote_rest_age_sec']=round(rest_age,1)
        elif last is not None:
            row['stock_quote_status']='LAST_0B'
            row['stock_quote_source']='KIWOOM_REAL_0B_LAST'
            row['stock_quote_display_source']='LAST_0B'
            row['stock_quote_display_price']=last['price']
            row['stock_quote_venue']=last['venue']
            row['stock_quote_exchange_at']=last['exchange_at']
            row['stock_quote_received_at']=last['received_at']
            row['stock_quote_last_observed_price']=last['price']
        elif rest_price is not None:
            row['stock_quote_status']='LAST_REST'
            row['stock_quote_source']='KIWOOM_REST_KA10001_LAST'
            row['stock_quote_display_source']='LAST_REST'
            row['stock_quote_display_price']=rest_price
            row['stock_quote_received_at']=rest['received_at']
            row['stock_quote_rest_age_sec']=round(rest_age,1) if rest_age is not None else None
        else:
            # Fast cold-start UI fallback only: preserve the original database
            # mark as explicitly OLD/STORED, not as a live quote or a SELL bid.
            # Broker fill decisions never read stock_quote_display_price.
            saved=None
            for key in ('ledger_last_price','ledger_current_price',
                        'ledger_market_price','ledger_mark','ledger_mark_price'):
                saved=_rest_positive_price(row.get(key))
                if saved is not None:break
            if saved is not None:
                row['stock_quote_status']='LAST_LEDGER'
                row['stock_quote_source']='STORED_LEDGER_MARK_NOT_LIVE'
                row['stock_quote_display_source']='LAST_LEDGER'
                row['stock_quote_display_price']=saved
            else:
                row['stock_quote_status']='NOT_RECEIVED'
                row['stock_quote_source']='NO_VERIFIED_PRICE'
    return row


def _mark_ledger(ledger,quotes,now,connected,visited,rest_cache=None,needed_codes=None):
    if not isinstance(ledger,dict):return
    token=id(ledger)
    if token in visited:return
    visited.add(token)
    rows=ledger.get('positions')
    if isinstance(rows,list):
        for row in rows:
            _annotate_position(row,quotes,now,connected,rest_cache,needed_codes)
    for key in ('ma_subledger','ma','gmo','wallet','wallets','trend'):
        nested=ledger.get(key)
        if isinstance(nested,dict):
            if key=='wallets':
                for val in nested.values():
                    _mark_ledger(val,quotes,now,connected,visited,rest_cache,needed_codes)
            else:
                _mark_ledger(nested,quotes,now,connected,visited,rest_cache,needed_codes)


def annotate_stock_packet(packet,runner,now=None):
    """Read-only positions display overlay for AT, MA, GMO, CLOSE and Trend."""
    if not isinstance(packet,dict):return packet
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None:raise ValueError('aware snapshot clock required')
    stock=packet.get('stock')
    if not isinstance(stock,dict):return packet
    connected=bool(getattr(runner,'_confirmed_ws_session',False))
    _ensure_rest_lookup_started(runner)
    lock=getattr(runner,'_v066_quote_lock',None)
    if lock is not None:
        with lock:
            quotes=dict(getattr(runner,'_v066_quote_by_venue',{}) or {})
            rest_cache=dict(getattr(runner,'_v072_rest_quotes',{}) or {})
            rest_errors=dict(getattr(runner,'_v072_rest_errors',{}) or {})
    else:
        quotes={};rest_cache={};rest_errors={}
    visited=set()
    needed=set()
    observed={}
    for strategy in ('at','ma','gmo','cw','close','trend'):
        item=stock.get(strategy)
        if not isinstance(item,dict):continue
        ledger=item.get('ledger')
        if not isinstance(ledger,dict):continue
        _mark_ledger(ledger,quotes,now,connected,visited,rest_cache,needed)
        # Record a non-invasive strategy-level diagnostics summary.
        codes=[]
        for pos in ledger.get('positions') or []:
            if isinstance(pos,dict) and pos.get('stock_quote_checked_v066'):
                codes.append((pos.get('stock_quote_code'),pos.get('stock_quote_status')))
        item['display_quote_diagnostic_v066']={
            'codes':len(codes),
            'fresh':sum(1 for _,v in codes if v=='FRESH'),
            'asof':now.isoformat(timespec='seconds'),
            'only_ui':True}
        observed[strategy]=item['display_quote_diagnostic_v066']
    if lock is not None and hasattr(runner,'_v072_quote_requested'):
        with lock:
            runner._v072_quote_requested=set(needed)
    subscribed=set(str(s).replace('_NX','') for s in getattr(runner,'subscriptions',[]) or [])
    stock['display_quote_diagnostic_v066']={
        'connected':connected,'captured_stock_venue_pairs':len(quotes),
        'held_symbols':len(needed),'requested_but_unsubscribed':sorted(needed-subscribed),
        'official_rest_cached':len(rest_cache),
        'official_rest_errors':{k:rest_errors[k] for k in sorted(rest_errors)[:10]},
        'strategies':observed,'ttl_seconds':FRESH_SECONDS,
        'source':'REAL_0B_NOT_LEDGER_MARK',
        'scope':'DISPLAY_ONLY_NO_ORDERS_NO_LEDGER_WRITES'}
    return packet


def install(_core):
    old=_core.Runtime.hub_records
    if getattr(old,'_v066_stock_quote_installed',False):return

    def hub_with_verified_prices(self):
        runner=getattr(self,'stock_runner',None)
        try:
            if runner is not None:
                install_ui_quote_tap(runner)
        except (AttributeError,TypeError,RuntimeError):
            pass
        raw=old(self)
        try:
            is_string=isinstance(raw,str)
            packet=json.loads(raw) if is_string else raw
            if not isinstance(packet,dict):return raw
            result=annotate_stock_packet(packet,runner)
            return json.dumps(result,ensure_ascii=False,default=str) if is_string else result
        except (ValueError,TypeError,AttributeError,OverflowError):
            return raw
    hub_with_verified_prices._v066_stock_quote_installed=True
    _core.Runtime.hub_records=hub_with_verified_prices
