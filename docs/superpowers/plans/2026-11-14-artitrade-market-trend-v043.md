# ARTITRADE v0.7.43 Market Trend RSI20 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a signed, update-compatible ARTITRADE APK integrating market-score-driven RSI(20) alternate entry/exit rules in the existing stock and coin reference/MA strategies.

**Architecture:** Add a shared RSI20 candidate/series evaluator and durable event store, a runtime coordination layer that gates existing entry algorithms without changing existing-position exits, and thin stock/coin order adapters that allocate from existing strategy/frame accounts. Extend the existing snapshot UI and AI research only, without a new strategy tab.

**Tech Stack:** Android Java, Chaquopy Python 3.11, SQLite/WAL, existing Kiwoom and Upbit realtime/order interfaces, Gradle/GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-14-market-trend-rsi20-design.md`

## Global Constraints
- Keep package `com.artitrade.unified.s21dex`, original signing certificate, and existing installed user ledgers.
- No new tab, no new operating seed, preserve AT/MA separated funds/timeframes and GMO/CLOSE.
- Negative score replaces AT/MA *new entry* only; existing positions keep their original exit policy.
- RSI(20) 5m/30m/60m/day, only freshly observed current-bar data; entry at bar-close -12s (KRX final bars at 15:19:48).
- Top 50 KOSPI+50 KOSDAQ and top 40 Upbit KRW, refresh 5min; avoid same-symbol buys across AT/MA/timeframes.
- Full paper/live separation and actual/virtual research separation; no hypothetical fills represented as broker executions.

## Review Focus
- Market score read returns stale/missing/bad coverage: block entry but preserve sell monitoring.
- Stock 15:20 and close/holiday edge: cancel unmatched pending buys and no new auction orders.
- Race between strategy wallets or frame signals: atomic reservation and first valid signal priority.
- Restart after partial fill or market gate flip: reconcile broker order IDs and WAL before new buy.
- Crypto decimal quantities/ticks and stock lot/ticks: truncate legally; never sell more than remaining holdings.

### Task 1: Data & score gate
**Files:** Create `ARTITRADE_ANDROID/app/src/main/python/market_trend_v043.py`; test `ARTITRADE_ANDROID/ui_tests/test_v043_market_trend.py`.
**Interfaces:** `rsi20(closed_prices, forming_close=None)`, `candidate_transition(state, closed_rsi, live_rsi)`, `market_mode(score,status,last_seen)`, `market_hold_until(entry_at,frame,market,calendar)`.
- [ ] Write failing tests for RSI20 values, insufficient history, 0 vs negative score, re-arm/re-entry, and holiday/coin hold calculation.
- [ ] Run `python3 -m unittest ... -v` and confirm failed expectations.
- [ ] Implement pure functions and durable SQLite event journal.
- [ ] Run tests and confirm pass.
- [ ] Commit.

### Task 2: Full trade state machine and signal journal
**Files:** Extend `market_trend_v043.py`; extend `test_v043_market_trend.py`.
**Interfaces:** `TrendEngine.advance_candle`, `TrendEngine.offer_signal`, `TrendEngine.on_fill`, `TrendEngine.on_quote`, `TrendEngine.exit_decision`, `TrendEngine.snapshot`.
- [ ] Failing tests for pre-close boundaries, TF priority, first/second targets (3% cap), 30/35/35, 4% peak trail, -9% pre-close stop, +3 RSI exits, 48/72/120h/10d holdings, repeated-signal lock, missed-buy virtual performance.
- [ ] Implement SQLite transaction-safe event/position/claim updates.
- [ ] Verify no double actual/virtual credits and no repeated same-bar action.
- [ ] Commit.

### Task 3: Stock/coin runtime adapters and original-entry gating
**Files:** Create `ARTITRADE_ANDROID/app/src/main/python/market_trend_runtime_v043.py`; modify `android_runtime_v078.py`; test `test_v043_runtime.py`.
**Interfaces:** `install(core)`, `start_market_trend(rt)`, `stop_market_trend(rt)`, `market_trend_status(rt)`.
- [ ] Failing adapter tests for stock/coin independent score and buy pause, original positions still exiting, pending order preservation vs cancel.
- [ ] Hook existing runner tick/feed and scan paths without duplicating market connections; track refresh and stale-data timeout.
- [ ] Attach to original AT/MA frame wallets and existing brokers with pending order reconciliation; keep bid/ask validity and existing reserved funds checks.
- [ ] Run unit and regression tests; commit.

### Task 4: UI & AI history
**Files:** Modify `UnifiedVisualActivity.java`, `StrategyRuntimeBridge.java`, `ai_research_v040.py` / add query glue; test `test_v043_ai.py`.
**Interfaces:** snapshot JSON adds `market_trend_status`, record fields include `entry_mode` and explicit `rsi20_live` / `rsi20_closed`.
- [ ] Failing test for separate actual/paper data and history retention.
- [ ] Add `현재 적용 매매법` text to existing AT/MA view; no additional strategy tab.
- [ ] Add AI evidence ingestion and safe period-specific analysis; run tests and commit.

### Task 5: Build, validate signature and deliver APK
**Files:** Update `app/build.gradle` version 50 and `.github/workflows/artitrade-build-v043-original-signing.yml`.
- [ ] Run all v043 Python tests and prior v028-v042 suites; verify Android build.
- [ ] Build `:app:assembleUnifiedDebug` under original cached keystore and assert original cert fingerprint.
- [ ] Upload `ARTITRADE_UNIFIED_v0.7.43_20261114.apk` and SHA256 via Actions; retrieve artifact and provide verified download link.
- [ ] Reject delivery if unsafe/partial integration or build/signature checks fail.
