"""Owner-isolated, atomic native PAPER stage reconciliation for ARTITRADE 0.0.19.

This is a guarded internal transaction adapter, NOT a generic price-to-SELL
API. An authentic source BUY, immutable persisted plan, complete chronological
evidence and a private backup + anti-rollback witness are prerequisites.
Only the ORIGINAL position's SQLite owner is written. A user-approved
PAPER_HISTORICAL_LIMIT_ASSUMPTION is distinct from a broker/live receipt.

No writer is invoked on app import or on UI refresh. PRIVATE original device
data is never copied to Github, tests use synthetic wallets only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
from pathlib import Path

from paper_exit_replay_v019 import OWNERS,positive,timestamp

TAG="PAPER_V019_ORIGINAL_OWNER_ATOMIC_HISTORICAL_ASSUMPTION"
OWNER_DBS={
 "stock/at":"stock/ARTITRADE_D.sqlite3",
 "stock/ma":"stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
 "stock/cw":"stock/close/AT_CLOSE_PAPER.sqlite3",
 "stock/trend":"stock/trend/market_trend_wallet_v054.sqlite3",
 "coin/general":"coin/base/upbit_paper.sqlite3",
 "coin/ma":"coin/ma_epoch_v067/upbit_paper.sqlite3",
 "coin/trend":"coin/trend/market_trend_wallet_v054.sqlite3",
}
STAGE_POLICIES={
 "stock/at":("NL1118_TP1","NL1118_TP2"),
 "coin/general":("NL1118_TP1","NL1118_TP2"),
 "stock/ma":("MA_V1118_UP_FIRST_30PCT","MA_V1118_UP_SECOND_35PCT"),
 "coin/ma":("MA_V1118_UP_FIRST_30PCT","MA_V1118_UP_SECOND_35PCT"),
 "stock/trend":("MT_TP1","MT_TP2"),
 "coin/trend":("MT_TP1","MT_TP2"),
 "stock/cw":("FIRST_TARGET",None),
}
_CREATE="""CREATE TABLE IF NOT EXISTS v019_paper_replay_applied(
    epoch TEXT NOT NULL,owner TEXT NOT NULL,position_id TEXT NOT NULL,
    stage INTEGER NOT NULL CHECK(stage IN (1,2)),execution_id INTEGER NOT NULL,
    evidence_sha256 TEXT NOT NULL,source_event_at TEXT NOT NULL,
    receipt_reason TEXT NOT NULL,original_qty TEXT NOT NULL,
    recovered_qty TEXT NOT NULL,recovered_price TEXT NOT NULL,
    original_buy_receipt TEXT NOT NULL,applied_at TEXT NOT NULL,
    PRIMARY KEY(epoch,owner,position_id,stage)
)"""

class UnsafeRecovery(RuntimeError):pass

def _utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def _existing(c,table):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None

def _columns(c,table):
    return {str(row[1]) for row in c.execute("PRAGMA table_info("+table+")")}

def _d(x):
    val=positive(x)
    if val is None:raise UnsafeRecovery("UNVERIFIED_QUANTITY_OR_PRICE")
    return val

def _row(c,sql,params=()):
    r=c.execute(sql,params).fetchone()
    if r is None:raise UnsafeRecovery("NATIVE_SOURCE_ROW_NOT_FOUND")
    return r

def _integer(x):
    v=_d(x)
    if v!=v.to_integral_value() or v>2**63-1:
        raise UnsafeRecovery("NATIVE_STOCK_NONINTEGER_QUANTITY")
    return int(v)

def _source_mode(c,owner,p):
    if owner.endswith("/trend"):
        if str(p["execution_mode"])!="PAPER":
            raise UnsafeRecovery("ORIGINAL_TREND_BROKER_POSITION")
    if "execution_mode" in p.keys() and str(p["execution_mode"]).upper()=="LIVE":
        raise UnsafeRecovery("ORIGINAL_LIVE_POSITION")
    if "broker_order_id" in p.keys() and str(p["broker_order_id"] or ""):
        raise UnsafeRecovery("ORIGINAL_BROKER_ORDER_ID_EXISTS")
    if owner=="stock/at":
        # Ancient V1.8 adopted positions with separate reserve MUST NOT
        # be booked through the new NEW LEGACY accounting.
        if _existing(c,"v19_legacy_members") and c.execute(
            "SELECT 1 FROM v19_legacy_members WHERE position_id=?",
            (int(p["id"]),)).fetchone():
            raise UnsafeRecovery("GRANDFATHERED_ACCOUNT_PROTECTED")

def _validate_buy(c,owner,p,expected_receipt,original_qty):
    if not _existing(c,"executions"):
        raise UnsafeRecovery("SOURCE_EXECUTIONS_MISSING")
    cols=_columns(c,"executions")
    pid=int(p["id"])
    q=c.execute("SELECT * FROM executions WHERE position_id=? AND UPPER(side)='BUY' ORDER BY id",
                (pid,)).fetchall()
    if not q:raise UnsafeRecovery("IMMUTABLE_BUY_RECEIPT_MISSING")
    ids={str(x["id"]) for x in q}
    # The source may have more than one legitimate partial BUY. Prove exact
    # aggregate original initial quantity and a receipt from this cohort.
    if str(expected_receipt) not in ids:
        raise UnsafeRecovery("SOURCE_BUY_RECEIPT_UNLINKED")
    sum_qty=sum((_d(x["qty"]) for x in q),Decimal(0))
    if sum_qty!=original_qty:
        raise UnsafeRecovery("ORIGINAL_BUY_SUM_NOT_INITIAL_QUANTITY")
    mode_col="execution_mode" in cols
    if mode_col and any(str(x["execution_mode"])!="PAPER" for x in q):
        raise UnsafeRecovery("BROKER_BUY_RECEIPT_PROTECTED")

def _validate_native_plan(c,owner,p,stage,limit,qty,epoch):
    if owner=="stock/cw":
        if stage!=1:raise UnsafeRecovery("CW_SECOND_STAGE_NOT_DEFINED")
        if str(p["strategy"])!="CLOSE":
            raise UnsafeRecovery("ORIGINAL_CW_OWNER_MISMATCH")
        from at_close_engine import POLICY
        if float(POLICY["CLOSE"]["first"])!=1.2:
            raise UnsafeRecovery("ORIGINAL_CW_POLICY_CHANGED")
        expected=Decimal(str(p["entry_price"]))*Decimal("1.012")
        if limit!=expected or qty!=_d(p["entry_qty"]):
            raise UnsafeRecovery("CW_ORIGINAL_LIMIT_OR_FULL_QTY_MISMATCH")
        return
    if not _existing(c,"nl1118_resting_sells"):
        raise UnsafeRecovery("PERSISTED_STAGE_TABLE_MISSING")
    s=_row(c,"SELECT * FROM nl1118_resting_sells WHERE position_id=? AND stage=?",
           (int(p["id"]),stage))
    filled=Decimal(str(s["filled_qty"]))
    if (not filled.is_finite() or filled<0 or
        s["policy"]!="NEW_LEGACY_1118" or
        str(s["status"]) not in ("PAPER_RESTING","PAPER_PARTIAL") or
        str(s["broker_order_id"] or "") or
        _d(s["limit_price"])!=limit or
        _d(s["original_qty"])!=_initial_qty(owner,p) or
        filled>_d(s["order_qty"])):
        raise UnsafeRecovery("NATIVE_ORIGINAL_PLAN_CONFLICT")
    if owner.startswith("coin") and s["market"]!="coin":
        raise UnsafeRecovery("NATIVE_PLAN_MARKET_CONFLICT")
    if owner.startswith("stock") and s["market"]!="stock":
        raise UnsafeRecovery("NATIVE_PLAN_MARKET_CONFLICT")
    if s["frame"]!=str(p["tf"]) or s["symbol"]!=_symbol(owner,p):
        raise UnsafeRecovery("NATIVE_PLAN_SYMBOL_OR_FRAME_MISMATCH")
    expected_stored=_d(s["order_qty"])
    already=Decimal(str(s["filled_qty"]))
    if expected_stored-already!=qty:
        raise UnsafeRecovery("NATIVE_STAGE_REMAINING_QTY_CHANGED")
    # If the source stage has already sold a part, exact ledger allocations
    # must be verifiable before another assumed PAPER partial fill.
    if already>0:
        if not _existing(c,"nl1118_resting_fill_allocations"):
            raise UnsafeRecovery("NATIVE_PARTIAL_ALLOCATION_MISSING")
        totals=c.execute("SELECT quantity FROM nl1118_resting_fill_allocations WHERE position_id=? AND stage=?",
                         (int(p["id"]),stage)).fetchall()
        if sum((Decimal(str(x[0])) for x in totals),Decimal(0))!=already:
            raise UnsafeRecovery("NATIVE_PARTIAL_ALLOCATION_MISMATCH")

def _symbol(owner,p):
    if owner.startswith("coin") and not owner.endswith("/trend"):return str(p["market"])
    if owner.endswith("/trend"):return str(p["symbol"])
    return str(p["code"])

def _initial_qty(owner,p):
    if owner=="stock/at" or owner=="stock/cw":return _d(p["entry_qty"])
    if owner=="stock/ma":return _d(p["qty0"])
    return _d(p["qty0"])

def _available(owner,p):
    return _d(p["qty"])

def _price(owner,limit,observed):
    # The assumed limit must not give a better result than the actual
    # authenticated print. Require an exact tradeable source price for stocks.
    obs=_d(observed)
    if obs<=limit:raise UnsafeRecovery("HISTORICAL_OVERSHOOT_NOT_STRICT")
    if owner.startswith("stock"):
        if obs!=obs.to_integral_value():
            raise UnsafeRecovery("STOCK_SOURCE_PRINT_NOT_INTEGER")
        # Executing at actual observed trade PRICE is a disclosed PAPER-only
        # assumption; source prints and user-approved TP are never REAL fills.
        return int(obs)
    # Upbit original _sell accepts a Decimal, and fee/qty rules are native.
    return obs

def _source_identity(c,owner,p,proven):
    pid=str(p["id"])
    if proven.get("owner")!=owner or str(proven.get("position_id"))!=pid:
        raise UnsafeRecovery("REPLAY_OWNER_OR_POSITION_MISMATCH")
    if proven.get("status")!="PAPER_ASSUMPTION_CANDIDATES_VERIFIED":
        raise UnsafeRecovery("CHRONOLOGY_NOT_VERIFIED")
    if not str(proven.get("digest_sha256") or ""):
        raise UnsafeRecovery("REPLAY_DIGEST_MISSING")
    if _initial_qty(owner,p)!=_d(proven["original_qty"]):
        raise UnsafeRecovery("REPLAY_ORIGINAL_QUANTITY_MISMATCH")
    if _available(owner,p)!=_d(proven["source_remaining_qty"]):
        raise UnsafeRecovery("REPLAY_NATIVE_REMAINING_MISMATCH")

def _receipt_reason(owner,stage):
    return "PAPER_V019_HISTORICAL_LIMIT_ASSUMED_TP"+str(stage)

def _settle_stock_at(c,p,stage,qty,px,now,reason,event_key):
    # Use the authoritative D19PaperEngine._sell accounting including FIX8
    # member initial_won and cumulative prorated original BUY fee.
    from d_v19_portfolio import D19PaperEngine
    # Original native method references only SQL and _log in PAPER. Its
    # diagnostic log normally says LIVE_0B; skip that diagnostic for a
    # clearly historical assumed limit. The native SELL receipt carries
    # PAPER_V019 in its reason and private replay receipt records evidence.
    engine=object.__new__(D19PaperEngine)
    engine._log=lambda *_args,**_kwargs:None
    sold=D19PaperEngine._sell(engine,c,p,now,int(qty),int(px),reason,event_key=event_key)
    if sold is None:raise UnsafeRecovery("D19_NATIVE_OWNER_SELL_FAILED")

def _settle_stock_ma(c,p,stage,qty,px,now,reason,event_key):
    from stock_ma_engine import FEE,SELL_TAX
    from ma_v067_stock_runtime import _read_state,_store_state
    import ma_v067_policy as policy
    kind,state=_read_state(c,int(p["id"]))
    if kind!="REVERSAL_UP" or not isinstance(state,dict):
        raise UnsafeRecovery("STOCK_MA_ORIGINAL_UP_POLICY_REQUIRED")
    amount=int(qty);rate=int(px);entry=int(p["entry"])
    gross=amount*rate
    fee=round(gross*FEE)
    tax=round(gross*SELL_TAX)
    net=gross-amount*entry-fee-tax-round(amount*entry*FEE)
    a=c.execute("UPDATE accounts SET cash=cash+?,realized=realized+? WHERE tf=?",
                (gross-fee-tax,net,p["tf"]))
    if a.rowcount!=1:raise UnsafeRecovery("STOCK_MA_ACCOUNT_NOT_OWNED")
    c.execute("""INSERT INTO executions(tf,position_id,code,name,ts,side,qty,
        price,fees,tax,net_profit,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (p["tf"],p["id"],p["code"],p["name"],now,"SELL",amount,rate,fee,tax,net,reason))
    rest=int(p["qty"])-amount
    if c.execute("UPDATE positions SET qty=?,status=?,last_price=? WHERE id=? AND status='OPEN'",
            (rest,"CLOSED" if rest==0 else "OPEN",rate,p["id"])).rowcount!=1:
        raise UnsafeRecovery("STOCK_MA_SOURCE_POSITION_MOVED")
    native_reason=STAGE_POLICIES["stock/ma"][stage-1]
    applied=policy.apply_up_fill(state,native_reason,amount,
        dt.datetime.fromisoformat(now),p["qty0"],rate)
    _store_state(c,p["id"],applied,dt.datetime.fromisoformat(now))
    first,second=policy.up_stage_quantities(applied,p["qty0"])
    s1=policy.number(applied.get("first_sold")) or Decimal(0)
    s2=policy.number(applied.get("second_sold")) or Decimal(0)
    c.execute("UPDATE positions SET first_sold=?,second_sold=? WHERE id=?",
              (int(first>0 and s1>=first),int(second>0 and s2>=second),p["id"]))

