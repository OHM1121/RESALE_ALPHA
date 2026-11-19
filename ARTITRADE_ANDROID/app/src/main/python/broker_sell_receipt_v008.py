"""v0.0.8 explicit READ-ONLY Kiwoom unfilled sell-order inquiry.

This runs only after the user presses the receipt-query button. A local
NEW_LEGACY sell_intent is NOT a submitted / accepted / open brokerage order.
The adapter never calls an order, cancellation or bank endpoint.

Official source: https://openapi.kiwoom.com/m/guide/apiguide/08/ka10075
"""
from __future__ import annotations

import datetime as dt
import math

KST = dt.timezone(dt.timedelta(hours=9))
MAX_ROWS = 200


def _positive_int(v):
    try:
        s=str(v).replace(",","").strip().replace("+","")
        n=int(float(s))
        return n if n > 0 else 0
    except (ValueError,TypeError,OverflowError):
        return 0


def _stock_code(s):
    s=str(s or "").strip().upper()
    if s.startswith("A"):s=s[1:]
    return s if len(s)==6 and s.isdigit() else ""


def normalize_response(status_code,payload,verified_at,has_more=False):
    """No made-up broker ACK, fills or evidence of zero outstanding orders."""
    base={"ok":False,"source":"KIWOOM_KA10075_UNFILLED_SELL_READ_ONLY",
          "verified_at":verified_at,"orders":[],"has_more":bool(has_more),
          "account_orders_queried":True,"broker_orders_registered_by_app":None}
    if status_code>=400 or not isinstance(payload,dict):
        return dict(base,error="BROKER_HTTP_OR_JSON_FAILURE")
    if str(payload.get("return_code",""))!="0":
        return dict(base,error="BROKER_QUERY_REJECTED")
    rows=payload.get("oso")
    if not isinstance(rows,list):
        return dict(base,error="BROKER_ROWS_MISSING_OR_MALFORMED")
    output=[]
    for row in rows[:MAX_ROWS]:
        if not isinstance(row,dict):
            return dict(base,error="BROKER_ROW_MALFORMED")
        side=str(row.get("io_tp_nm") or "").replace("+","").replace("-","").strip()
        if "매도" not in side:continue
        code=_stock_code(row.get("stk_cd"))
        oid=str(row.get("ord_no") or "").strip()
        qty=_positive_int(row.get("oso_qty"))
        if not code or not oid or qty<=0:
            # This row is NOT proof of an active sell order. Do not infer it.
            continue
        output.append({"code":code,"broker_order_id":oid,
                       "state":"BROKER_UNFILLED_VERIFIED",
                       "remaining_qty":qty,
                       "order_qty":_positive_int(row.get("ord_qty")),
                       "limit_price":_positive_int(row.get("ord_pric")),
                       "broker_state":str(row.get("ord_stt") or "")[:30],
                       "venue":str(row.get("stex_tp_txt") or "")[:16],
                       "verified_at":verified_at})
    return dict(base,ok=True,orders=output,
                has_more=bool(has_more or len(rows)>MAX_ROWS),
                error="" if not (has_more or len(rows)>MAX_ROWS)
                       else "PAGINATION_INCOMPLETE_NO_NEGATIVE_ASSERTION")


def audit_stock_sells(runtime,clock=None):
    when=(clock or (lambda:dt.datetime.now(KST)))()
    seen=when.isoformat(timespec="seconds")
    if runtime is None:
        return {"ok":False,"error":"ENGINE_NOT_STARTED","verified_at":None,
                "orders":[],"source":"KIWOOM_KA10075"}
    client=getattr(runtime,"kiwoom",None)
    if client is None or not client.ready():
        return {"ok":False,"error":"KIWOOM_KEYS_UNAVAILABLE",
                "verified_at":None,"orders":[],"source":"KIWOOM_KA10075"}
    try:
        # Query only open SELL orders on the user's account, across both
        # supported venues. Live toggle stays OFF. Five calls/sec max in docs.
        r,d=client._post("ka10075","/api/dostk/acnt",
                         {"all_stk_tp":"0","trde_tp":"1",
                          "stk_cd":"","stex_tp":"0"},4)
        more=(str(getattr(r,"headers",{}).get("cont-yn","")).upper()=="Y")
        result=normalize_response(r.status_code,d,seen,has_more=more)
    except Exception as exc:
        result={"ok":False,"source":"KIWOOM_KA10075_UNFILLED_SELL_READ_ONLY",
                "verified_at":None,"orders":[],
                "error":"BROKER_QUERY_UNAVAILABLE_"+type(exc).__name__}
    # Keep app-side intents and broker acknowledgement strictly separate.
    result["app_live_enabled"]=bool(runtime.live_enabled("stock"))
    result["stages"]=[
      {"stage":1,"status":"NOT_VERIFIED_AS_BROKER_ORDER"},
      {"stage":2,"status":"NOT_VERIFIED_AS_BROKER_ORDER"}]
    return result
