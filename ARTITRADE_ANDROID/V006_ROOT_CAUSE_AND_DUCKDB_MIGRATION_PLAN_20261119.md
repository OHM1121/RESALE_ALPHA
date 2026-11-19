# ARTITRADE v006 - trading-disabled investigation and storage migration plan
Date: 2026-11-19 KST. Status: source fixes + offline regression, NOT phone-verified.
Baseline: pre-reset fix-artitrade-v095-errors-20261118 -> signed v005 fix-artitrade-005-score-feed-and-empty-wallet-20261119.
Desktop comparison: user-supplied ARTITRADE_STOCK_CLOSE_PNL_FIX2_MANIFEST_SYNC_20261112.exe (dated 11/12) is a WINDOWS application, not the APK before 11/18 reset. Unpacked offline; archive holds 2187 members, 254 Python files including runtime dependencies. Preserve its older source as a separate functional reference, not an Android drop-in.

## Diagnosed gates / root-cause candidates
1. Android FreshEpochGateV106.mandatoryFreshStartPending() stops service before ANY strategy startup until marker committed. Interrupted marker or read failure means no quote feeds or buys.
2. launchWithMigration checks migrationGate(), derived-cache guard, LedgerAntiRollbackV002.verify() before strategy.start(). A failed verification can persist blocked_reason; do not bypass without proving ledger integrity. Distinguish intentional no-trading from app regression with on-device non-secret logs.
3. Old code checked only '!started.optBoolean("ok",true)' then continued to hubRecordsUi(), allowing a failed Python engine to resemble a running UI. v006 now records ENGINE_START_FAILED, refuses falsely healthy startup and stops safely.
4. StrategyRuntimeBridge.start() may fail on Python import, credential/store errors or native core load; service heartbeat / market-score UI are NOT evidence of an enabled strategy.
5. market score and order evidence have different authorities. v005 scoreSnapshotV005 is a UI-only display source; entry rules in reference_1118_runtime.py and at_market_score_gate_v107.py still use independently validated score evidence, and freshness/book/session/wallet constraints must be audited independently.
6. The current APK is intentionally PAPER/simulated only: android_runtime_v078.set_live(true) explicitly returns SIMULATED_ACCOUNT_ONLY_NO_BROKER_ORDERS. Never imply real broker orders are working on a phone or enable LIVE automatically.
7. MarketTrend: new_legacy_1118_policy.TREND_FRAMES was already 30m/60m/day, yet market_trend_v043.FRAMES, wallets, coordinator and Java UI still accepted and showed 5m. This is a genuine multi-layer policy mismatch. v006 closes buy boundaries in both Trend wallets and legacy 11/18 policy overlay, excludes 5m active scans/watch/UI/history/capital controls, while keeping 5m native AT/MA and existing Trend 5m SELL eligibility.
8. Real phone source evidence is missing: broker WS 0B/0D arrival, coin ticker/orderbook received timestamps, private epoch marker state, anti-rollback reason, Python traceback, candidate block reasons, native PAPER positions, Android foreground/battery state. No static build can determine which gate actually blocked the user's device.

## Compatibility / regression constraints
- No SQLite ledger deleted, rolled back, reassigned, exported to new epoch or rewritten.
- No fake orders, positions or fills. Existing 5m Trend holdings, if any, must remain visible and SELL-eligible.
- Preserve stock and coin original reference/AT, MA 5m execution. Do not globally remove 5m from market_trend_v043.FRAMES because other engines and persisted wallet schemas depend on that symbol.
- New branch uses independent native score journal, original actual-execution timestamp matching, and unchanged broker fill reconciliation.
- Verify Java compile and existing 8-wallet PAPER scenarios, abnormal disconnect/restart tests, and stock/coin old/new Trend entry tests in CI. Compiling or native simulation never certifies on-device broker connectivity.

## DuckDB adoption decision
Decision: keep SQLite as the synchronous transactional truth for ACTIVE cash/position/order/fill and execution idempotency, as well as score_events needed at BUY/SELL. Use DuckDB first on PC/off-device for ANALYTICS; consider opt-in Android archive only after a supported ABI integration is proven. Do NOT replace SQLite or move live execution lookup to a separate database during a trading-restoration incident.

Data flow:
1. SQLite transaction commits real or PAPER execution row, order id and archival outbox row with UNIQUE event_id.
2. A separate bounded low-priority archive worker copies completed trades, buy/sell timestamps, prices, follow-up maximums, research records, AI features and error events to an append-only transfer store or synchronized PC endpoint.
3. A single DuckDB writer imports batches with event_id PRIMARY/UNIQUE and upsert semantics. Acknowledgement occurs only after archive write+verification; retry after crash is idempotent.
4. All active position high-water/next-sell tracking stays in SQLite until the position is closed and the observation window is finalized. Never archive-and-delete active position state. Longer follow-up high tracking needs dedicated observation status so post-sell highs can continue growing.
5. Retention/compaction: optional, only after counting/checksumming per market/strategy/table/epoch and verifying source/target parity, restoring from backup, and preserving independent anti-rollback checkpoint. No archive loss may lead to a fabricated execution.

Risk mitigations:
- Android arm64/Chaquopy is not interchangeable with Linux AArch64 DuckDB Python wheels; native ABI/build size/dependencies can fail. Keep Android free of DuckDB until toolchain proof.
- DuckDB analytical memory and startup/IO bursts can delay critical broker loops: process separation on PC, bounded batches/backpressure, no hot-path synchronous queries.
- Multiple Windows/Android processes opening one native .duckdb read-write file can conflict: single-writer service; do not share file over network.
- Two databases are not an atomic transaction: durable SQLite outbox + monotonically acked sequence + safe retry + de-dup and periodic parity check.
- DuckDB does not eliminate costs of SQLite long transactions or large UI JSON: fix cache/windowing and score/UI lanes independently.
- Error logs contain secrets? Redact credentials, tokens and customer identifiers before archive; encrypt backups and scope access.
- Migration: schema-versioned staging, test batch parity, 7-day canary, rollback feature flag, never silently delete source.

Open gating evidence required before shipping APK: successful CI, read-only on-device logs and per-engine candidate->buy->sell->position reconciliation. No actual mobile verification yet.