def _settle_coin(c,owner,p,stage,qty,px,now,reason,event_key):
    from d_upbit import fmt_qty,krw_fee,money,qty_floor
    q=qty_floor(qty)
    if q!=qty:raise UnsafeRecovery("NATIVE_COIN_QTY_STEP_MISMATCH")
    original=_d(p["qty0"])
    current=_d(p["qty"])
    if q>current:raise UnsafeRecovery("NATIVE_COIN_SELL_EXCEEDS_HOLDING")
    sold=original-current
    gross=q*Decimal(str(px))
    fee=krw_fee(gross)
    prior_fee=money(Decimal(str(p["entry_fee"]))*sold/original)
    after_fee=money(Decimal(str(p["entry_fee"]))* (sold+q)/original)
    prorated=after_fee-prior_fee
    net=money(gross-q*Decimal(str(p["entry"]))-fee-prorated)
    w=_row(c,"SELECT cash FROM wallets WHERE tf=?",(p["tf"],))
    cash=int(w["cash"])+money(gross-fee)
    if c.execute("UPDATE wallets SET cash=?,realized=realized+? WHERE tf=?",
                 (cash,net,p["tf"])).rowcount!=1:
        raise UnsafeRecovery("NATIVE_COIN_WALLET_OWNER_MISSING")
    s1=Decimal(str(p["first_sold_qty"])) if "first_sold_qty" in p.keys() else Decimal(0)
    if stage==1:
        s1=min(_d(p["first_qty"]),s1+q)
    rest=current-q
    if c.execute("UPDATE positions SET qty=?,first_sold_qty=?,mark=?,status=? WHERE id=? AND status='OPEN'",
                 (fmt_qty(rest),fmt_qty(s1),str(px),
                  "CLOSED" if rest==0 else "OPEN",p["id"])).rowcount!=1:
        raise UnsafeRecovery("NATIVE_COIN_POSITION_MOVED")
    c.execute("""INSERT INTO executions(event_key,position_id,tf,market,at,side,
        qty,price,fees,tax,net_pnl,reason,cash_after)
        VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?)""",
        (event_key,p["id"],p["tf"],p["market"],now,"SELL",fmt_qty(q),str(px),
         fee,net,reason,cash))
    if owner=="coin/ma":
        import ma_v067_policy as policy
        kind,state=_row(c,"SELECT kind,state_json FROM ma_v067_positions WHERE position_id=?",
                         (p["id"],))
        if kind!="REVERSAL_UP":raise UnsafeRecovery("NATIVE_COIN_MA_KIND_NOT_UP")
        try:state=json.loads(state)
        except (ValueError,TypeError):raise UnsafeRecovery("NATIVE_COIN_MA_STATE_INVALID")
        native_reason=STAGE_POLICIES["coin/ma"][stage-1]
        applied=policy.apply_up_fill(state,native_reason,q,
                                    dt.datetime.fromisoformat(now),p["qty0"],px)
        if c.execute("UPDATE ma_v067_positions SET state_json=?,updated_at=? WHERE position_id=?",
              (json.dumps(applied,ensure_ascii=False),now,p["id"])).rowcount!=1:
            raise UnsafeRecovery("NATIVE_COIN_MA_SIDECAR_MOVED")

