"""GMO v0.8.0: D+1 07:50 NXT / 08:50 KRX preparation and three staged PAPER entry intents.

Original completed-D signal, PAPER wallet, fees, fills, risk exits and BUY UUID
authority remain in the frozen projectgmo Engine. There is NO broker-order API.
NXT's early first received trade is not the official KRX opening auction.
Only verified same-venue 0B+0D can consume NXT PAPER liquidity.
"""
from __future__ import annotations
import datetime as dt
import math
import os
import threading
from projectgmo.prices import plus_ticks, up_tick
from buy_execution_recovery_v082 import Intent

KST=dt.timezone(dt.timedelta(hours=9))
_INSTALLED=False
PHASE={"NXT":("075000","080000","085000"),"KRX":("085000","090000","153000")}
# Official 2026 CSAT session: NXT 08:00–08:50 is suspended on this day.
NXT_PREMARKET_CLOSED=frozenset(("2026-11-19",))
ACTIVE=("NEW","OPEN")

def _now():
    return dt.datetime.now(KST)

def _stamp(raw):
    try:
        t=dt.datetime.fromisoformat(str(raw))
        return t if t.tzinfo is not None else None
    except (ValueError,TypeError):
        return None

def _verified(tick,clock):
    # GMO runs on the ONE Kiwoom shared 0B dispatcher. A malformed value
    # from a partial/out-of-order frame must not raise into the dispatcher
    # and starve unrelated AT/CW/MA/Trend tick processing.
    if (not isinstance(tick,(tuple,list)) or len(tick)<9
            or tick[7] not in ("NXT","KRX")):
        return None
    t=_stamp(tick[6])
    if t is None or not -.5<=(clock-t).total_seconds()<=3:
        return None
    from d_minute_repair_fast import exchange_stamp_is_fresh
    if not exchange_stamp_is_fresh(tick[8],tick[6]):
        return None
    try:
        price=float(tick[4]);volume=float(tick[5])
    except (TypeError,ValueError,OverflowError):
        return None
    if (not str(tick[0]).strip() or not math.isfinite(price)
            or not math.isfinite(volume) or price<=0 or volume<=0):
        return None
    return t

def _venue_phase(venue,at):
    """One KRX special-session calendar for D+1 PAPER preparation and entry."""
    day=at.date().isoformat()
    if venue=="NXT" and day in NXT_PREMARKET_CLOSED:return None
    if venue=="KRX":
        from krx_session_v041 import SPECIAL_SESSIONS,START,END
        start,end=SPECIAL_SESSIONS.get(day,(START,END))
        def clock(minute):
            return f"{minute//60:02d}{minute%60:02d}00"
        return clock(start-10),clock(start),clock(end)
    return PHASE.get(venue)


def _valid_phase(venue,at,prepare=False):
    phase=_venue_phase(venue,at)
    if not phase:return False
    hhmmss=at.strftime("%H%M%S")
    if prepare:
        return phase[0]<=hhmmss<phase[1]
    return phase[1]<=hhmmss<phase[2]

def _d1_verified(service,signal,at):
    try:
        from d_gmo_runtime import _next_day
        return signal.entry_date==at.date().isoformat() and (
            _next_day(signal.event_date,service.root)==signal.entry_date)
    except (ValueError,TypeError,KeyError,OSError):
        return False

