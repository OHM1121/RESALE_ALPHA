package com.artitrade.mobile;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Pure JVM regression (needs org.json on the class path).
 * Boundary that failed: a timer-driven benign fault filled the 30-row runtime
 * fault buffer and evicted every older diagnostic.
 */
public final class FaultLogPolicyTest {
    static void check(boolean v,String msg){if(!v)throw new AssertionError(msg);}

    static JSONArray add(JSONArray buf,long at,String stage,String type,String msg)throws Exception{
        return FaultLogPolicy.merge(buf,at,"0.0.18",stage,type,msg,"stack@"+at);
    }

    public static void main(String[] args)throws Exception{
        // Replay of the observed defect: 10 useful diagnostics, then the
        // optional-server monitor failing every 2.5 s for ~10 minutes.
        JSONArray buf=new JSONArray();
        for(int i=0;i<10;i++)buf=add(buf,1000L+i,"useful_"+i,"java.lang.IllegalStateException","cause "+i);
        for(int tick=0;tick<240;tick++)
            buf=add(buf,100_000L+tick*2500L,"mserver_monitor","java.net.ConnectException",
                "Failed to connect to /127.0.0.1:8765");
        check(buf.length()==11,"240 identical faults must be ONE row next to the 10 useful rows, got "+buf.length());
        JSONObject top=buf.getJSONObject(0);
        check("mserver_monitor".equals(top.getString("stage")),"merged row moves to the front");
        check(top.getInt("count")==240,"exact occurrence count kept: "+top.getInt("count"));
        check(top.getLong("first_at")==100_000L,"first_at is the first occurrence");
        check(top.getLong("at")==100_000L+239*2500L,"at is the latest occurrence");
        check(("stack@"+(100_000L+239*2500L)).equals(top.getString("stack")),"latest stack kept");
        boolean allUseful=true;
        for(int i=0;i<10;i++){
            boolean found=false;
            for(int j=0;j<buf.length();j++)
                if(("useful_"+i).equals(buf.getJSONObject(j).getString("stage")))found=true;
            allUseful&=found;
        }
        check(allUseful,"older diagnostics must survive a repeating fault");

        // Different message / type / stage are DIFFERENT faults.
        JSONArray d=new JSONArray();
        d=add(d,1,"s","T","m");
        d=add(d,2,"s","T","m2");
        d=add(d,3,"s","T2","m");
        d=add(d,4,"s2","T","m");
        check(d.length()==4,"distinct faults are never merged: "+d.length());
        // Alternating A,B,A,B must also merge to two rows (not only adjacent repeats).
        JSONArray alt=new JSONArray();
        for(int i=0;i<20;i++)alt=add(alt,i,"s","T",(i%2==0)?"A":"B");
        check(alt.length()==2,"alternating faults merge by identity: "+alt.length());
        check(alt.getJSONObject(0).getInt("count")==10&&alt.getJSONObject(1).getInt("count")==10,"counts 10/10");

        // Fields cannot bleed: ("a","b|c") != ("a|b","c") style collisions.
        check(!FaultLogPolicy.signature("ab","c","m").equals(FaultLogPolicy.signature("a","bc","m")),"stage/type boundary");
        check(!FaultLogPolicy.signature("a","b","cm").equals(FaultLogPolicy.signature("a","bc","m")),"type/message boundary");

        // Legacy rows (written by <=0.0.18, no count/first_at) are counted as 1 and upgraded.
        JSONArray legacy=new JSONArray().put(new JSONObject().put("at",5L).put("app_version","0.0.17")
            .put("stage","s").put("type","T").put("message","m").put("stack","old"));
        JSONArray upgraded=add(legacy,9,"s","T","m");
        check(upgraded.length()==1,"legacy row merged");
        check(upgraded.getJSONObject(0).getInt("count")==2,"legacy row counted as 1 then +1");
        check(upgraded.getJSONObject(0).getLong("first_at")==5L,"legacy first_at falls back to its at");

        // Every row records the app's own version (merged rows take the newest).
        check("0.0.18".equals(top.getString("app_version")),"row records app_version");
        JSONArray upg=FaultLogPolicy.merge(legacy,11L,"0.0.19","s","T","m","x");
        check("0.0.19".equals(upg.getJSONObject(0).getString("app_version")),"merged row takes current app_version");

        // Existing schema keys are all still present.
        for(String key:new String[]{"at","app_version","stage","type","message","stack"})
            check(top.has(key),"schema key kept: "+key);

        // The bound still holds for many DISTINCT faults, newest first.
        JSONArray many=new JSONArray();
        for(int i=0;i<100;i++)many=add(many,i,"stage"+i,"T","m");
        check(many.length()==FaultLogPolicy.MAX_ROWS,"bounded to MAX_ROWS");
        check("stage99".equals(many.getJSONObject(0).getString("stage")),"newest first");

        // Null safety (RuntimeCrashStore.add(stage,null) is a real call shape).
        JSONArray nulls=FaultLogPolicy.merge(null,7L,null,null,null,null,null);
        check(nulls.length()==1&&nulls.getJSONObject(0).getInt("count")==1,"null inputs tolerated");
        System.out.println("FAULT_LOG_POLICY_PASS");
    }
}
