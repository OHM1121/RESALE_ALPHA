package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/** Runs the exact latest uploaded EXE Python strategy state machines inside Android. */
final class StrategyRuntimeBridge {
    private static final int PARTS=22;
    private static final String CORE_SHA256="d72c514e746085158e3e27608f54b49c1cd8e62dd8fa4e0637b5692ca86a872b";
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
            File source=prepareCore();
            Python py=Python.getInstance();
            PyObject sys=py.getModule("sys");
            sys.get("path").callAttr("insert",0,source.getAbsolutePath());
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

    private File prepareCore() throws Exception{
        File dir=new File(context.getFilesDir(),"strategy_v070_core");
        File marker=new File(dir,".core_sha256");
        File main=new File(dir,"android_live_bridge.py");
        if(marker.isFile()&&main.isFile()){
            try(InputStream in=new java.io.FileInputStream(marker)){
                byte[] b=new byte[(int)marker.length()];
                int n=in.read(b);
                if(n>0&&CORE_SHA256.equals(new String(b,0,n,StandardCharsets.US_ASCII).trim()))return dir;
            }catch(Exception ignored){}
        }
        deleteTree(dir);
        if(!dir.mkdirs()&&!dir.isDirectory())throw new IllegalStateException("strategy core directory unavailable");

        ByteArrayOutputStream encoded=new ByteArrayOutputStream(640000);
        byte[] buf=new byte[8192];
        for(int i=0;i<PARTS;i++){
            String name=String.format(java.util.Locale.ROOT,"strategy_v070/part_%02d.txt",i);
            try(InputStream in=context.getAssets().open(name)){
                int n;while((n=in.read(buf))!=-1)encoded.write(buf,0,n);
            }
        }
        byte[] zip=Base64.decode(encoded.toByteArray(),Base64.DEFAULT);
        String digest=sha256(zip);
        if(!CORE_SHA256.equals(digest))throw new SecurityException("strategy core digest mismatch");

        try(ZipInputStream zin=new ZipInputStream(new java.io.ByteArrayInputStream(zip))){
            ZipEntry e;
            String root=dir.getCanonicalPath()+File.separator;
            while((e=zin.getNextEntry())!=null){
                File out=new File(dir,e.getName());
                String canonical=out.getCanonicalPath();
                if(!canonical.startsWith(root))throw new SecurityException("invalid strategy zip path");
                if(e.isDirectory()){
                    if(!out.mkdirs()&&!out.isDirectory())throw new IllegalStateException("cannot create strategy dir");
                }else{
                    File parent=out.getParentFile();
                    if(parent!=null&&!parent.exists()&&!parent.mkdirs())throw new IllegalStateException("cannot create strategy parent");
                    try(FileOutputStream fos=new FileOutputStream(out)){
                        int n;while((n=zin.read(buf))!=-1)fos.write(buf,0,n);
                    }
                }
                zin.closeEntry();
            }
        }
        try(FileOutputStream out=new FileOutputStream(marker)){
            out.write(CORE_SHA256.getBytes(StandardCharsets.US_ASCII));
        }
        if(!main.isFile())throw new IllegalStateException("strategy bridge missing after extraction");
        return dir;
    }

    private static void deleteTree(File f){
        if(f==null||!f.exists())return;
        if(f.isDirectory()){
            File[] children=f.listFiles();
            if(children!=null)for(File c:children)deleteTree(c);
        }
        if(!f.delete()&&f.exists())throw new IllegalStateException("cannot replace strategy core");
    }
    private static String sha256(byte[] data)throws Exception{
        MessageDigest md=MessageDigest.getInstance("SHA-256");
        byte[] d=md.digest(data);
        StringBuilder s=new StringBuilder(64);
        for(byte b:d)s.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
        return s.toString();
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
    synchronized JSONObject hubRecords(){
        if(module==null)return new JSONObject();
        try{return parse(module.callAttr("hub_records"));}
        catch(Throwable e){crashes.add("python_hub_records",e);return new JSONObject();}
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
    JSONArray diagnostics(){return crashes.read();}

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
