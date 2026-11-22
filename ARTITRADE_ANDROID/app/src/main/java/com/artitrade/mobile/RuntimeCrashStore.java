package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.PrintWriter;
import java.io.StringWriter;

/** Bounded local startup/runtime fault log. Never records API secrets. */
final class RuntimeCrashStore {
    private static final String PREF="artitrade_runtime_faults";
    private static final String KEY="items";
    private final SharedPreferences prefs;
    private final Context appContext;
    RuntimeCrashStore(Context c){
        appContext=c.getApplicationContext();
        prefs=appContext.getSharedPreferences(PREF,Context.MODE_PRIVATE);
    }
    private String appVersion(){
        try{return appContext.getPackageManager().getPackageInfo(
            appContext.getPackageName(),0).versionName;}
        catch(Exception e){return "UNKNOWN_INSTALLED_VERSION";}
    }

    /**
     * Identical faults (same stage, exception type and message) are merged into
     * ONE row that moves to the front and carries {@code count}, {@code first_at}
     * and the latest {@code at}/{@code stack}. A repeating benign fault can no
     * longer push every other diagnostic out of the bounded buffer. Existing
     * keys keep their meaning; {@code count}/{@code first_at} are additive and
     * rows written by older versions are treated as count=1.
     */
    synchronized void add(String stage,Throwable error){
        try{
            JSONArray old=new JSONArray(prefs.getString(KEY,"[]"));
            JSONArray out=FaultLogPolicy.merge(old,System.currentTimeMillis(),
                appVersion(),stage,
                error==null?"":error.getClass().getName(),
                safe(error==null?"":String.valueOf(error.getMessage()),600),
                safe(stack(error),stage!=null&&stage.startsWith("engine_hang")?9500:2800));
            prefs.edit().putString(KEY,out.toString()).apply();
        }catch(Exception ignored){}
    }
    synchronized JSONArray read(){
        try{return new JSONArray(prefs.getString(KEY,"[]"));}catch(Exception e){return new JSONArray();}
    }
    synchronized void clear(){prefs.edit().remove(KEY).apply();}
    private static String stack(Throwable e){
        if(e==null)return "";
        try{StringWriter s=new StringWriter();e.printStackTrace(new PrintWriter(s));return s.toString();}
        catch(Exception ignored){return e.toString();}
    }
    private static String safe(String s,int max){
        if(s==null)return "";s=s.replaceAll("(?i)(appkey|secretkey|access[_ -]?key|authorization|bearer)\\s*[:=]\\s*[^\\s,}]+","$1=[REDACTED]");
        return s.length()>max?s.substring(0,max):s;
    }
}
