package com.artitrade.mobile;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Identity and merge rule for the bounded runtime fault ring buffer. It has no
 * Android Context dependency (org.json only) so it runs in a plain JVM test.
 *
 * Root cause it removes: {@link RuntimeCrashStore} keeps only
 * {@link #MAX_ROWS} rows and used to insert one row per call. A benign fault
 * that repeats on a timer (for example a monitor that cannot reach an optional
 * local server every 2.5 s) therefore evicted every older, useful diagnostic
 * within about {@code MAX_ROWS * 2.5 s}. Two faults are "the same" only when
 * stage, exception type and the (bounded) message are identical, so distinct
 * failures are never merged and a merged row keeps an exact occurrence count.
 */
final class FaultLogPolicy {
    static final int MAX_ROWS=30;
    /** Characters of the message that take part in the identity. */
    static final int IDENTITY_MESSAGE_CHARS=200;
    private FaultLogPolicy(){}

    static String signature(String stage,String type,String message){
        String m=message==null?"":message;
        if(m.length()>IDENTITY_MESSAGE_CHARS)m=m.substring(0,IDENTITY_MESSAGE_CHARS);
        // U+001F cannot appear in a stage/type, so fields cannot bleed together.
        return (stage==null?"":stage)+"\u001f"+(type==null?"":type)+"\u001f"+m;
    }

    /**
     * Returns the new buffer, newest first. An identical earlier row is removed
     * and re-created at the front with {@code count+1}, the original
     * {@code first_at}, and the latest {@code at}/{@code stack}/{@code
     * app_version}. Rows written by older versions (no {@code count}) count 1.
     * Existing keys keep their meaning; {@code count}/{@code first_at} are
     * additive.
     */
    static JSONArray merge(JSONArray old,long nowMs,String appVersion,
                           String stage,String type,String message,String stack)
            throws JSONException{
        String safeStage=stage==null?"":stage;
        String safeType=type==null?"":type;
        String safeMessage=message==null?"":message;
        String signature=signature(safeStage,safeType,safeMessage);
        JSONArray source=old==null?new JSONArray():old;
        int same=-1;
        for(int i=0;i<source.length();i++){
            JSONObject candidate=source.optJSONObject(i);
            if(candidate==null)continue;
            if(signature.equals(signature(candidate.optString("stage",""),
                    candidate.optString("type",""),
                    candidate.optString("message","")))){same=i;break;}
        }
        int count=1;
        long firstAt=nowMs;
        if(same>=0){
            JSONObject previous=source.getJSONObject(same);
            count=Math.max(1,previous.optInt("count",1));
            if(count<Integer.MAX_VALUE)count++;
            firstAt=previous.optLong("first_at",previous.optLong("at",nowMs));
        }
        JSONObject row=new JSONObject()
            .put("at",nowMs)
            .put("first_at",firstAt)
            .put("count",count)
            .put("app_version",appVersion==null?"":appVersion)
            .put("stage",safeStage)
            .put("type",safeType)
            .put("message",safeMessage)
            .put("stack",stack==null?"":stack);
        JSONArray out=new JSONArray();
        out.put(row);
        for(int i=0;i<source.length()&&out.length()<MAX_ROWS;i++){
            if(i==same)continue;
            out.put(source.opt(i));
        }
        return out;
    }
}
