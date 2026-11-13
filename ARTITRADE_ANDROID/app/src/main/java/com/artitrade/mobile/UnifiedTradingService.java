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
import org.json.JSONArray;
import org.json.JSONObject;

import java.util.UUID;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
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
    static final String RETRY_SYNC="com.artitrade.mobile.RETRY_MSERVER_SYNC";
    static final String APPLY_PC_MIGRATION="com.artitrade.mobile.APPLY_PC_MIGRATION";
    private static final String CHANNEL="artitrade_phone_main_v070";
    private static final int NOTIFY_ID=5701;
    private final AtomicBoolean running=new AtomicBoolean(false);
    private final AtomicBoolean migrationRestarting=new AtomicBoolean(false);
    private volatile long lastMigrationPoll=0L;
    private volatile String lastAutomaticMigrationSha="";
    private Thread monitor;
    private PowerManager.WakeLock wakeLock;
    private long stockEpoch=0,coinEpoch=0;
    private String clientId;
    private StrategyRuntimeBridge strategy;
    private RuntimeCrashStore crashes;
    private volatile long lastHubPublish=0L;
    private volatile boolean lastMserverAuthOk=false;

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
            if(intent!=null&&RETRY_SYNC.equals(intent.getAction())){
                lastHubPublish=0L; // next monitor pass republishes; never runs a second concurrent writer
                mserverSyncProgress("RETRY","원장 다시 게시 요청",0,0,0,System.currentTimeMillis());
            }
            if(intent!=null&&RESTART_FEEDS.equals(intent.getAction())){
                new Thread(()->safeRestartStrategy(),"artitrade-v074-restart").start();
            }
            if(intent!=null&&APPLY_PC_MIGRATION.equals(intent.getAction())&&running.get()){
                new Thread(()->{
                    if(liveOrdersArmed()){
                        getSharedPreferences("artitrade_pc_ledger_import_result",MODE_PRIVATE).edit()
                            .putString("result",new JSONObject().toString()).apply();
                        crashes.add("pc_migration_live_blocked",
                            new IllegalStateException("LIVE_TRADING_ACTIVE: disable live orders before PC DB restore"));
                        return;
                    }
                    safeRestartStrategy();
                },"artitrade-pc-ledger-apply").start();
            }
            if(intent!=null&&START.equals(intent.getAction())&&running.get()){
                new Thread(()->checkPendingPCArchive(new LocalHubClient(this)),
                    "artitrade-pc-ledger-app-reopen").start();
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

    private boolean liveOrdersArmed(){
        return strategy.liveEnabled("stock")||strategy.liveEnabled("coin");
    }
    private void saveMigrationResult(JSONObject result){
        getSharedPreferences("artitrade_pc_ledger_import_result",MODE_PRIVATE).edit()
            .putString("result",result.toString()).putLong("at",System.currentTimeMillis()).apply();
        if(!result.optBoolean("ok",true)&&!"PHONE_HAS_REAL_FILLS".equals(result.optString("status")))
            crashes.add("pc_ledger_migration_reply",new IllegalStateException(result.toString()));
    }
    private void launchWithMigration(){
        LegacyLedgerMigration importer=new LegacyLedgerMigration(this,crashes);
        JSONObject migration=importer.importIfReady();
        JSONObject started=strategy.start();
        if(!started.optBoolean("ok",true))
            crashes.add("strategy_start_reply",new IllegalStateException(started.toString()));
        JSONObject records=strategy.hubRecords();
        JSONObject verified=importer.finishRuntimeVerification(migration,started,records);
        saveMigrationResult(verified);
        applyPendingOperation();
    }
    private void safeStartStrategy(){
        if(!migrationRestarting.compareAndSet(false,true))return;
        try{launchWithMigration();}
        catch(Throwable e){crashes.add("strategy_start_thread",e);}
        finally{migrationRestarting.set(false);}
    }
    private void checkPendingPCArchive(LocalHubClient hub){
        if(!strategy.isStarted()||migrationRestarting.get())return;
        long now=System.currentTimeMillis();
        if(now-lastMigrationPoll<15000L)return;
        lastMigrationPoll=now;
        try{
            String installed=getSharedPreferences("artitrade_pc_ledger_import_v1",MODE_PRIVATE)
                .getString("imported_sha256","");
            // Once a verified PC DB is active, all later paper and broker records
            // belong to this phone. Never auto-import a different PC snapshot.
            if(!installed.isEmpty())return;
            JSONObject st=hub.migrationStatus();
            if(!st.optBoolean("ok",false)||!st.optBoolean("ready",false)
                ||!st.optBoolean("archive_exists",false))return;
            String sha=st.optString("archive_sha256","");
            if(sha.isEmpty()||sha.equalsIgnoreCase(lastAutomaticMigrationSha)||liveOrdersArmed())return;
            lastAutomaticMigrationSha=sha;
            new Thread(this::safeRestartStrategy,"artitrade-pc-ledger-auto-apply").start();
        }catch(Throwable e){crashes.add("pc_ledger_auto_check",e);}
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
        if(!migrationRestarting.compareAndSet(false,true))return;
        try{
            strategy.stop();
            // Allow queued CLOSE/MA/coin database writers to release their handles.
            try{Thread.sleep(450L);}catch(InterruptedException e){Thread.currentThread().interrupt();}
            launchWithMigration();
        }catch(Throwable e){crashes.add("strategy_restart",e);}
        finally{migrationRestarting.set(false);}
    }

    private void monitorLoop(){
        LocalHubClient hub=new LocalHubClient(this);
        RealtimeState live=new RealtimeState(this);
        while(running.get()){
            try{
                JSONObject hs=hub.status();
                lastMserverAuthOk=hs.optBoolean("ok",false);
                getSharedPreferences("artitrade_mserver_auth_v077",MODE_PRIVATE).edit()
                    .putLong("at",System.currentTimeMillis())
                    .putBoolean("ok",lastMserverAuthOk)
                    .putString("error",hs.optString("error",""))
                    .putInt("http_status",hs.optInt("http_status",200)).apply();
                if(lastMserverAuthOk){
                    stockEpoch=maintain(hub,"stock",hs.optJSONObject("stock"),stockEpoch);
                    coinEpoch=maintain(hub,"coin",hs.optJSONObject("coin"),coinEpoch);
                    checkPendingPCArchive(hub);
                }
            }catch(Throwable e){
                lastMserverAuthOk=false;
                getSharedPreferences("artitrade_mserver_auth_v077",MODE_PRIVATE).edit()
                    .putLong("at",System.currentTimeMillis()).putBoolean("ok",false)
                    .putString("error","UNREACHABLE:"+e.getClass().getSimpleName()+":"+String.valueOf(e.getMessage()))
                    .putInt("http_status",0).apply();
                crashes.add("mserver_monitor",e);
            }
            try{
                JSONObject p=strategy.snapshot();
                try{p.put("operation",strategy.operationSnapshot());}catch(Throwable ignored){}
                getSharedPreferences("artitrade_runtime_snapshot",MODE_PRIVATE).edit()
                    .putString("snapshot",p.toString()).apply();
                JSONObject st=p.optJSONObject("stock"),co=p.optJSONObject("coin");
                boolean sc=st!=null&&st.optBoolean("connected");
                boolean cc=co!=null&&co.optBoolean("connected");
                JSONObject stockFeed=p.optJSONObject("stock_feed");
                int realSubscriptions=stockFeed==null?0:stockFeed.optInt("subscribed",0);
                long realLastFrame=stockFeed==null?0L:stockFeed.optLong("last_frame_ms",0L);
                String stockDetail=st==null?"전략엔진 준비":st.optString("status","전략엔진 준비");
                if(!sc&&stockFeed!=null&&!stockFeed.optString("stage","").isEmpty())
                    stockDetail+=" · "+stockFeed.optString("stage","")+
                        " · "+stockFeed.optString("detail","");
                live.set("stock",sc,realSubscriptions,realLastFrame,stockDetail);
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
                JSONObject localRecords=strategy.hubRecords();
                getSharedPreferences("artitrade_local_records_v075",MODE_PRIVATE).edit()
                    .putString("records",localRecords.toString()).putLong("updated_at",now).apply();
                if(now-lastHubPublish>=4500){
                    publishPhoneLedgers(hub,localRecords);
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

    private boolean meaningfulRecord(Object value){
        if(value==null||value==JSONObject.NULL)return false;
        if(value instanceof JSONArray)return ((JSONArray)value).length()>0;
        if(!(value instanceof JSONObject))return false;
        JSONObject o=(JSONObject)value;
        for(String key:new String[]{"positions","pending","executions","items","candidate_rows","restorations"}){
            JSONArray a=o.optJSONArray(key);if(a!=null&&a.length()>0)return true;
        }
        for(String key:new String[]{"realized_pnl","unrealized_pnl","total_pnl","candidate_count","positions_count","pending_count"}){
            if(Math.abs(o.optDouble(key,0))>0.000001)return true;
        }
        java.util.Iterator<String> it=o.keys();
        while(it.hasNext()){
            String k=it.next();Object child=o.opt(k);
            if(child instanceof JSONObject||child instanceof JSONArray)if(meaningfulRecord(child))return true;
        }
        return false;
    }

    private void mserverSyncProgress(String stage,String current,int done,int total,int failed,long started){
        int successful=Math.max(0,done-failed);
        int percent=total>0?(int)Math.max(0,Math.min(100,(successful*100L)/total)):0;
        getSharedPreferences("artitrade_mserver_sync_v078",MODE_PRIVATE).edit()
            .putString("stage",stage==null?"":stage).putString("current",current==null?"":current)
            .putInt("done",done).putInt("processed",done).putInt("successful",successful)
            .putInt("total",total).putInt("failed",failed).putInt("percent",percent)
            .putLong("started_at",started).putLong("updated_at",System.currentTimeMillis())
            .putBoolean("complete","COMPLETE".equals(stage)&&failed==0).apply();
    }

    private String recordHash(JSONObject data){
        try{
            byte[] digest=MessageDigest.getInstance("SHA-256").digest(data.toString().getBytes(StandardCharsets.UTF_8));
            StringBuilder out=new StringBuilder(64);
            for(byte b:digest)out.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
            return out.toString();
        }catch(Exception e){throw new IllegalStateException("RECORD_HASH_FAILED",e);}
    }
    private String recordKey(String market,String strategy,String bucket){
        return market+"__"+strategy+"__"+bucket;
    }

    private void publishPhoneLedgers(LocalHubClient hub,JSONObject all){
        if(all==null||all.length()==0){
            mserverSyncProgress("WAIT","전략 원장 생성 대기",0,0,0,System.currentTimeMillis());
            return;
        }
        int total=0;
        for(String market:new String[]{"stock","coin"}){
            JSONObject m=all.optJSONObject(market);if(m==null)continue;
            String[] strategies="stock".equals(market)?new String[]{"shared","at","cw"}:new String[]{"shared","general","ma"};
            for(String strategyName:strategies){
                JSONObject st=m.optJSONObject(strategyName);if(st==null)continue;
                for(String bucket:new String[]{"ledger","research","errors"})if(st.optJSONObject(bucket)!=null)total++;
            }
        }
        if(total==0){
            mserverSyncProgress("WAIT","전송할 전략 원장 생성 대기",0,0,0,System.currentTimeMillis());
            return;
        }
        int ok=0,failed=0,preserved=0,skipped=0,done=0,lastHttp=200;
        String lastError="",lastMarket="",lastStrategy="",lastBucket="";
        long started=System.currentTimeMillis();
        android.content.SharedPreferences hashes=getSharedPreferences("artitrade_mserver_record_hash_v078",MODE_PRIVATE);
        mserverSyncProgress("AUTH","MSERVER 인증",0,total,0,started);
        try{
            JSONObject auth=hub.status();
            if(!auth.optBoolean("ok",false)){
                failed=1;
                lastHttp=auth.optInt("http_status",200);
                lastError=auth.optString("error","MSERVER_AUTH_FAILED");
                if(lastError.isEmpty())lastError="MSERVER_AUTH_FAILED";
                mserverSyncProgress("FAILED","MSERVER 인증 실패",0,total,failed,started);
            }else{
                for(String market:new String[]{"stock","coin"}){
                    JSONObject m=all.optJSONObject(market);if(m==null)continue;
                    String[] strategies="stock".equals(market)?new String[]{"shared","at","cw"}:new String[]{"shared","general","ma"};
                    for(String strategyName:strategies){
                        JSONObject st=m.optJSONObject(strategyName);if(st==null)continue;
                        for(String bucket:new String[]{"ledger","research","errors"}){
                            JSONObject data=st.optJSONObject(bucket);if(data==null)continue;
                            String current=market+"/"+strategyName+"/"+bucket;
                            String key=recordKey(market,strategyName,bucket);
                            String hash=recordHash(data);
                            String previous=hashes.getString(key+"_sha","");
                            long lastVerified=hashes.getLong(key+"_verified",0L);
                            mserverSyncProgress("PUBLISH",current,done,total,failed,started);
                            try{
                                boolean remoteExists=false;
                                boolean unchanged=hash.equals(previous);
                                // Avoid network round-trips on every 4.5 s monitor pass. Verify
                                // unchanged records periodically so clearing MSERVER storage can self-heal.
                                if(unchanged && System.currentTimeMillis()-lastVerified<60_000L){
                                    skipped++;done++;
                                    mserverSyncProgress("PUBLISH",current+" · 변경없음",done,total,failed,started);
                                    continue;
                                }
                                JSONObject meta=hub.recordMeta(market,strategyName,bucket);
                                remoteExists=meta.optBoolean("ok")&&meta.optBoolean("exists");
                                if(unchanged&&remoteExists){
                                    skipped++;done++;
                                    hashes.edit().putLong(key+"_verified",System.currentTimeMillis()).apply();
                                    mserverSyncProgress("PUBLISH",current+" · 변경없음",done,total,failed,started);
                                    continue;
                                }
                                if(!meaningfulRecord(data)&&remoteExists){
                                    preserved++;done++;
                                    hashes.edit().putLong(key+"_verified",System.currentTimeMillis()).apply();
                                    mserverSyncProgress("PUBLISH",current+" · 기존자료 보존",done,total,failed,started);
                                    continue;
                                }
                                JSONObject result=hub.putRecord(market,strategyName,bucket,data);
                                if(result.optBoolean("ok")){
                                    ok++;
                                    hashes.edit().putString(key+"_sha",hash)
                                        .putLong(key+"_verified",System.currentTimeMillis()).apply();
                                }else{
                                    failed++;lastMarket=market;lastStrategy=strategyName;lastBucket=bucket;
                                    lastHttp=result.optInt("http_status",200);
                                    String code=result.optString("error","MSERVER_WRITE_FAILED");
                                    if("data_too_large".equals(code)){
                                        lastError=code+": "+result.optLong("bytes",0)+"/"+result.optLong("max_bytes",0)+" bytes";
                                    }else lastError=code+(lastHttp!=200?" HTTP "+lastHttp:"");
                                    crashes.add("mserver_publish_"+market+"_"+strategyName+"_"+bucket,new IllegalStateException(lastError));
                                }
                            }catch(Throwable e){
                                failed++;lastMarket=market;lastStrategy=strategyName;lastBucket=bucket;lastHttp=0;
                                lastError=e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage());
                                crashes.add("mserver_publish_"+market+"_"+strategyName+"_"+bucket,e);
                            }
                            done++;
                            mserverSyncProgress("PUBLISH",current,done,total,failed,started);
                        }
                    }
                }
                String complete=failed==0
                    ?(ok>0?"MSERVER 변경 원장 게시 완료":"MSERVER 변경없음 · 동기화 유지")
                    :"일부 원장 게시 실패";
                mserverSyncProgress(failed==0?"COMPLETE":"FAILED",complete,done,total,failed,started);
            }
        }catch(Throwable e){
            failed++;lastHttp=0;lastError="MSERVER_UNREACHABLE:"+e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage());
            crashes.add("mserver_publish",e);
            mserverSyncProgress("FAILED","MSERVER 연결 실패",done,total,failed,started);
        }
        getSharedPreferences("artitrade_hub_publish_v075",MODE_PRIVATE).edit()
            .putLong("at",System.currentTimeMillis()).putInt("ok",ok).putInt("failed",failed)
            .putInt("preserved",preserved).putInt("skipped",skipped)
            .putBoolean("legacy_preserved",preserved>0).putString("last_error",lastError)
            .putString("last_market",lastMarket).putString("last_strategy",lastStrategy).putString("last_bucket",lastBucket)
            .putInt("last_http_status",lastHttp).apply();
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
        return b.setContentTitle("ARTITRADE S21+ 메인 엔진 v0.7.8")
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
