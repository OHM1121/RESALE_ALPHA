package com.artitrade.mobile;

/** Pure JVM regression for the frozen-engine decision rule (issue #146, 0.3.1). */
public final class EngineHangPolicyTest {
    static void check(boolean v,String msg){if(!v)throw new AssertionError(msg);}
    static final long[] NONE=new long[0];
    static final long UP=EngineHangPolicy.ENGINE_MIN_UP_MS;
    static final long STALE_AGE=60_000L;     // heartbeat older than the 14 s freshness bound

    /** Run supervisor ticks every 10 s from `from` to `to`, return the actions in order. */
    static String run(EngineHangPolicy.State s,long from,long to,long age,boolean started,
                      long[] past,long wallBase){
        StringBuilder out=new StringBuilder();
        for(long t=from;t<=to;t+=EngineHangPolicy.TICK_MS){
            EngineHangPolicy.Action a=EngineHangPolicy.step(s,t,age,started,true,past,wallBase+t);
            if(a!=EngineHangPolicy.Action.NONE)out.append(a).append('@').append((t-from)/1000).append(' ');
        }
        return out.toString().trim();
    }

    public static void main(String[] args){
        // 1. The 2026-11-22 freeze: engine up for hours, heartbeat stale from t0 on.
        EngineHangPolicy.State s=new EngineHangPolicy.State();
        long t0=UP+3_600_000L;
        run(s,0,t0,0,true,NONE,0);                                  // healthy history
        String log=run(s,t0+10_000L,t0+600_000L,STALE_AGE,true,NONE,1_000_000_000L);
        check(log.startsWith("DUMP@"),"first a dump: "+log);
        check(log.contains("RESTART@"),"then a restart: "+log);
        check(log.indexOf("DUMP")<log.indexOf("RESTART"),"dump before restart: "+log);
        // dump happens once per episode, restart no sooner than the threshold
        check(log.split("DUMP").length==2,"exactly one dump: "+log);
        long restartAt=Long.parseLong(log.substring(log.indexOf("RESTART@")+8).split(" ")[0]);
        check(restartAt>=EngineHangPolicy.RESTART_AFTER_STALE_MS/1000-10
            &&restartAt<=EngineHangPolicy.RESTART_AFTER_STALE_MS/1000+10,"restart near 240 s: "+restartAt);

        // 2. A healthy heartbeat never triggers anything.
        s=new EngineHangPolicy.State();
        check(run(s,0,t0,5_000L,true,NONE,0).isEmpty(),"healthy engine is left alone");

        // 3. A slow turn that recovers before the dump threshold changes nothing and resets the episode.
        s=new EngineHangPolicy.State();
        run(s,0,t0,0,true,NONE,0);
        check(run(s,t0+10_000L,t0+80_000L,STALE_AGE,true,NONE,0).isEmpty(),"short stall: no action");
        run(s,t0+90_000L,t0+100_000L,3_000L,true,NONE,0);          // recovered
        check(run(s,t0+110_000L,t0+190_000L,STALE_AGE,true,NONE,0).isEmpty(),
            "episode restarts after recovery (no carry-over)");

        // 4. A first engine start is never judged (long preload is not a hang).
        s=new EngineHangPolicy.State();
        check(run(s,0,UP-10_000L,STALE_AGE,true,NONE,0).isEmpty(),"engine younger than 5 min is not judged");
        s=new EngineHangPolicy.State();
        check(run(s,0,t0,STALE_AGE,false,NONE,0).isEmpty(),"engine not started is not judged");

        // 5. Process suspension (gap in supervisor ticks) restarts the stale clock.
        s=new EngineHangPolicy.State();
        run(s,0,t0,0,true,NONE,0);
        run(s,t0+10_000L,t0+60_000L,STALE_AGE,true,NONE,0);         // 50 s stale observed
        EngineHangPolicy.Action a=EngineHangPolicy.step(s,t0+60_000L+EngineHangPolicy.CONTINUITY_MS+1,
            STALE_AGE,true,true,NONE,0);                            // frozen process thawed
        check(a==EngineHangPolicy.Action.NONE,"a suspension gap must not count as stale time");
        check(s.staleForMs(t0+60_000L+EngineHangPolicy.CONTINUITY_MS+1)==0L
            ||s.staleSinceMs==t0+60_000L+EngineHangPolicy.CONTINUITY_MS+1,"episode restarted after the gap");

        // 6. Restart rate limits (persisted by the caller across the process kill).
        long now=10_000_000_000L;
        check(EngineHangPolicy.restartAllowed(NONE,now),"first restart allowed");
        check(!EngineHangPolicy.restartAllowed(new long[]{now-60_000L},now),"not within 10 min of the last one");
        check(EngineHangPolicy.restartAllowed(new long[]{now-EngineHangPolicy.MIN_RESTART_GAP_MS},now),"10 min later is allowed");
        long[] three={now-3_000_000L,now-2_000_000L,now-1_000_000L};
        check(!EngineHangPolicy.restartAllowed(three,now),"3 restarts within an hour stop further restarts");
        long[] old={now-8_000_000L,now-7_000_000L,now-6_000_000L};
        check(EngineHangPolicy.restartAllowed(old,now),"restarts older than an hour do not count");
        check(EngineHangPolicy.restartAllowed(new long[]{now+5_000_000L},now),"a future timestamp (clock moved back) is ignored");

        // 7. When the limit forbids a restart the policy keeps dumping once and never restarts.
        s=new EngineHangPolicy.State();
        run(s,0,t0,0,true,NONE,0);
        long[] recent={now-1_500_000L,now-1_000_000L,now-700_000L};   // all inside the hour for the whole run
        String limited=run(s,t0+10_000L,t0+900_000L,STALE_AGE,true,recent,now-t0);   // wall clock ~= now
        check(limited.contains("DUMP")&&!limited.contains("RESTART"),"rate limited: "+limited);

        System.out.println("EngineHangPolicyTest OK");
    }
}
