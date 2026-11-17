"""v094 +2 *exchange* ticks, strictly for already partially bought symbols.

NOT an entry generator, capital expander, quote substitute or broker override.
Caller must still confirm fresh original 0D/Upbit book, genuine available
volume, an actual persisted first BUY execution and its per-symbol principal.
The reference is the first verified fill, never rebased on a later average.

KRX prices: 2023+ unified stock tick ladder. Upbit KRW: July 31 2025+
ladder. USDT/USDC exceptional KRW pairs need an instrument's official
tick_size. Source: regulation.krx.co.kr and docs.upbit.com/kr/kr/docs/krw-market-info
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

STOCK_TIERS = ((Decimal('500000'),Decimal('1000')),
               (Decimal('200000'),Decimal('500')),
               (Decimal('50000'),Decimal('100')),
               (Decimal('20000'),Decimal('50')),
               (Decimal('5000'),Decimal('10')),
               (Decimal('2000'),Decimal('5')),
               (Decimal('0'),Decimal('1')))
COIN_KRW_TIERS = ((Decimal('1000000'),Decimal('1000')),
                  (Decimal('500000'),Decimal('500')),
                  (Decimal('100000'),Decimal('100')),
                  (Decimal('50000'),Decimal('50')),
                  (Decimal('10000'),Decimal('10')),
                  (Decimal('5000'),Decimal('5')),
                  (Decimal('1000'),Decimal('1')),
                  (Decimal('100'),Decimal('1')),
                  (Decimal('10'),Decimal('0.1')),
                  (Decimal('1'),Decimal('0.01')),
                  (Decimal('0.1'),Decimal('0.001')),
                  (Decimal('0.01'),Decimal('0.0001')),
                  (Decimal('0.001'),Decimal('0.00001')),
                  (Decimal('0.0001'),Decimal('0.000001')),
                  (Decimal('0.00001'),Decimal('0.0000001')),
                  (Decimal('0'),Decimal('0.00000001')))


def decimal(value):
    try:
        x=Decimal(str(value))
        return x if x.is_finite() and x>0 else None
    except (ValueError,TypeError,InvalidOperation):
        return None


def tick_size(market,price,symbol=None,instrument_tick=None):
    price=decimal(price)
    if price is None:return None
    if market=='stock':
        if price!=price.to_integral_value():return None
        table=STOCK_TIERS
    elif market=='coin':
        if symbol in ('KRW-USDT','KRW-USDC') and instrument_tick is None:
            return None
        if instrument_tick is not None:
            return decimal(instrument_tick)
        table=COIN_KRW_TIERS
    else:return None
    return next((step for cutoff,step in table if price>=cutoff),None)


def two_ticks_max(market,first_fill_price,symbol=None,instrument_tick=None):
    first=decimal(first_fill_price)
    if first is None:return None
    price=first
    for _ in range(2):
        step=tick_size(market,price,symbol,instrument_tick)
        if step is None:return None
        price+=step
    return price


def qualified_topup(market,first_fill_price,ask,spent,
                    max_per_symbol_seed,already_sold,has_verified_buy,
                    symbol=None,instrument_tick=None):
    """Checked eligibility ONLY; the caller must reserve and confirm fill.

    No new position can be created by this function. Reprice cannot extend
    eligibility after one confirmed SELL or once the slot allocation is spent.
    """
    first=decimal(first_fill_price)
    offer=decimal(ask)
    paid=decimal(spent)
    limit=decimal(max_per_symbol_seed)
    if any(x is None for x in (first,offer,paid,limit)):return False
    if already_sold or not has_verified_buy or paid>=limit:return False
    ceiling=two_ticks_max(market,first,symbol,instrument_tick)
    if ceiling is None:return False
    return offer<=ceiling
