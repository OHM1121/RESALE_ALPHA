"""11/18 RSI20 watch 24h + verified Bollinger MID-touch MarketTrend.

This replaces the earlier closed-RSI-first-10/next-open-10sec BUY worker,
NOT the native exchange quote validation, TrendWallet ledger, 0B/0D, FOK,
position CASH, order lock, dedupe or SELL fill-verification engine.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import hashlib
from pathlib import Path
import sqlite3
import threading
import time

import market_trend_v043 as mt
from new_legacy_1118_policy import (
    POLICY,TREND_FRAMES,RSI_TRACK_HOURS,bollinger,
    actual_middle_touch,targets,holding_deadline)

_INSTALLED=False
_KST=mt.KST

class RsiWatch:
    """Persist RSI watch provenance without deleting or resetting old rows."""
    def __init__(self,root):
        self.path=Path(root)/"market_trend_rsi_watch_v1118.sqlite3"
        self.lock=threading.RLock()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("""CREATE TABLE IF NOT EXISTS rsi_watch(
                market TEXT NOT NULL,symbol TEXT NOT NULL,frame TEXT NOT NULL,
                observed_at TEXT NOT NULL,expires_at TEXT NOT NULL,
                first_rsi20 REAL NOT NULL,status TEXT NOT NULL,
                purchased_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(market,symbol,frame))""")
            columns={str(row[1]) for row in c.execute("PRAGMA table_info(rsi_watch)")}
            if "rsi_evidence" not in columns:
                c.execute("ALTER TABLE rsi_watch ADD COLUMN rsi_evidence TEXT NOT NULL DEFAULT ''")

    def load(self):
        with self.lock,sqlite3.connect(self.path) as c:
            out={}
            for r in c.execute("""SELECT market,symbol,frame,observed_at,
                   expires_at,first_rsi20,status,purchased_at,rsi_evidence
                   FROM rsi_watch"""):
                try:proof=json.loads(r[8]) if r[8] else None
                except (ValueError,TypeError):proof=None
                out[(r[0],r[1],r[2])]={
                    "observed_at":r[3],"expires_at":r[4],
                    "rsi20":r[5],"status":r[6],"purchased_at":r[7],
                    "evidence":proof}
            return out

    def save(self,key,row):
        with self.lock,sqlite3.connect(self.path) as c:
            c.execute("""INSERT INTO rsi_watch(market,symbol,frame,observed_at,
                expires_at,first_rsi20,status,purchased_at,rsi_evidence)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(market,symbol,frame) DO UPDATE SET
                observed_at=excluded.observed_at,expires_at=excluded.expires_at,
                first_rsi20=excluded.first_rsi20,status=excluded.status,
                purchased_at=excluded.purchased_at,
                rsi_evidence=excluded.rsi_evidence""",
                (*key,row["observed_at"],row["expires_at"],row["rsi20"],
                 row["status"],row.get("purchased_at",""),
                 json.dumps(row["evidence"],sort_keys=True) if row.get("evidence") else ""))

def _ready_market(co,market,now):
    if market=="stock":
        from krx_session_v041 import is_regular_open
        if not is_regular_open(now):return False
    elif market=="coin":
        runner=getattr(co.rt,"coin_runner",None)
        gate=getattr(runner,"preload_gate",None)
        if gate is not None and not gate.snapshot().get("ready",False):
            return False
    else:return False
    # RSI watch is market-quote driven independently of score: a crossing
    # observed in ORIGINAL mode must survive 24h and may be bought later
    # only when the normal verified MARKET_TREND mode allows it.
    return True

def _confirmed_recent(history,now,market,frame):
    """Reject source gaps, missing latest completed bars and unknown venue data.

    Source candles and a current genuine print are separate obligations:
    live prints must NEVER stand in for a missing completed RSI candle.
    """
    if frame not in TREND_FRAMES or len(history)<21:return []
    rows=sorted((r for r in history
                 if isinstance(r,dict) and
                 isinstance(r.get("end"),dt.datetime) and
                 r["end"].tzinfo is not None and r["end"]<=now),
                key=lambda x:x["end"])
    if len(rows)<21:return []
    latest=rows[-1]["end"]
    width=mt.FRAME_SECS[frame]
    if market=="coin":
        # A 30m 08:41:27 entry needs the real completed 08:30 candle.
        expected=dt.datetime.fromtimestamp((int(now.timestamp())//width)*width,mt.KST)
        if latest!=expected:return []
        recent=rows[-21:]
        if any((recent[i]["end"]-recent[i-1]["end"]).total_seconds()!=width
               for i in range(1,len(recent))):return []
        if any(r.get("source") not in
               ("UPBIT_CLOSED","NATIVE_UPBIT_VERIFIED_COMPLETED")
               for r in recent):return []
    elif market=="stock":
        from market_trend_v043 import _krx_day
        from krx_session_v041 import SPECIAL_SESSIONS,START,END
        opening,closing=SPECIAL_SESSIONS.get(now.date().isoformat(),(START,END))
        begin=now.replace(hour=opening//60,minute=opening%60,second=0,microsecond=0)
        finish=now.replace(hour=closing//60,minute=closing%60,second=0,microsecond=0)
        if not begin<=now<finish:return []
        prior=now.date()-dt.timedelta(days=1)
        while not _krx_day(prior):
            prior-=dt.timedelta(days=1)
            if (now.date()-prior).days>15:return []
        prior_closing=SPECIAL_SESSIONS.get(prior.isoformat(),(START,END))[1]
        prior_close=dt.datetime.combine(prior,dt.time(prior_closing//60,prior_closing%60),
                                         tzinfo=mt.KST)
        if frame=="day":
            if latest!=prior_close:return []
        else:
            completed=int((now-begin).total_seconds()//width)
            expected=begin+dt.timedelta(seconds=completed*width) if completed else prior_close
            if latest!=expected:return []
        if any(r.get("source") not in ("LOCAL_KRX_VERIFIED","KIWOOM_VERIFIED")
               for r in rows[-21:]):return []
    else:return []
    if any(mt._finite(r.get("close")) is None or
           float(r["close"])<=0 for r in rows[-21:]):return []
    return rows[-90:]

def _mark_rsi_watch(self,market,key,observed,rsi,now,evidence=None):
    # A pre-migration watch without evidence is not a new BUY permission.
    if (observed is not None and observed.get("status")=="WATCHING"
            and not observed.get("evidence") and rsi<=30 and evidence):
        observed=None
    if observed is None:
        start=now
        row={"observed_at":start.isoformat(),
             "expires_at":(start+dt.timedelta(hours=RSI_TRACK_HOURS)).isoformat(),
             "rsi20":float(rsi),"status":"WATCHING","purchased_at":"",
             "evidence":evidence}
        self._nl1118_watches[key]=row
        self._nl1118_watch_db.save(key,row)
        return row
    if observed["status"]=="WATCHING" and now>=dt.datetime.fromisoformat(
            observed["expires_at"]):
        row={**observed,"status":"EXPIRED"}
        self._nl1118_watches[key]=row
        self._nl1118_watch_db.save(key,row)
        return row
    # No new endless 24h period while RSI stays below 30. Re-arm only after
    # a new >30 observation resets the prior expired watch.
    if observed["status"]=="BOUGHT" and rsi>30:
        row={**observed,"status":"READY"}
        self._nl1118_watches[key]=row
        self._nl1118_watch_db.save(key,row)
        return row
    if observed["status"]=="EXPIRED" and rsi>30:
        row={**observed,"status":"READY"}
        self._nl1118_watches[key]=row
        self._nl1118_watch_db.save(key,row)
        return row
    if observed["status"]=="READY" and rsi<=30:
        row={"observed_at":now.isoformat(),
             "expires_at":(now+dt.timedelta(hours=RSI_TRACK_HOURS)).isoformat(),
             "rsi20":float(rsi),"status":"WATCHING","purchased_at":"",
             "evidence":evidence}
        self._nl1118_watches[key]=row
        self._nl1118_watch_db.save(key,row)
        return row
    return observed

def _entry_window(self,market,now):
    if not _ready_market(self,market,now):return
    if not hasattr(self,"_nl1118_watch_db"):
        self._nl1118_watch_db=RsiWatch(self.rt.root)
        self._nl1118_watches=self._nl1118_watch_db.load()
        self._nl1118_previous_trade={}
        self._nl1118_quote_stamps={}
    with self.lock:
        candidates=dict(self._candidates[market])
        history=dict((key,list(v)) for key,v in self._history.items()
                     if key[0]==market and key[2] in TREND_FRAMES)
        ticks=dict(self._quotes[market])
    ordered={}
    for code in candidates:
        quote=ticks.get(code)
        # The raw verified-ticker timestamp must be NEW. A stale quote
        # cannot trigger a Bollinger touch, even if a broker book is fresh.
        if quote is None or not isinstance(quote,tuple) or len(quote)<2:
            continue
        try:
            trade=float(quote[0]);stamp=float(quote[1])
        except (ValueError,TypeError):continue
        if not math.isfinite(trade) or trade<=0:
            continue
        if time.monotonic()-stamp>(10 if market=="coin" else 3):
            continue
        # First RSI<=30 observation is allowed even when a valid book is
        # briefly unavailable. Only BUY needs separately fresh 0D/depth.
        previous=self._nl1118_previous_trade.get((market,code))
        # Only one crossed price per genuinely new exchange print.
        # The previously observed print is separate from live order-book bid.
        if self._nl1118_quote_stamps.get((market,code))==stamp:
            continue
        self._nl1118_quote_stamps[(market,code)]=stamp
        self._nl1118_previous_trade[(market,code)]=trade
        choices=[]
        for frame in TREND_FRAMES:
            k=(market,code,frame)
            closed=_confirmed_recent(history.get(k,()),now,market,frame)
            if not closed:continue
            closes=[float(row["close"]) for row in closed]
            rsi=mt.rsi20(closes,forming_close=trade)
            if rsi is None:continue
            old=self._nl1118_watches.get(k)
            if old is None and rsi>30:continue
            proof=None
            if (rsi<=30 and
                    (old is None or old["status"]=="READY" or
                     (old["status"]=="WATCHING" and not old.get("evidence")))):
                completed=closed[-21:]
                payload="|".join(
                    row["end"].isoformat()+":"+repr(float(row["close"]))
                    for row in completed)
                proof={"market":market,"symbol":code,"frame":frame,
                       "observed_at":now.isoformat(),"rsi20":float(rsi),
                       "verified_trade":trade,"history_end":closed[-1]["end"].isoformat(),
                       "source":closed[-1].get("source"),
                       "closed21_sha256":hashlib.sha256(payload.encode("utf8")).hexdigest()}
            if old is None or old["status"] in ("WATCHING","EXPIRED","READY"):
                old=_mark_rsi_watch(self,market,k,old,rsi,now,evidence=proof)
            if old["status"]!="WATCHING":continue
            evidence=old.get("evidence")
            if (not isinstance(evidence,dict) or
                    evidence.get("market")!=market or
                    evidence.get("symbol")!=code or
                    evidence.get("frame")!=frame or
                    evidence.get("observed_at")!=old.get("observed_at") or
                    not isinstance(evidence.get("rsi20"),(int,float)) or
                    evidence["rsi20"]>30):
                continue
            condition=bollinger(closes[-20:]+[trade])
            if condition is None or not condition["angle_allowed"]:continue
            if previous is None or not actual_middle_touch(
                    previous,trade,condition["middle"]):continue
            if not (now<dt.datetime.fromisoformat(old["expires_at"])):continue
            choices.append((frame,rsi,condition,old))
        if choices:
            verified=self._quote(market,code)
            if verified is None or verified.get("trade")!=trade:
                continue
            # Bind the original pair of genuinely observed exchange prints
            # to THIS symbol. Never let a later loop symbol substitute them.
            choices.sort(key=lambda x:mt.PRIORITY[x[0]],reverse=True)
            ordered[code]=(choices[0],verified,previous,trade)
    for code,(signal,quote,previous,trade) in ordered.items():
        frame,rsi,boll,watch=signal
        if self.stop_event.is_set():return
        # NEW LEGACY 11.18 has its own RSI20 + Bollinger entry logic.
        # Coordinator._update_score deliberately keeps native AT/MA mode
        # ORIGINAL (MT54 independent wallet), so requiring MARKET_TREND
        # here made ALL otherwise valid trend BUYs impossible, even though
        # the exchange was connected and the watch/book were verified.
        # The workbook does NOT require a signed-market-score mode switch
        # for this independent strategy. Existing native wallet guards
        # (current quote/depth, market session, slot, funds and re-entry)
        # remain authoritative; no BUY/fill is synthesized.
        at=self._decision_clock()
        if at.tzinfo is None:continue
        if at>=dt.datetime.fromisoformat(watch["expires_at"]):continue
        if (self.journal.stop_reentry_blocked(market,code,at) or
            self.standalone_wallet.stop_reentry_blocked(market,code,at)):
            continue
        if self.standalone_wallet.owned(market,code):continue
        # Recheck genuine broker feed / book BEFORE original position BUY.
        fresh=self._quote(market,code)
        with self.lock:
            observed_tick=self._quotes[market].get(code)
        if (fresh is None or fresh.get("trade")!=trade or
                observed_tick!=(trade,self._nl1118_quote_stamps.get((market,code))) or
                time.monotonic()-observed_tick[1]>(10 if market=="coin" else 3) or
                not boll["angle_allowed"] or
                not actual_middle_touch(previous,trade,boll["middle"])):
            continue
        finish=__import__("market_trend_runtime_v043")._end_clock(at,market,frame)
        if finish is None:continue
        name=(str(candidates[code][1]) if isinstance(
            candidates[code],(tuple,list)) else str(code))
        try:
            position,result=self.standalone_wallet.buy(
                market,frame,code,name,fresh["ask"],fresh["ask_size"],
                at,finish.isoformat(),rsi)
        except Exception as exc:
            self._last_fetch_error[market]="NL1118_BUY:"+type(exc).__name__
            continue
        if position is None:continue
        with self.lock:
            self._positions[(market,"trend",frame,code)]=position
        saved={**watch,"status":"BOUGHT","purchased_at":at.isoformat()}
        self._nl1118_watches[(market,code,frame)]=saved
        self._nl1118_watch_db.save((market,code,frame),saved)
        self.journal.event(
            "NL1118:BUY:"+market+":"+code+":"+frame+":"+
            watch["observed_at"],market,"trend",frame,code,
            "ACTUAL" if position.execution_mode=="LIVE" else "PAPER",
            "NL1118_RSI20_24H_BOLLINGER_MID_VERIFIED",
            price=float(position.entry),rsi20_live=rsi,
            payload={"rsi_first_observed":watch["observed_at"],
                     "rsi_at_buy":rsi,"bollinger":boll,
                      "rsi_watch_evidence":watch.get("evidence"),
                     "top50":True,"verified_trade":trade,
                     "native_wallet":"TrendWalletV054",
                     "original_fill_mode":position.execution_mode})

def _trend_exit_action(self,price,now,bar_finished=True,bar3_preclose=False):
    from new_legacy_1118_policy import exit_signal
    if getattr(self,"nl1118_policy",None)!=POLICY:
        # Old MarketTrend exits are not an authorized 11.18 policy.
        # Keep the original holding intact; require audited migration.
        return None
    if self.qty<=0:return None
    p=mt._finite(price)
    if p is None or p<=0:return None
    now=mt._as_kst(now)
    self.last_price=p
    state={
        "market":self.market,"kind":"trend","frame":self.frame,
        "entry":self.entry,"qty":self.qty,"qty0":self.original_qty,
        "stop":self.entry*.85,"first":self.target1,
        "second":self.target2,"trail":
            {"30m":.04,"60m":.05,"day":.06}[self.frame],
        "stage":self.stage,"stage1_filled":self.tp1_sold,
        "stage2_filled":self.tp2_sold,
        "peak_after_second":self.peak,"seen_above_entry":self.above_entry,
        "deadline":self.deadline.isoformat()}
    found=exit_signal(state,p,now)
    self.above_entry=bool(state["seen_above_entry"])
    self.peak=state.get("peak_after_second",self.peak)
    if found is None:return None
    reason,fraction=found
    if reason=="NL1118_TP1":return "MT_TP1",fraction
    if reason=="NL1118_TP2":return "MT_TP2",fraction
    if reason=="NL1118_HARD_STOP_15PCT":return "MT1118_STOP_15PCT",1.
    if reason=="NL1118_HOLD_EXPIRY":return "MT1118_HOLD_DEADLINE",1.
    return reason, fraction

_ORIGINAL_ACTION=None
_ORIGINAL_BUY=None

def install():
    global _INSTALLED,_ORIGINAL_ACTION,_ORIGINAL_BUY
    if _INSTALLED:return
    import market_trend_runtime_v043 as runtime
    from market_trend_wallet_v054 import TrendWalletV054
    _ORIGINAL_ACTION=mt.TrendPosition.action
    _ORIGINAL_BUY=TrendWalletV054.buy
    mt.TrendPosition.action=_trend_exit_action
    runtime.Coordinator._preclose_candidates=_entry_window

    def buy_and_preplan(self,market,frame,symbol,name,ask,ask_size,at,
                        bar_key,rsi20_live):
        if frame not in TREND_FRAMES:
            return None,'TREND_5M_RETIRED_NO_NEW_BUY'
        p,reason=_ORIGINAL_BUY(self,market,frame,symbol,name,ask,ask_size,
                               at,bar_key,rsi20_live)
        if p is not None and frame in TREND_FRAMES:
            d=targets(p.entry,frame,"trend")
            p.target1=d["first"]
            p.target2=d["second"]
            p.deadline=holding_deadline(p.entry_at,"trend",frame)
            p.nl1118_policy=POLICY
            p.nl1118_sell_intents=d["sell_intents"]
            p.nl1118_stop=p.entry*.85
            p.entry_signal_policy="NL1118_RSI20_24H_BOLL_20_1_5"
            p.bar_finalized=True
            self.save_position(p)
            # Two durable, independent PAPER resting sell orders immediately
            # after the original confirmed BUY. LIVE orders are NOT submitted
            # here; a planning row must never masquerade as a broker receipt.
            import new_legacy_1118_resting as resting
            from new_legacy_1118_policy import stage_quantity
            from decimal import Decimal, ROUND_DOWN
            initial=resting.amount(p.original_qty)
            if market=='stock':
                first=resting.amount(stage_quantity(initial,market,1))
                second=resting.amount(stage_quantity(initial,market,2))
            else:
                step=Decimal('.00000001')
                first=(initial*Decimal('.30')).quantize(step,rounding=ROUND_DOWN)
                second=(initial*Decimal('.35')).quantize(step,rounding=ROUND_DOWN)
            with self.order_lock,self._connect(market) as c:
                resting.place(c,int(p.position_id),market,str(p.symbol),
                    str(p.frame),p.entry_at.isoformat(),initial,
                    d['first'],d['second'],first,second,
                    paper=(str(p.execution_mode)=='PAPER'))
        return p,reason
    TrendWalletV054.buy=buy_and_preplan
    _INSTALLED=True