def _settle_cw(c,p,stage,qty,px,now,reason,event_key):
    from at_close_engine import charge,SELL_FEE,SELL_TAX
    amount=int(qty);price=int(px)
    if amount!=int(p["qty"]):
        raise UnsafeRecovery("CW_ORIGINAL_TP_MUST_SELL_ALL")
    gross=amount*price
    fee=charge(gross,SELL_FEE)
    tax=charge(gross,SELL_TAX)
    prorated=int(round(int(p["entry_fee"])*amount/int(p["entry_qty"])))
    realized=gross-amount*int(p["entry_price"])-fee-tax-prorated
    wallet=_row(c,"SELECT cash FROM wallets WHERE strategy='CLOSE'")
    cash=int(wallet["cash"])+gross-fee-tax
    if c.execute("UPDATE wallets SET cash=?,realized=realized+?,updated_at=? WHERE strategy='CLOSE'",
          (cash,realized,now)).rowcount!=1:
        raise UnsafeRecovery("CW_NATIVE_WALLET_MISSING")
    c.execute("""INSERT INTO executions(ts,strategy,position_id,code,name,side,
        qty,price,fee,tax,cash_after,reason,net_realized)
        VALUES(?,'CLOSE',?,?,?,?,?,?,?,?,?,?,?)""",
        (now,p["id"],p["code"],p["name"],"SELL",amount,price,fee,tax,
         cash,reason,realized))
    if c.execute("""UPDATE positions SET qty=0,status='CLOSED',closed_ts=?,
                   exit_reason=?,last_price=?,first_sold=1 WHERE id=? AND status='OPEN'""",
                 (now,reason,price,p["id"])).rowcount!=1:
        raise UnsafeRecovery("CW_POSITION_MOVED")

