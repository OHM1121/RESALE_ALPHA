"""v061 MA signals and exit rules (pure, time-ordered, deterministic).

NO normal highest-volume/reference breakout path for MA. Two new-entry kinds:
  DOWN_BEAR: >=3 descending SMA slopes and a completed bearish candle;
  TURNPOINT: first completed bar with falling SMA120, four shorter SMAs below
             SMA120 and all four slopes positive, while previous bar did not
             qualify; pivot width is <=2 percent of its low.
Never use a forming candle as a finalized entry signal.
"""
from __future__ import annotations
from decimal import Decimal,InvalidOperation

FRAMES=('5m','30m','60m','day')
PERIODS=(5,10,20,60,120)
FIRST_TARGET_PCT={'5m':Decimal('1.5'),'30m':Decimal('2'),
                  '60m':Decimal('3'),'day':Decimal('5')}
PIVOT_RANGE_MAX_PCT=Decimal('2')
SECOND_EXTRA_PCT=Decimal('3')
THIRD_TRAIL_PCT=Decimal('4')
FIRST_SELL_FRACTION=Decimal('.20')
SECOND_REMAINING_FRACTION=Decimal('.50')
MAX_ENTRY_AFTER_OPEN_SEC=5

def dec(value):
    try:
        x=Decimal(str(value))
        return x if x.is_finite() else None
    except (ValueError,TypeError,InvalidOperation):
        return None

def ma_values(closes,ending):
    """SMA values and 1-bar gradients at ending index, no future lookahead."""
    if ending<120:return None
    result={}
    for n in PERIODS:
        cur=sum(closes[ending-n+1:ending+1],Decimal(0))/Decimal(n)
        prev=sum(closes[ending-n:ending],Decimal(0))/Decimal(n)
        result[n]={'value':cur,'prior':prev,'delta':cur-prev}
    return result

def turnpoint_condition(mas):
    if not mas:return False
    root=mas[120]
    return (root['delta']<0 and
        all(mas[n]['delta']>0 and mas[n]['value']<root['value']
            for n in PERIODS[:-1]))

def candidate_from_closed(bars,frame):
    """Return a single MA entry-kind, never a normal volume-record candidate."""
    if frame not in FRAMES or len(bars)<122:return {'kind':None,'reason':'SMA_122_BARS_REQUIRED'}
    closes=[dec(x.get('close')) for x in bars]
    if any(x is None or x<=0 for x in closes):return {'kind':None,'reason':'INVALID_CANDLE_CLOSE'}
    cur=bars[-1]
    opening,closing,high,low=(dec(cur.get(x)) for x in ('open','close','high','low'))
    if any(v is None for v in (opening,closing,high,low)) or not 0<low<=min(opening,closing)<=max(opening,closing)<=high:
        return {'kind':None,'reason':'INVALID_OHLC'}
    mas=ma_values(closes,len(closes)-1)
    previous=ma_values(closes,len(closes)-2)
    if previous is None:return {'kind':None,'reason':'SMA_122_BARS_REQUIRED'}
    down=sum(mas[n]['delta']<0 for n in PERIODS)
    pivot=turnpoint_condition(mas) and not turnpoint_condition(previous)
    width_pct=(high-low)/low*100
    attributes={
        'ma_values':{str(k):str(v['value']) for k,v in mas.items()},
        'ma_deltas':{str(k):str(v['delta']) for k,v in mas.items()},
        'ma_down':down,
        'ma_up':sum(mas[n]['delta']>0 for n in PERIODS),
        'candle':'BEAR' if closing<opening else 'BULL' if closing>opening else 'DOJI',
        'width_pct':str(width_pct),'pivot_transition_first':bool(pivot),
        'pivot_short_below120':all(mas[n]['value']<mas[120]['value'] for n in PERIODS[:-1])}
    if pivot:
        if width_pct>PIVOT_RANGE_MAX_PCT:
            return dict(attributes,kind=None,reason='TURNPOINT_RANGE_OVER_2PCT')
        return dict(attributes,kind='TURNPOINT',reason='MA120_DOWN_SHORT4_FIRST_UP_BELOW')
    if down>=3 and closing<opening:
        return dict(attributes,kind='DOWN_BEAR',reason='MA3PLUS_DOWN_BEAR')
    return dict(attributes,kind=None,reason='NO_MA_ENTRY_SIGNAL')

