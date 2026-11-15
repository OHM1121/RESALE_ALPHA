"""v062 MA cohort payload wrapper, read-only archive vs new active ledger.

Current dashboard PnL/seed/holdings/pending always come solely from current
epoch SQLite. Historic rows are available ONLY under legacy_history, never
folded into new account statistics, research grids, or AI inputs.
"""
from __future__ import annotations
import json

def enrich(runtime,packet):
    import ma_epoch_v062 as epoch
    stock=(packet.get("stock") or {}).get("at") or {}
    stock_ledger=stock.get("ledger") or {}
    dst=stock_ledger.get("ma_subledger") or stock_ledger.get("ma") or {}
    if isinstance(dst,dict):
        dst["ma_epoch_v062"]=epoch.epoch_state(runtime.root,"stock") or {}
        dst["legacy_history"]=epoch.read_archive(runtime.root,"stock")
        dst["new_strategy_account"]=True
        dst["new_strategy_research_only"]=True
        if dst.get("realized_pnl") is None:
            dst["realized_pnl"]=0
        if dst.get("unrealized_pnl") is None:
            dst["unrealized_pnl"]=0
        if dst.get("total_pnl") is None:
            dst["total_pnl"]=0
    coin=(packet.get("coin") or {}).get("ma") or {}
    cdst=coin.get("ledger") or {}
    if isinstance(cdst,dict):
        cdst["ma_epoch_v062"]=epoch.epoch_state(runtime.root,"coin") or {}
        cdst["legacy_history"]=epoch.read_archive(runtime.root,"coin")
        cdst["new_strategy_account"]=True
        cdst["new_strategy_research_only"]=True
    res=coin.get("research") or {}
    if isinstance(res,dict):
        res["ma_epoch_v062"]=epoch.epoch_state(runtime.root,"coin") or {}
    stock_research=(stock.get("research") or {}).get("ma")
    if isinstance(stock_research,dict):
        stock_research["ma_epoch_v062"]=epoch.epoch_state(runtime.root,"stock") or {}
    return packet

def install(core):
    old=core.Runtime.hub_records
    def records(self):
        raw=old(self)
        try:
            packet=json.loads(raw) if isinstance(raw,str) else raw
            if isinstance(packet,dict):
                return json.dumps(enrich(self,packet),
                    ensure_ascii=False,default=str)
        except (OSError,ValueError,TypeError,AttributeError):
            pass
        return raw
    core.Runtime.hub_records=records