def _settle_trend(c,owner,p,stage,qty,px,now,reason,event_key):
    from market_trend_wallet_v054 import _money,STOCK_FEE,STOCK_SELL_TAX,COIN_FEE
    from market_trend_v043 import TrendPosition,_as_kst
    from decimal import Decimal
    market="stock" if owner.startswith("stock") else "coin"
    initial=_d(p["qty0"])
    original=_d(p["qty"])
    if market=="stock":
        amount=int(qty);rate=int(px)
    else:
        from market_trend_wallet_v043 import _coin_qty
        amount=_coin_qty(qty)
        if Decimal(str(amount))!=qty:
            raise UnsafeRecovery("TREND_COIN_ORIGINAL_STEP_CONFLICT")
        rate=float(px)
    gross=_money(float(amount)*rate)
    fee=_money(gross*(STOCK_FEE if market=="stock" else COIN_FEE))
    tax=_money(gross*STOCK_SELL_TAX) if market=="stock" else 0
    cost=_money(int(p["entry_fee"])*float(amount)/float(initial))
    pnl=gross-_money(float(amount)*float(p["entry"]))-fee-tax-cost
    if c.execute("UPDATE accounts SET cash=cash+?,realized=realized+? WHERE tf=?",
                 (gross-fee-tax,pnl,p["tf"])).rowcount!=1:
        raise UnsafeRecovery("TREND_NATIVE_ACCOUNT_MISSING")
    cash=_row(c,"SELECT cash FROM accounts WHERE tf=?",(p["tf"],))["cash"]
    try:
        obj=json.loads(p["payload"])
        state=TrendPosition(market,"trend",p["tf"],p["symbol"],
            obj["entry_at"],p["entry"],p["qty0"],p["bar_key"])
        state.__dict__.update(obj)
        state.entry_at=_as_kst(obj["entry_at"])
        state.deadline=_as_kst(obj["deadline"])
        state.qty=float(p["qty"])
        state.__dict__.update({"position_id":int(p["id"]),
            "execution_mode":"PAPER","broker_order_id":""})
        state.apply_sell(STAGE_POLICIES[owner][stage-1],float(amount),float(rate))
        payload=json.dumps(state.to_dict(),ensure_ascii=False,default=str)
    except (KeyError,TypeError,ValueError,AttributeError) as exc:
        raise UnsafeRecovery("TREND_ORIGINAL_PAYLOAD_CANNOT_UPDATE") from exc
    rest=original-qty
    if c.execute("""UPDATE positions SET qty=?,mark=?,status=?,payload=?,
                   updated_at=? WHERE id=? AND status='TREND_OPEN'""",
                 (float(rest),rate,"CLOSED" if rest==0 else "TREND_OPEN",
                  payload,now,p["id"])).rowcount!=1:
        raise UnsafeRecovery("TREND_NATIVE_POSITION_MOVED")
    c.execute("""INSERT INTO executions(event_key,position_id,tf,symbol,at,side,
          qty,price,fees,tax,net_pnl,cash_after,execution_mode,broker_order_id,reason)
          VALUES(?,?,?,?,?,'SELL',?,?,?,?,?,?,'PAPER','',?)""",
          (event_key,p["id"],p["tf"],p["symbol"],now,float(amount),rate,
           fee,tax,pnl,cash,reason))

