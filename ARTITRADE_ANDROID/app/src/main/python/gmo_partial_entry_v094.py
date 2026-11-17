"""GMO v094: +2 genuine KRX ticks ONLY to complete confirmed D+1 partial BUY.

Retains original PROJECTGMO engine order intent, PAPER fill process,
absolute KST D+1, original risk/holding limits, broker disarmament,
no late D+2 and no fake orderbook size. Existing first BUY print is the
immutable 2-tick reference, not weighted subsequent average.
"""
from __future__ import annotations
import datetime as dt
import math
from partial_entry_v094 import qualified_topup,two_ticks_max
_INSTALLED=False


def topup_verified_d1_gmo(engine,q):
    if q.venue!='KRX' or q.trading_day_index!=1:return None
    if q.receive_delay_seconds>3 or q.receive_delay_seconds<-.5:return None
    if (not all(math.isfinite(float(x)) and float(x)>0
                for x in (q.last,q.ask,q.ask_size))):
        return None
    try:
        stamp=engine._time(q.at)
    except (ValueError,TypeError):
        return None
    if not (stamp.date().isoformat()==q.at[:10] and
        dt.time(9,0)<=stamp.time()<dt.time(15,30)):
        return None

    with engine._lock:
        p=engine.positions.get(q.code)
        signal=engine.signals.get(q.code)
        if (p is None or signal is None or p.entry_date!=q.at[:10]
            or signal.entry_date!=p.entry_date or
            p.stage!='NORMAL' or p.exit_stage or p.blocked or
            p.first_profit_triggered or p.first_filled_qty or
            p.second_filled_qty or p.pending_sell_id):
            return None
        if any(o.code==q.code for o in engine._active_buys()):
            return None
        if any(f.get('code')==q.code for f in engine.fills):
            return None
        first_fills=[f for f in engine.buy_fills
                     if f.get('code')==q.code and str(f.get('time',''))[:10]==p.entry_date]
        if not first_fills or p.qty<=0 or p.total_qty<=0:
            return None
        first=float(first_fills[0]['price'])
        per_symbol=float(engine.cfg.seed)/max(1,int(engine.cfg.max_positions))
        spent=float(p.cost_basis)
        if not qualified_topup('stock',first,q.ask,spent,per_symbol,
            already_sold=False,has_verified_buy=True):
            return None
        limit=two_ticks_max('stock',first)
        if limit is None or q.last>float(limit):
            return None
        cash=min(max(0.0,engine.cash-engine._reserved_cash()),
                 max(0.0,per_symbol-spent))
        actual_qty=min(int(math.floor(float(q.ask_size)*.5)),
                       int(math.floor(cash/(float(q.ask)*(1+engine.cfg.buy_fee)))))
        if actual_qty<1:return None
        intent=engine._order(q.code,'BUY','D+1_PARTIAL_TWO_TICKS',
                              int(round(first)),int(limit),actual_qty,q.at)
        engine._log(q.code,'D1_VERIFIED_INITIAL_PARTIAL_CONTINUED',
                    f'first={first},ask={q.ask},ceiling={limit},qty={actual_qty}',
                    q.at)
        return intent


def install():
    global _INSTALLED
    if _INSTALLED:return
    from projectgmo.engine import Engine
    previous=Engine.on_quote
    def after_quote(self,q):
        result=previous(self,q)
        # Preserve ORIGINAL approved sell/expiry/stop actions before any
        # optional BUY continuation; never revive a previously sold holding.
        original_count=self.next_id
        option=topup_verified_d1_gmo(self,q)
        if option is not None and self.next_id>original_count:
            return list(result)+[option]
        return result
    Engine.on_quote=after_quote
    _INSTALLED=True
