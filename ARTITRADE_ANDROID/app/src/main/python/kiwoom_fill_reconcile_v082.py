"""v082 original Kiwoom FOK order-ID reconciliation hardening.

The bundled EXE's _find_fill returned (0,0) after 14 polls whenever
confirmed broker fills were fewer than requested, silently losing PARTIAL.
It also queried kt00009 with qry_tp='1' (filled-only), so a true
unfilled order could be indistinguishable from missing inquiry evidence.
This overlay never places, edits or cancels an order.
"""
from __future__ import annotations

from datetime import datetime
import time

_INSTALLED=False
POLL_SECONDS=1.05   # Official kt00009 TR limit: one request per second.
MAX_POLLS=6


def verified_find_fill(self,ord_no,code,side,qty):
    from android_live_bridge import LiveOrderAmbiguous,_num
    code=str(code).replace("A","")
    order=str(ord_no).lstrip("0")
    requested=int(qty)
    if not order or requested<=0 or side not in ("BUY","SELL"):
        raise LiveOrderAmbiguous("KIWOOM_INVALID_RECONCILIATION_ID")
    body={"ord_dt":datetime.now().strftime("%Y%m%d"),
          "stk_bond_tp":"1","mrkt_tp":"0",
          "sell_tp":"2" if side=="BUY" else "1",
          "qry_tp":"0", # Include BOTH filled and unfilled submissions.
          "stk_cd":code,"fr_ord_no":"","dmst_stex_tp":"%"}
    best_qty=0
    best_avg=0.0
    saw_order=False
    saw_response=False
    for attempt in range(MAX_POLLS):
        try:
            response,data=self._post("kt00009","/api/dostk/acnt",body,3)
            if response.status_code>=400 or str(data.get("return_code",0))!="0":
                raise RuntimeError("KIWOOM_ORDER_QUERY_FAILED")
            saw_response=True
            rows=data.get("acnt_ord_cntr_prst_array") or []
            if not isinstance(rows,list):
                raise ValueError("KIWOOM_ORDER_ROWS_MALFORMED")
            matched=[r for r in rows if isinstance(r,dict) and
                     str(r.get("ord_no") or "").lstrip("0")==order]
            if matched:
                saw_order=True
                total=sum(max(0,int(_num(x.get("cntr_qty")))) for x in matched)
                if total>requested:
                    raise LiveOrderAmbiguous("KIWOOM_ORDER_FILL_EXCEEDS_REQUEST")
                amount=sum(max(0,int(_num(x.get("cntr_qty"))))*
                           max(0,int(_num(x.get("cntr_uv")))) for x in matched)
                if total>best_qty:
                    best_qty=total
                    best_avg=amount/total if total else 0.0
                if total==requested:
                    return best_qty,best_avg
        except LiveOrderAmbiguous:
            raise
        except (OSError,TimeoutError,ValueError,KeyError,TypeError,RuntimeError):
            # An unavailable response is not evidence of zero actual fills.
            pass
        if attempt+1<MAX_POLLS:
            time.sleep(POLL_SECONDS)
    if not saw_response or not saw_order:
        raise LiveOrderAmbiguous("KIWOOM_ORDER_STATUS_NOT_VERIFIED")
    # Even 1 share must remain in the existing order-ID journal as PARTIAL.
    # Do not advance the original position or invent a later order.
    return best_qty,best_avg


def install():
    global _INSTALLED
    if _INSTALLED:return
    from android_live_bridge import KiwoomBroker
    KiwoomBroker._find_fill=verified_find_fill
    _INSTALLED=True
