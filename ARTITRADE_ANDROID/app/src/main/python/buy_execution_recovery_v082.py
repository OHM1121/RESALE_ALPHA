"""v082 evidence-only, broker-agnostic three-scenario BUY intent state machine.

The original MA, GMO and MarketTrend wallets remain the *only* authorities
for orders, fills and holdings. This module creates no execution and never
sends an HTTP request. Clients may submit its validated bounded plans to
their original wallet, then reconcile from matching real order/execution IDs.

Scenario 1: verified liquidity -> aggressive limit <= initial ask +2 true ticks
Scenario 2: confirmed partial/cancel -> remaining allocation re-evaluation
Scenario 3: no/stale book -> HOLD; resume on a NEW verified book.
"""
from __future__ import annotations

from decimal import Decimal,InvalidOperation,ROUND_FLOOR
from dataclasses import dataclass,field

from partial_entry_v094 import two_ticks_max

ALIVE=frozenset(("PREPARED","SUBMITTED","ACCEPTED","PARTIAL","CANCEL_REQUESTED",
                 "UNCERTAIN","WAIT_BOOK","WAIT_DEPTH","WAIT_PRICE"))
TERMINAL=frozenset(("FILLED","CANCELLED_CONFIRMED","NO_FILL_CONFIRMED",
                    "REJECTED_CONFIRMED"))
_MONEY=Decimal("0.00000001")


def _dec(value):
    try:
        n=Decimal(str(value))
        return n if n.is_finite() else None
    except (ValueError,TypeError,InvalidOperation):
        return None


