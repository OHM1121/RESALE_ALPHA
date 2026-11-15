# ARTITRADE v0.7.57 - Background warm return, AI result focus, dense UI

Requested by user: stop repeated splash/preload on screen return, keep service visibility, move technical AI evidence from AI results into trade-specific history, shrink navigation chrome.

Approaches: (1) hide preload only (fails when service dies), (2) always rebuild/retain UI in Activity (Android may destroy process), (3) chosen: service heartbeat + Activity recreation fast path, non-blocking bounded cold-load fallback, separate read-only evidence view. No new redundant market feed or trading thread.

UI:
- When service heartbeat is fresh or activity is recreated, immediately render native shell and asynchronously refresh the same account data; never fabricate a live connection.
- Cold start readiness screen must timeout to honest empty/stale state independently of any stalled I/O job. Show service age and CPU/Wi-Fi lock state in right drawer; allow idempotent START on truly stale foreground resumes with rate-limit.
- Brand header approx half height; market selector and strategy selector approx two thirds height; moved live/ledger/PC detail into right drawer.
- AI analysis still computes and exports comprehensive evidence, approved quantitative evidence, and post-exit research, but these 3 blocks no longer render in AI summary. AI screen emphasizes findings, backtested quantitative comparisons (if valid), and RSI-specific findings.
- Execution history adds horizontal per-record evidence strip. Policy history adds one horizontally-scrollable aligned wide table including recorded evidence and verified post-exit high. Never label a case linked without identity/confirmed timing.
- Evidence lookup is read-only and bounded. No input to buy/sell logic, original ledger or AI sidecar write. Missing evidence rendered as '미기록', never zeros/guesses.

Constraints: Retain package com.artitrade.unified.s21dex and original signing identity; versionCode 64 versionName 0.7.57; old live/PAPER flags unchanged; no new live auto-arm or strategy/order changes; no data deletion. Android cannot guarantee execution if OS/user force-stops the app; show truthful heartbeat diagnostics.