def _confirm_native_intent(c,owner,p,stage,receipt):
    if owner=="stock/cw":return
    import new_legacy_1118_resting as resting
    if not resting.confirmed_fill(c,int(p["id"]),stage,int(receipt)):
        raise UnsafeRecovery("NATIVE_RESTING_STAGE_CONFIRMATION_FAILED")
    if owner in ("stock/at","coin/general"):
        if _existing(c,"nl1118_reference_positions"):
            r=c.execute("""SELECT intent_json FROM nl1118_reference_positions
                WHERE position_id=? AND policy='NEW_LEGACY_1118'""",
                (p["id"],)).fetchone()
            if r:
                try:
                    plan=json.loads(r["intent_json"])
                    intents=plan["sell_intents"]
                    item=intents[stage-1]
                    item["state"]="FILLED"
                    item["filled_qty"]=str(_d(c.execute(
                        "SELECT qty FROM executions WHERE id=?",(receipt,)).fetchone()[0]))
                except (ValueError,TypeError,KeyError,IndexError):
                    raise UnsafeRecovery("REFERENCE_NATIVE_STAGE_JSON_MISMATCH")
                c.execute("""UPDATE nl1118_reference_positions SET intent_json=?,
                    updated_at=? WHERE position_id=?""",
                    (json.dumps(plan,ensure_ascii=False),_utc(),p["id"]))

