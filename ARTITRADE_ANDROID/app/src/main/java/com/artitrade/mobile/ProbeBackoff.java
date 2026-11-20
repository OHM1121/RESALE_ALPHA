package com.artitrade.mobile;

/**
 * Android-free retry schedule for an OPTIONAL local service probe.
 *
 * While the probe succeeds it stays due on every loop turn (cadence of the
 * caller is unchanged). After consecutive failures the next attempt is
 * delayed {@code base, 2*base, 4*base ...} capped at {@code max}. A success
 * resets the schedule at once. The caller injects a MONOTONIC clock
 * ({@code SystemClock.elapsedRealtime()}), never wall time.
 *
 * It only paces the probe. It never decides trading state: whoever owns the
 * "unverified" state must keep it fail-closed until a probe really succeeds.
 */
final class ProbeBackoff {
    private final long baseMs;
    private final long maxMs;
    private int failures=0;
    private long nextDueMs=Long.MIN_VALUE;

    ProbeBackoff(long baseMs,long maxMs){
        if(baseMs<=0||maxMs<baseMs)
            throw new IllegalArgumentException("need 0 < base <= max");
        this.baseMs=baseMs;
        this.maxMs=maxMs;
    }

    synchronized boolean due(long nowMs){return nowMs>=nextDueMs;}

    synchronized void onSuccess(){failures=0;nextDueMs=Long.MIN_VALUE;}

    synchronized void onFailure(long nowMs){
        if(failures<1_000_000)failures++;
        nextDueMs=nowMs+delayMs(failures);
    }

    synchronized int failures(){return failures;}

    /** Delay applied after the given number of consecutive failures. */
    long delayMs(int consecutiveFailures){
        if(consecutiveFailures<=0)return 0L;
        long delay=baseMs;
        for(int i=1;i<consecutiveFailures&&delay<maxMs;i++)
            delay=delay>maxMs/2?maxMs:delay*2;
        return Math.min(delay,maxMs);
    }

    /**
     * Whether this failure deserves a persisted fault row: the first one of a
     * streak and then every 10th. (The fault store also merges identical
     * rows, so this only bounds how often the store is rewritten.)
     */
    synchronized boolean shouldReport(){return failures==1||failures%10==0;}
}
