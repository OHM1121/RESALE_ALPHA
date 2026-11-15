"""v061 stock MA original KRX book/ledger overlay.

New stock MA entry kinds only: downward SMA3+ bearish -> low limit;
the first MA120-down / short4-up-below turnpoint -> next eligible KRX bar
open within 5 seconds, limited slippage <=1% from signal closing price.
All first/second/third pivot exits reuse the original stock MA confirmed
0D bid liquidity and broker FOK event interception.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
from decimal import Decimal,ROUND_CEILING
import ma_v061_rules as rule

_INSTALLED=False

def _next_stock_open(ref,tf):
    from d_storage import KST
    from datetime import timedelta
    if tf=='day':
        next_open=(ref+timedelta(days=1)).replace(hour=9,minute=0,second=0)
    else:
        seconds={'5m':300,'30m':1800,'60m':3600}[tf]
        next_open=ref+timedelta(seconds=seconds)
        if next_open.hour>15 or (next_open.hour==15 and next_open.minute>=20):
            next_open=(ref+timedelta(days=1)).replace(hour=9,minute=0,second=0)
    from d_v19_portfolio import live_session
    # The exchange session check covers non-trading public holidays as well
    # as weekends. No candidate is filled while KRX itself is closed.
    for _ in range(16):
        if next_open.weekday()<5 and live_session(next_open,'KRX'):
            break
        next_open=(next_open+timedelta(days=1)).replace(hour=9,minute=0,second=0)
    # Kiwoom's native live_session remains authoritative about holidays,
    # exchange halt and actual eligibility; this is an opening deadline.
    return next_open.astimezone(KST)

def _native_candle_candidate(m,rows,tf,when):
    from d_engine import _krx_tick
    from coin_v24_policy import body_policy
    from d_upbit import coin_diverse_priority
    from d_storage import KST
    rows=[dict(row) for row in rows]
    cur=rows[-1]
    facts=rule.candidate_from_closed(rows,tf)
    kind=facts.get('kind')
    audit={'decision':'REJECT','reason':facts.get('reason'),
           'reference':cur.get('ts'),'ma_v061_kind':kind or '',
           'ma_v061_signal':facts}
    if kind is None:return audit,None
    low=rule.dec(cur.get('low'))
    high=rule.dec(cur.get('high'))
    close=rule.dec(cur.get('close'))
    opening=rule.dec(cur.get('open'))
    if any(x is None for x in (low,high,close,opening)) or not 0<low<high:
        audit['reason']='MA_V061_INVALID_PRICE'
        return audit,None
    tick=Decimal(str(_krx_tick(int(close))))
    if tick<=0:return audit,None
    prior=rows[:-1]
    if kind=='DOWN_BEAR':
        policy=body_policy(cur,tick)
        if not policy or Decimal(str(policy['target']))<=low:
            audit['reason']='MA_V061_NATIVE_BODY_TARGET_INVALID'
            return audit,None
        target=int(policy['target'])
        limit=int(low)
        if target<=limit:
            audit['reason']='MA_V061_TARGET_BELOW_LOW'
            return audit,None
        avg_range=sum((float(x['high'])-float(x['low']) for x in prior[-15:]))/15
        avg_vol=sum((float(x['volume']) for x in prior[-15:]))/15
        width=float(high-low)
        vol=float(cur['volume'])
        ratio_range=width/avg_range if avg_range>0 else 0
        ratio_vol=vol/avg_vol if avg_vol>0 else 0
        big=ratio_range>=3 and ratio_vol>=m.VOLUME_GATE[tf]
        bonus=10 if big else 0
        special=bool(big)
        first_fraction=float(policy['first_fraction'])
        recovery1=int(math.ceil((float(low)+(float(close)-float(low))*.45)/float(tick))*float(tick))
        limit_high=max(int(opening),int(close))
        relative=float((close/low-1)*100)
        score_features={'rvol_prev10':vol/max(
              sum(float(x['volume']) for x in prior[-10:])/10,1e-12),
            'close_location':float((close-low)/(high-low)),
            'lower_wick_fraction':float((min(close,opening)-low)/(high-low)),
            'prior5_return_pct':float(100*(close/rule.dec(prior[-5]['close'])-1)),
            'range_pct':float((high-low)/low*100),
            'target_upside_pct':float((Decimal(target)/low-1)*100),
            'body_pct':float((close/opening-1)*100),
            'expected_hold_days_proxy':float(m.HOLD_SESSIONS[tf])}
        base_score=coin_diverse_priority(score_features)
        score=min(100,round(base_score+bonus,4))
        audit.update(decision='QUALIFIED',reason=('MA_DOWN_BIG_BEAR' if big else 'MA_DOWN_BEAR'),
                     special=special,score=score,ma_v061_bonus=bonus,
                     ma_v061_native_limit='LOW')
    else:
        # Only the first simultaneous upward transition under falling MA120.
        # No price-range >2% candidates, and no stale next-candle fills.
        start=dt.datetime.strptime(str(cur['ts'])[:14],'%Y%m%d%H%M%S').replace(tzinfo=KST)
        open_at=_next_stock_open(start,tf)
        audit['ma_v061_open_at']=open_at.isoformat()
        audit['ma_v061_deadline_at']=(open_at+dt.timedelta(seconds=5)).isoformat()
        audit['ma_v061_max_slippage_pct']=1
        audit['ma_v061_pivot_range_pct']=facts['width_pct']
        limit=int((close*Decimal('1.01')/tick).to_integral_value(rounding=ROUND_CEILING)*tick)
        target=int((close*Decimal('1.10')/tick).to_integral_value(rounding=ROUND_CEILING)*tick)
        if target<=limit:
            audit['reason']='MA_V061_TURNPOINT_BAD_MARKETABLE_LIMIT'
            return audit,None
        score=50.0
        special=False
        first_fraction=.20
        recovery1=int(low)
        limit_high=max(int(opening),int(close))
        audit.update(decision='QUALIFIED',reason='MA_V061_TURNPOINT_FIRST_UP',
                     score=score,special=False)
    candidate={'tf':tf,'code':str(cur['code']),'market':str(cur['market']),
        'ref_ts':str(cur['ts']),'name':'',
        'limit':limit,'target':target,
        'first_fraction':first_fraction,'score':score,'special':special,
        'low':int(low),'close':int(close),'high':int(high),
        'bodytop':limit_high,'recovery1':recovery1,'audit':audit}
    return audit,candidate


def _table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ma_v061_positions(
        position_id INTEGER PRIMARY KEY,kind TEXT NOT NULL,tf TEXT NOT NULL,
        code TEXT NOT NULL,ref_ts TEXT NOT NULL,state_json TEXT NOT NULL,
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")


def _disable_legacy_orders(self):
    import stock_ma_engine as m
    with self.lock,m._connect(self.db) as c:
        _table(c)
        stale=c.execute("""SELECT id FROM candidates WHERE status='PENDING'
            AND COALESCE(features,'') NOT LIKE '%"ma_v061_kind"%'""").fetchall()
        if stale:
            c.execute("""UPDATE candidates SET status='MA_V061_LEGACY_REFERENCE_DISABLED'
                WHERE status='PENDING'
                AND COALESCE(features,'') NOT LIKE '%"ma_v061_kind"%'""")
    self._refresh_watched()


def _make_buy_state(self,c,p):
    row=c.execute('SELECT features,ref_ts FROM candidates WHERE id=?',
                  (p['candidate_id'],)).fetchone()
    if not row:return
    try:
        evidence=json.loads(row['features'] or '{}')
        kind=str(evidence.get('ma_v061_kind') or '')
    except (ValueError,TypeError):return
    if kind not in ('TURNPOINT','DOWN_BEAR'):return
    stamp=str(p['entry_at'])
    state={'frame':p['tf'],'stage':0,'first_anchor':'0','first_sold':'0',
        'second_sold':'0','peak2':'0','last_observed_at':stamp}
    _table(c)
    c.execute("""INSERT OR IGNORE INTO ma_v061_positions(
        position_id,kind,tf,code,ref_ts,state_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)""",
        (p['id'],kind,p['tf'],p['code'],row['ref_ts'],
         json.dumps(state,ensure_ascii=False),stamp,stamp))
    if kind=='TURNPOINT':
        target=math.ceil(float(p['entry'])*(1+float(rule.FIRST_TARGET_PCT[p['tf']])/100))
        first=max(1,int(math.floor(int(p['qty0'])*.20)))
        c.execute('UPDATE positions SET target=?,first_qty=? WHERE id=?',
                  (target,first,p['id']))


def _restore_open_states(self):
    """Repair missing sidecar after a crash with exact candidate and fill IDs."""
    import stock_ma_engine as m
    with self.lock,m._connect(self.db) as c:
        _table(c)
        missing=c.execute("""SELECT p.* FROM positions p
            JOIN candidates a ON a.id=p.candidate_id
            LEFT JOIN ma_v061_positions v ON v.position_id=p.id
            WHERE p.status='OPEN' AND v.position_id IS NULL
              AND a.features LIKE '%"ma_v061_kind"%'""").fetchall()
        for p in missing:
            _make_buy_state(self,c,p)
            row=c.execute("""SELECT state_json FROM ma_v061_positions
                WHERE position_id=?""",(p['id'],)).fetchone()
            if not row:continue
            state=json.loads(row['state_json'])
            evidence=c.execute("""SELECT reason,qty,price FROM executions
                WHERE position_id=? AND side='SELL' ORDER BY id""",
                (p['id'],)).fetchall()
            for sale in evidence:
                reason=str(sale['reason'] or '')
                if reason=='MA_V061_FIRST_20PCT':
                    state=rule.quantity_after_fill(state,reason,sale['qty'])
                    state.update(stage=max(1,int(state['stage'])),
                                 first_anchor=str(sale['price']))
                elif reason=='MA_V061_SECOND_HALF_REMAINING':
                    state=rule.quantity_after_fill(state,reason,sale['qty'])
                    state.update(stage=2,peak2=str(sale['price']))
            if evidence:
                c.execute("""UPDATE ma_v061_positions SET state_json=?,updated_at=?
                    WHERE position_id=?""",
                    (json.dumps(state,ensure_ascii=False),
                     dt.datetime.now(dt.timezone.utc).isoformat(),p['id']))


def _slope_closes(self,code,tf,at):
    from d_storage import KST
    period={'5m':300,'30m':1800,'60m':3600,'day':86400}[tf]
    try:
        with self.market_store.connect() as source:
            rows=source.execute("""SELECT ts,close FROM bars
                WHERE frame=? AND code=? AND ts<=?
                ORDER BY ts DESC LIMIT 155""",
                (tf,code,at.strftime('%Y%m%d%H%M%S'))).fetchall()
        closes=[]
        for x in reversed(rows):
            stamp=str(x['ts'])
            start=dt.datetime.strptime(stamp[:14],'%Y%m%d%H%M%S').replace(tzinfo=KST)
            closed=(start+dt.timedelta(seconds=period)<=at)
            if tf=='day':closed=start.date()<at.date()
            if closed:
                closes.append(x['close'])
        return closes[-125:]
    except (sqlite3.Error,ValueError,TypeError,KeyError):
        return []


def _ma_exit(self,c,p,bid,now,book,native):
    import stock_ma_engine as m
    state_row=c.execute("""SELECT kind,state_json FROM ma_v061_positions
        WHERE position_id=?""",(p['id'],)).fetchone()
    if not state_row:
        return native(self,c,p,bid,now,book)
    kind=state_row['kind']
    closes=_slope_closes(self,p['code'],p['tf'],now)
    forced=rule.forced_exit_reason(closes,bid)
    if kind=='DOWN_BEAR' and not forced:
        return native(self,c,p,bid,now,book)
    qty=int(p['qty'])
    if qty<=0:return
    state=json.loads(state_row['state_json'])
    state['last_observed_at']=now.isoformat()
    if kind=='TURNPOINT':
        updated,wanted,reason=rule.evaluate_pivot_exit(
            state,bid,p['entry'],qty,p['qty0'],forced)
    elif forced:
        updated,wanted,reason=state,Decimal(qty),forced
    else:return
    if updated!=state:
        c.execute("""UPDATE ma_v061_positions SET state_json=?,updated_at=?
            WHERE position_id=?""",
            (json.dumps(updated,ensure_ascii=False),now.isoformat(),p['id']))
    size=min(qty,int(math.ceil(float(wanted))))
    if size<=0 or not reason:return
    actual=self._sell(c,p,size,int(bid),now,reason,book)
    if not actual:return
    applied=rule.quantity_after_fill(updated,reason,actual)
    c.execute("""UPDATE ma_v061_positions SET state_json=?,updated_at=?
        WHERE position_id=?""",
        (json.dumps(applied,ensure_ascii=False),now.isoformat(),p['id']))
    if actual>=qty and kind=='TURNPOINT':
        # No same-tick re-entry from an obsolete pivot signal.
        c.execute("""UPDATE candidates SET status='MA_V061_EXIT_COMPLETED'
            WHERE code=? AND status='PENDING'""",(p['code'],))


def _try_open_first5s(self,tick,now):
    """KRX: catch a real finalized pivot even when the bulk scan is slow.

    Uses only MarketStore's prior completed OHLCV; no forming next-bar candle,
    no simulated opening price and no late backdated purchase. The same native
    StockMAService.on_tick still checks KRX 0D quote depth and fills the buy.
    """
    import stock_ma_engine as m
    from d_v19_portfolio import live_session
    if (len(tick)<8 or tick[7]!='KRX' or not live_session(now,'KRX')
        or abs((dt.datetime.now(now.tzinfo)-now).total_seconds())>3
        or now.microsecond+now.second*1000000>5000000):
        return
    code,market,name=str(tick[0]),str(tick[1]),str(tick[2])
    frames=[]
    if now.minute%5==0:frames.append('5m')
    if now.minute%30==0:frames.append('30m')
    if now.minute==0:frames.append('60m')
    if now.hour==9 and now.minute==0:frames.append('day')
    if not frames:return
    token=now.strftime('%Y%m%d%H%M')
    if not hasattr(self,'_ma_v061_fast_done'):self._ma_v061_fast_done=set()
    done=self._ma_v061_fast_done
    for tf in frames:
        key=(code,tf,token)
        if key in done:continue
        try:
            cutoff=now.replace(second=0,microsecond=0).strftime('%Y%m%d%H%M%S')
            with self.market_store.connect() as source:
                if tf=='day':
                    history=source.execute("""SELECT code,market,ts,open,high,low,close,volume
                        FROM bars WHERE code=? AND frame=? AND SUBSTR(ts,1,8)<?
                        ORDER BY ts DESC LIMIT 150""",
                        (code,tf,now.strftime('%Y%m%d'))).fetchall()
                else:
                    history=source.execute("""SELECT code,market,ts,open,high,low,close,volume
                        FROM bars WHERE code=? AND frame=? AND ts<?
                        ORDER BY ts DESC LIMIT 150""",(code,tf,cutoff)).fetchall()
            if len(history)<122:continue
            ordered=[dict(x) for x in reversed(history)]
            audit,candidate=_native_candle_candidate(m,ordered,tf,now)
            if audit.get('ma_v061_kind')!='TURNPOINT' or not candidate:
                done.add(key);continue
            open_at=dt.datetime.fromisoformat(audit['ma_v061_open_at'])
            deadline=dt.datetime.fromisoformat(audit['ma_v061_deadline_at'])
            if not open_at<=now<=deadline:
                done.add(key);continue
            with self.lock,m._connect(self.db) as c:
                # Same atomic original candidate schema as MA scanner, so
                # original price/position/depth and LIVE FOK gates still apply.
                c.execute("""INSERT OR IGNORE INTO candidates(
                    tf,code,name,market,ref_ts,created_at,limit_price,
                    target,score,special,low,close_price,high,bodytop,
                    recovery1,first_fraction,features)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (tf,code,name,candidate['market'],candidate['ref_ts'],
                     now.isoformat(),candidate['limit'],candidate['target'],
                     candidate['score'],int(candidate['special']),
                     candidate['low'],candidate['close'],candidate['high'],
                     candidate['bodytop'],candidate['recovery1'],
                     candidate['first_fraction'],json.dumps(candidate['audit'],
                                                            ensure_ascii=False)))
                c.execute("""INSERT INTO research(ts,tf,code,ref_ts,verdict,detail)
                    VALUES(?,?,?,?,?,?)""",
                    (now.isoformat(),tf,code,candidate['ref_ts'],
                     'QUALIFIED',json.dumps(audit,ensure_ascii=False)))
            self._refresh_watched()
            done.add(key)
        except (sqlite3.Error,ValueError,TypeError,KeyError,AttributeError):
            # Missing official record = no trade. Retry only within same 5s.
            continue
    if len(done)>5000:
        self._ma_v061_fast_done={x for x in done if x[2]==token}


