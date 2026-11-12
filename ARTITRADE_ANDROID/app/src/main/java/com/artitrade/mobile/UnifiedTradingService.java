package com.artitrade.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import org.json.JSONObject;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * S21+ phone-main runtime.
 * Latest EXE Python state machines own Kiwoom/Upbit market sessions and strategy
 * decisions. Android owns foreground lifetime, secrets, arming and monitoring.
 */
public final class UnifiedTradingService extends Service {
    static final String START="com.artitrade.mobile.UNIFIED_START";
    static final String STOP="com.artitrade.mobile.UNIFIED_STOP";
    static final String RESTART_FEEDS="com.artitrade.mobile.RESTART_FEEDS";
    static final String SET_LIVE="com.artitrade.mobile.SET_LIVE";
    private static final String CHANNEL="artitrade_phone_main_v070";
    private static final int NOTIFY_ID=5701;
    private final AtomicBoolean running=new AtomicBoolean(false);
    private Thread monitor;
    private PowerManager.WakeLock wakeLock;
    private long stockEpoch=0,coinEpoch=0;
    private String clientId;
    private StrategyRuntimeBridge strategy;
    private RuntimeCrashStore crashes;

    @Override public void onCreate(){
        super.onCreate();
        crashes=new RuntimeCrashStore(this);
        strategy=new StrategyRuntimeBridge(this);
        clientId=getSharedPreferences("unified_runtime",MODE_PRIVATE).getString("client_id","");
        if(clientId.isEmpty()){
            clientId="phone-"+UUID.randomUUID().toString().replace("-","").substring(0,20);
            getSharedPreferences("unified_runtime",MODE_PRIVATE).edit().putString("client_id",clientId).apply();
        }
        PowerManager pm=(PowerManager)getSystemService(Context.POWER_SERVICE);
        wakeLock=pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"ARTITRADE:PhoneMainV070");
    }

    @Override public int onStartCommand(Intent intent,int flags,int startId){
        try{
            if(intent!=null&&STOP.equals(intent.getAction())){
                stopSafely();stopSelf();return START_NOT_STICKY;
            }
            ensureChannel();
            startForeground(NOTIFY_ID,notification("안전 시작 · 최신 EXE 전략엔진 준비"));
            if(intent!=null&&SET_LIVE.equals(intent.getAction())){
                String market=intent.getStringExtra("market");
                boolean enabled=intent.getBooleanExtra("enabled",false);
                strategy.setLive(market,enabled);
            }
            if(intent!=null&&RESTART_FEEDS.equals(intent.getAction())){
                new Thread(()->safeRestartStrategy(),"artitrade-v070-restart").start();
            }
            if(running.compareAndSet(false,true)){
                if(wakeLock!=null&&!wakeLock.isHeld())wakeLock.acquire();
                // Never initialize broker/network work on Android's main thread.
                new Thread(()->safeStartStrategy(),"artitrade-v070-python-start").start();
                monitor=new Thread(this::monitorLoop,"artitrade-v070-monitor");
                monitor.start();
            }
            return START_STICKY;
        }catch(Throwable e){
            crashes.add("service_onStartCommand",e);
            // Keep service process alive in diagnostic mode rather than crashing launcher.
            try{ensureChannel();startForeground(NOTIFY_ID,notification("엔진 시작 오류 · 앱 오류 탭 확인"));}catch(Throwable ignored){}
            return START_STICKY;
        }
    }

    private void safeStartStrategy(){
        try{
            JSONObject s=strategy.start();
            if(!s.optBoolean("ok",true))crashes.add("strategy_start_reply",new IllegalStateException(s.toString()));
        }catch(Throwable e){crashes.add("strategy_start_thread",e);}
    }
    private void safeRestartStrategy(){
        try{strategy.restart();}catch(Throwable e){crashes.add("strategy_restart",e);}
    }

    private void monitorLoop(){
        LocalHubClient hub=new LocalHubClient(this);
        RealtimeState live=new RealtimeState(this);
        while(running.get()){
            try{
                JSONObject hs=hub.status();
                stockEpoch=maintain(hub,"stock",hs.optJSONObject("stock"),stockEpoch);
                coinEpoch=maintain(hub,"coin",hs.optJSONObject("coin"),coinEpoch);
            }catch(Throwable e){
                // Phone Hub failure must never stop market data or order state machine.
                crashes.add("hub_monitor",e);
            }
            try{
                JSONObject p=strategy.snapshot();
                JSONObject st=p.optJSONObject("stock"),co=p.optJSONObject("coin");
                boolean sc=st!=null&&st.optBoolean("connected");
                boolean cc=co!=null&&co.optBoolean("connected");
                live.set("stock",sc,sc?200:0,System.currentTimeMillis(),st==null?"전략엔진 준비":st.optString("status"));
                live.set("coin",cc,cc?35:0,System.currentTimeMillis(),co==null?"전략엔진 준비":co.optString("status"));
                JSONObject enabled=p.optJSONObject("live_enabled");
                String orders=(enabled!=null&&enabled.optBoolean("stock")?"S":"-")+(enabled!=null&&enabled.optBoolean("coin")?"C":"-");
                NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
                if(nm!=null)nm.notify(NOTIFY_ID,notification("주식 "+(sc?"LIVE":"—")+" · 코인 "+(cc?"LIVE":"—")+" · 실주문 "+orders));
            }catch(Throwable e){crashes.add("strategy_monitor",e);}
            try{Thread.sleep(2500);}catch(InterruptedException e){Thread.currentThread().interrupt();break;}
        }
        try{releaseQuietly(hub,"stock",stockEpoch);releaseQuietly(hub,"coin",coinEpoch);}catch(Throwable ignored){}
    }

    private long maintain(LocalHubClient hub,String market,JSONObject status,long epoch){
        if(status==null||!status.optBoolean("ok"))return epoch;
        // S21+ is now the main runtime. Phone is the only supported automatic executor.
        String desired=status.optString("desired","off");
        if(!"phone".equals(desired)){
            if(epoch>0)releaseQuietly(hub,market,epoch);
            return 0;
        }
        try{
            if(epoch>0){
                JSONObject r=hub.renew(market,clientId,epoch);
                if(r.optBoolean("ok"))return r.optLong("epoch",epoch);
                epoch=0;
            }
            JSONObject c=hub.claim(market,clientId);
            return c.optBoolean("ok")?c.optLong("epoch",0):0;
        }catch(Exception e){return epoch;}
    }
    private void releaseQuietly(LocalHubClient hub,String market,long epoch){
        if(epoch<=0)return;try{hub.release(market,clientId,epoch);}catch(Exception ignored){}
    }

    private Notification notification(String text){
        Notification.Builder b=Build.VERSION.SDK_INT>=26?new Notification.Builder(this,CHANNEL):new Notification.Builder(this);
        return b.setContentTitle("ARTITRADE S21+ 메인 엔진 v0.7")
            .setContentText(text).setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true).setCategory(Notification.CATEGORY_SERVICE).build();
    }
    private void ensureChannel(){
        if(Build.VERSION.SDK_INT>=26){
            NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
            if(nm!=null){
                NotificationChannel c=new NotificationChannel(CHANNEL,"ARTITRADE S21+ 메인 엔진",NotificationManager.IMPORTANCE_LOW);
                c.setDescription("최신 EXE 전략·키움·업비트 실시간·실주문 엔진");
                nm.createNotificationChannel(c);
            }
        }
    }
    private void stopSafely(){
        running.set(false);
        try{if(strategy!=null)strategy.stop();}catch(Throwable e){if(crashes!=null)crashes.add("service_stop_strategy",e);}
        if(monitor!=null)monitor.interrupt();
        if(wakeLock!=null&&wakeLock.isHeld())wakeLock.release();
    }
    @Override public void onDestroy(){stopSafely();super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
