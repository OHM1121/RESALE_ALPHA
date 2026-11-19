"""0.0.19 fail-closed PAPER chronology audit, NOT a fill or wallet correction.

Original owner/native immutable BUY, EXIT and stage records remain authoritative.
This pure-function audit never writes SQLite, submits orders or issues receipts.
It refuses unordered OHLC, missing-source or post-native-full-exit profits.
Only an independent verified native transaction processor can later settle.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

OWNERS=frozenset(("stock/at","stock/ma","stock/gmo","stock/cw",
                  "stock/trend","coin/general","coin/ma","coin/trend"))
FULL_EXIT=frozenset(("NATIVE_FULL_EXIT","FORCED_FULL_EXIT","HARD_STOP",
                     "EXPIRY_FULL_EXIT","TRAILING_FULL_EXIT",
                     "REV1_LOW_CLOSE_FULL_EXIT","PROTECTION_FULL_EXIT"))
_TP=("TP1","TP2")


def dec(value):
    if isinstance(value,bool):return None
    try:
        x=Decimal(str(value))
        return x if x.is_finite() else None
    except (InvalidOperation,TypeError,ValueError,OverflowError):
        return None


def when(value):
    if not isinstance(value,str):return None
    try:
        obj=dt.datetime.fromisoformat(value.replace("Z","+00:00"))
        return obj.astimezone(dt.timezone.utc) if obj.tzinfo else None
    except ValueError:return None


def _out(reason,sequence=(),quantity=None):
    return {"schema":"PAPER_EXIT_CHRONOLOGY_V019_AUDIT_ONLY",
            "status":reason,"chronology":list(sequence),
            "remaining_qty":float(quantity) if quantity is not None else None,
            "wallet_written":False,"native_execution_created":False,
            "broker_execution_created":False,
            "inferred_profit_booked":False}


def audit(position,events):
    """Reconcile ordered source observations; return evidence, never P/L.

    position fields: owner/epoch/position_id/entry_at/entry_qty/entry_price,
      market/venue/mode, stages list persisted at BUY with price/qty/status.
    events fields: ts/owner/position_id/venue/kind, price for tick, or
      OHLC hi/lo plus native triggered stop/force with proof. Native exits
      must be explicit original-identity-linked, not guessed by price alone.
    """
    if not isinstance(position,dict) or not isinstance(events,(list,tuple)):
        return _out("SOURCE_POSITION_UNVERIFIED")
    owner=position.get("owner")
    pid=position.get("position_id")
    entry=when(position.get("entry_at"))
    qty=dec(position.get("entry_qty"))
    cost=dec(position.get("entry_price"))
    epoch=position.get("epoch")
    if (owner not in OWNERS or not str(pid or "").strip()
        or not str(epoch or "").strip() or entry is None
        or qty is None or qty<=0 or cost is None or cost<=0
        or position.get("mode")!="PAPER"
        or not str(position.get("venue") or "").strip()):
        return _out("SOURCE_POSITION_UNVERIFIED")
    venue=position["venue"]
    rows=position.get("stages")
    if not isinstance(rows,list) or not rows or len(rows)>2:
        return _out("PERSISTED_TARGET_PLAN_UNVERIFIED",quantity=qty)
    stages={}
    for r in rows:
        if not isinstance(r,dict):return _out("PERSISTED_TARGET_PLAN_UNVERIFIED")
        n=r.get("stage")
        limit=dec(r.get("price"))
        amount=dec(r.get("qty"))
        if (n not in (1,2) or n in stages or limit is None
            or limit<=cost or amount is None or amount<=0
            or r.get("source")!="ORIGINAL_NATIVE_PREBUY_COMMITTED"):
            return _out("PERSISTED_TARGET_PLAN_UNVERIFIED")
        stages[n]=(limit,amount)
    if 1 not in stages or sum((v[1] for v in stages.values()),Decimal(0))>qty:
        return _out("PERSISTED_TARGET_PLAN_UNVERIFIED")
    if 2 in stages and stages[2][0]<=stages[1][0]:
        return _out("PERSISTED_TARGET_PLAN_UNVERIFIED")
    parsed=[]
    for e in events:
        if not isinstance(e,dict):
            return _out("SOURCE_EVENT_UNVERIFIED")
        stamp=when(e.get("ts"))
        if (stamp is None or e.get("owner")!=owner or
            str(e.get("position_id"))!=str(pid) or e.get("venue")!=venue
            or e.get("epoch")!=epoch or
            e.get("source") not in ("ORIGINAL_NATIVE_RECEIPT",
                                    "AUTHENTICATED_SAME_VENUE_TICK",
                                    "AUTHENTICATED_CLOSED_OHLC")):
            return _out("SOURCE_EVENT_UNVERIFIED")
        if stamp<=entry:
            # A pre-BUY record can never prove this position's exit.
            continue
        kind=e.get("kind")
        if kind not in FULL_EXIT and kind not in ("TRADE_TICK","OHLC","NATIVE_TP1","NATIVE_TP2"):
            return _out("SOURCE_EVENT_UNVERIFIED")
        if kind in FULL_EXIT or kind in ("NATIVE_TP1","NATIVE_TP2"):
            if e["source"]!="ORIGINAL_NATIVE_RECEIPT":
                return _out("NATIVE_EXIT_PROVENANCE_UNVERIFIED")
        if kind in ("TRADE_TICK","OHLC") and e["source"]=="ORIGINAL_NATIVE_RECEIPT":
            return _out("SOURCE_EVENT_UNVERIFIED")
        parsed.append((stamp,e))
    parsed.sort(key=lambda x:x[0])
    active=qty
    next_stage=1
    audit_rows=[]
    terminal=False
    for index,(stamp,e) in enumerate(parsed):
        if terminal:break
        kind=e["kind"]
        peers=[x[1] for x in parsed if x[0]==stamp]
        if len(peers)>1 and any(p["kind"] in FULL_EXIT for p in peers):
            # Same timestamp alone does not establish which decision fired.
            if any(p["kind"] in ("TRADE_TICK","OHLC","NATIVE_TP1","NATIVE_TP2") for p in peers):
                return _out("EXIT_SEQUENCE_UNVERIFIED",audit_rows,active)
        if kind in FULL_EXIT:
            audit_rows.append({"at":stamp.isoformat(),"kind":kind,
                               "state":"ORIGINAL_NATIVE_FULL_EXIT_PRECEDES_LATER_TP",
                               "qty_at_exit":float(active)})
            active=Decimal(0)
            terminal=True
            continue
        if kind in ("NATIVE_TP1","NATIVE_TP2"):
            stage=1 if kind=="NATIVE_TP1" else 2
            if stage!=next_stage or stage not in stages:
                return _out("NATIVE_STAGE_ORDER_UNVERIFIED",audit_rows,active)
            sold=dec(e.get("qty"))
            if sold is None or sold<=0 or sold>stages[stage][1] or sold>active:
                return _out("NATIVE_STAGE_QUANTITY_UNVERIFIED",audit_rows,active)
            active-=sold
            audit_rows.append({"at":stamp.isoformat(),"kind":kind,
                               "state":"ORIGINAL_NATIVE_RECEIPT_ONLY",
                               "qty":float(sold)})
            if sold==stages[stage][1]:
                next_stage+=1
            else:
                # Without original partial-fill dedupe/IDs, no extrapolation.
                return _out("NATIVE_PARTIAL_STAGE_RECONCILIATION_REQUIRED",
                            audit_rows,active)
            if active==0:terminal=True
            continue
        if next_stage not in stages:
            continue
        limit,planned=stages[next_stage]
        if kind=="OHLC":
            high=dec(e.get("high"));low=dec(e.get("low"))
            if high is None or low is None or high<low:
                return _out("SOURCE_EVENT_UNVERIFIED",audit_rows,active)
            if high>=limit:
                # OHLC cannot prove that a stop, expiry or trailing rule
                # did not happen earlier within the same candle.
                return _out("EXIT_SEQUENCE_UNVERIFIED",audit_rows,active)
            continue
        observed=dec(e.get("price"))
        if observed is None or observed<=0:
            return _out("SOURCE_EVENT_UNVERIFIED",audit_rows,active)
        if observed<limit:continue
        if observed==limit:
            audit_rows.append({"at":stamp.isoformat(),"kind":"TP"+str(next_stage),
                               "state":"LIMIT_TOUCH_NEEDS_BOOK_AND_QUEUE_PROOF"})
            continue
        if e.get("tradable") is not True:
            return _out("ORIGINAL_ORDER_ELIGIBILITY_UNVERIFIED",audit_rows,active)
        # A strictly higher authentic print is a SHADOW assumption candidate
        # only, not a completed fill: no historical book queue proof.
        audit_rows.append({"at":stamp.isoformat(),"kind":"TP"+str(next_stage),
                           "state":"SHADOW_PAPER_LIMIT_CANDIDATE_ONLY",
                           "planned_qty":float(min(planned,active)),
                           "verified_print_above":float(observed)})
        # Do not debit even the hypothetical quantity. Continue observing
        # later authentic native stops. A later full exit still terminates
        # this position; the shadow candidate never becomes a native fill.
        # TP2 cannot be declared achieved without actual native TP1 proof.
        continue
    return _out("CHRONOLOGY_AUDITED_NO_SYNTHETIC_FILL" if terminal
                or not any(x.get("state")=="SHADOW_PAPER_LIMIT_CANDIDATE_ONLY"
                           for x in audit_rows)
                else "SHADOW_ASSUMPTION_NOT_SETTLED",
                audit_rows,active)
