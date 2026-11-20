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
import android.net.wifi.WifiManager;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Network;
import org.json.JSONArray;
import org.json.JSONObject;
import android.util.Base64;
import com.chaquo.python.Python;

import java.io.File;
import java.io.RandomAccessFile;

import java.util.UUID;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

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
    static final String SET_TREND_MANUAL="com.artitrade.mobile.SET_TREND_MANUAL";
    static final String APPLY_OPERATION="com.artitrade.mobile.APPLY_OPERATION";
    static final String MANUAL_PAPER_SELL="com.artitrade.mobile.MANUAL_PAPER_SELL";
    static final String RETRY_SYNC="com.artitrade.mobile.RETRY_MSERVER_SYNC";
    static final String SET_PC_SYNC="com.artitrade.mobile.SET_PC_LIVE_SYNC";
    static final String READ_BROKER_SELLS="com.artitrade.mobile.READ_ONLY_BROKER_SELLS";
    private static final String CHANNEL="artitrade_phone_main_v070";
    private static final int NOTIFY_ID=5701;
    private static volatile boolean SERVICE_INSTANCE_ACTIVE=false;
    static boolean isServiceActiveForFreshReset(){return SERVICE_INSTANCE_ACTIVE;}
    private final AtomicBoolean running=new AtomicBoolean(false);
    private final AtomicBoolean migrationRestarting=new AtomicBoolean(false);
    private final AtomicBoolean uiRecordCollecting=new AtomicBoolean(false);
    private final AtomicBoolean marketScoreCollectingV005=new AtomicBoolean(false);
    private volatile long marketScoreLastStartedV005=0L;
    // Explicit PAPER exits take precedence over expensive read-only snapshots.
    private final AtomicInteger manualSellOutstanding=new AtomicInteger(0);
    private volatile long uiRecordLastStartedMs=0L;
    private Thread monitor;
    private Thread preloadObserver;
    private Thread priceOnlyPublisher;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;
    private long stockEpoch=0,coinEpoch=0;
    private String clientId;
    private StrategyRuntimeBridge strategy;
    private RuntimeCrashStore crashes;
    private volatile long lastHubPublish=0L;
    private volatile boolean publishOneShot=false;
    private volatile boolean lastMserverAuthOk=false;
    private volatile boolean antiRollbackReady=false;
    private volatile long lastAntiRollbackCheckMs=0L;
    private static final long ANTI_ROLLBACK_CHECK_MS=30_000L;
    private static final long DAILY_STABILIZE_MS=90_000L;
    private static final long DAILY_RETRY_MS=5*60_000L;
    private final AtomicBoolean dailyBackupRunning=new AtomicBoolean(false);
    private volatile long serviceBootAtMs=System.currentTimeMillis();
    // The MSERVER (127.0.0.1:8765) is an OPTIONAL separate app. While it is
    // absent, probing every 2.5 s only burns CPU and fills the fault log.
    // Healthy => probed every loop turn (unchanged); failing => 2.5s..30s.
    private final ProbeBackoff hubProbe=new ProbeBackoff(2_500L,30_000L);
    // Re-post the foreground notification only when its text changes (or at
    // most once per minute so a user-dismissed notification comes back).
    private String lastNotificationText="";
    private long lastNotificationAtMs=0L;
    private static final long NOTIFICATION_REFRESH_MS=60_000L;

    @Override public void onCreate(){
        super.onCreate();
        SERVICE_INSTANCE_ACTIVE=true;
        serviceBootAtMs=System.currentTimeMillis();
        ProcessHealthV087.install(this);
        crashes=new RuntimeCrashStore(this);
        strategy=new StrategyRuntimeBridge(this);
        clientId=getSharedPreferences("unified_runtime",MODE_PRIVATE).getString("client_id","");
        if(clientId.isEmpty()){
            clientId="phone-"+UUID.randomUUID().toString().replace("-","").substring(0,20);
            getSharedPreferences("unified_runtime",MODE_PRIVATE).edit().putString("client_id",clientId).apply();
        }
        PowerManager pm=(PowerManager)getSystemService(Context.POWER_SERVICE);
        if(pm!=null){
            wakeLock=pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"ARTITRADE:PhoneMainV070");
            wakeLock.setReferenceCounted(false);
        }
        // A CPU PARTIAL_WAKE_LOCK does not ensure screen-off Wi-Fi delivery.
        WifiManager wm=(WifiManager)getApplicationContext().getSystemService(Context.WIFI_SERVICE);
        if(wm!=null){
            wifiLock=wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF,
                "ARTITRADE:PhoneTradingWifi");
            wifiLock.setReferenceCounted(false);
        }
    }

    @Override public int onStartCommand(Intent intent,int flags,int startId){
        try{
            if(intent!=null&&STOP.equals(intent.getAction())){
                stopSafely();stopSelf();return START_NOT_STICKY;
            }
            // v108: the new package is a fresh PAPER-only epoch. Before the
            // phone finishes one complete reset, forbid ANY strategy startup,
            // automatic PC import or background daemon resurrection.
            try{
                if(FreshEpochGateV106.mandatoryFreshStartPending(this)){
                    stopSafely();stopSelf();return START_NOT_STICKY;
                }
            }catch(Exception markerError){
                crashes.add("mandatory_fresh_paper_epoch_gate",markerError);
                stopSafely();stopSelf();return START_NOT_STICKY;
            }
            ensureChannel();
            startForeground(NOTIFY_ID,notification("안전 시작 · 최신 EXE 전략엔진 준비"));
            if(intent!=null&&MANUAL_PAPER_SELL.equals(intent.getAction())){
                final String payload=intent.getStringExtra("request");
                JSONObject manualRequest=new JSONObject(payload==null?"{}":payload);
                String requestId=manualRequest.optString("request_id","");
                if(requestId.isEmpty())throw new IllegalArgumentException("MANUAL_SELL_REQUEST_ID_REQUIRED");
                android.content.SharedPreferences results=getSharedPreferences(
                    "artitrade_manual_sale_v085",MODE_PRIVATE);
                String resultKey="result_"+requestId;
                // Durable claim before any attempt. Never execute the same UUID twice.
                if(!results.contains(resultKey)){
                    JSONObject waiting=new JSONObject().put("request_id",requestId)
                        .put("status","MANUAL_SELL_PENDING").put("stage","QUEUED")
                        .put("market",manualRequest.optString("market",""))
                        .put("strategy",manualRequest.optString("strategy",""))
                        .put("code",manualRequest.optString("code",""))
                        .put("submitted_at",System.currentTimeMillis());
                    if(!results.edit().putString(resultKey,waiting.toString()).commit())
                        throw new IllegalStateException("MANUAL_SELL_PENDING_NOT_DURABLE");
                    manualSellOutstanding.incrementAndGet();
                    try{
                        Thread worker=new Thread(()->applyManualPaperSell(payload),
                            "artitrade-v096-user-paper-exit");
                        worker.setPriority(Thread.NORM_PRIORITY+2);
                        worker.start();
                    }catch(Throwable launchError){
                        manualSellOutstanding.decrementAndGet();
                        crashes.add("manual_paper_sell_thread_start",launchError);
                        JSONObject failed=new JSONObject().put("request_id",requestId)
                            .put("ok",false).put("status","FAILED")
                            .put("error","MANUAL_SELL_WORKER_START_FAILED")
                            .put("detail",launchError.getClass().getSimpleName())
                            .put("completed_at",System.currentTimeMillis());
                        if(!results.edit().putString(resultKey,failed.toString()).commit())
                            throw new IllegalStateException("MANUAL_SELL_START_FAILURE_NOT_DURABLE");
                    }
                }
            }
            if(intent!=null&&READ_BROKER_SELLS.equals(intent.getAction())){
                // Read-only request on a separate worker; no broker order is
                // submitted and no regular quote / strategy loop is blocked.
                new Thread(this::checkBrokerSellReceipts,
                    "artitrade-v008-kiwoom-sells-read-only").start();
            }
            if(intent!=null&&SET_LIVE.equals(intent.getAction())){
                String market=intent.getStringExtra("market");
                boolean enabled=intent.getBooleanExtra("enabled",false);
                final String requestedMarket=market;
                final boolean requestedEnabled=enabled;
                new Thread(()->applyLiveMode(requestedMarket,requestedEnabled),"artitrade-v070-live-arm").start();
            }
            if(intent!=null&&SET_TREND_MANUAL.equals(intent.getAction())){
                final String requestedMarket=intent.getStringExtra("market");
                final boolean requestedEnabled=intent.getBooleanExtra("enabled",false);
                new Thread(()->applyTrendManual(requestedMarket,requestedEnabled),
                    "artitrade-v045-manual-trend").start();
            }
            if(intent!=null&&SET_PC_SYNC.equals(intent.getAction())){
                boolean enabled=intent.getBooleanExtra("enabled",false);
                if(!getSharedPreferences("artitrade_pc_live_sync_v1",MODE_PRIVATE).edit()
                    .putBoolean("enabled",enabled).commit())
                    throw new IllegalStateException("Cannot persist PC sync consent");
                lastHubPublish=0L;
                publishOneShot=false;
                mserverSyncProgress(enabled?"STARTING":"OFF",
                    enabled?"PC와 실시간연동 시작 · 최초 원장 게시 대기":"PC 실시간연동 중지",
                    0,0,0,System.currentTimeMillis());
            }
            if(intent!=null&&RETRY_SYNC.equals(intent.getAction())){
                publishOneShot=true; // explicitly requested single publish, not background opt-in
                lastHubPublish=0L;
                mserverSyncProgress("RETRY","원장 1회 게시 요청",0,0,0,System.currentTimeMillis());
            }
            if(intent!=null&&RESTART_FEEDS.equals(intent.getAction())){
                new Thread(()->safeRestartStrategy(),"artitrade-v074-restart").start();
            }
            // Legacy PC import/replay actions are permanently unsupported.
            // A service restart must not try to rehydrate any prior PAPER ZIP.
            if(intent!=null&&APPLY_OPERATION.equals(intent.getAction())){
                final String raw=intent.getStringExtra("settings");
                if(raw!=null)getSharedPreferences("artitrade_operation_pending",MODE_PRIVATE).edit().putString("settings",raw).apply();
                new Thread(()->applyPendingOperation(),"artitrade-v074-operation").start();
            }
            if(running.compareAndSet(false,true)){
                if(wakeLock!=null&&!wakeLock.isHeld())wakeLock.acquire();
                if(wifiLock!=null&&!wifiLock.isHeld()){
                    try{wifiLock.acquire();}
                    catch(SecurityException e){crashes.add("wifi_lock_permission",e);}
                }
                // Both original broker engines initialize on the worker thread;
                // a separate READ-ONLY readiness observer never opens a socket.
                getSharedPreferences("artitrade_preload_v081",MODE_PRIVATE).edit()
                    .putLong("boot_at",System.currentTimeMillis())
                    .putString("stage","ENGINE_BOOT").putBoolean("complete",false).apply();
                new Thread(()->safeStartStrategy(),"artitrade-v070-python-start").start();
                preloadObserver=new Thread(this::observeStartupReadiness,
                    "artitrade-v081-preload-observer");preloadObserver.start();
                monitor=new Thread(this::monitorLoop,"artitrade-v070-monitor");
                monitor.start();
                priceOnlyPublisher=new Thread(this::priceOnlyLoop,
                    "artitrade-v089-price-only-no-rest");
                priceOnlyPublisher.setDaemon(true);
                priceOnlyPublisher.setPriority(Thread.MIN_PRIORITY+1);
                priceOnlyPublisher.start();
            }
            return START_STICKY;
        }catch(Throwable e){
            crashes.add("service_onStartCommand",e);
            // Keep service process alive in diagnostic mode rather than crashing launcher.
            try{ensureChannel();startForeground(NOTIFY_ID,notification("엔진 시작 오류 · 앱 오류 탭 확인"));}catch(Throwable ignored){}
            return START_STICKY;
        }
    }

    private void checkBrokerSellReceipts(){
        JSONObject result;
        try{
            result=(strategy!=null&&strategy.isStarted())
                ?strategy.brokerSellsV008()
                :new JSONObject().put("ok",false)
                    .put("error","ENGINE_NOT_STARTED").put("orders",new JSONArray());
        }catch(Throwable error){
            crashes.add("broker_sell_receipts_v008",error);
            result=new JSONObject();
            try{result.put("ok",false).put("orders",new JSONArray())
                .put("error","BROKER_READ_ONLY_ERROR_"+error.getClass().getSimpleName());}
            catch(Exception ignored){}
        }
        getSharedPreferences("artitrade_broker_sells_v008",MODE_PRIVATE).edit()
            .putString("receipt",result.toString())
            .putLong("checked_at",System.currentTimeMillis()).apply();
    }

    /** Read-only Android network capability probe. Broker-specific WS/REG and
     * authentic quotes remain the separate execution authority. */
    private Boolean validatedInternetV092(){
        try{
            ConnectivityManager cm=(ConnectivityManager)
                getSystemService(Context.CONNECTIVITY_SERVICE);
            if(cm==null)return null;
            Network active=cm.getActiveNetwork();
            if(active==null)return false;
            NetworkCapabilities caps=cm.getNetworkCapabilities(active);
            return caps!=null &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
        }catch(SecurityException ex){
            crashes.add("recovery_network_capabilities",ex);
            return null;
        }
    }

    private boolean liveOrdersArmed(){
        if(strategy.liveEnabled("stock")||strategy.liveEnabled("coin"))return true;
        // A failed OFF can leave the Python broker runtime enabled even when
        // the UI preference is false; migration must not trust prefs alone.
        try{
            JSONObject runtime=strategy.snapshot();
            JSONObject enabled=runtime.optJSONObject("live_enabled");
            return enabled!=null&&(enabled.optBoolean("stock",false)
                    ||enabled.optBoolean("coin",false));
        }catch(Throwable e){
            crashes.add("live_state_unverified",e);
            return true;
        }
    }
    private void saveMigrationResult(JSONObject result){
        getSharedPreferences("artitrade_pc_ledger_import_result",MODE_PRIVATE).edit()
            .putString("result",result.toString()).putLong("at",System.currentTimeMillis()).apply();
        if(!result.optBoolean("ok",true)&&!"PHONE_HAS_REAL_FILLS".equals(result.optString("status")))
            crashes.add("pc_ledger_migration_reply",new IllegalStateException(result.toString()));
    }
    private void launchWithMigration(){
        // The user's definitive fresh-only epoch has ZERO legacy PAPER
        // holdings to import. Do NOT instantiate the old PC ZIP importer,
        // even for a missing checkpoint. A missing/invalid marker is fatal.
        JSONObject migration=FreshEpochGateV106.migrationGate(this);
        if(migration==null){
            saveMigrationResult(new JSONObject());
            return;
        }
        String epochState=migration.optString("status","");
        if("FRESH_EPOCH_BLOCKED".equals(epochState)){
            saveMigrationResult(migration);
            // An interrupted/deformed reset must NEVER start source engines,
            // resubscribe quotes or import any old PC archive.
            return;
        }
        if("FRESH_EPOCH_NO_PC_IMPORT".equals(epochState)){
            try{FreshEpochGateV106.clearDerivedCachesIfNeeded(this);}
            catch(Exception staleCache){
                crashes.add("fresh_epoch_display_cache_guard",staleCache);
                try{
                    JSONObject blocked=new JSONObject();
                    blocked.put("ok",false);
                    blocked.put("status","FRESH_EPOCH_BLOCKED");
                    blocked.put("reason","STALE_CACHE_NOT_CLEARED_NO_TRADING_START");
                    saveMigrationResult(blocked);
                }catch(Exception reportError){crashes.add("fresh_epoch_report",reportError);}
                return;
            }
        }
        // Verify the committed epoch against an independent durable
        // high-water mark BEFORE opening any original exchange session.
        // If the old 11/14 paper database has reappeared, no buy/sell engine
        // may run and no automatic restore is attempted.
        try{
            LedgerAntiRollbackV002.verify(this);
            antiRollbackReady=true;
            lastAntiRollbackCheckMs=System.currentTimeMillis();
        }catch(Throwable rollback){
            LedgerAntiRollbackV002.block(this,rollback);
            crashes.add("PAPER_LEDGER_ROLLBACK_START_BLOCKED",rollback);
            stopSafely();
            stopSelf();
            return;
        }
        JSONObject started=strategy.start();
        if(!started.optBoolean("ok",false)||!started.optBoolean("started",false)){
            // A failed Python engine must not masquerade as a successful
            // fresh-epoch startup merely because the UI service is alive.
            // Preserve SQLite and the committed epoch marker unchanged.
            IllegalStateException failure=new IllegalStateException(
                "PAPER_ENGINE_NOT_STARTED:"+started.toString());
            crashes.add("strategy_start_reply",failure);
            JSONObject blocked=new JSONObject();
            try{
                blocked.put("ok",false).put("status","ENGINE_START_FAILED")
                    .put("reason","PYTHON_RUNTIME_NOT_STARTED")
                    .put("startup",started);
            }catch(org.json.JSONException ignored){}
            saveMigrationResult(blocked);
            getSharedPreferences("artitrade_preload_v081",MODE_PRIVATE).edit()
                .putString("stage","ENGINE_START_FAILED")
                .putBoolean("complete",false).commit();
            stopSafely();
            stopSelf();
            return;
        }
        JSONObject records=strategy.hubRecordsUi();
        // Make verified source ledgers available to the initial dashboard
        // without waiting for the MSERVER monitor's separate network request.
        if(records!=null&&records.length()>0){
            // Never write a giant source history JSON string into XML prefs.
            // Trading, all SQLite history and the full PC archive remain untouched.
            try{
                LargeRecordStoreV087.write(this,records.toString());
                getSharedPreferences("artitrade_local_records_v075",MODE_PRIVATE)
                    .edit().remove("records").putString("storage","file-v087")
                    .putLong("updated_at",System.currentTimeMillis()).apply();
            }catch(java.io.IOException viewError){
                crashes.add("ui_initial_bounded_snapshot_v104",viewError);
            }
        }
        // A new fresh-only epoch has NO older PC rows to verify or inherit.
        // Source ledgers come exclusively from the eight newly started wallets.
        // The former importer's finishRuntimeVerification() intentionally
        // cannot be called because all legacy migration is disabled.
        saveMigrationResult(migration);
        applyPendingOperation();
    }
    private void safeStartStrategy(){
        if(!migrationRestarting.compareAndSet(false,true))return;
        try{launchWithMigration();}
        catch(Throwable e){crashes.add("strategy_start_thread",e);}
        finally{migrationRestarting.set(false);}
    }
    private void observeStartupReadiness(){
        // The original market threads are already starting. Poll their
        // existing snapshot without creating a second Kiwoom/Upbit login.
        // Local preflight must progress even when MSERVER is unreachable.
        final long deadline=System.currentTimeMillis()+55_000L;
        int errors=0;
        while(running.get()&&System.currentTimeMillis()<deadline){
            try{
                if(strategy!=null&&strategy.isStarted()){
                    JSONObject snapshot=strategy.snapshot();
                    JSONObject readiness=snapshot.optJSONObject("startup_preload_v081");
                    if(readiness!=null&&readiness.optLong("updated_at_ms",0)>0){
                        getSharedPreferences("artitrade_runtime_snapshot",MODE_PRIVATE)
                            .edit().putString("snapshot",snapshot.toString()).apply();
                        JSONObject stock=readiness.optJSONObject("stock");
                        JSONObject coin=readiness.optJSONObject("coin");
                        boolean needStock=stock!=null&&stock.optBoolean("keys_present",false)
                            &&stock.optBoolean("session_required",false);
                        boolean stockReady=!needStock||stock.optBoolean("ws_ready",false);
                        boolean coinReady=coin!=null&&coin.optBoolean("ws_ready",false);
                        JSONObject coinGate=coin==null?null:coin.optJSONObject("preload");
                        JSONObject stockGate=stock==null?null:stock.optJSONObject("preload");
                        boolean fullyWarm=coinGate!=null&&coinGate.optBoolean("ready",false)
                            &&(!needStock||(stockGate!=null&&stockGate.optBoolean("ready",false)));
                        String stage=fullyWarm&&stockReady&&coinReady?"READY":
                            stockReady&&coinReady?"WARMING_CANDLES":"CONNECTING";
                        getSharedPreferences("artitrade_preload_v081",MODE_PRIVATE).edit()
                            .putString("stage",stage)
                            .putLong("observed_at",System.currentTimeMillis())
                            .putBoolean("stock_ready",stockReady)
                            .putBoolean("coin_ready",coinReady)
                            .putBoolean("complete",fullyWarm&&stockReady&&coinReady).apply();
                        if(fullyWarm&&stockReady&&coinReady)break;
                    }
                }
            }catch(Throwable e){
                if(errors++<2)crashes.add("v081_startup_observer",e);
            }
            try{Thread.sleep(1350L);}
            catch(InterruptedException ie){Thread.currentThread().interrupt();break;}
        }
    }

    private void applyManualPaperSell(String raw){
        JSONObject result=new JSONObject();
        String requestId="";
        long began=System.currentTimeMillis();
        android.content.SharedPreferences results=getSharedPreferences(
            "artitrade_manual_sale_v085",MODE_PRIVATE);
        try{
            JSONObject request=new JSONObject(raw==null?"{}":raw);
            requestId=request.optString("request_id","");
            // Mark the exact UUID's worker state before entering the
            // synchronized Python bridge; it may be busy building UI snapshots.
            JSONObject active=new JSONObject().put("request_id",requestId)
                .put("status","MANUAL_SELL_RUNNING").put("stage","ENGINE_LOCK_WAIT")
                .put("started_at",began)
                .put("market",request.optString("market",""))
                .put("strategy",request.optString("strategy",""))
                .put("code",request.optString("code",""));
            if(!results.edit().putString("result_"+requestId,active.toString()).commit())
                throw new IllegalStateException("MANUAL_SELL_RUNNING_NOT_DURABLE");
            if(!request.optBoolean("user_confirmed",false))
                result.put("ok",false).put("error","MANUAL_SELL_CONFIRMATION_REQUIRED");
            else if(!running.get()||strategy==null||!strategy.isStarted())
                result.put("ok",false).put("error","PAPER_STRATEGY_NOT_RUNNING");
            else{
                active.put("stage","ENGINE_EXECUTION_REQUESTED")
                    .put("engine_request_at",System.currentTimeMillis());
                if(!results.edit().putString("result_"+requestId,active.toString()).commit())
                    throw new IllegalStateException("MANUAL_SELL_EXECUTING_NOT_DURABLE");
                result=strategy.manualPaperSell(request);
                if(!result.has("ok"))
                    result.put("ok",false).put("error","MANUAL_SELL_RESULT_INVALID");
            }
        }catch(Throwable error){
            try{crashes.add("manual_paper_sell_service",error);}catch(Throwable ignored){}
            try{result=new JSONObject().put("ok",false)
                .put("error","MANUAL_SELL_EXECUTION_EXCEPTION")
                .put("detail",error.getClass().getSimpleName()+": "+
                    String.valueOf(error.getMessage()));}
            catch(Throwable ignored){result=new JSONObject();}
        }finally{
            try{
                result.put("request_id",requestId);
                result.put("completed_at",System.currentTimeMillis());
                result.put("elapsed_ms",System.currentTimeMillis()-began);
                // Persist the exact UUID's result before permitting another
                // UI snapshot. Never synthesize a fill or retry an unknown order.
                boolean saved=results.edit().putString("result_"+requestId,result.toString())
                    .putString("last_result",result.toString()).commit();
                if(!saved)throw new IllegalStateException("MANUAL_SELL_RESULT_NOT_DURABLE");
                if(!result.optBoolean("ok",false))
                    crashes.add("manual_paper_sell_rejected",
                        new IllegalStateException(result.optString("error","UNKNOWN")+
                            ": "+result.optString("detail","")));
            }catch(Throwable storeError){
                try{crashes.add("manual_paper_sell_result_store",storeError);}
                catch(Throwable ignored){}
            }finally{
                manualSellOutstanding.decrementAndGet();
                // A successful native PAPER SELL updates the authoritative SQLite
                // immediately, but dashboards read a separately published view
                // snapshot. Make the next low-priority read-only publication due
                // NOW, without changing orders, executions, or the user's DB.
                // This must run after the manual worker releases its priority gate.
                if(result.optBoolean("ok",false)&&running.get()
                        &&strategy!=null&&strategy.isStarted()){
                    uiRecordLastStartedMs=0L;
                    try{
                        scheduleUiRecordsIfDue(new LocalHubClient(this));
                    }catch(Throwable refreshError){
                        try{crashes.add("manual_sell_view_refresh",refreshError);}
                        catch(Throwable ignored){}
                    }
                }
            }
        }
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

    /**
     * A FULL historical SQLite/Python JSON aggregation previously ran on the
     * lease/heartbeat monitor every 2.5 seconds and wrote a multi-MB value
     * into SharedPreferences XML. Rendering also parsed this every 3.5s.
     *
     * Keep market tick/order workers and the 2.5s heartbeat unchanged, but
     * run the expensive read-only display snapshot on one bounded worker.
     * On a hidden UI only the display refresh slows; executions do not.
     */
    /** Independent small market-score snapshot for the market screen.
     * Never changes score_events, fills, orders, credentials or wallets.
     * This lane is not blocked by the old 5MB local_records screen view.
     */
    private void scheduleMarketScoreV005(){
        if(!running.get()||strategy==null||!strategy.isStarted())return;
        long now=System.currentTimeMillis();
        if(now-marketScoreLastStartedV005<5000L)return;
        if(!marketScoreCollectingV005.compareAndSet(false,true))return;
        marketScoreLastStartedV005=now;
        Thread worker=new Thread(()->{
            try{
                if(!running.get()||!strategy.isStarted())return;
                JSONObject snapshot=strategy.scoreSnapshotV005();
                if(!"ARTITRADE_MARKET_SCORE_PLANE_V005".equals(
                        snapshot.optString("schema","")))return;
                long received=System.currentTimeMillis();
                getSharedPreferences("artitrade_market_score_v005",MODE_PRIVATE)
                    .edit().putString("snapshot",snapshot.toString())
                    .putLong("written_at_ms",received).apply();
            }catch(Throwable err){
                try{crashes.add("market_score_plane_v005",err);}
                catch(Throwable ignored){}
            }finally{
                marketScoreCollectingV005.set(false);
            }
        },"artitrade-v005-score-plane");
        worker.setDaemon(true);
        worker.setPriority(Thread.MIN_PRIORITY+1);
        worker.start();
    }

    private void scheduleUiRecordsIfDue(LocalHubClient hub){
        if(!running.get()||strategy==null||!strategy.isStarted()
            ||manualSellOutstanding.get()>0)return;
        long now=System.currentTimeMillis();
        android.content.SharedPreferences visibility=getSharedPreferences(
            "artitrade_ui_visibility_v087",MODE_PRIVATE);
        boolean visible=visibility.getBoolean("foreground",false)
            &&now-visibility.getLong("at_ms",0L)<13_000L;
        long interval=visible?6_500L:17_000L;
        if(!publishOneShot&&now-uiRecordLastStartedMs<interval)return;
        if(!uiRecordCollecting.compareAndSet(false,true))return;
        uiRecordLastStartedMs=now;
        Thread worker=new Thread(()->{
            long began=android.os.SystemClock.elapsedRealtime();
            try{
                if(!running.get()||!strategy.isStarted()
                    ||manualSellOutstanding.get()>0)return;
                // The original complete data is still present in every SQLite DB;
                // only a display window is serialized for the 256MB Java heap.
                JSONObject localRecords=strategy.hubRecordsUi();
                if(!running.get())return;
                String serialized=localRecords.toString();
                // Atomic file is a VIEW, never the broker/order authority.
                // Oversize stops only a UI write, never a live tick or SELL.
                LargeRecordStoreV087.write(this,serialized);
                long completed=System.currentTimeMillis();
                getSharedPreferences("artitrade_local_records_v075",MODE_PRIVATE)
                    .edit().remove("records").putString("storage","file-v087")
                    .putLong("updated_at",completed).apply();
                ProcessHealthV087.sample(this,"ledger_ui_publish",serialized.length(),
                    android.os.SystemClock.elapsedRealtime()-began,false);
                boolean enabled=getSharedPreferences("artitrade_pc_live_sync_v1",
                    MODE_PRIVATE).getBoolean("enabled",false);
                if(running.get()&&PcLiveSyncPolicy.shouldPublish(
                    enabled,publishOneShot,completed,lastHubPublish)){
                    publishOneShot=false;
                    // Never publish a truncated UI window as a complete PC ledger.
                    // Existing full source buckets remain the sync authority.
                    JSONObject originalForSync=strategy.hubRecords();
                    publishPhoneLedgers(hub,originalForSync);
                    lastHubPublish=completed;
                }
            }catch(Throwable error){
                try{crashes.add("ui_record_source_throttled_v087",error);}
                catch(Throwable ignored){}
            }finally{
                uiRecordCollecting.set(false);
            }
        },"artitrade-v087-view-snapshot");
        worker.setDaemon(true);
        worker.setPriority(Thread.MIN_PRIORITY+1);
        worker.start();
    }

    /**
     * Price-plane heartbeat never makes a Kiwoom/Upbit request, constructs
     * historical bars, scans SQLite or enters the full UI aggregation lane.
     * UI refresh is independently rate-limited to avoid GIL/CPU contention.
     */
    private void priceOnlyLoop(){
        String previous="";
        while(running.get()){
            long interval=1200L;
            try{
                android.content.SharedPreferences visibility=getSharedPreferences(
                    "artitrade_ui_visibility_v087",MODE_PRIVATE);
                long now=System.currentTimeMillis();
                boolean foreground=visibility.getBoolean("foreground",false)
                    &&now-visibility.getLong("at_ms",0L)<13_000L;
                interval=foreground?1200L:3500L;
                // Human-confirmed SELL and its ledger evidence take priority.
                if(manualSellOutstanding.get()==0&&strategy!=null
                    &&strategy.isStarted()){
                    JSONObject snapshot=strategy.quoteSnapshotV089();
                    String revision=snapshot.optString("revision","");
                    if(snapshot.optBoolean("ready",false)
                        &&!revision.isEmpty()&&!revision.equals(previous)){
                        previous=revision;
                        getSharedPreferences("artitrade_quote_plane_v089",MODE_PRIVATE)
                            .edit().putString("snapshot",snapshot.toString())
                            .putLong("written_at_ms",now).apply();
                    }
                }
            }catch(Throwable err){
                // Price display is best-effort; it must never stop trading.
                previous="";
            }
            try{Thread.sleep(interval);}
            catch(InterruptedException interrupted){
                Thread.currentThread().interrupt();break;
            }
        }
    }

    private void monitorLoop(){
        LocalHubClient hub=new LocalHubClient(this);
        RealtimeState live=new RealtimeState(this);
        while(running.get()){
            // Outside quote/order threads: periodically catch a replaced
            // or older SQLite/WAL snapshot even while the app remains open.
            // Do not compete with a user-confirmed PAPER SELL worker.
            if(antiRollbackReady&&manualSellOutstanding.get()==0&&
                System.currentTimeMillis()-lastAntiRollbackCheckMs>=
                    ANTI_ROLLBACK_CHECK_MS){
                try{
                    LedgerAntiRollbackV002.verify(this);
                    lastAntiRollbackCheckMs=System.currentTimeMillis();
                }catch(Throwable rollback){
                    LedgerAntiRollbackV002.block(this,rollback);
                    crashes.add("PAPER_LEDGER_ROLLBACK_RUNTIME_BLOCKED",rollback);
                    stopSafely();
                    stopSelf();
                    return;
                }
            }
            long probeNowMs=android.os.SystemClock.elapsedRealtime();
            // While a probe is not due, the previous result stays in force:
            // after a failure lastMserverAuthOk=false and both epochs are 0
            // (fail-closed), exactly as set below.
            if(hubProbe.due(probeNowMs)){
                try{
                    JSONObject hs=hub.status();
                    lastMserverAuthOk=hs.optBoolean("ok",false);
                    getSharedPreferences("artitrade_mserver_auth_v077",MODE_PRIVATE).edit()
                        .putLong("at",System.currentTimeMillis())
                        .putBoolean("ok",lastMserverAuthOk)
                        .putString("error",hs.optString("error",""))
                        .putInt("http_status",hs.optInt("http_status",200)).apply();
                    if(lastMserverAuthOk){
                        hubProbe.onSuccess();
                        stockEpoch=maintain(hub,"stock",hs.optJSONObject("stock"),stockEpoch);
                        coinEpoch=maintain(hub,"coin",hs.optJSONObject("coin"),coinEpoch);
                    }else{
                        // A rejected/expired hub status is not proof of execution ownership.
                        hubProbe.onFailure(probeNowMs);
                        stockEpoch=0;coinEpoch=0;
                        disarmIfAndroidFlagsArmed("MSERVER_STATUS_UNVERIFIED");
                    }
                }catch(Throwable e){
                    hubProbe.onFailure(probeNowMs);
                    lastMserverAuthOk=false;
                    stockEpoch=0;coinEpoch=0;
                    disarmIfAndroidFlagsArmed("MSERVER_STATUS_UNREACHABLE");
                    getSharedPreferences("artitrade_mserver_auth_v077",MODE_PRIVATE).edit()
                        .putLong("at",System.currentTimeMillis()).putBoolean("ok",false)
                        .putString("error","UNREACHABLE:"+e.getClass().getSimpleName()+":"+String.valueOf(e.getMessage()))
                        .putInt("http_status",0).apply();
                    // Identical rows are merged by RuntimeCrashStore; report the
                    // first failure of a streak and then every 10th only.
                    if(hubProbe.shouldReport())crashes.add("mserver_monitor",e);
                }
            }
            try{
                // Heartbeat comes from the service thread even when Activity
                // is paused, invisible, or the display has gone to sleep.
                long heartbeat=System.currentTimeMillis();
                getSharedPreferences("artitrade_background_health_v1",MODE_PRIVATE).edit()
                    .putLong("last_heartbeat_ms",heartbeat)
                    .putBoolean("cpu_lock",wakeLock!=null&&wakeLock.isHeld())
                    .putBoolean("wifi_lock",wifiLock!=null&&wifiLock.isHeld())
                    .putBoolean("service_running",running.get()).apply();
                // Avoid competing for the synchronized Python bridge while
                // a user-confirmed PAPER SELL is waiting to execute.
                if(manualSellOutstanding.get()>0){
                    ProcessHealthV087.sample(this,"manual_sell_priority",0L,0L,false);
                    // Retain the original 2.5-second lease/heartbeat cadence,
                    // instead of hammering the hub while a sell is pending.
                    try{Thread.sleep(2500L);}
                    catch(InterruptedException e){Thread.currentThread().interrupt();break;}
                    continue;
                }
                // Distinguish actual Android internet loss from a healthy
                // network with only one broker WebSocket/subscription down.
                // Pure local observation; never requests an API or arms orders.
                Boolean validated=validatedInternetV092();
                if(validated!=null){
                    getSharedPreferences("artitrade_recovery_network_v092",MODE_PRIVATE)
                        .edit().putBoolean("validated_internet",validated)
                        .putLong("observed_at",System.currentTimeMillis()).apply();
                    strategy.reportNetworkV092(validated);
                }
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
                // Without an actual coin subscription count and last-frame
                // timestamp, publish unknown (0). Never claim synthetic 35/now.
                live.set("coin",cc,0,0L,co==null?"전략엔진 준비":co.optString("status"));
                JSONObject enabled=p.optJSONObject("live_enabled");
                // Python fail-closed order reconciliation can disarm a market internally.
                // Mirror that state into Android prefs so UI/notification/next boot cannot re-arm it silently.
                if(enabled!=null){
                    if(strategy.liveEnabled("stock")&&!enabled.optBoolean("stock",false))strategy.setLive("stock",false);
                    if(strategy.liveEnabled("coin")&&!enabled.optBoolean("coin",false))strategy.setLive("coin",false);
                }
                String orders=(enabled!=null&&enabled.optBoolean("stock")?"S":"-")+(enabled!=null&&enabled.optBoolean("coin")?"C":"-");
                String notificationText="주식 "+(sc?"LIVE":"—")+" · 코인 "+(cc?"LIVE":"—")+" · 실주문 "+orders;
                long notifyNow=android.os.SystemClock.elapsedRealtime();
                if(!notificationText.equals(lastNotificationText)
                        ||notifyNow-lastNotificationAtMs>=NOTIFICATION_REFRESH_MS){
                    NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
                    if(nm!=null){
                        nm.notify(NOTIFY_ID,notification(notificationText));
                        lastNotificationText=notificationText;
                        lastNotificationAtMs=notifyNow;
                    }
                }
                scheduleMarketScoreV005();
                scheduleUiRecordsIfDue(hub);
                scheduleDailyBackupIfReady(hub);
                ProcessHealthV087.sample(this,"service_heartbeat",0L,0L,false);
                // Reuse THIS turn's snapshot: a second synchronized Python
                // snapshot() per turn only re-read the same live flags.
                if(stockEpoch<=0||coinEpoch<=0)
                    disarmUnverifiedMarkets("LEASE_UNCONFIRMED",p);
            }catch(Throwable e){crashes.add("strategy_monitor",e);}
            try{Thread.sleep(2500);}catch(InterruptedException e){Thread.currentThread().interrupt();break;}
        }
        try{releaseQuietly(hub,"stock",stockEpoch);releaseQuietly(hub,"coin",coinEpoch);}catch(Throwable ignored){}
    }

    /**
     * Early (mid-turn) fail-closed path. It needs a Python snapshot only when
     * Android's own arm flags say a market might be armed; otherwise it does
     * nothing. A Python-side arm that Android does not know about is still
     * caught at the end of the same loop turn from that turn's snapshot (and
     * this APK cannot arm either side: setLive(true) is refused in Java and
     * in Python).
     */
    private void disarmIfAndroidFlagsArmed(String reason){
        if(strategy.liveEnabled("stock")||strategy.liveEnabled("coin"))
            disarmUnverifiedMarkets(reason,null);
    }

    /** @param knownState a snapshot the caller already holds, or null to fetch one. */
    private void disarmUnverifiedMarkets(String reason,JSONObject knownState){
        JSONObject enabled=new JSONObject();
        try{
            JSONObject state=knownState!=null?knownState:strategy.snapshot();
            JSONObject flags=state.optJSONObject("live_enabled");
            if(flags!=null)enabled=flags;
        }catch(Throwable e){crashes.add("disarm_snapshot_"+reason,e);}
        try{
            if((stockEpoch<=0||!lastMserverAuthOk)
                    &&(strategy.liveEnabled("stock")||enabled.optBoolean("stock",false)))
                strategy.setLive("stock",false);
        }catch(Throwable e){crashes.add("disarm_stock_"+reason,e);}
        try{
            if((coinEpoch<=0||!lastMserverAuthOk)
                    &&(strategy.liveEnabled("coin")||enabled.optBoolean("coin",false)))
                strategy.setLive("coin",false);
        }catch(Throwable e){crashes.add("disarm_coin_"+reason,e);}
    }

    private void applyTrendManual(String market,boolean enabled){
        JSONObject result;
        try{
            if(!("stock".equals(market)||"coin".equals(market))){
                result=new JSONObject().put("ok",false)
                    .put("error","INVALID_TREND_MARKET");
            }else if(strategy==null||!strategy.isStarted()){
                result=new JSONObject().put("ok",false)
                    .put("error","TREND_RUNTIME_NOT_READY");
            }else{
                result=strategy.setTrendManual(market,enabled);
            }
        }catch(Throwable e){
            crashes.add("manual_trend_service",e);
            try{result=new JSONObject().put("ok",false)
                .put("error","MANUAL_TREND_SERVICE_FAILED");}
            catch(Exception ignored){result=new JSONObject();}
        }
        // Acknowledgement is not evidence of an executed broker order.
        getSharedPreferences("artitrade_trend_manual_result_v045",MODE_PRIVATE)
            .edit().putString("market",market==null?"":market)
            .putString("result",result.toString())
            .putLong("at",System.currentTimeMillis()).commit();
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
            if(epoch<=0)throw new IllegalStateException("PHONE_LEASE_EPOCH_MISSING");
            JSONObject applied=strategy.setLive(market,true);
            if(!applied.optBoolean("ok",false))
                throw new IllegalStateException("LIVE_RUNTIME_ARM_FAILED:"+applied);
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

    /**
     * Daily 20:10 KST on verified KRX trading days, 11:30 KST every coin day.
     * After power-off/reboot, a missed due window remains pending until
     * 90s of stable native engine and monotonic SQLite start-up validation.
     * One market per low-priority worker, never on the order/feed thread.
     */
    private void scheduleDailyBackupIfReady(LocalHubClient hub){
        long now=System.currentTimeMillis();
        if(!running.get()||!antiRollbackReady||!lastMserverAuthOk||
            strategy==null||!strategy.isStarted()||
            manualSellOutstanding.get()>0||
            now-serviceBootAtMs<DAILY_STABILIZE_MS||
            dailyBackupRunning.get())return;
        android.content.SharedPreferences sp=getSharedPreferences(
            "artitrade_daily_backup_v003",MODE_PRIVATE);
        for(String market:new String[]{"stock","coin"}){
            // The 0.0.7 export proves an older MSERVER responds
            // unknown_endpoint to /daily. Don't spam it every five minutes;
            // retain the exact due date and every original source SQLite.
            if(now<sp.getLong("remote_protocol_retry_after_"+market,0L))
                continue;
            if(now-sp.getLong("attempt_at_"+market,0L)<DAILY_RETRY_MS)
                continue;
            if(!dailyBackupRunning.compareAndSet(false,true))return;
            final String current=market;
            // Reserve attempt before worker launch to prevent a duplicate.
            sp.edit().putLong("attempt_at_"+market,now).apply();
            Thread worker=new Thread(()->{
                try{runDailyBackup(hub,current);}
                catch(Throwable failure){
                    final String detail=String.valueOf(failure.getMessage());
                    boolean unsupported=detail.contains(
                        "MSERVER_DAILY_ENDPOINT_UNAVAILABLE:unknown_endpoint");
                    long checked=System.currentTimeMillis();
                    // Protocol mismatch is recoverable only after the PC
                    // endpoint is upgraded. Never label the backup VERIFIED.
                    if(!unsupported||checked-sp.getLong(
                            "last_protocol_fault_"+current,0L)>=6*60*60_000L)
                        crashes.add("daily_backup_"+current,failure);
                    android.content.SharedPreferences.Editor edit=sp.edit()
                        .putString("stage_"+current,unsupported?
                            "REMOTE_ENDPOINT_UNSUPPORTED":"FAILED")
                        .putString("error_"+current,
                            failure.getClass().getSimpleName()+":"+detail);
                    if(unsupported){
                        edit.putLong("remote_protocol_retry_after_"+current,
                            checked+6*60*60_000L);
                        edit.putLong("last_protocol_fault_"+current,checked);
                    }else{
                        edit.remove("remote_protocol_retry_after_"+current);
                    }
                    edit.apply();
                }finally{dailyBackupRunning.set(false);}
            },"artitrade-daily-"+market+"-wal-safe");
            worker.setDaemon(true);
            worker.setPriority(Thread.MIN_PRIORITY+1);
            worker.start();
            return;
        }
    }

    private static String sha256Daily(byte[] raw){
        try{
            byte[] h=MessageDigest.getInstance("SHA-256").digest(raw);
            StringBuilder out=new StringBuilder(64);
            for(byte b:h)out.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
            return out.toString();
        }catch(Exception e){throw new IllegalStateException("DAILY_SHA_INVALID",e);}
    }

    private void runDailyBackup(LocalHubClient hub,String market)throws Exception{
        android.content.SharedPreferences sp=getSharedPreferences(
            "artitrade_daily_backup_v003",MODE_PRIVATE);
        String last=sp.getString("done_date_"+market,"");
        String status=Python.getInstance().getModule(
            "android_daily_paper_backup_v003").callAttr("due_json",market,last)
            .toString();
        JSONObject due=new JSONObject(status);
        if(!due.optBoolean("ok",false))
            throw new IllegalStateException("DAILY_TRADING_CALENDAR:"+
                due.optString("error",""));
        if(!due.optBoolean("due",false))return;
        String date=due.getString("date");
        String epoch=FreshEpochGateV106.read(this).getString("epoch_id");
        // The server is an ADDITIONAL private-application backup sink.
        // It must never provide a record to normal active trading UI.
        JSONObject server=hub.dailyLatest(market,epoch);
        if(!server.optBoolean("ok",false))
            throw new IllegalStateException("MSERVER_DAILY_ENDPOINT_UNAVAILABLE:"+
                server.optString("error",""));
        if(server.optBoolean("exists",false) &&
            date.equals(server.optString("date"))){
            sp.edit().putString("done_date_"+market,date)
                .putString("stage_"+market,"VERIFIED_SERVER_PRESENT")
                .putString("sha256_"+market,server.optString("sha256"))
                .putLong("saved_at_"+market,System.currentTimeMillis())
                .remove("error_"+market).commit();
            return;
        }
        // Before reading any row, enforce the independent epoch/wallet guard.
        LedgerAntiRollbackV002.verify(this);
        JSONObject plan=new JSONObject(Python.getInstance()
            .getModule("android_daily_paper_backup_v003")
            .callAttr("prepare",
                new File(getFilesDir(),"ARTITRADE_PHONE_MAIN_V070").getAbsolutePath(),
                getCacheDir().getAbsolutePath(),market,date).toString());
        if(!plan.optBoolean("ok",false))
            throw new IllegalStateException("DAILY_NATIVE_BACKUP_FAILED:"+
                plan.optString("error",""));
        if(!epoch.equals(plan.optString("epoch_id"))||
           !market.equals(plan.optString("market"))||
           !date.equals(plan.optString("date")))
            throw new SecurityException("DAILY_BACKUP_WRONG_EPOCH_OR_SCOPE");
        File allowed=new File(getCacheDir(),"artitrade_daily_v003").getCanonicalFile();
        File zip=new File(plan.getString("path")).getCanonicalFile();
        if(!allowed.equals(zip.getParentFile())||!zip.isFile()||
           zip.length()!=plan.getLong("bytes"))
            throw new SecurityException("DAILY_BACKUP_INVALID_LOCAL_FILE");
        long size=zip.length();
        String hash=plan.getString("sha256");
        if(!hash.matches("[0-9a-f]{64}"))
            throw new IllegalStateException("DAILY_BACKUP_HASH_INVALID");
        final int chunk=DailyBackupStoreV003.CHUNK_BYTES;
        int chunks=(int)((size+chunk-1)/chunk);
        String session="d"+date.replace("-","")+"_"+market+"_"+hash.substring(0,24);
        JSONObject begin=hub.dailyBegin(market,date,epoch,session,size,hash,chunks);
        if(!begin.optBoolean("ok",false))
            throw new IllegalStateException("DAILY_SERVER_BEGIN:"+
                begin.optString("error",""));
        if(!begin.optBoolean("committed",false)){
            int received=begin.optInt("received",0);
            if(received<0||received>chunks)
                throw new IllegalStateException("DAILY_SERVER_INVALID_RESUME_OFFSET");
            sp.edit().putString("stage_"+market,"UPLOADING").apply();
            try(RandomAccessFile input=new RandomAccessFile(zip,"r")){
                input.seek((long)received*chunk);
                for(int i=received;i<chunks;i++){
                    if(!running.get()||manualSellOutstanding.get()>0||
                        !antiRollbackReady)
                        throw new IllegalStateException("DAILY_ENGINE_UNSTABLE_STOP_UPLOAD");
                    int want=(int)Math.min((long)chunk,size-(long)i*chunk);
                    byte[] data=new byte[want];
                    input.readFully(data);
                    JSONObject ack=hub.dailyChunk(market,date,session,i,
                        Base64.encodeToString(data,Base64.NO_WRAP),
                        sha256Daily(data));
                    if(!ack.optBoolean("ok",false)||
                       ack.optInt("received",-1)<i+1)
                        throw new IllegalStateException("DAILY_CHUNK_ACK_FAILURE:"+
                            ack.optString("error",""));
                    if(i%8==0)sp.edit().putInt("progress_"+market,
                        (int)(100L*(i+1)/chunks)).apply();
                }
            }
        }
        JSONObject committed=hub.dailyCommit(market,date,session);
        if(!committed.optBoolean("ok",false)||
           !hash.equalsIgnoreCase(committed.optString("sha256",""))||
           committed.optLong("bytes",-1)!=size)
            throw new IllegalStateException("DAILY_SERVER_COMMIT_UNVERIFIED:"+
                committed.optString("error",""));
        JSONObject latest=hub.dailyLatest(market,epoch);
        if(!latest.optBoolean("ok",false)||
            !date.equals(latest.optString("date"))||
            !hash.equalsIgnoreCase(latest.optString("sha256")))
            throw new IllegalStateException("DAILY_SERVER_READBACK_MISMATCH");
        // Commit the completed date only AFTER MSERVER readback is verified.
        if(!sp.edit().putString("done_date_"+market,date)
            .putString("stage_"+market,"VERIFIED")
            .putString("sha256_"+market,hash)
            .putLong("saved_at_"+market,System.currentTimeMillis())
            .putInt("progress_"+market,100).remove("error_"+market).commit())
            throw new IllegalStateException("DAILY_LOCAL_ACK_NOT_DURABLE");
        // The server retains the complete snapshot; remove only this
        // temporary working copy, never the actual source trading SQLite.
        if(!zip.delete()&&zip.exists())
            crashes.add("daily_cache_cleanup_"+market,
                new IllegalStateException("DAILY_CACHE_ZIP_RETAINED"));
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
        if(status==null||!status.optBoolean("ok"))return 0;
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
        }catch(Exception e){return 0;}
    }
    private void releaseQuietly(LocalHubClient hub,String market,long epoch){
        if(epoch<=0)return;try{hub.release(market,clientId,epoch);}catch(Exception ignored){}
    }

    private Notification notification(String text){
        Notification.Builder b=Build.VERSION.SDK_INT>=26?new Notification.Builder(this,CHANNEL):new Notification.Builder(this);
        return b.setContentTitle("ARTITRADE")
            .setContentText(text).setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true).setCategory(Notification.CATEGORY_SERVICE).build();
    }
    private void ensureChannel(){
        if(Build.VERSION.SDK_INT>=26){
            NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
            if(nm!=null){
                NotificationChannel c=new NotificationChannel(CHANNEL,"ARTITRADE",NotificationManager.IMPORTANCE_LOW);
                c.setDescription("최신 EXE 전략·키움·업비트 실시간·실주문 엔진");
                nm.createNotificationChannel(c);
            }
        }
    }
    private void stopSafely(){
        running.set(false);
        try{if(strategy!=null)strategy.stop();}catch(Throwable e){if(crashes!=null)crashes.add("service_stop_strategy",e);}
        if(monitor!=null)monitor.interrupt();
        if(priceOnlyPublisher!=null)priceOnlyPublisher.interrupt();
        if(preloadObserver!=null)preloadObserver.interrupt();
        getSharedPreferences("artitrade_preload_v081",MODE_PRIVATE).edit()
            .putString("stage","STOPPED").putBoolean("complete",false).apply();
        if(wifiLock!=null&&wifiLock.isHeld())wifiLock.release();
        if(wakeLock!=null&&wakeLock.isHeld())wakeLock.release();
        getSharedPreferences("artitrade_background_health_v1",MODE_PRIVATE).edit()
            .putBoolean("service_running",false).putBoolean("wifi_lock",false)
            .putBoolean("cpu_lock",false).apply();
    }
    @Override public void onDestroy(){
        try{stopSafely();}
        finally{SERVICE_INSTANCE_ACTIVE=false;super.onDestroy();}
    }
    @Override public IBinder onBind(Intent intent){return null;}
}
