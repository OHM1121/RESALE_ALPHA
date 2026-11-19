package com.artitrade.mobile;

import android.app.ActivityManager;
import android.app.ApplicationExitInfo;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Debug;
import android.os.Process;
import org.json.JSONObject;
import java.util.List;

/** Diagnoses the PREVIOUS real process death, including kills which never reach Java catch. */
final class ProcessHealthV087 {
    private static final String PREF="artitrade_process_health_v087";
    private static final Object LOCK=new Object();
    private static volatile boolean installed=false;
    private static long lastSampleWall=0L;
    private ProcessHealthV087(){}

    static void install(Context context){
        Context app=context.getApplicationContext();
        synchronized(LOCK){
            if(!installed){
                Thread.UncaughtExceptionHandler original=Thread.getDefaultUncaughtExceptionHandler();
                Thread.setDefaultUncaughtExceptionHandler((thread,error)->{
                    try{
                        new RuntimeCrashStore(app).add(
                            "UNCAUGHT_JAVA_"+thread.getName(),error);
                        sample(app,"uncaught",0L,0L,true);
                    }catch(Throwable ignored){}
                    if(original!=null)original.uncaughtException(thread,error);
                });
                installed=true;
            }
        }
        recordPreviousSystemExits(app);
        sample(app,"startup",0L,0L,true);
    }

    private static String exitReason(int reason){
        switch(reason){
            case ApplicationExitInfo.REASON_LOW_MEMORY:return "LOW_MEMORY";
            case ApplicationExitInfo.REASON_CRASH:return "JAVA_CRASH";
            case ApplicationExitInfo.REASON_CRASH_NATIVE:return "NATIVE_CRASH";
            case ApplicationExitInfo.REASON_ANR:return "ANR";
            case ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE:return "EXCESSIVE_RESOURCE_USAGE";
            case ApplicationExitInfo.REASON_INITIALIZATION_FAILURE:return "INITIALIZATION_FAILURE";
            case ApplicationExitInfo.REASON_SIGNALED:return "SIGNAL_OR_UNVERIFIED_LOW_MEMORY";
            default:return "OTHER_"+reason;
        }
    }

    static void recordPreviousSystemExits(Context context){
        if(Build.VERSION.SDK_INT<30)return;
        synchronized(LOCK){
            try{
                ActivityManager manager=(ActivityManager)context.getSystemService(Context.ACTIVITY_SERVICE);
                if(manager==null)return;
                SharedPreferences p=context.getSharedPreferences(PREF,Context.MODE_PRIVATE);
                long seen=p.getLong("last_checked_exit_ms",0L);
                long newest=seen;
                List<ApplicationExitInfo> events=manager.getHistoricalProcessExitReasons(
                    context.getPackageName(),0,12);
                if(events==null)return;
                RuntimeCrashStore store=new RuntimeCrashStore(context);
                for(int i=events.size()-1;i>=0;i--){
                    ApplicationExitInfo x=events.get(i);
                    if(x==null)continue;
                    long t=x.getTimestamp();
                    if(t<=seen||t>System.currentTimeMillis()+5000L)continue;
                    if(t>newest)newest=t;
                    int reason=x.getReason();
                    if(reason!=ApplicationExitInfo.REASON_LOW_MEMORY
                        &&reason!=ApplicationExitInfo.REASON_CRASH
                        &&reason!=ApplicationExitInfo.REASON_CRASH_NATIVE
                        &&reason!=ApplicationExitInfo.REASON_ANR
                        &&reason!=ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE
                        &&reason!=ApplicationExitInfo.REASON_INITIALIZATION_FAILURE
                        &&reason!=ApplicationExitInfo.REASON_SIGNALED)continue;
                    String prior=p.getString("last_memory_sample","");
                    String message="Android OS exit reason="+exitReason(reason)
                        +" reason_code="+reason+" status="+x.getStatus()
                        +" pid="+x.getPid()+" pss_kb="+x.getPss()
                        +" rss_kb="+x.getRss()+" process_at_ms="+t
                        +" prior_health="+(prior.length()>550?prior.substring(0,550):prior);
                    // SIGKILL (status 9) is NOT automatically proven to be OOM.
                    store.add("SYSTEM_PROCESS_EXIT_"+exitReason(reason),
                              new IllegalStateException(message));
                }
                if(newest>seen)p.edit().putLong("last_checked_exit_ms",newest).commit();
            }catch(Throwable e){
                try{new RuntimeCrashStore(context).add("process_exit_history",e);}
                catch(Throwable ignored){}
            }
        }
    }

    static void trim(Context c,String owner,int level){
        sample(c,"trim_"+owner+"_"+level,0L,0L,true);
    }

    static void sample(Context c,String stage,long snapshotChars,long workMillis,
                       boolean force){
        long now=System.currentTimeMillis();
        synchronized(LOCK){
            if(!force && now-lastSampleWall<45_000L)return;
            lastSampleWall=now;
        }
        try{
            Runtime runtime=Runtime.getRuntime();
            long javaUsed=runtime.totalMemory()-runtime.freeMemory();
            long javaMax=runtime.maxMemory();
            long nativeBytes=Debug.getNativeHeapAllocatedSize();
            ActivityManager manager=(ActivityManager)c.getSystemService(Context.ACTIVITY_SERVICE);
            ActivityManager.MemoryInfo avail=new ActivityManager.MemoryInfo();
            if(manager!=null)manager.getMemoryInfo(avail);
            int pss=0;
            try{
                if(manager!=null){
                    Debug.MemoryInfo[] records=manager.getProcessMemoryInfo(
                        new int[]{Process.myPid()});
                    if(records!=null&&records.length>0&&records[0]!=null)
                        pss=records[0].getTotalPss();
                }
            }catch(RuntimeException ignored){}
            JSONObject sample=new JSONObject()
                .put("at_ms",now).put("stage",stage)
                .put("java_heap_used_mb",javaUsed/(1024*1024))
                .put("java_heap_max_mb",javaMax/(1024*1024))
                .put("native_heap_allocated_mb",nativeBytes/(1024*1024))
                .put("process_pss_kb",pss)
                .put("device_available_mb",avail.availMem/(1024*1024))
                .put("device_memory_low",avail.lowMemory)
                .put("snapshot_characters",snapshotChars)
                .put("snapshot_work_ms",workMillis)
                .put("threads_estimate",Thread.activeCount());
            SharedPreferences p=c.getApplicationContext().getSharedPreferences(
                PREF,Context.MODE_PRIVATE);
            p.edit().putString("last_memory_sample",sample.toString()).apply();
            if(avail.lowMemory || (javaMax>0&&javaUsed*100/javaMax>85)){
                long last=p.getLong("last_memory_warning_ms",0L);
                if(now-last>600_000L){
                    p.edit().putLong("last_memory_warning_ms",now).apply();
                    new RuntimeCrashStore(c).add("MEMORY_PRESSURE_"+stage,
                        new IllegalStateException(sample.toString()));
                }
            }
        }catch(Throwable ignored){}
    }
}
