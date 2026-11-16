"""v065 user-provided MA contract. Pure decision functions; no broker IO.

REVERSAL_BEAR: sustained 120-down / all 4-below-and-down alignment,
then ANY confirmed large bearish OPEN-to-CLOSE body >=3/4/5/7% while
alignment continues, never just the first following candle.
Buy at the reversal candle low or below, but never after the price has
fallen below its low and rebounded back to the low.

REVERSAL_UP: first 120-down / all 4-up transition on FORMING candle;
requires it to still hold at close minus 5 seconds. No 4-below filter.
Any entry fill delayed over 15 seconds is prohibited.

All new positions: forced 120-up / all 4-above-and-down instant exit,
and during the SECOND bar AFTER the buy bar (d+1) the forming close below
entry causes an instant full exit.

First bear case has high-based 30% / high+2% next 50% / -3% peak exit.
Case 2 uses the user's subsequent explicit first/second targets and
confirmed-fill-only 10% / 50%-of-remainder exit stages.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from typing import Mapping
import ma_v061_rules as legacy

FRAMES=('5m','30m','60m','day')
SHORT=(5,10,20,60)
BEAR_BODY_PCT={'5m':Decimal('3'),'30m':Decimal('4'),
               '60m':Decimal('5'),'day':Decimal('7')}
FIRST_FRACTION=Decimal('.30')
SECOND_FRACTION_OF_REMAINING=Decimal('.50')
SECOND_ADDITIONAL=Decimal('.02')
PEAK_TRAIL=Decimal('.03')
HARD_AFTER_FIRST=Decimal('.01')
GRACE=timedelta(minutes=5)
UP_SUBMIT_BEFORE_CLOSE_SECONDS=5
MAX_UP_FILL_DELAY_SECONDS=15


def number(x):
    try:
        v=Decimal(str(x))
        return v if v.is_finite() else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def slopes(prices):
    if len(prices)<121:return None
    values=[number(p) for p in prices]
    if any(v is None or v<=0 for v in values):return None
    return legacy.ma_values(values,len(values)-1)


def bear_setup(closed_bars):
    """True whenever one verified completed bar has full bear alignment."""
    if len(closed_bars)<121:return False
    m=slopes([b['close'] for b in closed_bars])
    if m is None:return False
    return (m[120]['delta']<0 and all(
        m[n]['value']<m[120]['value'] and m[n]['delta']<0
        for n in SHORT))


def bear_reversal_completed(bars,frame):
    """Identify any large bear while established alignment remains satisfied."""
    if frame not in FRAMES or len(bars)<122:
        return {'kind':None,'reason':'REQUIRES_122_VERIFIED_BARS'}
    # v067 correction: the 120-down / four-below-down alignment need not
    # have formed on the immediately preceding bar. It must ALREADY have
    # existed and STILL hold on the large bearish signal candle.
    if not bear_setup(bars[:-1]):
        return {'kind':None,'reason':'BEAR_ALIGNMENT_NOT_ESTABLISHED'}
    if not bear_setup(bars):
        return {'kind':None,'reason':'BEAR_ALIGNMENT_NOT_MAINTAINED'}
    x=bars[-1]
    op,close,hi,lo=(number(x.get(k)) for k in ('open','close','high','low'))
    if any(v is None for v in (op,close,hi,lo)) or not (0<lo<=min(op,close)<=max(op,close)<=hi):
        return {'kind':None,'reason':'INVALID_CONFIRMED_OHLC'}
    if close>=op:
        return {'kind':None,'reason':'NOT_BEARISH'}
    body=(op-close)/op*100
    if body<BEAR_BODY_PCT[frame]:
        return {'kind':None,'reason':'BEAR_BODY_BELOW_FRAME_MIN','body_pct':str(body)}
    return {'kind':'REVERSAL_BEAR','reason':'SUSTAINED_ALIGNMENT_LARGE_BEAR',
            'body_pct':str(body),'reversal_low':str(lo),
            'reversal_high':str(hi),'frame':frame}


def upward_transition_on_forming(prior_closed,price,seconds_before_close):
    """No forward-lookahead; only at final 5s and only the first flip."""
    sec=number(seconds_before_close)
    if sec is None or not Decimal('4')<=sec<=Decimal('5'):
        return {'kind':None,'reason':'NOT_CLOSE_MINUS_FIVE_SECONDS'}
    if len(prior_closed)<121:
        return {'kind':None,'reason':'SMA_HISTORY_REQUIRED'}
    prev=slopes(prior_closed)
    cur=slopes(list(prior_closed)+[price])
    if prev is None or cur is None:
        return {'kind':None,'reason':'INVALID_SMA_DATA'}
    qualifies=lambda m:m[120]['delta']<0 and all(m[k]['delta']>0 for k in SHORT)
    if not qualifies(cur):
        return {'kind':None,'reason':'NO_SIMULTANEOUS_SHORT4_UP_120_DOWN'}
    if qualifies(prev):
        return {'kind':None,'reason':'UPWARD_ALIGNMENT_ALREADY_EXISTED'}
    return {'kind':'REVERSAL_UP','reason':'FIRST_FORMING_SHORT4_UP_AT_MINUS_5',
            'ma_120_delta':str(cur[120]['delta'])}


def within_up_fill_deadline(order_submitted_at,filled_at):
    if (not isinstance(order_submitted_at,datetime)
        or not isinstance(filled_at,datetime)
        or order_submitted_at.tzinfo is None or filled_at.tzinfo is None):
        return False
    age=(filled_at-order_submitted_at).total_seconds()
    return 0<=age<=MAX_UP_FILL_DELAY_SECONDS


def bear_low_entry(state,price,reversal_low):
    """Return (state, allowable); once rebound to low occurred, lock forever.

    The same real timestamp/order book sequence must be passed in order.
    No inferred missing prints and no retroactive fill.
    """
    state=dict(state or {})
    p,low=number(price),number(reversal_low)
    if p is None or low is None or p<=0 or low<=0:
        return state,False
    if state.get('rebounded_to_low'):
        return state,False
    if p<low:
        state['seen_below_low']=True
        return state,True
    if p==low:
        if state.get('seen_below_low'):
            state['rebounded_to_low']=True
            return state,False
        return state,True
    if state.get('seen_below_low'):
        state['rebounded_to_low']=True
    return state,False


def forced_ma_exit(prior_closes,forming_price):
    m=slopes(list(prior_closes)+[forming_price])
    if not m:return ''
    if m[120]['delta']>0 and all(
        m[n]['value']>m[120]['value'] and m[n]['delta']<0 for n in SHORT):
        return 'MA_V065_FORCE_120_UP_FOUR_ABOVE_TURN_DOWN'
    return ''


def forced_second_followup_bar(entry_bar_sequence,current_bar_sequence,
                               forming_close,entry):
    """Caller provides VERIFIED sequential KRX/Upbit bar ordinals.

    Not wall-clock gaps: weekends/holidays must not count as stock bars.
    The entry bar is 0, d is 1, d+1 is 2. Trigger only inside d+1.
    """
    p,b=number(forming_close),number(entry)
    return (isinstance(entry_bar_sequence,int)
            and isinstance(current_bar_sequence,int)
            and p is not None and b is not None and p>0 and b>0
            and current_bar_sequence==entry_bar_sequence+2 and p<b)


def initial_bear_exit_state(frame,ref_high):
    if frame not in FRAMES:raise ValueError('unsupported frame')
    high=number(ref_high)
    if high is None or high<=0:raise ValueError('invalid reversal high')
    return {'policy':'MA_V065_REVERSAL_BEAR','frame':frame,
            'ref_high':str(high),'first_sold':'0','second_sold':'0',
            'first_completed_at':'','peak_after_second':'0',
            'seen_above_entry':False}


def _full(qty,reason,state):
    return state,qty,reason


def bear_exit(state,bid,entry,available_qty,initial_qty,now,forced=''):
    """Return immutable decision (updated state, desired quantity, reason).

    State only accounts for CONFIRMED sold sizes via apply_bear_fill().
    All observed protection triggers take precedence over take-profit.
    """
    state=dict(state)
    price,base,avail,initial,high=map(number,
        (bid,entry,available_qty,initial_qty,state.get('ref_high')))
    if (any(v is None for v in (price,base,avail,initial,high))
        or base<=0 or initial<=0 or high<=0 or price<=0 or avail<0):
        return state,Decimal(0),''
    if forced:return _full(avail,forced,state)
    if not isinstance(now,datetime) or now.tzinfo is None:
        return state,Decimal(0),''
    first_goal=initial*FIRST_FRACTION
    first_sold=number(state.get('first_sold')) or Decimal(0)
    second_goal=(initial-first_goal)*SECOND_FRACTION_OF_REMAINING
    second_sold=number(state.get('second_sold')) or Decimal(0)
    if price>base:state['seen_above_entry']=True
    if first_sold<first_goal:
        if price>=high:
            return state,min(avail,first_goal-first_sold),'MA_V065_FIRST_30_AT_REVERSAL_HIGH'
        if state.get('seen_above_entry') and price<=base:
            return _full(avail,'MA_V065_PRE_FIRST_ENTRY_RETRACE',state)
        return state,Decimal(0),''
    # Confirmation of first full sale arms the unconditional +1% guard.
    if price<=base*(1+HARD_AFTER_FIRST):
        return _full(avail,'MA_V065_HARD_ENTRY_PLUS_1_AFTER_FIRST',state)
    if second_sold<second_goal:
        target2=high*(1+SECOND_ADDITIONAL)
        if price>=target2:
            return state,min(avail,second_goal-second_sold),'MA_V065_SECOND_HALF_REMAINING'
        t=state.get('first_completed_at')
        try:started=datetime.fromisoformat(t)
        except (TypeError,ValueError):started=None
        if started is not None and started.tzinfo is not None and now-started>GRACE and price<=high:
            return _full(avail,'MA_V065_FIRST_HIGH_RETURN_AFTER_FIVE_MIN',state)
        return state,Decimal(0),''
    peak=max(number(state.get('peak_after_second')) or Decimal(0),price)
    state['peak_after_second']=str(peak)
    target2=high*(1+SECOND_ADDITIONAL)
    middle=(high+target2)/2
    if price<=middle:
        return _full(avail,'MA_V065_HIGH_TARGET_MIDPOINT_STOP',state)
    if price<=peak*(1-PEAK_TRAIL):
        return _full(avail,'MA_V065_PEAK_MINUS_3',state)
    return state,Decimal(0),''


def apply_bear_fill(state,reason,quantity,at):
    """Advance only after confirmed actual/PAPER execution, never price touch."""
    state=dict(state)
    q=number(quantity)
    if q is None or q<=0 or not isinstance(at,datetime) or at.tzinfo is None:
        return state
    if reason=='MA_V065_FIRST_30_AT_REVERSAL_HIGH':
        state['first_sold']=str((number(state.get('first_sold')) or Decimal(0))+q)
        # The caller sets first_completed_at when initial 30% is ACTUALLY sold.
    elif reason=='MA_V065_SECOND_HALF_REMAINING':
        state['second_sold']=str((number(state.get('second_sold')) or Decimal(0))+q)
        state['peak_after_second']=str(max(
            number(state.get('peak_after_second')) or Decimal(0),
            number(state.get('last_execution_price')) or Decimal(0)))
    return state


def mark_stage_completion(state,initial_qty,at,execution_price=None):
    """Called after a confirmed fill to start grace, and to arm peak tracker."""
    state=dict(state)
    n=number(initial_qty)
    if n is None or n<=0:return state
    if number(state.get('first_sold'))>=n*FIRST_FRACTION and not state.get('first_completed_at'):
        state['first_completed_at']=at.isoformat()
    if number(state.get('second_sold')) >= n*(1-FIRST_FRACTION)*SECOND_FRACTION_OF_REMAINING:
        p=number(execution_price)
        if p is not None and p>0:
            state['peak_after_second']=str(max(number(state.get('peak_after_second')) or Decimal(0),p))
    return state


# Case 2, the upward-forming reversal (REVERSAL_UP). These targets apply to
# this case only; case 1 (REVERSAL_BEAR) remains entirely separate.
UP_FIRST_TARGET_PCT={
    '5m':Decimal('2'), '30m':Decimal('2.5'),
    '60m':Decimal('3'), 'day':Decimal('5'),
}
UP_SECOND_EXTRA_PCT={
    '5m':Decimal('2'), '30m':Decimal('2.5'),
    '60m':Decimal('3'), 'day':Decimal('3'),
}
UP_FIRST_FRACTION=Decimal('.10')
UP_SECOND_FRACTION_OF_REMAINDER=Decimal('.50')
UP_TRAIL_FROM_SECOND=Decimal('.03')
UP_FIRST_TO_SECOND_HARD_STOP=Decimal('.01')


def up_targets(entry,frame):
    """Targets derived from entry and the FIRST target (not an actual fill).

    The real confirmed first/second sale prices must never be used as a
    substitute for these configured target prices.
    """
    base=number(entry)
    if frame not in FRAMES:raise ValueError('unsupported timeframe')
    if base is None or base<=0:raise ValueError('invalid entry')
    first=base*(1+UP_FIRST_TARGET_PCT[frame]/100)
    return first,first*(1+UP_SECOND_EXTRA_PCT[frame]/100)


def initial_up_exit_state(frame):
    """No sale, no target touch, no synthetic execution at position opening."""
    if frame not in FRAMES:raise ValueError('unsupported timeframe')
    return {'policy':'MA_V065_REVERSAL_UP','frame':frame,
            'first_sold':'0','second_sold':'0',
            'first_completed_at':'','second_completed_at':'',
            'peak_after_second':'0','seen_above_entry':False}


def up_stage_quantities(state,initial):
    """Use exact fractions for fractional crypto, verified integer lots for stock.

    Broker/stock shares cannot fill 1.3 units. If confirmed 2-share first
    sale represents the >=10% target of a 13-share position, the state must
    advance by those SAME confirmed 2 shares, not silently reject the fill.
    """
    n=number(initial)
    if n is None or n<=0:return Decimal(0),Decimal(0)
    first=n*UP_FIRST_FRACTION
    first_integer=number(state.get('rounded_first_goal'))
    if (first_integer is not None and first_integer>0 and
        first_integer<=n and first_integer==first_integer.to_integral_value()):
        first=first_integer
    second=(n-first)*UP_SECOND_FRACTION_OF_REMAINDER
    second_integer=number(state.get('rounded_second_goal'))
    if (second_integer is not None and second_integer>=0 and
        second_integer<=n-first and
        second_integer==second_integer.to_integral_value()):
        second=second_integer
    return first,second


def up_exit(state,bid,entry,available_qty,initial_qty,now,forced=''):
    """One CURRENT book decision for a REVERSAL_UP position.

    Returns (persistable state, wanted sell qty, precise reason). Stages are
    determined by confirmed executed quantities, not touching a price.
    Common MA forced-exit rules are passed by caller as 'forced' and precede
    all profit targets. A missing/faulty quote may not trigger a fictitious
    sell. No brokerage order is sent by this pure function.
    """
    state=dict(state)
    if state.get('policy')!='MA_V065_REVERSAL_UP':
        return state,Decimal(0),''
    frame=state.get('frame')
    if frame not in FRAMES:
        return state,Decimal(0),''
    price,base,avail,initial=map(number,
        (bid,entry,available_qty,initial_qty))
    if (any(x is None for x in (price,base,avail,initial))
        or price<=0 or base<=0 or avail<0 or initial<=0
        or not isinstance(now,datetime) or now.tzinfo is None):
        return state,Decimal(0),''
    if forced:
        return _full(avail,forced,state)
    first_target,second_target=up_targets(base,frame)
    first_goal,second_goal=up_stage_quantities(state,initial)
    first_sold=number(state.get('first_sold')) or Decimal(0)
    second_sold=number(state.get('second_sold')) or Decimal(0)

    if first_sold<first_goal:
        # Only an observed climb followed by a RETURN to entry can trigger
        # the user's pre-first retrace rule. Do not invent a prior price path.
        if price>base:
            state['seen_above_entry']=True
        if state.get('seen_above_entry') and price<=base:
            return _full(avail,'MA_V065_UP_PRE_FIRST_ENTRY_RETURN',state)
        if price>=first_target:
            return state,min(avail,first_goal-first_sold),'MA_V065_UP_FIRST_10PCT'
        return state,Decimal(0),''

    if second_sold<second_goal:
        # A completed FIRST sale, with second NOT yet fully executed:
        # at <=entry+1% sell the entire currently held remainder.
        if price<=base*(1+UP_FIRST_TO_SECOND_HARD_STOP):
            return _full(avail,'MA_V065_UP_AFTER_FIRST_ENTRY_PLUS_1',state)
        if price>=second_target:
            return state,min(avail,second_goal-second_sold),'MA_V065_UP_SECOND_HALF_REMAINING'
        return state,Decimal(0),''

    # The SECOND stage must have actually filled 50% of the remaining
    # post-first position. First-target protection outranks peak trailing.
    if price<=first_target:
        return _full(avail,'MA_V065_UP_AFTER_SECOND_FIRST_TARGET_FLOOR',state)
    peak=max(number(state.get('peak_after_second')) or Decimal(0),price)
    state['peak_after_second']=str(peak)
    if price<=peak*(1-UP_TRAIL_FROM_SECOND):
        return _full(avail,'MA_V065_UP_PEAK_MINUS_3',state)
    return state,Decimal(0),''


def apply_up_fill(state,reason,quantity,filled_at,initial_qty,
                  execution_price):
    """Record confirmed fill only. Partial fills do NOT advance a stage.

    Caller persists this state in the same durable transaction as the
    accepted PAPER or real broker fill, then calls up_exit on subsequent
    fresh order books. Never call on rejected/cancelled/ambiguous orders.
    """
    state=dict(state)
    if state.get('policy')!='MA_V065_REVERSAL_UP':return state
    q,n,price=map(number,(quantity,initial_qty,execution_price))
    if (any(x is None for x in (q,n,price))
        or q<=0 or n<=0 or price<=0
        or not isinstance(filled_at,datetime) or filled_at.tzinfo is None):
        return state
    first_goal,second_goal=up_stage_quantities(state,n)
    if reason=='MA_V065_UP_FIRST_10PCT':
        was=number(state.get('first_sold')) or Decimal(0)
        # Wrong/duplicate sizes should not silently advance to stage 2.
        if was>=first_goal or q>first_goal-was:return state
        total=was+q
        state['first_sold']=str(total)
        if total>=first_goal and not state.get('first_completed_at'):
            state['first_completed_at']=filled_at.isoformat()
    elif reason=='MA_V065_UP_SECOND_HALF_REMAINING':
        if (number(state.get('first_sold')) or Decimal(0))<first_goal:
            return state
        was=number(state.get('second_sold')) or Decimal(0)
        if was>=second_goal or q>second_goal-was:return state
        total=was+q
        state['second_sold']=str(total)
        if total>=second_goal and not state.get('second_completed_at'):
            state['second_completed_at']=filled_at.isoformat()
            # Start following highest bid only AFTER the second fill.
            state['peak_after_second']=str(price)
    return state
