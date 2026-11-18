"""Observe real candidate/entry prerequisites for 8 ARTITRADE strategy wallets.

Provenance journal is NOT an order engine. All wrappers call the exact
previous native execution method unchanged, including failures and SELLs.
"""
from __future__ import annotations
import json
from pathlib import Path
from execution_entry_plans_v107 import record_plan,capture_candidate

_INSTALLED=False
ERRORS=[]

def _safe(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except (ValueError,TypeError,KeyError,AttributeError,OSError,RuntimeError) as exc:
        ERRORS.append(type(exc).__name__+":"+str(exc)[:90])
        if len(ERRORS)>40:del ERRORS[:-20]
        return False
    except Exception as exc:
        # Instrumentation cannot alter the underlying strategy's BUY decisions.
        ERRORS.append("UNEXPECTED_"+type(exc).__name__)
        if len(ERRORS)>40:del ERRORS[:-20]
        return False

def _root(core):
    rt=getattr(core,"_RUNTIME",None)
    return getattr(rt,"root",None)

def _reason_from_dict(data,fallback):
    if not isinstance(data,dict):return fallback
    facts=data.get("audit") or data.get("features") or {}
    if not isinstance(facts,dict):facts={}
    return str(facts.get("reason") or
               data.get("reason") or
               facts.get("ma_v067_kind") or
               facts.get("ma_v061_kind") or
               fallback)[:260]

def _stock_ma_candidate(core,native):
    original=native.ma_reference_decision
    def capture(rows,tf,when):
        result=original(rows,tf,when)
        if (isinstance(result,tuple) and len(result)>=2 and
            isinstance(result[1],dict)):
            candidate=result[1]
            root=_root(core)
            if root:
                _safe(capture_candidate,root,"stock","ma",
                      candidate.get("ref_ts") or str(when),candidate,
                      reason=_reason_from_dict(candidate,"VERIFIED_STOCK_MA_CANDIDATE"),
                      phase="PREBUY_CONFIRMED_BAR")
        return result
    native.ma_reference_decision=capture

def _coin_ma_candidate(core,native):
    original=native.ma_reference_decision
    def capture(history,tf,symbol,tick,now):
        result=original(history,tf,symbol,tick,now)
        if (isinstance(result,tuple) and len(result)>=2 and
            isinstance(result[1],dict)):
            candidate=result[1]
            root=_root(core)
            if root:
                _safe(capture_candidate,root,"coin","ma",
                      candidate.get("ref_at") or str(now),candidate,
                      reason=_reason_from_dict(candidate,"VERIFIED_COIN_MA_CANDIDATE"),
                      phase="PREBUY_CONFIRMED_BAR")
        return result
    native.ma_reference_decision=capture

def _close_entry(core,native):
    original=native.PaperEngine.open
    def capture(self,strategy,rank,signal_id,ask,ask_qty,feature):
        root=_root(core)
        if root and str(strategy)=="CLOSE":
            # Signal ID is the original verified source identifier. Its
            # reason/features already exist in CW SQLite BEFORE order attempt.
            sig={}
            try:
                with self.store.connect() as c:
                    row=c.execute("SELECT code,reason_json,feature_json FROM signals WHERE id=?",
                                  (signal_id,)).fetchone()
                    if row is not None:sig=dict(row)
            except Exception:
                pass
            facts=feature if isinstance(feature,dict) else {}
            symbol=sig.get("code") or facts.get("code") or (
                rank.get("code") if isinstance(rank,dict) else None)
            if symbol:
                why=sig.get("reason_json") or facts.get("reason") or "CW_ORIGINAL_SIGNAL"
                _safe(record_plan,root,"stock","close",
                      facts.get("frame") or "3m",symbol,str(signal_id),
                      reason="CW_VERIFIED_SIGNAL:"+str(why)[:260],
                      phase="PREBUY_ENTRY_ATTEMPT",
                      extra={"signal_id":signal_id,"feature":facts,
                             "original_order_executes_separately":True})
        return original(self,strategy,rank,signal_id,ask,ask_qty,feature)
    native.PaperEngine.open=capture

def _gmo_preplan(core,native):
    original=native.Preplanner.prepare
    def capture(self,venue,at=None):
        result=original(self,venue,at)
        root=_root(core)
        if not root:return result
        selected=set(result or ())
        if not selected:return result
        try:
            plans=tuple(self.plans.values())
        except (AttributeError,TypeError):return result
        for plan in plans:
            if plan.get("code") not in selected or plan.get("venue")!=venue:
                continue
            _safe(record_plan,root,"stock","gmo","D+1",plan["code"],
                  str(plan["entry_date"])+":"+str(venue),
                  reason="ORIGINAL_GMO_D1_VERIFIED_SIGNAL:"+str(plan.get("event_date")),
                  phase="PREBUY_D1_PLAN",
                  explanation="Verified open required; sell target unknown before actual fill",
                  extra={"plan":{k:v for k,v in plan.items()
                                 if k in ("code","venue","event_date","entry_date",
                                 "prepared_at","opening_at","scenario_templates",
                                 "max_budget_won","quantity_formula","status")},
                         "no_fill_inferred":True})
        return result
    native.Preplanner.prepare=capture

def _market_trend_entry(core,native):
    original=native.TrendWalletV054.buy
    def capture(self,market,frame,symbol,name,ask,ask_size,at,bar_key,rsi20_live):
        root=_root(core)
        if root:
            _safe(record_plan,root,market,"trend",frame,symbol,str(bar_key),
                  reason="MARKET_TREND_FIRST_CONFIRMED_CLOSED_RSI20_UNDER_30_IN_10",
                  phase="PREBUY_NEXT_OPEN_10S",
                  explanation="RSI20 from closed candle; entry candle price-dependent exits not yet known",
                  extra={"rsi20_closed_at_buy":rsi20_live,
                         "bar_key":str(bar_key),"observed_at":str(at),
                         "target_unknown_no_invention":True,
                         "ask_market_observation":ask})
        return original(self,market,frame,symbol,name,ask,ask_size,at,
                        bar_key,rsi20_live)
    native.TrendWalletV054.buy=capture

def install(core):
    global _INSTALLED
    if _INSTALLED:return
    import stock_ma_engine
    import coin_ma_engine
    import at_close_engine
    import gmo_d1_preplan_v097
    import market_trend_wallet_v054
    _stock_ma_candidate(core,stock_ma_engine)
    _coin_ma_candidate(core,coin_ma_engine)
    _close_entry(core,at_close_engine)
    _gmo_preplan(core,gmo_d1_preplan_v097)
    _market_trend_entry(core,market_trend_wallet_v054)
    _INSTALLED=True