class Preplanner:
    def __init__(self,service,clock=_now):
        self.service=service
        self.clock=clock
        self.lock=threading.RLock()
        self.plans={}
        self._v082_intents={}
        self.runner=None

    def _key(self,code,venue,at):
        return at.date().isoformat(),str(code),venue

    def _nxt_allowed(self,code,at):
        runner=self.runner
        if runner is None:return False
        try:return bool(runner.nxt_snapshot.allows(code,at.strftime("%Y%m%d")))
        except (AttributeError,ValueError):return False

    def prepare(self,venue,at=None):
        at=at or self.clock()
        if not _valid_phase(venue,at,True):return ()
        if os.environ.get("ARTITRADE_REAL_MODE")=="1":return ()
        service=self.service
        made=[]
        with self.lock,service._lock,service.engine._lock:
            for code,signal in list(service.engine.signals.items()):
                if not _d1_verified(service,signal,at):continue
                existing=service.engine.positions.get(code)
                if existing is not None and (venue=="NXT" or
                    existing.entry_date!=signal.entry_date or existing.stage!="NORMAL" or
                    existing.pending_sell_id or existing.first_profit_triggered or
                    existing.first_filled_qty or existing.second_filled_qty):
                    continue
                if any(f.get("code")==code and str(f.get("time",""))[:10]==signal.entry_date
                       for f in service.engine.fills):continue
                # NXT membership is an independently cached venue-eligibility
                # fact, NOT a live trade or orderbook. Prepare the entire
                # D-confirmed scenario even if the membership snapshot has
                # not arrived yet; such a plan cannot issue a BUY until
                # self._nxt_allowed() is independently verified at execution.
                nxt_verified=(venue!="NXT" or self._nxt_allowed(code,at))
                key=self._key(code,venue,at)
                prior=self.plans.get(key)
                if prior is not None:
                    if (venue=="NXT" and nxt_verified and
                        prior["status"]=="WAIT_NXT_ELIGIBILITY"):
                        prior["status"]="PREPARED"
                        prior["nxt_eligibility_verified"]=True
                        prior["eligibility_verified_at"]=at.isoformat()
                        made.append(code)
                    continue
                per_stock=float(service.engine.cfg.seed)/max(1,int(service.engine.cfg.max_positions))
                used=sum(float(f.get("paid") or 0) for f in service.engine.buy_fills
                         if f.get("code")==code and str(f.get("time",""))[:10]==signal.entry_date)
                # All three scenario/quantity formulas are ready ten minutes
                # before opening; NO price or exact shares may be invented.
                # Cash, existing positions and reservations are revalidated
                # by the original order engine at the actual market opening.
                cash_snapshot=max(0.0,float(service.engine.cash)-
                                  float(service.engine._reserved_cash()))
                indicative_budget=max(0.0,min(per_stock-used,cash_snapshot))
                scenario_templates=[
                    {"scenario":1,"price_rule":"VERIFIED_OPEN_OR_BELOW",
                     "limit_price":None,"quantity":None},
                    {"scenario":2,"price_rule":"VERIFIED_OPEN_PLUS_1_EXCHANGE_TICK",
                     "limit_price":None,"quantity":None},
                    {"scenario":3,"price_rule":"VERIFIED_OPEN_PLUS_2_EXCHANGE_TICKS",
                     "limit_price":None,"quantity":None}]
                opening_hhmm=_venue_phase(venue,at)[1]
                opening_at=at.replace(hour=int(opening_hhmm[:2]),
                                      minute=int(opening_hhmm[2:4]),
                                      second=0,microsecond=0)
                plan={"code":code,"venue":venue,"event_date":signal.event_date,
                      "entry_date":signal.entry_date,
                      "prepared_at":at.isoformat(timespec="seconds"),
                      "opening_at":opening_at.isoformat(),
                      "max_budget_won":per_stock,"spent_won":used,
                      "indicative_remaining_budget_won":indicative_budget,
                      "buy_fee_rate":float(service.engine.cfg.buy_fee),
                      "scenario_templates":scenario_templates,
                      "quantity_formula":"floor(remaining_authorized_budget / "
                          "(verified_open_plus_2ticks * (1+buy_fee)))",
                      "requires_live_price_at_prepare":False,
                      "opening_quote_required_for_final_price_and_qty":True,
                      "nxt_eligibility_verified":bool(nxt_verified),
                      "status":"PREPARED" if nxt_verified
                                 else "WAIT_NXT_ELIGIBILITY",
                      "scenario":0,"opening_price":None,
                      "opening_source":None,"order_id":None}
                self.plans[key]=plan
                if nxt_verified:made.append(code)
                service.engine._log(code,"GMO_D1_PREPLAN_READY",
                    f"venue={venue} D={signal.event_date} D1={signal.entry_date} "
                    f"budget={per_stock:.0f} eligible={nxt_verified} "
                    f"at={plan['prepared_at']}",at.isoformat())
        return tuple(made)

    def active_codes(self,at=None):
        at=at or self.clock()
        with self.lock:
            return {code for (day,code,_),p in self.plans.items()
                    if day==at.date().isoformat() and
                    p["status"] not in ("COMPLETED","EXPIRED","BLOCKED","SOLD")}

    def _paper_only(self):
        if os.environ.get("ARTITRADE_REAL_MODE")=="1":return False
        try:
            import android_live_bridge as bridge
            runtime=getattr(bridge,"_RUNTIME",None)
            return runtime is None or not runtime.live_enabled("stock")
        except (ImportError,AttributeError):
            return True

    def _open_buy(self,engine,code):
        return next((o for o in engine.orders.values() if o.side=="BUY"
                     and o.code==code and o.state in ACTIVE),None)

    def _clear_nxt(self,at):
        if at.strftime("%H%M%S")<"085000":
            return
        service=self.service;eng=service.engine
        changed=False
        with self.lock,service._lock,eng._lock:
            # Recover even after an app restart that lost transient preplan
            # memory: the ORIGINAL order journal is the cancellation authority.
            for o in tuple(eng.orders.values()):
                if (o.side=="BUY" and o.purpose.startswith("D+1_V097_NXT_")
                    and str(o.time)[:10]<=at.date().isoformat() and o.state in ACTIVE):
                    eng._cancel(o,"NXT 08:50 session ended; KRX preplan separate",at.isoformat())
                    if o.state=="CANCEL_REQUESTED":
                        eng.acknowledge_cancel(o.id,at.isoformat())
                    changed=True
            for (day,code,venue),plan in list(self.plans.items()):
                if venue!="NXT" or day!=at.date().isoformat():
                    continue
                if plan["status"] not in ("COMPLETED","SOLD"):
                    plan["status"]="EXPIRED"
                plan["closed_at"]=at.isoformat()
            if changed:service._persist(True)

    def background(self):
        svc=self.service
        while not svc._stop.is_set():
            at=self.clock()
            try:
                # At 08:50 NXT is closed: finalize any outstanding NXT intent
                # BEFORE preparing the KRX 09:00 budget and scenarios.
                phase=_venue_phase("KRX",at)
                if phase and phase[0]<=at.strftime("%H%M%S")<phase[1]:
                    self._clear_nxt(at)
                if _valid_phase("NXT",at,True):
                    self.prepare("NXT",at)
                if _valid_phase("KRX",at,True):
                    self.prepare("KRX",at)
            except Exception as e:
                svc.last_error="GMO_PREPLAN_"+type(e).__name__+": "+str(e)[:120]
            hhmm=at.strftime("%H%M")
            # Apply the same scheduling budget on ordinary and special days.
            current=at.hour*3600+at.minute*60+at.second
            near_start=near_open=False
            for v in ("NXT","KRX"):
                phase=_venue_phase(v,at)
                if not phase:continue
                prep=int(phase[0][:2])*3600+int(phase[0][2:4])*60
                opening=int(phase[1][:2])*3600+int(phase[1][2:4])*60
                near_start |= abs(current-prep)<=60
                near_open |= -180<=current-opening<=120
            svc._stop.wait(.5 if near_start or near_open else 4.0)

    def on_tick(self,tick,venue):
        at=self.clock()
        phase=_venue_phase("KRX",at)
        if venue=="KRX" and phase:
            seconds=at.hour*3600+at.minute*60+at.second
            opening=int(phase[1][:2])*3600+int(phase[1][2:4])*60
            if opening<=seconds<opening+600:self._clear_nxt(at)
        if not self._paper_only() or not _valid_phase(venue,at):return
        t=_verified(tick,at)
        if t is None or tick[7]!=venue:return
        code=str(tick[0]);service=self.service
        key=self._key(code,venue,at)
        plan=self.plans.get(key)
        if plan is None or plan["status"] in ("EXPIRED","BLOCKED","SOLD","COMPLETED"):
            return
        phase=_venue_phase(venue,at)
        if not phase:return
        opening=at.replace(hour=int(phase[1][:2]),
                           minute=int(phase[1][2:4]),second=0,microsecond=0)
        if plan["prepared_at"]>=opening.isoformat():
            return
        if venue=="NXT":
            if not self._nxt_allowed(code,at):return
            if plan["status"]=="WAIT_NXT_ELIGIBILITY":
                plan["status"]="PREPARED"
                plan["nxt_eligibility_verified"]=True
                plan["eligibility_verified_at"]=at.isoformat()
            runner=self.runner
            subscribed=any(code+"_NX" in group for group in getattr(runner,"_subscribed_groups",{}).values())
            if not subscribed:return
            # Pin the opening reference from the FIRST verified 0B even
            # if 0D delivery follows it. Never replace it with a later print.
            opener=plan.get("opening_price")
            if opener is None:
                if at.strftime("%H%M%S")>"080003":
                    plan["status"]="OPEN_NOT_VERIFIED"
                    return
                opener=int(tick[4])
                plan["opening_price"]=opener
                plan["opening_source"]="FIRST_RECEIVED_NXT_0B_PREARMED"
            with service._lock:
                book=service.priority_books.get((code,"NXT"))
            if not book or book[4].tzinfo is None or not -.5<=(at-book[4]).total_seconds()<=3:
                return
            ask,bid,ask_qty,bid_qty=map(int,book[:4])
            if ask<=0 or bid<=0 or ask<bid or ask_qty<=0:return
            last=int(tick[4]);qty_depth=max(1,ask_qty//2)
            self._step(plan,at,last,ask,qty_depth,venue,tick=tick,bid=bid)
        else:
            from d_gmo_runtime import _number
            opener=service.opens.get((code,at.date().isoformat()))
            if opener is None or int(opener)<=0:return
            plan["opening_price"]=int(opener)
            plan["opening_source"]="VERIFIED_KRX_OFFICIAL_OPEN"
            q=service._quote(tick)
            if q is None:return
            ask=int(q.ask)
            last=int(q.last)
            if ask<=0:return
            # KRX PAPER fill is delegated back to its original 0B+0D matcher.
            self._step(plan,at,last,ask,max(0,int(q.ask_size)),venue,q=q,tick=tick)

    def _step(self,plan,at,last,ask,depth,venue,q=None,tick=None,bid=None):
        svc=self.service;engine=svc.engine
        code=plan["code"];signal=engine.signals.get(code)
        if not signal or not _d1_verified(svc,signal,at):return
        opener=plan.get("opening_price")
        if not opener:return
        caps=(int(opener),plus_ticks(int(opener),1),plus_ticks(int(opener),2))
        # Only a real same-venue verified 0B exchange timestamp may drive
        # the recovery command. A missing/expired quote leaves the original
        # plan waiting; no backfilled print or fake order acknowledgement.
        proof=None
        if tick is not None and len(tick)>=9:
            stamp=_stamp(tick[8])
            if stamp is not None and abs((at-stamp).total_seconds())<=3:
                best_bid=(bid if bid is not None else getattr(q,"bid",0))
                proof={"symbol":code,"exchange_ms":int(stamp.timestamp()*1000),
                       "ask":int(ask),"bid":int(best_bid or 0),
                       "ask_size":max(0,int(depth))}
        if proof is not None:
            intent_key=(plan["entry_date"],code,venue)
            intent=self._v082_intents.get(intent_key)
            if intent is None:
                try:
                    per=float(engine.cfg.seed)/max(1,int(engine.cfg.max_positions))
                    intent=Intent("gmo","stock",code,
                        plan["entry_date"]+":"+venue,str(opener),str(per),
                        str(engine.cfg.buy_fee))
                    self._v082_intents[intent_key]=intent
                except (ValueError,TypeError,ArithmeticError):
                    intent=None
            if intent is not None:
                observed=intent.quote(proof,int(at.timestamp()*1000))
                plan["v082_recovery"]=dict(intent.status(),
                    action=observed.get("action"),
                    quote_reason=observed.get("reason"),
                    original_order_owner="GMO_PAPER")
                if observed.get("action")=="WAIT":
                    plan["status"]="WAIT_VERIFIED_RECOVERY_BOOK"
                    return
        # Resolve pre-created rule templates only from the original verified
        # opening reference; preflight NEVER substitutes a prior close.
        for template,price in zip(plan.get("scenario_templates",()),caps):
            template["limit_price"]=int(price)
        touch=max(int(last),int(ask))
        scenario=next((i+1 for i,cap in enumerate(caps) if touch<=cap),0)
        if not scenario:return
        cap=caps[scenario-1]
        changed=False
        with self.lock,svc._lock,engine._lock:
            if any(f.get("code")==code and str(f.get("time",""))[:10]<=plan["entry_date"]
                   for f in engine.fills):
                plan["status"]="SOLD";return
            pos=engine.positions.get(code)
            if pos and (pos.entry_date!=plan["entry_date"] or
                        pos.stage!="NORMAL" or pos.exit_stage or pos.pending_sell_id or
                        pos.first_profit_triggered or pos.first_filled_qty or pos.second_filled_qty):
                plan["status"]="BLOCKED";return
            active=self._open_buy(engine,code)
            if active is not None and not active.purpose.startswith("D+1_V097_"+venue+"_"):
                # Respect old pending or unresolved orders. Never double-submit.
                return
            if active is None:
                other_pending=sum(1 for o in engine._active_buys()
                                  if o.code not in engine.positions)
                if pos is None and len(engine.positions)+other_pending>=engine.cfg.max_positions:
                    plan["status"]="WAITING_SLOT";return
                per=float(engine.cfg.seed)/max(1,int(engine.cfg.max_positions))
                spent=float(pos.cost_basis) if pos is not None else 0.0
                reserved_own=0.0
                available=max(0.0,engine.cash-engine._reserved_cash())
                budget=min(max(0.0,per-spent),available)
                # Reserve against the WORST permitted +2 tick price from onset.
                worst=caps[2]
                qty=math.floor(budget/(worst*(1+engine.cfg.buy_fee)))
                plan["finalized_max_quantity_at_plus2_ticks"]=int(qty)
                plan["finalized_budget_at_open_won"]=float(budget)
                for item in plan.get("scenario_templates",()):
                    item["quantity"]=int(qty)
                if qty<1:
                    plan["status"]="COMPLETED" if pos else "INSUFFICIENT_BUDGET"
                    return
                if code+"|"+signal.entry_date in engine.used and pos is None:
                    prior_own=any(o.code==code and o.side=="BUY" and
                        o.purpose.startswith("D+1_V097_NXT_") and
                        o.state=="CANCELED" and str(o.time)[:10]==signal.entry_date
                        for o in engine.orders.values())
                    if not (venue=="KRX" and prior_own):
                        plan["status"]="BLOCKED";return
                engine._v097_managed_codes.add(code)
                # Scenario 1 is an IMMEDIATE aggressive +2-tick limit, not
                # a best-ask-sized passive order. Original 0B/0D matcher
                # still fills only proved actual PAPER liquidity at or below
                # the limit, then keeps the SAME intent for residual shares.
                active=engine._order(code,"BUY",f"D+1_V097_{venue}_S{scenario}",
                                      int(opener),int(caps[2]),int(qty),at.isoformat())
                if proof is not None:
                    intent=self._v082_intents.get((plan["entry_date"],code,venue))
                    if intent is not None:
                        try:
                            intent.submit(str(active.id),str(qty),proof["exchange_ms"])
                            intent.acknowledge(str(active.id))
                        except (RuntimeError,ValueError,ArithmeticError) as issue:
                            plan["v082_order_audit"]=(type(issue).__name__+
                                ":"+str(issue)[:100])
                engine.used.add(code+"|"+signal.entry_date)
                changed=True
            elif scenario>int(plan.get("scenario") or 0):
                # Replace only a still-active PAPER intent's limit; never create
                # simultaneous scenario orders or exceed the fixed +2 cap.
                active.limit=max(int(active.limit),int(cap))
                active.purpose=f"D+1_V097_{venue}_S{scenario}"
                engine._log(code,"GMO_D1_SCENARIO_ESCALATED",
                            f"order={active.id} S{scenario} limit={active.limit}",at.isoformat())
                changed=True
            plan.update(status="ORDER_PENDING",scenario=max(int(plan.get("scenario") or 0),scenario),
                        limit_price=int(active.limit),order_id=active.id,
                        ceiling_2ticks=int(caps[2]),requested_qty=int(active.requested))
            before=int(active.filled)
            if venue=="NXT" and depth>0 and ask<=active.limit and last<=active.limit:
                # PAPER only; live NXT 0B trade AND same-venue fresh 0D depth.
                px=min(int(active.limit),up_tick(ask*(1+engine.cfg.paper_buy_slip)))
                remaining=active.requested-active.filled
                available=math.floor(engine.cash/(px*(1+engine.cfg.buy_fee)))
                unspent=max(0.0,float(engine.cfg.seed)/max(1,int(engine.cfg.max_positions))-
                            (pos.cost_basis if pos else 0.0))
                per_left=math.floor(unspent/(px*(1+engine.cfg.buy_fee)))
                qty=min(depth,remaining,available,per_left)
                if qty>0:
                    engine.fill(active.id,int(qty),float(px),at.isoformat())
            elif venue=="KRX" and q is not None:
                # Original KRX PAPER matcher uses actual 0B+0D or bounded genuine
                # 0B trade proxy. Never call the broker adapter.
                engine.paper_match(q)
                if int(getattr(q,"ask_size",0))<=0 and tick is not None and int(tick[5])>0:
                    engine.paper_match_krx_print_proxy(q,int(tick[5]),
                        buy_missing_book=True,sell_missing_book=False)
            actual=engine.orders.get(active.id)
            if actual is not None:
                plan["v082_actual_order_status"]={
                    "original_order_id":str(actual.id),
                    "original_state":str(actual.state),
                    "original_requested_qty":int(actual.requested),
                    "original_filled_qty":int(actual.filled),
                    "original_unfilled_qty":max(0,int(actual.requested)-int(actual.filled)),
                    "aggressive_ceiling_2ticks":int(caps[2])}
            if actual is not None and actual.filled>before:
                changed=True
                plan["filled_qty"]=int(actual.filled)
                plan["status"]="PARTIALLY_FILLED" if actual.state!="FILLED" else "FILLED"
            if actual is not None and actual.state=="FILLED":
                pos=engine.positions.get(code)
                if pos is not None and pos.cost_basis >=float(engine.cfg.seed)/max(1,int(engine.cfg.max_positions))-caps[2]:
                    plan["status"]="COMPLETED"
            if changed:
                svc._persist(True)

    def snapshot(self):
        with self.lock:
            return [dict(p) for p in self.plans.values()]

def install(core):
    global _INSTALLED
    if _INSTALLED:return
    import d_gmo_runtime,d_live,gmo_partial_entry_v094
    service_class=d_gmo_runtime.GMOService
    original_start=service_class.start
    original_priority=service_class.priority_watchlist
    original_priority_tick=service_class.on_priority_tick
    original_live_tick=service_class.on_live_tick
    original_snapshot=service_class.snapshot
    original_subscriptions=d_live.LivePaperRunner._desired_subscriptions
    original_topup=gmo_partial_entry_v094.topup_verified_d1_gmo

    def start(self,*args,**kwargs):
        out=original_start(self,*args,**kwargs)
        if getattr(self,"_v097_planner",None) is None:
            p=Preplanner(self)
            self._v097_planner=p
            eng=self.engine
            old_open=eng.open_entry
            old_resume=eng.resume_legacy_partial_buy
            old_window=eng._d1_buy_window_ok
            eng._v097_managed_codes={
                o.code for o in eng.orders.values()
                if o.side=="BUY" and o.purpose.startswith("D+1_V097_")
            }
            def deny_native_open(code,entry_date,*a,**k):
                signal=eng.signals.get(code)
                if signal and signal.entry_date==entry_date:
                    raise ValueError("V097_PREPLANNED_D1_ENTRY_OWNER")
                return old_open(code,entry_date,*a,**k)
            def deny_legacy_rearm(code,*a,**k):
                if code in eng._v097_managed_codes:return None
                return old_resume(code,*a,**k)
            def nxt_session_window(order,at):
                purpose=str(order.purpose)
                if purpose.startswith("D+1_V097_NXT_"):
                    stamp=_stamp(at)
                    return bool(stamp and stamp.date().isoformat()==order.time[:10] and
                                _valid_phase("NXT",stamp))
                if purpose.startswith("D+1_V097_KRX_"):
                    stamp=_stamp(at)
                    from krx_session_v041 import SPECIAL_SESSIONS
                    if stamp and stamp.date().isoformat() in SPECIAL_SESSIONS:
                        # Only the managed original PAPER order's opening
                        # hour changes; no synthetic trade/book may fill it.
                        return (stamp.date().isoformat()==order.time[:10]
                                and _valid_phase("KRX",stamp))
                return old_window(order,at)
            eng.open_entry=deny_native_open
            eng.resume_legacy_partial_buy=deny_legacy_rearm
            eng._d1_buy_window_ok=nxt_session_window
            t=threading.Thread(target=p.background,name="GMO_V097_D1_PREPLANS",daemon=True)
            t.start()
        return out

    def priority(self,at=None):
        codes=set(original_priority(self,at))
        p=getattr(self,"_v097_planner",None)
        if p is not None:codes.update(p.active_codes(at))
        return codes

    def priority_tick(self,tick):
        out=original_priority_tick(self,tick)
        p=getattr(self,"_v097_planner",None)
        if p is not None and len(tick)>=8 and tick[7]=="NXT":
            p.on_tick(tick,"NXT")
        return out

    def live_tick(self,tick):
        # The original routine still owns all SELL state transitions and PAPER
        # order matching. New KRX buy is placed only AFTER the verified open.
        out=original_live_tick(self,tick)
        p=getattr(self,"_v097_planner",None)
        if p is not None and len(tick)>=8 and tick[7]=="KRX":
            p.on_tick(tick,"KRX")
        return out

    def snapshot(self):
        out=original_snapshot(self)
        p=getattr(self,"_v097_planner",None)
        if p is None:return out
        plans=p.snapshot()
        out["d1_preplans_v097"]=plans
        by_code={x["code"]:x for x in plans if x["status"]!="EXPIRED"}
        for row in out.get("pending") or []:
            q=by_code.get(row.get("code"))
            if q:
                row["status"]="D+1 가상매수 "+q["venue"]+" "+q["status"]+" · S"+str(q["scenario"])
                row["limit_price"]=q.get("limit_price")
                row["preplan_status"]=q["status"]
                row["preplan_venue"]=q["venue"]
                row["preplan_ready_at"]=q["prepared_at"]
                row["preplan_max_budget_won"]=q["max_budget_won"]
                row["preplan_scenarios"]="시가 / 시가+1호가 / 시가+2호가"
        for row in out.get("positions") or []:
            q=by_code.get(row.get("code"))
            if q:
                row["preplan_status"]=q["status"]
                row["preplan_scenario"]=q["scenario"]
                row["preplan_limit_price"]=q.get("limit_price")
        return out

    def subscriptions(self,at=None):
        # V097 formerly replaced EVERY other strategy's shared Kiwoom 0B/0D
        # subscriptions with GMO-only names during the 07:58 NXT prewarm.
        # V093 is the SOLE venue/entitlement/priority selector for all five
        # strategies, but its ordinary NXT clock opens at 08:00 and would
        # return [] if called at 07:58. Prewarm the SAME original selector
        # using the exact scheduled venue opening clock (never an executable
        # trade timestamp). This preserves both early GMO pre-subscriptions
        # AND AT/CW/MA/Trend/held exit pins within the hard 200-symbol cap.
        now=at or d_live.now_kst()
        p=getattr(self.gmo,"_v097_planner",None)
        if p is not None:p.runner=self
        if (now.date().isoformat() not in NXT_PREMARKET_CLOSED and
                "075800"<=now.strftime("%H%M%S")<"080000"):
            opening=now.replace(hour=8,minute=0,second=0,microsecond=0)
            return original_subscriptions(self,opening)
        # Ensure 09:58 special-day prewarm also uses the SHARED owner.
        # This clock is for selection only, never a real price or order.
        phase=_venue_phase("KRX",now)
        if phase:
            opening_seconds=int(phase[1][:2])*3600+int(phase[1][2:4])*60
            now_seconds=now.hour*3600+now.minute*60+now.second
            if opening_seconds-120<=now_seconds<opening_seconds:
                opening=now.replace(hour=int(phase[1][:2]),
                                    minute=int(phase[1][2:4]),
                                    second=0,microsecond=0)
                return original_subscriptions(self,opening)
        return original_subscriptions(self,at)

    def no_extra_topup(engine,q):
        if q.code in getattr(engine,"_v097_managed_codes",set()):
            return None
        return original_topup(engine,q)

    # Only the GMO service has entry responsibility; shared Kiwoom transport,
    # historical ledgers and other four strategy policies are untouched.
    service_class.start=start
    service_class.priority_watchlist=priority
    service_class.on_priority_tick=priority_tick
    service_class.on_live_tick=live_tick
    service_class.snapshot=snapshot
    d_live.LivePaperRunner._desired_subscriptions=subscriptions
    gmo_partial_entry_v094.topup_verified_d1_gmo=no_extra_topup
    _INSTALLED=True
