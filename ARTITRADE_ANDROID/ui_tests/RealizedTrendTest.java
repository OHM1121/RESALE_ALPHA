package com.artitrade.mobile;
import java.util.Arrays;
import java.util.Collections;

public final class RealizedTrendTest {
    static void ok(boolean pass,String what){if(!pass)throw new AssertionError(what);}
    public static void main(String[] args){
        long t1=RealizedTrend.time("2026-11-01 09:05:00");
        long t2=RealizedTrend.time("2026-11-03 09:05:00");
        ok(t1>0&&t2>t1,"KST dates sorted");
        ok(RealizedTrend.time("20261101090500")==t1,"compact YYYYMMDDHHMMSS");
        ok(RealizedTrend.time("2026-11-01T00:05:00Z")==t1,"UTC to KST");
        ok(RealizedTrend.time("invalid")==0,"missing timestamps not fabricated");
        RealizedTrend.Series s=RealizedTrend.cumulative(Arrays.asList(
            new RealizedTrend.Event(t2,-200),
            new RealizedTrend.Event(t1,100)
        ),100);
        ok(s.values.length==2&&s.values[0]==100&&s.values[1]==-100,"chronological realized PnL");
        ok(s.timestamps[0]==t1&&s.timestamps[1]==t2,"axis date alignment");
        RealizedTrend.Series last=RealizedTrend.cumulative(Arrays.asList(
            new RealizedTrend.Event(t1,100),
            new RealizedTrend.Event(t2,200),
            new RealizedTrend.Event(t2+1000,300)
        ),2);
        ok(last.values.length==2&&last.values[0]==300&&last.values[1]==600,
            "window preserves older cumulative sum");
        ok(RealizedTrend.cumulative(Collections.emptyList(),100).values.length==0,"empty");
        System.out.println("REALIZED_TREND_TEST_PASS");
    }
}
