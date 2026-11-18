"""v107 independent negative signed-score gate for native reference-bar buys.

STOCK: legacy PaperEngine AT and currently active D19 AT only.
COIN: base/general original four-timeframe reference-bar CoinLedger only.
All previously owned positions and all SELL methods remain unchanged.
"""
from __future__ import annotations
import math
from pathlib import Path

_INSTALLED=False
_NEGATIVE_ONLY="MARKET_SCORE_NEGATIVE_BLOCK_NEW_REFERENCE_BAR_BUY"

def score_evidence(root,market):
    if market not in ("stock","coin"):raise ValueError("unknown market")
    from market_research_v033 import _WORKERS,_WORKERS_LOCK
    with _WORKERS_LOCK:
        worker=_WORKERS.get(str(Path(root).resolve()))
    if worker is None:
        return {"negative":False,"score":None,"basis":"SCORE_NOT_YET_AVAILABLE"}
    row=(worker.snapshot() or {}).get(market) or {}
    try:
        value=float(row.get("score"))
        if not math.isfinite(value) or value < -100 or value > 100:
            raise ValueError("invalid signed market score")
        if row.get("stale"):
            return {"negative":False,"score":None,"basis":"STALE_SCORE_NO_DECISION"}
        return {"negative":value<0,"score":value,
                "basis":"SIGNED_MARKET_SCORE_V033"}
    except (ValueError,TypeError,OverflowError):
        return {"negative":False,"score":None,"basis":"SCORE_UNVERIFIED"}

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
        if root is not None and score_evidence(root,"stock")["negative"]:
            return None
        return native_legacy_stock(self,c,tf,minute,at)

    def d19_stock(self,c,row,when,venue,observed_trade_price,
                  observed_trade_qty=0):
        root=getattr(self.store,"root",None)
        if root is not None and score_evidence(root,"stock")["negative"]:
            return False,_NEGATIVE_ONLY
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
                if score_evidence(root,"coin")["negative"]:
                    return 0
        return native_coin_base(self,c,order,book,seen,amount_available)

    d_engine.PaperEngine._buy_if_touch=legacy_stock
    d_v19_portfolio.D19PaperEngine._buy_limit_depth=d19_stock
    d_upbit.CoinLedger._buy=coin_base
    _INSTALLED=True
