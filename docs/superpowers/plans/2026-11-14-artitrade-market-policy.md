# ARTITRADE v033 Market & Policy Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development; test-first, verify every deliverable.
**Goal:** policy-by-symbol and two-market evidence score, score-band cohort returns in integrated Android APK.
**Architecture:** Android SQLite sidecar stores immutable market scores and cohort entry baselines; dedicated read-only quote fetcher and scorer updates separately from order engine; UnifiedVisualActivity presents market and table layouts. Existing EXE original remains unaffected.
**Tech Stack:** Java 17 Android 35, org.json, SQLite, Chaquopy strategy records (read-only), GitHub Android build.
**Spec:** docs/superpowers/specs/2026-11-14-artitrade-market-policy-design.md

## Global Constraints
- Order logic, broker/API authentication, trade execution and import/overwrite semantics unchanged.
- Source observation timestamp required. Never backfill historical event score with current score.
- Coin score is own experimental rubric; stock is converted EXE directional score, with missing input evidence marked.
- Existing upgrade-signing certificate must match SHA-256 37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6.
## Review Focus
- Symbol same under multiple strategies, one click must not open other symbol history.
- Score 55→20 rebase, 20→25 no rebase; -4→+2 means +6pp.
- Broker disconnect, no quote timestamp/entry, incomplete cohort, delayed Yahoo/Upbit must not fabricate score.
- Historical BUY/SELL before score observations must show missing, not latest.
- Android screen 700dp: 5 policy columns readable and market score tab layout no side whitespace.

### Task 1: symbol-scoped policy
- [ ] Write PolicyHistoryFilterTest; red on absent or incorrectly broad symbol.
- [ ] Implement PolicyHistoryFilter key normalization and selection; apply clicked row source/frame/symbol.
- [ ] UI 5 weighted columns and close/reset filtering, exact historical score from Task 2.
- [ ] Run JVM tests, UI source regression, compile.
### Task 2: score and cohort store
- [ ] Write MarketRegimeRulesTest with 55→51–60 and 20→0–50, 6pp, no lookahead.
- [ ] Implement pure scorer stock EXE components, coin 5 factors, confidence coverage.
- [ ] Implement SQLite market event/position segment store + change-only cadence, exact score-at-fill join.
- [ ] Test missing data, repeated same score range, different strategies.
### Task 3: passive collectors
- [ ] Build independent Java HTTP read-only market collector, 3–5m bounded cadence, timeout.
- [ ] Hook app foreground service to trigger fetch, separate worker from order engine.
- [ ] Test parsing of Yahoo/Upbit fixture and error handling.
### Task 4: market top-level tab and graphics
- [ ] Top row 시장|주식|코인, market page, no trading controls.
- [ ] Draw numeric score gauge/filled horizontal bars and grouped cohort statistics for both markets.
- [ ] Show source, timestamp, group sample count, data gaps.
- [ ] Run UI compile and regression.
### Task 5: release
- [ ] Full compile-only regression + signed unified APK against original cert.
- [ ] PC EXE only if original source safely patched and tested; otherwise report accurate limitation.
