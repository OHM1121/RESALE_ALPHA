"""Durable NEW LEGACY 11.18 **PAPER** resting TP orders; never broker orders.

On actual native BUY fill, reserve 30% and 35% of the original holding
as two independent limit orders. The original wallet remains the only
execution authority: a resting record cannot create a SELL, cash or fill.
Native confirmed SELL rows uniquely reconcile these records. Broker LIVE
mode stores a NOT_SUBMITTED plan; only verified broker order IDs can ever
be described as live exchange orders (not implemented here).
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_DOWN

_CREATE = """CREATE TABLE IF NOT EXISTS nl1118_resting_sells(
    position_id INTEGER NOT NULL,
    stage INTEGER NOT NULL CHECK(stage IN (1,2)),
    policy TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    frame TEXT NOT NULL,
    limit_price TEXT NOT NULL,
    original_qty TEXT NOT NULL,
    order_qty TEXT NOT NULL,
    filled_qty TEXT NOT NULL DEFAULT '0',
    status TEXT NOT NULL,
    broker_order_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(position_id,stage))"""
_ALLOC = """CREATE TABLE IF NOT EXISTS nl1118_resting_fill_allocations(
    execution_id INTEGER NOT NULL,
    position_id INTEGER NOT NULL,
    stage INTEGER NOT NULL,
    quantity TEXT NOT NULL,
    PRIMARY KEY(execution_id,position_id,stage))"""


def amount(value):
    try:
        v=Decimal(str(value))
        return v if v.is_finite() else None
    except (ValueError,TypeError,InvalidOperation):
        return None


_FAULTS = """CREATE TABLE IF NOT EXISTS nl1118_resting_faults(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    position_id INTEGER NOT NULL,
    stage INTEGER NOT NULL,
    detail TEXT NOT NULL)"""


def initialize(c):
    c.execute(_CREATE)
    c.execute(_ALLOC)
    c.execute(_FAULTS)


def record_fault(c,position_id,stage,detail):
    """Durable, bounded, same-transaction note that the reconciliation sidecar failed.

    The original wallet SELL is the only authority; a sidecar problem must be visible
    (never swallowed silently) but must never undo or block that SELL.
    """
    try:
        initialize(c)
        c.execute("INSERT INTO nl1118_resting_faults(position_id,stage,detail) VALUES(?,?,?)",
                  (int(position_id),int(stage),str(detail)[:300]))
        c.execute("""DELETE FROM nl1118_resting_faults WHERE id<=
            (SELECT COALESCE(MAX(id),0)-200 FROM nl1118_resting_faults)""")
    except Exception:
        pass


def place(c,position_id,market,symbol,frame,entry_at,original_qty,
          first_limit,second_limit,first_qty,second_qty,paper=True):
    """Atomic with the native BUY journal. Never overwrites prior fills."""
    n=amount(original_qty)
    a,b=amount(first_qty),amount(second_qty)
    t1,t2=amount(first_limit),amount(second_limit)
    if (not isinstance(position_id,int) or position_id<=0
        or market not in ('stock','coin') or frame not in ('5m','30m','60m','day')
        or not symbol or not entry_at or
        any(x is None for x in (n,a,b,t1,t2))
        or n<=0 or a<0 or b<0 or a+b>n or t1<=0 or t2<=t1):
        raise ValueError('NL1118_RESTING_INVALID_VERIFIED_BUY')
    initialize(c)
    status='PAPER_RESTING' if paper else 'LIVE_PLAN_NOT_SUBMITTED'
    for stage,limit,qty in ((1,t1,a),(2,t2,b)):
        if qty<=0:continue
        existing=c.execute("""SELECT limit_price,original_qty,order_qty,filled_qty,status
            FROM nl1118_resting_sells WHERE position_id=? AND stage=?""",
            (position_id,stage)).fetchone()
        if existing is not None:
            _follow_policy_goal(c,position_id,stage,existing,n,limit,qty)
            continue
        c.execute("""INSERT OR IGNORE INTO nl1118_resting_sells(
            position_id,stage,policy,market,symbol,frame,limit_price,
            original_qty,order_qty,filled_qty,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,'0',?,?,?)""",
            (position_id,stage,'NEW_LEGACY_1118',market,symbol,frame,
             str(limit),str(n),str(qty),status,str(entry_at),str(entry_at)))


def _follow_policy_goal(c,position_id,stage,existing,original,limit,goal):
    """Keep an UNFINISHED plan equal to the policy's current stage goal.

    A partial top-up BUY raises the position's initial quantity and moves the target, while this
    plan was written once from the first fill. Left alone, every normal-sized SELL exceeded the
    plan and was rejected (1,589 times in one export). Fills and cancelled plans are never
    rewritten; the plan never drops below what has already been filled.
    """
    status=str(existing['status'])
    if status.startswith('PAPER_CANCELLED'):return
    filled=amount(existing['filled_qty']) or Decimal(0)
    want=max(goal,filled)
    old_qty=amount(existing['order_qty'])
    old_limit=amount(existing['limit_price'])
    old_original=amount(existing['original_qty'])
    if old_qty==want and old_limit==limit and old_original==original:return
    if status=='LIVE_PLAN_NOT_SUBMITTED':new_status=status
    elif filled>=want:new_status='PAPER_FILLED'
    elif filled>0:new_status='PAPER_PARTIAL'
    else:new_status='PAPER_RESTING'
    c.execute("""UPDATE nl1118_resting_sells SET limit_price=?,original_qty=?,order_qty=?,
        status=?,updated_at=datetime('now') WHERE position_id=? AND stage=?""",
        (str(limit),str(original),str(want),new_status,position_id,stage))


def confirmed_fill(c,position_id,stage,execution_id):
    """Associate one existing native SELL receipt with one stage, once."""
    initialize(c)
    order=c.execute("""SELECT * FROM nl1118_resting_sells
        WHERE position_id=? AND stage=?""",(position_id,stage)).fetchone()
    if order is None:return False
    # An exchange order was NOT submitted in this PAPER simulator. Do not
    # claim an actual exchange execution from a plan.
    if str(order['status'])=='LIVE_PLAN_NOT_SUBMITTED':return False
    proof=c.execute("""SELECT id,qty,price FROM executions
        WHERE id=? AND position_id=? AND side='SELL'""",
        (execution_id,position_id)).fetchone()
    if proof is None:raise ValueError('NL1118_RESTING_NO_NATIVE_SELL_RECEIPT')
    if c.execute("""SELECT 1 FROM nl1118_resting_fill_allocations
        WHERE execution_id=? AND position_id=? AND stage=?""",
        (execution_id,position_id,stage)).fetchone():return False
    qty=amount(proof['qty'])
    price=amount(proof['price'])
    limit=amount(order['limit_price'])
    prior=amount(order['filled_qty'])
    total=amount(order['order_qty'])
    if (any(x is None for x in (qty,price,limit,prior,total))
        or qty<=0 or price<limit):
        raise ValueError('NL1118_RESTING_SELL_MISMATCH')
    if prior+qty>total+Decimal('.0000000001'):
        # The wallet's SELL is the authority. The plan grows to cover it and the event is recorded;
        # a plan that is too small must never be a reason to reject a genuine sale.
        record_fault(c,position_id,stage,
            'PLAN_GREW order_qty=%s -> %s by execution %s'%(total,prior+qty,execution_id))
        total=prior+qty
        c.execute("""UPDATE nl1118_resting_sells SET order_qty=? WHERE position_id=? AND stage=?""",
                  (str(total),position_id,stage))
    new=min(total,prior+qty)
    c.execute("""INSERT INTO nl1118_resting_fill_allocations(
        execution_id,position_id,stage,quantity) VALUES(?,?,?,?)""",
        (execution_id,position_id,stage,str(qty)))
    c.execute("""UPDATE nl1118_resting_sells
        SET filled_qty=?,status=?,updated_at=datetime('now')
        WHERE position_id=? AND stage=?""",
        (str(new),'PAPER_FILLED' if new>=total else 'PAPER_PARTIAL',
         position_id,stage))
    return True


def cancel_remaining(c,position_id,reason='PROTECTIVE_EXIT'):
    """Stop two PAPER targets when a protective full exit takes priority."""
    initialize(c)
    c.execute("""UPDATE nl1118_resting_sells
        SET status=?,updated_at=datetime('now')
        WHERE position_id=? AND status IN ('PAPER_RESTING','PAPER_PARTIAL')""",
        ('PAPER_CANCELLED_'+str(reason)[:32],position_id))


def rows(c,position_id):
    initialize(c)
    return [dict(r) for r in c.execute("""SELECT position_id,stage,limit_price,
        original_qty,order_qty,filled_qty,status,broker_order_id,created_at
        FROM nl1118_resting_sells WHERE position_id=? ORDER BY stage""",
        (position_id,)).fetchall()]
