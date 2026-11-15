# ARTITRADE v0.7.57 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task. Every task uses red-green regression tests and full integration verification.

**Goal:** Warm return without splash, honest background service status, result-only AI display, horizontally scrollable evidence tied to individual trades, compact navigation.

**Architecture:** Preserve existing START_STICKY foreground service and original broker engine, add bounded UI restore policy and service heartbeat monitoring. Keep research engine unchanged; add read-only evidence joiner and display it in existing transaction/policy history after background loading. No live trades or source-ledger edits.

**Tech Stack:** Android Java, Chaquopy Python, SQLite, unittest, Gradle GitHub Actions.

**Spec:** docs/superpowers/specs/2026-11-16-v057-background-ai-ui-design.md

## Global Constraints
- VersionCode 64, same package and original signing certificate, incremental install.
- No broker/order/seed/strategy changes, no original trading database schema or data modification.
- Watchdog timeout is UI-only; never mark a stale market feed healthy.
- Evidence linkage requires strict original position identity or unique confirmed timestamp; ambiguous links omitted.

## Review Focus
1. Process death after screen off must not be misreported as live merely because last snapshot says connected.
2. Activity recreation must not trigger another order engine or reset settings.
3. 16-second first-load delay should not block user forever if local I/O stalls; stale status remains visible.
4. Multiple trades in same symbol must never receive each other's after-sale peak.
5. Horizontal table header and rows must scroll together without truncating source evidence.

### Task 1: Warm-resume lifecycle
- [ ] Add regression for unconditional showBoot and foreground service stale heartbeat.
- [ ] Verify failure on previous source.
- [ ] Implement fast shell from heartbeat / recreated saved state, bounded cold-load watchdog, onResume stale START idempotency, persisted process diagnostics and health drawer.
- [ ] Verify new and existing boot service tests.

### Task 2: Compact native navigation
- [ ] Add tests for 26dp header, 26dp market and 27dp strategy row, status moved to drawer.
- [ ] Verify old layout fails.
- [ ] Implement smaller text/navigation geometry without changing trading content rows.
- [ ] Verify UI tests + Android compiler.

### Task 3: AI outcome presentation
- [ ] Add test proving three detailed research cards not called from renderAiAnalysis; original report generation still invokes all detailed source analyzers.
- [ ] Verify old code fails.
- [ ] Remove presentation only, retain JSON/ZIP and diagnostic findings.
- [ ] Verify existing v056 AI scope tests.

### Task 4: Read-only per-trade evidence
- [ ] Add Python SQLite fixtures for two same-symbol episodes, unique source_ref and matching time.
- [ ] Verify previous source fails.
- [ ] Add trade_history_evidence_v057.py read-only bulk fetch for visible rows. Return only verified per-execution evidence; no DB writes.
- [ ] Add evidence strip to executions; synchronized horizontally-scrolling policy table with evidence columns, sourced from async lookup cache; use no UI-thread SQLite joins.
- [ ] Verify unit tests, Java compiler, emulator if available.

### Task 5: Release
- [ ] Full new and historical regression suite; document baseline unrelated failures.
- [ ] GitHub isolated review branch and original-cert APK build pinned to exact tested commit, check SHA256 and versionCode.
- [ ] Validate Android foreground/home/return with ADB emulator; report actual result, not assumption.