@dataclass
class Intent:
    strategy:str
    market:str
    symbol:str
    signal_id:str
    anchor:str
    max_seed:str
    fee_rate:str="0"
    state:str="PREPARED"
    active_order_id:str=""
    active_qty:str="0"
    reserved_won:str="0"
    filled_qty:str="0"
    confirmed_spent:str="0"
    verified_fills:dict=field(default_factory=dict)
    saw_gap:bool=False
    sell_started:bool=False
    attempts:int=0
    last_book_ms:int=0
    last_reason:str=""
    ceiling:str=""

    def __post_init__(self):
        if self.market not in ("stock","coin") or self.strategy not in (
            "gmo","ma","trend"):raise ValueError("UNKNOWN_STRATEGY_OR_MARKET")
        anchor,budget,fee=map(_dec,(self.anchor,self.max_seed,self.fee_rate))
        if any(x is None for x in (anchor,budget,fee)) or (
            anchor<=0 or budget<=0 or not 0<=fee<Decimal(".10")):
            raise ValueError("INVALID_INITIAL_VERIFIED_BUDGET")
        ceiling=_dec(two_ticks_max(self.market,anchor,self.symbol))
        if ceiling is None or ceiling<anchor:raise ValueError("INVALID_TICK_CEILING")
        self.ceiling=str(ceiling)

    def remaining(self):
        seed,spent,reserved=map(_dec,(
            self.max_seed,self.confirmed_spent,self.reserved_won))
        return max(Decimal(0),seed-spent-reserved)

    def unknown(self,reason):
        self.state="UNCERTAIN"
        self.last_reason=str(reason)
        return self.status()

    def mark_selling(self):
        self.sell_started=True
        self.last_reason="SELL_ALREADY_STARTED"
        return self.status()

    def quote(self,book,now_ms):
        """Only verified same-symbol exchange orderbook; never paper-fill here."""
        if self.sell_started:
            self.last_reason="SELL_ALREADY_STARTED"
            return {"action":"BLOCK","reason":self.last_reason}
        if self.state=="UNCERTAIN":
            return {"action":"BLOCK","reason":"ORDER_STATUS_UNCERTAIN"}
        if self.active_order_id:
            return {"action":"MONITOR","order_id":self.active_order_id,
                    "reason":"OUTSTANDING_ORDER_NOT_TERMINAL"}
        if not isinstance(book,dict) or book.get("symbol")!=self.symbol:
            self.saw_gap=True;self.state="WAIT_BOOK"
            return {"action":"WAIT","reason":"VERIFIED_BOOK_MISSING"}
        try:
            timestamp=int(book["exchange_ms"])
            now_ms=int(now_ms)
        except (TypeError,ValueError,KeyError):
            timestamp=0;now_ms=0
        if not timestamp or not 0<=now_ms-timestamp<=3000:
            self.saw_gap=True;self.state="WAIT_BOOK"
            return {"action":"WAIT","reason":"STALE_OR_FUTURE_EXCHANGE_BOOK"}
        ask,bid=map(_dec,(book.get("ask"),book.get("bid")))
        if ask is None or bid is None or ask<=0 or bid<=0 or bid>ask:
            self.saw_gap=True;self.state="WAIT_BOOK"
            return {"action":"WAIT","reason":"INVALID_EXCHANGE_QUOTES"}
        cap=_dec(self.ceiling)
        if ask>cap:
            self.state="WAIT_PRICE";self.last_reason="ABOVE_FIRST_VERIFIED_PLUS2_TICKS"
            return {"action":"WAIT","reason":self.last_reason}
        depth=Decimal(0)
        levels=book.get("asks")
        if isinstance(levels,(list,tuple)) and levels:
            # Aggregate only VERIFIED levels priced at or below max ceiling.
            for row in levels:
                if not isinstance(row,(list,tuple)) or len(row)!=2:
                    return {"action":"WAIT","reason":"MALFORMED_VERIFIED_DEPTH"}
                price,qty=map(_dec,row)
                if price is None or qty is None or price<=0 or qty<0:
                    return {"action":"WAIT","reason":"MALFORMED_VERIFIED_DEPTH"}
                if price<=cap:depth+=qty
        else:
            q=_dec(book.get("ask_size"))
            if q is not None and q>0:depth=q
        if depth<=0:
            self.saw_gap=True;self.state="WAIT_DEPTH"
            return {"action":"WAIT","reason":"NO_SELLABLE_ASK_DEPTH"}
        cash=self.remaining()
        price_budget=cap*(1+_dec(self.fee_rate))
        quantity=(cash/price_budget).to_integral_value(rounding=ROUND_FLOOR)
        if self.market=="coin":
            quantity=((cash/price_budget)*Decimal("100000000")).to_integral_value(
                rounding=ROUND_FLOOR)/Decimal("100000000")
        size=min(quantity,depth)
        if self.market=="stock":size=size.to_integral_value(rounding=ROUND_FLOOR)
        if size<=0:
            self.state="WAIT_DEPTH"
            return {"action":"WAIT","reason":"INSUFFICIENT_UNRESERVED_SEED"}
        stage=3 if self.saw_gap else (2 if _dec(self.filled_qty)>0 or self.attempts>0 else 1)
        return {"action":"PLACE","scenario":stage,"limit":str(cap),
                "qty":str(size),"remaining_seed":str(cash),
                "verified_book_ms":timestamp,
                "reason":("RECOVERED_BOOK" if stage==3 else
                         "PARTIAL_OR_NOFILL_RETRY" if stage==2 else
                         "IMMEDIATE_AGGRESSIVE_LIMIT")}

    def submit(self,order_id,quantity,verified_book_ms):
        """Prepare one original-wallet order. No second order while active."""
        if self.active_order_id or self.state=="UNCERTAIN" or self.sell_started:
            raise RuntimeError("OUTSTANDING_OR_UNCERTAIN_BUY")
        size=_dec(quantity)
        if size is None or size<=0:raise ValueError("INVALID_ORDER_QUANTITY")
        budget=size*_dec(self.ceiling)*(1+_dec(self.fee_rate))
        if budget>self.remaining():
            raise ValueError("ORDER_EXCEEDS_UNRESERVED_SEED")
        if int(verified_book_ms)<self.last_book_ms:
            raise RuntimeError("REPLAYED_OLD_EXCHANGE_BOOK")
        if not str(order_id):raise ValueError("ORDER_ID_REQUIRED")
        self.active_order_id=str(order_id)
        self.active_qty=str(size)
        self.reserved_won=str(budget)
        self.last_book_ms=int(verified_book_ms)
        self.attempts+=1
        self.state="SUBMITTED"
        self.last_reason="AWAIT_BROKER_ACCEPTANCE"
        return self.status()

    def acknowledge(self,order_id):
        if str(order_id)!=self.active_order_id:
            raise RuntimeError("UNKNOWN_ACK_ORDER_ID")
        self.state="ACCEPTED"
        return self.status()

    def confirm_fill(self,order_id,execution_id,qty,price):
        """The CALLER must already have matching real/verified PAPER ledger."""
        if str(order_id)!=self.active_order_id:
            raise RuntimeError("FILL_ORDER_MISMATCH")
        if not str(execution_id):raise ValueError("EXECUTION_ID_REQUIRED")
        if str(execution_id) in self.verified_fills:
            return self.status()
        amount,px=map(_dec,(qty,price))
        if any(v is None for v in (amount,px)) or amount<=0 or px<=0:
            raise ValueError("INVALID_CONFIRMED_FILL")
        old=_dec(self.filled_qty)
        claimed=sum(_dec(x["qty"]) for x in self.verified_fills.values()
                    if x["order_id"]==str(order_id))
        if claimed+amount>_dec(self.active_qty):
            raise RuntimeError("EXECUTION_EXCEEDS_ORIGINAL_ORDER")
        if px>_dec(self.ceiling):
            raise RuntimeError("FILL_ABOVE_IMMUTABLE_CEILING")
        amount_paid=amount*px*(1+_dec(self.fee_rate))
        if _dec(self.confirmed_spent)+amount_paid>_dec(self.max_seed)+_MONEY:
            raise RuntimeError("EXECUTION_EXCEEDS_SYMBOL_SEED")
        self.verified_fills[str(execution_id)]={
            "order_id":str(order_id),"qty":str(amount),"price":str(px)}
        self.confirmed_spent=str(_dec(self.confirmed_spent)+amount_paid)
        self.filled_qty=str(old+amount)
        self.reserved_won=str(max(Decimal(0),
            _dec(self.reserved_won)-amount*_dec(self.ceiling)*(1+_dec(self.fee_rate))))
        self.state="PARTIAL"
        self.last_reason="MATCHED_EXECUTION_CONFIRMED"
        return self.status()

    def terminal(self,order_id,state,broker_confirmed=False):
        """No new request until broker terminal status is externally verified."""
        if str(order_id)!=self.active_order_id:
            raise RuntimeError("UNKNOWN_TERMINAL_ORDER_ID")
        if not broker_confirmed or state not in TERMINAL:
            return self.unknown("BROKER_TERMINAL_NOT_CONFIRMED")
        total=sum(_dec(x["qty"]) for x in self.verified_fills.values()
                  if x["order_id"]==str(order_id))
        if state=="NO_FILL_CONFIRMED" and total!=0:
            return self.unknown("CONTRADICTORY_NONZERO_FILL")
        self.active_order_id=""
        self.active_qty="0"
        self.reserved_won="0"
        self.state="FILLED" if self.remaining()<=0 else state
        self.last_reason="BROKER_TERMINAL_RECONCILED"
        return self.status()

    def status(self):
        return {key:getattr(self,key) for key in (
            "strategy","market","symbol","signal_id","ceiling","max_seed",
            "state","active_order_id","active_qty","filled_qty",
            "confirmed_spent","reserved_won","attempts","last_book_ms",
            "last_reason","sell_started")}
