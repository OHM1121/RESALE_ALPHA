package com.artitrade.mobile;

/** Pure JVM regression for the ledger-view pacing rule (issue #148, 0.3.3). */
public final class UiRecordPacingTest {
    static void check(boolean v,String msg){if(!v)throw new AssertionError(msg);}

    public static void main(String[] args){
        long t=1_000_000_000L;
        // A fast build keeps the original cadence: 6.5 s visible, 17 s hidden, counted from the START.
        check(!UiRecordPacingV0330.due(t+6_000L,true,t,t+300L,300L),"visible: not before 6.5 s");
        check(UiRecordPacingV0330.due(t+6_500L,true,t,t+300L,300L),"visible: due at 6.5 s");
        check(!UiRecordPacingV0330.due(t+16_000L,false,t,t+300L,300L),"hidden: not before 17 s");
        check(UiRecordPacingV0330.due(t+17_000L,false,t,t+300L,300L),"hidden: due at 17 s");
        // First build ever: due.
        check(UiRecordPacingV0330.due(t,true,0L,0L,0L),"first build is due");

        // The 2026-11-23 case: a 180 s build. The next one must NOT start right after it ends.
        long dur=180_000L, end=t+dur;
        check(!UiRecordPacingV0330.due(end+1_000L,true,t,end,dur),"no back-to-back slow builds");
        check(!UiRecordPacingV0330.due(end+299_000L,true,t,end,dur),"quiet gap is capped at 300 s, not before");
        check(UiRecordPacingV0330.due(end+300_000L,true,t,end,dur),"due after the 300 s cap");

        // A 10 s build: quiet 40 s after its end.
        long d10=10_000L, e10=t+d10;
        check(!UiRecordPacingV0330.due(e10+39_000L,true,t,e10,d10),"10 s build: quiet 40 s");
        check(UiRecordPacingV0330.due(e10+40_000L,true,t,e10,d10),"10 s build: due after 40 s");

        // Duty cycle of a slow view job is at most 1/(1+4) = 20 %.
        check(UiRecordPacingV0330.quietAfterEndMs(20_000L)==80_000L,"4x duration");
        check(UiRecordPacingV0330.quietAfterEndMs(Long.MAX_VALUE/2)==UiRecordPacingV0330.MAX_QUIET_MS,"no overflow");
        check(UiRecordPacingV0330.quietAfterEndMs(-5L)==0L,"negative duration is ignored");
        System.out.println("UiRecordPacingTest OK");
    }
}
