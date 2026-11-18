"""v086: stock AT three-leg recovery using genuine, current KRX 0B.

The original original-PAPER buy/exit policy is preserved. On the FIRST
verifiably fresh 0B after a long quote gap, if the same trade already exceeds
both take-profit targets, run the native first and second exits serially
against ONE transaction and prove first SELL before second. This never
backfills a missed historical trade and never touches a live brokerage order.
"""
from __future__ import annotations
import time

_INSTALLED=False
FIRST_REASON='3LEG31 수익 1차 30%'
SECOND_REASON='3LEG31 수익 2차 35% · 1차타점+3%'
GAP_SECONDS=15.0


def install():
    global _INSTALLED
    if _INSTALLED:return
    from d_engine import PaperEngine
    original_exit=PaperEngine._minute_exits_threeleg
    original_sell=PaperEngine._sell

    def verified_sell(self,c,p,at,qty,price,reason,*,event_key=None):
        # Use the OBSERVED current 0B trade only during this thread's
        # catch-up, rather than pretending yesterday's target was executable.
        # The rest of the native cash, fee, tax, event-key and position ledger
        # is unchanged and remains the sole source of truth.
        mode=str(getattr(self._execution_context,'fill_method',''))
        observed=getattr(self._execution_context,'v086_at_recovery_price',None)
        if (mode=='LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE'
                and isinstance(observed,int) and observed>0
                and reason in (FIRST_REASON,SECOND_REASON)
                and observed>=int(price)):
            price=observed
        return original_sell(self,c,p,at,qty,price,reason,event_key=event_key)

    def recovered_threeleg(self,c,p,minute,at):
        mode=str(getattr(self._execution_context,'fill_method',''))
        code=str(p['code'])
        try:
            current=int(minute['close'])
            single=all(int(minute[k])==current for k in ('open','high','low'))
        except (KeyError,TypeError,ValueError):
            single=False;current=0
        last=getattr(self._execution_context,'v086_at_last_by_code',None)
        if not isinstance(last,dict):
            last={};self._execution_context.v086_at_last_by_code=last
        now=time.monotonic()
        # Values not sourced from the original verified 0B fast tick, or
        # ordinary uninterrupted ticks, follow exact previous behavior.
        recovery=(mode=='LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE'
                  and single and current>0 and minute.get('code')==code
                  and (code not in last or now-last[code]>GAP_SECONDS))
        last[code]=now
        if not recovery:return original_exit(self,c,p,minute,at)
        previous=getattr(self._execution_context,'v086_at_recovery_price',None)
        self._execution_context.v086_at_recovery_price=current
        try:
            after=original_exit(self,c,p,minute,at)
            # If step 1 was genuinely executed on this 0B quote, one
            # additional decision may reach step 2. Never infer a fill from
            # a target touch, only from the original exact executions row.
            if (int(p['exit_stage'])==0 and after is not None
                    and after['status']=='OPEN'
                    and int(after['exit_stage'])==1
                    and current>=int(p['exit_first_target'])*1.03):
                proof=c.execute("""SELECT 1 FROM executions
                    WHERE position_id=? AND ts=? AND side='SELL' AND reason=?
                    LIMIT 1""",(p['id'],at,FIRST_REASON)).fetchone()
                if proof is not None:
                    return original_exit(self,c,after,minute,at)
            return after
        finally:
            self._execution_context.v086_at_recovery_price=previous

    PaperEngine._sell=verified_sell
    PaperEngine._minute_exits_threeleg=recovered_threeleg
    _INSTALLED=True
