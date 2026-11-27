# ARTITRADE - real-trading parity audit of the paper fill models (rule R15)

Owner rule R15 (2026-11-27): the paper program becomes the real program; paper-only conservatism without a real reason is removed; no code that a real
order could not use; a real non-fill stays a non-fill. Status per version; "audited" = read in the code, not "device verified".

| # | Item | Where | Paper behaviour | Real order | Status |
|---|---|---|---|---|---|
| 1 | Displayed best ask / bid size takeable | `d_v19_portfolio.Book.available / available_sell / reserve` (AT, MA, MarketTrend stock wallets) | only 50 % (`BOOK_PARTICIPATION`) | the whole displayed size at the displayed price | **Changed in 0.5.0** (`real_parity_v0500.install_book_depth`, 100 %; one snapshot is still shared by all wallets) |
| 2 | Paper slippage | `projectgmo/engine.py` `paper_buy_slip / paper_sell_slip` = 0.05 % plus tick rounding | fill one tick + 0.05 % worse than the displayed ask / bid | the displayed price | **Changed in 0.5.0** (`real_parity_v0500.real_price_cfg`, 0; fees and tax stay) |
| 3 | GMO fill needs a trade print | `Engine.paper_match` | no print = no fill | marketable limit fills against the book on arrival | Partly relaxed in 0.4.9 (two consecutive fresh snapshots); the single-snapshot "arrival book" case (11-26 Doosan Bobcat) needs the owner's latency / feed-liveness decision |
| 4 | 3 s freshness of book / print | `Quote.receive_delay_seconds`, `BOOK_MAX_AGE_SEC`, `exchange_stamp_is_fresh` | stale = ignored | an order must not be sent on a stale quote either | Kept (protects real orders) |
| 5 | Resting limit buy fills = 50 % of an observed print at the limit | `d_v19_portfolio._buy_limit_depth` passive touch, `BOOK_PARTICIPATION` | queue position unknown, half of the print volume | depends on the queue position | Kept as an explicit proxy (cannot be known without the broker) |
| 6 | 10 % print proxy when no depth is shown | `Engine.paper_match_krx_print_proxy` | 10 % of the print volume, minimum one share | not applicable | Kept as an explicit proxy |
| 7 | Order latency | nowhere | the order is matched against the snapshot it was decided on | the order meets the book some time later (price can jump) | Not modelled; needs a measured latency (first export) |
| 8 | Sell "slippage beyond sell floor" cancel | `Engine.paper_match` SELL branch | cancels the order | a resting limit sell waits | To review (not changed) |
| 9 | Real mode and the supply-demand D+1 plan | `gmo_d1_preplan_v097._step` returns when `ARTITRADE_REAL_MODE=1`; `deny_native_open` blocks the native entry for a planned code | no D+1 order is planned in that mode | an order is needed | To verify in code before the real switch (not changed) |
| 10 | Upbit coin paper, MA / MarketTrend wallet fills, manual paper sells | `d_upbit.py`, `market_trend_wallet_v043/v054`, `stock_manual_v086` | not audited yet | | Next audit |
