"""Repair stock AT's D19 paper-entry policy parity with original FIX31.

Root cause: d_engine.PaperEngine._buy_if_touch() explicitly initializes
3LEG31, but the independent D19PaperEngine._buy_limit_depth() INSERT did not.
DStore's additive schema defaults exit_policy='LEGACY' and
exit_first_target=0. Consequently new V19 buys silently inherited 40/60,
and stock_at_catchup_v086 never entered its 30/35/35 recovery path.

No price is invented; no manual, live or simulated SELL is manufactured.
Existing SELL rows and true imported legacy members are immutable.
"""
from __future__ import annotations
from decimal import Decimal

_ACTIVATION_BUY_TS='20261113000000'  # post-phone-conversion D19 open lots
_REASON='AT DIVERSE 1.1'
_INSTALLED=False


def _eligible(p):
    try:
        return (str(p['status'])=='OPEN' and int(p['qty'])>0
                and int(p['qty'])==int(p['entry_qty'])
                and not int(p['first_sold']) and not int(p['exit_stage'])
                and not int(p['downside_stage'])
                and str(p['exit_policy'])=='LEGACY'
                and not int(p['exit_first_target'])
                and str(p['entry_ts'])>=_ACTIVATION_BUY_TS
                and int(p['ref_low'])>0
                and int(p['ref_low'])<=int(p['ref_bodytop'])<=int(p['ref_high']))
    except (TypeError,ValueError,KeyError):return False


def _stamp(c,store,p,source):
    from d_engine import won
    if not _eligible(p):return False
    qty=int(p['entry_qty'])
    first=max(1,min(qty,won(Decimal(qty)*Decimal('0.30'))))
    target=int(p['ref_bodytop'])
    updated=c.execute("""UPDATE positions
        SET exit_policy='3LEG31',exit_first_target=?,first_sell_qty=?
        WHERE id=? AND status='OPEN' AND qty=entry_qty
          AND first_sold=0 AND exit_stage=0 AND downside_stage=0
          AND exit_policy='LEGACY' AND exit_first_target=0""",
        (target,first,p['id']))
    if updated.rowcount!=1:return False
    store.audit(c,'v091-at-entry-policy:'+str(p['id']),
        str(p['entry_ts']),str(p['tf']),str(p['code']),
        'AT_POLICY_ORIGINAL_INIT_REPAIRED',
        {'reason':'D19_INSERT_OMITTED_ORIGINAL_3LEG31_FIELDS',
         'source':source,'from':'LEGACY_40_60_DEFAULT',
         'to':'3LEG31_30_35_35','entry_qty':qty,
         'first_sell_qty':first,'exit_first_target':target,
         'prior_buy_qty_unchanged':True,'prior_buy_price_unchanged':True,
         'prior_sells_unchanged':True,'auto_sell_executed':False})
    return True


def repair_open_v19_positions(store):
    """One-time, atomic, *non-selling* repair of provably D19-origin lots.

    Existing legacy/adopted positions, pre-2026-11-13 lots, partial sells,
    historic executions, held quantities and wallet cash are never rewritten.
    """
    fixed=[]
    with store.lock,store.connect() as c:
        try:
            rows=c.execute("""SELECT p.* FROM positions p
                 JOIN v19_members v ON v.position_id=p.id
                 WHERE p.status='OPEN' AND p.qty>0 AND p.qty=p.entry_qty
                   AND p.exit_policy='LEGACY' AND p.exit_first_target=0
                   AND p.entry_ts>=? AND p.first_sold=0
                   AND p.exit_stage=0 AND p.downside_stage=0
                   AND NOT EXISTS(SELECT 1 FROM v19_legacy_members l
                                  WHERE l.position_id=p.id)
                   AND EXISTS(SELECT 1 FROM executions e
                     WHERE e.position_id=p.id AND e.side='BUY'
                       AND e.reason LIKE ?)
                   AND NOT EXISTS(SELECT 1 FROM executions e
                     WHERE e.position_id=p.id AND e.side='SELL')
                 ORDER BY p.id""",(_ACTIVATION_BUY_TS,_REASON+'%')).fetchall()
        except Exception:
            # Old ledgers without the original D19 provenance table are not
            # eligible for any automatic historical-policy migration.
            return []
        for p in rows:
            if _stamp(c,store,p,'ORIGINAL_D19_BUY_EXECUTION_PROVEN'):
                fixed.append(int(p['id']))
    return fixed


def install():
    global _INSTALLED
    if _INSTALLED:return
    from d_v19_portfolio import D19PaperEngine
    original_init=D19PaperEngine.__init__
    original_buy=D19PaperEngine._buy_limit_depth
    original_sell=D19PaperEngine._sell
    reasons={'3LEG31 수익 1차 30%',
             '3LEG31 수익 2차 35% · 1차타점+3%'}

    def initialized(self,store):
        original_init(self,store)
        try:repair_open_v19_positions(store)
        except Exception as exc:
            # An inability to repair one old row must not reset the account.
            try:store.write_runtime_error(exc,'v091_stock_at_policy_init')
            except Exception:pass

    def buy_with_original_threeleg(self,c,row,when,venue,price,qty=0):
        answer=original_buy(self,c,row,when,venue,price,qty)
        if not answer[0]:return answer
        p=c.execute("""SELECT p.* FROM positions p
            JOIN v19_members v ON v.position_id=p.id
            WHERE p.code=? AND p.tf=? AND p.entry_ts=?
              AND p.status='OPEN'
            ORDER BY p.id DESC LIMIT 1""",
            (row['code'],row['tf'],when.strftime('%Y%m%d%H%M%S'))).fetchone()
        if p is None:
            raise RuntimeError('D19_BUY_POLICY_POSITION_MISSING')
        # Existing parents are never mutated; new D19 buys get the same
        # FIX31 opt-in the original PaperEngine._buy_if_touch() already uses.
        if not _stamp(c,self.store,p,'ORIGINAL_D19_NEW_BUY_ATOMIC'):
            raise RuntimeError('D19_BUY_POLICY_INIT_FAILED_CLOSED')
        return answer

    def sell_at_confirmed_current(self,c,p,at,qty,price,reason,*,event_key=None):
        mode=str(getattr(self._execution_context,'fill_method',''))
        observed=getattr(self._execution_context,'v086_at_recovery_price',None)
        if (reason in reasons and
            mode=='LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE' and
            isinstance(observed,int) and observed>=int(price)>0 and
            str(p['exit_policy'])=='3LEG31'):
            price=observed
        # D19 owns its OWN cash, tax, trade and position ledger. Do not call
        # PaperEngine._sell(), which belongs to the legacy accounts table.
        return original_sell(self,c,p,at,qty,price,reason,event_key=event_key)

    D19PaperEngine.__init__=initialized
    D19PaperEngine._buy_limit_depth=buy_with_original_threeleg
    D19PaperEngine._sell=sell_at_confirmed_current
    _INSTALLED=True
