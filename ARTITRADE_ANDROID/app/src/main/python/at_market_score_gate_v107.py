"""v017 strictly positive, fresh signed-score gate for native reference-bar buys.

STOCK: legacy PaperEngine AT and currently active D19 AT only.
COIN: base/general original four-timeframe reference-bar CoinLedger only.
All previously owned positions and all SELL methods remain unchanged.
"""
from __future__ import annotations
import math
from pathlib import Path
from execution_entry_plans_v107 import record_plan

_INSTALLED=False
_NOT_POSITIVE="MARKET_SCORE_NOT_FRESH_STRICTLY_POSITIVE_AT_BUY"

def _observe(root,market,strategy,frame,symbol,reference,reason,
             candidate_target=None,extra=None):
    if root is None or not frame or not symbol or not reference:return
    try:
        record_plan(root,market,strategy,frame,symbol,reference,
                    reason=reason,candidate_target=candidate_target,
                    phase="PREBUY_NATIVE_VERIFIED_ATTEMPT",
                    extra=extra)
    except Exception:
        # Audit failure never places, alters, or denies an order.
        pass

def _score_worker_root(root,market):
    """Map wallet directory to the shared score-publisher root.

    Native stock DStore.root is <phone>/stock; _MarketWorker is registered
    at <phone>. Previously the strict 11.18 BUY wrapper looked up the wrong
    key and rejected ALL qualifying stock BUY attempts as "not positive",
    even with a live positive signed score. Never invent/reuse cached score.
    """
    path=Path(root).resolve()
    if market=="stock" and path.name=="stock":
        return path.parent
    return path

def score_evidence(root,market):
    if market not in ("stock","coin"):raise ValueError("unknown market")
    from market_research_v033 import _WORKERS,_WORKERS_LOCK
    key=str(_score_worker_root(root,market))
    with _WORKERS_LOCK:
        worker=_WORKERS.get(key)
    if worker is None:
        return {"negative":False,"positive":False,"score":None,
                "basis":"SCORE_PUBLISHER_NOT_READY","score_root":key}
    try:
        row=(worker.snapshot() or {}).get(market) or {}
    except Exception:
        return {"negative":False,"positive":False,"score":None,
                "basis":"SCORE_WORKER_UNAVAILABLE"}
    try:
        value=float(row.get("score"))
        if not math.isfinite(value) or value < -100 or value > 100:
            raise ValueError("invalid signed market score")
        if row.get("stale"):
            return {"negative":False,"positive":False,"score":None,"basis":"STALE_SCORE_NO_DECISION"}
        return {"negative":value<0,"positive":value>0,"score":value,
                "basis":"SIGNED_MARKET_SCORE_V033"}
    except (ValueError,TypeError,OverflowError):
        return {"negative":False,"positive":False,"score":None,"basis":"SCORE_UNVERIFIED"}

def buy_allowed(root,market,symbol=None,frame=None,conn=None):
    """0.2.9 (sheet 11.22 C11): positive fresh score, OR an UP regime whatever the score.

    0.3.0: the regime is judged on the bought symbol on its own frame."""
    if root is None:return False
    evidence=score_evidence(root,market)
    if market=="coin":
        # 0.4.3 (#158, owner pink text): coin reference bar - no buy at score +40 or more, and none at a negative
        # score even when the symbol is UP (the former UP exception is removed for coins).
        try:
            import buy_record_v0430 as record
            if not record.coin_reference_allowed(evidence):return False
        except Exception:
            pass
    positive=evidence["positive"]
    if positive:return True
    try:
        import market_regime_v0290 as regime
        return regime.at_buy_allowed(root,market,False,symbol=symbol,frame=frame,conn=conn)
    except Exception:
        return False


def _pending_code(self,c,tf):
    try:
        pending=self._pending(c,tf)
        return str(dict(pending).get("code")) if pending is not None else None
    except Exception:
        return None

