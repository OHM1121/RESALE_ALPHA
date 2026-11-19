"""0.0.19 read-only, per-original-wallet staged exit target projection.

No broker-limit order is submitted or implied. No wallet/SQLite writes, cash
changes, candidate buys, SELL receipts, hindsight candle highs or market depth
are used. Six NEW LEGACY owners already persist their actual PAPER plans in
their independent native wallets on BUY; GMO and CLOSE retain their immutable
ORIGINAL strategy thresholds and do not acquire 30%/35% exits.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, InvalidOperation
import json
import math
import sqlite3
from pathlib import Path
from urllib.parse import quote

_TAG="V019_ORIGINAL_OWNER_BUY_PRICE_TARGET_PLAN_VIEW_ONLY"
_NON_EXECUTABLE="PLAN_ONLY_NO_BROKER_ORDER_OR_PAPER_FILL"
_COIN_PRECISION=Decimal("0.00000001")
_ALLOWED=frozenset((
    "stock/at","stock/ma","stock/gmo","stock/cw","stock/trend",
    "coin/general","coin/ma","coin/trend",
))

def _decimal(value):
    if isinstance(value,bool):return None
    try:
        v=Decimal(str(value))
        return v if v.is_finite() and v>0 else None
    except (InvalidOperation,TypeError,ValueError,OverflowError):
        return None

def _first(row,*keys):
    for k in keys:
        v=_decimal(row.get(k))
        if v is not None:return v
    return None

def _original_qty(row,market):
    initial=_first(row,"qty0","entry_qty","total_qty","original_qty",
                   "initial_qty")
    if initial is None:return None
    # Never treat a CURRENT reduced position as original quantity:
    # it would show 30% of the remainder after a partial native SELL.
    if market=="stock":
        if initial!=initial.to_integral_value():return None
        return initial
    return initial.quantize(_COIN_PRECISION,rounding=ROUND_DOWN)

def _nl_quantities(q,market):
    if market=="stock":
        base=int(q)
        q1=max(1,min(base,math.ceil(base*.30)))
        q2=max(0,min(base-q1,math.ceil(base*.35)))
        return Decimal(q1),Decimal(q2)
    one=(q*Decimal(".30")).quantize(_COIN_PRECISION,rounding=ROUND_DOWN)
    two=(q*Decimal(".35")).quantize(_COIN_PRECISION,rounding=ROUND_DOWN)
    return min(q,one),min(max(Decimal(0),q-one),two)

def _gmo_native(runtime,row):
    try:
        runner=getattr(runtime,"stock_runner",None)
        service=getattr(runner,"gmo",None)
        engine=getattr(service,"engine",None)
        positions=getattr(engine,"positions",None)
        if not isinstance(positions,dict):return None,None
        code=str(row.get("code") or "")
        p=positions.get(code)
        if p is None:return None,None
        if str(getattr(p,"code",""))!=code:return None,None
        if _decimal(getattr(p,"entry_price",None)) is None:return None,None
        buy=_first(row,"entry_price","buy_price","entry")
        actual=_decimal(getattr(p,"entry_price",None))
        # Only match the *same* original LIVE position. Never reuse an older
        # score tier from a symbol's previous purchase or a research event.
        if buy is None or abs(float(buy-actual))>max(.011,float(buy)*.00001):
            return None,None
        entry_day=str(row.get("entry_date") or
                      row.get("buy_at") or row.get("entry_at") or "")[:10]
        if entry_day and entry_day!=str(getattr(p,"entry_date",""))[:10]:
            return None,None
        return p,getattr(engine,"cfg",None)
    except (AttributeError,TypeError,ValueError,RuntimeError):
        return None,None


def _read_native_resting(root,owner,positions):
    """Read only original committed stages; never open or create a writable DB.

    An old holding without a native persisted plan is NOT silently assigned a
    newly calculated NEW LEGACY limit. Each wallet has its own native owner.
    """
    if root is None or not positions:return {}
    try:
        from position_uid_v015 import _DB_PATHS
        rel=_DB_PATHS.get(owner)
        if not rel:return {}
        path=Path(root)/rel
        if not path.is_file():return {}
        ids=set()
        for p in positions:
            if not isinstance(p,dict):continue
            raw=p.get("id") or p.get("position_id")
            if isinstance(raw,bool):continue
            try:
                pid=int(str(raw))
                if pid>0 and str(pid)==str(raw).strip():ids.add(pid)
            except (TypeError,ValueError,OverflowError):continue
        if not ids:return {}
        link="file:"+quote(str(path.resolve()),safe="/")+"?mode=ro"
        plans={}
        with sqlite3.connect(link,uri=True,timeout=1.5) as c:
            c.row_factory=sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            table=c.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='nl1118_resting_sells'""").fetchone()
            if table is None:return {}
            # SQLite's bound-variable limit is lower on some phone builds.
            sorted_ids=sorted(ids)
            for start in range(0,len(sorted_ids),400):
                part=sorted_ids[start:start+400]
                marks=",".join("?" for _ in part)
                sql=("SELECT * FROM nl1118_resting_sells WHERE position_id IN ("
                     +marks+") ORDER BY position_id,stage")
                for item in c.execute(sql,part):
                    v=dict(item)
                    if v.get("policy")!="NEW_LEGACY_1118":continue
                    pid=int(v["position_id"])
                    stage=int(v["stage"])
                    if stage not in (1,2):continue
                    plans.setdefault(pid,{})[stage]=v
        return plans
    except (OSError,sqlite3.Error,ValueError,TypeError,OverflowError):
        # Optional UI evidence cannot block any authentic native SELL.
        return {}