def install():
    global _INSTALLED
    if _INSTALLED:return
    import stock_ma_engine as m
    m.ma_reference_decision=lambda bars,tf,at:_native_candle_candidate(
        m,bars,tf,at)
    original_init=m.StockMAService.__init__
    def init(self,*a,**kw):
        original_init(self,*a,**kw)
        _disable_legacy_orders(self)
        _restore_open_states(self)
    m.StockMAService.__init__=init
    original_exit=m.StockMAService._exit
    def exits(self,c,p,bid,now,book):
        return _ma_exit(self,c,p,bid,now,book,original_exit)
    m.StockMAService._exit=exits
    original_tick=m.StockMAService.on_tick
    def tick(self,data,book):
        try:
            now=dt.datetime.fromisoformat(str(data[6]))
            code=str(data[0])
            if now.tzinfo:
                with self.lock,m._connect(self.db) as c:
                    rows=c.execute("""SELECT id,features FROM candidates
                        WHERE code=? AND status='PENDING' AND
                        features LIKE '%"ma_v061_kind": "TURNPOINT"%'""",
                        (code,)).fetchall()
                    for row in rows:
                        try:
                            audit=json.loads(row['features'])
                            start=dt.datetime.fromisoformat(audit['ma_v061_open_at'])
                            end=dt.datetime.fromisoformat(audit['ma_v061_deadline_at'])
                            if not start<=now<=end:
                                if now>end:
                                    c.execute("""UPDATE candidates SET status='MA_V061_OPEN_5S_MISSED'
                                        WHERE id=?""",(row['id'],))
                        except (ValueError,KeyError,TypeError):
                            c.execute("""UPDATE candidates SET status='MA_V061_BAD_OPEN_WINDOW'
                                WHERE id=?""",(row['id'],))
                    # Candles for old MA candidates must not be bought.
        except (IndexError,ValueError,TypeError):
            pass
        try:
            received=dt.datetime.fromisoformat(str(data[6]))
            if received.tzinfo is not None:
                _try_open_first5s(self,data,received)
        except (ValueError,TypeError,IndexError):
            pass
        result=original_tick(self,data,book)
        if result:
            with self.lock,m._connect(self.db) as c:
                _table(c)
                fresh=c.execute("""SELECT p.* FROM positions p
                    LEFT JOIN ma_v061_positions v ON v.position_id=p.id
                    WHERE p.status='OPEN' AND p.code=? AND v.position_id IS NULL
                    ORDER BY p.id DESC LIMIT 8""",(str(data[0]),)).fetchall()
                for p in fresh:_make_buy_state(self,c,p)
        return result
    m.StockMAService.on_tick=tick
    _INSTALLED=True
