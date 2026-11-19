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

def preview(owner,row,runtime=None):
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
        return _make_plan(entry,qty,first,second,q1,q2,owner,
            "ORIGINAL_GMO_SCORE_TIER_4_5_7_FIRST20_SECOND40",
            order_state=order_state,score=int(round(tier*100)))

    qty=_original_qty(row,market)
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

    frame=str(row.get("tf") or row.get("frame") or "")
    if market=="coin" and strategy=="general":kind="reference"
    else:kind=strategy
    if kind not in ("reference","at","ma","trend"):return None
    if kind=="trend" and frame=="5m":
        # Preserve historical retired Trend-5m exit; never invent a new
        # NEW-LEGACY entry/plan from old 5m position without native intents.
        return None
    try:
        from new_legacy_1118_policy import FIRST_GAIN,SECOND_GAIN
        f=Decimal(str(FIRST_GAIN[frame]))
        s=Decimal(str(SECOND_GAIN[frame]))
    except (KeyError,ImportError,ValueError,TypeError):
        return None
    # Parent MA bear/recovery holds belong to a different original policy.
    kind_hint=str(row.get("ma_v067_kind") or row.get("kind") or "")
    if kind=="ma" and kind_hint in ("REVERSAL_BEAR","BEAR"):
        return None
    first=entry*(1+f)
    second=first*(1+s)
    q1,q2=_nl_quantities(qty,market)
    return _make_plan(entry,qty,first,second,q1,q2,owner,
            "NEW_LEGACY_1118_ORIGINAL_BUY_PRICE_30_35")

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
        for row in positions:
            if not isinstance(row,dict):continue
            planned=preview(owner,row,runtime=runtime)
            if planned:
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
