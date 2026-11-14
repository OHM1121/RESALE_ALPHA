"""RSI(20) market-trend entry-mode mathematics and durable signal research.

One coordinator may service stock/coin and their independent AT/MA wallets.
The module never sends orders, modifies a legacy portfolio or invents fills.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import contextlib
import sqlite3
import threading
import time
from pathlib import Path

KST=dt.timezone(dt.timedelta(hours=9))
FRAMES=("5m","30m","60m","day")
FRAME_SECS={"5m":300,"30m":1800,"60m":3600,"day":86400}
HOLD_HOURS={"5m":48,"30m":72,"60m":120,"day":240}
PRIORITY={"day":4,"60m":3,"30m":2,"5m":1}
RSI_PERIOD=20
RSI_ARM=30.0
RSI_WATCH=40.0


def _finite(value):
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (ValueError,TypeError,OverflowError):
        return None


def rsi20(closed_prices,forming_close=None):
    """Wilder RSI(20), using an unfinished current bar only when requested.

    At least 21 completed closing prices are required to initialize the
    preceding Wilder state.  No nonexistent future bar is synthesized.
    """
    closes=[_finite(x) for x in closed_prices]
    if len(closes)<21 or any(x is None or x<=0 for x in closes):
        return None
    periods=closes[:]
    if forming_close is not None:
        point=_finite(forming_close)
        if point is None or point<=0:return None
        periods.append(point)
    changes=[periods[i]-periods[i-1] for i in range(1,len(periods))]
    gain=sum(max(0.0,v) for v in changes[:20])/20
    loss=sum(max(0.0,-v) for v in changes[:20])/20
    for delta in changes[20:]:
        gain=(gain*19+max(delta,0.0))/20
        loss=(loss*19+max(-delta,0.0))/20
    if gain==0 and loss==0:return 50.0
    if loss==0:return 100.0
    return 100.0-100.0/(1.0+gain/loss)


def market_mode(score,status="",stale=False,market_open=True):
    """Only an explicitly fresh numeric signed market score permits buying."""
    val=_finite(score)
    if val is None or bool(stale) or not market_open:
        return "PAUSED"
    s=str(status or "")
    if any(word in s for word in ("실패","오류","미수신","확인 중","재시도")):
        return "PAUSED"
    if not -100<=val<=100:return "PAUSED"
    return "MARKET_TREND" if val<0 else "ORIGINAL"


def _as_kst(stamp):
    if isinstance(stamp,(int,float)):
        return dt.datetime.fromtimestamp(stamp,KST)
    if isinstance(stamp,str):
        stamp=dt.datetime.fromisoformat(stamp.replace("Z","+00:00"))
    if not isinstance(stamp,dt.datetime):raise TypeError("aware datetime or timestamp required")
    if stamp.tzinfo is None:raise ValueError("timezone is required")
    return stamp.astimezone(KST)


def _krx_day(day):
    if day.weekday()>=5:return False
    from krx_session_v041 import CLOSED
    if day.year not in CLOSED:return False
    return day.isoformat() not in CLOSED[day.year]


def hold_deadline(entry,frame,market,is_krx_day=None):
    """KRX counts 24h for each trading date; coin counts continuous hours.

    On a KRX day, even non-session hours count. Weekend/closure dates do not.
    """
    if frame not in HOLD_HOURS:raise ValueError("unknown timeframe")
    start=_as_kst(entry)
    amount=HOLD_HOURS[frame]*3600
    if market=="coin":return start+dt.timedelta(seconds=amount)
    if market!="stock":raise ValueError("unknown market")
    checker=is_krx_day or _krx_day
    point=start
    safety_days=0
    while amount>0:
        safety_days+=1
        if safety_days>400:
            raise ValueError("UNVERIFIED_KRX_CALENDAR_OR_UNREACHABLE_HOLD_DEADLINE")
        next_day=dt.datetime.combine(point.date()+dt.timedelta(days=1),dt.time(),KST)
        if checker(point.date()):
            sec=int((next_day-point).total_seconds())
            if amount<=sec:return point+dt.timedelta(seconds=amount)
            amount-=sec
        point=next_day
    return point


def preclose_due(now,bar_end,frame,market):
    """Select exactly the intended order-submission second on an aware clock."""
    now=_as_kst(now);finish=_as_kst(bar_end)
    target=finish-dt.timedelta(seconds=12)
    if market=="stock" and finish.hour==15 and finish.minute==30 and frame in ("30m","60m","day"):
        target=finish.replace(hour=15,minute=19,second=48,microsecond=0)
    if market=="stock" and (now.hour>15 or (now.hour==15 and now.minute>=20)):
        return False
    return target<=now<target+dt.timedelta(seconds=1)


def live_rsi_check_due(now,bar_end,frame,market):
    now=_as_kst(now);finish=_as_kst(bar_end)
    target=finish-dt.timedelta(seconds=15)
    if market=="stock" and finish.hour==15 and finish.minute==30 and frame in ("30m","60m","day"):
        target=finish.replace(hour=15,minute=19,second=45,microsecond=0)
    return target<=now<target+dt.timedelta(seconds=1)


def sell_bar3_due(now,bar_end):
    t=_as_kst(now);end=_as_kst(bar_end)
    target=end-dt.timedelta(seconds=10)
    return target<=t<target+dt.timedelta(seconds=1)


class SignalLatch:
    """State of one (market,code,frame) so a missed buy is not retried."""
    def __init__(self,armed=False,last_bar="",fired=False):
        self.armed=bool(armed)
        self.last_bar=str(last_bar)
        self.fired=bool(fired)

    def completed(self,bar_key,closed_rsi):
        v=_finite(closed_rsi)
        if v is None:return
        if v>RSI_ARM:
            self.armed=True
            self.fired=False
        elif self.last_bar==str(bar_key) and self.fired:
            self.armed=False
        self.last_bar=str(bar_key)

    def offer(self,bar_key,live_rsi):
        v=_finite(live_rsi)
        if v is None or v>RSI_ARM or not self.armed or self.fired:
            return False
        self.armed=False
        self.fired=True
        self.last_bar=str(bar_key)
        return True

    def to_dict(self):
        return {"armed":self.armed,"last_bar":self.last_bar,"fired":self.fired}


def choose_frame(signals):
    """Signals contain frame and must already be time-coincident."""
    available=[x for x in signals if x in PRIORITY]
    return max(available,key=lambda x:PRIORITY[x]) if available else None


def candle_targets(entry,high,low):
    """High-ref first target, with 3% width cap for exceptional entry-at-high."""
    v=[_finite(x) for x in (entry,high,low)]
    if any(x is None for x in v):return None
    buy,hi,lo=v
    if buy<=0 or lo<=0 or hi<lo:return None
    d=min(0.03,(hi-lo)/hi)
    if d<=0:return None
    first=(buy*(1+d) if buy>=hi else hi)
    second=first*(1+d)
    return {"first":first,"second":second,"width_ratio":d,
            "high":hi,"low":lo}


def sale_quantities(total):
    if total<=0:return (0,0,0)
    # The provider may not support fractions of a stock; rounding is delegated
    # to the adapter. The return percentages are target allocations.
    return (total*.30,total*.35,total*.35)


class TrendPosition:
    """Pure decision state; all fills are external, acknowledged and journaled."""
    def __init__(self,market,strategy,frame,symbol,entry_at,entry,qty,bar_key):
        if market not in ("stock","coin") or frame not in FRAMES:
            raise ValueError("bad market or frame")
        if _finite(entry) is None or float(entry)<=0 or float(qty)<=0:
            raise ValueError("bad fill")
        self.market=market;self.strategy=strategy;self.frame=frame;self.symbol=str(symbol)
        self.entry_at=_as_kst(entry_at);self.entry=float(entry)
        self.original_qty=float(qty);self.qty=float(qty);self.stage=0
        self.target1=None;self.target2=None;self.peak=float(entry)
        self.above_entry=False;self.bar_key=str(bar_key)
        self.tp1_sold=0.0;self.tp2_sold=0.0
        self.closed_seq=-1;self.last_closed_bar=None
        self.last_action="";self.last_price=float(entry)
        self.deadline=hold_deadline(self.entry_at,frame,market)

    def close_entry_bar(self,bar_key,high,low,closed_rsi):
        self.target1=None;self.target2=None
        targets=candle_targets(self.entry,high,low)
        if targets:
            self.target1=targets["first"]
            self.target2=targets["second"]
        self.closed_seq=0 if _finite(closed_rsi) is not None and closed_rsi<=30 else -1
        self.last_closed_bar=str(bar_key)
        self.bar_key=str(bar_key)

    def close_next_bar(self,bar_key,closed_rsi):
        key=str(bar_key)
        if key==self.last_closed_bar:return
        if _finite(closed_rsi) is None:return
        self.last_closed_bar=key
        if closed_rsi>30:
            self.closed_seq=-1
        elif self.closed_seq<0:
            self.closed_seq=0
        else:
            self.closed_seq=min(2,self.closed_seq+1)

    def action(self,price,now,bar_finished=True,bar3_preclose=False):
        """Return (SELL reason, fraction of remaining) or None; no fill mutation."""
        if self.qty<=0:return None
        p=_finite(price)
        if p is None or p<=0:return None
        now=_as_kst(now)
        self.last_price=p
        if p<=self.entry*.91:
            return ("MT_STOP_9PCT",1.0)
        if now>=self.deadline:
            return ("MT_HOLD_DEADLINE",1.0)
        if not bar_finished:return None
        if bar3_preclose and self.closed_seq==2:
            return ("MT_RSI_3_BARS",1.0)
        self.peak=max(self.peak,p)
        if self.stage==0:
            if p>self.entry:self.above_entry=True
            if self.above_entry and p<=self.entry:
                return ("MT_ENTRY_RETOUCH",1.0)
            if self.target1 is not None and p>=self.target1:
                due=(max(1,int(round(self.original_qty*.30))) if self.market=="stock" else self.original_qty*.30)
                return ("MT_TP1",min(1.0,max(0,due-self.tp1_sold)/self.qty))
        elif self.stage==1:
            if self.target1 is not None and p<=self.target1:
                return ("MT_TP1_RETOUCH",1.0)
            if self.target2 is not None and p>=self.target2:
                due=(max(1,int(round(self.original_qty*.35))) if self.market=="stock" else self.original_qty*.35)
                return ("MT_TP2",min(1.0,max(0,due-self.tp2_sold)/self.qty))
        elif self.stage>=2:
            if (self.target2 is not None and p<=self.target2) or p<=self.peak*.96:
                return ("MT_TP3_TRAIL",1.0)
        return None

    def apply_sell(self,reason,filled_qty,filled_price):
        qty=float(filled_qty)
        if qty<=0 or qty>self.qty+1e-8:raise ValueError("invalid filled quantity")
        self.qty=max(0.0,self.qty-qty)
        self.last_price=float(filled_price)
        self.last_action=str(reason)
        if reason=="MT_TP1" and self.qty>0:
            self.tp1_sold+=qty
            target=max(1,int(round(self.original_qty*.30))) if self.market=="stock" else self.original_qty*.30
            if self.tp1_sold>=target-1e-7:self.stage=max(1,self.stage)
        elif reason=="MT_TP2" and self.qty>0:
            self.tp2_sold+=qty
            target=max(1,int(round(self.original_qty*.35))) if self.market=="stock" else self.original_qty*.35
            if self.tp2_sold>=target-1e-7:
                self.stage=max(2,self.stage)
                self.peak=float(filled_price)
        if self.qty<=1e-8:self.qty=0.0

    def to_dict(self):
        d=dict(self.__dict__)
        for key in ("entry_at","deadline"):d[key]=d[key].isoformat()
        return d


class _AutoClosingSqlite(sqlite3.Connection):
    def __exit__(self,exc_type,exc,tb):
        try:return super().__exit__(exc_type,exc,tb)
        finally:self.close()


class TrendJournal:
    """Market-trend evidence only; original order engines own fills and balances."""
    def __init__(self,root):
        self.path=Path(root)/"market_trend_v043.sqlite3"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        with self.connect() as c:
            c.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS latch(
                market TEXT NOT NULL,symbol TEXT NOT NULL,frame TEXT NOT NULL,
                payload TEXT NOT NULL,PRIMARY KEY(market,symbol,frame));
              CREATE TABLE IF NOT EXISTS position_state(
                market TEXT NOT NULL,strategy TEXT NOT NULL,frame TEXT NOT NULL,
                symbol TEXT NOT NULL, position_id INTEGER NOT NULL,
                payload TEXT NOT NULL,status TEXT NOT NULL,updated_at REAL NOT NULL,
                PRIMARY KEY(market,strategy,frame,symbol,position_id));
              CREATE INDEX IF NOT EXISTS idx_trend_open
                ON position_state(market,symbol,status);
              CREATE TABLE IF NOT EXISTS events(
                event_key TEXT PRIMARY KEY,at REAL NOT NULL,market TEXT NOT NULL,
                strategy TEXT NOT NULL,frame TEXT NOT NULL,symbol TEXT NOT NULL,
                mode TEXT NOT NULL,rsi20_live REAL,rsi20_closed REAL,
                price REAL,qty REAL,reason TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}');
              CREATE TABLE IF NOT EXISTS watch_observations(
                market TEXT NOT NULL,symbol TEXT NOT NULL,frame TEXT NOT NULL,
                observed_bucket INTEGER NOT NULL,rsi20_live REAL,
                price REAL NOT NULL,bar_key TEXT NOT NULL,
                PRIMARY KEY(market,symbol,frame,observed_bucket));
              CREATE TABLE IF NOT EXISTS virtual_position_state(
                signal_key TEXT PRIMARY KEY, market TEXT NOT NULL,
                strategy TEXT NOT NULL, frame TEXT NOT NULL, symbol TEXT NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL, updated_at REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS ix_virtual_scope
                ON virtual_position_state(market,strategy,frame,status);
              CREATE TABLE IF NOT EXISTS gate_events(
                at REAL NOT NULL,market TEXT NOT NULL,old_mode TEXT NOT NULL,
                new_mode TEXT NOT NULL,reason TEXT NOT NULL);
            """)

    def connect(self):
        c=sqlite3.connect(str(self.path),timeout=10,factory=_AutoClosingSqlite)
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def latch(self,market,symbol,frame):
        with self.lock,self.connect() as c:
            row=c.execute("SELECT payload FROM latch WHERE market=? AND symbol=? AND frame=?",
                          (market,symbol,frame)).fetchone()
        return SignalLatch(**json.loads(row["payload"])) if row else SignalLatch()

    def save_latch(self,market,symbol,frame,latch):
        raw=json.dumps(latch.to_dict(),separators=(",",":"))
        with self.lock,self.connect() as c:
            c.execute("""INSERT INTO latch VALUES (?,?,?,?)
            ON CONFLICT(market,symbol,frame) DO UPDATE SET payload=excluded.payload""",
                (market,symbol,frame,raw))

    def event(self,key,market,strategy,frame,symbol,mode,reason,price=None,qty=None,
              rsi20_live=None,rsi20_closed=None,payload=None,at=None):
        with self.lock,self.connect() as c:
            c.execute("""INSERT OR IGNORE INTO events(event_key,at,market,strategy,
               frame,symbol,mode,rsi20_live,rsi20_closed,price,qty,reason,payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (str(key),float(at or time.time()),market,strategy,frame,symbol,mode,
               _finite(rsi20_live),_finite(rsi20_closed),_finite(price),_finite(qty),
               str(reason),json.dumps(payload or {},ensure_ascii=False,default=str)))

    def record_watch(self,market,symbol,frame,price,rsi20_live,bar_key,at=None,
                     after_trigger=False):
        if _finite(rsi20_live) is None:return
        if rsi20_live>40 and not after_trigger:return
        ts=float(at or time.time())
        with self.lock,self.connect() as c:
            c.execute("""INSERT OR REPLACE INTO watch_observations
               VALUES(?,?,?,?,?,?,?)""",
               (market,symbol,frame,int(ts//30),float(rsi20_live),
                float(price),str(bar_key)))

    def save_position(self,pos,position_id):
        payload=json.dumps(pos.to_dict(),ensure_ascii=False,separators=(",",":"))
        with self.lock,self.connect() as c:
            c.execute("""INSERT INTO position_state VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(market,strategy,frame,symbol,position_id)
              DO UPDATE SET payload=excluded.payload,status=excluded.status,
                            updated_at=excluded.updated_at""",
              (pos.market,pos.strategy,pos.frame,pos.symbol,int(position_id),payload,
               "OPEN" if pos.qty>0 else "CLOSED",time.time()))

    def open_positions(self):
        with self.lock,self.connect() as c:
            rows=c.execute("SELECT * FROM position_state WHERE status='OPEN'").fetchall()
        return [dict(x) for x in rows]


    def save_virtual(self,key,pos):
        raw=json.dumps(pos.to_dict(),ensure_ascii=False,separators=(",",":"),default=str)
        with self.lock,self.connect() as c:
            c.execute("""INSERT INTO virtual_position_state
              (signal_key,market,strategy,frame,symbol,payload,status,updated_at)
              VALUES(?,?,?,?,?,?,?,?)
              ON CONFLICT(signal_key) DO UPDATE SET
                payload=excluded.payload,status=excluded.status,
                updated_at=excluded.updated_at""",
              (str(key),pos.market,pos.strategy,pos.frame,pos.symbol,raw,
               "OPEN" if pos.qty>0 else "CLOSED",time.time()))

    def virtual_open(self):
        with self.lock,self.connect() as c:
            return [dict(x) for x in c.execute(
                "SELECT * FROM virtual_position_state WHERE status='OPEN'")]

    def events_recent(self,limit=120):
        with self.lock,self.connect() as c:
            return [dict(x) for x in c.execute(
                "SELECT * FROM events ORDER BY at DESC LIMIT ?",(int(limit),))]

def rsi_research_summary(root,market,strategy):
    """Research-only evidence for the existing AI analysis tab.

    Distinguishes executed/paper buys from no-fill shadow observations.
    Never merges hypothetical returns into realized profits.
    """
    path=Path(root)/"market_trend_v043.sqlite3"
    empty={"signals":0,"filled":0,"virtual":0,"stop_exits":0,
           "watched":0,"entry_rsi_mean":None,"frames":{}}
    if not path.is_file():return empty
    try:
        with sqlite3.connect(str(path),timeout=3) as c:
            signals=c.execute("""SELECT frame,reason,rsi20_live,mode
                FROM events WHERE market=? AND strategy=?
                  AND event_key LIKE 'MT43:SIG:%'""",(market,strategy)).fetchall()
            filled=int(c.execute("""SELECT COUNT(*) FROM events
                WHERE market=? AND strategy=? AND reason='MT_BUY_FILLED'
                  AND mode IN ('ACTUAL','PAPER')""",(market,strategy)).fetchone()[0])
            stopped=int(c.execute("""SELECT COUNT(*) FROM events
                WHERE market=? AND strategy=? AND reason='MT_STOP_9PCT'
                  AND mode IN ('ACTUAL','PAPER')""",(market,strategy)).fetchone()[0])
            watched=int(c.execute("""SELECT COUNT(*) FROM watch_observations
                WHERE market=?""",(market,)).fetchone()[0])
        frames={}
        rsi=[]
        for frame,reason,value,mode in signals:
            entry=frames.setdefault(frame,{"signals":0,"filled":0,"virtual":0})
            entry["signals"]+=1
            if mode=="VIRTUAL":entry["virtual"]+=1
            else:entry["filled"]+=1
            if _finite(value) is not None:rsi.append(float(value))
        return {"signals":len(signals),"filled":filled,
                "virtual":sum(1 for x in signals if x[3]=="VIRTUAL"),
                "stop_exits":stopped,"watched":watched,
                "entry_rsi_mean":round(sum(rsi)/len(rsi),2) if rsi else None,
                "frames":frames}
    except sqlite3.Error:
        return empty
