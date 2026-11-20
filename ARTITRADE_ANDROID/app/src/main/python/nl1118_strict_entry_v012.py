"""Final NEW LEGACY entry guard; leave GMO and CLOSE strategies untouched.

The original completed-candle MA scanners served bearish and next-bar V061
entries. NEW LEGACY 11.18 only permits the separately verified close-minus-
10..2 second first upward MA120 turnpoint. These scanners are not allowed to
create any new PAPER or LIVE MA entry after this signed source version.
Existing 0B/0D live up-entry services and genuine-order FOK checks remain.
"""
_INSTALLED=False

def _retired_stock_ma_scan(bars,tf,now):
    return {"decision":"REJECT","reason":"NL1118_OLD_COMPLETED_MA_SCAN_RETIRED"},None

def _retired_coin_ma_scan(history,frame,market,tick,now):
    return {"status":"REJECT","reason":"NL1118_OLD_COMPLETED_MA_SCAN_RETIRED",
            "selected_ref_at":None,"metrics":{}},None

def install():
    global _INSTALLED
    if _INSTALLED:return
    import stock_ma_engine
    import coin_ma_engine
    # V067 upward-turnpoint entry workers register directly from fresh
    # exchange tick and order book. This mutation affects ONLY the separate
    # old completed-candle scanners for MA, not AT, GMO or CW.
    stock_ma_engine.ma_reference_decision=_retired_stock_ma_scan
    coin_ma_engine.ma_reference_decision=_retired_coin_ma_scan
    _INSTALLED=True
