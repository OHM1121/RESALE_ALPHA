package com.artitrade.mobile;

/** Pure JVM regression for the optional-MSERVER probe schedule. */
public final class ProbeBackoffTest {
    static void check(boolean v,String msg){if(!v)throw new AssertionError(msg);}

    public static void main(String[] args){
        ProbeBackoff p=new ProbeBackoff(2_500L,30_000L);
        check(p.due(0L)&&p.due(Long.MAX_VALUE),"never failed => always due (cadence unchanged)");
        long now=1_000L;
        long[] expected={2_500L,5_000L,10_000L,20_000L,30_000L,30_000L,30_000L};
        for(int i=0;i<expected.length;i++){
            p.onFailure(now);
            check(p.failures()==i+1,"failure count");
            check(!p.due(now+expected[i]-1),"not due before delay #"+(i+1));
            check(p.due(now+expected[i]),"due exactly at delay #"+(i+1)+" = "+expected[i]);
            now+=expected[i];
        }
        check(p.delayMs(0)==0L,"no failures => no delay");
        check(p.delayMs(1_000_000)==30_000L,"huge streak stays capped without overflow");
        p.onSuccess();
        check(p.failures()==0&&p.due(0L),"success resets the schedule immediately");

        // Reporting: first failure of a streak, then every 10th.
        ProbeBackoff r=new ProbeBackoff(1L,2L);
        StringBuilder reported=new StringBuilder();
        for(int i=1;i<=25;i++){r.onFailure(0L);if(r.shouldReport())reported.append(i).append(',');}
        check("1,10,20,".equals(reported.toString()),"report schedule: "+reported);
        r.onSuccess();
        r.onFailure(0L);
        check(r.shouldReport(),"a new streak reports its first failure again");

        // A tiny max does not break doubling.
        ProbeBackoff t=new ProbeBackoff(5L,5L);
        t.onFailure(10L);t.onFailure(10L);t.onFailure(10L);
        check(!t.due(14L)&&t.due(15L),"base==max stays at base");

        boolean threw=false;
        try{new ProbeBackoff(0L,10L);}catch(IllegalArgumentException e){threw=true;}
        check(threw,"base must be positive");
        threw=false;
        try{new ProbeBackoff(10L,5L);}catch(IllegalArgumentException e){threw=true;}
        check(threw,"max must be >= base");
        System.out.println("PROBE_BACKOFF_PASS");
    }
}
