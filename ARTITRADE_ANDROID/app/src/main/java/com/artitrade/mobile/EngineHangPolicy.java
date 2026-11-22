package com.artitrade.mobile;

/**
 * Android-free decision rule for a FROZEN trading engine (issue #146, 0.3.1).
 *
 * Evidence: in the 2026-11-22 20:35 export the Python engine stopped at 19:39 and stayed stopped
 * for 55 minutes while the process and the UI thread were alive. The UI watchdog logged six
 * "feed_restart_requested" faults, but every restart goes through the bridge's synchronized
 * methods, which queue behind the blocked synchronized snapshot() call, so nothing ever ran.
 * A recovery that depends on the frozen part cannot recover it.
 *
 * This policy is evaluated by an independent supervisor thread that never takes the bridge
 * monitor. It only decides; the service performs DUMP (record where the threads are blocked)
 * and RESTART (end the process; START_STICKY brings the service back, ledgers are SQLite
 * transactions and the paper engine re-reads them on start).
 *
 * Guards against a false restart:
 *  - the engine must have been reported started for ENGINE_MIN_UP_MS (a long first start is not a hang);
 *  - staleness must be observed on CONTINUOUS supervisor ticks; a gap above CONTINUITY_MS means the
 *    whole process was suspended (doze/freezer), so the clock for this episode starts over;
 *  - at most MAX_RESTARTS_PER_HOUR restarts, at least MIN_RESTART_GAP_MS apart (persisted by the caller).
 */
final class EngineHangPolicy {
    enum Action {NONE,DUMP,RESTART}

    static final long TICK_MS=10_000L;
    static final long CONTINUITY_MS=30_000L;
    /** Same freshness bound the UI uses for "background engine running". */
    static final long FRESH_MS=14_000L;
    static final long DUMP_AFTER_STALE_MS=90_000L;
    static final long RESTART_AFTER_STALE_MS=240_000L;
    static final long ENGINE_MIN_UP_MS=300_000L;
    static final long MIN_RESTART_GAP_MS=600_000L;
    static final int MAX_RESTARTS_PER_HOUR=3;

    /** Mutable per-process state; owned by the single supervisor thread. */
    static final class State{
        long lastTickMs=Long.MIN_VALUE;
        long engineUpSinceMs=Long.MIN_VALUE;
        long staleSinceMs=Long.MIN_VALUE;
        boolean dumped=false;
        long staleForMs(long nowMs){return staleSinceMs==Long.MIN_VALUE?0L:nowMs-staleSinceMs;}
    }

    private EngineHangPolicy(){}

    /**
     * @param nowMs        monotonic clock (SystemClock.elapsedRealtime())
     * @param heartbeatAgeMs wall-clock age of the service heartbeat, or -1 when it was never written
     * @param pastRestartsWallMs wall-clock times of earlier hang restarts (any order, may be empty)
     * @param nowWallMs    wall clock now
     */
    static Action step(State s,long nowMs,long heartbeatAgeMs,boolean engineStarted,
                       boolean serviceRunning,long[] pastRestartsWallMs,long nowWallMs){
        long gap=s.lastTickMs==Long.MIN_VALUE?0L:nowMs-s.lastTickMs;
        s.lastTickMs=nowMs;
        if(!serviceRunning||!engineStarted){
            s.engineUpSinceMs=Long.MIN_VALUE;
            clearEpisode(s);
            return Action.NONE;
        }
        if(s.engineUpSinceMs==Long.MIN_VALUE)s.engineUpSinceMs=nowMs;
        if(gap>CONTINUITY_MS)clearEpisode(s);          // the process itself was suspended
        if(nowMs-s.engineUpSinceMs<ENGINE_MIN_UP_MS||heartbeatAgeMs<0){
            clearEpisode(s);
            return Action.NONE;
        }
        if(heartbeatAgeMs<=FRESH_MS){
            clearEpisode(s);
            return Action.NONE;
        }
        if(s.staleSinceMs==Long.MIN_VALUE)s.staleSinceMs=nowMs;
        long stale=nowMs-s.staleSinceMs;
        if(stale>=RESTART_AFTER_STALE_MS&&restartAllowed(pastRestartsWallMs,nowWallMs)){
            clearEpisode(s);
            s.engineUpSinceMs=Long.MIN_VALUE;
            return Action.RESTART;
        }
        if(stale>=DUMP_AFTER_STALE_MS&&!s.dumped){
            s.dumped=true;
            return Action.DUMP;
        }
        return Action.NONE;
    }

    static boolean restartAllowed(long[] pastWallMs,long nowWallMs){
        int inHour=0;
        long latest=Long.MIN_VALUE;
        if(pastWallMs!=null)for(long t:pastWallMs){
            if(t>nowWallMs)continue;                    // clock moved back: ignore the entry
            if(nowWallMs-t<3_600_000L)inHour++;
            if(t>latest)latest=t;
        }
        if(inHour>=MAX_RESTARTS_PER_HOUR)return false;
        return latest==Long.MIN_VALUE||nowWallMs-latest>=MIN_RESTART_GAP_MS;
    }

    private static void clearEpisode(State s){
        s.staleSinceMs=Long.MIN_VALUE;
        s.dumped=false;
    }
}
