"""November 18 workbook: executable PAPER-only price/exit decision policy.

Do not confuse target SELL INTENT with an actual broker order or fill.
Every executable BUY/SELL is still performed by the original wallet and
requires its own current genuine exchange quote and ledger transaction.
"""
from __future__ import annotations
import datetime as dt
import math
from statistics import fmean, pstdev

POLICY="NEW_LEGACY_1118"
FRAMES=("5m","30m","60m","day")
FIRST_GAIN={"5m":.02,"30m":.03,"60m":.035,"day":.04}
SECOND_GAIN={"5m":.02,"30m":.02,"60m":.025,"day":.03}
TRAIL={"5m":.025,"30m":.03,"60m":.03,"day":.04}
TREND_TRAIL={"30m":.04,"60m":.05,"day":.06}
MA_HOLD_HOURS={"5m":48,"30m":80,"60m":120,"day":360}
TREND_HOLD_HOURS={"30m":100,"60m":192,"day":360}
FIRST_SHARE=.30
SECOND_SHARE=.35
RSI_THRESHOLD=30.0
RSI_TRACK_HOURS=24
BOLL_PERIOD=20
BOLL_MULT=1.5
BOLL_ANGLE_MIN=-20.0
BOLL_ANGLE_MAX=30.0
TREND_FRAMES=("30m","60m","day")

def valid_num(x):
    try:
        y=float(x)
        return y if math.isfinite(y) else None
    except (TypeError,ValueError,OverflowError):
        return None

def validate_stock_score(score):
    """New reference-bar BUY requires current verified strictly POSITIVE score.

    A reference bar formed while score was negative can be traded later if
    the score is now positive; candidate-generation score does not gate it.
    Missing/stale score is NOT positive and never authorizes a new BUY.
    """
    value=valid_num(score)
    return value is not None and -100<=value<=100 and value>0

def at_hard_stop(ref_low,ref_high):
    lo,hi=valid_num(ref_low),valid_num(ref_high)
    if lo is None or hi is None or lo<=0 or hi<lo:
        raise ValueError("REFERENCE_BAR_HIGH_LOW_UNVERIFIED")
    return max(0.0,lo-(hi-lo))

def targets(entry,frame,kind="reference",ref_low=None,ref_high=None):
    base=valid_num(entry)
    if base is None or base<=0 or frame not in FRAMES:
        raise ValueError("UNVERIFIED_ENTRY_OR_FRAME")
    if kind not in ("reference","ma","trend"):
        raise ValueError("UNSUPPORTED_TARGET_POLICY")
    if kind=="trend" and frame not in TREND_FRAMES:
        raise ValueError("TREND_5M_RETIRED")
    t1=base*(1+FIRST_GAIN[frame])
    t2=t1*(1+SECOND_GAIN[frame])
    stop=(at_hard_stop(ref_low,ref_high) if kind=="reference"
          else base*.85)
    return {"policy":POLICY,"kind":kind,"frame":frame,"entry":base,
            "stop":stop,"first":t1,"second":t2,
            "first_fraction":FIRST_SHARE,"second_fraction":SECOND_SHARE,
            "third_fraction":1-FIRST_SHARE-SECOND_SHARE,
            "trail":(TREND_TRAIL[frame] if kind=="trend" else TRAIL[frame]),
            "sell_intents":[
                {"stage":1,"price":t1,"fraction_of_original":FIRST_SHARE,
                 "state":"ARMED","is_real_broker_order":False},
                {"stage":2,"price":t2,"fraction_of_original":SECOND_SHARE,
                 "state":"ARMED","is_real_broker_order":False}]}

def ma_up_alignment(prior_ma,current_ma):
    """MA120 strictly negative, short four beneath 120 and strictly rising.

    Prior alignment should not already qualify: first upward transition only.
    """
    def is_up(m):
        try:
            a=m[120]
            return (valid_num(a["delta"]) is not None and
                    float(a["delta"])<0 and
                    all(float(m[n]["delta"])>0 and
                        float(m[n]["value"])<float(a["value"])
                        for n in (5,10,20,60)))
        except (TypeError,KeyError,ValueError,OverflowError):
            return False
    return is_up(current_ma) and not is_up(prior_ma)

VISUAL_CHART_HEIGHT_PX=180.0
VISUAL_CANDLE_PITCH_PX=8.0
VISUAL_AXIS_PADDING=0.08


def projected_chart_angle(former,now,chart_view):
    """The pixel slope of the line drawn in this EXACT price-axis viewport.

    The renderer MUST use the same vertical price bounds and candle pitch.
    A third-party chart with a different zoom/scale need not have this angle.
    Missing or degenerate geometry NEVER permits an executable BUY.
    """
    try:
        lo=valid_num(chart_view["price_min"])
        hi=valid_num(chart_view["price_max"])
        height=valid_num(chart_view["plot_height_px"])
        pitch=valid_num(chart_view["candle_pitch_px"])
        if any(v is None for v in (lo,hi,height,pitch)):
            return None
        if not (hi>lo>0 and height>0 and pitch>0):
            return None
        dy_px=(now-former)*height/(hi-lo)
        angle=math.degrees(math.atan2(dy_px,pitch))
        return angle if math.isfinite(angle) else None
    except (ValueError,TypeError,KeyError,OverflowError):
        return None