def _native_staged(owner,row,entry,market,stages):
    """Show a limit only if the matching original BUY persisted the stages."""
    if not isinstance(stages,dict) or 1 not in stages:return None
    first=stages[1]
    second=stages.get(2)
    if first.get("market")!=market or first.get("policy")!="NEW_LEGACY_1118":
        return None
    frame=str(row.get("tf") or row.get("frame") or "")
    if frame not in ("5m","30m","60m","day") or first.get("frame")!=frame:
        return None
    # Retired 5m Trend is only an old held SELL recovery, not a new plan.
    if owner.endswith("/trend") and frame=="5m":return None
    symbol=str(row.get("code") or row.get("market") or row.get("symbol") or "")
    if not symbol or symbol!=str(first.get("symbol") or ""):return None
    initial=_original_qty(row,market)
    original=_decimal(first.get("original_qty"))
    current=_decimal(row.get("qty"))
    if original is None or initial is None or current is None:
        return None
    if original!=initial or current>original:return None
    at=str(row.get("entry_at") or row.get("entry_ts") or "")
    stored_at=str(first.get("created_at") or "")
    # Original position times can carry a timezone, while the native plan
    # sometimes keeps the same instant without a local display conversion.
    # A mismatch is not permission to invent a different native BUY.
    if at and stored_at and at!=stored_at:return None
    if second is not None:
        if (second.get("market")!=market or
            second.get("policy")!="NEW_LEGACY_1118" or
            second.get("frame")!=frame or
            second.get("symbol")!=symbol or
            _decimal(second.get("original_qty"))!=original or
            str(second.get("created_at") or "")!=stored_at):
            return None
    t1=_decimal(first.get("limit_price"))
    q1=_decimal(first.get("order_qty"))
    q1filled=_decimal(first.get("filled_qty")) if str(
        first.get("filled_qty") or "") not in ("0","0.0") else Decimal(0)
    t2=_decimal(second.get("limit_price")) if second else None
    q2=_decimal(second.get("order_qty")) if second else None
    q2filled=(_decimal(second.get("filled_qty")) if second and str(
        second.get("filled_qty") or "") not in ("0","0.0") else Decimal(0))
    if any(x is None for x in (t1,q1,q1filled,q2filled)) or (
        second is not None and (t2 is None or q2 is None)):
        return None
    if q1filled>q1 or q2filled>(q2 or Decimal(0)):return None
    if q1filled+q2filled>original-current:return None
    # The original policy grants GMO/CW no NEW LEGACY stage ownership.
    plan=_make_plan(entry,original,t1,t2,q1,q2,owner,
        "NEW_LEGACY_1118_PERSISTED_NATIVE_PLAN",
        order_state=str(first.get("status") or "UNKNOWN"))
    if plan is None:return None
    plan.update({
        "source":"ORIGINAL_OWNER_SQLITE_NL1118_RESTING",
        "plan_durable_at_buy":True,
        "first_filled_qty":float(q1filled),
        "second_filled_qty":float(q2filled),
        "first_stage_status":str(first.get("status") or "UNKNOWN"),
        "second_stage_status":str(second.get("status") or "NOT_APPLICABLE")
            if second else "NONE",
        "entry_at":at or stored_at,
    })
    return plan

