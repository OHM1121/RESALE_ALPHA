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
    RuntimeCrashStore(Context c){prefs=c.getApplicationContext().getSharedPreferences(PREF,Context.MODE_PRIVATE);}

    synchronized void add(String stage,Throwable error){
        try{
            JSONArray old=new JSONArray(prefs.getString(KEY,"[]"));
            JSONArray out=new JSONArray();
            JSONObject row=new JSONObject()
                .put("at",System.currentTimeMillis())
                .put("stage",stage==null?"":stage)
                .put("type",error==null?"":error.getClass().getName())
                .put("message",safe(error==null?"":String.valueOf(error.getMessage()),600))
                .put("stack",safe(stack(error),2800));
            out.put(row);
            for(int i=0;i<old.length()&&out.length()<30;i++)out.put(old.opt(i));
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