def bollinger(closes,chart_view=None):
    """Bollinger(20,1.5), midpoint slope in matching PIXEL-CHART geometry.

    The previous atan(100*midpoint percentage change) was not the
    screenshot's apparent line angle and could approve steep falls.
    For reproducible unattended execution we use one fixed internal chart
    projection; the app must display these exact axis bounds if comparing
    visually. An external exchange chart can only match after zoom/axis sync.
    """
    if len(closes)<21:return None
    items=[valid_num(x) for x in closes[-21:]]
    if any(x is None or x<=0 for x in items):return None
    former=fmean(items[:-1][-BOLL_PERIOD:])
    now=fmean(items[-BOLL_PERIOD:])
    if former<=0 or now<=0:return None
    std=pstdev(items[-BOLL_PERIOD:])
    if chart_view is None:
        low,high=min(items),max(items)
        span=max(high-low,now*0.001)
        chart_view={"price_min":max(0.0000000001,low-span*VISUAL_AXIS_PADDING),
                    "price_max":high+span*VISUAL_AXIS_PADDING,
                    "plot_height_px":VISUAL_CHART_HEIGHT_PX,
                    "candle_pitch_px":VISUAL_CANDLE_PITCH_PX,
                    "lookback_bars":21}
    angle=projected_chart_angle(former,now,chart_view)
    if angle is None:return None
    return {"middle":now,"upper":now+BOLL_MULT*std,
            "lower":now-BOLL_MULT*std,"angle_deg":angle,
            "angle_basis":"PIXEL_PROJECTED_BOLLINGER_MID_21_BAR_VIEWPORT_V008",
            "chart_view":dict(chart_view),
            "angle_allowed":BOLL_ANGLE_MIN<angle<BOLL_ANGLE_MAX}

def actual_middle_touch(previous_trade,current_trade,middle):
    """Only two genuinely observed trade prices can establish a cross.

    Not a historical candle high-low and not a synthetic broker fill.
    """
    old,new,mid=[valid_num(x) for x in
                 (previous_trade,current_trade,middle)]
    return (old is not None and new is not None and mid is not None
            and old>0 and new>0 and mid>0 and
            min(old,new)<=mid<=max(old,new))

def stage_quantity(initial,market,stage):
    v=valid_num(initial)
    if v is None or v<=0 or market not in ("stock","coin") or stage not in (1,2):
        return 0.0
    if market=="stock":
        import math as _math
        first=max(1,min(int(v),_math.ceil(int(v)*FIRST_SHARE)))
        if stage==1:return float(first)
        return float(max(0,min(int(v)-first,
                             _math.ceil(int(v)*SECOND_SHARE))))
    return v*(FIRST_SHARE if stage==1 else SECOND_SHARE)

def holding_deadline(entry_at,kind,frame):
    if entry_at.tzinfo is None:raise ValueError("ENTRY_TIMESTAMP_MUST_HAVE_TZ")
    hours=(MA_HOLD_HOURS if kind=="ma" else
           TREND_HOLD_HOURS if kind=="trend" else None)
    if hours is None or frame not in hours:
        raise ValueError("UNKNOWN_HOLD_TIMEFRAME")
    # Stock SELL executes on first genuinely open exchange session after
    # this time, not on a manufactured weekend/overnight price tick.
    return entry_at+dt.timedelta(hours=hours[frame])

def exit_signal(state,observed_bid,now,market_score=None):
    """Return (reason, fraction_of_REMAINING) or None; requires real bid proof.

    State changes on observed prices, but stage advances only by separately
    confirmed original-wallet SELL fills. The broker quote is verified by
    the caller's unchanged native depth/freshness/venue guards.
    """
    p=valid_num(observed_bid)
    if p is None or p<=0 or not isinstance(now,dt.datetime) or now.tzinfo is None:
        return None
    t=state
    entry=valid_num(t.get("entry"))
    if entry is None or entry<=0:return None
    kind=t.get("kind")
    if kind=="reference":
        if p<=float(t.get("stop",0)):
            return "NL1118_REFERENCE_HARD_STOP",1.0
        expiry=t.get("deadline")
        if expiry:
            try:
                end=dt.datetime.fromisoformat(str(expiry))
                if end.tzinfo is not None and now>=end:
                    return "NL1118_REFERENCE_HOLD_EXPIRY",1.0
            except (ValueError,TypeError):pass
        score=valid_num(market_score)
        if (t.get("score_positive_at_buy") is True and
            score is not None and score<=-10.0):
            return "NL1118_MARKET_SCORE_MINUS_10",1.0
    elif kind in ("ma","trend"):
        if p<=entry*.85:
            return "NL1118_HARD_STOP_15PCT",1.0
        expires=t.get("deadline")
        if expires:
            try:
                deadline=dt.datetime.fromisoformat(str(expires))
                if deadline.tzinfo is not None and now>=deadline:
                    return "NL1118_HOLD_EXPIRY",1.0
            except (TypeError,ValueError):pass
    stage=int(t.get("stage",0))
    if stage<=0:
        if p>entry:t["seen_above_entry"]=True
        if t.get("seen_above_entry") and p<=entry:
            return "NL1118_PRE_FIRST_RETURN_TO_ENTRY",1.0
        if p>=float(t["first"]):
            left=max(0,stage_quantity(t["qty0"],t["market"],1)-
                     float(t.get("stage1_filled",0)))
            if left>0:return "NL1118_TP1",min(1.,left/max(1e-12,float(t["qty"])))
    elif stage==1:
        if p<=entry*1.01:
            return "NL1118_AFTER_FIRST_PLUS_1_STOP",1.0
        if p>=float(t["second"]):
            left=max(0,stage_quantity(t["qty0"],t["market"],2)-
                     float(t.get("stage2_filled",0)))
            if left>0:return "NL1118_TP2",min(1.,left/max(1e-12,float(t["qty"])))
    else:
        t["peak_after_second"]=max(float(t.get("peak_after_second") or entry),p)
        if p<=float(t["first"]):
            return "NL1118_AFTER_SECOND_FIRST_FLOOR",1.0
        if p<=t["peak_after_second"]*(1-float(t["trail"])):
            return "NL1118_AFTER_SECOND_TRAIL",1.0
    return None
