# ARTITRADE native 2026-11-18 crash evidence and repair boundary

Source: the user-supplied ARTITRADE_ALL_ERROR_HISTORY_20261118_160809_5d193d_ANALYSIS_READY.json, independently text-readable (107,955 bytes). It is diagnostic metadata only and **not** the underlying SQLite rows, trades, or all 8 ZIP parts. Do not put the original user file or any broker secrets in this public repository.

## Facts confirmed in user-supplied diagnostic metadata

- 8 compressed parts, each under 95,000,000 bytes; 40 source files and 3,291,157,900 snapshot source bytes across the source SQLite/JSON/JSONL files.
- Android native fault summary: 30 records; includes 5 OutOfMemoryError records labelled ui_record_source_throttled_v087, 1 additional uncaught OutOfMemoryError in UI activity, 4 recorded OS Java crashes, 3 recorded native crashes, 8 manual PAPER sell rejections, 2 failed SQLite backup attempts, 7 diagnostic memory-pressure events. Categories above can overlap causally; do not claim these are distinct device restarts.
- In the native crash stacks, java.lang.OutOfMemoryError attempts to allocate **75,497,480 bytes** inside org.json.JSONObject.toString() → UnifiedTradingService.scheduleUiRecordsIfDue, with **256MiB** maximum Java heap and only about **47-48MiB** available. The previous screen source built a huge JSONObject then serialized it before checking LargeRecordStoreV087.MAX_UI_RECORD_CHARS. This places the size guard **after** the OOM allocation.
- Another OutOfMemoryError stack points into org.json.JSONTokener → UnifiedVisualActivity.refresh() (legacy large XML or UI data JSON parse).
- Memory heartbeat reports java_heap_used_mb 223/228/230/232/239/244 out of 256, independently corroborating pressure.
- Manual PAPER sell rejection strings recorded include:
  - RuntimeError: 실주문 및 계좌 접근 API는 D버전에서 영구 차단
  - ValueError: FRESH_STOCK_TRADE_MISSING
  - ValueError: FRESH_COIN_ORDERBOOK_MISSING
  - ValueError: COIN_MANUAL_FILL_NOT_RECORDED
  These are distinct safety/order provenance checks. It would be unsafe to bypass them or manufacture paper fills without corresponding original quote/ledger proof.
- Two historical combined_error_export failures report SQLITE_BACKUP_UNAVAILABLE_SOURCE_UNCHANGED ... SQLITE_BACKUP_STALLED for upbit_paper.sqlite3. They confirm snapshot retries once failed but do not establish that the final 8-part export is damaged. Its manifest exists and is readable in standalone JSON.
- The manifest reports stock MA positions=0/executions=0 in current and both epoch ledgers, stock Trend positions=0/executions=0, and CW positions=6/executions=12. It confirms zero rows but *not* the initial MA/Trend market signal rejection reason; those rows require the original journal contents.

## v104 change based on actual OOM stack, not speculation

1. Android runtime gains hub_records_ui_v104, a screen-only bounded projection constructed BEFORE the Python JSON is serialized for Java. Full original Runtime.hub_records, SQLite books, candidate evidence, and all ZIP source snapshots are unchanged. The JSON view records omitted UI counts by path; it must not be used as broker order authority or complete PC research archive.
2. The phone's periodic service UI refresh uses hubRecordsUi() instead of full hubRecords() and never places the giant full snapshot through Java JSONObject.toString().
3. Initial app startup no longer serializes the complete full hub JSON into SharedPreferences XML. It stores the small display projection with the existing atomic file code.
4. Old oversized JSON files and legacy SharedPreferences raw JSON are excluded from UI parsing. They are display caches only and must not delete or reset the original DB.
5. When original complete PC ledger synchronization is explicitly enabled, the full source bucket path is still used. Do NOT accidentally upload the UI-projected partial history as an entire PC source archive.
6. Device quote 3-second validity windows, original manual sell checks, D19 exit policy, live broker safety and actual SQLite executions are not altered.

## Important separate failure: ChatGPT ClientError

The ChatGPT tool returned caas.internal.errors.ClientError for trivial process execution (/bin/true) without opening any ZIP, and the Files service did successfully materialize one 90,947,994-byte ZIP. This isolates the immediate obstruction to container/program execution rather than proving ZIP corruption, ZIP complexity or a file-size threshold is its root. The exact underlying platform cause cannot be determined without OpenAI tool-host/infrastructure execution logs, which are not exposed by the current tools. The standalone JSON file is text-readable through Files and yielded the actual native crash evidence above.

## Verification and next evidence

- Run v104 exact-source Python projection tests (multi-million-character fixture, no original mutation), prior v101 ZIP CRC/SHA/SQLite snapshot restore tests, and existing v091 D19 manual exit/recovery regressions.
- Android Gradle assembled APK must use the original signing certificate SHA-256 37967ed67598f4137045db4a31b16e156993f6c7c88c9d93d0905b27d5f723c6; package com.artitrade.unified.s21dex. VersionName must be bare numeric 0.9.4 without suffix.
- Monitor Java heap and UI refresh long enough to rule out recurrent UI OOM on a physical device. A CI test verifies the code paths only and cannot prove no further real phone crashes.
- For original 1/2/3-stage selling and MA/Trend/CW missing purchases, read original exact SQLite rows from all eight parts (never the table counts alone) or provide a separately generated small read-only JSONL evidence export with per-record provenance and hash. Do not claim the broker/data root is fixed based on this summary.