def _guard_before(c,owner,proven):
    pid=str(proven["position_id"])
    p=_row(c,"SELECT * FROM positions WHERE id=?",(int(pid),))
    state="TREND_OPEN" if owner.endswith("/trend") else "OPEN"
    if str(p["status"])!=state:
        raise UnsafeRecovery("NATIVE_POSITION_NOT_OPEN")
    _source_mode(c,owner,p)
    _source_identity(c,owner,p,proven)
    _validate_buy(c,owner,p,proven["source_buy_receipt"],_initial_qty(owner,p))
    if not _existing(c,"nl1118_resting_sells") and owner!="stock/cw":
        raise UnsafeRecovery("BUY_PERSISTED_PLAN_MISSING")
    if owner=="stock/cw" and str(p["strategy"])!="CLOSE":
        raise UnsafeRecovery("WRONG_CW_STRATEGY")
    return p

def apply_inside_transaction(c,owner,proof,epoch,observed_prices):
    """Call ONLY after private proof replay and BEGIN IMMEDIATE.

    The caller owns backup/witness verification. All stage updates share one
    SQLite transaction; any single mismatch rolls back every cash/fee/fill.
    """
    if owner not in OWNER_DBS or owner not in OWNERS:
        raise UnsafeRecovery("ORIGINAL_OWNER_NOT_SQLITE_SETTLEMENT")
    if proof.get("epoch")!=epoch:
        raise UnsafeRecovery("INDEPENDENT_EPOCH_CHANGED")
    if proof.get("status")!="PAPER_ASSUMPTION_CANDIDATES_VERIFIED":
        raise UnsafeRecovery("UNVERIFIED_CHRONOLOGICAL_PROOF")
    plans=proof.get("stages")
    if not isinstance(plans,list) or not plans or len(plans)>2:
        raise UnsafeRecovery("STAGE_COUNT_UNVERIFIED")
    if not isinstance(observed_prices,dict):
        raise UnsafeRecovery("ORIGINAL_PRINT_PRICES_NOT_LINKED")
    c.row_factory=sqlite3.Row
    c.execute(_CREATE)
    p=_guard_before(c,owner,proof)
    remain=_available(owner,p)
    results=[]
    # Stage 2 must never be recovered ahead of stage 1. Source first fills
    # already reconciled by original receipt are recognized, not fabricated.
    for item in plans:
        stage=item.get("stage")
        if stage not in (1,2) or stage==2 and owner=="stock/cw":
            raise UnsafeRecovery("UNKNOWN_OR_FORBIDDEN_STAGE")
        try:obs=observed_prices[str(item["evidence_event_id"])]
        except (KeyError,TypeError):raise UnsafeRecovery("SOURCE_PRINT_LOOKUP_MISSING")
        limit=_d(item["limit"])
        qty=_d(item["qty"])
        if owner.startswith("stock") and qty!=qty.to_integral_value():
            raise UnsafeRecovery("STOCK_STAGE_FRACTIONAL")
        if qty>remain:raise UnsafeRecovery("REPLAY_OVERSOLD_NATIVE_POSITION")
        if stage==2:
            if owner!="stock/cw":
                first=_row(c,"SELECT * FROM nl1118_resting_sells WHERE position_id=? AND stage=1",
                           (p["id"],))
                if Decimal(str(first["filled_qty"]))!=_d(first["order_qty"]):
                    raise UnsafeRecovery("TP2_BEFORE_FULLY_CONFIRMED_TP1")
        _validate_native_plan(c,owner,p,stage,limit,qty,epoch)
        if c.execute("""SELECT 1 FROM v019_paper_replay_applied
             WHERE epoch=? AND owner=? AND position_id=? AND stage=?""",
             (epoch,owner,str(p["id"]),stage)).fetchone():
            raise UnsafeRecovery("ALREADY_APPLIED_EXACTLY_ONCE")
        price=_price(owner,limit,obs)
        at=_utc()  # Append current recovery receipt; never reorder past native fills.
        reason=_receipt_reason(owner,stage)
        event_key="V019:"+epoch+":"+owner+":"+str(p["id"])+":"+str(stage)
        if "event_key" in _columns(c,"executions") and c.execute(
             "SELECT 1 FROM executions WHERE event_key=?",(event_key,)).fetchone():
            raise UnsafeRecovery("NATIVE_EVENT_KEY_ALREADY_PRESENT")
        current=_row(c,"SELECT * FROM positions WHERE id=?",(p["id"],))
        if owner=="stock/at":
            _settle_stock_at(c,current,stage,qty,price,at,reason,event_key)
        elif owner=="stock/ma":
            _settle_stock_ma(c,current,stage,qty,price,at,reason,event_key)
        elif owner in ("coin/general","coin/ma"):
            _settle_coin(c,owner,current,stage,qty,price,at,reason,event_key)
        elif owner=="stock/cw":
            _settle_cw(c,current,stage,qty,price,at,reason,event_key)
        elif owner.endswith("/trend"):
            _settle_trend(c,owner,current,stage,qty,price,at,reason,event_key)
        else:raise UnsafeRecovery("OWNER_NATIVE_SETTLEMENT_UNKNOWN")
        receipt=c.execute("SELECT id FROM executions WHERE position_id=? AND UPPER(side)='SELL' AND reason=? ORDER BY id DESC LIMIT 1",
                          (p["id"],reason)).fetchone()
        if receipt is None:raise UnsafeRecovery("NATIVE_SELL_RECEIPT_NOT_COMMITTED")
        recid=int(receipt[0])
        _confirm_native_intent(c,owner,p,stage,recid)
        c.execute("""INSERT INTO v019_paper_replay_applied(
            epoch,owner,position_id,stage,execution_id,evidence_sha256,
            source_event_at,receipt_reason,original_qty,recovered_qty,recovered_price,
            original_buy_receipt,applied_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (epoch,owner,str(p["id"]),stage,recid,proof["digest_sha256"],
             item["at"],reason,str(_initial_qty(owner,p)),str(qty),str(price),
             proof["source_buy_receipt"],at))
        remain-=qty
        results.append({"stage":stage,"receipt_id":recid,
                        "assumed_qty":str(qty),"assumed_price":str(price)})
    after=_row(c,"SELECT qty FROM positions WHERE id=?",(p["id"],))
    actual=Decimal(str(after["qty"]))
    if not actual.is_finite() or actual!=remain:
        raise UnsafeRecovery("NATIVE_REMAINING_QUANTITY_MISMATCH")
    return {"status":"PAPER_NATIVE_RECONCILED","owner":owner,"position_id":p["id"],
            "receipt_count":len(results),"receipts":results,
            "broker_receipt":False,"original_position_preserved":True}