def _make_plan(entry,q,first,second,q1,q2,owner,policy,
               *,order_state="NO_BROKER_ORDER_ACK",score=None):
    if any(v is None or not v.is_finite() or v<=0
           for v in (entry,q,first,q1)):return None
    if first<=entry or q1>q:return None
    if (second is None)!=(q2 is None):return None
    if second is not None and (
        second<=first or q2<0 or q1+q2>q):return None
    return {
        "schema":_TAG,"owner":owner,"policy":policy,
        "first_target":float(first),"first_qty":float(q1),
        "second_target":float(second) if second is not None else None,
        "second_qty":float(q2) if q2 is not None else None,
        "original_qty":float(q),"entry_price":float(entry),
        "first_pct":float((first/entry-1)*100),
        "second_pct_from_first":
            float((second/first-1)*100) if second is not None else None,
        "state":_NON_EXECUTABLE,"actual_broker_order":False,
        "actual_native_sell_receipt":False,"trade_fill_inferred":False,
        "order_state":order_state,"score_tier":score,
        "historic_candle_high_fill_assumed":False,
    }

def preview(owner,row,runtime=None,native_stages=None):
    """Plan from an ORIGINAL position and its ORIGINAL wallet policy only.

    Fail closed when original BUY size, frame, score, ownership or entry price
    is unverified. A planning preview is never a native order or a fill.
    """
    if owner not in _ALLOWED or not isinstance(row,dict):
        return None
    market,strategy=owner.split("/",1)
    if str(row.get("status") or "OPEN").upper() not in ("OPEN","TREND_OPEN"):
        return None
    if _decimal(row.get("qty")) is None:return None
    entry=_first(row,"entry_price","entry","buy_price","avg_buy_price")
    if entry is None:return None

    if owner=="stock/gmo":
        p,cfg=_gmo_native(runtime,row)
        if p is None:return None
        qty=_decimal(getattr(p,"total_qty",None))
        tier=_decimal(getattr(p,"first_profit_target",None))
        if qty is None or qty!=qty.to_integral_value() or tier is None:
            return None
        if tier not in (Decimal(".04"),Decimal(".05"),Decimal(".07")):
            return None
        # Original GMO: tier-specific first +4/5/7%, first floor(20%);
        # second +3% above the first PRICE, floor(40%) of original shares.
        cfg1=_decimal(getattr(cfg,"first_sell_fraction",None))
        cfg2=_decimal(getattr(cfg,"second_sell_fraction",None))
        step=_decimal(getattr(cfg,"second_target_up",None))
        if (cfg1!=Decimal(".20") or cfg2!=Decimal(".40")
            or step!=Decimal(".03")):return None
        q1=Decimal(max(1,math.floor(qty*cfg1)))
        q2=Decimal(min(int(qty-q1),max(1,math.floor(qty*cfg2))))
        first=entry*(1+tier)
        second=first*(1+step)
        pending=str(getattr(p,"pending_sell_id","") or "")
        order_state=("NATIVE_SELL_INTENT_ID_PRESENT_BROKER_ACK_UNVERIFIED"
            if pending else "NO_BROKER_ORDER_ACK")
        plan=_make_plan(entry,qty,first,second,q1,q2,owner,
            "ORIGINAL_GMO_SCORE_TIER_4_5_7_FIRST20_SECOND40",
            order_state=order_state,score=int(round(tier*100)))
        if plan is None:return None
        # Display ORIGINAL engine-reconciled stage quantities. An intent or
        # historical candle observation is not itself a native SELL receipt.
        filled1=_decimal(getattr(p,"first_filled_qty",0))
        filled2=_decimal(getattr(p,"second_filled_qty",0))
        if str(getattr(p,"first_filled_qty",0)) in ("0","0.0"):filled1=Decimal(0)
        if str(getattr(p,"second_filled_qty",0)) in ("0","0.0"):filled2=Decimal(0)
        if (filled1 is not None and filled2 is not None and
            filled1<=q1 and filled2<=q2 and filled1+filled2<=qty):
            plan["first_filled_qty"]=float(filled1)
            plan["second_filled_qty"]=float(filled2)
            plan["native_stage_quantity_source"]="ORIGINAL_GMO_ENGINE"
        return plan

    qty=_original_qty(row,market)
    # CW original full-position target can be observed only when its native
    # source position includes a verified initial quantity. Never use the
    # currently reduced holding as an assumed original BUY size.
    if qty is None or qty<=0:return None

    if owner=="stock/cw":
        # Original at_close_engine.POLICY['CLOSE']:
        # first=final=+1.2% and FIRST_TARGET exits ALL original shares.
        try:
            from at_close_engine import POLICY
            pol=POLICY.get("CLOSE",{})
            gain=Decimal(str(pol.get("first")))
            final=Decimal(str(pol.get("final")))
        except (ImportError,TypeError,ValueError,KeyError):
            return None
        if gain!=Decimal("1.2") or final!=gain:return None
        first=entry*(1+gain/100)
        return _make_plan(entry,qty,first,None,qty,None,owner,
            "ORIGINAL_CW_FIRST_PLUS_1_2_FULL_EXIT_NO_SECOND")

    # The six workbook strategies must use their native BUY-persisted plans,
    # not a fresh target computed from a possibly old/partial holding.
    return _native_staged(owner,row,entry,market,native_stages)