def active_slopes(prior_closed,forming_price):
    """Live forming-candle slopes for critical exits; prices before current bar."""
    price=dec(forming_price)
    history=[dec(x) for x in prior_closed]
    if price is None or price<=0 or len(history)<120 or any(v is None or v<=0 for v in history):
        return None
    closes=history+[price]
    return ma_values(closes,len(closes)-1)

def forced_exit_reason(prior_closed,forming_price):
    mas=active_slopes(prior_closed,forming_price)
    if not mas:return ''
    if all(mas[n]['delta']<0 for n in PERIODS):
        return 'MA_V061_FORCE_ALL5_DOWN'
    if (mas[120]['delta']>0 and all(
        mas[n]['value']>mas[120]['value'] and mas[n]['delta']<0
        for n in PERIODS[:-1])):
        return 'MA_V061_FORCE_120UP_TOP4_DOWN'
    return ''

def evaluate_pivot_exit(state,bid,entry,available_qty,initial_qty,forced=''):
    """Observe stages before deciding execution; updates can persist without fill.

    State: stage=0/1/2, first_anchor, first_sold, second_sold, peak2.
    The 1st target guard becomes active at the first PRICE TOUCH, even when
    the broker's limited depth did not yet complete the first partial sale.
    This avoids a false initial stop at entry before the 1st target touched.
    """
    state=dict(state)
    price,base,qty,initial=map(dec,(bid,entry,available_qty,initial_qty))
    if any(v is None for v in (price,base,qty,initial)) or base<=0 or initial<=0:
        return state,Decimal(0),''
    # An observed first-target touch must arm the entry-price guard even if
    # the bid depth was zero and no partial sale was executable at that quote.
    if forced:
        return state,qty,forced
    stage=int(state.get('stage') or 0)
    anchor=dec(state.get('first_anchor')) or Decimal(0)
    first_done=dec(state.get('first_sold')) or Decimal(0)
    second_done=dec(state.get('second_sold')) or Decimal(0)
    first_goal=initial*FIRST_SELL_FRACTION
    second_goal=(initial-first_goal)*SECOND_REMAINING_FRACTION
    threshold=base*(1+FIRST_TARGET_PCT[state['frame']]/100)
    if stage==0:
        if price<threshold:
            return state,Decimal(0),''
        stage=1
        anchor=price
        state.update(stage=1,first_anchor=str(price),
                     first_target_touched_at=state.get('last_observed_at') or '')
    if price<=base:
        return state,qty,'MA_V061_AFTER_FIRST_ENTRY_GUARD'
    if stage==1 and first_done<first_goal:
        return state,min(qty,first_goal-first_done),'MA_V061_FIRST_20PCT'
    if stage==1 and price>=anchor*(1+SECOND_EXTRA_PCT/100):
        stage=2
        state.update(stage=2,peak2=str(price))
    if stage==2:
        peak=max(dec(state.get('peak2')) or Decimal(0),price)
        state['peak2']=str(peak)
        if second_done<second_goal:
            return state,min(qty,second_goal-second_done),'MA_V061_SECOND_HALF_REMAINING'
        if peak>0 and price<=peak*(1-THIRD_TRAIL_PCT/100):
            return state,qty,'MA_V061_THIRD_PEAK_MINUS4'
    return state,Decimal(0),''

def quantity_after_fill(state,reason,sold):
    state=dict(state)
    size=dec(sold)
    if size is None or size<=0:return state
    if reason=='MA_V061_FIRST_20PCT':
        state['first_sold']=str((dec(state.get('first_sold')) or Decimal(0))+size)
    elif reason=='MA_V061_SECOND_HALF_REMAINING':
        state['second_sold']=str((dec(state.get('second_sold')) or Decimal(0))+size)
    return state
