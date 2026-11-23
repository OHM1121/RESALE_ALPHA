package com.artitrade.mobile;

/**
 * 0.3.3 (issue #148): pacing of the read-only ledger VIEW builder (hub_records_ui).
 *
 * Evidence (error export 2026-11-23 10:31, app 0.3.2): one build took 100-500+ s on the phone and the
 * next one started immediately after it ended (the fixed 6.5 s / 17 s gap, counted from the START,
 * had long passed), so the Python GIL and the CPU stayed saturated by a pure display job while the
 * trading engine, the Upbit WebSocket and the scanners starved.
 *
 * Now a build is due only when BOTH hold:
 *  - the original 6.5 s (screen visible) / 17 s (hidden) has passed since the last START, and
 *  - a quiet gap of {@link #DURATION_FACTOR} x the last build's duration (capped at
 *    {@link #MAX_QUIET_MS}) has passed since the last END.
 * So a slow view job can use at most ~20% of the time; a fast build keeps the original cadence.
 * Pure Java, no Android dependency (JVM-tested in CI).
 */
final class UiRecordPacingV0330 {
    static final long VISIBLE_GAP_MS=6_500L;
    static final long HIDDEN_GAP_MS=17_000L;
    static final long DURATION_FACTOR=4L;
    static final long MAX_QUIET_MS=300_000L;

    private UiRecordPacingV0330(){}

    static long quietAfterEndMs(long lastDurationMs){
        if(lastDurationMs<=0L)return 0L;
        if(lastDurationMs>MAX_QUIET_MS/DURATION_FACTOR)return MAX_QUIET_MS;
        return lastDurationMs*DURATION_FACTOR;
    }

    static boolean due(long nowMs,boolean visible,long lastStartedMs,long lastFinishedMs,long lastDurationMs){
        long base=visible?VISIBLE_GAP_MS:HIDDEN_GAP_MS;
        if(nowMs-lastStartedMs<base)return false;
        if(lastFinishedMs<=0L)return true;
        return nowMs-lastFinishedMs>=quietAfterEndMs(lastDurationMs);
    }
}
