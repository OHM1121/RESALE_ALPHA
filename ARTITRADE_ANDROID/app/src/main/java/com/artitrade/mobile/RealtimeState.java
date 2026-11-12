package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;

/** Small local runtime snapshot. No API secret is written here. */
final class RealtimeState {
    private final SharedPreferences prefs;
    RealtimeState(Context c){prefs=c.getApplicationContext().getSharedPreferences("artitrade_realtime_state",Context.MODE_PRIVATE);}
    synchronized void set(String market,boolean connected,int subscribed,long lastFrame,String detail){
        try{
            JSONObject o=new JSONObject();
            o.put("connected",connected);o.put("subscribed",subscribed);o.put("last_frame",lastFrame);
            o.put("detail",detail==null?"":detail);o.put("updated_at",System.currentTimeMillis());
            prefs.edit().putString(market,o.toString()).apply();
        }catch(Exception ignored){}
    }
    synchronized JSONObject get(String market){
        try{return new JSONObject(prefs.getString(market,"{}"));}catch(Exception e){return new JSONObject();}
    }
    synchronized JSONObject all(){
        try{return new JSONObject().put("stock",get("stock")).put("coin",get("coin"));}catch(Exception e){return new JSONObject();}
    }
}
