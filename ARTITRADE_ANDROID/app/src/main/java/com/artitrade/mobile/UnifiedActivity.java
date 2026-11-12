package com.artitrade.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.graphics.Color;
import android.graphics.Insets;
import android.os.Build;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.util.Base64;
import android.webkit.ConsoleMessage;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.content.Intent;
import android.net.Uri;
import android.provider.Settings;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * ARTITRADE unified mobile console.
 *
 * Fast-path design:
 * 1) preload every phone-hub snapshot for stock + coin in parallel,
 * 2) keep a single in-memory JSON cache,
 * 3) switch market/strategy/pages entirely inside WebView without network waits,
 * 4) refresh the cache in the background and push one atomic packet to the UI.
 *
 * The activity is a UI shell only. Broker sessions and strategy execution live in
 * UnifiedTradingService / the EXE-derived Python runtime so a WebView fault never
 * changes strategy state or order semantics.
 */
public final class UnifiedActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newFixedThreadPool(8);
    private final Object cacheLock = new Object();
    private FrameLayout root;
    private WebView web;
    private View dashboardCover;
    private JSONObject cache = new JSONObject();
    private volatile boolean dashboardReady = false;
    private volatile boolean refreshRunning = false;
    private volatile boolean pageCommitted = false;
    private volatile boolean runtimeStartIssued = false;
    private int webReloadAttempts = 0;
    private long preloadStarted;
    private static final int TOTAL_PRELOAD = 25;
    private static final long REFRESH_MS = 3500L;

    private final Runnable periodic = new Runnable() {
        @Override public void run() {
            refreshAll(false);
            main.postDelayed(this, REFRESH_MS);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try {
            getWindow().setStatusBarColor(Color.rgb(8,15,26));
            getWindow().setNavigationBarColor(Color.rgb(8,15,26));
            root = new FrameLayout(this);
            root.setBackgroundColor(Color.rgb(8,15,26));
            if (Build.VERSION.SDK_INT >= 30) {
                getWindow().setDecorFitsSystemWindows(false);
                WindowInsetsController controller=getWindow().getInsetsController();
                if(controller!=null)controller.setSystemBarsAppearance(0,
                    WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS |
                    WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
                root.setOnApplyWindowInsetsListener((v,insets)->{
                    Insets safe=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout());
                    v.setPadding(safe.left,safe.top,safe.right,safe.bottom);
                    return insets;
                });
            }
            setContentView(root);
            if(Build.VERSION.SDK_INT>=30) root.requestApplyInsets();
            showPreloader();
            // SAFEBOOT: the UI/preload comes up first. Real-time services are
            // started only after WebView is alive, so broker/network faults can
            // never close the launcher activity at startup.
            refreshAll(true);
        } catch(Throwable fatal) {
            new RuntimeCrashStore(this).add("activity_onCreate",fatal);
            showStartupRecovery(fatal);
        }
    }

    private int dp(float v){return (int)(v*getResources().getDisplayMetrics().density+.5f);}
    private TextView text(String value,int sp){
        TextView t=new TextView(this);t.setText(value);t.setTextColor(Color.WHITE);t.setTextSize(sp);return t;
    }

    private void startRuntimeService(){
        try{
            Intent i=new Intent(this,UnifiedTradingService.class);
            i.setAction(UnifiedTradingService.START);
            if(Build.VERSION.SDK_INT>=26)startForegroundService(i); else startService(i);
        }catch(Exception ignored){}
    }

    private void showPreloader(){
        root.removeAllViews();
        LinearLayout box=new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_VERTICAL);
        box.setPadding(dp(54),dp(28),dp(54),dp(28));
        root.addView(box,new FrameLayout.LayoutParams(-1,-1));

        TextView brand=text("ARTITRADE",24);
        brand.setLetterSpacing(.12f);
        brand.setTypeface(null,android.graphics.Typeface.BOLD);
        box.addView(brand);
        TextView title=text("주식 · 코인 통합 사전로딩",15);
        title.setTextColor(Color.rgb(154,178,209));
        title.setPadding(0,dp(9),0,dp(20));
        box.addView(title);

        ProgressBar bar=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);
        bar.setId(0x61710001);bar.setMax(100);bar.setProgress(0);
        box.addView(bar,new LinearLayout.LayoutParams(-1,dp(12)));

        TextView percent=text("0%",27);
        percent.setId(0x61710002);
        percent.setPadding(0,dp(15),0,0);
        percent.setTypeface(null,android.graphics.Typeface.BOLD);
        box.addView(percent);

        TextView status=text("폰 서버 연결 준비",13);
        status.setId(0x61710003);status.setTextColor(Color.rgb(173,197,224));
        status.setPadding(0,dp(6),0,0);box.addView(status);

        TextView eta=text("예상 완료시간 계산 중",11);
        eta.setId(0x61710004);eta.setTextColor(Color.rgb(116,143,178));
        eta.setPadding(0,dp(5),0,0);box.addView(eta);
        preloadStarted=System.currentTimeMillis();
    }

    private void updatePreload(int done,String label){
        if(!(!dashboardReady))return;
        int p=Math.min(100,(int)Math.round(done*100.0/TOTAL_PRELOAD));
        ProgressBar b=findViewById(0x61710001);
        TextView pct=findViewById(0x61710002),status=findViewById(0x61710003),eta=findViewById(0x61710004);
        if(b!=null)b.setProgress(p);
        if(pct!=null)pct.setText(p+"%");
        if(status!=null)status.setText(label);
        if(eta!=null){
            long elapsed=Math.max(1,System.currentTimeMillis()-preloadStarted);
            if(done>0&&done<TOTAL_PRELOAD){
                long remain=(long)((elapsed/(double)done)*(TOTAL_PRELOAD-done));
                eta.setText("예상 완료 "+Math.max(1,remain/1000)+"초");
            }else if(done>=TOTAL_PRELOAD)eta.setText("사전로딩 완료");
        }
    }

    private static final String[][] RECORDS={
        {"stock","shared","ledger","stockSharedLedger"},
        {"stock","shared","research","stockSharedResearch"},
        {"stock","shared","errors","stockSharedErrors"},
        {"stock","shared","settings","stockSharedSettings"},
        {"stock","at","ledger","stockAtLedger"},
        {"stock","at","research","stockAtResearch"},
        {"stock","at","errors","stockAtErrors"},
        {"stock","at","settings","stockAtSettings"},
        {"stock","cw","ledger","stockCwLedger"},
        {"stock","cw","research","stockCwResearch"},
        {"stock","cw","errors","stockCwErrors"},
        {"stock","cw","settings","stockCwSettings"},
        {"coin","shared","ledger","coinSharedLedger"},
        {"coin","shared","research","coinSharedResearch"},
        {"coin","shared","errors","coinSharedErrors"},
        {"coin","shared","settings","coinSharedSettings"},
        {"coin","general","ledger","coinGeneralLedger"},
        {"coin","general","research","coinGeneralResearch"},
        {"coin","general","errors","coinGeneralErrors"},
        {"coin","general","settings","coinGeneralSettings"},
        {"coin","ma","ledger","coinMaLedger"},
        {"coin","ma","research","coinMaResearch"},
        {"coin","ma","errors","coinMaErrors"},
        {"coin","ma","settings","coinMaSettings"}
    };

    private void refreshAll(boolean first){
        if(refreshRunning)return;
        refreshRunning=true;
        io.execute(()->{
            JSONObject packet=new JSONObject();
            JSONObject records=new JSONObject();
            LocalHubClient client=new LocalHubClient(this);
            AtomicInteger done=new AtomicInteger();
            try{
                JSONObject status;
                try{status=client.status();}catch(Exception e){status=new JSONObject().put("ok",false);}
                packet.put("status",status);
                packet.put("realtime",new RealtimeState(this).all());
                BrokerSecretStore brokerSecrets=new BrokerSecretStore(this);
                packet.put("credentials",new JSONObject()
                    .put("kiwoom",brokerSecrets.hasKiwoom())
                    .put("upbit",brokerSecrets.hasUpbit()));
                try{
                    String runtimeRaw=getSharedPreferences("artitrade_runtime_snapshot",MODE_PRIVATE).getString("snapshot","{}");
                    packet.put("strategy_runtime",new JSONObject(runtimeRaw));
                }catch(Exception ignored){packet.put("strategy_runtime",new JSONObject());}
                android.content.SharedPreferences livePrefs=getSharedPreferences("unified_live_orders",MODE_PRIVATE);
                packet.put("live_orders",new JSONObject()
                    .put("stock",livePrefs.getBoolean("live_stock",false))
                    .put("coin",livePrefs.getBoolean("live_coin",false)));
                packet.put("runtime_diagnostics",new RuntimeCrashStore(this).read());
                int n=done.incrementAndGet();
                if(first)main.post(()->updatePreload(n,"서버 상태 확인"));

                CountDownLatch latch=new CountDownLatch(RECORDS.length);
                for(String[] key:RECORDS){
                    io.execute(()->{
                        try{
                            JSONObject value;
                            try{value=client.record(key[0],key[1],key[2]);}
                            catch(Exception e){value=new JSONObject().put("ok",false);}
                            synchronized(records){records.put(key[3],value);}
                        }catch(Exception ignored){}
                        int d=done.incrementAndGet();
                        if(first)main.post(()->updatePreload(d,key[0].equals("stock")?"주식 원장 사전로딩":"코인 원장 사전로딩"));
                        latch.countDown();
                    });
                }
                latch.await(8,TimeUnit.SECONDS);
                packet.put("records",records);
                packet.put("loaded_at",System.currentTimeMillis());
            }catch(Exception ignored){}
            synchronized(cacheLock){cache=packet;}
            refreshRunning=false;
            main.post(()->{
                if(first&&!dashboardReady){
                    updatePreload(TOTAL_PRELOAD,"화면 구성");
                    main.postDelayed(this::showDashboard,120);
                }else if(dashboardReady){
                    pushCache();
                }
            });
        });
    }

    private void showDashboard(){
        dashboardReady=false;
        pageCommitted=false;
        root.removeAllViews();

        web=new WebView(this);
        // Samsung/DeX/Phone Link can occasionally leave a hardware-composited
        // WebView surface blank while the Activity itself stays alive. The
        // dashboard is UI-only, so software composition is the safer first paint.
        web.setLayerType(View.LAYER_TYPE_SOFTWARE,null);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        WebSettings s=web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(false);
        s.setAllowContentAccess(false);
        s.setAllowFileAccess(false);
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setJavaScriptCanOpenWindowsAutomatically(false);
        WebView.setWebContentsDebuggingEnabled(false);
        web.setBackgroundColor(Color.rgb(8,15,26));
        web.addJavascriptInterface(new Bridge(),"Artitrade");

        web.setWebChromeClient(new WebChromeClient(){
            @Override public boolean onConsoleMessage(ConsoleMessage msg){
                try{
                    if(msg!=null&&msg.messageLevel()==ConsoleMessage.MessageLevel.ERROR){
                        new RuntimeCrashStore(UnifiedActivity.this).add(
                            "web_console",
                            new IllegalStateException(msg.message()+" @"+msg.lineNumber())
                        );
                    }
                }catch(Throwable ignored){}
                return false;
            }
        });

        web.setWebViewClient(new WebViewClient(){
            @Override public void onPageCommitVisible(WebView view,String url){
                pageCommitted=true;
                updateDashboardCover("대시보드 첫 화면 확인 · 기능 준비 중");
            }

            @Override public void onPageFinished(WebView view,String url){
                // Do not start Python/broker engines merely because WebView reported
                // "finished". Confirm that the dashboard JavaScript is actually alive.
                main.postDelayed(()->probeDashboardReady(view),120);
            }

            @Override public void onReceivedError(WebView view,WebResourceRequest req,WebResourceError err){
                if(req!=null&&req.isForMainFrame()){
                    String detail=err==null?"main frame load error":String.valueOf(err.getDescription());
                    new RuntimeCrashStore(UnifiedActivity.this).add("web_main_frame",
                        new IllegalStateException(detail));
                    showWebFailure("대시보드 로드 실패",detail);
                }
            }

            @Override public boolean onRenderProcessGone(WebView view,RenderProcessGoneDetail detail){
                String why=(detail!=null&&detail.didCrash())?"WebView renderer crash":"WebView renderer killed";
                new RuntimeCrashStore(UnifiedActivity.this).add("web_renderer",
                    new IllegalStateException(why));
                try{
                    if(view!=null){root.removeView(view);view.destroy();}
                }catch(Throwable ignored){}
                if(web==view)web=null;
                dashboardReady=false;
                pageCommitted=false;
                webReloadAttempts++;
                if(webReloadAttempts<=1){
                    showPreloader();
                    updatePreload(1,"화면 렌더러 자동 복구");
                    main.postDelayed(UnifiedActivity.this::showDashboard,650);
                }else{
                    showWebFailure("대시보드 렌더러 복구 실패",why);
                }
                return true;
            }
        });

        root.addView(web,new FrameLayout.LayoutParams(-1,-1));
        dashboardCover=createDashboardCover();
        root.addView(dashboardCover,new FrameLayout.LayoutParams(-1,-1));

        try(InputStream in=getAssets().open("unified_dashboard.html")){
            ByteArrayOutputStream out=new ByteArrayOutputStream();
            byte[] b=new byte[8192];int n;
            while((n=in.read(b))!=-1)out.write(b,0,n);
            byte[] htmlBytes=out.toByteArray();
            String encoded=Base64.encodeToString(htmlBytes,Base64.NO_WRAP);
            // A base64 data document avoids asset/file-origin policy differences
            // between Samsung WebView, DeX and Phone Link.
            web.loadData(encoded,"text/html","base64");
        }catch(Throwable e){
            new RuntimeCrashStore(this).add("dashboard_asset",e);
            showWebFailure("통합 대시보드 로드 실패",e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()));
            return;
        }

        main.postDelayed(()->{
            if(!dashboardReady&&web!=null){
                String stage=pageCommitted?"JavaScript 준비 지연":"첫 화면 표시 지연";
                new RuntimeCrashStore(UnifiedActivity.this).add("web_ready_timeout",
                    new IllegalStateException(stage));
                showWebFailure("대시보드 표시 지연",stage+" · 다시 열기를 누르면 엔진을 건드리지 않고 UI만 재생성합니다.");
            }
        },8000);
    }

    private View createDashboardCover(){
        LinearLayout box=new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(dp(36),dp(24),dp(36),dp(24));
        box.setBackgroundColor(Color.rgb(8,15,26));
        TextView brand=text("ARTITRADE",24);
        brand.setTypeface(null,android.graphics.Typeface.BOLD);
        brand.setLetterSpacing(.12f);
        box.addView(brand);
        TextView state=text("대시보드 화면 불러오는 중",14);
        state.setId(0x61710011);
        state.setTextColor(Color.rgb(173,197,224));
        state.setPadding(0,dp(12),0,dp(14));
        box.addView(state);
        ProgressBar spin=new ProgressBar(this);
        box.addView(spin,new LinearLayout.LayoutParams(dp(34),dp(34)));
        TextView note=text("화면 확인 후 주식·코인 엔진을 지연 시작합니다.",11);
        note.setTextColor(Color.rgb(116,143,178));
        note.setPadding(0,dp(14),0,0);
        box.addView(note);
        return box;
    }

    private void updateDashboardCover(String msg){
        if(dashboardCover==null)return;
        TextView t=dashboardCover.findViewById(0x61710011);
        if(t!=null)t.setText(msg);
    }

    private void probeDashboardReady(WebView target){
        if(target==null||target!=web||dashboardReady)return;
        try{
            target.evaluateJavascript(
                "(typeof window.__ARTI_SNAPSHOT==='function' && !!document.getElementById('view'))",
                value->{
                    if("true".equals(String.valueOf(value)))markDashboardReady("js_probe");
                    else main.postDelayed(()->{
                        if(target==web&&!dashboardReady){
                            try{target.evaluateJavascript(
                                "(typeof window.__ARTI_SNAPSHOT==='function' && !!document.getElementById('view'))",
                                v->{if("true".equals(String.valueOf(v)))markDashboardReady("js_probe_retry");}
                            );}catch(Throwable e){new RuntimeCrashStore(UnifiedActivity.this).add("web_probe_retry",e);}
                        }
                    },450);
                }
            );
        }catch(Throwable e){
            new RuntimeCrashStore(this).add("web_probe",e);
        }
    }

    private void markDashboardReady(String source){
        if(dashboardReady)return;
        dashboardReady=true;
        webReloadAttempts=0;
        if(dashboardCover!=null){
            try{root.removeView(dashboardCover);}catch(Throwable ignored){}
            dashboardCover=null;
        }
        pushCache();
        if(!runtimeStartIssued){
            runtimeStartIssued=true;
            main.postDelayed(()->{
                try{startRuntimeService();}
                catch(Throwable e){new RuntimeCrashStore(UnifiedActivity.this).add("delayed_runtime_start_"+source,e);}
            },1800);
        }
    }

    private void showWebFailure(String title,String detail){
        dashboardReady=false;
        if(web!=null){
            try{root.removeView(web);web.stopLoading();web.destroy();}catch(Throwable ignored){}
            web=null;
        }
        root.removeAllViews();
        LinearLayout panel=new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER_VERTICAL);
        panel.setPadding(dp(34),dp(20),dp(34),dp(20));
        panel.addView(text("ARTITRADE 화면 안전복구",22));
        TextView h=text(title,14);h.setTextColor(Color.rgb(255,190,120));h.setPadding(0,dp(10),0,dp(6));panel.addView(h);
        TextView d=text(detail==null?"상세 미기록":detail,11);d.setTextColor(Color.rgb(181,202,225));panel.addView(d);
        Button retry=button("UI만 다시 열기",()->{
            try{showDashboard();}
            catch(Throwable e){new RuntimeCrashStore(UnifiedActivity.this).add("web_manual_retry",e);showStartupRecovery(e);}
        });
        retry.setPadding(dp(12),dp(8),dp(12),dp(8));
        panel.addView(retry);
        TextView engine=text("화면 오류만으로 실시간 엔진/원장 상태를 임의 변경하지 않습니다.",10);
        engine.setTextColor(Color.rgb(116,143,178));engine.setPadding(0,dp(10),0,0);panel.addView(engine);
        root.addView(panel,new FrameLayout.LayoutParams(-1,-1));
    }

    private void pushCache(){
        if(web==null)return;
        JSONObject copy;
        synchronized(cacheLock){try{copy=new JSONObject(cache.toString());}catch(Exception e){copy=new JSONObject();}}
        String quoted=JSONObject.quote(copy.toString());
        web.evaluateJavascript("window.__ARTI_SNAPSHOT("+quoted+");",null);
    }

    private void showStartupRecovery(Throwable error){
        if(root==null){
            root=new FrameLayout(this);
            root.setBackgroundColor(Color.rgb(8,15,26));
            try{setContentView(root);}catch(Throwable ignored){}
        }
        root.removeAllViews();
        LinearLayout panel=new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER_VERTICAL);
        panel.setPadding(dp(30),dp(18),dp(30),dp(18));
        panel.addView(text("ARTITRADE 안전 시작 모드",21));
        TextView note=text("앱 시작 중 일부 구성요소가 실패했습니다. 앱은 종료하지 않고 진단 모드로 유지합니다.",13);
        note.setTextColor(Color.rgb(181,202,225));note.setPadding(0,dp(10),0,dp(12));panel.addView(note);
        TextView reason=text((error==null?"알 수 없는 오류":error.getClass().getSimpleName()+": "+String.valueOf(error.getMessage())),11);
        reason.setTextColor(Color.rgb(255,145,155));panel.addView(reason);
        Button retry=button("대시보드 다시 열기",()->{
            try{showPreloader();refreshAll(true);}
            catch(Throwable e){new RuntimeCrashStore(this).add("safe_retry",e);}
        });
        panel.addView(retry);
        root.addView(panel,new FrameLayout.LayoutParams(-1,-1));
    }

    private Button button(String value,Runnable action){
        Button b=new Button(this);b.setText(value);b.setOnClickListener(v->action.run());return b;
    }

    private void showFatal(String msg){
        root.removeAllViews();
        LinearLayout p=new LinearLayout(this);p.setOrientation(LinearLayout.VERTICAL);p.setGravity(Gravity.CENTER);
        p.addView(text(msg,18));root.addView(p,new FrameLayout.LayoutParams(-1,-1));
    }

    private final class Bridge {
        @JavascriptInterface public void uiReady(){main.post(()->markDashboardReady("js_bridge"));}
        @JavascriptInterface public void refresh(){refreshAll(false);}
        @JavascriptInterface public void pair(){
            main.post(()->{
                EditText input=new EditText(UnifiedActivity.this);
                input.setSingleLine(true);
                input.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
                input.setHint("폰 서버 64자리 연결 토큰");
                new AlertDialog.Builder(UnifiedActivity.this).setTitle("폰 서버 연결")
                    .setView(input).setNegativeButton("취소",null)
                    .setPositiveButton("저장",(d,w)->{
                        try{
                            new LocalHubClient(UnifiedActivity.this).storePairingToken(input.getText().toString().trim());
                            dashboardReady=false;showPreloader();refreshAll(true);
                        }catch(Exception e){new AlertDialog.Builder(UnifiedActivity.this).setMessage("64자리 연결 토큰을 확인하십시오.").setPositiveButton("확인",null).show();}
                    }).show();
            });
        }
        @JavascriptInterface public void runtimeSettings(){
            main.post(()->{
                Intent i=new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
                try{startActivity(i);}catch(Exception ignored){}
            });
        }
        @JavascriptInterface public void apiSettings(){
            main.post(()->{
                BrokerSecretStore store=new BrokerSecretStore(UnifiedActivity.this);
                LinearLayout form=new LinearLayout(UnifiedActivity.this);
                form.setOrientation(LinearLayout.VERTICAL);
                form.setPadding(dp(12),dp(4),dp(12),dp(4));
                EditText kk=new EditText(UnifiedActivity.this),ks=new EditText(UnifiedActivity.this),
                    ua=new EditText(UnifiedActivity.this),us=new EditText(UnifiedActivity.this);
                kk.setHint(store.hasKiwoom()?"키움 App Key · 저장됨":"키움 App Key");
                ks.setHint(store.hasKiwoom()?"키움 Secret Key · 저장됨":"키움 Secret Key");
                ua.setHint(store.hasUpbit()?"업비트 Access Key · 저장됨":"업비트 Access Key");
                us.setHint(store.hasUpbit()?"업비트 Secret Key · 저장됨":"업비트 Secret Key");
                for(EditText v:new EditText[]{kk,ks,ua,us}){
                    v.setSingleLine(true);
                    v.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
                    form.addView(v,new LinearLayout.LayoutParams(-1,dp(48)));
                }
                new AlertDialog.Builder(UnifiedActivity.this).setTitle("S21+ 직접 API 설정")
                    .setMessage("빈 칸은 기존 값을 유지합니다. 키는 APK나 폰 서버 원장에 기록하지 않고 Android Keystore로 암호화합니다.")
                    .setView(form).setNegativeButton("취소",null)
                    .setNeutralButton("전체 삭제",(d,w)->{
                        store.saveKiwoom("","");store.saveUpbit("","");
                        restartDirectFeeds();refreshAll(false);
                    })
                    .setPositiveButton("저장",(d,w)->{
                        String k1=kk.getText().toString().trim(),k2=ks.getText().toString().trim();
                        String u1=ua.getText().toString().trim(),u2=us.getText().toString().trim();
                        if(!k1.isEmpty()||!k2.isEmpty()){
                            if(k1.isEmpty()||k2.isEmpty()){
                                new AlertDialog.Builder(UnifiedActivity.this).setMessage("키움 App Key와 Secret Key를 함께 입력하십시오.").setPositiveButton("확인",null).show();
                                return;
                            }
                            store.saveKiwoom(k1,k2);
                        }
                        if(!u1.isEmpty()||!u2.isEmpty()){
                            if(u1.isEmpty()||u2.isEmpty()){
                                new AlertDialog.Builder(UnifiedActivity.this).setMessage("업비트 Access Key와 Secret Key를 함께 입력하십시오.").setPositiveButton("확인",null).show();
                                return;
                            }
                            store.saveUpbit(u1,u2);
                        }
                        restartDirectFeeds();refreshAll(false);
                    }).show();
            });
        }
        @JavascriptInterface public void setLiveTrading(String market,boolean enabled){
            if(!("stock".equals(market)||"coin".equals(market)))return;
            main.post(()->{
                Runnable apply=()->{
                    try{
                        Intent i=new Intent(UnifiedActivity.this,UnifiedTradingService.class);
                        i.setAction(UnifiedTradingService.SET_LIVE);
                        i.putExtra("market",market);
                        i.putExtra("enabled",enabled);
                        if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);
                        main.postDelayed(()->refreshAll(false),900);
                    }catch(Throwable e){new RuntimeCrashStore(UnifiedActivity.this).add("ui_live_toggle_"+market,e);}
                };
                if(!enabled){apply.run();return;}
                String name="stock".equals(market)?"주식":"코인";
                new AlertDialog.Builder(UnifiedActivity.this)
                    .setTitle(name+" 실거래 자동주문 ON")
                    .setMessage("최신 EXE 전략 신호가 실제 계좌 주문으로 전송됩니다. FOK 전량체결만 전략 원장에 반영되며, 부분체결·응답불명은 자동 정지합니다.")
                    .setNegativeButton("취소",null)
                    .setPositiveButton("실거래 ON",(d,w)->apply.run()).show();
            });
        }
        @JavascriptInterface public void setDesired(String market,String desired){
            if(!("stock".equals(market)||"coin".equals(market)))return;
            if(!("pc".equals(desired)||"phone".equals(desired)||"off".equals(desired)))return;
            io.execute(()->{
                try{new LocalHubClient(UnifiedActivity.this).control(market,desired);}catch(Exception ignored){}
                refreshAll(false);
            });
        }
        @JavascriptInterface public String operationConfig(String market,String strategy){
            try{
                String key=market+"_"+strategy;
                String raw=getSharedPreferences("unified_operation",MODE_PRIVATE).getString(key,"{}");
                return new JSONObject(raw).toString();
            }catch(Exception e){return "{}";}
        }
        @JavascriptInterface public void saveOperation(String market,String strategy,String seedRaw,String maxRaw){
            if(!("stock".equals(market)||"coin".equals(market)))return;
            try{
                long seed=Long.parseLong(seedRaw),max=Long.parseLong(maxRaw);
                if(seed<0||max<0||max>200)return;
                JSONObject value=new JSONObject().put("seed",seed).put("max_hold",max).put("saved_at",System.currentTimeMillis());
                getSharedPreferences("unified_operation",MODE_PRIVATE).edit()
                    .putString(market+"_"+strategy,value.toString()).apply();
            }catch(Exception ignored){}
        }
    }

    private void restartDirectFeeds(){
        try{
            Intent i=new Intent(this,UnifiedTradingService.class);
            i.setAction(UnifiedTradingService.RESTART_FEEDS);
            if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);
        }catch(Exception ignored){}
    }

    @Override protected void onResume(){
        super.onResume();
        main.removeCallbacks(periodic);
        main.postDelayed(periodic,REFRESH_MS);
    }
    @Override protected void onPause(){
        main.removeCallbacks(periodic);
        super.onPause();
    }
    @Override public void onDestroy(){
        main.removeCallbacks(periodic);
        io.shutdownNow();
        if(web!=null){web.destroy();web=null;}
        super.onDestroy();
    }
}
