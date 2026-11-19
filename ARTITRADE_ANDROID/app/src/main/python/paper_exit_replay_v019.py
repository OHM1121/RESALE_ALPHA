"""ARTITRADE 0.0.19: original-owner chronological PAPER limit assumption.

Pure decision engine. It NEVER touches a wallet, places a broker order, mutates
an authentic native receipt or books income. Only a separately guarded
transaction coordinator may consume its fully attributed eligible candidates.

A trading candle HIGH is at most PAPER-limit assumption evidence. Replaying
the entirety of credible post-BUY source observations and original owner
protective decisions is mandatory. Missing evidence is an explicit rejection.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal, InvalidOperation

OWNERS=("stock/at","stock/ma","stock/gmo","stock/cw","stock/trend",
        "coin/general","coin/ma","coin/trend")
# An owner-specific native decision audit must demonstrate all original
# protections were evaluated in the interval. A mere candle HIGH or ticker
# observed by the display does not satisfy these gates.
GUARDS={
 "stock/at":("REFERENCE_STOP","SCORE_STOP","EXPIRY","TRAILING"),
 "stock/ma":("HARD_STOP","REV1_LOW_CLOSE","EXPIRY","TRAILING"),
 "stock/gmo":("GMO_PROTECTION","GMO_EXPIRY","GMO_TRAILING"),
 "stock/cw":("CW_STOP","CW_EXPIRY","CW_PROTECTION"),
 "stock/trend":("HARD_STOP","REV1_LOW_CLOSE","EXPIRY","TRAILING"),
 "coin/general":("REFERENCE_STOP","SCORE_STOP","EXPIRY","TRAILING"),
 "coin/ma":("HARD_STOP","REV1_LOW_CLOSE","EXPIRY","TRAILING"),
 "coin/trend":("HARD_STOP","REV1_LOW_CLOSE","EXPIRY","TRAILING"),
}
FULL_EXIT=frozenset(("NATIVE_FULL_EXIT","HARD_STOP","FORCED_FULL_EXIT",
                   "PROTECTIVE_FULL_EXIT","EXPIRY_FULL_EXIT",
                   "REV1_LOW_CLOSE_FULL_EXIT","TRAILING_FULL_EXIT"))
SOURCES=frozenset(("NATIVE_EXECUTION_RECEIPT","AUTHENTICATED_EXCHANGE_TICK",
                   "AUTHENTICATED_CLOSED_ONE_MINUTE","ORIGINAL_POLICY_EVALUATION"))
MAX_GAP_SECONDS=75


def positive(value):
    if isinstance(value,bool):return None
    try:
        d=Decimal(str(value))
        if d.is_finite() and d>0:return d
    except (InvalidOperation,TypeError,ValueError,OverflowError):
        pass
    return None


def timestamp(value):
    if not isinstance(value,str):return None
    try:
        t=dt.datetime.fromisoformat(value.replace("Z","+00:00"))
        return t.astimezone(dt.timezone.utc) if t.tzinfo else None
    except ValueError:return None


def _failure(reason,why="",events=0,prior=()):
    return {
      "schema":"PAPER_V019_CHRONOLOGICAL_REPLAY",
      "status":reason,"reason":why,"events_checked":events,
      "candidates":[],"chronology":list(prior),"wallet_mutation":False,
      "broker_order_created":False,"native_receipt_created":False,
    }


def replay(position,events):
    """Qualify *counterfactual* PAPER stage(s) from a complete source tape.

    position: from one immutable original owner + epoch + BUY & native stage
      receipts; stages must have original native ID, first/second limit, qty,
      filled_qty, active status, creation time. No symbol-based fill matching.
    events: complete, monotonically numbered authenticated same-venue tape,
      includes all original native exit receipts and complete owner-protection
      decisions, including stops and expiry; no gaps or future/leap events.
    """
    if not isinstance(position,dict) or not isinstance(events,(list,tuple)):
        return _failure("SOURCE_POSITION_UNVERIFIED")
    owner=position.get("owner")
    if owner not in OWNERS or position.get("mode")!="PAPER":
        return _failure("UNSUPPORTED_OR_REAL_POSITION")
    epoch=position.get("epoch")
    pid=position.get("position_id")
    entry=timestamp(position.get("buy_at"))
    original=positive(position.get("original_qty"))
    remaining=positive(position.get("remaining_qty"))
    buy_price=positive(position.get("buy_price"))
    venue=position.get("venue")
    if (not isinstance(epoch,str) or not epoch or not str(pid or "").strip()
        or not isinstance(venue,str) or not venue or entry is None or
        original is None or remaining is None or remaining>original or
        buy_price is None or position.get("status")!="OPEN"
        or not str(position.get("original_buy_receipt") or "").strip()):
        return _failure("SOURCE_BUY_PROVENANCE_UNVERIFIED")
    if position.get("broker_order_present") is not False:
        return _failure("BROKER_ORDER_PROVENANCE_UNVERIFIED")
    plans=position.get("stages")
    if not isinstance(plans,(list,tuple)) or not (1<=len(plans)<=2):
        return _failure("ORIGINAL_NATIVE_PLAN_UNVERIFIED")
    levels={}
    credited=Decimal(0)
    for plan in plans:
        if not isinstance(plan,dict):return _failure("ORIGINAL_NATIVE_PLAN_UNVERIFIED")
        stage=plan.get("stage")
        limit=positive(plan.get("limit"))
        qty=positive(plan.get("qty"))
        source_qty=plan.get("original_qty")
        filled=Decimal(str(plan.get("filled_qty",0)))
        if (stage not in (1,2) or stage in levels or limit is None or
            qty is None or filled<0 or filled>qty or
            positive(source_qty)!=original or limit<=buy_price or
            plan.get("policy_source")!="ORIGINAL_NATIVE_BUY_PLAN"
            or timestamp(plan.get("created_at"))!=entry or
            plan.get("broker_order_id") not in ("",None)):
            return _failure("ORIGINAL_NATIVE_PLAN_UNVERIFIED")
        levels[stage]=(limit,qty,filled)
        credited+=filled
    if 1 not in levels or (2 in levels and levels[2][0]<=levels[1][0]):
        return _failure("ORIGINAL_NATIVE_PLAN_UNVERIFIED")
    if sum((x[1] for x in levels.values()),Decimal(0))>original:
        return _failure("ORIGINAL_NATIVE_PLAN_UNVERIFIED")
    if credited>original-remaining:
        return _failure("ORIGINAL_SELL_LEDGER_MISMATCH")
    if position.get("original_full_exit_seen"):
        return _failure("ORIGINAL_FULL_EXIT_PROTECTED")
    # A partial original stage 1 cannot be repaired by a stage 2 hindsight
    # candidate before the original first stage is completely reconciled.
    if credited>0 and not position.get("original_stage_receipts_verified"):
        return _failure("ORIGINAL_PARTIAL_RECEIPTS_UNVERIFIED")
    if not events:return _failure("POST_BUY_TAPE_MISSING")
    parsed=[]
    for i,e in enumerate(events):
        if not isinstance(e,dict):
            return _failure("SOURCE_EVENT_UNVERIFIED",events=i)
        t=timestamp(e.get("ts"))
        if (t is None or e.get("source") not in SOURCES
            or e.get("owner")!=owner or e.get("epoch")!=epoch or
            str(e.get("position_id"))!=str(pid) or
            e.get("venue")!=venue or
            not str(e.get("original_feed_event_id") or "").strip()
            or not isinstance(e.get("sequence"),int)):
            return _failure("SOURCE_EVENT_UNVERIFIED",events=i)
        if t<=entry:return _failure("PRE_BUY_PRICE_NOT_ELIGIBLE",events=i)
        if t>dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=2):
            return _failure("FUTURE_PRICE_REJECTED",events=i)
        kind=e.get("kind")
        if kind not in ("TRADE","CLOSED_1M","SESSION_BREAK",
                       "NATIVE_STAGE_FILL") and kind not in FULL_EXIT:
            return _failure("UNKNOWN_EXIT_EVENT",events=i)
        if kind=="TRADE" and e["source"]!="AUTHENTICATED_EXCHANGE_TICK":
            return _failure("TICK_SOURCE_UNVERIFIED",events=i)
        if kind=="CLOSED_1M" and (e["source"]!="AUTHENTICATED_CLOSED_ONE_MINUTE"
                                  or e.get("final") is not True):
            return _failure("CLOSED_CANDLE_SOURCE_UNVERIFIED",events=i)
        if (kind=="NATIVE_STAGE_FILL" or kind=="NATIVE_FULL_EXIT") and (
                e["source"]!="NATIVE_EXECUTION_RECEIPT"):
            return _failure("NATIVE_RECEIPT_SOURCE_UNVERIFIED",events=i)
        if kind in FULL_EXIT and kind!="NATIVE_FULL_EXIT" and (
                e["source"] not in ("ORIGINAL_POLICY_EVALUATION",
                                    "NATIVE_EXECUTION_RECEIPT")):
            return _failure("STOP_POLICY_SOURCE_UNVERIFIED",events=i)
        if kind in ("TRADE","CLOSED_1M"):
            if not set(GUARDS[owner]).issubset(set(e.get("native_protections_checked") or ())):
                return _failure("ORIGINAL_PROTECTIVE_RULES_UNVERIFIED",events=i)
        parsed.append((t,e))
    # Source sequence must be stable independently of time sorting.
    # Do NOT just sort unordered events and invent missing chronology.
    seqs=[e["sequence"] for _,e in parsed]
    if seqs[0]!=1 or any(a+1!=b for a,b in zip(seqs,seqs[1:])):
        return _failure("TAPE_EVENT_SEQUENCE_GAP")
    if any(a[0]>b[0] for a,b in zip(parsed,parsed[1:])):
        return _failure("TAPE_TIMESTAMP_OUT_OF_ORDER")
    if (parsed[0][0]-entry).total_seconds()>MAX_GAP_SECONDS:
        return _failure("POST_BUY_TAPE_INCOMPLETE")
    for (older,first),(later,second) in zip(parsed,parsed[1:]):
        if (later-older).total_seconds()>MAX_GAP_SECONDS and (
            first.get("kind")!="SESSION_BREAK" or
            first.get("source")!="ORIGINAL_POLICY_EVALUATION"
            or first.get("session_closed_verified") is not True or
            second.get("session_reopened_verified") is not True):
            return _failure("TAPE_TIME_GAP")
    # Multiple event types at exactly the same timestamp cannot establish
    # whether profit or hard stop happened first.
    for i,(t,e) in enumerate(parsed):
        peers=[p[1]["kind"] for p in parsed if p[0]==t]
        if any(p in FULL_EXIT for p in peers) and any(
            p in ("TRADE","CLOSED_1M","NATIVE_STAGE_FILL") for p in peers):
            return _failure("EXIT_SEQUENCE_UNVERIFIED",events=i)
    working=remaining
    stages=[]
    history=[]
    stage_index=1 if levels[1][2]<levels[1][1] else 2
    if stage_index==2 and 2 not in levels:stage_index=3
    for i,(t,e) in enumerate(parsed):
        kind=e["kind"]
        if kind in FULL_EXIT:
            # Stop-first discards later profitable highs. A historical source
            # already fully exited must NEVER be reopened for virtual profit.
            return _failure("PRIOR_PROTECTIVE_EXIT_PREVENTS_CATCHUP",
                            why=kind,events=i+1,prior=history)
        if kind=="NATIVE_STAGE_FILL":
            native_stage=e.get("stage")
            if native_stage not in levels:return _failure("UNLINKED_NATIVE_STAGE_FILL")
            history.append({"kind":"NATIVE_STAGE_FILL","at":t.isoformat(),
                            "stage":native_stage})
            # The original SQLite already accounted for this; no rewrite.
            continue
        if kind=="SESSION_BREAK":continue
        if stage_index not in levels:continue
        limit,qty,filled=levels[stage_index]
        if kind=="TRADE":
            value=positive(e.get("price"))
            if value is None:return _failure("TICK_PRICE_UNVERIFIED",events=i+1)
            high=value
            low=value
        else:
            high=positive(e.get("high"))
            low=positive(e.get("low"))
            if high is None or low is None or high<low:
                return _failure("OFFICIAL_CANDLE_OHLC_INVALID",events=i+1)
        # Even a signed policy rule may trigger at another price within a
        # candle. Re-run the actual protective thresholds before interpreting
        # the candle's HIGH; the same bar cannot resolve TP vs stop.
        hit_stop=bool(e.get("native_stop_triggered",False))
        stop_price=positive(e.get("original_stop_limit"))
        if stop_price is not None and low<=stop_price:
            hit_stop=True
        if hit_stop:
            if high>=limit:
                return _failure("EXIT_SEQUENCE_UNVERIFIED",events=i+1,prior=history)
            return _failure("STOP_FIRST_NO_PROFIT_CATCHUP",
                            events=i+1,prior=history)
        if high==limit:
            history.append({"kind":"TP_TOUCH_ONLY","stage":stage_index,
                            "at":t.isoformat()})
            continue
        if high<limit:continue
        if e.get("limit_order_eligible") is not True:
            return _failure("ORIGINAL_ORDER_ELIGIBILITY_UNVERIFIED",events=i+1)
        desired=min(qty-filled,working)
        if desired<=0 or desired>original:
            return _failure("REMAINING_OR_STAGE_QUANTITY_UNVERIFIED",events=i+1)
        # No broker order is inferred. This is the user's approved PAPER-only
        # limit assumption and must retain a distinct evidence label.
        stages.append({"stage":stage_index,"qty":str(desired),
            "limit":str(limit),"at":t.isoformat(),
            "evidence_event_id":e["original_feed_event_id"],
            "evidence_kind":"PAPER_LIMIT_STRICT_OVERSHOOT_ASSUMPTION"})
        working-=desired
        history.append({"kind":"PAPER_LIMIT_ASSUMPTION_CANDIDATE",
                        "stage":stage_index,"at":t.isoformat(),
                        "qty":str(desired)})
        stage_index+=1
        if working<=0:break
    # Re-run all later events above; a confirmed native stop after even a
    # shadow TP forbids committing to a presently unsettled full-exit ledger.
    if not stages:
        return _failure("NO_VERIFIED_POST_BUY_STRICT_OVERSHOOT",
                        events=len(parsed),prior=history)
    raw=json.dumps({
        "owner":owner,"epoch":epoch,"position_id":str(pid),
        "buy_at":entry.isoformat(),"native_buy":str(position["original_buy_receipt"]),
        "stages":stages,
        "events":[{"sequence":e["sequence"],"ts":t.isoformat(),
                   "kind":e["kind"],"event_id":e["original_feed_event_id"]}
                  for t,e in parsed]},
        sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    return {"schema":"PAPER_V019_CHRONOLOGICAL_REPLAY",
        "status":"PAPER_ASSUMPTION_CANDIDATES_VERIFIED",
        "owner":owner,"epoch":epoch,"position_id":str(pid),
        "source_buy_receipt":str(position["original_buy_receipt"]),
        "original_qty":str(original),"source_remaining_qty":str(remaining),
        "venue":venue,"native_execution_created":False,
        "broker_order_created":False,"wallet_mutation":False,
        "chronology":history,"events_checked":len(parsed),
        "stages":stages,"digest_sha256":hashlib.sha256(raw).hexdigest()}
