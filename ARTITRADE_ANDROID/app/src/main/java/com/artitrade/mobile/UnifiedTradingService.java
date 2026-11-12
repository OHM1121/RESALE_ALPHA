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
    static final String APPLY_OPERATION="com.artitrade.mobile.APPLY_OPERATION";
    private static final String CHANNEL="artitrade_phone_main_v070";
    private static final int NOTIFY_ID=5701;
    private final AtomicBoolean running=new AtomicBoolean(false);
    private Thread monitor;
    private PowerManager.WakeLock wakeLock;
    private long stockEpoch=0,coinEpoch=0;
    private String clientId;
    private StrategyRuntimeBridge strategy;
    private RuntimeCrashStore crashes;
    private long lastHubPublish=0L;

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
                final String requestedMarket=market;
                final boolean requestedEnabled=enabled;
                new Thread(()->applyLiveMode(requestedMarket,requestedEnabled),"artitrade-v070-live-arm").start();
            }
            if(intent!=null&&RESTART_FEEDS.equals(intent.getAction())){
                new Thread(()->safeRestartStrategy(),"artitrade-v074-restart").start();
            }
            if(intent!=null&&APPLY_OPERATION.equals(intent.getAction())){
                final String raw=intent.getStringExtra("settings");
                if(raw!=null)getSharedPreferences("artitrade_operation_pending",MODE_PRIVATE).edit().putString("settings",raw).apply();
                new Thread(()->applyPendingOperation(),"artitrade-v074-operation").start();
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
            applyPendingOperation();
        }catch(Throwable e){crashes.add("strategy_start_thread",e);}
    }
    private void applyPendingOperation(){
        try{
            String raw=getSharedPreferences("artitrade_operation_pending",MODE_PRIVATE).getString("settings","");
            if(raw.isEmpty()||strategy==null||!strategy.isStarted())return;
            JSONObject result=strategy.applyOperation(new JSONObject(raw));
            getSharedPreferences("artitrade_operation_result",MODE_PRIVATE).edit()
                .putString("result",result.toString()).putLong("at",System.currentTimeMillis()).apply();
            if(result.optBoolean("ok",false)){
                getSharedPreferences("artitrade_operation_pending",MODE_PRIVATE).edit().remove("settings").apply();
            }else crashes.add("operation_apply_reply",new IllegalStateException(result.toString()));
        }catch(Throwable e){crashes.add("operation_apply_pending",e);}
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
                try{p.put("operation",strategy.operationSnapshot());}catch(Throwable ignored){}
                getSharedPreferences("artitrade_runtime_snapshot",MODE_PRIVATE).edit()
                    .putString("snapshot",p.toString()).apply();
                JSONObject st=p.optJSONObject("stock"),co=p.optJSONObject("coin");
                boolean sc=st!=null&&st.optBoolean("connected");
                boolean cc=co!=null&&co.optBoolean("connected");
                live.set("stock",sc,sc?200:0,System.currentTimeMillis(),st==null?"전략엔진 준비":st.optString("status"));
                live.set("coin",cc,cc?35:0,System.currentTimeMillis(),co==null?"전략엔진 준비":co.optString("status"));
                JSONObject enabled=p.optJSONObject("live_enabled");
                // Python fail-closed order reconciliation can disarm a market internally.
                // Mirror that state into Android prefs so UI/notification/next boot cannot re-arm it silently.
                if(enabled!=null){
                    if(strategy.liveEnabled("stock")&&!enabled.optBoolean("stock",false))strategy.setLive("stock",false);
                    if(strategy.liveEnabled("coin")&&!enabled.optBoolean("coin",false))strategy.setLive("coin",false);
                }
                String orders=(enabled!=null&&enabled.optBoolean("stock")?"S":"-")+(enabled!=null&&enabled.optBoolean("coin")?"C":"-");
                NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
                if(nm!=null)nm.notify(NOTIFY_ID,notification("주식 "+(sc?"LIVE":"—")+" · 코인 "+(cc?"LIVE":"—")+" · 실주문 "+orders));
                long now=System.currentTimeMillis();
                if(now-lastHubPublish>=4500){
                    publishPhoneLedgers(hub,strategy.hubRecords());
                    lastHubPublish=now;
                }
                if(strategy.liveEnabled("stock")&&stockEpoch<=0)strategy.setLive("stock",false);
                if(strategy.liveEnabled("coin")&&coinEpoch<=0)strategy.setLive("coin",false);
            }catch(Throwable e){crashes.add("strategy_monitor",e);}
            try{Thread.sleep(2500);}catch(InterruptedException e){Thread.currentThread().interrupt();break;}
        }
        try{releaseQuietly(hub,"stock",stockEpoch);releaseQuietly(hub,"coin",coinEpoch);}catch(Throwable ignored){}
    }

    private void applyLiveMode(String market,boolean enabled){
        if(!("stock".equals(market)||"coin".equals(market)))return;
        LocalHubClient hub=new LocalHubClient(this);
        try{
            if(!enabled){
                strategy.setLive(market,false);
                return;
            }
            BrokerSecretStore secrets=new BrokerSecretStore(this);
            if("stock".equals(market)&&!secrets.hasKiwoom())throw new IllegalStateException("KIWOOM_KEYS_MISSING");
            if("coin".equals(market)&&!secrets.hasUpbit())throw new IllegalStateException("UPBIT_KEYS_MISSING");
            JSONObject preferred=hub.control(market,"phone");
            if(!preferred.optBoolean("ok"))throw new IllegalStateException("PHONE_EXECUTOR_NOT_SELECTED");
            JSONObject claim=hub.claim(market,clientId);
            if(!claim.optBoolean("ok")&&!claim.optBoolean("granted"))throw new IllegalStateException("PHONE_LEASE_DENIED:"+claim.optString("error"));
            long epoch=claim.optLong("epoch",0);
            if("stock".equals(market))stockEpoch=epoch;else coinEpoch=epoch;
            strategy.setLive(market,true);
        }catch(Throwable e){
            strategy.setLive(market,false);
            crashes.add("live_arm_"+market,e);
        }
    }

    private void publishPhoneLedgers(LocalHubClient hub,JSONObject all){
        if(all==null||all.length()==0)return;
        try{
            for(String market:new String[]{"stock","coin"}){
                JSONObject m=all.optJSONObject(market);if(m==null)continue;
                String[] strategies="stock".equals(market)?new String[]{"shared","at","cw"}:new String[]{"shared","general","ma"};
                for(String strategyName:strategies){
                    JSONObject s=m.optJSONObject(strategyName);if(s==null)continue;
                    for(String bucket:new String[]{"ledger","research","errors"}){
                        JSONObject data=s.optJSONObject(bucket);if(data==null)continue;
                        try{
                            JSONObject result=hub.putRecord(market,strategyName,bucket,data);
                            if(!result.optBoolean("ok"))crashes.add("hub_publish_"+market+"_"+strategyName+"_"+bucket,
                                new IllegalStateException(result.optString("error","HUB_WRITE_FAILED")));
                        }catch(Throwable e){crashes.add("hub_publish_"+market+"_"+strategyName+"_"+bucket,e);}
                    }
                }
            }
        }catch(Throwable e){crashes.add("hub_publish",e);}
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
        return b.setContentTitle("ARTITRADE S21+ 메인 엔진 v0.7.4")
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