def decorate(packet,runtime=None):
    """Add visual projections across the eight exact owner paths.

    This operates AFTER all original strategy hub decorators and position UID
    enrichment and never mutates execution rows, native wallet or SQLite.
    """
    if not isinstance(packet,dict):return packet
    from position_uid_v015 import _walk
    seen=set()
    counts={k:0 for k in _ALLOWED}
    skipped=0
    for owner,ledger in _walk(packet):
        if owner not in _ALLOWED or not isinstance(ledger,dict):
            continue
        key=(owner,id(ledger))
        if key in seen:continue
        seen.add(key)
        positions=ledger.get("positions")
        if not isinstance(positions,list):continue
        committed=_read_native_resting(getattr(runtime,"root",None),owner,positions)
        for row in positions:
            if not isinstance(row,dict):continue
            try:
                source_id=int(row.get("id") or row.get("position_id") or 0)
            except (ValueError,TypeError,OverflowError):source_id=0
            planned=preview(owner,row,runtime=runtime,
                native_stages=committed.get(source_id))
            if planned:
                planned["position_id"]=row.get("id") or row.get("position_id")
                planned["position_uid"]=row.get("position_uid") or ""
                planned["entry_at"]=planned.get("entry_at") or str(
                    row.get("buy_at") or row.get("entry_at") or
                    row.get("entry_ts") or row.get("entry_date") or "")
                if owner=="stock/gmo":
                    planned["source"]="ORIGINAL_GMO_NATIVE_ENGINE"
                    planned["plan_durable_at_buy"]=bool(planned["entry_at"])
                elif owner=="stock/cw":
                    planned["source"]="ORIGINAL_CW_NATIVE_POSITION_POLICY"
                    planned["plan_durable_at_buy"]=bool(planned["entry_at"])
                row["v019_exit_plan"]=planned
                counts[owner]+=1
            else:
                skipped+=1
    packet["staged_exit_preview_v019"]={
        "schema":_TAG,"position_plan_counts":counts,
        "unverified_or_nonopen_rows":skipped,
        "native_wallet_written":False,
        "broker_order_submitted":False,"fill_created":False}
    return packet

_INSTALLED=False
def install(core):
    global _INSTALLED
    if _INSTALLED:return
    old=core.Runtime.hub_records
    def source_plan_hub(self,for_ui=False):
        raw=old(self,for_ui=for_ui)
        try:
            source=json.loads(raw) if isinstance(raw,str) else raw
            mapped=decorate(source,runtime=self)
            return json.dumps(mapped,ensure_ascii=False,default=str,
                              separators=(",",":"))
        except Exception as exc:
            # This is only a UI plan. Never block original real exit feeds,
            # mutate wallet/order status or hide genuine positions.
            try:self._err("staged_exit_preview_v019_readonly",exc)
            except Exception:pass
            return raw
    core.Runtime.hub_records=source_plan_hub
    _INSTALLED=True
