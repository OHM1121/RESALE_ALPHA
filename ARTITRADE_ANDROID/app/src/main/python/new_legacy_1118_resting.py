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


def initialize(c):
    c.execute(_CREATE)
    c.execute(_ALLOC)


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
        c.execute("""INSERT OR IGNORE INTO nl1118_resting_sells(
            position_id,stage,policy,market,symbol,frame,limit_price,
            original_qty,order_qty,filled_qty,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,'0',?,?,?)""",
            (position_id,stage,'NEW_LEGACY_1118',market,symbol,frame,
             str(limit),str(n),str(qty),status,str(entry_at),str(entry_at)))


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
        or qty<=0 or price<limit or prior+qty>total+Decimal('.0000000001')):
        raise ValueError('NL1118_RESTING_SELL_MISMATCH')
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