def install():
    """Wrap only original native BUY functions, never BUY replays or SELL."""
    global _INSTALLED
    if _INSTALLED:return
    import d_engine
    import d_v19_portfolio
    import d_upbit

    native_legacy_stock=d_engine.PaperEngine._buy_if_touch
    native_d19_stock=d_v19_portfolio.D19PaperEngine._buy_limit_depth
    native_coin_base=d_upbit.CoinLedger._buy

    def legacy_stock(self,c,tf,minute,at):
        root=getattr(self.store,"root",None)
        if not buy_allowed(root,"stock",_pending_code(self,c,tf),tf,c):
            return None
        try:
            pending=self._pending(c,tf)
            if pending is not None:
                raw=dict(pending)
                _observe(root,"stock","at",tf,raw.get("code"),
                         raw.get("signal_id") or str(at),
                         "ORIGINAL_STOCK_AT_LEGACY_SIGNAL_PENDING",
                         extra={"original_pending":raw,"no_fill_inferred":True})
        except Exception:
            pass
        return native_legacy_stock(self,c,tf,minute,at)

    def d19_stock(self,c,row,when,venue,observed_trade_price,
                  observed_trade_qty=0):
        root=getattr(self.store,"root",None)
        try:
            symbol,frame=str(dict(row).get("code")),str(dict(row).get("tf"))
        except Exception:
            symbol,frame=None,None
        if not buy_allowed(root,"stock",symbol,frame,c):
            return False,_NOT_POSITIVE
        try:
            data=dict(row)
            _observe(root,"stock","at",data.get("tf"),data.get("code"),
                     data.get("signal_id") or data.get("ref_ts") or str(when),
                     "ORIGINAL_D19_AT_REFERENCE_BAR_VERIFIED_TRADE",
                     candidate_target=data.get("target"),
                     extra={"candidate":{str(k):str(v)[:200] for k,v in data.items()},
                            "verified_venue":str(venue),"no_fill_inferred":True})
        except Exception:
            pass
        return native_d19_stock(self,c,row,when,venue,
                                observed_trade_price,observed_trade_qty)

    def coin_base(self,c,order,book,seen,amount_available):
        # MA, trend, any renamed/new ledger, and recovery-owned SELL are
        # NOT part of the reference-bar strategy gate. Fail scope closed:
        # unknown paths keep the original function intact.
        path=getattr(self,"path",None)
        if path is not None:
            path=Path(path).resolve()
            if (path.name=="upbit_paper.sqlite3" and
                path.parent.name=="base" and
                path.parent.parent.name=="coin"):
                root=path.parent.parent.parent
                try:
                    symbol,frame=str(dict(order).get("market")),str(dict(order).get("tf"))
                except Exception:
                    symbol,frame=None,None
                if not buy_allowed(root,"coin",symbol,frame):
                    return 0
                try:
                    data=dict(order)
                    candidate=c.execute(
                        "SELECT * FROM candidates WHERE tf=? AND market=? AND ref_at=? LIMIT 1",
                        (data.get("tf"),data.get("market"),
                         data.get("ref_at"))).fetchone()
                    facts=dict(candidate) if candidate is not None else {}
                    _observe(root,"coin","general",data.get("tf"),
                             data.get("market"),data.get("ref_at") or str(seen),
                             str(facts.get("reason") or
                                 "ORIGINAL_UPBIT_REFERENCE_BAR_PENDING_ORDER"),
                             candidate_target=facts.get("target"),
                             extra={"original_order":{str(k):str(v)[:200]
                                                    for k,v in data.items()},
                                    "candidate":{str(k):str(v)[:300]
                                                 for k,v in facts.items()},
                                    "no_fill_inferred":True})
                except Exception:
                    pass
        return native_coin_base(self,c,order,book,seen,amount_available)

    d_engine.PaperEngine._buy_if_touch=legacy_stock
    d_v19_portfolio.D19PaperEngine._buy_limit_depth=d19_stock
    d_upbit.CoinLedger._buy=coin_base
    _INSTALLED=True
