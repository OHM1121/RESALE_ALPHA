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
from typing import Any

KST=dt.timezone(dt.timedelta(hours=9))
FRESH_SECONDS=10.0
MAX_EXCHANGE_TO_RECEIPT_SECONDS=3.0
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
        or delivery_lag > MAX_EXCHANGE_TO_RECEIPT_SECONDS):
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
            'is_fresh':max(recv_age,exchange_age)<=FRESH_SECONDS}


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
    original=runner._new_tick

    def quote_only_then_native(tick):
        try:
            _observe(runner,tick)
        except (AttributeError,TypeError,ValueError,OverflowError):
            pass
        return original(tick)

    runner._new_tick=quote_only_then_native
    runner._v066_quote_tap_enabled=True
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
        if (lag<-0.5 or lag>MAX_EXCHANGE_TO_RECEIPT_SECONDS
            or recv_age<-0.5 or exchange_age<-0.5):
            continue
        point=dict(event)
        point['is_fresh']=(bool(feed_connected)
                            and max(recv_age,exchange_age)<=FRESH_SECONDS)
        items.append(point)
    if not items:return None,None
    valid=[e for e in items if e['is_fresh']]
    # During KRX trading, prefer its exact official trade over a possible
    # NXT source. Never silently construct a cross-venue blended price.
    valid.sort(key=lambda e:(e['venue']=='KRX',e['exchange_ms']),reverse=True)
    stale=max(items,key=lambda e:e['exchange_ms'])
    return valid[0] if valid else None,stale


def _purge_stale_fields(row):
    # Only the returned UI row changes; the original SQLite and broker
    # event history are untouched. Retain the last ledger valuation separately.
    for field in ('last_price','mark','current_price','market_price',
                  'mark_price','paper_pnl','unrealized_pnl','net_pnl','pnl'):
        if field in row:
            row.setdefault('ledger_'+field,row[field])
            row.pop(field,None)


def _annotate_position(row,quotes,now,connected):
    if not isinstance(row,dict):
        return row
    state=str(row.get('status') or 'OPEN').upper()
    if state not in _VALID_STOCK_STATES:
        return row
    code=_stock_code(row.get('code') or row.get('symbol'))
    row['stock_quote_checked_v066']=True
    row['stock_quote_code']=code
    if not code:
        _purge_stale_fields(row)
        row['stock_quote_status']='SYMBOL_MISSING'
        row['stock_quote_source']='NO_VERIFIED_0B'
        return row
    fresh,last=_choose_for_code(quotes,code,now,connected)
    _purge_stale_fields(row)
    if fresh is not None:
        row['last_price']=fresh['price']
        row['current_price']=fresh['price']
        row['stock_quote_status']='FRESH'
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
        row['stock_quote_status']='STALE' if last else 'NOT_RECEIVED'
        row['stock_quote_source']='NO_FRESH_VERIFIED_0B'
        row['stock_quote_expires_ms']=0
        if last:
            row['stock_quote_venue']=last['venue']
            row['stock_quote_exchange_at']=last['exchange_at']
            row['stock_quote_received_at']=last['received_at']
            row['stock_quote_last_observed_price']=last['price']
    return row


def _mark_ledger(ledger,quotes,now,connected,visited):
    if not isinstance(ledger,dict):return
    token=id(ledger)
    if token in visited:return
    visited.add(token)
    rows=ledger.get('positions')
    if isinstance(rows,list):
        for row in rows:
            _annotate_position(row,quotes,now,connected)
    for key in ('ma_subledger','ma','gmo','wallet','wallets','trend'):
        nested=ledger.get(key)
        if isinstance(nested,dict):
            if key=='wallets':
                for val in nested.values():
                    _mark_ledger(val,quotes,now,connected,visited)
            else:
                _mark_ledger(nested,quotes,now,connected,visited)


def annotate_stock_packet(packet,runner,now=None):
    """Read-only positions display overlay for AT, MA, GMO, CLOSE and Trend."""
    if not isinstance(packet,dict):return packet
    now=now or dt.datetime.now(KST)
    if now.tzinfo is None:raise ValueError('aware snapshot clock required')
    stock=packet.get('stock')
    if not isinstance(stock,dict):return packet
    connected=bool(getattr(runner,'_confirmed_ws_session',False))
    lock=getattr(runner,'_v066_quote_lock',None)
    if lock is not None:
        with lock:
            quotes=dict(getattr(runner,'_v066_quote_by_venue',{}) or {})
    else:
        quotes={}
    visited=set()
    observed={}
    for strategy in ('at','ma','gmo','cw','close','trend'):
        item=stock.get(strategy)
        if not isinstance(item,dict):continue
        ledger=item.get('ledger')
        if not isinstance(ledger,dict):continue
        _mark_ledger(ledger,quotes,now,connected,visited)
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
    stock['display_quote_diagnostic_v066']={
        'connected':connected,'captured_stock_venue_pairs':len(quotes),
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
