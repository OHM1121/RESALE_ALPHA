package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;
import java.io.File;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/** Runs the latest extracted PC Python strategy state machines inside Android. */
final class StrategyRuntimeBridge {
    private final Context context;
    private final RuntimeCrashStore crashes;
    private PyObject module;
    private boolean started=false;

    StrategyRuntimeBridge(Context c){
        context=c.getApplicationContext();
        crashes=new RuntimeCrashStore(context);
    }
    private SharedPreferences prefs(){
        return context.getSharedPreferences("unified_live_orders",Context.MODE_PRIVATE);
    }
    boolean liveEnabled(String market){return prefs().getBoolean("live_"+market,false);}
    synchronized JSONObject start(){
        try{
            if(!Python.isStarted())Python.start(new AndroidPlatform(context));
            Python py=Python.getInstance();
            module=py.getModule("android_live_bridge");
            File root=new File(context.getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");
            if(!root.exists()&&!root.mkdirs())throw new IllegalStateException("strategy runtime directory unavailable");
            BrokerSecretStore s=new BrokerSecretStore(context);
            PyObject result=module.callAttr("start",root.getAbsolutePath(),
                s.kiwoomKey(),s.kiwoomSecret(),s.upbitAccess(),s.upbitSecret(),
                liveEnabled("stock"),liveEnabled("coin"));
            started=true;
            return parse(result);
        }catch(Throwable e){
            crashes.add("python_strategy_start",e);started=false;
            return error("PYTHON_STRATEGY_START",e);
        }
    }
    synchronized JSONObject restart(){
        try{stop();}catch(Throwable ignored){}
        return start();
    }
    synchronized JSONObject snapshot(){
        if(module==null)return new JSONObject();
        try{return parse(module.callAttr("snapshot"));}
        catch(Throwable e){crashes.add("python_strategy_snapshot",e);return error("PYTHON_SNAPSHOT",e);}
    }
    synchronized JSONObject setLive(String market,boolean enabled){
        if(!("stock".equals(market)||"coin".equals(market)))return new JSONObject();
        prefs().edit().putBoolean("live_"+market,enabled).commit();
        try{
            if(module!=null)return parse(module.callAttr("set_live",market,enabled));
        }catch(Throwable e){crashes.add("python_set_live_"+market,e);}
        return new JSONObject();
    }
    synchronized void stop(){
        if(module!=null){
            try{module.callAttr("stop");}catch(Throwable e){crashes.add("python_strategy_stop",e);}
        }
        started=false;
    }
    boolean isStarted(){return started;}
    private static JSONObject parse(PyObject p){
        try{return new JSONObject(p==null?"{}":p.toString());}
        catch(Exception e){return new JSONObject();}
    }
    private static JSONObject error(String code,Throwable e){
        try{return new JSONObject().put("ok",false).put("error",code)
            .put("type",e==null?"":e.getClass().getSimpleName())
            .put("message",e==null?"":String.valueOf(e.getMessage()));}
        catch(Exception ignored){return new JSONObject();}
    }
}
