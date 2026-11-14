package com.artitrade.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.provider.DocumentsContract;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.animation.TranslateAnimation;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.PopupMenu;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.OutputStream;
import java.io.InputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.security.MessageDigest;
import com.chaquo.python.Python;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.text.NumberFormat;
import java.text.SimpleDateFormat;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Date;
import java.util.HashMap;
import java.util.Map;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * ARTITRADE S21+ unified native console.
 *
 * Deliberately uses no WebView. The first frame, strategy tabs, account metrics,
 * lists, research, diagnostics and settings are all Android native Views.
 * Strategy execution remains in UnifiedTradingService / EXE-derived Python core.
 */
public final class UnifiedVisualActivity extends Activity {
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService io=Executors.newFixedThreadPool(6);
    // A refresh builds a new immutable-for-read snapshot on a worker thread.
    // Never stringify and reparse the entire imported PC ledger on the UI thread.
    private static final class HistoryRow {
        final JSONObject row;
        final String source;
        final String frame;
        final long at;
        HistoryRow(JSONObject row,String source,String frame,long at){
            this.row=row;this.source=source;this.frame=frame;this.at=at;
        }
    }
    private static final class UiSnapshot {
        final JSONObject packet;
        final Map<String,Stats> totals;
        final Map<String,Stats> frames;
        final Map<String,java.util.List<HistoryRow>> histories;
        final Map<String,RealizedTrend.Series> trends;
        final Map<String,ResearchScoreGrid> researchGrids;
        final long preloadMs;
        final int preloadRows;
        final boolean reused;
        UiSnapshot(JSONObject packet,Map<String,Stats> totals,Map<String,Stats> frames,
                   Map<String,java.util.List<HistoryRow>> histories,
                   Map<String,RealizedTrend.Series> trends,
                   Map<String,ResearchScoreGrid> researchGrids,
                   long preloadMs,int preloadRows,boolean reused){
            this.packet=packet;
            this.totals=java.util.Collections.unmodifiableMap(totals);
            this.frames=java.util.Collections.unmodifiableMap(frames);
            this.histories=java.util.Collections.unmodifiableMap(histories);
            this.trends=java.util.Collections.unmodifiableMap(trends);
            this.researchGrids=java.util.Collections.unmodifiableMap(researchGrids);
            this.preloadMs=preloadMs;
            this.preloadRows=preloadRows;
            this.reused=reused;
        }
    }
    private volatile UiSnapshot uiSnapshot=new UiSnapshot(new JSONObject(),new HashMap<>(),
        new HashMap<>(),new HashMap<>(),new HashMap<>(),new HashMap<>(),
        0L,0,false);
    // Worker-only parsed record reuse. The service publishes a fresh JSON string
    // frequently, even when all historical records are unchanged.
    private String cachedLocalRaw;
    private JSONObject cachedLocalParsed=new JSONObject();
    private boolean cachedLocalMeaningful;
    // One strategy having live rows must not suppress fallback for other
    // missing strategies. Poll only source-specific gaps and never replace local truth.
    private long lastHubGapPollMs=0L;
    private JSONObject cachedHubGapRecords=new JSONObject();
    private FrameLayout root;
    private LinearLayout content;
    private LinearLayout strategyBar;
    private TextView statusText;
    private TextView clockText;
    private volatile boolean refreshRunning=false;
    private volatile boolean runtimeStartIssued=false;
    private volatile boolean brokerProbeIssued=false;
    // Keep the first visible frame as a real preparation screen, not an empty main UI.
    private volatile boolean preloadScreenReady=false;
    private long preloadStartedAt=0L;
    private ProgressBar startupProgress;
    private TextView startupStage;
    private String market="stock";
    private String policyTargetCode="";
    private String policyTargetStrategy="";
    private String policyTargetMarket="";
    private String strategy="at";
    private String page="home";
    private boolean integratedMode=false;
    private volatile boolean aiAnalysisRunning=false;
    private JSONObject aiAnalysisReport=null;
    private String aiAnalysisScope="";
    private String aiAnalysisError="";
    private String historyFilterStock="current",historyFilterCoin="current";
    // History is never truncated; only the visible Android Views are paged.
    private String historyContext="";
    private int historyOffset=0;
    private String lastTappedSymbol="";
    private int consecutiveSymbolTaps=0;
    private long lastSymbolTapMs=0L;
    private float swipeX=Float.NaN;
    private float swipeY=Float.NaN;
    private View drawerOverlay;
    private static final long DRAWER_PROGRESS_REFRESH_MS=750L;
    private static final class DrawerProgressViews {
        final TextView percent;
        final ProgressBar bar;
        final TextView detail;
        DrawerProgressViews(TextView percent,ProgressBar bar,TextView detail){
            this.percent=percent;this.bar=bar;this.detail=detail;
        }
    }
    private DrawerProgressViews drawerPublishProgress;
    private DrawerProgressViews drawerImportProgress;
    // Refresh only the visible progress widgets, not the whole drawer or its scroll position.
    // Import and publish progress are stored locally by the service after each completed unit.
    private final Runnable drawerProgressTicker=new Runnable(){
        @Override public void run(){
            if(drawerOverlay==null)return;
            refreshOpenDrawerProgress();
            main.postDelayed(this,DRAWER_PROGRESS_REFRESH_MS);
        }
    };
    // Per-table presentation choices only; original strategy rows remain unchanged.
    private final Map<String,ColumnQueryV036> uiTableQueries=new HashMap<>();
    private final Map<String,EditText[]> operationFields=new HashMap<>();
    // Unsaved drafts are isolated from the live strategy settings. Only Save applies them.
    private final Map<String,String> operationDraftValues=new HashMap<>();
    private boolean operationDraftLoaded=false;
    private static final int REQUEST_EXPORT_RESEARCH=7711;
    private static final int REQUEST_EXPORT_FULL_LEDGER=7712;
    private static final int REQUEST_EXPORT_COMBINED_RESEARCH=7713;
    private final java.util.concurrent.atomic.AtomicBoolean fullExportRunning=
        new java.util.concurrent.atomic.AtomicBoolean(false);
    private byte[] pendingResearchExport;
    private String pendingResearchReports;
    private String pendingCombinedResearchScope;
    private static final long REFRESH_MS=3500L;

    private final Runnable periodic=new Runnable(){
        @Override public void run(){
            refresh(false);
            updateClock();
            main.postDelayed(this,REFRESH_MS);
        }
    };

    private static final String[][] RECORDS={
        {"stock","shared","ledger","stockSharedLedger"},
        {"stock","shared","errors","stockSharedErrors"},
        {"stock","at","ledger","stockAtLedger"},
        {"stock","at","research","stockAtResearch"},
        {"stock","at","errors","stockAtErrors"},
        {"stock","at","settings","stockAtSettings"},
        {"stock","cw","ledger","stockCwLedger"},
        {"stock","cw","research","stockCwResearch"},
        {"stock","cw","errors","stockCwErrors"},
        {"stock","cw","settings","stockCwSettings"},
        {"coin","shared","ledger","coinSharedLedger"},
        {"coin","shared","errors","coinSharedErrors"},
        {"coin","general","ledger","coinGeneralLedger"},
        {"coin","general","research","coinGeneralResearch"},
        {"coin","general","errors","coinGeneralErrors"},
        {"coin","general","settings","coinGeneralSettings"},
        {"coin","ma","ledger","coinMaLedger"},
        {"coin","ma","research","coinMaResearch"},
        {"coin","ma","errors","coinMaErrors"},
        {"coin","ma","settings","coinMaSettings"}
    };

    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        restoreNavigation(state);
        try{
            getWindow().setStatusBarColor(PremiumUi.BG);
            getWindow().setNavigationBarColor(PremiumUi.BG);
            root=new FrameLayout(this);
            root.setBackgroundColor(PremiumUi.BG);
            setContentView(root);
            preloadStartedAt=android.os.SystemClock.elapsedRealtime();
            showBoot();
            // First frame is the real preloader. Service and all UI model parsing
            // happen asynchronously while the main dashboard stays hidden.
            main.postDelayed(()->{
                try{
                    updateStartupProgress(12,"백그라운드 전략엔진 시작");
                    startRuntimeLater();
                    refresh(true);
                }catch(Throwable e){
                    new RuntimeCrashStore(this).add("native_preload",e);
                    showFatal(e);
                }
            },120);
        }catch(Throwable e){
            try{new RuntimeCrashStore(this).add("native_onCreate",e);}catch(Throwable ignored){}
            showFatal(e);
        }
    }

    private void restoreNavigation(Bundle saved){
        android.content.SharedPreferences prefs=getSharedPreferences("artitrade_ui_navigation_v1",MODE_PRIVATE);
        market=saved==null?prefs.getString("market","stock"):saved.getString("market",prefs.getString("market","stock"));
        strategy=saved==null?prefs.getString("strategy","at"):saved.getString("strategy",prefs.getString("strategy","at"));
        page=saved==null?prefs.getString("page","home"):saved.getString("page",prefs.getString("page","home"));
        integratedMode=saved==null?prefs.getBoolean("integrated_mode",false):
            saved.getBoolean("integrated_mode",prefs.getBoolean("integrated_mode",false));
        if(integratedMode&&!("home".equals(page)||"positions".equals(page)))
            page="home";
        // v2 keys deliberately reset the legacy v0.7.17 global 'all' default.
        historyFilterStock=prefs.getString("stock_filter_v2","current");
        historyFilterCoin=prefs.getString("coin_filter_v2","current");
        if(!"stock".equals(market)&&!"coin".equals(market))market="stock";
        boolean validStrategy=false;
        for(String[] item:strategyList(market))if(item[0].equals(strategy))validStrategy=true;
        if(!validStrategy)strategy="stock".equals(market)?"at":"general";
        java.util.HashSet<String> validPages=new java.util.HashSet<>(
            java.util.Arrays.asList("home","positions","pending","executions",
                "research","analysis","operation","scores","errors","policyHistory","market","more"));
        if(!validPages.contains(page))page="home";
    }

    private void persistNavigation(){
        getSharedPreferences("artitrade_ui_navigation_v1",MODE_PRIVATE).edit()
            .putString("market",market).putString("strategy",strategy).putString("page",page)
            .putBoolean("integrated_mode",integratedMode)
            .putString("stock_filter_v2",historyFilterStock)
            .putString("coin_filter_v2",historyFilterCoin)
            .apply();
    }

    @Override protected void onSaveInstanceState(Bundle state){
        state.putString("market",market);
        state.putString("strategy",strategy);
        state.putString("page",page);
        state.putBoolean("integrated_mode",integratedMode);
        persistNavigation();
        super.onSaveInstanceState(state);
    }

    private int dp(float v){return (int)(v*getResources().getDisplayMetrics().density+.5f);}

    private TextView text(String value,int sp){
        TextView t=new TextView(this);
        t.setText(value);
        t.setTextSize(Math.max(12,sp));
        t.setTextColor(colorByStatus(value,PremiumUi.TEXT));
        t.setGravity(Gravity.CENTER_VERTICAL);
        t.setIncludeFontPadding(false);
        t.setLineSpacing(dp(2),1.0f);
        return t;
    }

    private void autoSingle(TextView t,int minSp,int maxSp){
        t.setSingleLine(true);
        if(Build.VERSION.SDK_INT>=26)t.setAutoSizeTextTypeUniformWithConfiguration(minSp,maxSp,1,TypedValue.COMPLEX_UNIT_SP);
    }

    private GradientDrawable bg(int color,int stroke,int radius){
        GradientDrawable d=new GradientDrawable();
        d.setColor(color);d.setCornerRadius(dp(radius));
        if(stroke!=0)d.setStroke(dp(1),stroke);
        return d;
    }

    private int accent(){return "stock".equals(market)?PremiumUi.STOCK:PremiumUi.COIN;}

    private Button button(String label,boolean active,Runnable action){
        Button b=new Button(this);
        b.setAllCaps(false);
        b.setText(label);
        b.setTextSize(13);
        b.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        boolean dangerous=label.contains("강제매도")||label.equals("실거래 ON");
        b.setTextColor(active?PremiumUi.BG:dangerous?PremiumUi.LOSS:PremiumUi.TEXT);
        b.setBackground(PremiumUi.rounded(this,
            active?accent():PremiumUi.SURFACE_HIGH,
            active?accent():dangerous?PremiumUi.LOSS:PremiumUi.LINE,12));
        b.setPadding(dp(10),0,dp(10),0);
        b.setMinHeight(0);b.setMinimumHeight(0);
        b.setOnClickListener(v->action.run());
        PremiumUi.tapMotion(b);
        return b;
    }

    private LinearLayout card(){
        LinearLayout c=new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(10),dp(8),dp(10),dp(8));
        c.setBackground(bg(PremiumUi.SURFACE,PremiumUi.LINE,13));
        return c;
    }

    private TextView segmentItem(String label,boolean active,Runnable action){
        TextView t=text(label,13);
        t.setGravity(Gravity.CENTER);
        t.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        t.setTextColor(active?accent():PremiumUi.MUTED);
        t.setBackground(PremiumUi.rounded(this,
            active?PremiumUi.SURFACE_HIGH:Color.TRANSPARENT,
            active?accent():Color.TRANSPARENT,11));
        t.setOnClickListener(v->action.run());
        PremiumUi.tapMotion(t);
        return t;
    }

    private TextView strategyTab(String label,boolean active,Runnable action){
        TextView t=text(label,14);
        t.setGravity(Gravity.CENTER);
        t.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        t.setTextColor(active?accent():PremiumUi.MUTED);
        t.setBackground(PremiumUi.rounded(this,
            active?PremiumUi.SURFACE_HIGH:Color.TRANSPARENT,
            active?accent():Color.TRANSPARENT,11));
        t.setOnClickListener(v->action.run());
        PremiumUi.tapMotion(t);
        return t;
    }

    private View iconAction(String glyph,Runnable action){
        PremiumIconView v=new PremiumIconView(this,
            "☰".equals(glyph)?"settings":"×".equals(glyph)?"close":"refresh",
            PremiumUi.TEXT);
        v.setBackground(PremiumUi.rounded(this,PremiumUi.SURFACE,PremiumUi.LINE,12));
        v.setContentDescription("☰".equals(glyph)?"설정 열기":"×".equals(glyph)?"닫기":"새로고침");
        v.setOnClickListener(x->action.run());
        PremiumUi.tapMotion(v);
        return v;
    }

    private LinearLayout bottomNavItem(String key,String unusedGlyph,String label){
        boolean active=key.equals(page);
        int color=active?accent():PremiumUi.MUTED;
        LinearLayout box=new LinearLayout(this);
        box.setOrientation(LinearLayout.HORIZONTAL);box.setGravity(Gravity.CENTER);
        box.setPadding(dp(2),dp(3),dp(2),dp(3));
        box.setBackground(PremiumUi.rounded(this,
            active?PremiumUi.SURFACE_HIGH:Color.TRANSPARENT,
            active?accent():Color.TRANSPARENT,11));
        PremiumIconView glyph=new PremiumIconView(this,key,color);
        box.addView(glyph,new LinearLayout.LayoutParams(dp(18),dp(25)));
        TextView caption=text(label,11);caption.setGravity(Gravity.CENTER);
        caption.setSingleLine(true);
        caption.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        caption.setTextColor(active?PremiumUi.TEXT:PremiumUi.MUTED);
        LinearLayout.LayoutParams capLp=new LinearLayout.LayoutParams(-2,dp(30));
        capLp.setMargins(dp(2),0,0,0);box.addView(caption,capLp);
        box.setContentDescription(label+(active?" · 현재 화면":" · 탭하여 이동"));
        box.setOnClickListener(v->{page=key;persistNavigation();showShell();});
        PremiumUi.tapMotion(box);
        return box;
    }

    private View divider(){
        View v=new View(this);v.setBackgroundColor(Color.rgb(35,39,53));return v;
    }

    private void showBoot(){
        root.removeAllViews();
        LinearLayout p=new LinearLayout(this);
        p.setOrientation(LinearLayout.VERTICAL);p.setGravity(Gravity.CENTER);
        p.setPadding(dp(32),dp(24),dp(32),dp(24));
        p.setBackgroundColor(PremiumUi.BG);
        TextView brand=text("ARTITRADE",28);brand.setTypeface(null,Typeface.BOLD);brand.setLetterSpacing(.12f);p.addView(brand);
        TextView sub=text("S21+ PHONE MAIN · NATIVE SAFEBOOT",12);sub.setTextColor(Color.rgb(139,164,194));sub.setPadding(0,dp(8),0,dp(18));p.addView(sub);
        startupProgress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);
        startupProgress.setIndeterminate(false);startupProgress.setMax(100);startupProgress.setProgress(5);
        p.addView(startupProgress,new LinearLayout.LayoutParams(dp(320),dp(10)));
        startupStage=text("원장 · 전략별 요약 · 화면 자료를 준비 중",12);
        startupStage.setTextColor(Color.rgb(116,143,178));
        startupStage.setPadding(0,dp(14),0,0);p.addView(startupStage);
        TextView note=text("거래·시세·원장 상태를 백그라운드에서 준비한 뒤 메인화면을 엽니다.",11);
        note.setTextColor(PremiumUi.MUTED);note.setPadding(0,dp(7),0,0);p.addView(note);
        root.addView(p,new FrameLayout.LayoutParams(-1,-1));
    }

    private void updateStartupProgress(int percent,String stage){
        main.post(()->{
            if(preloadScreenReady)return;
            if(startupProgress!=null)
                startupProgress.setProgress(Math.max(startupProgress.getProgress(),
                    Math.min(98,Math.max(0,percent))));
            if(startupStage!=null)startupStage.setText(stage);
        });
    }

    private void finishPreload(boolean engineReady){
        if(preloadScreenReady)return;
        preloadScreenReady=true;
        if(startupProgress!=null)startupProgress.setProgress(100);
        startupProgress=null;startupStage=null;
        showShell(); // showShell already renders the prepared snapshot once.
        updateHeader();
        if(!engineReady && statusText!=null)
            statusText.setText("원장 초기화 대기 · 백그라운드 연결 확인");
        main.postDelayed(this::autoProbeBroker,900);
    }

    private String marketVersionLabel(){
        return integratedMode?"v0.7.40 · 통합":("stock".equals(market)?"v0.7.40 · 주식":"v0.7.40 · 코인");
    }

    private void showShell(){
        root.removeAllViews();
        drawerOverlay=null;
        stopDrawerProgressTicker();
        LinearLayout shell=new LinearLayout(this);shell.setOrientation(LinearLayout.VERTICAL);shell.setBackgroundColor(PremiumUi.BG);
        root.addView(shell,new FrameLayout.LayoutParams(-1,-1));

        final boolean wideHeader=getResources().getConfiguration().screenWidthDp>=720;
        LinearLayout top=new LinearLayout(this);top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.setPadding(dp(10),dp(3),dp(7),dp(3));
        LinearLayout brandBox=new LinearLayout(this);
        brandBox.setOrientation(LinearLayout.VERTICAL);brandBox.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout brandLine=new LinearLayout(this);brandLine.setOrientation(LinearLayout.HORIZONTAL);
        brandLine.setGravity(Gravity.CENTER_VERTICAL);
        TextView brand=text("ARTITRADE",18);
        brand.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
        brand.setLetterSpacing(.065f);
        brandLine.addView(brand);
        if(wideHeader){
            TextView ver=text("  ·  "+marketVersionLabel(),12);
            ver.setTextColor(accent());
            brandLine.addView(ver,new LinearLayout.LayoutParams(-2,dp(26)));
        }
        brandBox.addView(brandLine,new LinearLayout.LayoutParams(-1,dp(26)));
        statusText=text("● 연결 확인 중",12);
        statusText.setSingleLine(true);
        statusText.setEllipsize(android.text.TextUtils.TruncateAt.END);
        statusText.setTextColor(PremiumUi.MUTED);
        brandBox.addView(statusText,new LinearLayout.LayoutParams(-1,dp(19)));
        top.addView(brandBox,new LinearLayout.LayoutParams(0,dp(46),1));


        // Keep the brand/status/header readable on narrow phones.
        clockText=text("",12);
        clockText.setTextColor(PremiumUi.MUTED);
        clockText.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams clockLp=new LinearLayout.LayoutParams(
            dp(wideHeader?115:59),dp(38));
        clockLp.setMargins(dp(6),0,dp(4),0);
        top.addView(clockText,clockLp);
        top.addView(iconAction("☰",this::openSettingsDrawer),
            new LinearLayout.LayoutParams(dp(43),dp(43)));
        shell.addView(top,new LinearLayout.LayoutParams(-1,dp(51)));

        // The four main destinations occupy their own full-width row.
        LinearLayout marketSwitch=new LinearLayout(this);
        marketSwitch.setOrientation(LinearLayout.HORIZONTAL);
        marketSwitch.setPadding(dp(3),dp(3),dp(3),dp(3));
        marketSwitch.setBackground(bg(PremiumUi.SURFACE,PremiumUi.LINE,14));
        TextView marketTab=segmentItem("시장","market".equals(page)&&!integratedMode,()->{
            integratedMode=false;page="market";persistNavigation();showShell();
        });
        TextView integrated=segmentItem("통합",integratedMode,()->{
            integratedMode=true;page="home";persistNavigation();showShell();
        });
        TextView stock=segmentItem("주식","stock".equals(market)&&!integratedMode&&
            !"market".equals(page),()->{
            integratedMode=false;
            if(!"stock".equals(market)){market="stock";strategy="at";resetHistoryFilter();}
            page="home";persistNavigation();showShell();
        });
        TextView coin=segmentItem("코인","coin".equals(market)&&!integratedMode&&
            !"market".equals(page),()->{
            integratedMode=false;
            if(!"coin".equals(market)){market="coin";strategy="general";resetHistoryFilter();}
            page="home";persistNavigation();showShell();
        });
        marketSwitch.addView(marketTab,new LinearLayout.LayoutParams(0,dp(33),1));
        marketSwitch.addView(integrated,new LinearLayout.LayoutParams(0,dp(33),1));
        marketSwitch.addView(stock,new LinearLayout.LayoutParams(0,dp(33),1));
        marketSwitch.addView(coin,new LinearLayout.LayoutParams(0,dp(33),1));
        shell.addView(marketSwitch,new LinearLayout.LayoutParams(-1,dp(39)));
        shell.addView(divider(),new LinearLayout.LayoutParams(-1,dp(1)));

        HorizontalScrollView sh=new HorizontalScrollView(this);sh.setHorizontalScrollBarEnabled(false);sh.setFillViewport(true);
        strategyBar=new LinearLayout(this);strategyBar.setOrientation(LinearLayout.HORIZONTAL);strategyBar.setGravity(Gravity.CENTER_VERTICAL);strategyBar.setPadding(dp(11),dp(3),dp(11),dp(3));
        sh.addView(strategyBar,new HorizontalScrollView.LayoutParams(-1,-1));if(!"market".equals(page)&&!integratedMode){
            shell.addView(sh,new LinearLayout.LayoutParams(-1,dp(40)));
            buildStrategyBar();
        }

        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.setBackgroundColor(PremiumUi.BG);
        content=new LinearLayout(this);content.setOrientation(LinearLayout.VERTICAL);content.setPadding(dp(7),dp(5),dp(7),dp(8));
        scroll.addView(content,new ScrollView.LayoutParams(-1,-2));
        scroll.setOnTouchListener((v,e)->{
            if(integratedMode||"analysis".equals(page)||"operation".equals(page)||"errors".equals(page)||
               "market".equals(page)||"policyHistory".equals(page))return false;
            if(e.getAction()==MotionEvent.ACTION_DOWN){
                swipeX=e.getX();swipeY=e.getY();
            }else if(e.getAction()==MotionEvent.ACTION_CANCEL){
                swipeX=Float.NaN;swipeY=Float.NaN;
            }else if(e.getAction()==MotionEvent.ACTION_UP
                &&!Float.isNaN(swipeX)&&!Float.isNaN(swipeY)){
                float dx=e.getX()-swipeX,dy=e.getY()-swipeY;
                swipeX=Float.NaN;swipeY=Float.NaN;
                if(StrategySwipeThreshold.shouldSwitch(dx,dy,dp(112))){
                    switchStrategy(dx<0?1:-1);
                    return true;
                }
            }
            return false;
        });
        shell.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));

        shell.addView(divider(),new LinearLayout.LayoutParams(-1,dp(1)));
        LinearLayout bottom=new LinearLayout(this);bottom.setOrientation(LinearLayout.HORIZONTAL);bottom.setGravity(Gravity.CENTER);bottom.setPadding(dp(6),dp(3),dp(6),dp(3));bottom.setBackgroundColor(PremiumUi.BG);
        bottom.addView(bottomNavItem("home","⌂","대시보드"),
            new LinearLayout.LayoutParams(0,dp(46),1));
        bottom.addView(bottomNavItem("positions","◆","보유종목"),
            new LinearLayout.LayoutParams(0,dp(46),1));
        if(!integratedMode){
            bottom.addView(bottomNavItem("pending","◌","매수대기"),
                new LinearLayout.LayoutParams(0,dp(46),1));
            bottom.addView(bottomNavItem("executions","↔","체결이력"),
                new LinearLayout.LayoutParams(0,dp(46),1));
            bottom.addView(bottomNavItem("research","◎","연구자료"),
                new LinearLayout.LayoutParams(0,dp(46),1));
            bottom.addView(bottomNavItem("analysis","◇","AI분석"),
                new LinearLayout.LayoutParams(0,dp(46),1));
        }
        shell.addView(bottom,new LinearLayout.LayoutParams(-1,dp(52)));

        updateClock();
        render();
        PremiumUi.reveal(content);
    }

    private String etaText(JSONObject p){
        if(p==null)return "";
        int done=p.optInt("done",0),total=p.optInt("total",0);
        long start=p.optLong("started_at",0L);
        if(done<=0||total<=done||start<=0)return total>0?done+"/"+total:"";
        long elapsed=Math.max(1,System.currentTimeMillis()-start);
        long remain=Math.max(0,(long)((elapsed/(double)done)*(total-done)));
        long sec=remain/1000;
        return done+"/"+total+" · 예상 "+(sec>=60?(sec/60)+"분 "+(sec%60)+"초":sec+"초");
    }

    private void updateDrawerProgressView(DrawerProgressViews views,JSONObject state){
        if(views==null)return;
        int pct=state==null?0:Math.max(0,Math.min(100,state.optInt("percent",0)));
        views.percent.setText(pct+"%");
        views.bar.setProgress(pct,true);
        String stage=state==null?"":state.optString("stage","");
        boolean error=state!=null&&(state.optInt("failed",0)>0||
            stage.toUpperCase(Locale.ROOT).contains("FAILED")||
            stage.toUpperCase(Locale.ROOT).contains("ERROR"));
        int done=state==null?0:state.optInt("processed",state.optInt("done",0));
        int total=state==null?0:state.optInt("total",0);
        boolean active=total>0||(!stage.isEmpty()&&
            !"IDLE".equalsIgnoreCase(stage)&&!"WAIT".equalsIgnoreCase(stage));
        views.detail.setText(SyncStatusV036.phase(
            state!=null&&state.optBoolean("complete",false),
            error,done,total,active));
    }

    private DrawerProgressViews addProgressBlock(LinearLayout parent,String title,JSONObject state,int accent){
        LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(dp(2),dp(7),dp(2),dp(8));
        LinearLayout head=new LinearLayout(this);head.setOrientation(LinearLayout.HORIZONTAL);
        TextView t=text(title,12);t.setTypeface(null,Typeface.BOLD);t.setTextColor(Color.rgb(215,222,237));head.addView(t,new LinearLayout.LayoutParams(0,dp(24),1));
        TextView pc=text("0%",12);pc.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);pc.setTypeface(null,Typeface.BOLD);pc.setTextColor(accent);head.addView(pc,new LinearLayout.LayoutParams(dp(58),dp(24)));box.addView(head);
        ProgressBar bar=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);bar.setIndeterminate(false);bar.setMax(100);
        if(Build.VERSION.SDK_INT>=21)bar.setProgressTintList(android.content.res.ColorStateList.valueOf(accent));
        box.addView(bar,new LinearLayout.LayoutParams(-1,dp(8)));
        TextView d=text("",10);d.setTextColor(Color.rgb(139,151,174));d.setPadding(0,dp(5),0,0);box.addView(d);
        parent.addView(box,new LinearLayout.LayoutParams(-1,-2));
        DrawerProgressViews views=new DrawerProgressViews(pc,bar,d);
        updateDrawerProgressView(views,state);
        return views;
    }

    private JSONObject effectiveImportProgress(JSONObject raw,JSONObject snapshot){
        JSONObject state=raw==null?new JSONObject():raw;
        JSONObject server=snapshot==null?null:snapshot.optJSONObject("mserver_migration");
        if(server!=null&&!server.optBoolean("ready",false)&&server.optInt("total_chunks",0)>0
                &&!state.optBoolean("complete",false)){
            try{
                state=new JSONObject(state.toString());
                int received=server.optInt("received",0),total=server.optInt("total_chunks",0);
                state.put("stage","PC→MSERVER 업로드").put("detail","승계 ZIP 수신")
                    .put("done",received).put("total",total)
                    .put("percent",total>0?received*100/total:0);
            }catch(Exception ignored){}
        }
        return state;
    }

    private JSONObject drawerLocalProgress(String prefName,boolean importStage){
        android.content.SharedPreferences p=getSharedPreferences(prefName,MODE_PRIVATE);
        JSONObject state=new JSONObject();
        try{
            state.put("stage",p.getString("stage",""));
            state.put(importStage?"detail":"current",p.getString(importStage?"detail":"current",""));
            state.put("done",importStage?p.getLong("done",0L):p.getInt("done",0));
            state.put("total",importStage?p.getLong("total",0L):p.getInt("total",0));
            state.put("percent",p.getInt("percent",0));
            state.put("complete",p.getBoolean("complete",false));
            if(!importStage)state.put("started_at",p.getLong("started_at",0L));
        }catch(Exception ignored){}
        return state;
    }

    private void refreshOpenDrawerProgress(){
        if(drawerOverlay==null)return;
        JSONObject snapshot=copyCache();
        updateDrawerProgressView(drawerPublishProgress,
            drawerLocalProgress("artitrade_mserver_sync_v078",false));
        updateDrawerProgressView(drawerImportProgress,
            effectiveImportProgress(drawerLocalProgress("artitrade_pc_ledger_import_progress_v078",true),snapshot));
    }

    private void stopDrawerProgressTicker(){
        main.removeCallbacks(drawerProgressTicker);
        drawerPublishProgress=null;
        drawerImportProgress=null;
    }

    private void logMserverIssue(String key,String original){
        if(original==null||original.trim().isEmpty())return;
        String clean=original.trim();
        String previous=getSharedPreferences("artitrade_mserver_issues_v036",MODE_PRIVATE)
            .getString(key,"");
        if(clean.equals(previous))return;
        getSharedPreferences("artitrade_mserver_issues_v036",MODE_PRIVATE)
            .edit().putString(key,clean).apply();
        new RuntimeCrashStore(this).add("mserver_"+key,
            new IllegalStateException(clean.length()>900?clean.substring(0,900):clean));
    }

    private void advancedServerTools(){
        String[] actions={"전체 원장 백업","절전 예외 설정","실시간 엔진 재연결"};
        new AlertDialog.Builder(this).setTitle("추가 관리 도구")
            .setItems(actions,(d,w)->{
                if(w==0)requestFullLedgerBackup();
                else if(w==1){
                    try{startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}
                    catch(Exception e){new RuntimeCrashStore(this).add("battery_settings",e);}
                }else if(w==2)restartFeeds();
            }).setNegativeButton("닫기",null).show();
    }

    private void openSettingsDrawer(){
        if(drawerOverlay!=null)return;
        FrameLayout overlay=new FrameLayout(this);overlay.setBackgroundColor(Color.argb(125,0,0,0));
        View dim=new View(this);dim.setBackgroundColor(Color.TRANSPARENT);dim.setOnClickListener(v->closeSettingsDrawer());
        overlay.addView(dim,new FrameLayout.LayoutParams(-1,-1));

        int width=Math.min(dp(380),(int)(getResources().getDisplayMetrics().widthPixels*.88f));
        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.setBackgroundColor(PremiumUi.BG);
        LinearLayout panel=new LinearLayout(this);panel.setOrientation(LinearLayout.VERTICAL);panel.setPadding(dp(18),dp(18),dp(18),dp(22));scroll.addView(panel,new ScrollView.LayoutParams(-1,-2));
        LinearLayout head=new LinearLayout(this);head.setOrientation(LinearLayout.HORIZONTAL);head.setGravity(Gravity.CENTER_VERTICAL);
        TextView title=text(("stock".equals(market)?"주식":"코인")+" 설정",21);title.setTypeface(null,Typeface.BOLD);head.addView(title,new LinearLayout.LayoutParams(0,dp(42),1));
        head.addView(iconAction("×",this::closeSettingsDrawer),new LinearLayout.LayoutParams(dp(38),dp(38)));panel.addView(head);

        TextView manageTitle=text("관리",10);manageTitle.setTypeface(null,Typeface.BOLD);manageTitle.setTextColor(Color.rgb(121,130,151));manageTitle.setPadding(0,dp(16),0,dp(7));panel.addView(manageTitle);
        LinearLayout manage=card();
        TextView opRow=text("운용 설정   ·   전 전략 시드 / 최대보유",12);opRow.setTypeface(null,Typeface.BOLD);opRow.setPadding(dp(4),dp(7),dp(4),dp(7));opRow.setOnClickListener(v->{page="operation";closeSettingsDrawer();main.postDelayed(this::showShell,190);});manage.addView(opRow,new LinearLayout.LayoutParams(-1,dp(44)));
        manage.addView(divider(),new LinearLayout.LayoutParams(-1,dp(1)));
        TextView erRow=text("오류 이력   ·   "+("stock".equals(market)?"주식 전체":"코인 전체"),12);erRow.setTypeface(null,Typeface.BOLD);erRow.setPadding(dp(4),dp(7),dp(4),dp(7));erRow.setOnClickListener(v->{page="errors";closeSettingsDrawer();main.postDelayed(this::showShell,190);});manage.addView(erRow,new LinearLayout.LayoutParams(-1,dp(44)));
        manage.addView(divider(),new LinearLayout.LayoutParams(-1,dp(1)));
        TextView scoreRow=text("엔진 점수 비교   ·   미선정 후보 포함",12);scoreRow.setTypeface(null,Typeface.BOLD);scoreRow.setPadding(dp(4),dp(7),dp(4),dp(7));scoreRow.setOnClickListener(v->{page="scores";closeSettingsDrawer();main.postDelayed(this::showShell,190);});manage.addView(scoreRow,new LinearLayout.LayoutParams(-1,dp(44)));
        TextView policyRow=text("정책이력   ·   실제 체결과 매도 판단 근거",12);
        policyRow.setPadding(dp(4),dp(7),dp(4),dp(7));
        policyRow.setOnClickListener(v->{
            policyTargetCode="";policyTargetStrategy="";policyTargetMarket="";
            page="policyHistory";closeSettingsDrawer();main.postDelayed(this::showShell,190);
        });
        manage.addView(divider(),new LinearLayout.LayoutParams(-1,dp(1)));
        manage.addView(policyRow,new LinearLayout.LayoutParams(-1,dp(44)));
        panel.addView(manage);

        String brokerKey="stock".equals(market)?"kiwoom":"upbit";
        String brokerName="stock".equals(market)?"키움":"업비트";
        TextView apiTitle=text(brokerName+" API 연결",13);apiTitle.setTypeface(null,Typeface.BOLD);apiTitle.setTextColor(Color.rgb(168,176,198));apiTitle.setPadding(0,dp(18),0,dp(8));panel.addView(apiTitle);
        LinearLayout api=card();TextView brokerState=text(brokerStatusText(brokerKey),12);brokerState.setTextColor(Color.rgb(205,212,229));api.addView(brokerState);
        LinearLayout ar=new LinearLayout(this);ar.setOrientation(LinearLayout.HORIZONTAL);ar.setPadding(0,dp(12),0,0);
        ar.addView(button(brokerName+" 키 설정",true,this::apiSettings),new LinearLayout.LayoutParams(0,dp(44),1));
        LinearLayout.LayoutParams prp=new LinearLayout.LayoutParams(0,dp(44),1);prp.setMargins(dp(8),0,0,0);ar.addView(button("인증 검사",false,()->runBrokerProbe(false)),prp);api.addView(ar);panel.addView(api);

        TextView srvTitle=text("MSERVER",14);
        srvTitle.setTypeface(null,Typeface.BOLD);
        srvTitle.setTextColor(Color.rgb(161,192,231));
        srvTitle.setPadding(0,dp(15),0,dp(7));
        panel.addView(srvTitle);
        LinearLayout tools=card();
        JSONObject snapshot=copyCache();
        JSONObject hubPub=snapshot.optJSONObject("hub_publish");
        JSONObject auth=snapshot.optJSONObject("mserver_auth");
        JSONObject sync=snapshot.optJSONObject("mserver_sync");
        JSONObject migration=snapshot.optJSONObject("pc_ledger_migration");
        JSONObject importProgress=effectiveImportProgress(
            snapshot.optJSONObject("pc_import_progress"),snapshot);
        boolean authOk=auth!=null&&auth.optBoolean("ok",false);
        String authError=auth==null?"":auth.optString("error","");
        boolean pcSyncEnabled=getSharedPreferences("artitrade_pc_live_sync_v1",
            MODE_PRIVATE).getBoolean("enabled",false);
        boolean failedPublish=(hubPub!=null&&hubPub.optInt("failed",0)>0)||
            (sync!=null&&sync.optInt("failed",0)>0);
        boolean importFailed=migration!=null&&(
            migration.optString("status","").contains("FAILED")||
            "IMPORT_FAILED".equals(migration.optString("status","")));
        if(failedPublish&&hubPub!=null)
            logMserverIssue("publisher",hubPub.optString("last_error","게시 실패"));
        if(importFailed&&migration!=null)
            logMserverIssue("migration",migration.toString());
        if(!authOk&&!authError.isEmpty())
            logMserverIssue("auth",authError);

        LinearLayout stateRow=new LinearLayout(this);
        stateRow.setOrientation(LinearLayout.HORIZONTAL);
        stateRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView connection=text("연결",13);
        connection.setTextColor(PremiumUi.TEXT);
        stateRow.addView(connection,new LinearLayout.LayoutParams(0,dp(31),1));
        String connectionState=authOk?"완료":!authError.isEmpty()?"오류":"미완료";
        TextView connectValue=text(connectionState,14);
        connectValue.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
        connectValue.setTextColor(authOk?Color.rgb(92,222,177):
            !authError.isEmpty()?Color.rgb(250,125,149):Color.rgb(239,197,120));
        stateRow.addView(connectValue,new LinearLayout.LayoutParams(dp(80),dp(31)));
        tools.addView(stateRow,new LinearLayout.LayoutParams(-1,dp(31)));

        LinearLayout syncStateRow=new LinearLayout(this);
        syncStateRow.setOrientation(LinearLayout.HORIZONTAL);
        syncStateRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView syncCaption=text("PC 실시간연동",13);
        syncCaption.setTextColor(PremiumUi.TEXT);
        syncStateRow.addView(syncCaption,new LinearLayout.LayoutParams(0,dp(31),1));
        TextView syncCaptionState=text(pcSyncEnabled?"진행중":"미완료",13);
        syncCaptionState.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
        syncCaptionState.setTextColor(pcSyncEnabled?Color.rgb(91,221,169):PremiumUi.MUTED);
        syncStateRow.addView(syncCaptionState,new LinearLayout.LayoutParams(dp(80),dp(31)));
        tools.addView(syncStateRow,new LinearLayout.LayoutParams(-1,dp(31)));

        drawerPublishProgress=addProgressBlock(tools,"휴대전화 원장 전송",
            sync,Color.rgb(0,196,164));
        drawerImportProgress=addProgressBlock(tools,"PC 원장 승계",
            importProgress,Color.rgb(77,124,254));
        // Detailed counts, source DB names, network codes, thread/preload timing
        // and per-strategy raw verification stay in the diagnostic store.
        // Never put them in the user-facing MSERVER drawer.
        LinearLayout firstActions=new LinearLayout(this);
        firstActions.setOrientation(LinearLayout.HORIZONTAL);
        Button toggle=button(pcSyncEnabled?"실시간연동 중지":"실시간연동 시작",
            pcSyncEnabled,this::togglePcLiveSync);
        toggle.setTextSize(12);
        firstActions.addView(toggle,new LinearLayout.LayoutParams(0,dp(41),1));
        Button send=button("원장 전송",false,this::requestLedgerRepublish);
        send.setTextSize(12);
        LinearLayout.LayoutParams sendLp=new LinearLayout.LayoutParams(0,dp(41),1);
        sendLp.setMargins(dp(6),0,0,0);
        firstActions.addView(send,sendLp);
        tools.addView(firstActions,new LinearLayout.LayoutParams(-1,dp(42)));

        LinearLayout secondActions=new LinearLayout(this);
        secondActions.setOrientation(LinearLayout.HORIZONTAL);
        Button pairing=button("연결 토큰",false,this::pair);
        pairing.setTextSize(12);
        secondActions.addView(pairing,new LinearLayout.LayoutParams(0,dp(41),1));
        Button verify=button("PC 승계 재검증",false,this::requestPcMigrationApply);
        verify.setTextSize(12);
        LinearLayout.LayoutParams verifyLp=new LinearLayout.LayoutParams(0,dp(41),1);
        verifyLp.setMargins(dp(6),0,0,0);
        secondActions.addView(verify,verifyLp);
        LinearLayout.LayoutParams secondLp=new LinearLayout.LayoutParams(-1,dp(43));
        secondLp.setMargins(0,dp(5),0,0);
        tools.addView(secondActions,secondLp);
        Button advanced=button("기타 관리 도구",false,this::advancedServerTools);
        advanced.setTextSize(12);
        LinearLayout.LayoutParams advancedLp=new LinearLayout.LayoutParams(-1,dp(37));
        advancedLp.setMargins(0,dp(5),0,0);
        tools.addView(advanced,advancedLp);
        panel.addView(tools);

        FrameLayout.LayoutParams pp=new FrameLayout.LayoutParams(width,-1,Gravity.END);overlay.addView(scroll,pp);
        drawerOverlay=overlay;root.addView(overlay,new FrameLayout.LayoutParams(-1,-1));
        TranslateAnimation anim=new TranslateAnimation(width,0,0,0);anim.setDuration(220);scroll.startAnimation(anim);
        main.removeCallbacks(drawerProgressTicker);
        main.post(drawerProgressTicker);
    }

    private void closeSettingsDrawer(){
        if(drawerOverlay==null)return;
        View old=drawerOverlay;drawerOverlay=null;
        stopDrawerProgressTicker();
        TranslateAnimation anim=new TranslateAnimation(0,dp(380),0,0);anim.setDuration(180);
        anim.setAnimationListener(new android.view.animation.Animation.AnimationListener(){
            public void onAnimationStart(android.view.animation.Animation a){}
            public void onAnimationRepeat(android.view.animation.Animation a){}
            public void onAnimationEnd(android.view.animation.Animation a){try{root.removeView(old);}catch(Exception ignored){}}
        });
        old.startAnimation(anim);
    }

    private String brokerStatusText(String which){
        JSONObject c=copyCache();JSONObject storage=c.optJSONObject("broker_storage");JSONObject probe=c.optJSONObject("broker_probe");
        boolean saved=storage!=null&&storage.optBoolean("kiwoom".equals(which)?"kiwoom_saved":"upbit_saved",false);
        boolean encrypted=storage!=null&&storage.optBoolean("kiwoom".equals(which)?"kiwoom_encrypted_present":"upbit_encrypted_present",false);
        boolean decrypt=storage==null||storage.optBoolean("kiwoom".equals(which)?"kiwoom_decrypt_ok":"upbit_decrypt_ok",true);
        String masked=storage==null?"":storage.optString("kiwoom".equals(which)?"kiwoom_key_masked":"upbit_access_masked","");
        JSONObject p=probe==null?null:probe.optJSONObject(which);
        String name="kiwoom".equals(which)?"키움":"업비트";
        if(encrypted&&!decrypt)return name+" · 암호화값 존재 · Keystore 복호화 실패 · 키 재등록 필요";
        if(!saved)return name+" · 키 미저장";
        if(p==null)return name+" · "+masked+" · 저장됨 · 인증검사 필요";
        if(p.optBoolean("ok",false))return name+" · "+masked+" · 인증 정상";
        String msg=p.optString("message","인증 실패");
        if(p.optBoolean("ip_hint",false))msg+=" · 허용 IP 확인";
        return name+" · "+masked+" · "+msg;
    }


    private View space(){View v=new View(this);v.setBackgroundColor(Color.TRANSPARENT);return v;}

    private void buildStrategyBar(){
        if(strategyBar==null)return;
        strategyBar.removeAllViews();
        String[][] list=strategyList(market);
        for(String[] item:list){
            boolean active=item[0].equals(strategy);
            TextView tab=strategyTab(item[1],active,()->{if(!item[0].equals(strategy)){strategy=item[0];resetHistoryFilter();}persistNavigation();showShell();});
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(32),1);
            if(strategyBar.getChildCount()>0)lp.setMargins(dp(4),0,0,0);
            strategyBar.addView(tab,lp);
        }
    }

    private void switchStrategy(int delta){
        String[] a="stock".equals(market)?new String[]{"at","ma","close","gmo"}:new String[]{"general","ma"};
        int idx=0;for(int i=0;i<a.length;i++)if(a[i].equals(strategy))idx=i;
        int n=idx+delta;if(n<0||n>=a.length)return;strategy=a[n];resetHistoryFilter();persistNavigation();showShell();
    }

    private void refresh(boolean first){
        if(refreshRunning)return;
        refreshRunning=true;
        io.execute(()->{
            JSONObject packet=new JSONObject();JSONObject records=new JSONObject();
            boolean reusedLocalRecords=false,meaningfulLocalRecords=false;
            try{
                LocalHubClient client=new LocalHubClient(this);
                try{packet.put("status",client.status());}catch(Exception e){packet.put("status",new JSONObject().put("ok",false));}
                try{packet.put("realtime",new RealtimeState(this).all());}catch(Exception e){packet.put("realtime",new JSONObject());}
                try{packet.put("hub_pairing",client.pairingStatus());}catch(Exception e){packet.put("hub_pairing",new JSONObject());}
                try{
                    BrokerSecretStore b=new BrokerSecretStore(this);
                    packet.put("credentials",new JSONObject().put("kiwoom",b.hasKiwoom()).put("upbit",b.hasUpbit()));
                    packet.put("broker_storage",b.status());
                    String rawProbe=getSharedPreferences("broker_probe_v1",MODE_PRIVATE).getString("result","{}");
                    packet.put("broker_probe",new JSONObject(rawProbe));
                }catch(Exception e){
                    packet.put("credentials",new JSONObject());packet.put("broker_storage",new JSONObject());packet.put("broker_probe",new JSONObject());
                }
                try{
                    android.content.SharedPreferences p=getSharedPreferences("unified_live_orders",MODE_PRIVATE);
                    packet.put("live_orders",new JSONObject().put("stock",p.getBoolean("live_stock",false)).put("coin",p.getBoolean("live_coin",false)));
                }catch(Exception e){packet.put("live_orders",new JSONObject());}
                try{
                    String raw=getSharedPreferences("artitrade_runtime_snapshot",MODE_PRIVATE).getString("snapshot","{}");
                    packet.put("strategy_runtime",new JSONObject(raw));
                }catch(Exception e){packet.put("strategy_runtime",new JSONObject());}
                try{packet.put("runtime_diagnostics",new RuntimeCrashStore(this).read());}catch(Exception e){packet.put("runtime_diagnostics",new JSONArray());}

                JSONObject localRecords=new JSONObject();
                try{
                    android.content.SharedPreferences lp=getSharedPreferences("artitrade_local_records_v075",MODE_PRIVATE);
                    String raw=lp.getString("records","{}");
                    reusedLocalRecords=cachedLocalRaw!=null
                        &&(cachedLocalRaw==raw||cachedLocalRaw.equals(raw));
                    if(reusedLocalRecords){
                        localRecords=cachedLocalParsed;
                        meaningfulLocalRecords=cachedLocalMeaningful;
                    }else{
                        localRecords=new JSONObject(raw);
                        meaningfulLocalRecords=meaningfulTree(localRecords);
                        cachedLocalRaw=raw;
                        cachedLocalParsed=localRecords;
                        cachedLocalMeaningful=meaningfulLocalRecords;
                    }
                    packet.put("local_records",localRecords);
                    packet.put("local_records_at",lp.getLong("updated_at",0L));
                }catch(Exception e){packet.put("local_records",new JSONObject());}
                // v0.7.25: source-by-source gap recovery. The old global flag
                // hid MA/CLOSE/GMO or coin data whenever AT had any records.
                // Keep phone-created records authoritative, and fetch MSERVER only
                // for a *missing bucket*, not for an entire market.
                long now=android.os.SystemClock.elapsedRealtime();
                boolean hubResponding=packet.optJSONObject("status")!=null
                    &&packet.optJSONObject("status").optBoolean("ok",false);
                boolean pollGaps=preloadScreenReady&&hubResponding&&
                    now-lastHubGapPollMs>=25_000L;
                if(pollGaps)lastHubGapPollMs=now;
                boolean anyHub=false;
                for(String[] k:RECORDS){
                    JSONObject marketTree=localRecords.optJSONObject(k[0]);
                    JSONObject strategyTree=marketTree==null?null:marketTree.optJSONObject(k[1]);
                    JSONObject localBucket=strategyTree==null?null:strategyTree.optJSONObject(k[2]);
                    if(meaningfulRecord(localBucket)
                        &&!missingNestedBucket(localBucket,k[3]))continue;
                    JSONObject response=cachedHubGapRecords.optJSONObject(k[3]);
                    if(pollGaps){
                        try{
                            JSONObject fresh=client.record(k[0],k[1],k[2]);
                            JSONObject data=fresh.optJSONObject("data");
                            if(meaningfulRecord(data)){
                                cachedHubGapRecords.put(k[3],fresh);
                                response=fresh;
                            }else if(fresh.optBoolean("ok",false)){
                                // Confirmed empty data must replace stale cache.
                                cachedHubGapRecords.remove(k[3]);
                                response=fresh;
                            }
                        }catch(Exception e){
                            new RuntimeCrashStore(this).add("hub_gap_"+k[3],e);
                        }
                    }
                    if(response!=null&&meaningfulRecord(response.optJSONObject("data"))){
                        records.put(k[3],response);
                        anyHub=true;
                    }
                }
                packet.put("using_hub_fallback",anyHub);
                packet.put("hub_gap_refresh_available",hubResponding);
                packet.put("hub_gap_status",anyHub?"MISSING_BUCKETS_FROM_MSERVER":
                    hubResponding?"LOCAL_OR_EMPTY":"MSERVER_OFFLINE");
                try{
                    android.content.SharedPreferences sp=getSharedPreferences("artitrade_mserver_sync_v078",MODE_PRIVATE);
                    packet.put("mserver_sync",new JSONObject()
                        .put("stage",sp.getString("stage","")).put("current",sp.getString("current",""))
                        .put("done",sp.getInt("done",0)).put("processed",sp.getInt("processed",0))
                        .put("successful",sp.getInt("successful",0)).put("total",sp.getInt("total",0))
                        .put("failed",sp.getInt("failed",0)).put("percent",sp.getInt("percent",0))
                        .put("started_at",sp.getLong("started_at",0L)).put("updated_at",sp.getLong("updated_at",0L))
                        .put("complete",sp.getBoolean("complete",false)));
                }catch(Exception e){packet.put("mserver_sync",new JSONObject());}
                try{
                    android.content.SharedPreferences ip=getSharedPreferences("artitrade_pc_ledger_import_progress_v078",MODE_PRIVATE);
                    packet.put("pc_import_progress",new JSONObject()
                        .put("stage",ip.getString("stage","")).put("detail",ip.getString("detail",""))
                        .put("done",ip.getLong("done",0L)).put("total",ip.getLong("total",0L))
                        .put("percent",ip.getInt("percent",0)).put("updated_at",ip.getLong("updated_at",0L))
                        .put("complete",ip.getBoolean("complete",false)).put("error",ip.getString("error","")));
                }catch(Exception e){packet.put("pc_import_progress",new JSONObject());}
                try{packet.put("mserver_migration",client.migrationStatus());}
                catch(Exception e){packet.put("mserver_migration",new JSONObject());}
                try{
                    android.content.SharedPreferences hp=getSharedPreferences("artitrade_hub_publish_v075",MODE_PRIVATE);
                    packet.put("hub_publish",new JSONObject()
                        .put("at",hp.getLong("at",0L)).put("ok",hp.getInt("ok",0))
                        .put("failed",hp.getInt("failed",0)).put("preserved",hp.getInt("preserved",0))
                        .put("legacy_preserved",hp.getBoolean("legacy_preserved",false))
                        .put("last_error",hp.getString("last_error",""))
                        .put("last_market",hp.getString("last_market",""))
                        .put("last_strategy",hp.getString("last_strategy",""))
                        .put("last_bucket",hp.getString("last_bucket",""))
                        .put("last_http_status",hp.getInt("last_http_status",200)));
                }catch(Exception e){packet.put("hub_publish",new JSONObject());}
                try{
                    android.content.SharedPreferences mp=getSharedPreferences("artitrade_pc_ledger_import_result",MODE_PRIVATE);
                    String raw=mp.getString("result","{}");
                    packet.put("pc_ledger_migration",new JSONObject(raw));
                }catch(Exception e){packet.put("pc_ledger_migration",new JSONObject());}
                try{
                    android.content.SharedPreferences ap=getSharedPreferences("artitrade_mserver_auth_v077",MODE_PRIVATE);
                    packet.put("mserver_auth",new JSONObject()
                        .put("at",ap.getLong("at",0L)).put("ok",ap.getBoolean("ok",false))
                        .put("error",ap.getString("error","")).put("http_status",ap.getInt("http_status",0)));
                }catch(Exception e){packet.put("mserver_auth",new JSONObject());}
                packet.put("records",records);packet.put("loaded_at",System.currentTimeMillis());
            }catch(Exception e){try{packet.put("refresh_error",e.getClass().getSimpleName()+": "+e.getMessage());}catch(Exception ignored){}}
            if(!preloadScreenReady)updateStartupProgress(52,"전략별 원장 데이터 확인 · 집계 중");
            long preloadStart=android.os.SystemClock.elapsedRealtime();
            UiSnapshot previous=uiSnapshot;
            Map<String,Stats> preparedTotals=new HashMap<>();
            Map<String,Stats> preparedFrames=new HashMap<>();
            // Preload summaries for every strategy in the worker, before a tab can draw them.
            // No stock/coin order, SQLite write or strategy state is changed here.
            JSONObject previousHub=previous.packet.optJSONObject("records");
            boolean hubGapChanged=!(records.toString().equals(
                previousHub==null?"{}":previousHub.toString()));
            if(reusedLocalRecords&&meaningfulLocalRecords&&!hubGapChanged
                &&!previous.totals.isEmpty()){
                preparedTotals=previous.totals;
                preparedFrames=previous.frames;
            }else{
                for(String m:new String[]{"stock","coin"}){
                    String[] strategies="stock".equals(m)
                        ?new String[]{"at","ma","close","gmo"}
                        :new String[]{"general","ma"};
                    for(String stg:strategies){
                        try{
                            Stats st=calculateStats(packet,m,stg);
                            preparedTotals.put(m+"/"+stg,st);
                            if("at".equals(stg)||"ma".equals(stg)||"general".equals(stg)){
                                for(String tf:new String[]{"5m","30m","60m","day"})
                                    preparedFrames.put(m+"/"+stg+"/"+tf,
                                        calculateStatsTf(packet,m,stg,tf,st));
                            }
                        }catch(Exception e){
                            new RuntimeCrashStore(this).add("ui_ledger_preload_"+m+"_"+stg,e);
                        }
                    }
                }
            }
            if(!preloadScreenReady)updateStartupProgress(76,"주식·코인 전략별 화면 자료 사전 계산");
            boolean ledgerChanged=preparedTotals!=previous.totals;
            Map<String,java.util.List<HistoryRow>> histories=ledgerChanged
                ?prepareHistories(preparedTotals):previous.histories;
            Map<String,RealizedTrend.Series> trends=ledgerChanged
                ?prepareTrends(preparedTotals):previous.trends;
            Map<String,ResearchScoreGrid> researchGrids=ledgerChanged
                ?prepareResearchGrids(preparedTotals):previous.researchGrids;
            int rows=ledgerChanged?countPreparedRows(preparedTotals):previous.preloadRows;
            long preloadMs=android.os.SystemClock.elapsedRealtime()-preloadStart;
            uiSnapshot=new UiSnapshot(packet,preparedTotals,preparedFrames,histories,
                trends,researchGrids,preloadMs,rows,!ledgerChanged);
            refreshRunning=false;
            // Home has live feed indicators; historical pages need not recreate
            // hundreds of views when the ledger version has not changed.
            main.post(()->{
                if(!preloadScreenReady){
                    JSONObject engine=packet.optJSONObject("strategy_runtime");
                    boolean engineReady=packet.optLong("local_records_at",0L)>0 &&
                        uiSnapshot.totals.size()>=6 && engine!=null &&
                        (engine.optJSONObject("stock")!=null ||
                         engine.optJSONObject("coin")!=null);
                    boolean timedOut=android.os.SystemClock.elapsedRealtime()-
                        preloadStartedAt>=16000L;
                    if(engineReady||timedOut){
                        // A disconnected broker must not make the phone unusable.
                        // Timeouts are explicitly displayed as engine-waiting, not success.
                        finishPreload(engineReady);
                    }else{
                        updateStartupProgress(88,"원장 생성 및 엔진 상태 확인 중");
                        main.postDelayed(()->refresh(true),850);
                    }
                    return;
                }
                updateHeader();
                if(!"operation".equals(page)
                    &&(ledgerChanged||"home".equals(page)||"errors".equals(page)||
                        "market".equals(page)))render();
            });
        });
    }

    private void updateHeader(){
        if(statusText==null)return;
        JSONObject c=copyCache();JSONObject rt=c.optJSONObject("strategy_runtime");
        JSONObject mr=rt==null?null:rt.optJSONObject(market);
        boolean live=mr!=null&&mr.optBoolean("connected",false);
        JSONObject halted=rt==null?null:rt.optJSONObject("halted");
        boolean stopped=halted!=null&&!halted.isNull(market)&&halted.optJSONObject(market)!=null;
        long recordsAt=c.optLong("local_records_at",0L);
        String sync=recordsAt>0?" · 원장 "+Math.max(0,(System.currentTimeMillis()-recordsAt)/1000)+"초":" · 원장 준비";
        JSONObject mp=c.optJSONObject("mserver_sync"),ip=c.optJSONObject("pc_import_progress"),ms=c.optJSONObject("mserver_migration");
        String progress="";
        if(mp!=null&&!mp.optBoolean("complete",false)&&mp.optInt("total",0)>0)progress+=" · MSERVER "+mp.optInt("percent",0)+"%";
        int ipp=ip==null?0:ip.optInt("percent",0);
        if(ip!=null&&!ip.optBoolean("complete",false)&&ip.optInt("total",0)>0)progress+=" · 과거원장 "+ipp+"%";
        else if(ms!=null&&!ms.optBoolean("ready",false)&&ms.optInt("total_chunks",0)>0)
            progress+=" · PC전송 "+Math.max(0,Math.min(100,ms.optInt("received",0)*100/Math.max(1,ms.optInt("total_chunks",1))))+"%";
        statusText.setText((live?"실시간 LIVE":"실시간 대기")+sync+progress+(stopped?" · 주문정지":""));
        statusText.setTextColor(stopped?Color.rgb(255,119,133):!progress.isEmpty()?Color.rgb(255,201,94):live?Color.rgb(62,224,181):Color.rgb(255,201,94));
    }

    private void updateClock(){
        if(clockText==null)return;
        boolean wide=getResources().getConfiguration().screenWidthDp>=720;
        SimpleDateFormat f=new SimpleDateFormat(wide?"MM/dd HH:mm":"HH:mm",Locale.KOREA);
        f.setTimeZone(TimeZone.getTimeZone("Asia/Seoul"));
        clockText.setText(f.format(new Date()));
    }

    private int countPreparedRows(Map<String,Stats> totals){
        int sum=0;
        for(Stats x:totals.values())sum+=x.positions.length()+x.executions.length()+x.research.length();
        return sum;
    }

    private static Object firstValue(JSONObject row,String... fields){
        if(row==null)return null;
        for(String key:fields){
            Object v=row.opt(key);
            if(v!=null&&v!=JSONObject.NULL&&!String.valueOf(v).trim().isEmpty()
                &&!"null".equalsIgnoreCase(String.valueOf(v)))return v;
        }
        return null;
    }

    private static String timeFrameOf(JSONObject row){
        Object raw=firstValue(row,"tf","timeframe","frame","account_frame","strategy_tf");
        return HistorySelection.frame(raw==null?"":String.valueOf(raw));
    }

    private static long rowTimestamp(JSONObject row){
        Object ts=firstValue(row,"filled_at","executed_at","fill_time","trade_time",
            "exit_at","sold_at","closed_at","ts","time","at","timestamp",
            "created_at","buy_at","entry_at","entry_ts",
            "ref_ts","ref_at","reference_ts","event_date","gmo_event_date","event_day");
        return RealizedTrend.time(ts);
    }

    private Map<String,java.util.List<HistoryRow>> prepareHistories(Map<String,Stats> totals){
        Map<String,java.util.List<HistoryRow>> buckets=new HashMap<>();
        for(Map.Entry<String,Stats> source:totals.entrySet()){
            String[] parts=source.getKey().split("/");
            if(parts.length!=2)continue;
            String m=parts[0],s=parts[1];Stats st=source.getValue();
            for(String kind:new String[]{"positions","pending","executions","research"}){
                JSONArray data="positions".equals(kind)?st.positions:
                    "pending".equals(kind)?st.pending:
                    "executions".equals(kind)?st.executions:st.research;
                for(int i=0;i<data.length();i++){
                    JSONObject row=data.optJSONObject(i);if(row==null)continue;
                    String frame=timeFrameOf(row);
                    HistoryRow item=new HistoryRow(row,s,frame,rowTimestamp(row));
                    // Current-strategy default, all-strategy opt-in and per-frame within the current strategy.
                    buckets.computeIfAbsent(m+"/"+s+"/"+kind+"/all",k->new java.util.ArrayList<>()).add(item);
                    buckets.computeIfAbsent(m+"/all/"+kind+"/all",k->new java.util.ArrayList<>()).add(item);
                    if(!frame.isEmpty())
                        buckets.computeIfAbsent(m+"/"+s+"/"+kind+"/"+frame,
                            k->new java.util.ArrayList<>()).add(item);
                }
            }
        }
        for(java.util.List<HistoryRow> group:buckets.values())
            group.sort((a,b)->Long.compare(b.at,a.at));
        return buckets;
    }

    private Map<String,RealizedTrend.Series> prepareTrends(Map<String,Stats> totals){
        java.util.List<RealizedTrend.Event> integratedEvents=new java.util.ArrayList<>();
        // In v0.7.17 trends were indexed only by market, which merged AT+MA+GMO+CLOSE.
        Map<String,RealizedTrend.Series> ready=new HashMap<>();
        for(Map.Entry<String,Stats> source:totals.entrySet()){
            String[] parts=source.getKey().split("/");
            if(parts.length!=2)continue;
            String m=parts[0],strategyName=parts[1];
            java.util.Map<String,java.util.List<RealizedTrend.Event>> grouped=new HashMap<>();
            grouped.put("all",new java.util.ArrayList<>());
            if(StrategyDashboardScope.showFrameCharts(strategyName))
                for(String tf:new String[]{"5m","30m","60m","day"})
                    grouped.put(tf,new java.util.ArrayList<>());
            JSONArray fills=source.getValue().executions;
            for(int i=0;i<fills.length();i++){
                JSONObject row=fills.optJSONObject(i);if(row==null)continue;
                String side=row.optString("side",row.optString("type",""))
                    .toUpperCase(Locale.ROOT);
                if(!(side.contains("SELL")||side.contains("매도")))continue;
                double pnl=num(row,"net_profit","net_realized","net_pnl","pnl","profit");
                long at=rowTimestamp(row);
                if(at<=0||!finite(pnl))continue;
                RealizedTrend.Event event=new RealizedTrend.Event(at,pnl);
                grouped.get("all").add(event);
                integratedEvents.add(event);
                String frame=timeFrameOf(row);
                if(grouped.containsKey(frame))grouped.get(frame).add(event);
            }
            for(Map.Entry<String,java.util.List<RealizedTrend.Event>> e:grouped.entrySet())
                ready.put(StrategyDashboardScope.trend(m,strategyName,e.getKey()),
                    RealizedTrend.cumulative(e.getValue(),100));
        }
        ready.put("integrated/all",RealizedTrend.cumulative(integratedEvents,180));
        return ready;
    }

    private Map<String,ResearchScoreGrid> prepareResearchGrids(Map<String,Stats> totals){
        Map<String,ResearchScoreGrid> grids=new HashMap<>();
        for(Map.Entry<String,Stats> entry:totals.entrySet()){
            String key=entry.getKey();
            String[] parts=key.split("/");
            if(parts.length!=2)continue;
            ResearchScoreGrid own=grids.computeIfAbsent(key,k->new ResearchScoreGrid());
            ResearchScoreGrid across=grids.computeIfAbsent(
                parts[0]+"/all",k->new ResearchScoreGrid());
            JSONArray records=entry.getValue().research;
            for(int i=0;i<records.length();i++){
                JSONObject row=records.optJSONObject(i);if(row==null)continue;
                String frame=timeFrameOf(row);
                if(frame.isEmpty())frame=parts[1];
                double score=num(row,"score","gmo_score_at_signal","original_score","signal_score");
                boolean purchased=bool(row,"bought","actually_bought","filled");
                boolean entered=bool(row,"entry_observed","virtual_entered","shadow_entered");
                String outcome=row.optString("outcome","").toUpperCase(Locale.ROOT);
                if(!("HIT".equals(outcome)||"MISS".equals(outcome))){
                    Boolean hit=target(row);
                    outcome=hit==null?"PENDING":hit?"HIT":"MISS";
                }
                own.add(frame,score,purchased,entered,outcome);
                across.add(frame,score,purchased,entered,outcome);
            }
        }
        return grids;
    }

    private String activeHistoryFilter(){
        return "stock".equals(market)?historyFilterStock:historyFilterCoin;
    }

    private java.util.List<HistoryRow> selectedHistory(String kind){
        String key=StrategyDashboardScope.history(market,strategy,kind,activeHistoryFilter());
        java.util.List<HistoryRow> selected=uiSnapshot.histories.get(key);
        return selected==null?java.util.Collections.emptyList():selected;
    }

    private void resetHistoryFilter(){
        if("stock".equals(market))historyFilterStock="current";else historyFilterCoin="current";
        historyContext="";historyOffset=0;
    }

    private void showHistoryFilterMenu(Button anchor){
        PopupMenu menu=new PopupMenu(this,anchor);
        String[] filters="stock".equals(market)
            ?new String[]{"current","all","5m","30m","60m","day","gmo","close"}
            :new String[]{"current","all","5m","30m","60m","day"};
        for(String choice:filters){
            android.view.MenuItem item=menu.getMenu().add(HistorySelection.filterLabel(choice));
            item.setCheckable(true);item.setChecked(choice.equals(activeHistoryFilter()));
            item.setOnMenuItemClickListener(selected->{
                if("stock".equals(market))historyFilterStock=choice;
                else historyFilterCoin=choice;
                historyContext="";historyOffset=0;
                persistNavigation();render();
                return true;
            });
        }
        menu.show();
    }

    private Button historyFilterButton(){
        Button selection=button("목록 ▾ "+HistorySelection.filterLabel(activeHistoryFilter()),
            false,()->{});
        selection.setOnClickListener(v->showHistoryFilterMenu(selection));
        selection.setTextSize(13);
        return selection;
    }

    private int colorByStatus(String status,int normal){
        switch(HistorySelection.semantic(status)){
            case -1:return Color.rgb(255,119,133);
            case 1:return Color.rgb(255,194,91);
            case 2:return Color.rgb(62,224,181);
            default:return normal;
        }
    }

    private JSONObject copyCache(){
        // Packet objects are built completely before publication and are read-only afterwards.
        return uiSnapshot.packet;
    }

    private boolean meaningfulTree(Object value){
        if(value==null||value==JSONObject.NULL)return false;
        if(value instanceof JSONArray){
            JSONArray a=(JSONArray)value;
            if(a.length()>0)return true;
            return false;
        }
        if(value instanceof JSONObject){
            JSONObject o=(JSONObject)value;
            for(String key:new String[]{"positions","pending","executions","items","candidate_rows","restorations"}){
                JSONArray a=o.optJSONArray(key);if(a!=null&&a.length()>0)return true;
            }
            for(String key:new String[]{"realized_pnl","unrealized_pnl","total_pnl","candidate_count","positions_count","pending_count"}){
                if(Math.abs(o.optDouble(key,0))>0.000001)return true;
            }
            java.util.Iterator<String> it=o.keys();
            while(it.hasNext()){
                String k=it.next();
                if("schema".equals(k)||"origin".equals(k)||"ok".equals(k)||"version".equals(k))continue;
                Object child=o.opt(k);
                if(child instanceof JSONObject||child instanceof JSONArray){
                    if(meaningfulTree(child))return true;
                }
            }
        }
        return false;
    }

    private boolean meaningfulRecord(JSONObject value){
        return value!=null&&meaningfulTree(value);
    }
    private boolean recoveryCandidate(JSONObject nested,String recordKey){
        if(nested==null||nested.length()==0)return true;
        if("stockAtResearch".equals(recordKey)){
            JSONArray items=nested.optJSONArray("items");
            if(items!=null&&items.length()>0)return false;
            String status=nested.optString("load_status","");
            return !("OK".equals(status)&&nested.optLong("source_total",0L)==0L);
        }
        if("stockAtLedger".equals(recordKey)){
            JSONObject diagnostic=nested.optJSONObject("load_diagnostic");
            String stage=diagnostic==null?"":diagnostic.optString("stage","");
            return "PC_SOURCE_NOT_PRESENT".equals(stage)||
                "ENGINE_NOT_READY".equals(stage);
        }
        return false;
    }
    private boolean missingNestedBucket(JSONObject local,String recordKey){
        if(local==null)return true;
        if("stockAtLedger".equals(recordKey)||"stockAtResearch".equals(recordKey)){
            for(String nested:new String[]{"ma","gmo"}){
                String field="stockAtLedger".equals(recordKey)&&"ma".equals(nested)
                    ?"ma_subledger":nested;
                if(recoveryCandidate(local.optJSONObject(field),recordKey))return true;
            }
        }
        return false;
    }
    /** Supplement genuinely missing or failed nested buckets, but
     * never overwrite a complete phone trading ledger with old PC data.
     */
    private JSONObject mergeMissingNested(JSONObject local,JSONObject remote,String recordKey){
        if(!meaningfulRecord(local))return remote;
        if(!meaningfulRecord(remote)||!missingNestedBucket(local,recordKey))return local;
        String[] keys="stockAtLedger".equals(recordKey)
            ?new String[]{"ma_subledger","gmo"}:new String[]{"ma","gmo"};
        try{
            JSONObject merged=new JSONObject(local.toString());
            for(String nested:keys){
                JSONObject source=remote.optJSONObject(nested);
                if(recoveryCandidate(merged.optJSONObject(nested),recordKey)
                    &&meaningfulRecord(source))merged.put(nested,source);
            }
            return merged;
        }catch(Exception ignored){return local;}
    }


    private JSONObject localRecord(String key){return localRecord(copyCache(),key);}
    private JSONObject localRecord(JSONObject snapshot,String key){
        JSONObject all=snapshot.optJSONObject("local_records");if(all==null)return new JSONObject();
        try{
            String market="",strategy="",bucket="";
            if(key.startsWith("stock"))market="stock";else if(key.startsWith("coin"))market="coin";else return new JSONObject();
            if(key.contains("Shared"))strategy="shared";
            else if(key.contains("At"))strategy="at";
            else if(key.contains("Cw"))strategy="cw";
            else if(key.contains("General"))strategy="general";
            else if(key.contains("Ma"))strategy="ma";
            if(key.endsWith("Ledger"))bucket="ledger";
            else if(key.endsWith("Research"))bucket="research";
            else if(key.endsWith("Errors"))bucket="errors";
            JSONObject m=all.optJSONObject(market),st=m==null?null:m.optJSONObject(strategy);
            JSONObject out=st==null?null:st.optJSONObject(bucket);
            return out==null?new JSONObject():out;
        }catch(Exception e){return new JSONObject();}
    }

    private JSONObject record(String key){return record(copyCache(),key);}
    private JSONObject record(JSONObject snapshot,String key){
        JSONObject local=localRecord(snapshot,key);
        JSONObject p=snapshot.optJSONObject("records");
        JSONObject remote=null;
        if(p!=null){
            JSONObject envelope=p.optJSONObject(key);
            remote=envelope==null?null:envelope.optJSONObject("data");
        }
        if(meaningfulRecord(local))
            return mergeMissingNested(local,remote,key);
        if(meaningfulRecord(remote))return remote;
        return local==null?new JSONObject():local;
    }

    private JSONObject ledger(String m,String s){return ledger(copyCache(),m,s);}
    private JSONObject ledger(JSONObject snapshot,String m,String s){
        if("coin".equals(m))return record(snapshot,"ma".equals(s)?"coinMaLedger":"coinGeneralLedger");
        if("close".equals(s))return record(snapshot,"stockCwLedger");
        JSONObject base=record(snapshot,"stockAtLedger");
        if("ma".equals(s)){
            JSONObject x=base.optJSONObject("ma_subledger");return x==null?new JSONObject():x;
        }
        if("gmo".equals(s)){
            JSONObject x=base.optJSONObject("gmo");return x==null?new JSONObject():x;
        }
        return base;
    }

    private JSONObject research(String m,String s){return research(copyCache(),m,s);}
    private JSONObject research(JSONObject snapshot,String m,String s){
        if("coin".equals(m))return record(snapshot,"ma".equals(s)?"coinMaResearch":"coinGeneralResearch");
        if("close".equals(s))return record(snapshot,"stockCwResearch");
        JSONObject at=record(snapshot,"stockAtResearch");
        if("ma".equals(s)){
            JSONObject nested=at.optJSONObject("ma");
            if(nested!=null)return nested;
            JSONObject ma=ledger(snapshot,"stock","ma");JSONArray items=ma.optJSONArray("research");
            try{return new JSONObject().put("items",items==null?new JSONArray():items);}
            catch(Exception e){return new JSONObject();}
        }
        if("gmo".equals(s)){
            JSONObject nested=at.optJSONObject("gmo");
            return nested==null?new JSONObject():nested;
        }
        return at;
    }

    private JSONObject errors(String m,String s){
        if("coin".equals(m))return record("ma".equals(s)?"coinMaErrors":"coinGeneralErrors");
        if("close".equals(s))return record("stockCwErrors");
        return record("stockAtErrors");
    }

    private String tag(JSONObject r){
        String[] k={"strategy","strategy_name","engine","tf","mode","account","bucket"};
        for(String x:k){String v=r.optString(x,"").toLowerCase(Locale.ROOT);if(!v.isEmpty())return v;}return "";
    }

    private boolean belongs(JSONObject r,String m,String s){
        if("coin".equals(m)||"close".equals(s)||"ma".equals(s))return true;
        String t=tag(r);
        if("gmo".equals(s))return t.contains("gmo")||t.contains("수급");
        return !(t.contains("ma")||t.contains("gmo")||t.contains("close")||t.contains("cw")||t.contains("종가")||t.contains("수급"));
    }

    private JSONArray array(JSONObject o,String... keys){
        for(String k:keys){JSONArray a=o.optJSONArray(k);if(a!=null)return a;}return new JSONArray();
    }

    private JSONArray rows(JSONObject o,String m,String s,String... keys){
        JSONArray src=array(o,keys);if("coin".equals(m)||"close".equals(s)||"ma".equals(s))return src;
        JSONArray out=new JSONArray();
        for(int i=0;i<src.length();i++){JSONObject r=src.optJSONObject(i);if(r!=null&&belongs(r,m,s))out.put(r);}
        return out;
    }

    private double num(JSONObject o,String... keys){
        for(String k:keys){
            if(o.has(k)&&!o.isNull(k)){
                Object v=o.opt(k);if(v instanceof Number)return ((Number)v).doubleValue();
                try{return Double.parseDouble(String.valueOf(v).replace(",",""));}catch(Exception ignored){}
            }
        }
        return Double.NaN;
    }

    private boolean finite(double v){return !Double.isNaN(v)&&!Double.isInfinite(v);}

    private static final class Stats{
        JSONArray positions=new JSONArray(),pending=new JSONArray(),executions=new JSONArray(),research=new JSONArray();
        double realized=Double.NaN,unrealized=Double.NaN,total=Double.NaN,win=Double.NaN,target=Double.NaN;
        double researchActualTarget=Double.NaN,researchVirtualTarget=Double.NaN;
        int candidates=0,bought=0,confirmed=0,hits=0,closed=0;
        int researchActualEvaluated=0,researchActualHit=0,researchActualMiss=0,researchActualPending=0;
        int researchVirtualEvaluated=0,researchVirtualHit=0,researchVirtualMiss=0,researchVirtualPending=0,researchVirtualEntry=0;
        int flowPending=0,flowBought=0,flowHolding=0,flowSold=0;
        boolean flowKnown=false;
    }

    private Stats stats(String m,String s){
        Stats preloaded=uiSnapshot.totals.get(m+"/"+s);
        return preloaded==null?new Stats():preloaded;
    }
    private Stats calculateStats(JSONObject snapshot,String m,String s){
        Stats st=new Stats();JSONObject l=ledger(snapshot,m,s);
        st.positions=rows(l,m,s,"positions","holdings");
        st.pending=rows(l,m,s,"pending","pending_orders","orders","candidates");
        st.executions=rows(l,m,s,"executions","fills","transactions","trades");

        if("stock".equals(m)&&("at".equals(s)||"gmo".equals(s))){
            JSONArray ac=array(l,"accounts");double rr=0,uu=0;boolean rk=false,uk=false;
            for(int i=0;i<ac.length();i++){JSONObject a=ac.optJSONObject(i);if(a==null||!belongs(a,m,s))continue;
                double x=num(a,"realized_pnl","realized","net_realized");if(finite(x)){rr+=x;rk=true;}
                double y=num(a,"unrealized_pnl","holding","holding_pnl");if(finite(y)){uu+=y;uk=true;}
            }
            if(rk)st.realized=rr;if(uk)st.unrealized=uu;
        }
        if(!finite(st.realized))st.realized=num(l,"realized_pnl","realized_profit","net_realized","realized");
        if(!finite(st.unrealized))st.unrealized=num(l,"unrealized_pnl","unrealized_profit","holding_pnl","holding");
        if(!finite(st.realized))st.realized=sum(st.executions,true);
        if(!finite(st.unrealized))st.unrealized=sum(st.positions,false);
        if(finite(st.realized)&&finite(st.unrealized))st.total=st.realized+st.unrealized;
        else st.total=num(l,"total_pnl","net_pnl","profit","total_profit");

        int sell=0,buy=0,known=0,wins=0;
        for(int i=0;i<st.executions.length();i++){JSONObject r=st.executions.optJSONObject(i);if(r==null)continue;
            String side=r.optString("side",r.optString("type","")).toUpperCase(Locale.ROOT);
            if(side.contains("BUY")||side.contains("매수"))buy++;
            if(side.contains("SELL")||side.contains("매도")){sell++;double p=num(r,"net_profit","net_realized","net_pnl","pnl","profit");if(finite(p)){known++;if(p>0)wins++;}}
        }
        st.closed=sell;if(known>0)st.win=wins*100.0/known;
        double remoteWin=num(l,"win_rate","win_rate_pct");if(finite(remoteWin))st.win=remoteWin;

        JSONObject actual=l.optJSONObject("actual_target");
        if(actual!=null){
            double confirmed=num(actual,"hit");
            // A conditional 1/1 among completed positions is not 100%
            // of the 10 positions which entered. Outstanding target statuses
            // are denominator entries, never assumed MISS or SUCCESS.
            int pending=actual.optInt("pending",0);
            int decided=actual.optInt("evaluated",
                actual.optInt("hit",0)+actual.optInt("miss",0));
            int eligible=actual.optInt("eligible",decided+pending);
            if(finite(confirmed)&&eligible>0)st.target=100.0*confirmed/eligible;
            else st.target=num(actual,"rate_pct");
        }
        st.flowPending=st.pending.length();st.flowHolding=st.positions.length();st.flowBought=buy;st.flowSold=sell;
        JSONObject perf=l.optJSONObject("performance_by_frame");
        if(perf!=null){
            int fp=0,fb=0,fh=0,fs=0;boolean any=false;
            java.util.Iterator<String> keys=perf.keys();
            while(keys.hasNext()){
                String k=keys.next();if("all".equals(k))continue;
                JSONObject q=perf.optJSONObject(k);if(q==null)continue;any=true;
                fp+=q.optInt("pending_count",0);
                fb+=q.has("bought_count")?q.optInt("bought_count",0):q.optInt("total_trades",0);
                fh+=q.optInt("open_count",0);fs+=q.optInt("closed_count",0);
            }
            if(any){st.flowPending=fp;st.flowBought=fb;st.flowHolding=fh;st.flowSold=fs;}
        }

        JSONObject rs=research(snapshot,m,s);JSONArray items=rs.optJSONArray("items");
        if(items==null&&"ma".equals(s))items=l.optJSONArray("research");
        if(items==null)items=new JSONArray();
        JSONArray filtered=new JSONArray();
        for(int i=0;i<items.length();i++){JSONObject r=items.optJSONObject(i);if(r!=null&&("coin".equals(m)||"close".equals(s)||belongs(r,m,s)))filtered.put(r);}
        st.research=filtered;
        st.candidates=rs.has("signals")?rs.optInt("signals",filtered.length()):filtered.length();
        st.bought=rs.has("bought")?rs.optInt("bought",0):0;
        JSONObject ra=rs.optJSONObject("actual_summary"),rv=rs.optJSONObject("virtual_summary");
        if(ra!=null){
            st.researchActualEvaluated=ra.optInt("evaluated",0);st.researchActualHit=ra.optInt("hit",0);
            st.researchActualMiss=ra.optInt("miss",0);st.researchActualPending=ra.optInt("pending",0);
            st.researchActualTarget=num(ra,"rate_pct");
        }
        if(rv!=null){
            st.researchVirtualEvaluated=rv.optInt("evaluated",0);st.researchVirtualHit=rv.optInt("hit",0);
            st.researchVirtualMiss=rv.optInt("miss",0);st.researchVirtualPending=rv.optInt("pending",0);
            st.researchVirtualTarget=num(rv,"rate_pct");
        }
        st.researchVirtualEntry=rs.optInt("virtual_entry",0);
        st.confirmed=st.researchActualEvaluated+st.researchVirtualEvaluated;
        st.hits=st.researchActualHit+st.researchVirtualHit;
        return st;
    }

    private JSONArray filterTf(JSONArray src,String tf){
        JSONArray out=new JSONArray();
        if(src==null)return out;
        for(int i=0;i<src.length();i++){
            JSONObject r=src.optJSONObject(i);if(r==null)continue;
            String x=r.optString("tf",r.optString("frame",""));
            if(tf.equals(x))out.put(r);
        }
        return out;
    }

    private Stats statsTf(String m,String stg,String tf){
        Stats preloaded=uiSnapshot.frames.get(m+"/"+stg+"/"+tf);
        return preloaded==null?new Stats():preloaded;
    }
    private Stats calculateStatsTf(JSONObject snapshot,String m,String stg,String tf,Stats all){
        Stats st=new Stats();
        st.positions=filterTf(all.positions,tf);st.pending=filterTf(all.pending,tf);st.executions=filterTf(all.executions,tf);st.research=filterTf(all.research,tf);
        JSONObject l=ledger(snapshot,m,stg);
        JSONObject pf=l.optJSONObject("performance_by_frame");JSONObject p=pf==null?null:pf.optJSONObject(tf);
        if(p!=null){
            st.realized=num(p,"realized_won","realized_pnl","realized");
            st.unrealized=num(p,"holding_won","unrealized_pnl","holding");
            st.target=num(p,"actual_target_rate_pct");
            st.flowPending=p.optInt("pending_count",st.pending.length());
            st.flowBought=p.has("bought_count")?p.optInt("bought_count",0):p.optInt("total_trades",0);
            st.flowHolding=p.optInt("open_count",st.positions.length());
            st.flowSold=p.optInt("closed_count",0);
            st.flowKnown=true;
        }
        if(!finite(st.realized)||!finite(st.unrealized)){
            JSONArray ac=array(l,"accounts");
            for(int i=0;i<ac.length();i++){
                JSONObject a=ac.optJSONObject(i);if(a==null||!tf.equals(a.optString("tf","")))continue;
                double rr=num(a,"realized_pnl","realized","net_realized");
                double uu=num(a,"unrealized_pnl","holding","holding_pnl");
                if(finite(rr))st.realized=rr;if(finite(uu))st.unrealized=uu;
            }
        }
        if(!finite(st.realized))st.realized=sum(st.executions,true);
        if(!finite(st.unrealized))st.unrealized=sum(st.positions,false);
        if(finite(st.realized)&&finite(st.unrealized))st.total=st.realized+st.unrealized;

        int known=0,wins=0,sell=0;
        for(int i=0;i<st.executions.length();i++){
            JSONObject r=st.executions.optJSONObject(i);if(r==null)continue;
            String side=r.optString("side",r.optString("type","")).toUpperCase(Locale.ROOT);
            if(side.contains("SELL")||side.contains("매도")){
                sell++;double v=num(r,"net_profit","net_realized","net_pnl","pnl","profit");
                if(finite(v)){known++;if(v>0)wins++;}
            }
        }
        st.closed=sell;if(known>0)st.win=wins*100.0/known;
        st.candidates=0;st.bought=0;st.confirmed=0;st.hits=0;
        if(!st.flowKnown){
            st.flowPending=st.pending.length();
            st.flowHolding=st.positions.length();
            st.flowSold=st.closed;
            st.flowBought=Math.max(st.flowHolding+st.flowSold,st.bought);
        }
        return st;
    }

    private double[] trendValues(JSONArray executions){
        java.util.ArrayList<Double> pts=new java.util.ArrayList<>();double cum=0;
        if(executions==null)return new double[0];
        for(int i=executions.length()-1;i>=0;i--){
            JSONObject r=executions.optJSONObject(i);if(r==null)continue;
            String side=r.optString("side",r.optString("type","")).toUpperCase(Locale.ROOT);
            if(!(side.contains("SELL")||side.contains("매도")))continue;
            double v=num(r,"net_profit","net_realized","net_pnl","pnl","profit");if(!finite(v))continue;
            cum+=v;pts.add(cum);if(pts.size()>=60)break;
        }
        double[] out=new double[pts.size()];for(int i=0;i<pts.size();i++)out[i]=pts.get(i);return out;
    }

    private String tfLabel(String tf){
        if("5m".equals(tf))return "5분";
        if("30m".equals(tf))return "30분";
        if("60m".equals(tf))return "1시간";
        if("day".equals(tf))return "일봉";
        return tf;
    }

    private String[][] strategyList(String m){
        return "stock".equals(m)
            ?new String[][]{{"at","기준봉"},{"ma","MA"},{"close","종가베팅"},{"gmo","수급매매"}}
            :new String[][]{{"general","기준봉"},{"ma","MA"}};
    }

    private double sum(JSONArray a,boolean sellsOnly){
        double t=0;boolean known=false;
        for(int i=0;i<a.length();i++){JSONObject r=a.optJSONObject(i);if(r==null)continue;
            if(sellsOnly){String side=r.optString("side",r.optString("type","")).toUpperCase(Locale.ROOT);if(!(side.contains("SELL")||side.contains("매도")))continue;}
            double v=num(r,sellsOnly?new String[]{"net_profit","net_realized","net_pnl","pnl","profit"}:new String[]{"net_pnl","unrealized_pnl","pnl","paper_pnl"});
            if(finite(v)){t+=v;known=true;}
        }
        return known?t:Double.NaN;
    }

    private boolean bool(JSONObject r,String... keys){
        for(String k:keys)if(r.has(k)&&!r.isNull(k)){
            Object v=r.opt(k);
            if(v instanceof Boolean)return (Boolean)v;
            String raw=String.valueOf(v).trim().toLowerCase(Locale.ROOT);
            // 'NOT_FILLED', 'UNFILLED', 'NO_FILL' and 'FALSE' are negative,
            // never actual BUY fills. Do not reclassify virtual research rows.
            if(raw.equals("1")||raw.equals("true")||raw.equals("yes")||
               raw.equals("filled")||raw.equals("completed")||
               raw.equals("매수완료")||raw.equals("체결완료"))return true;
            if(raw.equals("0")||raw.equals("false")||raw.equals("no")||
               raw.equals("not_filled")||raw.equals("unfilled")||
               raw.equals("no_fill")||raw.equals("pending")||
               raw.equals("미체결")||raw.equals("미매수"))return false;
        }
        return false;
    }

    private Boolean target(JSONObject r){
        String[] keys={"target_hit","hit_target","target_reached","goal_hit","reached","touched_target","success","prebuy_target_touched_at"};
        for(String k:keys)if(r.has(k)&&!r.isNull(k)){
            Object v=r.opt(k);if(v instanceof Boolean)return (Boolean)v;
            String s=String.valueOf(v).trim().toLowerCase(Locale.ROOT);if(s.isEmpty())continue;
            if("1".equals(s)||s.contains("true")||s.contains("hit")||s.contains("reach")||s.contains("success")||s.contains("도달")||s.contains("성공"))return true;
            if("0".equals(s)||s.contains("false")||s.contains("fail")||s.contains("실패"))return false;
        }return null;
    }

    private void render(){
        if(content==null)return;content.removeAllViews();updateHeader();
        if(integratedMode){
            if("positions".equals(page))renderIntegratedPositions();
            else renderIntegratedHome();
        }
        else if("market".equals(page))renderMarket();
        else if("home".equals(page))renderHome();
        else if("positions".equals(page))renderList("보유 종목",stats(market,strategy).positions,"positions");
        else if("pending".equals(page))renderList("매수 대기",stats(market,strategy).pending,"pending");
        else if("executions".equals(page))renderList("체결 이력",stats(market,strategy).executions,"executions");
        else if("operation".equals(page))renderOperation();
        else if("research".equals(page))renderResearch();
        else if("analysis".equals(page))renderAiAnalysis();
        else if("scores".equals(page))renderScores();
        else if("errors".equals(page))renderErrors();
        else if("policyHistory".equals(page))renderPolicyHistory();
        else renderMore();
    }

    private LinearLayout marketEvidenceCard(JSONObject factor,boolean crypto){
        LinearLayout tile=card();
        tile.setPadding(dp(9),dp(7),dp(8),dp(6));
        tile.setOrientation(LinearLayout.VERTICAL);
        TextView name=text(factor.optString("name","근거 미기록"),11);
        name.setTextColor(Color.rgb(168,193,219));
        name.setMaxLines(2);
        tile.addView(name);
        // The impact is always the signed contribution: -100..+100.
        // The raw value remains an observation, not the market score.
        double impact=factor.optDouble("impact",Double.NaN);
        String display=finite(impact)?
            String.format(Locale.KOREA,"%+.1f / ±100",impact):"점수 미기록";
        TextView metric=text(display,15);
        metric.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
        metric.setTextColor(impact<0?Color.rgb(253,145,155):
            impact>0?Color.rgb(119,229,180):Color.rgb(229,237,253));
        tile.addView(metric);
        double observed=factor.optDouble("value",Double.NaN);
        if(finite(observed)){
            TextView raw=text((crypto?"기초지표 ":"관측값 ")+
                String.format(Locale.KOREA,"%+.2f",observed),10);
            raw.setTextColor(PremiumUi.MUTED);
            tile.addView(raw);
        }
        return tile;
    }

    private LinearLayout marketResearchPanel(String which,JSONObject snapshot){
        boolean crypto="coin".equals(which);
        LinearLayout panel=card();
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(11),dp(9),dp(11),dp(9));
        TextView heading=text(crypto?"코인 시장":"주식 시장",19);
        heading.setTypeface(null,Typeface.BOLD);
        heading.setTextColor(PremiumUi.TEXT);
        panel.addView(heading);
        if(snapshot==null)snapshot=new JSONObject();
        final JSONObject data=snapshot;
        String status=data.optString("status","수집 대기");
        TextView state=text(status,12);
        state.setTextColor(Color.rgb(159,191,225));
        state.setMaxLines(2);
        panel.addView(state);
        MarketRegimeGraphicView gauge=new MarketRegimeGraphicView(this);
        gauge.setScore(data);
        panel.addView(gauge,new LinearLayout.LayoutParams(-1,dp(105)));
        long at=data.optLong("asof",0L);
        if(at>0){
            TextView updated=text("갱신 "+formatKst(at*1000L),11);
            updated.setTextColor(PremiumUi.MUTED);
            panel.addView(updated);
        }

        TextView bandTitle=text("점수 구간별 평균 등락률",16);
        bandTitle.setTypeface(null,Typeface.BOLD);
        bandTitle.setPadding(0,dp(10),0,dp(8));
        panel.addView(bandTitle);
        LinearLayout labels=new LinearLayout(this);
        labels.setGravity(Gravity.CENTER_VERTICAL);
        TextView bandHeader=text("시장점수",13);
        bandHeader.setTextColor(PremiumUi.MUTED);
        labels.addView(bandHeader,new LinearLayout.LayoutParams(0,dp(30),1));
        TextView changeHeader=text("평균 등락률",13);
        changeHeader.setTextColor(PremiumUi.MUTED);
        changeHeader.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
        labels.addView(changeHeader,new LinearLayout.LayoutParams(0,dp(30),1));
        TextView durationHeader=text("관측시간",13);
        durationHeader.setTextColor(PremiumUi.MUTED);
        durationHeader.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
        labels.addView(durationHeader,new LinearLayout.LayoutParams(0,dp(30),1));
        panel.addView(labels);
        JSONArray groups=data.optJSONArray("bands");
        String[] marketBands={
            "-100~-81","-80~-61","-60~-41","-40~-21","-20~-1",
            "0~19","20~39","40~59","60~79","80~100"
        };
        for(String band:marketBands){
            JSONObject found=null;
            if(groups!=null)for(int j=0;j<groups.length();j++){
                JSONObject entry=groups.optJSONObject(j);
                if(entry!=null&&band.equals(entry.optString("band"))){
                    found=entry;break;
                }
            }
            boolean active=band.equals(data.optString("active_band",""));
            LinearLayout line=new LinearLayout(this);
            line.setOrientation(LinearLayout.HORIZONTAL);
            line.setGravity(Gravity.CENTER_VERTICAL);
            line.setPadding(dp(4),dp(2),dp(4),dp(2));
            line.setBackground(bg(active?PremiumUi.SURFACE_HIGH:
                Color.TRANSPARENT,active?PremiumUi.LINE:Color.TRANSPARENT,6));
            TextView interval=text(band,14);
            interval.setTextColor(active?Color.rgb(103,223,185):Color.rgb(188,210,233));
            if(active)interval.setTypeface(null,Typeface.BOLD);
            line.addView(interval,new LinearLayout.LayoutParams(0,dp(33),1));
            boolean hasReturn=found!=null&&found.has("avg_change_pp")&&
                !found.isNull("avg_change_pp")&&found.optInt("position_segments",0)>0;
            double delta=hasReturn?found.optDouble("avg_change_pp"):0;
            String deltaString=hasReturn?
                String.format(Locale.KOREA,"%+.2f%%",delta):"—";
            TextView change=text(deltaString,15);
            change.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
            change.setTypeface(null,Typeface.BOLD);
            if(hasReturn)change.setTextColor(delta>=0?
                Color.rgb(90,223,183):Color.rgb(255,133,149));
            line.addView(change,new LinearLayout.LayoutParams(0,dp(33),1));
            double hours=found==null?0:found.optDouble("observed_seconds",0)/3600.0;
            String hourText=hours>0&&hours<0.1?
                String.format(Locale.KOREA,"%.2f시간",hours):
                String.format(Locale.KOREA,"%.1f시간",hours);
            TextView hoursView=text(hourText,14);
            hoursView.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
            hoursView.setTextColor(PremiumUi.MUTED);
            line.addView(hoursView,new LinearLayout.LayoutParams(0,dp(33),1));
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,dp(38));
            lp.setMargins(0,dp(1),0,0);
            panel.addView(line,lp);
        }
        return panel;
    }

    private void renderMarket(){
        TextView title=text("시장 점수",21);
        title.setTypeface(null,Typeface.BOLD);
        content.addView(title);
        JSONObject local=uiSnapshot.packet.optJSONObject("local_records");
        JSONObject research=local==null?null:local.optJSONObject("market_research");
        JSONObject stock=research==null?null:research.optJSONObject("stock");
        JSONObject coin=research==null?null:research.optJSONObject("coin");
        boolean wide=getResources().getConfiguration().screenWidthDp>=720;
        LinearLayout panels=new LinearLayout(this);
        panels.setOrientation(wide?LinearLayout.HORIZONTAL:LinearLayout.VERTICAL);
        LinearLayout first=marketResearchPanel("stock",stock);
        LinearLayout second=marketResearchPanel("coin",coin);
        if(wide){
            panels.addView(first,new LinearLayout.LayoutParams(0,-2,1));
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,-2,1);
            lp.setMargins(dp(7),0,0,0);
            panels.addView(second,lp);
        }else{
            panels.addView(first,new LinearLayout.LayoutParams(-1,-2));
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);
            lp.setMargins(0,dp(7),0,0);
            panels.addView(second,lp);
        }
        content.addView(panels,new LinearLayout.LayoutParams(-1,-2));
    }


    private static final class IntegratedHolding {
        final String scopeMarket,scopeStrategy;
        final JSONObject row;
        IntegratedHolding(String m,String s,JSONObject r){
            scopeMarket=m;scopeStrategy=s;row=r;
        }
    }
    private java.util.List<IntegratedHolding> integratedHoldings(){
        java.util.ArrayList<IntegratedHolding> all=new java.util.ArrayList<>();
        java.util.HashSet<String> seen=new java.util.HashSet<>();
        for(String m:new String[]{"stock","coin"}){
            for(String[] def:strategyList(m)){
                String s=def[0];
                JSONArray pos=stats(m,s).positions;
                for(int i=0;i<pos.length();i++){
                    JSONObject r=pos.optJSONObject(i);
                    if(r==null)continue;
                    String status=r.optString("status","").toUpperCase(Locale.ROOT);
                    if("CLOSED".equals(status)||"SOLD".equals(status)||
                       "RESET_B_V1".equals(status))continue;
                    double qty=num(r,"qty","quantity","holding_qty");
                    if(finite(qty)&&qty<=0)continue;
                    String id=r.optString("id",r.optString("position_id","row_"+i));
                    String key=m+"/"+s+"/"+timeFrameOf(r)+"/"+id;
                    if(seen.add(key))all.add(new IntegratedHolding(m,s,r));
                }
            }
        }
        return all;
    }
    private void integratedTextLine(LinearLayout parent,String label,String value,int size){
        LinearLayout line=new LinearLayout(this);
        line.setOrientation(LinearLayout.HORIZONTAL);
        line.setGravity(Gravity.CENTER_VERTICAL);
        TextView t=text(label,12);
        t.setTextColor(PremiumUi.MUTED);
        line.addView(t,new LinearLayout.LayoutParams(0,dp(24),1));
        TextView v=text(value,size);
        v.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
        v.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        line.addView(v,new LinearLayout.LayoutParams(-2,dp(24)));
        parent.addView(line);
    }
    private String scopeTitle(String m,String s){
        for(String[] opt:strategyList(m))
            if(opt[0].equals(s))return ("stock".equals(m)?"주식 ":"코인 ")+opt[1];
        return ("stock".equals(m)?"주식 ":"코인 ")+s;
    }
    private LinearLayout integratedTrend(RealizedTrend.Series series,String caption,int heightDp){
        LinearLayout c=card();
        c.setPadding(dp(6),dp(6),dp(6),dp(4));
        PnlSparklineView chart=new PnlSparklineView(this);
        chart.setSeries(series==null?
            new RealizedTrend.Series(new long[0],new double[0]):series,caption);
        c.addView(chart,new LinearLayout.LayoutParams(-1,dp(heightDp-12)));
        return c;
    }
    private void renderIntegratedHome(){
        TextView title=text("통합 운용 대시보드",20);
        title.setTypeface(null,Typeface.BOLD);
        content.addView(title);
        double realized=0,unrealized=0;
        int knownR=0,knownU=0,totalScopes=0;
        for(String m:new String[]{"stock","coin"}){
            for(String[] def:strategyList(m)){
                Stats v=stats(m,def[0]);
                totalScopes++;
                if(finite(v.realized)){realized+=v.realized;knownR++;}
                if(finite(v.unrealized)){unrealized+=v.unrealized;knownU++;}
            }
        }
        java.util.List<IntegratedHolding> held=integratedHoldings();
        boolean complete=knownR==totalScopes&&knownU==totalScopes;
        LinearLayout hero=card();
        hero.setPadding(dp(15),dp(10),dp(15),dp(10));
        TextView info=text("전략 6개 · 주식 4 / 코인 2 · 보유 "+held.size()+"종목",12);
        info.setTextColor(PremiumUi.MUTED);hero.addView(info);
        TextView totalTitle=text("전체 누적손익",12);
        totalTitle.setTextColor(PremiumUi.MUTED);
        totalTitle.setPadding(0,dp(6),0,dp(2));hero.addView(totalTitle);
        double aggregate=realized+unrealized;
        TextView total=text(complete?WonFormat.signed(aggregate):"일부 원장 미수신",27);
        total.setTypeface(null,Typeface.BOLD);
        autoSingle(total,16,27);
        if(complete)paintProfit(total,aggregate);
        hero.addView(total);
        LinearLayout sums=new LinearLayout(this);
        sums.setOrientation(LinearLayout.HORIZONTAL);
        addLargeMoneyMetric(sums,"●","실현 ("+knownR+"/6)",knownR>0?realized:Double.NaN,
                            Color.rgb(79,210,247));
        addLargeMoneyMetric(sums,"◇","평가 ("+knownU+"/6)",knownU>0?unrealized:Double.NaN,
                            Color.rgb(184,158,255));
        LinearLayout.LayoutParams sp=new LinearLayout.LayoutParams(-1,dp(55));
        sp.setMargins(0,dp(7),0,0);hero.addView(sums,sp);
        LinearLayout.LayoutParams h=new LinearLayout.LayoutParams(-1,-2);
        h.setMargins(0,dp(7),0,0);content.addView(hero,h);

        TextView allTrend=text("전 전략 실현손익 추세",15);
        allTrend.setTypeface(null,Typeface.BOLD);
        allTrend.setPadding(dp(2),dp(13),0,dp(4));
        content.addView(allTrend);
        LinearLayout graph=integratedTrend(uiSnapshot.trends.get("integrated/all"),
            "주식 + 코인 · 실현손익 합산",151);
        content.addView(graph,new LinearLayout.LayoutParams(-1,dp(151)));

        TextView strategyTitle=text("전략별 실적 · 실현손익 추세",15);
        strategyTitle.setTypeface(null,Typeface.BOLD);
        strategyTitle.setPadding(dp(2),dp(13),0,dp(6));
        content.addView(strategyTitle);
        boolean wide=getResources().getConfiguration().screenWidthDp>=720;
        LinearLayout pair=null;int count=0;
        for(String m:new String[]{"stock","coin"}){
            for(String[] def:strategyList(m)){
                String s=def[0];
                Stats v=stats(m,s);
                LinearLayout tile=card();
                tile.setPadding(dp(9),dp(7),dp(9),dp(7));
                LinearLayout heading=new LinearLayout(this);
                heading.setGravity(Gravity.CENTER_VERTICAL);
                TextView name=text(scopeTitle(m,s),14);
                name.setTypeface(null,Typeface.BOLD);
                heading.addView(name,new LinearLayout.LayoutParams(0,dp(25),1));
                TextView hold=text("보유 "+v.positions.length(),11);
                hold.setTextColor(PremiumUi.MUTED);
                heading.addView(hold);
                tile.addView(heading);
                LinearLayout figures=new LinearLayout(this);
                figures.setGravity(Gravity.CENTER_VERTICAL);
                TextView current=text(finite(v.total)?WonFormat.signed(v.total):"손익 미수신",20);
                current.setTypeface(null,Typeface.BOLD);
                autoSingle(current,12,20);
                if(finite(v.total))paintProfit(current,v.total);
                figures.addView(current,new LinearLayout.LayoutParams(0,dp(29),1));
                TextView win=text("매도 "+v.flowSold+"건",11);
                win.setTextColor(PremiumUi.MUTED);figures.addView(win);
                tile.addView(figures);
                RealizedTrend.Series series=uiSnapshot.trends.get(
                    StrategyDashboardScope.trend(m,s,"all"));
                PnlSparklineView chart=new PnlSparklineView(this);
                chart.setSeries(series==null?
                    new RealizedTrend.Series(new long[0],new double[0]):series,
                    "실현 "+(finite(v.realized)?WonFormat.signed(v.realized):"—"));
                tile.addView(chart,new LinearLayout.LayoutParams(-1,dp(108)));
                tile.setContentDescription(scopeTitle(m,s)+" · 손익 "+
                    (finite(v.total)?WonFormat.signed(v.total):"미수신")+
                    " · 탭하면 해당 전략 열기");
                final String chosenMarket=m,chosenStrategy=s;
                tile.setOnClickListener(view->{
                    integratedMode=false;market=chosenMarket;strategy=chosenStrategy;
                    page="home";resetHistoryFilter();persistNavigation();showShell();
                });
                PremiumUi.tapMotion(tile);
                if(!wide){
                    LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);
                    lp.setMargins(0,0,0,dp(6));content.addView(tile,lp);
                }else{
                    if(count%2==0){
                        pair=new LinearLayout(this);
                        pair.setOrientation(LinearLayout.HORIZONTAL);
                        LinearLayout.LayoutParams rowLp=
                            new LinearLayout.LayoutParams(-1,-2);
                        rowLp.setMargins(0,0,0,dp(6));content.addView(pair,rowLp);
                    }
                    LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,-2,1);
                    if(count%2!=0)lp.setMargins(dp(6),0,0,0);
                    pair.addView(tile,lp);
                }
                count++;
            }
        }
    }
    private void renderIntegratedPositions(){
        java.util.List<IntegratedHolding> all=integratedHoldings();
        TextView heading=text("통합 보유종목 · "+all.size()+"건",19);
        heading.setTypeface(null,Typeface.BOLD);
        heading.setPadding(0,0,0,dp(8));
        content.addView(heading);
        if(all.isEmpty()){
            empty("주식·코인 전략에서 확인된 보유종목 없음");
            return;
        }
        for(IntegratedHolding holding:all){
            JSONObject p=holding.row;
            LinearLayout tile=card();
            tile.setPadding(dp(10),dp(8),dp(10),dp(7));
            LinearLayout top=new LinearLayout(this);
            top.setGravity(Gravity.CENTER_VERTICAL);
            TextView symbol=text(nameOf(p),15);
            symbol.setTypeface(null,Typeface.BOLD);
            symbol.setSingleLine(true);
            bindSymbolFourTap(symbol,p,holding.scopeMarket);
            top.addView(symbol,new LinearLayout.LayoutParams(0,dp(26),1));
            TextView origin=text(scopeTitle(holding.scopeMarket,holding.scopeStrategy)+
                " "+tfLabel(timeFrameOf(p)),11);
            origin.setTextColor(PremiumUi.MUTED);
            top.addView(origin);
            tile.addView(top);
            double pnl=tradePnL(p,"positions");
            double rate=tradeReturnPct(p,"positions",pnl);
            String detail="매수가 "+formatMaybeMoney(p,"entry_price","entry","avg_buy_price")+
                "    현재가 "+formatMaybeMoney(p,"last_price","mark","current_price")+
                "    수량 "+formatMaybeNumber(p,"qty","quantity","holding_qty");
            TextView prices=text(detail,12);
            prices.setTextColor(Color.rgb(168,193,219));
            prices.setMaxLines(2);
            tile.addView(prices);
            TextView result=text("평가손익 "+
                (finite(pnl)?WonFormat.signed(pnl):"—")+
                (finite(rate)?" ("+signedPct(rate)+")":""),14);
            if(finite(pnl))paintProfit(result,pnl);
            tile.addView(result);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);
            lp.setMargins(0,0,0,dp(6));
            content.addView(tile,lp);
        }
    }

    private void renderHome(){
        Stats selected=stats(market,strategy);
        if("stock".equals(market)&&"gmo".equals(strategy)&&
           selected.positions.length()+selected.executions.length()+selected.research.length()==0){
            JSONObject diag=ledger(market,strategy).optJSONObject("load_diagnostic");
            String detail=diag==null?"원장 확인 필요":diag.optString("stage","원장 확인 필요");
            TextView warn=text("◉  수급매매 원장 확인 필요 · "+detail,11);
            warn.setTextColor(Color.rgb(255,119,133));
            content.addView(warn,new LinearLayout.LayoutParams(-1,dp(22)));
        }
        LinearLayout headline=new LinearLayout(this);headline.setGravity(Gravity.CENTER_VERTICAL);
        TextView title=text(strategyName(market,strategy),20);
        title.setTypeface(null,Typeface.BOLD);
        headline.addView(title,new LinearLayout.LayoutParams(0,dp(29),1));
        TextView source=text("● 실제 계좌",12);
        source.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
        source.setTextColor(Color.rgb(132,177,213));
        headline.addView(source,new LinearLayout.LayoutParams(dp(100),dp(29)));
        content.addView(headline,new LinearLayout.LayoutParams(-1,dp(32)));

        String feedName="stock".equals(market)?"키움 · KOSPI/KOSDAQ":"업비트 · KRW TOP35";
        content.addView(feedCard(market,feedName),new LinearLayout.LayoutParams(-1,dp(46)));

        // A single performance strip on landscape; no stacked 167dp+142dp panels.
        final boolean wide=getResources().getConfiguration().screenWidthDp>=680;
        LinearLayout pnl=card();
        TextView pl=text("◆  누적손익",13);
        pl.setTextColor(Color.rgb(160,180,213));
        pnl.addView(pl,new LinearLayout.LayoutParams(-1,dp(22)));
        TextView totalValue=text(WonFormat.signed(selected.total),37);
        totalValue.setTypeface(null,Typeface.BOLD);
        autoSingle(totalValue,19,37);paintProfit(totalValue,selected.total);
        pnl.addView(totalValue,new LinearLayout.LayoutParams(-1,dp(53)));
        LinearLayout split=new LinearLayout(this);split.setOrientation(LinearLayout.HORIZONTAL);
        addLargeMoneyMetric(split,"●","실현",selected.realized,Color.rgb(79,210,247));
        addLargeMoneyMetric(split,"◇","평가",selected.unrealized,Color.rgb(184,158,255));
        pnl.addView(split,new LinearLayout.LayoutParams(-1,dp(55)));
        pnl.setContentDescription(strategyName(market,strategy)+" 누적손익 "+
            money(selected.total)+", 실현손익 "+money(selected.realized)+
            ", 평가손익 "+money(selected.unrealized));

        LinearLayout hero=new LinearLayout(this);hero.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout winCard=card();DonutMetricView win=new DonutMetricView(this);
        win.setMetric("실현손익 승률",selected.win,pct(selected.win));
        winCard.addView(win,new LinearLayout.LayoutParams(-1,-1));
        hero.addView(winCard,new LinearLayout.LayoutParams(0,-1,1));
        LinearLayout hitCard=card();DonutMetricView hit=new DonutMetricView(this);
        hit.setMetric("목표확인 비중",selected.target,pct(selected.target));
        hitCard.addView(hit,new LinearLayout.LayoutParams(-1,-1));
        LinearLayout.LayoutParams hp=new LinearLayout.LayoutParams(0,-1,1);
        hp.setMargins(dp(6),0,0,0);hero.addView(hitCard,hp);

        if(wide){
            LinearLayout overview=new LinearLayout(this);
            overview.setOrientation(LinearLayout.HORIZONTAL);
            LinearLayout.LayoutParams plp=new LinearLayout.LayoutParams(0,dp(149),1.12f);
            overview.addView(pnl,plp);
            LinearLayout.LayoutParams hrp=new LinearLayout.LayoutParams(0,dp(149),1f);
            hrp.setMargins(dp(7),0,0,0);
            overview.addView(hero,hrp);
            LinearLayout.LayoutParams olp=new LinearLayout.LayoutParams(-1,dp(149));
            olp.setMargins(0,dp(7),0,0);content.addView(overview,olp);
        }else{
            LinearLayout.LayoutParams plp=new LinearLayout.LayoutParams(-1,dp(150));
            plp.setMargins(0,dp(7),0,0);content.addView(pnl,plp);
            LinearLayout.LayoutParams hrp=new LinearLayout.LayoutParams(-1,dp(124));
            hrp.setMargins(0,dp(6),0,0);content.addView(hero,hrp);
        }

        // Counts double as direct navigation; the entire card is a touch target.
        LinearLayout activity=new LinearLayout(this);activity.setOrientation(LinearLayout.HORIZONTAL);
        addDashboardKpi(activity,"positions","보유",selected.positions.length(),"positions");
        addDashboardKpi(activity,"pending","대기",selected.pending.length(),"pending");
        addDashboardKpi(activity,"buy","매수",selected.flowBought,"executions");
        addDashboardKpi(activity,"sell","매도",selected.flowSold,"executions");
        LinearLayout.LayoutParams ap=new LinearLayout.LayoutParams(-1,dp(65));
        ap.setMargins(0,dp(7),0,0);content.addView(activity,ap);

        if(StrategyDashboardScope.showFrameCharts(strategy)){
            TextView sec=text("시간봉별 성과",17);
            sec.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
            sec.setTextColor(PremiumUi.TEXT);
            sec.setPadding(dp(2),dp(10),0,dp(5));
            content.addView(sec);
            boolean sideBySide=getResources().getConfiguration().screenWidthDp>=720;
            LinearLayout pair=null;
            int pairCount=0;
            for(String tf:new String[]{"5m","30m","60m","day"}){
                Stats q=statsTf(market,strategy,tf);
                TimeframeAccountFlowView flow=new TimeframeAccountFlowView(this);
                flow.setData(tfLabel(tf),q.total,q.realized,q.unrealized,q.win,q.target,
                    q.flowPending,q.flowBought,q.flowHolding,q.flowSold);
                flow.setContentDescription(tfLabel(tf)+" 누적손익 "+money(q.total)+
                    ", 실현손익 "+money(q.realized)+", 평가손익 "+money(q.unrealized)+
                    ", 실현 승률 "+pct(q.win)+", 목표확인 비중 "+pct(q.target)+
                    ", 대기 "+q.flowPending+", 매수 "+q.flowBought+
                    ", 보유 "+q.flowHolding+", 매도 "+q.flowSold);
                if(!sideBySide){
                    LinearLayout.LayoutParams fp=new LinearLayout.LayoutParams(-1,dp(172));
                    if(!"5m".equals(tf))fp.setMargins(0,dp(6),0,0);
                    content.addView(flow,fp);
                    continue;
                }
                if(pairCount==0){
                    pair=new LinearLayout(this);
                    pair.setOrientation(LinearLayout.HORIZONTAL);
                    LinearLayout.LayoutParams pairParams=new LinearLayout.LayoutParams(-1,dp(172));
                    pairParams.setMargins(0,dp(6),0,0);
                    content.addView(pair,pairParams);
                }
                LinearLayout.LayoutParams fp=new LinearLayout.LayoutParams(0,dp(172),1);
                if(pairCount>0)fp.setMargins(dp(7),0,0,0);
                pair.addView(flow,fp);
                pairCount=(pairCount+1)%2;
            }
        }

        TextView trendTitle=text("⌁  "+strategyName(market,strategy)+" · 실현손익 추세",16);
        trendTitle.setTypeface(null,Typeface.BOLD);
        trendTitle.setTextColor(Color.rgb(213,228,245));
        trendTitle.setPadding(dp(2),dp(10),0,dp(3));content.addView(trendTitle);
        // Current strategy only; never merge AT, MA, CLOSE or GMO automatically.
        addTrendCard("all",strategyName(market,strategy)+" 누적",null);
        if(StrategyDashboardScope.showFrameCharts(strategy)){
            addTrendCard("5m","5분봉","30m");
            addTrendCard("60m","1시간봉","day");
        }
    }

    private void addDashboardKpi(LinearLayout row,String glyph,String label,int value,String destination){
        LinearLayout tile=card();
        tile.setOrientation(LinearLayout.HORIZONTAL);
        tile.setGravity(Gravity.CENTER_VERTICAL);
        tile.setPadding(dp(6),dp(6),dp(5),dp(6));
        tile.setBackground(PremiumUi.rounded(this,PremiumUi.SURFACE,PremiumUi.LINE,12));
        int shade="pending".equals(glyph)?PremiumUi.WAIT:
            "sell".equals(glyph)?PremiumUi.PROFIT:accent();
        PremiumIconView mark=new PremiumIconView(this,glyph,shade);
        tile.addView(mark,new LinearLayout.LayoutParams(dp(25),dp(34)));
        LinearLayout summary=new LinearLayout(this);summary.setOrientation(LinearLayout.VERTICAL);
        TextView count=text(String.valueOf(value),24);
        count.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
        count.setTextColor(PremiumUi.TEXT);
        autoSingle(count,17,24);
        summary.addView(count,new LinearLayout.LayoutParams(-1,dp(29)));
        TextView caption=text(label,12);
        caption.setTextColor(PremiumUi.MUTED);
        summary.addView(caption,new LinearLayout.LayoutParams(-1,dp(19)));
        tile.addView(summary,new LinearLayout.LayoutParams(0,dp(48),1));
        tile.setContentDescription(label+" "+value+"건, 목록 열기");
        tile.setOnClickListener(v->{page=destination;persistNavigation();showShell();});
        PremiumUi.tapMotion(tile);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(64),1);
        if(row.getChildCount()>0)lp.setMargins(dp(5),0,0,0);
        row.addView(tile,lp);
    }

    private LinearLayout chartView(String frame,String label){
        LinearLayout panel=card();
        PnlSparklineView chart=new PnlSparklineView(this);
        RealizedTrend.Series series=uiSnapshot.trends.get(
            StrategyDashboardScope.trend(market,strategy,frame));
        if(series==null)series=new RealizedTrend.Series(new long[0],new double[0]);
        chart.setSeries(series,label);
        chart.setContentDescription(label+" 실현손익 추세, 최근 "+
            (series.values.length>0?WonFormat.signed(series.values[series.values.length-1]):"체결기록 없음"));
        panel.addView(chart,new LinearLayout.LayoutParams(-1,dp(136)));
        return panel;
    }

    private void addTrendCard(String frame,String label,String pairedFrame){
        if(pairedFrame==null){
            LinearLayout full=chartView(frame,label);
            LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,dp(152));
            params.setMargins(0,dp(6),0,0);
            content.addView(full,params);
            return;
        }
        LinearLayout row=new LinearLayout(this);row.setOrientation(LinearLayout.HORIZONTAL);
        row.addView(chartView(frame,label),new LinearLayout.LayoutParams(0,dp(152),1));
        LinearLayout.LayoutParams second=new LinearLayout.LayoutParams(0,dp(152),1);
        second.setMargins(dp(6),0,0,0);
        row.addView(chartView(pairedFrame,tfLabel(pairedFrame)),second);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,dp(152));
        params.setMargins(0,dp(6),0,0);
        content.addView(row,params);
    }

    private void addLargeMoneyMetric(LinearLayout row,String glyph,String label,
                                      double amount,int accent){
        LinearLayout tile=new LinearLayout(this);tile.setOrientation(LinearLayout.VERTICAL);
        tile.setPadding(dp(9),dp(5),dp(7),dp(3));
        tile.setBackground(bg(PremiumUi.SURFACE_HIGH,PremiumUi.LINE,11));
        LinearLayout labelRow=new LinearLayout(this);labelRow.setOrientation(LinearLayout.HORIZONTAL);
        TextView icon=text(glyph,12);icon.setTextColor(accent);
        labelRow.addView(icon,new LinearLayout.LayoutParams(dp(19),dp(17)));
        TextView caption=text(label,12);caption.setTextColor(PremiumUi.MUTED);
        labelRow.addView(caption,new LinearLayout.LayoutParams(-1,dp(17)));
        tile.addView(labelRow);
        TextView value=text(WonFormat.signed(amount),21);
        value.setTypeface(null,Typeface.BOLD);autoSingle(value,13,21);
        paintProfit(value,amount);
        tile.addView(value,new LinearLayout.LayoutParams(-1,dp(30)));
        tile.setContentDescription(label+"손익 "+money(amount));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(55),1);
        if(row.getChildCount()>0)lp.setMargins(dp(6),0,0,0);
        row.addView(tile,lp);
    }

    private View feedCard(String m,String title){
        LinearLayout c=new LinearLayout(this);c.setOrientation(LinearLayout.HORIZONTAL);c.setGravity(Gravity.CENTER_VERTICAL);c.setPadding(dp(13),0,dp(13),0);
        c.setBackground(bg(PremiumUi.SURFACE,PremiumUi.LINE,14));
        JSONObject rt=copyCache().optJSONObject("realtime");JSONObject r=rt==null?null:rt.optJSONObject(m);boolean on=r!=null&&r.optBoolean("connected",false);
        TextView dot=text("●",10);dot.setTextColor(on?Color.rgb(86,214,170):Color.rgb(255,176,92));c.addView(dot,new LinearLayout.LayoutParams(dp(20),dp(34)));
        LinearLayout copy=new LinearLayout(this);copy.setOrientation(LinearLayout.VERTICAL);copy.setGravity(Gravity.CENTER_VERTICAL);
        TextView t=text(title,13);t.setTypeface(null,Typeface.BOLD);t.setTextColor(Color.rgb(220,226,239));copy.addView(t);
        String d=r==null?"연결 확인 중":r.optString("detail","");int n=r==null?0:r.optInt("subscribed",0);
        JSONObject runtime=copyCache().optJSONObject("strategy_runtime");
        JSONObject feed=runtime==null?null:runtime.optJSONObject("stock_feed");
        if("stock".equals(m)&&!on&&feed!=null){
            String stage=feed.optString("stage",""),reason=feed.optString("detail","");
            if(!stage.isEmpty())d=stage+(reason.isEmpty()?"":" · "+reason);
        }
        TextView x=text((on?"실시간 수신":"시세 대기")+(n>0?" · 등록 "+n+"종목":"")+(d.isEmpty()?"":" · "+d),12);
        int feedColor=on?Color.rgb(62,224,181):
            HistorySelection.semantic(d)==-1?Color.rgb(255,119,133):
            Color.rgb(255,194,91);
        dot.setTextColor(feedColor);
        x.setMaxLines(2);x.setTextColor(feedColor);copy.addView(x);
        c.addView(copy,new LinearLayout.LayoutParams(0,dp(42),1));
        TextView badge=text(on?"LIVE":"WAIT",11);badge.setGravity(Gravity.CENTER);badge.setTypeface(null,Typeface.BOLD);badge.setTextColor(feedColor);badge.setBackground(bg(Color.rgb(22,26,36),Color.rgb(48,54,70),10));c.addView(badge,new LinearLayout.LayoutParams(dp(54),dp(28)));
        if("stock".equals(m))c.setOnClickListener(v->showKiwoomFeedDiagnosis());
        return c;
    }

    private void showKiwoomFeedDiagnosis(){
        JSONObject all=copyCache(),engine=all.optJSONObject("strategy_runtime");
        JSONObject stock=engine==null?null:engine.optJSONObject("stock");
        JSONObject feed=engine==null?null:engine.optJSONObject("stock_feed");
        JSONObject stages=feed==null?null:feed.optJSONObject("preload");
        JSONObject transport=feed==null?null:feed.optJSONObject("transport");
        JSONObject probes=all.optJSONObject("broker_probe");
        JSONObject oauth=probes==null?null:probes.optJSONObject("kiwoom");
        JSONObject auth=all.optJSONObject("broker_storage");
        String credential=auth!=null&&auth.optBoolean("kiwoom_saved",false)?"키 저장됨":"키 미등록 / 복호화 실패";
        String oauthState=oauth==null?"인증검사 미실시":
            oauth.optBoolean("ok",false)?"REST 토큰 발급 성공":
            "REST 인증 실패 · "+oauth.optString("message","응답 없음");
        String msg="API 키: "+credential+"\n인증: "+oauthState+
            "\n전략 상태: "+(stock==null?"전략엔진 준비":stock.optString("status","확인 중"))+
            "\n진행 단계: "+(feed==null?"기록 없음":feed.optString("stage","없음"))+
            "\n상세: "+(feed==null?"":feed.optString("detail",""))+
            "\nWebSocket: "+(transport==null?"정보 없음":transport.optString("state","정보 없음"))+
            (transport==null?"":" · "+transport.optString("reason",""))+
            "\n구독 요청: "+(feed==null?0:feed.optInt("subscribed",0))+"종목";
        if(stages!=null)msg+="\n사전 로딩: "+stages.optString("stage","")+
            (stages.optBoolean("failed",false)?" · 실패":"")+
            " · "+stages.optString("detail","");
        new AlertDialog.Builder(this).setTitle("키움 실시간 WAIT 원인 진단")
            .setMessage(msg)
            .setNegativeButton("닫기",null)
            .setNeutralButton("REST 인증검사",(d,w)->runBrokerProbe(false))
            .setPositiveButton("시세 재연결",(d,w)->restartFeeds())
            .show();
    }
    private void addMini(LinearLayout row,String label,String value){
        LinearLayout c=new LinearLayout(this);c.setOrientation(LinearLayout.VERTICAL);c.setGravity(Gravity.CENTER_VERTICAL);
        TextView a=text(label,12);a.setTextColor(Color.rgb(142,152,173));c.addView(a,new LinearLayout.LayoutParams(-1,dp(22)));
        TextView b=text(value,19);b.setTypeface(null,Typeface.BOLD);autoSingle(b,14,19);
        int baseline=Color.rgb(235,238,246);
        if(label.contains("손익")||label.contains("수익")){
            b.setTextColor(value.startsWith("-")||value.startsWith("−")?
                Color.rgb(255,119,133):value.startsWith("+")?
                Color.rgb(62,224,181):Color.rgb(255,194,91));
        }else b.setTextColor(colorByStatus(value,baseline));
        c.addView(b,new LinearLayout.LayoutParams(-1,dp(30)));
        row.addView(c,new LinearLayout.LayoutParams(0,dp(54),1));
    }

    private void addKpi(LinearLayout row,String label,String value){
        LinearLayout tile=card();tile.setGravity(Gravity.CENTER_VERTICAL);
        TextView caption=text(label,10);caption.setTextColor(Color.rgb(150,165,190));
        tile.addView(caption,new LinearLayout.LayoutParams(-1,dp(17)));
        TextView number=text(value,24);number.setTypeface(null,Typeface.BOLD);
        autoSingle(number,15,24);number.setTextColor(Color.rgb(241,248,255));
        tile.addView(number,new LinearLayout.LayoutParams(-1,dp(32)));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(66),1);
        if(row.getChildCount()>0)lp.setMargins(dp(6),0,0,0);
        row.addView(tile,lp);
    }

    private double usefulNumber(JSONObject r,String... fields){
        return num(r,fields);
    }
    private String scenarioSummary(JSONObject r){
        // Read-only hold diagnosis comes from the actual position.hold_days,
        // not from a guessed target price or the reference candle timestamp.
        String hold=r.optString("hold_exit_diagnostic","").trim();
        if(!hold.isEmpty())return "보유기간 / 매도 판단 · "+hold;
        String state=r.optString("exit_scenario",r.optString("next_exit_scenario",r.optString("sell_scenario",""))).trim();
        if(!state.isEmpty()&&!state.equalsIgnoreCase("null"))return "가까운 매도 조건 · "+state;
        double now=usefulNumber(r,"last_price","current_price","market_price","price");
        double first=usefulNumber(r,"first_target","target","target_price","first_exit_price");
        double entry=usefulNumber(r,"entry_price","buy_price","avg_buy_price");
        if(finite(now)&&finite(first)&&first>0){
            if(now>=first)return "가까운 매도 조건 · 1차 목표가 도달 (체결 확인 필요)";
            return "가까운 매도 조건 · 1차 목표까지 "+pct((first/Math.max(now,.000001)-1)*100.0);
        }
        if(finite(now)&&finite(entry)&&entry>0)
            return "현재 손익률 "+pct((now/entry-1.0)*100.0)+" · 매도 조건 판정자료 부족";
        return "매도 조건 판정 대기 · 실시간 가격/목표가 확인 필요";
    }
    private String cellValue(JSONObject r,String kind,String key){
        if("positions".equals(kind)){
            switch(key){
                case "a":return "보유 "+number(usefulNumber(r,"qty","quantity","holding_qty"));
                case "b":return "매수가 "+formatMaybeMoney(r,"entry_price","avg_buy_price","buy_price");
                case "c":
                    double last=num(r,"last_price","mark","current_price","market_price","price");
                    return finite(last)?"현재가 "+money(last):"현재가 시세 미수신";
                default:return "1차 목표 "+formatMaybeMoney(r,"first_target","target","target_price");
            }
        }
        if("pending".equals(kind)){
            switch(key){
                case "a":return "점수 "+formatMaybeNumber(r,"score","signal_score");
                case "b":return "매수가 "+formatMaybeMoney(r,"limit_price","entry_price","price");
                case "c":return "목표가 "+formatMaybeMoney(r,"first_target","target","target_price");
                default:return "대기 상태 "+r.optString("status",r.optString("state","확인 중"));
            }
        }
        switch(key){
            case "a":return "구분 "+tradeSideOf(r);
            case "b":return "체결가 "+formatMaybeMoney(r,"price","fill_price");
            case "c":return "체결량 "+formatMaybeNumber(r,"qty","quantity","fill_qty");
            default:return "순손익 "+formatMaybeMoney(r,"net_profit","net_realized","net_pnl","profit");
        }
    }
    private String formatMaybeNumber(JSONObject r,String... fields){
        double value=usefulNumber(r,fields);
        return finite(value)?number(value):"미기록";
    }
    private String formatMaybeMoney(JSONObject r,String... fields){
        double value=usefulNumber(r,fields);
        return finite(value)?money(value):"미기록";
    }
    private void addGridCell(LinearLayout parent,String value){
        TextView t=text(value,13);
        int standard=Color.rgb(202,215,234);
        if(value.startsWith("순손익 ")||value.startsWith("현재 손익률 ")){
            double v=Double.NaN;
            try{v=Double.parseDouble(value.replaceAll("[^0-9.\\-]",""));}catch(Exception ignored){}
            t.setTextColor(Double.isNaN(v)?standard:v<0?Color.rgb(255,119,133):
                v>0?Color.rgb(62,224,181):Color.rgb(255,194,91));
        }else t.setTextColor(colorByStatus(value,standard));
        t.setPadding(dp(2),dp(4),dp(2),dp(4));t.setMaxLines(2);
        parent.addView(t,new LinearLayout.LayoutParams(0,-2,1));
    }
    private int historyStart(String kind,int total){
        String key=market+"/"+strategy+"/"+kind+"/"+activeHistoryFilter();
        if(!key.equals(historyContext)){
            historyContext=key;
            historyOffset=0;
        }
        historyOffset=HistoryPager.start(historyOffset,total);
        return historyOffset;
    }

    private void addHistoryPager(String kind,int total){
        if(total<=HistoryPager.PAGE_SIZE)return;
        int start=historyStart(kind,total),end=HistoryPager.end(start,total);
        LinearLayout navigation=new LinearLayout(this);
        navigation.setOrientation(LinearLayout.HORIZONTAL);
        navigation.setGravity(Gravity.CENTER_VERTICAL);
        navigation.setPadding(0,dp(4),0,dp(5));
        TextView count=text((start+1)+"~"+end+" / "+total+"건",12);
        count.setTextColor(Color.rgb(156,177,204));
        navigation.addView(count,new LinearLayout.LayoutParams(0,dp(42),1));
        Button previous=button("이전",false,()->{
            historyOffset=HistoryPager.previous(historyOffset,total);
            render();
        });
        previous.setEnabled(start>0);
        navigation.addView(previous,new LinearLayout.LayoutParams(dp(68),dp(40)));
        Button next=button("다음",true,()->{
            historyOffset=HistoryPager.next(historyOffset,total);
            render();
        });
        next.setEnabled(end<total);
        LinearLayout.LayoutParams np=new LinearLayout.LayoutParams(dp(68),dp(40));
        np.setMargins(dp(6),0,0,0);navigation.addView(next,np);
        content.addView(navigation,new LinearLayout.LayoutParams(-1,-2));
    }

    /** Exactly comparable numbers; never use a reference candle as BUY cost. */
    private double positionBuyPrice(JSONObject r){
        return usefulNumber(r,"entry_price","entry","avg_buy_price","buy_price");
    }
    private double positionMark(JSONObject r){
        return usefulNumber(r,"last_price","mark","current_price","market_price");
    }
    private double tradePnL(JSONObject r,String kind){
        if("positions".equals(kind)){
            double value=usefulNumber(r,"net_pnl","unrealized_pnl","paper_pnl","pnl");
            if(finite(value))return value;
            double entry=positionBuyPrice(r),mark=positionMark(r);
            double qty=usefulNumber(r,"qty","quantity","holding_qty");
            return finite(entry)&&entry>0&&finite(mark)&&mark>0&&finite(qty)&&qty>0
                ?(mark-entry)*qty:Double.NaN;
        }
        if("executions".equals(kind)&&"매도".equals(tradeSideOf(r)))
            return usefulNumber(r,"net_profit","net_realized","net_pnl","profit");
        return Double.NaN;
    }
    private double tradeReturnPct(JSONObject r,String kind,double profit){
        if("positions".equals(kind)){
            double entry=positionBuyPrice(r),mark=positionMark(r);
            double qty=usefulNumber(r,"qty","quantity","holding_qty");
            if(finite(profit)&&finite(entry)&&entry>0&&finite(qty)&&qty>0)
                return profit/(entry*qty)*100d;
            return finite(entry)&&entry>0&&finite(mark)&&mark>0
                ?(mark/entry-1d)*100d:Double.NaN;
        }
        if("executions".equals(kind)&&"매도".equals(tradeSideOf(r))){
            double measured=usefulNumber(r,"realized_pct");
            if(finite(measured))return measured;
            double bought=usefulNumber(r,"buy_amount");
            if(finite(bought)&&bought>0&&finite(profit))return profit/bought*100d;
        }
        return Double.NaN;
    }
    private String signedPct(double value){
        return !finite(value)?"—":(value>0?"+":"")+number(value)+"%";
    }
    /** Two-level cards: a 10sp key followed by a 16sp value, not dense
     * label/value sentences spread across the entire panel.
     */
    private void tradeMetricTile(LinearLayout row,String label,String value,boolean highlight){
        LinearLayout tile=new LinearLayout(this);
        tile.setOrientation(LinearLayout.VERTICAL);
        tile.setPadding(dp(9),dp(5),dp(7),dp(5));
        tile.setBackground(bg(Color.rgb(18,24,33),Color.rgb(39,52,69),10));
        TextView key=text(label,11);
        key.setTextColor(Color.rgb(151,172,195));
        tile.addView(key,new LinearLayout.LayoutParams(-1,dp(18)));
        TextView amount=text(value,16);
        amount.setTypeface(null,Typeface.BOLD);
        amount.setTextColor(highlight?Color.rgb(227,239,252):Color.rgb(205,219,236));
        amount.setSingleLine(true);
        autoSingle(amount,13,16);
        tile.addView(amount,new LinearLayout.LayoutParams(-1,dp(24)));
        LinearLayout.LayoutParams cell=new LinearLayout.LayoutParams(0,dp(54),1);
        if(row.getChildCount()>0)cell.setMargins(dp(5),0,0,0);
        row.addView(tile,cell);
    }
    private TextView tradeStage(String value,int backgroundColor,int foreground){
        TextView chip=text(value,16);
        chip.setTypeface(null,Typeface.BOLD);
        chip.setGravity(Gravity.CENTER);
        chip.setTextColor(foreground);
        chip.setBackground(bg(backgroundColor,backgroundColor,12));
        return chip;
    }

    private Map<String,String> historyColumnValues(HistoryRow item,String kind){
        JSONObject r=item.row;
        Map<String,String> values=new HashMap<>();
        values.put("name",nameOf(r));
        values.put("strategy",HistorySelection.strategyTag(item.source,item.frame));
        values.put("side",tradeSideOf(r));
        values.put("when",fillTimeOf(r,kind,item.source));
        values.put("buy",Double.toString(positionBuyPrice(r)));
        values.put("current",Double.toString(positionMark(r)));
        values.put("price",Double.toString(num(r,"price","fill_price","limit_price")));
        values.put("qty",Double.toString(usefulNumber(r,"qty","quantity","holding_qty")));
        values.put("pnl",Double.toString(tradePnL(r,kind)));
        values.put("reason",tradeReasonOf(r,"executions".equals(kind)?
            tradeSideOf(r):"매수"));
        values.put("score",Double.toString(num(r,"score","signal_score","gmo_score")));
        values.put("status",r.optString("status",r.optString("state","")));
        return values;
    }

    private void renderList(String title,JSONArray a,String kind){
        boolean marketWide="positions".equals(kind)||"pending".equals(kind)||
            "executions".equals(kind);
        java.util.List<HistoryRow> history=marketWide?
            new java.util.ArrayList<>(selectedHistory(kind)):null;
        String tableId="list/"+market+"/"+strategy+"/"+kind;
        ColumnQueryV036 query=tableQuery(tableId,
            "name","strategy","side","when","buy","current","price",
            "qty","pnl","reason","score","status");
        if(marketWide)filterAndSortHistory(history,query,
            item->historyColumnValues(item,kind));
        int total=marketWide?history.size():a.length();
        LinearLayout header=new LinearLayout(this);
        header.setGravity(Gravity.CENTER_VERTICAL);
        if(marketWide)header.addView(historyFilterButton(),
            new LinearLayout.LayoutParams(dp(116),dp(42)));
        TextView h=text(title+" · "+total+"건",18);
        h.setTypeface(null,Typeface.BOLD);
        header.addView(h,new LinearLayout.LayoutParams(0,dp(43),1));
        Button arrange=button("정렬·필터",false,()->
            tableOptionsDialog(tableId,query,this::render));
        arrange.setTextSize(11);
        LinearLayout.LayoutParams flp=new LinearLayout.LayoutParams(dp(83),dp(38));
        flp.setMargins(dp(3),0,dp(3),0);
        header.addView(arrange,flp);
        if("positions".equals(kind)){
            Button policyButton=button("정책이력",false,()->{
                page="policyHistory";persistNavigation();render();
            });
            policyButton.setTextSize(13);
            header.addView(policyButton,new LinearLayout.LayoutParams(dp(92),dp(39)));
        }
        content.addView(header,new LinearLayout.LayoutParams(-1,dp(45)));
        if(total==0){
            empty(uiSnapshot.totals.isEmpty()?"원장 화면 사전로딩 중":"선택한 목록에 기록 없음");
            return;
        }
        int first=historyStart(kind,total),last=HistoryPager.end(first,total);
        addHistoryPager(kind,total);
        for(int i=first;i<last;i++){
            HistoryRow item=marketWide?history.get(i):null;
            JSONObject r=marketWide?item.row:a.optJSONObject(i);
            if(r==null)continue;
            String source=marketWide?item.source:strategy;
            double price=positionBuyPrice(r);
            double current=positionMark(r);
            double qty=usefulNumber(r,"qty","quantity","holding_qty");
            double profit=tradePnL(r,kind);
            double rate=tradeReturnPct(r,kind,profit);
            String side="executions".equals(kind)?tradeSideOf(r):
                "pending".equals(kind)?"대기":"보유";
            boolean isSell="매도".equals(side),isBuy="매수".equals(side);
            int chipBg=isSell?Color.rgb(79,34,46):
                isBuy?Color.rgb(28,72,67):
                "positions".equals(kind)?Color.rgb(37,55,88):Color.rgb(68,59,34);
            int chipText=isSell?Color.rgb(255,174,188):
                isBuy?Color.rgb(118,245,208):
                "positions".equals(kind)?Color.rgb(154,199,255):Color.rgb(255,215,137);
            LinearLayout panel=card();
            panel.setPadding(dp(12),dp(10),dp(12),dp(10));
            LinearLayout top=new LinearLayout(this);
            top.setOrientation(LinearLayout.HORIZONTAL);
            top.setGravity(Gravity.CENTER_VERTICAL);
            TextView badge=tradeStage(side,chipBg,chipText);
            top.addView(badge,new LinearLayout.LayoutParams(dp(64),dp(36)));
            TextView symbol=text(nameOf(r)+" · "+
                HistorySelection.strategyTag(source,marketWide?item.frame:timeFrameOf(r)),16);
            symbol.setTypeface(null,Typeface.BOLD);
            symbol.setSingleLine(true);
            bindSymbolFourTap(symbol,r,market);
            LinearLayout.LayoutParams sym=new LinearLayout.LayoutParams(0,dp(36),1);
            sym.setMargins(dp(8),0,dp(8),0);
            top.addView(symbol,sym);
            String value=finite(profit)?money(profit)+(finite(rate)?" ("+signedPct(rate)+")":""):
                finite(rate)?signedPct(rate):"—";
            TextView net=text(value,19);
            net.setTypeface(null,Typeface.BOLD);
            net.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
            autoSingle(net,16,19);
            if(finite(profit))paintProfit(net,profit);
            else if(finite(rate))paintProfit(net,rate);
            else net.setTextColor(PremiumUi.MUTED);
            top.addView(net,new LinearLayout.LayoutParams(-2,dp(38)));
            panel.addView(top,new LinearLayout.LayoutParams(-1,dp(38)));

            LinearLayout figures=new LinearLayout(this);
            figures.setOrientation(LinearLayout.HORIZONTAL);
            figures.setPadding(0,dp(7),0,dp(3));
            if("positions".equals(kind)){
                tradeMetricTile(figures,"매수가",finite(price)?money(price):"—",true);
                tradeMetricTile(figures,"현재가",finite(current)&&current>0?money(current):"미수신",true);
                tradeMetricTile(figures,"보유수량",finite(qty)?number(qty):"—",false);
                tradeMetricTile(figures,"1차 목표",formatMaybeMoney(r,"first_target","target","target_price"),false);
            }else if("pending".equals(kind)){
                tradeMetricTile(figures,"점수",formatMaybeNumber(r,"score","signal_score"),true);
                tradeMetricTile(figures,"매수대기",formatMaybeMoney(r,"limit_price","entry_price","price"),true);
                tradeMetricTile(figures,"목표가",formatMaybeMoney(r,"first_target","target","target_price"),false);
                tradeMetricTile(figures,"상태",r.optString("status",r.optString("state","확인 중")),false);
            }else if(isSell){
                double buyPerShare=usefulNumber(r,"buy_price","entry_price");
                tradeMetricTile(figures,"매수가",finite(buyPerShare)?money(buyPerShare):"미기록",true);
                tradeMetricTile(figures,"매수금액",formatMaybeMoney(r,"buy_amount"),true);
                tradeMetricTile(figures,"매도가",formatMaybeMoney(r,"price","fill_price"),false);
                tradeMetricTile(figures,"매도수량",formatMaybeNumber(r,"qty","fill_qty"),false);
            }else{
                tradeMetricTile(figures,"매수가",formatMaybeMoney(r,"price","fill_price"),true);
                tradeMetricTile(figures,"매수금액",formatMaybeMoney(r,"buy_amount"),true);
                tradeMetricTile(figures,"매수수량",formatMaybeNumber(r,"qty","fill_qty"),false);
                tradeMetricTile(figures,"수수료",formatMaybeMoney(r,"fees","fee"),false);
            }
            panel.addView(figures,new LinearLayout.LayoutParams(-1,dp(64)));
            // A diverging geometric bar is an exact two-value comparison.
            // It does not pretend the app knows the intervening price path.
            if(("positions".equals(kind)||isSell)&&finite(rate)){
                TradeGeometryView pathView=new TradeGeometryView(this,rate,true);
                panel.addView(pathView,new LinearLayout.LayoutParams(-1,dp(24)));
            }
            String time=fillTimeOf(r,kind,source);
            TextView when=text("executions".equals(kind)?"체결  "+time:
                "positions".equals(kind)?"매수  "+time:"접수  "+time,12);
            when.setTextColor(Color.rgb(151,175,198));when.setMaxLines(4);
            TextView scenario=null;
            if("positions".equals(kind)){
                scenario=text(scenarioSummary(r),12);
                scenario.setTextColor(Color.rgb(127,208,184));
                scenario.setMaxLines(4);
                LinearLayout actions=new LinearLayout(this);
                actions.setOrientation(LinearLayout.HORIZONTAL);
                Button sell=button("강제매도",false,()->showManualSellInfo(r));
                sell.setTextSize(12);
                actions.addView(sell,new LinearLayout.LayoutParams(0,dp(35),1));
                Button policy=button("정책이력",false,()->{
                    openPolicyForPosition(r,source);
                });
                policy.setTextSize(12);
                LinearLayout.LayoutParams pl=new LinearLayout.LayoutParams(0,dp(35),1);
                pl.setMargins(dp(6),0,0,0);
                actions.addView(policy,pl);
                panel.addView(actions);
            }
            String action="executions".equals(kind)?tradeSideOf(r):
                "positions".equals(kind)||"pending".equals(kind)?"매수":"판단";
            String actualReason=tradeReasonOf(r,action);
            if("positions".equals(kind)){
                String recorded=r.optString("buy_reason_source","");
                if(recorded.isEmpty() && 
                    actualReason.equals("원장에 매수 사유가 기록되지 않음"))
                    actualReason="매수체결 근거 미연결 · 원본 BUY 체결 확인 필요";
                else if(!recorded.isEmpty())
                    actualReason="매수체결 근거 · "+actualReason;
                String originalSignal=r.optString("buy_signal_reason","").trim();
                if(!originalSignal.isEmpty()){
                    String readable=TradeExplanation.shortReason(originalSignal,"매수");
                    if(readable.startsWith("원본 판단코드 확인 필요"))
                        readable="원본 신호코드 "+(originalSignal.length()>90?
                            originalSignal.substring(0,87)+"…":originalSignal);
                    actualReason+="\n신호선정 근거 · "+readable;
                }
            }
            TextView why=text(actualReason,12);
            why.setTextColor(Color.rgb(181,202,221));why.setMaxLines(6);
            // Reuse the full card width for timestamp / sell scenario / reason.
            LinearLayout listFooterColumns=new LinearLayout(this);
            boolean wideList=getResources().getConfiguration().screenWidthDp>=700;
            listFooterColumns.setOrientation(wideList?
                LinearLayout.HORIZONTAL:LinearLayout.VERTICAL);
            listFooterColumns.setPadding(0,dp(5),0,0);
            listFooterColumns.addView(when,new LinearLayout.LayoutParams(
                wideList?0:-1,-2,wideList?1.4f:0f));
            if(scenario!=null){
                LinearLayout.LayoutParams statusLp=new LinearLayout.LayoutParams(
                    wideList?0:-1,-2,wideList?1.7f:0f);
                if(wideList)statusLp.setMargins(dp(10),0,0,0);
                listFooterColumns.addView(scenario,statusLp);
            }
            LinearLayout.LayoutParams whyLp=new LinearLayout.LayoutParams(
                wideList?0:-1,-2,wideList?2.0f:0f);
            if(wideList)whyLp.setMargins(dp(10),0,0,0);
            listFooterColumns.addView(why,whyLp);
            panel.addView(listFooterColumns,new LinearLayout.LayoutParams(-1,-2));
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);
            lp.setMargins(0,0,0,dp(6));content.addView(panel,lp);
        }
        addHistoryPager(kind,total);
    }
    /** A holding's policy button must open ONLY that same market/strategy/symbol. */
    private void openPolicyForPosition(JSONObject position,String sourceStrategy){
        String selected=PolicyHistoryScope.symbol(position.optString("code",
            position.optString("market","")));
        if(selected.isEmpty()){
            new AlertDialog.Builder(this).setTitle("정책이력 확인 불가")
                .setMessage("이 보유종목의 원본 코드가 없어 다른 종목 이력을 표시하지 않습니다.")
                .setPositiveButton("닫기",null).show();
            return;
        }
        policyTargetCode=selected;
        policyTargetStrategy=sourceStrategy;
        policyTargetMarket=market;
        page="policyHistory";
        historyContext="";historyOffset=0;
        persistNavigation();render();
    }

    private void showManualSellInfo(JSONObject position){
        String identity=nameOf(position);
        new AlertDialog.Builder(this).setTitle(identity+" · 강제매도")
            .setMessage("이 버전에서는 강제매도 버튼이 실제 주문을 발행하지 않습니다. 현재 EXE의 가상 수동매도와 Android 실거래 주문 상태머신이 동일한 결과를 보장하는지 확인되지 않았습니다.\n\n오주문 방지를 위해 강제매도를 차단합니다. 실거래 중에는 증권사/거래소 앱에서 직접 매도하십시오.")
            .setPositiveButton("확인",null).show();
    }
    private void policyHistoryCell(LinearLayout row,String content,float weight,
                                    int color,boolean header){
        TextView cell=text(content,header?12:12);
        cell.setTextColor(color);
        cell.setGravity(Gravity.CENTER_VERTICAL|Gravity.LEFT);
        cell.setPadding(dp(8),dp(3),dp(5),dp(3));
        cell.setMaxLines(header?2:4);
        cell.setEllipsize(android.text.TextUtils.TruncateAt.END);
        if(header)cell.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
        row.addView(cell,new LinearLayout.LayoutParams(0,-1,weight));
    }

    private void renderPolicyHistory(){
        boolean scoped=!policyTargetCode.isEmpty()&&market.equals(policyTargetMarket);
        String scopeStrategy=scoped?policyTargetStrategy:"all";
        String key=market+"/"+scopeStrategy+"/executions/all";
        java.util.List<HistoryRow> src=uiSnapshot.histories.get(key);
        if(src==null)src=java.util.Collections.emptyList();
        java.util.ArrayList<HistoryRow> entries=new java.util.ArrayList<>();
        for(HistoryRow item:src){
            JSONObject row=item.row;if(row==null)continue;
            String rowCode=row.optString("code",row.optString("market",""));
            if(!scoped||PolicyHistoryScope.matches(policyTargetMarket,
                policyTargetStrategy,policyTargetCode,market,item.source,rowCode))
                entries.add(item);
        }
        String id="policy/"+market+"/"+scopeStrategy;
        ColumnQueryV036 query=tableQuery(id,"identity","side","reason","when","score");
        File root=new File(getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");
        java.util.NavigableMap<Long,Double> marketScores=
            MarketResearchScoreLookup.scoreTimeline(root,market);
        final Map<HistoryRow,Map<String,String>> cached=new HashMap<>();
        for(HistoryRow item:entries){
            JSONObject record=item.row;
            Map<String,String> values=new HashMap<>();
            values.put("identity",nameOf(record)+"\n"+
                HistorySelection.strategyTag(item.source,item.frame));
            values.put("side",tradeSideOf(record));
            values.put("reason",tradeReasonOf(record,tradeSideOf(record)));
            values.put("when",fillTimeOf(record,"executions",item.source));
            Double recorded=MarketResearchScoreLookup.atTime(marketScores,item.at);
            values.put("score",recorded==null?"미기록":
                String.format(Locale.KOREA,"%+.1f",recorded));
            cached.put(item,values);
        }
        filterAndSortHistory(entries,query,cached::get);
        LinearLayout titleRow=new LinearLayout(this);
        titleRow.setOrientation(LinearLayout.HORIZONTAL);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView heading=text(scoped?policyTargetCode+" · 정책이력":
            ("stock".equals(market)?"주식":"코인")+" · 정책이력",19);
        heading.setTypeface(null,Typeface.BOLD);
        titleRow.addView(heading,new LinearLayout.LayoutParams(0,dp(41),1));
        Button prefs=button("열 · 필터",false,()->
            tableOptionsDialog(id,query,this::render));
        prefs.setTextSize(11);
        titleRow.addView(prefs,new LinearLayout.LayoutParams(dp(89),dp(36)));
        if(scoped){
            Button all=button("전체",false,()->{
                policyTargetCode="";policyTargetMarket="";policyTargetStrategy="";
                historyContext="";historyOffset=0;render();
            });
            all.setTextSize(11);
            LinearLayout.LayoutParams ap=new LinearLayout.LayoutParams(dp(51),dp(36));
            ap.setMargins(dp(5),0,0,0);titleRow.addView(all,ap);
        }
        content.addView(titleRow,new LinearLayout.LayoutParams(-1,dp(44)));
        TextView note=text("표시 "+entries.size()+"건 · 열 터치: 정렬 / 길게 누르기: 필터·열 이동",11);
        note.setTextColor(PremiumUi.MUTED);note.setPadding(dp(3),0,0,dp(6));
        content.addView(note);
        if(entries.isEmpty()){
            empty(scoped?"해당 종목의 기록 없음 / 적용한 필터 확인":
                "조건에 맞는 실제 체결 기록 없음");
            return;
        }
        LinearLayout headings=new LinearLayout(this);
        headings.setOrientation(LinearLayout.HORIZONTAL);
        headings.setBackground(bg(PremiumUi.SURFACE_HIGH,PremiumUi.LINE,7));
        Runnable redraw=this::render;
        for(String column:query.visibleColumns()){
            String label="identity".equals(column)?"종목명":
                "side".equals(column)?"매수 / 매도":
                "reason".equals(column)?"판단 근거":
                "when".equals(column)?"체결 시점":"당시 시장점수";
            tableHeaderCell(headings,label,column,
                policyColumnWeight(column),id,query,redraw);
        }
        content.addView(headings,new LinearLayout.LayoutParams(-1,dp(47)));
        int first=historyStart("policyHistory",entries.size());
        int last=HistoryPager.end(first,entries.size());
        addHistoryPager("policyHistory",entries.size());
        for(int i=first;i<last;i++){
            HistoryRow item=entries.get(i);
            Map<String,String> values=cached.get(item);
            if(values==null)continue;
            LinearLayout row=new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setBackground(bg(i%2==0?PremiumUi.SURFACE:
                PremiumUi.SURFACE_HIGH,PremiumUi.LINE,5));
            for(String field:query.visibleColumns()){
                String val=values.getOrDefault(field,"미기록");
                int color="side".equals(field)?
                    "매수".equals(val)?Color.rgb(113,222,181):
                    "매도".equals(val)?Color.rgb(255,156,167):PremiumUi.MUTED:
                    "score".equals(field)?Color.rgb(145,213,246):
                    Color.rgb(190,209,226);
                String textValue="score".equals(field)&&!"미기록".equals(val)
                    ?val+" / ±100":val;
                policyHistoryCell(row,textValue,policyColumnWeight(field),color,false);
            }
            LinearLayout.LayoutParams rp=new LinearLayout.LayoutParams(-1,dp(79));
            rp.setMargins(0,dp(3),0,0);
            content.addView(row,rp);
        }
        addHistoryPager("policyHistory",entries.size());
    }

    private float policyColumnWeight(String key){
        switch(key){
            case "identity":return 1.5f;
            case "side":return 0.85f;
            case "reason":return 2.6f;
            case "when":return 1.75f;
            case "score":return 1.05f;
            default:return 1f;
        }
    }

    private void zipPut(ZipOutputStream zip,String name,String text)throws Exception{
        ZipEntry e=new ZipEntry(name);zip.putNextEntry(e);
        zip.write((text==null?"":text).getBytes(StandardCharsets.UTF_8));zip.closeEntry();
    }

    /** User chooses a SAF file; no automatic email, upload or scheduled action. */
    private void requestFullLedgerBackup(){
        if(fullExportRunning.get()){
            new AlertDialog.Builder(this).setTitle("원장 백업 진행 중")
                .setMessage("동일한 원장을 중복 생성하지 않습니다.")
                .setPositiveButton("확인",null).show();
            return;
        }
        String stamp=new SimpleDateFormat("yyyyMMdd_HHmmss",Locale.KOREA).format(new Date());
        Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("application/zip");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        intent.putExtra(Intent.EXTRA_TITLE,"ARTITRADE_FULL_LEDGER_"+stamp+".zip");
        startActivityForResult(intent,REQUEST_EXPORT_FULL_LEDGER);
    }

    private static String hex(byte[] bytes){
        StringBuilder b=new StringBuilder(bytes.length*2);
        for(byte x:bytes)b.append(String.format(Locale.ROOT,"%02x",x&255));
        return b.toString();
    }

    private void fullLedgerExportResult(boolean success,String message,boolean research){
        getSharedPreferences("artitrade_full_ledger_export_v1",MODE_PRIVATE).edit()
            .putBoolean("ok",success).putString("message",message)
            .putLong("at",System.currentTimeMillis()).apply();
        main.post(()->{
            if(isFinishing()||(Build.VERSION.SDK_INT>=17&&isDestroyed()))return;
            String what=research?"연구자료 전체 추출":"전체 원장 백업";
            new AlertDialog.Builder(this).setTitle(what+(success?" 완료":" 실패"))
                .setMessage(message).setPositiveButton("확인",null).show();
        });
    }
    private void fullLedgerExportResult(boolean success,String message){
        fullLedgerExportResult(success,message,false);
    }

    private void exportFullLedgerToDocument(Uri target){
        exportFullLedgerToDocument(target,null);
    }
    private void exportFullLedgerToDocument(Uri target,String reportsJson){
        final boolean research=reportsJson!=null;
        if(!fullExportRunning.compareAndSet(false,true))return;
        new Thread(()->{
            File archive=new File(getCacheDir(),"artitrade_full_ledger_"+System.nanoTime()+".zip");
            try{
                if(target==null||!"content".equals(target.getScheme()))
                    throw new SecurityException("지원되는 Android 파일 저장 위치가 아닙니다.");
                if(!Python.isStarted())
                    throw new IllegalStateException("전략엔진 시작 후 전체 원장을 백업할 수 있습니다.");
                File rootDir=new File(getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");
                com.chaquo.python.PyObject backup=
                    Python.getInstance().getModule("android_ledger_backup");
                String raw=(reportsJson==null
                    ?backup.callAttr("make_backup",rootDir.getAbsolutePath(),
                        archive.getAbsolutePath())
                    :backup.callAttr("make_backup",rootDir.getAbsolutePath(),
                        archive.getAbsolutePath(),reportsJson)).toString();
                JSONObject result=new JSONObject(raw);
                if(!result.optBoolean("ok")||!archive.isFile()||
                    result.optLong("bytes",-1)!=archive.length())
                    throw new IOException("SQLite 원장 스냅샷 생성/크기 검사 실패");
                String expected=result.optString("sha256","");
                long length=0;
                MessageDigest written=MessageDigest.getInstance("SHA-256");
                try(InputStream in=new FileInputStream(archive);
                    OutputStream out=getContentResolver().openOutputStream(target,"w")){
                    if(out==null)throw new IOException("백업 저장 위치에 쓰기 불가");
                    byte[] buf=new byte[1024*1024];int n;
                    while((n=in.read(buf))!=-1){
                        out.write(buf,0,n);written.update(buf,0,n);length+=n;
                    }
                    out.flush();
                }
                if(length!=archive.length()||!expected.equalsIgnoreCase(hex(written.digest())))
                    throw new IOException("저장 중 원장 백업 크기 또는 SHA-256 불일치");
                // Content providers can fail after a write appears complete.
                // Read the picked destination back and verify every saved byte.
                MessageDigest saved=MessageDigest.getInstance("SHA-256");
                long confirmed=0;
                try(InputStream verify=getContentResolver().openInputStream(target)){
                    if(verify==null)throw new IOException("저장된 백업 재조회 불가");
                    byte[] buf=new byte[1024*1024];int n;
                    while((n=verify.read(buf))!=-1){saved.update(buf,0,n);confirmed+=n;}
                }
                if(confirmed!=length||!expected.equalsIgnoreCase(hex(saved.digest())))
                    throw new IOException("선택한 저장 위치에서 백업 재검증 불일치");
                fullLedgerExportResult(true,
                    "원본 파일 "+result.optInt("file_count",0)+"개 (SQLite "+
                    result.optInt("sqlite_count",0)+"개)\n"+
                    "읽기용 분석자료 "+result.optInt("report_count",0)+"개\n"+
                    "용량 "+String.format(Locale.KOREA,"%,d",confirmed)+"바이트\n"+
                    "ZIP SHA-256 "+expected+"\n"+
                    "과거·현재 실제/가상매매 원본 SQLite 전체 및 저장본을 검증했습니다.",
                    research);
            }catch(Throwable e){
                new RuntimeCrashStore(this).add(research?"research_full_export":"full_ledger_backup",e);
                fullLedgerExportResult(false,
                    "기존 원장 데이터는 변경되지 않았습니다.\n"+e.getClass().getSimpleName()+
                    ": "+String.valueOf(e.getMessage()),research);
            }finally{
                if(archive.exists()&&!archive.delete())
                    new RuntimeCrashStore(this).add("full_ledger_backup_cache_cleanup",
                        new IOException("Temporary archive cleanup failed"));
                fullExportRunning.set(false);
            }
        },"artitrade-full-ledger-export").start();
    }

    private void exportResearch(Stats st){
        if(fullExportRunning.get()){
            new AlertDialog.Builder(this).setTitle("백업 진행 중")
                .setMessage("기존 백업이 끝난 뒤 다시 추출할 수 있습니다.")
                .setPositiveButton("확인",null).show();
            return;
        }
        try{
            JSONObject rs=research(market,strategy);
            JSONObject summary=new JSONObject()
                .put("schema","ARTITRADE_RESEARCH_FULL_SOURCE_V079")
                .put("market",market)
                .put("strategy",strategy)
                .put("strategy_name",strategyName(market,strategy))
                .put("exported_at_kst",new SimpleDateFormat(
                    "yyyy-MM-dd HH:mm:ss",Locale.KOREA).format(new Date())+" KST")
                .put("source_total",rs.optInt("source_total",-1))
                .put("displayed_preview_rows",rs.optInt("loaded_items",st.research.length()))
                .put("preview_can_be_truncated",rs.optBoolean("partial_window",false))
                .put("actual_research",rs.optJSONObject("actual_summary"))
                .put("virtual_research",rs.optJSONObject("virtual_summary"))
                .put("score_bands",rs.optJSONArray("bands"))
                .put("evidence_table_counts",rs.optJSONObject("evidence_counts"))
                .put("note","화면 일부 조회와 무관하게 stock/·coin/ 원본 SQLite 전체를 ZIP에 포함");
            JSONObject reports=new JSONObject();
            reports.put("analysis/research_summary.json",summary.toString(2));
            String preview=rs.toString();
            if(preview.getBytes(StandardCharsets.UTF_8).length<7*1024*1024){
                reports.put("analysis/research_preview.json",preview);
            }
            reports.put("analysis/reason_guide_ko.txt",
                "매수 원인: 원본 신호·점수·진입조건 기록을 기준으로 확인\n"+
                "매도 원인: 원본 실제 체결의 reason / purpose / exit_reason 확인\n"+
                "가상 연구: shadow / forward / milestone 등 별도 원본 테이블 확인\n"+
                "체결시각: 원본의 UTC 시간대 정보가 있으면 한국시간(KST)으로 변환\n"+
                "실제 매도가 없는 고가 접촉은 실제 매도 성공으로 처리하지 않음\n"+
                "연구화면의 최근 표시건수와 ZIP의 전체 원본건수는 다를 수 있음\n");
            pendingResearchReports=reports.toString();
            pendingResearchExport=null; // Old preview-only in-memory ZIP is not full evidence.
            String stamp=new SimpleDateFormat("yyyyMMdd_HHmmss",Locale.KOREA)
                .format(new Date());
            Intent i=new Intent(Intent.ACTION_CREATE_DOCUMENT);
            i.addCategory(Intent.CATEGORY_OPENABLE);
            i.setType("application/zip");
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|
                Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            i.putExtra(Intent.EXTRA_TITLE,"ARTITRADE_"+market+"_"+strategy+
                "_RESEARCH_ALL_ORIGINALS_"+stamp+".zip");
            startActivityForResult(i,REQUEST_EXPORT_RESEARCH);
        }catch(Exception e){
            pendingResearchReports=null;
            new AlertDialog.Builder(this).setTitle("연구자료 추출 실패")
                .setMessage(String.valueOf(e.getMessage()))
                .setPositiveButton("확인",null).show();
        }
    }

    private void requestCombinedResearch(String scope){
        if(!("stock".equals(scope)||"coin".equals(scope)||"all".equals(scope)))
            return;
        if(fullExportRunning.get()){
            new AlertDialog.Builder(this).setTitle("연구이력 추출 진행 중")
                .setMessage("기존 추출이 끝난 후 재시도할 수 있습니다.")
                .setPositiveButton("확인",null).show();
            return;
        }
        pendingCombinedResearchScope=scope;
        Intent picker=new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        picker.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION|
            Intent.FLAG_GRANT_WRITE_URI_PERMISSION|
            Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        startActivityForResult(picker,REQUEST_EXPORT_COMBINED_RESEARCH);
    }

    private void exportCombinedResearchToFolder(Uri tree,String scope){
        if(!fullExportRunning.compareAndSet(false,true))return;
        new Thread(()->{
            File folder=new File(getCacheDir(),"artitrade_research_bundle_"+System.nanoTime());
            java.util.List<Uri> newDocuments=new java.util.ArrayList<>();
            try{
                if(!folder.mkdirs()&&!folder.isDirectory())
                    throw new IOException("사전 검증 임시폴더 생성 실패");
                if(!Python.isStarted())
                    throw new IllegalStateException("전략엔진 초기화 후 연구자료 추출 가능");
                if(tree==null||!"content".equals(tree.getScheme()))
                    throw new SecurityException("지원되지 않는 저장 폴더");
                try{
                    getContentResolver().takePersistableUriPermission(tree,
                        Intent.FLAG_GRANT_READ_URI_PERMISSION|
                        Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                }catch(SecurityException ignored){}
                File runtime=new File(getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");
                String raw=Python.getInstance().getModule("android_research_bundle")
                    .callAttr("make_bundle",runtime.getAbsolutePath(),
                        folder.getAbsolutePath(),scope).toString();
                JSONObject info=new JSONObject(raw);
                if(!info.optBoolean("ok")||info.optInt("parts")<=0)
                    throw new IOException("분할 리서치 생성 검증 실패");
                Uri parent=DocumentsContract.buildDocumentUriUsingTree(
                    tree,DocumentsContract.getTreeDocumentId(tree));
                JSONArray parts=info.getJSONArray("files");
                if(parts.length()!=info.optInt("parts"))
                    throw new IOException("분할 파일 목록과 실제 개수 불일치");
                long verified=0;
                for(int i=0;i<parts.length();i++){
                    JSONObject item=parts.getJSONObject(i);
                    String filename=item.getString("name");
                    if(!filename.matches("ARTITRADE_[A-Z]+_RESEARCH_[A-Za-z0-9_]+_PART_[0-9]{3}_OF_[0-9]{3}\\.zip"))
                        throw new SecurityException("안전하지 않은 출력 파일명");
                    File source=new File(folder,filename);
                    if(!source.isFile()||source.length()!=item.getLong("bytes")||
                       source.length()>500_000_000L)
                        throw new IOException("500MB 한도 또는 원본 파일 크기 검사 실패");
                    Uri saved=DocumentsContract.createDocument(getContentResolver(),
                        parent,"application/zip",filename);
                    if(saved==null)throw new IOException("선택한 폴더에 ZIP 파일 생성 불가");
                    newDocuments.add(saved);
                    MessageDigest streamed=MessageDigest.getInstance("SHA-256");
                    try(InputStream input=new FileInputStream(source);
                        OutputStream output=getContentResolver().openOutputStream(saved,"w")){
                        if(output==null)throw new IOException("ZIP 저장 스트림 없음");
                        byte[] buf=new byte[1024*1024];int n;
                        while((n=input.read(buf))!=-1){output.write(buf,0,n);streamed.update(buf,0,n);}
                        output.flush();
                    }
                    String expected=item.getString("sha256");
                    if(!expected.equalsIgnoreCase(hex(streamed.digest())))
                        throw new IOException("ZIP 출력 중 SHA-256 불일치");
                    MessageDigest disk=MessageDigest.getInstance("SHA-256");
                    long bytes=0;
                    try(InputStream input=getContentResolver().openInputStream(saved)){
                        if(input==null)throw new IOException("저장된 ZIP 재열기 실패");
                        byte[] buf=new byte[1024*1024];int n;
                        while((n=input.read(buf))!=-1){disk.update(buf,0,n);bytes+=n;}
                    }
                    if(bytes!=source.length()||
                        !expected.equalsIgnoreCase(hex(disk.digest())))
                        throw new IOException("저장된 ZIP 검증 불일치");
                    verified+=bytes;
                }
                fullLedgerExportResult(true,
                    ("all".equals(scope)?"프로그램 전체":"stock".equals(scope)?"주식 통합":"코인 통합")+
                    " 연구이력 "+parts.length()+"개 ZIP 저장 완료\n"+
                    "각 파일 500MB 이하 · 원본 SQLite/JSON 전체 포함\n"+
                    "검증한 용량 "+String.format(Locale.KOREA,"%,d",verified)+"바이트\n"+
                    "분할 원본은 PC 복원 도구로 다시 결합할 수 있습니다.",true);
            }catch(Throwable e){
                // A failed multi-file transfer must not masquerade as a complete export.
                for(Uri created:newDocuments){
                    try{DocumentsContract.deleteDocument(getContentResolver(),created);}
                    catch(Exception ignored){}
                }
                new RuntimeCrashStore(this).add("combined_research_export",e);
                fullLedgerExportResult(false,
                    "통합 연구이력 저장 실패. 불완전한 신규 파일은 가능한 범위에서 정리했습니다.\\n"+
                    e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()),true);
            }finally{
                File[] files=folder.listFiles();
                if(files!=null)for(File file:files)if(file.isFile())file.delete();
                folder.delete();
                fullExportRunning.set(false);
            }
        },"artitrade-scoped-multipart-research").start();
    }

    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data){
        super.onActivityResult(requestCode,resultCode,data);
        if(requestCode==REQUEST_EXPORT_COMBINED_RESEARCH){
            String scope=pendingCombinedResearchScope;
            pendingCombinedResearchScope=null;
            if(resultCode==RESULT_OK&&scope!=null&&data!=null&&data.getData()!=null)
                exportCombinedResearchToFolder(data.getData(),scope);
            return;
        }
        if(requestCode==REQUEST_EXPORT_FULL_LEDGER){
            if(resultCode==RESULT_OK&&data!=null&&data.getData()!=null)
                exportFullLedgerToDocument(data.getData());
            return;
        }
        if(requestCode!=REQUEST_EXPORT_RESEARCH)return;
        String reports=pendingResearchReports;
        pendingResearchReports=null;pendingResearchExport=null;
        if(resultCode!=RESULT_OK||reports==null||data==null||data.getData()==null)
            return;
        // Stream and SHA-verify the entire EXE/phone SQLite history, not the
        // current UI's limited 3,000-row research preview.
        exportFullLedgerToDocument(data.getData(),reports);
    }

    /** A date-only D-day is not a UTC instant and must not shift calendar day. */
    private String researchMoment(Object value){
        if(value==null||value==JSONObject.NULL)return "미기록";
        String raw=String.valueOf(value).trim();
        if(raw.isEmpty()||"null".equalsIgnoreCase(raw))return "미기록";
        if(raw.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}"))return raw;
        if(raw.matches("[0-9]{8}"))return raw.substring(0,4)+"-"+
            raw.substring(4,6)+"-"+raw.substring(6,8);
        return formatKst(value);
    }

    private ColumnQueryV036 tableQuery(String id,String... fields){
        ColumnQueryV036 existing=uiTableQueries.get(id);
        if(existing!=null)return existing;
        ColumnQueryV036 q=new ColumnQueryV036(fields);
        try{
            String saved=getSharedPreferences("artitrade_table_preferences_v036",MODE_PRIVATE)
                .getString(id,"");
            if(!saved.isEmpty()){
                JSONObject conf=new JSONObject(saved);
                JSONArray prior=conf.optJSONArray("order");
                if(prior!=null){
                    java.util.ArrayList<String> newOrder=new java.util.ArrayList<>();
                    for(int i=0;i<prior.length();i++){
                        String key=prior.optString(i,"");
                        if(q.order.contains(key)&&!newOrder.contains(key))newOrder.add(key);
                    }
                    for(String key:q.order)if(!newOrder.contains(key))newOrder.add(key);
                    q.order.clear();q.order.addAll(newOrder);
                }
                JSONArray hidden=conf.optJSONArray("hidden");
                if(hidden!=null)for(int i=0;i<hidden.length();i++){
                    String key=hidden.optString(i,"");
                    if(q.order.contains(key))q.hide(key,true);
                }
                JSONObject filters=conf.optJSONObject("filters");
                if(filters!=null){
                    java.util.Iterator<String> it=filters.keys();
                    while(it.hasNext()){String key=it.next();
                        q.setFilter(key,filters.optString(key,""));
                    }
                }
                String sort=conf.optString("sort","");
                if(q.order.contains(sort)){
                    q.sortColumn=sort;q.descending=conf.optBoolean("desc",false);
                }
            }
        }catch(Exception problem){
            new RuntimeCrashStore(this).add("table_preferences_read_"+id,problem);
        }
        uiTableQueries.put(id,q);
        return q;
    }

    private void saveTableQuery(String id,ColumnQueryV036 q){
        try{
            JSONObject saved=new JSONObject();
            JSONArray order=new JSONArray(),hidden=new JSONArray();
            for(String key:q.columns())order.put(key);
            for(String key:q.hidden)hidden.put(key);
            JSONObject filters=new JSONObject();
            for(Map.Entry<String,String> e:q.filters.entrySet())
                filters.put(e.getKey(),e.getValue());
            saved.put("order",order).put("hidden",hidden)
                .put("filters",filters).put("sort",q.sortColumn)
                .put("desc",q.descending);
            getSharedPreferences("artitrade_table_preferences_v036",MODE_PRIVATE)
                .edit().putString(id,saved.toString()).apply();
        }catch(Exception e){
            new RuntimeCrashStore(this).add("table_preferences_save_"+id,e);
        }
    }

    private void editTableFilter(String id,ColumnQueryV036 q,String key,Runnable redraw){
        EditText edit=new EditText(this);
        edit.setSingleLine(true);
        edit.setText(q.filters.getOrDefault(key,""));
        edit.setHint("포함된 값 입력 (정확히 일치: =값)");
        edit.setTextSize(14);
        edit.setPadding(dp(16),dp(6),dp(13),dp(6));
        new AlertDialog.Builder(this).setTitle(key+" · 값 필터")
            .setView(edit)
            .setNegativeButton("취소",null)
            .setNeutralButton("필터 해제",(d,w)->{
                q.setFilter(key,"");saveTableQuery(id,q);redraw.run();
            })
            .setPositiveButton("적용",(d,w)->{
                q.setFilter(key,edit.getText().toString());
                saveTableQuery(id,q);redraw.run();
            }).show();
    }

    private void tableColumnMenu(View anchor,String id,ColumnQueryV036 q,
                                  String key,Runnable redraw){
        PopupMenu menu=new PopupMenu(this,anchor);
        String[] actions={"오름차순 정렬","내림차순 정렬","값 필터 편집",
            "열 왼쪽으로","열 오른쪽으로",
            q.hidden.contains(key)?"열 다시 표시":"열 숨기기",
            "전체 필터 초기화"};
        for(int i=0;i<actions.length;i++){
            final int action=i;
            menu.getMenu().add(actions[i]).setOnMenuItemClickListener(v->{
                if(action==2){editTableFilter(id,q,key,redraw);return true;}
                if(action==0||action==1){q.sortColumn=key;q.descending=action==1;}
                else if(action==3)q.move(key,-1);
                else if(action==4)q.move(key,1);
                else if(action==5)q.hide(key,!q.hidden.contains(key));
                else if(action==6){q.filters.clear();q.sortColumn="";q.descending=false;}
                saveTableQuery(id,q);redraw.run();return true;
            });
        }
        menu.show();
    }

    /** General action menu: card lists can sort/filter; true tables can also
     * hide/reorder columns. User preferences are retained per market/screen. */
    private void tableOptionsDialog(String id,ColumnQueryV036 q,Runnable redraw){
        String[] choices=new String[q.order.size()+1];
        choices[0]="필터/정렬 초기화 · 모든 열 표시";
        for(int i=0;i<q.order.size();i++){
            String key=q.order.get(i);
            choices[i+1]=(q.hidden.contains(key)?"[숨김] ":"")+humanColumnName(key)+
                (q.filters.containsKey(key)?" · 필터 중":"")+
                (q.sortColumn.equals(key)?(q.descending?" · 내림차순":" · 오름차순"):"");
        }
        new AlertDialog.Builder(this).setTitle("표시 순서 · 정렬 · 필터")
            .setItems(choices,(d,w)->{
                if(w==0){
                    q.filters.clear();q.hidden.clear();
                    q.sortColumn="";q.descending=false;
                    saveTableQuery(id,q);redraw.run();
                }else{
                    String key=q.order.get(w-1);
                    boolean trueTable=id.startsWith("popup/")||id.startsWith("policy/");
                    String[] actions=trueTable?
                        new String[]{"오름차순","내림차순","값 필터",
                            "열 왼쪽으로","열 오른쪽으로",
                            q.hidden.contains(key)?"열 표시":"열 숨김"}:
                        new String[]{"오름차순 정렬","내림차순 정렬","값 필터"};
                    new AlertDialog.Builder(this).setTitle(humanColumnName(key))
                        .setItems(actions,(dialog,action)->{
                            if(action==2){editTableFilter(id,q,key,redraw);return;}
                            if(action==0||action==1){
                                q.sortColumn=key;q.descending=action==1;
                            }else if(action==3)q.move(key,-1);
                            else if(action==4)q.move(key,1);
                            else if(action==5)q.hide(key,!q.hidden.contains(key));
                            saveTableQuery(id,q);redraw.run();
                        }).setNegativeButton("닫기",null).show();
                }
            }).setPositiveButton("닫기",null).show();
    }

    private String humanColumnName(String key){
        switch(key){
            case "identity":return "종목명 / 전략";
            case "name":return "종목명";
            case "strategy":return "전략·시간봉";
            case "when":return "날짜·시간";
            case "buy":return "매수가";
            case "sell":return "매도가 / 목표관측";
            case "goal":return "목표 달성 여부";
            case "reason":return "매수·매도 판단근거";
            case "side":return "매수·매도 여부";
            case "price":return "체결가";
            case "current":return "현재가";
            case "qty":return "수량";
            case "pnl":return "손익금액";
            case "score":return "점수";
            case "status":return "상태";
            case "tf":return "시간봉";
            case "outcome":return "목표도달 판정";
            default:return key;
        }
    }

    private void tableHeaderCell(LinearLayout parent,String title,String key,float weight,
                                 String id,ColumnQueryV036 q,Runnable redraw){
        TextView label=text(title+
            (key.equals(q.sortColumn)?(q.descending?" ▼":" ▲"):"")+
            (q.filters.containsKey(key)?" •":""),12);
        label.setTextColor(Color.rgb(215,230,250));
        label.setGravity(Gravity.CENTER_VERTICAL|Gravity.LEFT);
        label.setPadding(dp(7),0,dp(3),0);
        label.setMaxLines(3);
        label.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
        label.setOnClickListener(v->{
            q.sort(key);saveTableQuery(id,q);redraw.run();
        });
        label.setOnLongClickListener(v->{
            tableColumnMenu(v,id,q,key,redraw);return true;
        });
        parent.addView(label,new LinearLayout.LayoutParams(0,-1,weight));
    }

    private void filterAndSortHistory(java.util.List<HistoryRow> data,
                                     ColumnQueryV036 q,
                        java.util.function.Function<HistoryRow,Map<String,String>> values){
        // Compute each formatted row only once. Re-formatting dates/prices
        // inside an O(n log n) sorting comparator could freeze a 3k+ row UI.
        Map<HistoryRow,Map<String,String>> prepared=new HashMap<>();
        for(HistoryRow row:data)prepared.put(row,values.apply(row));
        data.removeIf(row->!q.matches(prepared.get(row)));
        if(q.sortColumn.isEmpty())return;
        data.sort((left,right)->{
            int n=ColumnQueryV036.compare(
                prepared.get(left).get(q.sortColumn),
                prepared.get(right).get(q.sortColumn));
            return q.descending?-n:n;
        });
    }

    private void researchPopupCell(LinearLayout row,String value,float weight,boolean header,int color){
        TextView t=text(value,header?12:12);
        t.setTextColor(color);
        t.setGravity(Gravity.CENTER_VERTICAL|Gravity.LEFT);
        t.setPadding(dp(7),dp(4),dp(4),dp(4));
        t.setMaxLines(header?2:4);
        t.setEllipsize(android.text.TextUtils.TruncateAt.END);
        row.addView(t,new LinearLayout.LayoutParams(0,-1,weight));
    }

    private String[] researchBuyCell(HistoryRow source){
        JSONObject row=source.row;
        boolean bought=bool(row,"bought","actually_bought","filled");
        boolean observed=bool(row,"entry_observed","virtual_entered","shadow_entered");
        double price=num(row,"buy_price");
        Object at=firstValue(row,"buy_at");
        if(bought){
            String prefix="gmo".equals(source.source)?"PAPER 매수":"원장 매수";
            return new String[]{prefix+"  "+(finite(price)&&price>0?money(price):"미기록"),
                researchMoment(at)};
        }
        double virtual=num(row,"virtual_buy_price");
        if(observed&&finite(virtual)&&virtual>0){
            String prefix="gmo".equals(source.source)?"D+1 공식시가":"가상 진입가";
            return new String[]{prefix+"  "+money(virtual),
                researchMoment(firstValue(row,"virtual_buy_at","first_observed_limit_touch"))};
        }
        double reference=num(row,"limit_price","buy_reference_price");
        if(finite(reference)&&reference>0)
            return new String[]{"매수 기준가  "+money(reference)+" (미체결)",
                "체결일 없음"};
        return new String[]{"매수가 미기록","체결일 없음"};
    }

    /** Only an original recorded SELL price may be displayed as a filled sale. */
    private String[] researchSellCell(HistoryRow source){
        JSONObject row=source.row;
        boolean bought=bool(row,"bought","actually_bought","filled");
        double sold=num(row,"sell_price");
        Object at=firstValue(row,"sell_at");
        String evidence=row.optString("sell_evidence","");
        // Recover post-entry maximum and its true bar timestamp from the
        // market-data API; never mistake candidate_forward.last_ts for a peak.
        boolean minuteVerified="VERIFIED_MINUTE_BAR".equals(
            row.optString("observed_max_evidence",""));
        double fetchedHigh=num(row,"observed_max_price");
        Object fetchedPeak=firstValue(row,"observed_max_at");
        int precision=row.optInt("forward_high_precision_seconds",60);
        String precisionText=precision==60?"1분봉":"5분봉";
        boolean gotHigh=minuteVerified&&finite(fetchedHigh)&&fetchedHigh>0&&fetchedPeak!=null;
        String highLine=gotHigh?
            "후속고가  "+money(fetchedHigh)+"  ("+precisionText+")":"";
        String peakLine=gotHigh?researchMoment(fetchedPeak):"";
        if(bought&&!evidence.isEmpty()&&finite(sold)&&sold>0&&at!=null){
            String tag="gmo".equals(source.source)?"PAPER 매도":"원장 매도";
            return gotHigh?
                new String[]{tag+" "+money(sold)+" · "+highLine,
                    "매도 "+researchMoment(at)+" · 고점 "+peakLine}:
                new String[]{tag+"  "+money(sold),researchMoment(at)};
        }
        double virtual=num(row,"virtual_sell_price");
        Object virtualAt=firstValue(row,"virtual_sell_at");
        if(!bought&&!row.optString("virtual_sell_evidence","").isEmpty()&&
           finite(virtual)&&virtual>0&&virtualAt!=null)
            return new String[]{"가상매도  "+money(virtual)+" (모의체결)",
                researchMoment(virtualAt)};

        // Observed one-minute highs / BOOK_BID price paths are NEVER a
        // broker or PAPER SELL. But they must not be hidden as '미기록'.
        double observed=num(row,"observed_target_price");
        Object observedAt=firstValue(row,"observed_target_at");
        // AT candidate_forward is a summary, not independently cross-checked
        // one-minute bars. Keep source observations in research records but
        // never advertise them as a verified intraday high or target touch.
        if(!"at".equals(source.source)&&finite(observed)&&observed>0&&observedAt!=null&&
           !row.optString("observed_target_evidence","").isEmpty()){
            return new String[]{"목표 고가관측  "+money(observed)+" (매도 체결 아님)",
                researchMoment(observedAt)};
        }
        Object touched=firstValue(row,"first_observed_target_after_touch");
        double threshold=num(row,"first_target","target");
        if(!"at".equals(source.source)&&touched!=null&&finite(threshold)&&threshold>0)
            return new String[]{"목표가격 관측  "+money(threshold)+" (매도 체결 아님)",
                researchMoment(touched)};
        // The original GMO engine records PAPER last-exit time separately from
        // the *first-target* sale. Show it as final exit, never as first TP.
        Object lastExit=firstValue(row,"last_exit_at","paper_last_exit_at");
        if(bought&&lastExit!=null&&!row.optString("sell_reason","").isEmpty()){
            return new String[]{"최종매도 시각 · 1차 목표매도 여부 별도",
                researchMoment(lastExit)};
        }
        if(gotHigh)
            return new String[]{highLine,peakLine};
        if("at".equals(source.source)&&(row.has("observed_max_unverified")||
              row.has("last_observed_at"))){
            return new String[]{"후속고가 시세 API 조회 중",
                "매수 이후 고가·발생시각 조회"};
        }
        String quality=row.optString("observation_status","");
        if("DATA_GAP".equals(quality))
            return new String[]{"1분봉 수집 누락 · 목표 판정보류",
                "미관측 "+row.optInt("missing_observation_minutes",0)+"분"};
        if("NO_OBSERVATIONS".equals(quality))
            return new String[]{"후속 관측자료 0건 · 승계/시세 수집 점검",
                "원본 시각 미기록"};
        if(bought&&"HIT".equalsIgnoreCase(row.optString("outcome","")))
            return new String[]{"1차 매도 완료 표시 · 원장 체결가격/시각 미연결",
                "원본 executions 확인 필요"};
        if("MISS".equalsIgnoreCase(row.optString("outcome","")))
            return new String[]{"목표 미달성 확정 · 1차 목표체결 없음",
                "최종 시각 미기록"};
        return new String[]{"후속 관측 중 · 목표판정 미확정",
            "매도체결/고가관측 근거 없음"};
    }

    private boolean researchGoalVerified(HistoryRow source){
        JSONObject r=source.row;
        String outcome=r.optString("outcome","").toUpperCase(Locale.ROOT);
        if("MISS".equals(outcome)){
            return bool(r,"closed")||bool(r,"horizon_complete")||
                "NOT_TOUCHED_15_FINAL_DAYS".equals(
                    r.optString("research_5pct_status",""));
        }
        if(!"HIT".equals(outcome))return false;
        if(bool(r,"bought","actually_bought","filled")){
            // Original first_sold / first_target_filled is set after a real
            // account/PAPER first target sale, even if price is missing.
            return bool(r,"target_hit","first_target_filled")||
                !r.optString("sell_evidence","").isEmpty();
        }
        return !r.optString("virtual_sell_evidence","").isEmpty()||
               !r.optString("observed_target_evidence","").isEmpty()||
               "TOUCHED_HIGH_NOT_FILL".equals(
                   r.optString("research_5pct_status",""));
    }

    private Map<String,String> researchPopupValues(HistoryRow item){
        JSONObject r=item.row;
        boolean bought=bool(r,"bought","actually_bought","filled");
        boolean entered=bool(r,"entry_observed","virtual_entered","shadow_entered");
        String frame=item.frame.isEmpty()?item.source:item.frame;
        String identity=nameOf(r)+"\n"+HistorySelection.strategyTag(item.source,frame)
            +" · "+ResearchBandSelection.eventType(bought,entered);
        String day="gmo".equals(item.source)?"D일 ":"조건충족일 ";
        Object signal="gmo".equals(item.source)?
            firstValue(r,"gmo_event_date","event_date","at"):
            firstValue(r,"at","signal_ts","created_at","ref_ts");
        String[] buy=researchBuyCell(item),sell=researchSellCell(item);
        String outcome=ResearchBandSelection.goalSymbol(
            r.optString("outcome",""),researchGoalVerified(item));
        Map<String,String> values=new HashMap<>();
        values.put("identity",identity);
        values.put("when",day+researchMoment(signal));
        values.put("buy",buy[0]+"\n"+buy[1]);
        values.put("sell",sell[0]+"\n"+sell[1]);
        values.put("goal",outcome);
        return values;
    }

    private String popupColumnTitle(String key){
        switch(key){
            case "identity":return "종목명 / 전략";
            case "when":return "조건충족일 (D일)";
            case "buy":return "매수가 (일자)";
            case "sell":return "매도가 / 목표관측 (일자)";
            case "goal":return "목표달성 O/X";
            default:return key;
        }
    }
    private float popupColumnWidth(String key){
        switch(key){
            case "identity":return 2.05f;
            case "when":return 1.65f;
            case "buy":return 2.15f;
            case "sell":return 2.40f;
            case "goal":return 0.90f;
            default:return 1f;
        }
    }

    private View researchBandPopupRow(HistoryRow item,int position,ColumnQueryV036 columns){
        LinearLayout row=new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setBackground(bg(position%2==0?
            PremiumUi.SURFACE:PremiumUi.SURFACE_HIGH,0,5));
        Map<String,String> values=researchPopupValues(item);
        for(String key:columns.visibleColumns()){
            String value=values.getOrDefault(key,"미기록");
            int tint="goal".equals(key)?
                "O".equals(value)?Color.rgb(76,228,180):
                "X".equals(value)?Color.rgb(251,121,145):PremiumUi.MUTED:
                "buy".equals(key)?Color.rgb(150,207,247):
                "sell".equals(key)?Color.rgb(162,214,186):PremiumUi.TEXT;
            researchPopupCell(row,value,popupColumnWidth(key),false,tint);
        }
        row.setMinimumHeight(dp(83));
        return row;
    }

    private void showResearchBandPopup(String frame,String band,String entrySide){
        java.util.List<HistoryRow> all=selectedHistory("research");
        java.util.ArrayList<HistoryRow> sourceRows=new java.util.ArrayList<>();
        for(HistoryRow item:all){
            JSONObject r=item.row;
            if(r==null)continue;
            String candidateFrame=timeFrameOf(r);
            if(candidateFrame.isEmpty())candidateFrame=item.source;
            double score=num(r,"score","gmo_score_at_signal",
                "original_score","signal_score");
            boolean bought=bool(r,"bought","actually_bought","filled");
            boolean entered=bool(r,"entry_observed","virtual_entered","shadow_entered");
            if(ResearchBandSelection.matches(candidateFrame,score,frame,band)
                && ResearchBandSelection.includeInPopup(bought,entered,entrySide))
                sourceRows.add(item);
        }
        String section="gmo".equals(frame)?"수급매매":"close".equals(frame)?
            "종가베팅":tfLabel(frame);
        String bandLabel=ResearchScoreGrid.NO_SCORE.equals(band)?band:band+"점";
        String entryLabel="actual".equals(entrySide)?"실제 매매":"가상 진입";
        String id="popup/"+market+"/"+strategy+"/"+frame+"/"+band+"/"+entrySide;
        ColumnQueryV036 columns=tableQuery(id,"identity","when","buy","sell","goal");
        final java.util.ArrayList<HistoryRow> shown=new java.util.ArrayList<>();

        LinearLayout body=new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setBackgroundColor(PremiumUi.BG);
        body.setPadding(dp(7),dp(2),dp(7),dp(3));
        LinearLayout tools=new LinearLayout(this);
        tools.setGravity(Gravity.CENTER_VERTICAL);
        TextView note=text("",12);
        note.setTextColor(Color.rgb(174,204,232));
        note.setMaxLines(2);
        tools.addView(note,new LinearLayout.LayoutParams(0,dp(45),1));
        Button prefs=button("열 · 필터",false,()->{});
        prefs.setTextSize(12);
        tools.addView(prefs,new LinearLayout.LayoutParams(dp(90),dp(38)));
        body.addView(tools,new LinearLayout.LayoutParams(-1,dp(49)));

        LinearLayout headers=new LinearLayout(this);
        headers.setOrientation(LinearLayout.HORIZONTAL);
        headers.setBackground(bg(PremiumUi.SURFACE_HIGH,PremiumUi.LINE,5));
        body.addView(headers,new LinearLayout.LayoutParams(-1,dp(48)));

        android.widget.ListView list=new android.widget.ListView(this);
        list.setDividerHeight(dp(4));
        list.setCacheColorHint(Color.TRANSPARENT);
        list.setSelector(android.R.color.transparent);
        android.widget.BaseAdapter adapter=new android.widget.BaseAdapter(){
            @Override public int getCount(){return shown.size();}
            @Override public Object getItem(int position){return shown.get(position);}
            @Override public long getItemId(int position){return position;}
            @Override public View getView(int position,View convertView,
                                          android.view.ViewGroup parent){
                return researchBandPopupRow(shown.get(position),position,columns);
            }
        };
        list.setAdapter(adapter);
        int listHeight=Math.min((int)(getResources().getDisplayMetrics().heightPixels*.55f),
            dp(91)*Math.max(1,Math.min(sourceRows.size(),8)));
        body.addView(list,new LinearLayout.LayoutParams(-1,listHeight));
        TextView footer=text("O 확인된 목표 달성 · X 관측기간 종료 후 미달성 · "+
            "— 판정보류. 고가관측과 실제 매도체결은 별도 근거임.",11);
        footer.setTextColor(Color.rgb(174,179,194));
        footer.setPadding(dp(6),dp(7),dp(5),dp(4));
        body.addView(footer);
        final Runnable[] redraw=new Runnable[1];
        redraw[0]=()->{
            shown.clear();shown.addAll(sourceRows);
            filterAndSortHistory(shown,columns,this::researchPopupValues);
            headers.removeAllViews();
            for(String key:columns.visibleColumns()){
                tableHeaderCell(headers,popupColumnTitle(key),key,
                    popupColumnWidth(key),id,columns,redraw[0]);
            }
            note.setText(section+" · "+bandLabel+" · "+entryLabel+
                " "+shown.size()+"/"+sourceRows.size()+"건");
            adapter.notifyDataSetChanged();
        };
        prefs.setOnClickListener(v->tableOptionsDialog(id,columns,redraw[0]));
        redraw[0].run();
        AlertDialog dialog=new AlertDialog.Builder(this)
            .setTitle(section+" · "+bandLabel+" · "+entryLabel+" 종목")
            .setView(body)
            .setPositiveButton("닫기",null)
            .create();
        dialog.show();
        if(dialog.getWindow()!=null)
            dialog.getWindow().setLayout(
                (int)(getResources().getDisplayMetrics().widthPixels*.96f),-2);
    }

    private Map<String,String> researchListValues(HistoryRow item){
        JSONObject row=item.row;
        Map<String,String> values=researchPopupValues(item);
        values.put("name",nameOf(row));
        values.put("strategy",HistorySelection.strategyTag(item.source,item.frame));
        values.put("score",Double.toString(num(row,"score","gmo_score_at_signal",
            "original_score","signal_score")));
        values.put("outcome",row.optString("outcome","PENDING"));
        values.put("reason",reasonOf(row));
        return values;
    }


    // Manual analysis of the EXACT selected stock/coin strategy. No broker
    // order, strategy setting, exchange execution, or source database is edited.
    private void runAiAnalysis(){
        if(aiAnalysisRunning)return;
        final String chosenMarket=market,chosenStrategy=strategy;
        final String scope=chosenMarket+"/"+chosenStrategy;
        final JSONObject researchPacket=research(chosenMarket,chosenStrategy);
        final JSONObject ledgerPacket=ledger(chosenMarket,chosenStrategy);
        aiAnalysisRunning=true;
        aiAnalysisScope=scope;
        aiAnalysisReport=null;
        aiAnalysisError="";
        render();
        io.execute(()->{
            JSONObject report=null;
            String error="";
            try{
                if(!Python.isStarted())
                    throw new IllegalStateException("전략 엔진 연결 전");
                File folder=new File(getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");
                String raw=Python.getInstance().getModule("ai_strategy_analysis_v040")
                    .callAttr("analyze",folder.getAbsolutePath(),
                        chosenMarket,chosenStrategy,
                        researchPacket.toString(),ledgerPacket.toString()).toString();
                report=new JSONObject(raw);
                if(!report.optBoolean("ok",false)||
                    !scope.equals(report.optString("scope","")))
                    error=report.optString("message","선택한 전략의 분석값이 아님");
            }catch(Exception ex){
                error="분석 오류: "+ex.getClass().getSimpleName()+
                      (ex.getMessage()==null?"":" · "+ex.getMessage());
                new RuntimeCrashStore(this).add("ai_analysis_v040",ex);
            }
            final JSONObject done=report;
            final String failure=error;
            main.post(()->{
                if(scope.equals(aiAnalysisScope)){
                    aiAnalysisReport=failure.isEmpty()?done:null;
                    aiAnalysisError=failure;
                }
                aiAnalysisRunning=false;
                if("analysis".equals(page)&&
                   scope.equals(market+"/"+strategy)&&!integratedMode)render();
            });
        });
    }

    private void aiStatistic(LinearLayout row,String title,String value,int tint){
        LinearLayout tile=new LinearLayout(this);
        tile.setOrientation(LinearLayout.VERTICAL);
        tile.setGravity(Gravity.CENTER);
        tile.setPadding(dp(3),dp(6),dp(3),dp(7));
        tile.setBackground(bg(PremiumUi.SURFACE_HIGH,PremiumUi.LINE,10));
        TextView number=text(value,25);
        number.setGravity(Gravity.CENTER);
        number.setTypeface(null,Typeface.BOLD);
        number.setTextColor(tint);
        autoSingle(number,17,25);
        tile.addView(number,new LinearLayout.LayoutParams(-1,dp(34)));
        TextView label=text(title,14);
        label.setGravity(Gravity.CENTER);
        label.setTextColor(PremiumUi.MUTED);
        label.setSingleLine(true);
        tile.addView(label,new LinearLayout.LayoutParams(-1,dp(23)));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(70),1);
        if(row.getChildCount()>0)lp.setMargins(dp(6),0,0,0);
        row.addView(tile,lp);
    }
    private void aiTitle(LinearLayout parent,String textValue,int topPadding){
        TextView h=text(textValue,16);
        h.setTypeface(Typeface.create("sans-serif-medium",Typeface.BOLD));
        h.setTextColor(Color.rgb(198,220,240));
        h.setPadding(dp(2),dp(topPadding),dp(1),dp(5));
        parent.addView(h);
    }
    private String aiFormatPct(JSONObject obj,String key){
        if(obj==null||obj.isNull(key)||!obj.has(key))return "—";
        return String.format(Locale.KOREA,"%+.2f%%",obj.optDouble(key));
    }
    private String aiFormatScore(JSONObject obj,String key){
        if(obj==null||obj.isNull(key)||!obj.has(key))return "—";
        return String.format(Locale.KOREA,"%+.1f",obj.optDouble(key));
    }
    private void aiMarketBandMetric(LinearLayout parent,String title,String band,
                                    double delta,double hours,boolean good){
        LinearLayout tile=new LinearLayout(this);
        tile.setOrientation(LinearLayout.VERTICAL);
        tile.setPadding(dp(8),dp(7),dp(8),dp(7));
        tile.setBackground(bg(PremiumUi.SURFACE_HIGH,PremiumUi.LINE,8));
        TextView label=text(title+" · "+band,14);
        label.setTextColor(PremiumUi.MUTED);tile.addView(label);
        TextView change=text(String.format(Locale.KOREA,"%+.2f%%",delta),23);
        change.setTypeface(null,Typeface.BOLD);
        change.setTextColor(delta>=0?Color.rgb(93,225,179):Color.rgb(255,131,149));
        tile.addView(change);
        TextView observed=text(String.format(Locale.KOREA,"%.1f시간",hours),13);
        observed.setTextColor(PremiumUi.MUTED);tile.addView(observed);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(93),1);
        if(parent.getChildCount()>0)lp.setMargins(dp(7),0,0,0);
        parent.addView(tile,lp);
    }
    private void aiFrameCard(LinearLayout parent,JSONObject frame){
        LinearLayout tile=card();
        tile.setPadding(dp(12),dp(10),dp(12),dp(12));
        LinearLayout top=new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        String label=frame.optString("label","분봉");
        TextView name=text(label,22);
        name.setTypeface(null,Typeface.BOLD);
        top.addView(name,new LinearLayout.LayoutParams(0,dp(32),1));
        String status=frame.optString("status","");
        String state="VALIDATED".equals(status)?"최근 검증":
                     "HOLDOUT".equals(status)?"최근 비교":
                     "EXPLORATORY".equals(status)?"탐색 결과":
                     "OBSERVED".equals(status)?"종료 관측":
                     "LIVE_OBSERVATION".equals(status)?"현재 관측":"기록 0건";
        TextView badge=text(state,14);
        badge.setTextColor("VALIDATED".equals(status)?
            Color.rgb(83,224,180):Color.rgb(252,193,104));
        badge.setPadding(dp(8),dp(4),dp(8),dp(4));
        badge.setBackground(bg(PremiumUi.SURFACE_HIGH,PremiumUi.LINE,9));
        top.addView(badge);
        tile.addView(top,new LinearLayout.LayoutParams(-1,dp(37)));

        // First line communicates coverage without an explanatory paragraph.
        LinearLayout stats=new LinearLayout(this);
        stats.setOrientation(LinearLayout.HORIZONTAL);
        aiStatistic(stats,"연구 신호",number(frame.optInt("signals",0)),
                    Color.rgb(204,223,241));
        aiStatistic(stats,"실제 진입",number(frame.optInt("entries",0)),
                    Color.rgb(134,201,245));
        aiStatistic(stats,"매도 완료",number(frame.optInt("closed",0)),
                    Color.rgb(110,220,183));
        aiStatistic(stats,"현재 보유",number(frame.optInt("open",0)),
                    Color.rgb(252,191,108));
        LinearLayout.LayoutParams metricsLp=new LinearLayout.LayoutParams(-1,dp(70));
        metricsLp.setMargins(0,dp(6),0,0);
        tile.addView(stats,metricsLp);

        boolean actual=frame.optInt("closed",0)>0;
        boolean holding=frame.optInt("open_marked",0)>0;
        String outcomeLabel=actual?"실현 평균 수익률":
            holding?"보유 평가 수익률":"실현손익 관측 전";
        String amount=actual?aiFormatPct(frame,"avg_realized_pct"):
            holding?aiFormatPct(frame,"avg_open_pct"):"—";
        LinearLayout roi=new LinearLayout(this);
        roi.setOrientation(LinearLayout.HORIZONTAL);
        roi.setGravity(Gravity.CENTER_VERTICAL);
        TextView indicator=text(outcomeLabel,15);
        indicator.setTextColor(PremiumUi.MUTED);
        roi.addView(indicator,new LinearLayout.LayoutParams(0,dp(34),1));
        TextView value=text(amount,25);
        value.setTypeface(null,Typeface.BOLD);
        if(!"—".equals(amount))
            value.setTextColor(amount.startsWith("-")?
                Color.rgb(255,121,142):Color.rgb(85,222,177));
        roi.addView(value);
        LinearLayout.LayoutParams roiLp=new LinearLayout.LayoutParams(-1,dp(36));
        roiLp.setMargins(0,dp(9),0,0);
        tile.addView(roi,roiLp);

        JSONObject effect=frame.optJSONObject("improvement");
        aiTitle(tile,"개선효과 비교",7);
        AiReturnCompareV040 bars=new AiReturnCompareV040(this);
        bars.setResult(effect);
        tile.addView(bars,new LinearLayout.LayoutParams(-1,dp(89)));
        if(effect!=null){
            String improvement=aiFormatPct(effect,"delta_pp");
            TextView delta=text((effect.optBoolean("validated",false)?
                "검증된 개선폭 ":"과거 탐색 개선폭 ")+improvement.replace("%","%p")+
                "  ·  비교 "+effect.optInt("sample_n",0)+"건",15);
            delta.setTextColor(effect.optBoolean("validated",false)?
                Color.rgb(83,224,180):Color.rgb(251,194,106));
            delta.setMaxLines(2);
            tile.addView(delta);
        }

        aiTitle(tile,"지표 축적률",9);
        AiEvidenceMeterV040 evidence=new AiEvidenceMeterV040(this);
        evidence.setEvidence(frame.optJSONObject("coverage"));
        tile.addView(evidence,new LinearLayout.LayoutParams(-1,dp(139)));

        LinearLayout marketLine=new LinearLayout(this);
        marketLine.setGravity(Gravity.CENTER_VERTICAL);
        TextView scoreLabel=text("매수~현재/매도 시장점수 평균",14);
        scoreLabel.setTextColor(PremiumUi.MUTED);
        marketLine.addView(scoreLabel,new LinearLayout.LayoutParams(0,dp(30),1));
        TextView marketScore=text(aiFormatScore(frame,"avg_hold_market_score"),20);
        marketScore.setTypeface(null,Typeface.BOLD);
        marketLine.addView(marketScore);
        tile.addView(marketLine,new LinearLayout.LayoutParams(-1,dp(35)));
        JSONObject weakest=frame.optJSONObject("market_band_worst");
        JSONObject strongest=frame.optJSONObject("market_band_best");
        if(weakest!=null&&strongest!=null){
            aiTitle(tile,"시장점수 구간별 보유수익률 변화",11);
            LinearLayout bandRow=new LinearLayout(this);
            bandRow.setOrientation(LinearLayout.HORIZONTAL);
            aiMarketBandMetric(bandRow,"최저",
                weakest.optString("band",""),
                weakest.optDouble("avg_change_pp",0),
                weakest.optDouble("observed_hours",0),false);
            aiMarketBandMetric(bandRow,"최고",
                strongest.optString("band",""),
                strongest.optDouble("avg_change_pp",0),
                strongest.optDouble("observed_hours",0),true);
            tile.addView(bandRow,new LinearLayout.LayoutParams(-1,dp(95)));
        }
        String sellCause=frame.optString("exit_trigger","").trim();
        if(!sellCause.isEmpty()){
            aiTitle(tile,"실제 매도 사유",6);
            TextView cause=text(sellCause,16);
            cause.setTextColor(Color.rgb(236,192,139));
            cause.setMaxLines(2);
            tile.addView(cause);
        }

        JSONArray patterns=frame.optJSONArray("patterns");
        if(patterns!=null&&patterns.length()>0){
            LinearLayout summary=new LinearLayout(this);
            summary.setOrientation(LinearLayout.VERTICAL);
            summary.setPadding(dp(7),dp(4),dp(7),dp(6));
            summary.setBackground(bg(PremiumUi.SURFACE_HIGH,0,8));
            StringBuilder found=new StringBuilder();
            for(int i=0;i<Math.min(3,patterns.length());i++){
                JSONObject p=patterns.optJSONObject(i);
                if(p==null)continue;
                if(found.length()>0)found.append("   ·   ");
                found.append(p.optString("name",""))
                    .append(" ")
                    .append(String.format(Locale.KOREA,"%.1f",p.optDouble("median")));
            }
            TextView m=text(found.toString(),15);
            m.setTextColor(Color.rgb(193,212,232));
            m.setMaxLines(2);
            summary.addView(m);
            LinearLayout.LayoutParams mlp=new LinearLayout.LayoutParams(-1,-2);
            mlp.setMargins(0,dp(5),0,0);
            tile.addView(summary,mlp);
        }

        aiTitle(tile,"원인 후보 / 공통패턴",12);
        TextView finding=text(frame.optString("finding","현재 지표 관측 중"),17);
        finding.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        finding.setMaxLines(2);
        tile.addView(finding);
        aiTitle(tile,"개선 전략",9);
        TextView proposal=text(frame.optString("proposal","증거 축적 후 재분석"),17);
        proposal.setTextColor(Color.rgb(90,221,179));
        proposal.setMaxLines(3);
        tile.addView(proposal);
        LinearLayout.LayoutParams cardLp=new LinearLayout.LayoutParams(-1,-2);
        cardLp.setMargins(0,0,0,dp(8));
        parent.addView(tile,cardLp);
    }

    private void renderAiAnalysis(){
        final String scope=market+"/"+strategy;
        LinearLayout header=card();
        header.setPadding(dp(14),dp(11),dp(14),dp(11));
        TextView sub=text("AI 전략 분석",16);
        sub.setTextColor(PremiumUi.MUTED);
        header.addView(sub);
        TextView title=text(scopeTitle(market,strategy),26);
        title.setTypeface(null,Typeface.BOLD);
        title.setPadding(0,dp(3),0,dp(7));
        header.addView(title);
        Button start=button(aiAnalysisRunning?"분석 중":"분석 시작",false,this::runAiAnalysis);
        start.setTextSize(17);
        start.setEnabled(!aiAnalysisRunning);
        header.addView(start,new LinearLayout.LayoutParams(-1,dp(51)));
        content.addView(header);

        if(aiAnalysisRunning&&scope.equals(aiAnalysisScope)){
            LinearLayout pending=card();
            TextView msg=text("현재 연구자료 분석 중",18);
            pending.addView(msg);
            ProgressBar progress=new ProgressBar(this);
            LinearLayout.LayoutParams plp=new LinearLayout.LayoutParams(-1,dp(6));
            plp.setMargins(0,dp(12),0,0);
            pending.addView(progress,new LinearLayout.LayoutParams(-1,dp(35)));
            content.addView(pending);
            return;
        }
        if(!scope.equals(aiAnalysisScope)||aiAnalysisReport==null){
            LinearLayout emptyBox=card();
            TextView nothing=text(aiAnalysisError.isEmpty()?
                "이 전략의 분석을 시작하세요":aiAnalysisError,18);
            nothing.setTextColor(PremiumUi.MUTED);
            emptyBox.addView(nothing);
            content.addView(emptyBox);
            return;
        }
        JSONObject report=aiAnalysisReport;
        if(!scope.equals(report.optString("scope","")))return;

        LinearLayout summary=new LinearLayout(this);
        summary.setOrientation(LinearLayout.HORIZONTAL);
        aiStatistic(summary,"누적 신호",number(report.optInt("signals",0)),
                    Color.rgb(207,226,246));
        aiStatistic(summary,"진행 중",number(report.optInt("open",0)),
                    Color.rgb(248,199,120));
        aiStatistic(summary,"종료 관측",number(report.optInt("closed",0)),
                    Color.rgb(105,220,181));
        aiStatistic(summary,"축적 자료",number(report.optInt("feature_snapshots",0)+
            report.optInt("position_snapshots",0)),
                    Color.rgb(134,201,245));
        LinearLayout.LayoutParams totalLp=new LinearLayout.LayoutParams(-1,dp(70));
        totalLp.setMargins(0,dp(9),0,dp(12));
        content.addView(summary,totalLp);

        JSONArray frames=report.optJSONArray("frames");
        if(frames==null||frames.length()==0)return;
        boolean sideBySide=getResources().getConfiguration().screenWidthDp>=720;
        LinearLayout row=null;
        for(int i=0;i<frames.length();i++){
            JSONObject item=frames.optJSONObject(i);
            if(item==null)continue;
            if(!sideBySide){
                aiFrameCard(content,item);
            }else{
                if(i%2==0){
                    row=new LinearLayout(this);
                    row.setOrientation(LinearLayout.HORIZONTAL);
                    content.addView(row,new LinearLayout.LayoutParams(-1,-2));
                }
                LinearLayout wrapper=new LinearLayout(this);
                wrapper.setOrientation(LinearLayout.VERTICAL);
                LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,-2,1);
                if(i%2==1)lp.setMargins(dp(7),0,0,0);
                row.addView(wrapper,lp);
                aiFrameCard(wrapper,item);
            }
        }
    }

    private void renderResearch(){
        String filter=activeHistoryFilter();
        String scope="gmo".equals(filter)||"close".equals(filter)?filter:strategy;
        boolean combined="all".equals(filter);
        Stats st=stats(market,scope);
        JSONObject rs=research(market,scope);
        java.util.ArrayList<HistoryRow> entries=
            new java.util.ArrayList<>(selectedHistory("research"));
        String queryId="research/"+market+"/"+scope;
        ColumnQueryV036 researchQuery=tableQuery(queryId,
            "name","strategy","when","score","outcome","reason","buy","sell");
        filterAndSortHistory(entries,researchQuery,this::researchListValues);
        String gridKey=combined?market+"/all":market+"/"+scope;
        ResearchScoreGrid grid=uiSnapshot.researchGrids.get(gridKey);
        if(grid==null)grid=new ResearchScoreGrid();
        ResearchScoreGrid.Cell tally=grid.totals();

        LinearLayout head=new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);head.setGravity(Gravity.CENTER_VERTICAL);
        head.addView(historyFilterButton(),new LinearLayout.LayoutParams(dp(116),dp(42)));
        TextView h=text("리서치 · "+entries.size(),18);
        h.setTypeface(null,Typeface.BOLD);
        head.addView(h,new LinearLayout.LayoutParams(0,dp(42),1));
        Button export=button("현재 ZIP",false,()->exportResearch(st));
        export.setTextSize(13);head.addView(export,new LinearLayout.LayoutParams(dp(92),dp(38)));
        content.addView(head,new LinearLayout.LayoutParams(-1,dp(44)));

        // Each strategy keeps its existing independent ZIP button above.
        // These three additional actions export every original SQLite/JSON
        // for the selected MARKET group, not only the 3,000-row screen preview.
        LinearLayout bundleButtons=new LinearLayout(this);
        bundleButtons.setOrientation(LinearLayout.HORIZONTAL);
        bundleButtons.setPadding(0,dp(3),0,dp(7));
        Button stockAll=button("주식 통합 ZIP",false,()->requestCombinedResearch("stock"));
        Button coinAll=button("코인 통합 ZIP",false,()->requestCombinedResearch("coin"));
        Button every=button("프로그램 전체 ZIP",false,()->requestCombinedResearch("all"));
        stockAll.setTextSize(11);coinAll.setTextSize(11);every.setTextSize(11);
        bundleButtons.addView(stockAll,new LinearLayout.LayoutParams(0,dp(42),1));
        LinearLayout.LayoutParams groupLp=new LinearLayout.LayoutParams(0,dp(42),1);
        groupLp.setMargins(dp(4),0,dp(4),0);
        bundleButtons.addView(coinAll,groupLp);
        bundleButtons.addView(every,new LinearLayout.LayoutParams(0,dp(42),1));
        content.addView(bundleButtons,new LinearLayout.LayoutParams(-1,dp(52)));

        String loadStatus=rs.optString("load_status","");
        boolean researchReadFailed="ERROR".equals(loadStatus)||
            "QUERY_RETURNED_ZERO".equals(loadStatus)||
            "DB_MISSING".equals(loadStatus)||
            "TABLE_MISSING".equals(loadStatus)||
            "READ_ERROR".equals(loadStatus)||
            "ACCOUNT_STATE_MISSING".equals(loadStatus);
        int loaded=rs.optInt("loaded_items",st.research.length());
        int sourceCount=rs.optInt("source_total",-1);
        boolean partial=sourceCount>=0&&sourceCount>loaded;
        String sourceState=researchReadFailed?"오류":
            partial?"일부조회":
            "OK".equals(loadStatus)?"완료":"미완료";
        TextView origin=text("연구원장  "+sourceState+
            (partial?" · 화면은 최근 기록, 전체 원본은 ZIP에 포함":""),12);
        origin.setTextColor(researchReadFailed?Color.rgb(255,119,133):
            partial?Color.rgb(253,195,111):
            "완료".equals(sourceState)?Color.rgb(85,225,170):PremiumUi.MUTED);
        origin.setPadding(dp(2),dp(3),dp(2),dp(8));
        content.addView(origin);

        double actualRate=tally.actualRate(),virtualRate=tally.virtualRate();
        LinearLayout summary=new LinearLayout(this);summary.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout actualCard=card();DonutMetricView actual=new DonutMetricView(this);
        actual.setMetric("실제 확인도달 비중",tally.actualConfirmedShare(),
            pct(tally.actualConfirmedShare()));
        actualCard.addView(actual,new LinearLayout.LayoutParams(-1,dp(93)));
        TextView a=text(tally.actualHit+"/"+tally.actualCount+
            "  · 판정보류 "+tally.actualPending,12);
        a.setGravity(Gravity.CENTER);actualCard.addView(a,new LinearLayout.LayoutParams(-1,dp(21)));
        summary.addView(actualCard,new LinearLayout.LayoutParams(0,dp(131),1));
        LinearLayout virtualCard=card();DonutMetricView v=new DonutMetricView(this);
        v.setMetric("가상 확인도달 비중",tally.virtualConfirmedShare(),
            pct(tally.virtualConfirmedShare()));
        virtualCard.addView(v,new LinearLayout.LayoutParams(-1,dp(93)));
        TextView vCounts=text(tally.virtualHit+"/"+tally.virtualEntered+
            "  · 판정보류 "+tally.virtualPending,12);
        vCounts.setGravity(Gravity.CENTER);
        virtualCard.addView(vCounts,new LinearLayout.LayoutParams(-1,dp(21)));
        LinearLayout.LayoutParams vp=new LinearLayout.LayoutParams(0,dp(131),1);
        vp.setMargins(dp(8),0,0,0);summary.addView(virtualCard,vp);
        content.addView(summary,new LinearLayout.LayoutParams(-1,dp(131)));

        LinearLayout kpis=new LinearLayout(this);kpis.setOrientation(LinearLayout.HORIZONTAL);
        addKpi(kpis,"전체 신호",String.valueOf(tally.signals));
        addKpi(kpis,"실제",String.valueOf(tally.actualCount));
        addKpi(kpis,"가상",String.valueOf(tally.virtualEntered));
        addKpi(kpis,"미진입",String.valueOf(tally.virtualNotEntered));
        LinearLayout.LayoutParams klp=new LinearLayout.LayoutParams(-1,dp(68));
        klp.setMargins(0,dp(7),0,0);content.addView(kpis,klp);

        TextView section=text("시간봉 × 점수대  |  달성 확인 건수 / 진입종목 · 실제·가상 열 더블탭",17);
        section.setTypeface(null,Typeface.BOLD);
        section.setTextColor(Color.rgb(207,226,247));
        section.setPadding(dp(2),dp(9),dp(2),dp(5));content.addView(section);

        String[] frames=combined
            ?("stock".equals(market)
                ?new String[]{"5m","30m","60m","day","gmo","close"}
                :new String[]{"5m","30m","60m","day"})
            :StrategyDashboardScope.showFrameCharts(scope)
                ?new String[]{"5m","30m","60m","day"}:new String[]{scope};
        boolean matrixColumns=getResources().getConfiguration().screenWidthDp>=720;
        LinearLayout matrixPair=null;
        int matrixInRow=0,maxRowHeight=0;
        for(String tf:frames){
            if(!combined&&HistorySelection.frame(filter).length()>0
                &&!HistorySelection.frame(filter).equals(tf))continue;
            String label="gmo".equals(tf)?"수급매매":"close".equals(tf)?"종가베팅":tfLabel(tf);
            ResearchScoreMatrixView matrix=new ResearchScoreMatrixView(this);
            matrix.setGrid(grid,tf,label);
            matrix.setOnBandDoubleTapListener((matrixFrame,scoreBand,entrySide)->
                showResearchBandPopup(matrixFrame,scoreBand,entrySide));
            int height=grid.cell(tf,ResearchScoreGrid.NO_SCORE).signals>0?246:218;
            matrix.setContentDescription(label+" 점수대별 실제·가상 목표도달률");
            if(!matrixColumns){
                LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,dp(height));
                lp.setMargins(0,dp(5),0,0);content.addView(matrix,lp);
                continue;
            }
            if(matrixPair==null||matrixInRow==2){
                matrixPair=new LinearLayout(this);
                matrixPair.setOrientation(LinearLayout.HORIZONTAL);
                LinearLayout.LayoutParams pairParams=new LinearLayout.LayoutParams(-1,dp(height));
                pairParams.setMargins(0,dp(5),0,0);
                content.addView(matrixPair,pairParams);
                matrixInRow=0;maxRowHeight=height;
            }
            LinearLayout.LayoutParams itemParams=new LinearLayout.LayoutParams(0,-1,1);
            if(matrixInRow>0)itemParams.setMargins(dp(6),0,0,0);
            matrixPair.addView(matrix,itemParams);
            matrixInRow++;
            if(height>maxRowHeight){
                maxRowHeight=height;
                LinearLayout.LayoutParams params=(LinearLayout.LayoutParams)matrixPair.getLayoutParams();
                params.height=dp(height);matrixPair.setLayoutParams(params);
            }
        }
        TextView note=text("실제 열 더블탭 → 실제 진입만 / 가상 열 → 가상 진입만 표시 · "+
            "확인도달 비중=목표확인 건수÷전체 진입 건수 (판정보류는 실패로 간주하지 않음)",12);
        note.setTextColor(Color.rgb(150,170,195));
        note.setPadding(dp(3),dp(7),dp(3),dp(4));content.addView(note);

        LinearLayout researchHead=new LinearLayout(this);
        researchHead.setOrientation(LinearLayout.HORIZONTAL);
        researchHead.setGravity(Gravity.CENTER_VERTICAL);
        TextView recent=text("개별 연구 판단 근거 · "+entries.size()+"건",16);
        recent.setTypeface(null,Typeface.BOLD);
        recent.setPadding(dp(2),dp(7),0,dp(4));
        researchHead.addView(recent,new LinearLayout.LayoutParams(0,dp(43),1));
        Button rfilter=button("정렬·필터",false,()->
            tableOptionsDialog(queryId,researchQuery,this::render));
        rfilter.setTextSize(11);
        researchHead.addView(rfilter,new LinearLayout.LayoutParams(dp(84),dp(37)));
        content.addView(researchHead,new LinearLayout.LayoutParams(-1,dp(45)));
        if(entries.isEmpty()){
            empty("이 전략의 연구기록 없음 · 원본 건수와 로딩상태 확인");
            return;
        }
        int first=historyStart("research",entries.size());
        int last=HistoryPager.end(first,entries.size());
        addHistoryPager("research",entries.size());
        for(int i=first;i<last;i++){
            HistoryRow item=entries.get(i);
            JSONObject row=item.row;if(row==null)continue;
            LinearLayout panel=card();
            LinearLayout title=new LinearLayout(this);title.setOrientation(LinearLayout.HORIZONTAL);
            TextView symbol=text(nameOf(row)+" · "+HistorySelection.strategyTag(item.source,item.frame),14);
            symbol.setTypeface(null,Typeface.BOLD);
            bindSymbolFourTap(symbol,row,market);
            title.addView(symbol,new LinearLayout.LayoutParams(0,dp(26),1));
            double sc=num(row,"score","original_score","gmo_score_at_signal");
            TextView score=text(finite(sc)?number(sc):"—",19);
            score.setTextColor(Color.rgb(252,193,99));score.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
            title.addView(score,new LinearLayout.LayoutParams(dp(66),dp(26)));
            panel.addView(title,new LinearLayout.LayoutParams(-1,dp(29)));

            // The old research rows attached every status, reason and observation
            // as a left-aligned full-width sentence. Use three genuine weighted
            // information columns to occupy the landscape display instead.
            boolean wideEvidence=getResources().getConfiguration().screenWidthDp>=720;
            LinearLayout researchEvidenceColumns=new LinearLayout(this);
            researchEvidenceColumns.setOrientation(wideEvidence?
                LinearLayout.HORIZONTAL:LinearLayout.VERTICAL);
            LinearLayout identityCol=new LinearLayout(this);identityCol.setOrientation(LinearLayout.VERTICAL);
            LinearLayout reasonCol=new LinearLayout(this);reasonCol.setOrientation(LinearLayout.VERTICAL);
            LinearLayout observedCol=new LinearLayout(this);observedCol.setOrientation(LinearLayout.VERTICAL);
            String outcome=row.optString("outcome","");
            if(outcome.isEmpty()){Boolean reached=target(row);
                outcome=reached==null?"PENDING":reached?"HIT":"MISS";}
            boolean actualEntry=bool(row,"bought");
            boolean unselected=bool(row,"unselected")||
                row.optString("entry_status","").contains("UNSELECTED");
            String tag=actualEntry?("gmo".equals(item.source)?"PAPER체결":"원장체결"):
                unselected?"불채택":"가상";
            String outcomeKo="HIT".equals(outcome)?
                (actualEntry?"1차 매도 확인":"가상 목표관측"):
                "MISS".equals(outcome)?
                (actualEntry?"목표 미체결 종료":"가상 목표미도달"):"후속 관측 중";
            TextView status=text(tag+" · "+outcomeKo+"\n신호 "+timeOf(row,item.source),13);
            status.setTextColor("HIT".equals(outcome)?Color.rgb(62,224,181):
                "MISS".equals(outcome)?Color.rgb(255,119,133):Color.rgb(255,194,91));
            status.setMaxLines(5);
            identityCol.addView(status);
            String basis=reasonOf(row);
            TextView detail=text("판단근거\n"+basis,13);
            detail.setTextColor(Color.rgb(177,199,223));
            detail.setMaxLines(4);
            reasonCol.addView(detail);
            int ranked=row.optInt("origin_rank",0);
            if(unselected&&ranked>0){
                int peers=row.optInt("cohort_size",0);
                TextView rankText=text("상대 순위  "+ranked+
                    (peers>0?" / "+peers:"")+"  ·  "+row.optString("unselected_reason",""),12);
                rankText.setTextColor(Color.rgb(250,202,125));
                rankText.setMaxLines(3);
                reasonCol.addView(rankText);
            }

            double entry=num(row,"buy_price","virtual_buy_price","limit_price","official_d1_open");
            double targetPrice=num(row,"first_target","target","observed_target_price");
            String entryText=finite(entry)&&entry>0?money(entry):"미기록";
            String goalText=finite(targetPrice)&&targetPrice>0?money(targetPrice):"미기록";
            TextView priceFacts=text("매수·기준가 "+entryText+"  |  목표·관측가 "+goalText,12);
            priceFacts.setTextColor(Color.rgb(167,196,220));
            priceFacts.setMaxLines(3);
            observedCol.addView(priceFacts);
            // These measurements are EXE-observed price excursions, not P&L.
            double maxObs=num(row,"observed_max_gain_pct","observed_mfe_pct");
            double minObs=num(row,"observed_min_gain_pct","observed_mae_pct");
            double finalObs=num(row,"observed_return_pct");
            if(!finite(maxObs)&&finite(finalObs))maxObs=finalObs;
            if(!finite(minObs)&&finite(finalObs))minObs=finalObs;
            if(finite(maxObs)||finite(minObs)){
                TextView observed=text("후속 관측  최고 "+signedPct(maxObs)+
                    "  /  최저 "+signedPct(minObs),12);
                observed.setTextColor(Color.rgb(188,209,227));
                observed.setMaxLines(3);
                observedCol.addView(observed);
                TradeRangeView range=new TradeRangeView(this,minObs,maxObs);
                observedCol.addView(range,new LinearLayout.LayoutParams(-1,dp(25)));
            }
            // Only originally recorded position/fill timestamps prove a trade.
            Object buyAt=firstValue(row,"buy_at");
            if(actualEntry&&buyAt!=null){
                TextView bought=text(("gmo".equals(item.source)?"PAPER 매수":"원장 매수")+
                    " · "+researchMoment(buyAt),12);
                bought.setTextColor(PremiumUi.MUTED);
                identityCol.addView(bought);
            }
            Object sellAt=firstValue(row,"sell_at");
            if(actualEntry&&sellAt!=null&&!row.optString("sell_evidence","").isEmpty()){
                TextView sold=text(("gmo".equals(item.source)?"PAPER 매도":"원장 매도")+
                    " · "+researchMoment(sellAt),12);
                sold.setTextColor(PremiumUi.MUTED);observedCol.addView(sold);
                TextView exit=text("매도 원인 · "+tradeReasonOf(row,"매도"),12);
                exit.setTextColor(PremiumUi.TEXT);exit.setMaxLines(3);
                observedCol.addView(exit);
            }
            if(wideEvidence){
                researchEvidenceColumns.addView(identityCol,
                    new LinearLayout.LayoutParams(0,-2,1.25f));
                LinearLayout.LayoutParams reasonWeight=
                    new LinearLayout.LayoutParams(0,-2,1.55f);
                reasonWeight.setMargins(dp(9),0,0,0);
                researchEvidenceColumns.addView(reasonCol,reasonWeight);
                LinearLayout.LayoutParams observedWeight=
                    new LinearLayout.LayoutParams(0,-2,1.65f);
                observedWeight.setMargins(dp(9),0,0,0);
                researchEvidenceColumns.addView(observedCol,observedWeight);
            }else{
                researchEvidenceColumns.addView(identityCol,new LinearLayout.LayoutParams(-1,-2));
                researchEvidenceColumns.addView(reasonCol,new LinearLayout.LayoutParams(-1,-2));
                researchEvidenceColumns.addView(observedCol,new LinearLayout.LayoutParams(-1,-2));
            }
            panel.addView(researchEvidenceColumns,new LinearLayout.LayoutParams(-1,-2));
            LinearLayout.LayoutParams itemLp=new LinearLayout.LayoutParams(-1,-2);
            itemLp.setMargins(0,0,0,dp(6));content.addView(panel,itemLp);
        }
        addHistoryPager("research",entries.size());
    }

    private Map<String,String> scoreListValues(HistoryRow item){
        JSONObject x=item.row;
        Map<String,String> out=new HashMap<>();
        out.put("name",nameOf(x));
        out.put("strategy",HistorySelection.strategyTag(item.source,item.frame));
        out.put("score",Double.toString(num(x,"score","diverse_11_score","gmo_score_at_signal")));
        out.put("decision",x.optString("decision",
            x.optString("status",x.optString("verdict",""))));
        out.put("reason",reasonOf(x));
        out.put("when",timeOf(x,item.source));
        return out;
    }

    private void renderScores(){
        JSONObject rs=research(market,strategy);
        JSONArray items=rs.optJSONArray("score_items");if(items==null)items=rs.optJSONArray("items");if(items==null)items=new JSONArray();
        java.util.ArrayList<HistoryRow> scoreRows=new java.util.ArrayList<>();
        for(int i=0;i<items.length();i++){
            JSONObject x=items.optJSONObject(i);
            if(x!=null)scoreRows.add(new HistoryRow(x,strategy,timeFrameOf(x),rowTimestamp(x)));
        }
        String tableId="scores/"+market+"/"+strategy;
        ColumnQueryV036 query=tableQuery(tableId,
            "name","strategy","score","decision","reason","when");
        filterAndSortHistory(scoreRows,query,this::scoreListValues);
        LinearLayout scoreHead=new LinearLayout(this);
        scoreHead.setOrientation(LinearLayout.HORIZONTAL);
        scoreHead.setGravity(Gravity.CENTER_VERTICAL);
        TextView h=text(strategyName(market,strategy)+" · 엔진 점수 비교",20);
        h.setTypeface(null,Typeface.BOLD);
        scoreHead.addView(h,new LinearLayout.LayoutParams(0,dp(43),1));
        Button sort=button("정렬·필터",false,()->
            tableOptionsDialog(tableId,query,this::render));
        sort.setTextSize(11);
        scoreHead.addView(sort,new LinearLayout.LayoutParams(dp(86),dp(37)));
        content.addView(scoreHead,new LinearLayout.LayoutParams(-1,dp(45)));
        TextView sub=text("미선정 후보 포함 · 후보 당시 점수와 판정 상태 · 실제 계좌성과와 분리",13);sub.setTextColor(Color.rgb(135,160,190));sub.setPadding(0,dp(3),0,dp(9));content.addView(sub);
        LinearLayout k=card();LinearLayout kr=new LinearLayout(this);kr.setOrientation(LinearLayout.HORIZONTAL);
        addMini(kr,"후보",String.valueOf(items.length()));
        int hi=0,mid=0,low=0;for(int i=0;i<items.length();i++){JSONObject x=items.optJSONObject(i);double sc=x==null?Double.NaN:num(x,"score","diverse_11_score","gmo_score_at_signal");if(!finite(sc))continue;if(sc>=61)hi++;else if(sc>=41)mid++;else low++;}
        addMini(kr,"61점+",String.valueOf(hi));addMini(kr,"41~60",String.valueOf(mid));addMini(kr,"40점↓",String.valueOf(low));k.addView(kr);content.addView(k,new LinearLayout.LayoutParams(-1,dp(78)));
        if(scoreRows.isEmpty()){
            empty("점수 비교 기록 없음 / 필터 조건을 확인하십시오.");return;
        }
        int first=historyStart("scores",scoreRows.size()),
            last=HistoryPager.end(first,scoreRows.size());
        addHistoryPager("scores",scoreRows.size());
        for(int i=first;i<last;i++){
            JSONObject r=scoreRows.get(i).row;if(r==null)continue;LinearLayout c=card();
            LinearLayout hr=new LinearLayout(this);hr.setOrientation(LinearLayout.HORIZONTAL);
            TextView n=text(nameOf(r),16);n.setTypeface(null,Typeface.BOLD);bindSymbolFourTap(n,r,market);hr.addView(n,new LinearLayout.LayoutParams(0,dp(28),1));
            double sc=num(r,"score","diverse_11_score","gmo_score_at_signal");TextView sv=text(finite(sc)?number(sc)+"점":"점수 미기록",15);sv.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);sv.setTextColor(Color.rgb(255,201,94));hr.addView(sv,new LinearLayout.LayoutParams(dp(110),dp(28)));c.addView(hr);
            String dec=r.optString("decision",r.optString("status",r.optString("verdict","판정 미기록")));
            TextView d=text("판정  "+dec+"\n"+refLabel()+" "+timeOf(r),12);
            d.setTextColor(Color.rgb(153,175,201));d.setMaxLines(4);
            TextView why=text("판단 근거  "+reasonOf(r),12);
            why.setTextColor(Color.rgb(184,201,221));why.setMaxLines(4);
            LinearLayout scoreCandidateColumns=new LinearLayout(this);
            boolean wideScore=getResources().getConfiguration().screenWidthDp>=700;
            scoreCandidateColumns.setOrientation(wideScore?
                LinearLayout.HORIZONTAL:LinearLayout.VERTICAL);
            scoreCandidateColumns.addView(d,new LinearLayout.LayoutParams(
                wideScore?0:-1,-2,wideScore?1.2f:0f));
            LinearLayout.LayoutParams reasonLp=new LinearLayout.LayoutParams(
                wideScore?0:-1,-2,wideScore?1.9f:0f);
            if(wideScore)reasonLp.setMargins(dp(10),0,0,0);
            scoreCandidateColumns.addView(why,reasonLp);
            c.addView(scoreCandidateColumns,new LinearLayout.LayoutParams(-1,-2));
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(6));content.addView(c,lp);
        }
        addHistoryPager("scores",scoreRows.size());
    }

    private void appendErrorItems(JSONArray out,JSONObject bucket){
        if(bucket==null)return;JSONArray items=bucket.optJSONArray("items");if(items==null)return;
        for(int i=0;i<items.length();i++)if(items.optJSONObject(i)!=null)out.put(items.optJSONObject(i));
    }

    private boolean errorForMarket(JSONObject r,String m){
        if(r==null)return false;String x=r.toString().toLowerCase(Locale.ROOT);
        boolean stock=x.contains("stock")||x.contains("kiwoom")||x.contains("gmo")||x.contains("close")||x.contains("cw")||x.contains("0b")||x.contains("0d");
        boolean coin=x.contains("coin")||x.contains("upbit")||x.contains("myorder")||x.contains("myasset");
        if(!stock&&!coin)return true;
        return "stock".equals(m)?stock:coin;
    }

    private JSONArray marketErrors(){
        JSONArray raw=new JSONArray();
        if("stock".equals(market)){
            appendErrorItems(raw,record("stockSharedErrors"));
            appendErrorItems(raw,record("stockAtErrors"));
            appendErrorItems(raw,record("stockCwErrors"));
        }else{
            appendErrorItems(raw,record("coinSharedErrors"));
            appendErrorItems(raw,record("coinGeneralErrors"));
            appendErrorItems(raw,record("coinMaErrors"));
        }
        JSONObject c=copyCache();JSONArray run=c.optJSONArray("runtime_diagnostics");
        if(run!=null)for(int i=0;i<run.length();i++){JSONObject r=run.optJSONObject(i);if(errorForMarket(r,market))raw.put(r);}
        JSONObject py=c.optJSONObject("strategy_runtime");JSONArray pe=py==null?null:py.optJSONArray("errors");
        if(pe!=null)for(int i=0;i<pe.length();i++){JSONObject r=pe.optJSONObject(i);if(errorForMarket(r,market))raw.put(r);}
        java.util.HashSet<String> seen=new java.util.HashSet<>();JSONArray out=new JSONArray();
        for(int i=0;i<raw.length();i++){JSONObject r=raw.optJSONObject(i);if(r==null)continue;String key=r.toString();if(seen.add(key))out.put(r);}
        return out;
    }

    private Map<String,String> errorListValues(HistoryRow item){
        JSONObject r=item.row;
        Map<String,String> out=new HashMap<>();
        out.put("stage",r.optString("stage",
            r.optString("error_type",r.optString("code","오류"))));
        out.put("reason",r.optString("message",r.optString("detail","")));
        out.put("when",formatKst(r.opt("at")));
        out.put("status",r.optString("status",r.optString("result","ERROR")));
        return out;
    }

    private void renderErrors(){
        JSONArray all=marketErrors();
        java.util.ArrayList<HistoryRow> errorRows=new java.util.ArrayList<>();
        for(int i=0;i<all.length();i++){
            JSONObject r=all.optJSONObject(i);
            if(r!=null)errorRows.add(new HistoryRow(r,"errors","",rowTimestamp(r)));
        }
        String tableId="errors/"+market;
        ColumnQueryV036 query=tableQuery(tableId,
            "stage","reason","when","status");
        filterAndSortHistory(errorRows,query,this::errorListValues);
        LinearLayout errorsHead=new LinearLayout(this);
        errorsHead.setOrientation(LinearLayout.HORIZONTAL);
        errorsHead.setGravity(Gravity.CENTER_VERTICAL);
        TextView h=text(("stock".equals(market)?"주식":"코인")+
            " 오류이력 · "+errorRows.size(),18);
        h.setTypeface(null,Typeface.BOLD);
        errorsHead.addView(h,new LinearLayout.LayoutParams(0,dp(44),1));
        Button filter=button("정렬·필터",false,()->
            tableOptionsDialog(tableId,query,this::render));
        filter.setTextSize(11);
        errorsHead.addView(filter,new LinearLayout.LayoutParams(dp(87),dp(37)));
        content.addView(errorsHead,new LinearLayout.LayoutParams(-1,dp(45)));
        TextView note=text("선택 전략과 무관하게 해당 시장의 실시간·전략·주문·원장 오류를 통합 표시",10);note.setTextColor(Color.rgb(135,160,190));note.setPadding(0,dp(3),0,dp(9));content.addView(note);
        LinearLayout summary=new LinearLayout(this);summary.setOrientation(LinearLayout.HORIZONTAL);
        addKpi(summary,"전체",String.valueOf(all.length()));
        int auth=0,feed=0,order=0;
        for(int i=0;i<all.length();i++){JSONObject r=all.optJSONObject(i);String x=r==null?"":r.toString().toLowerCase(Locale.ROOT);if(x.contains("auth")||x.contains("token")||x.contains("key")||x.contains("oauth"))auth++;if(x.contains("websocket")||x.contains("feed")||x.contains("0b")||x.contains("0d"))feed++;if(x.contains("order")||x.contains("fill")||x.contains("체결")||x.contains("주문"))order++;}
        addKpi(summary,"인증",String.valueOf(auth));addKpi(summary,"실시간",String.valueOf(feed));addKpi(summary,"주문/체결",String.valueOf(order));content.addView(summary);
        if(errorRows.isEmpty()){
            empty(all.length()==0?"기록된 오류 없음":"필터 조건에 해당하는 오류 없음");
            return;
        }
        int first=historyStart("errors",errorRows.size()),
            last=HistoryPager.end(first,errorRows.size());
        addHistoryPager("errors",errorRows.size());
        for(int i=first;i<last;i++){
            JSONObject r=errorRows.get(i).row;if(r==null)continue;LinearLayout c=card();
            TextView n=text(r.optString("stage",
                r.optString("error_type",r.optString("code","오류 기록"))),13);
            n.setTypeface(null,Typeface.BOLD);n.setTextColor(Color.rgb(255,145,155));
            n.setMaxLines(3);
            TextView d=text(r.optString("message",r.optString("detail","상세 미기록")),12);
            d.setTextColor(Color.rgb(192,209,228));d.setMaxLines(5);
            TextView t=text(formatKst(r.opt("at")),11);
            t.setTextColor(Color.rgb(140,166,195));t.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
            LinearLayout errorHistoryColumns=new LinearLayout(this);
            boolean wideError=getResources().getConfiguration().screenWidthDp>=700;
            errorHistoryColumns.setOrientation(wideError?
                LinearLayout.HORIZONTAL:LinearLayout.VERTICAL);
            errorHistoryColumns.addView(n,new LinearLayout.LayoutParams(
                wideError?0:-1,-2,wideError?1.4f:0f));
            LinearLayout.LayoutParams detailLp=new LinearLayout.LayoutParams(
                wideError?0:-1,-2,wideError?2.6f:0f);
            if(wideError)detailLp.setMargins(dp(10),0,0,0);
            errorHistoryColumns.addView(d,detailLp);
            LinearLayout.LayoutParams timeLp=new LinearLayout.LayoutParams(
                wideError?0:-1,-2,wideError?1.1f:0f);
            if(wideError)timeLp.setMargins(dp(9),0,0,0);
            errorHistoryColumns.addView(t,timeLp);
            c.addView(errorHistoryColumns,new LinearLayout.LayoutParams(-1,-2));
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(7));content.addView(c,lp);
        }
        addHistoryPager("errors",errorRows.size());
    }

    private JSONObject defaultOperationSettings(){
        JSONObject root=new JSONObject();
        try{
            JSONObject stock=new JSONObject(),coin=new JSONObject();
            for(String stg:new String[]{"at","ma"}){
                JSONObject frames=new JSONObject();
                for(String tf:new String[]{"5m","30m","60m","day"})frames.put(tf,new JSONObject().put("seed",2000000).put("max_positions",4));
                stock.put(stg,new JSONObject().put("frames",frames));
            }
            stock.put("close",new JSONObject().put("seed",2000000).put("max_positions",4));
            stock.put("gmo",new JSONObject().put("seed",2000000).put("max_positions",4));
            for(String stg:new String[]{"general","ma"}){
                JSONObject frames=new JSONObject();
                for(String tf:new String[]{"5m","30m","60m","day"})frames.put(tf,new JSONObject().put("seed",2000000).put("max_positions",4));
                coin.put(stg,new JSONObject().put("frames",frames));
            }
            root.put("stock",stock).put("coin",coin);
        }catch(Exception ignored){}
        return root;
    }

    private JSONObject operationSettings(){
        // Latest explicitly saved UI values outrank a stale asynchronous engine snapshot.
        try{
            String raw=getSharedPreferences("unified_operation_v076",MODE_PRIVATE).getString("settings","");
            if(!raw.isEmpty())return new JSONObject(raw);
        }catch(Exception ignored){}
        try{
            JSONObject rt=copyCache().optJSONObject("strategy_runtime");
            JSONObject op=rt==null?null:rt.optJSONObject("operation");
            JSONObject settings=op==null?null:op.optJSONObject("settings");
            if(settings!=null&&settings.length()>0)return settings;
        }catch(Exception ignored){}
        try{
            String raw=getSharedPreferences("unified_operation_v074",MODE_PRIVATE).getString("settings","");
            if(!raw.isEmpty())return new JSONObject(raw);
        }catch(Exception ignored){}
        return defaultOperationSettings();
    }
    private void loadOperationDrafts(){
        if(operationDraftLoaded)return;
        operationDraftLoaded=true;
        try{
            String raw=getSharedPreferences("artitrade_operation_drafts_v1",MODE_PRIVATE).getString("values","{}");
            JSONObject obj=new JSONObject(raw);
            java.util.Iterator<String> it=obj.keys();
            while(it.hasNext()){String k=it.next();operationDraftValues.put(k,obj.optString(k,""));}
        }catch(Exception e){new RuntimeCrashStore(this).add("operation_draft_load",e);}
    }
    private void persistOperationDrafts(){
        try{
            JSONObject obj=new JSONObject();
            for(Map.Entry<String,String> entry:operationDraftValues.entrySet())obj.put(entry.getKey(),entry.getValue());
            getSharedPreferences("artitrade_operation_drafts_v1",MODE_PRIVATE).edit()
                .putString("values",obj.toString()).apply();
        }catch(Exception e){new RuntimeCrashStore(this).add("operation_draft_write",e);}
    }
    private String operationDraft(String key,String field,String fallback){
        loadOperationDrafts();
        String value=operationDraftValues.get(key+"."+field);
        return value==null?fallback:value;
    }
    private void trackOperationDraft(EditText field,String key){
        field.addTextChangedListener(new TextWatcher(){
            @Override public void beforeTextChanged(CharSequence s,int start,int count,int after){}
            @Override public void onTextChanged(CharSequence s,int start,int before,int count){}
            @Override public void afterTextChanged(Editable text){
                operationDraftValues.put(key,text.toString());
                persistOperationDrafts();
            }
        });
    }
    private void clearSavedOperationDrafts(String selectedMarket){
        loadOperationDrafts();
        operationDraftValues.keySet().removeIf(key->key.startsWith(selectedMarket+"."));
        persistOperationDrafts();
    }

    private JSONObject operationPair(JSONObject settings,String m,String stg,String tf){
        JSONObject ms=settings.optJSONObject(m);JSONObject cfg=ms==null?null:ms.optJSONObject(stg);
        if(cfg==null)cfg=new JSONObject();
        if(tf!=null){
            JSONObject frames=cfg.optJSONObject("frames");
            JSONObject pair=frames==null?null:frames.optJSONObject(tf);
            if(pair!=null)return pair;
            // v0.7.4/0.7.5 migration: one strategy-level pair becomes each timeframe.
        }
        return cfg;
    }

    private EditText operationInput(String value,String hint){
        EditText e=new EditText(this);e.setText(value);e.setHint(hint);e.setSingleLine(true);
        e.setTextSize(15);e.setTextColor(Color.WHITE);e.setHintTextColor(Color.rgb(111,121,143));
        e.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        e.setBackground(bg(PremiumUi.SURFACE,PremiumUi.LINE,10));
        e.setPadding(dp(10),0,dp(10),0);return e;
    }

    private LinearLayout operationFrameCard(String m,String stg,String tf,JSONObject settings){
        JSONObject cfg=operationPair(settings,m,stg,tf);
        long seed=cfg.optLong("seed",2000000),maxp=cfg.optLong("max_positions",4);
        LinearLayout c=card();
        LinearLayout head=new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        TextView tfv=text(tfLabel(tf),18);tfv.setTypeface(null,Typeface.BOLD);
        tfv.setTextColor(Color.rgb(218,214,255));
        head.addView(tfv,new LinearLayout.LayoutParams(0,dp(28),1));
        TextView acct=text("독립계좌",10);acct.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
        acct.setTextColor(Color.rgb(113,123,145));
        head.addView(acct,new LinearLayout.LayoutParams(dp(64),dp(28)));
        c.addView(head);

        String key=m+"."+stg+"."+tf;
        // Previous cards stacked both fields vertically, leaving the right
        // half empty; align independent seed / max-hold controls side by side.
        LinearLayout operationFrameColumns=new LinearLayout(this);
        operationFrameColumns.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout seedBox=new LinearLayout(this);seedBox.setOrientation(LinearLayout.VERTICAL);
        TextView sl=text("운용시드",12);sl.setTextColor(Color.rgb(150,169,194));
        seedBox.addView(sl,new LinearLayout.LayoutParams(-1,dp(24)));
        EditText seedEdit=operationInput(
            operationDraft(key,"seed",String.valueOf(seed)),"2,000,000");
        trackOperationDraft(seedEdit,key+".seed");
        seedBox.addView(seedEdit,new LinearLayout.LayoutParams(-1,dp(43)));
        operationFrameColumns.addView(seedBox,new LinearLayout.LayoutParams(0,dp(70),1.7f));

        LinearLayout maxBox=new LinearLayout(this);maxBox.setOrientation(LinearLayout.VERTICAL);
        TextView ml=text("최대 보유",12);ml.setTextColor(Color.rgb(150,169,194));
        maxBox.addView(ml,new LinearLayout.LayoutParams(-1,dp(24)));
        EditText maxEdit=operationInput(
            operationDraft(key,"max_positions",String.valueOf(maxp)),"4");
        trackOperationDraft(maxEdit,key+".max_positions");
        maxBox.addView(maxEdit,new LinearLayout.LayoutParams(-1,dp(43)));
        LinearLayout.LayoutParams maxLp=new LinearLayout.LayoutParams(0,dp(70),1f);
        maxLp.setMargins(dp(8),0,0,0);
        operationFrameColumns.addView(maxBox,maxLp);
        c.addView(operationFrameColumns,new LinearLayout.LayoutParams(-1,dp(74)));
        operationFields.put(key,new EditText[]{seedEdit,maxEdit});
        return c;
    }

    private LinearLayout operationSingleCard(String m,String stg,String label,JSONObject settings){
        JSONObject cfg=operationPair(settings,m,stg,null);
        long seed=cfg.optLong("seed",2000000),maxp=cfg.optLong("max_positions",4);
        LinearLayout c=card();
        TextView n=text(label,18);n.setTypeface(null,Typeface.BOLD);n.setTextColor(Color.rgb(230,232,242));c.addView(n,new LinearLayout.LayoutParams(-1,dp(30)));
        TextView scope=text("단일 전략 계좌",11);scope.setTextColor(Color.rgb(117,128,151));scope.setPadding(0,0,0,dp(7));c.addView(scope);
        LinearLayout row=new LinearLayout(this);row.setOrientation(LinearLayout.HORIZONTAL);
        String key=m+"."+stg;
        LinearLayout seedBox=new LinearLayout(this);seedBox.setOrientation(LinearLayout.VERTICAL);TextView sl=text("운용시드",11);sl.setTextColor(Color.rgb(137,147,169));seedBox.addView(sl,new LinearLayout.LayoutParams(-1,dp(23)));EditText seedEdit=operationInput(operationDraft(key,"seed",String.valueOf(seed)),"2,000,000");trackOperationDraft(seedEdit,key+".seed");seedBox.addView(seedEdit,new LinearLayout.LayoutParams(-1,dp(42)));row.addView(seedBox,new LinearLayout.LayoutParams(0,dp(68),2));
        LinearLayout maxBox=new LinearLayout(this);maxBox.setOrientation(LinearLayout.VERTICAL);TextView ml=text("최대 보유",11);ml.setTextColor(Color.rgb(137,147,169));maxBox.addView(ml,new LinearLayout.LayoutParams(-1,dp(23)));EditText maxEdit=operationInput(operationDraft(key,"max_positions",String.valueOf(maxp)),"4");trackOperationDraft(maxEdit,key+".max_positions");maxBox.addView(maxEdit,new LinearLayout.LayoutParams(-1,dp(42)));LinearLayout.LayoutParams mp=new LinearLayout.LayoutParams(0,dp(68),1);mp.setMargins(dp(8),0,0,0);row.addView(maxBox,mp);
        c.addView(row);
        operationFields.put(m+"."+stg,new EditText[]{seedEdit,maxEdit});
        return c;
    }

    private void addFrameOperationSection(String title,String m,String stg,JSONObject settings){
        TextView h=text(title,19);h.setTypeface(null,Typeface.BOLD);h.setTextColor(Color.rgb(224,226,236));h.setPadding(0,dp(18),0,dp(7));content.addView(h);
        TextView note=text("5분 · 30분 · 1시간 · 일봉 각각 별도 시드 / 최대보유 설정",12);note.setTextColor(Color.rgb(120,131,154));note.setPadding(0,0,0,dp(8));content.addView(note);
        String[] frames={"5m","30m","60m","day"};
        for(int row=0;row<2;row++){
            LinearLayout line=new LinearLayout(this);line.setOrientation(LinearLayout.HORIZONTAL);
            for(int col=0;col<2;col++){
                String tf=frames[row*2+col];
                LinearLayout card=operationFrameCard(m,stg,tf,settings);
                LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(132),1);
                if(col>0)lp.setMargins(dp(8),0,0,0);line.addView(card,lp);
            }
            LinearLayout.LayoutParams rp=new LinearLayout.LayoutParams(-1,dp(132));if(row>0)rp.setMargins(0,dp(8),0,0);content.addView(line,rp);
        }
    }

    private long operationLong(EditText e,String label){
        long v=Long.parseLong(e.getText().toString().replace(",","").trim());
        if(label.contains("시드")){
            if(v<10000||v>2000000000L)throw new IllegalArgumentException(label+"는 1만원~20억원 범위");
        }else if(v<1||v>100)throw new IllegalArgumentException(label+"는 1~100 범위");
        return v;
    }

    private void saveOperationAll(){
        JSONObject all=operationSettings();
        try{
            JSONObject marketObj=all.optJSONObject(market);if(marketObj==null){marketObj=new JSONObject();all.put(market,marketObj);}
            if("stock".equals(market)){
                for(String stg:new String[]{"at","ma"}){
                    JSONObject frames=new JSONObject();
                    for(String tf:new String[]{"5m","30m","60m","day"}){
                        EditText[] e=operationFields.get("stock."+stg+"."+tf);if(e==null)continue;
                        frames.put(tf,new JSONObject().put("seed",operationLong(e[0],"시드")).put("max_positions",operationLong(e[1],"최대보유")));
                    }
                    marketObj.put(stg,new JSONObject().put("frames",frames));
                }
                for(String stg:new String[]{"close","gmo"}){
                    EditText[] e=operationFields.get("stock."+stg);if(e==null)continue;
                    marketObj.put(stg,new JSONObject().put("seed",operationLong(e[0],"시드")).put("max_positions",operationLong(e[1],"최대보유")));
                }
            }else{
                for(String stg:new String[]{"general","ma"}){
                    JSONObject frames=new JSONObject();
                    for(String tf:new String[]{"5m","30m","60m","day"}){
                        EditText[] e=operationFields.get("coin."+stg+"."+tf);if(e==null)continue;
                        frames.put(tf,new JSONObject().put("seed",operationLong(e[0],"시드")).put("max_positions",operationLong(e[1],"최대보유")));
                    }
                    marketObj.put(stg,new JSONObject().put("frames",frames));
                }
            }
            if(!getSharedPreferences("unified_operation_v076",MODE_PRIVATE).edit()
                .putString("settings",all.toString()).commit())
                throw new IllegalStateException("운용설정 로컬 저장 실패");
            Intent i=new Intent(this,UnifiedTradingService.class);i.setAction(UnifiedTradingService.APPLY_OPERATION);i.putExtra("settings",all.toString());
            if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);
            // Clear only the market whose validated settings were explicitly saved.
            clearSavedOperationDrafts(market);
            main.postDelayed(()->refresh(false),1200);
        }catch(Exception e){
            new AlertDialog.Builder(this).setTitle("운용 설정 확인").setMessage(String.valueOf(e.getMessage())).setPositiveButton("확인",null).show();
        }
    }

    private void renderOperation(){
        operationFields.clear();
        TextView h=text(("stock".equals(market)?"주식":"코인")+" 통합 운용",24);h.setTypeface(null,Typeface.BOLD);content.addView(h);
        TextView dsc=text("각 실제 계좌 단위로 운용시드와 최대 보유종목을 설정 · 기본 2,000,000원 / 4종목",13);dsc.setTextColor(Color.rgb(137,147,169));dsc.setPadding(0,dp(4),0,dp(2));content.addView(dsc);
        JSONObject settings=operationSettings();

        if("stock".equals(market)){
            addFrameOperationSection("기준봉", "stock","at",settings);
            addFrameOperationSection("MA", "stock","ma",settings);

            TextView single=text("단일 전략 계좌",19);single.setTypeface(null,Typeface.BOLD);single.setTextColor(Color.rgb(224,226,236));single.setPadding(0,dp(18),0,dp(8));content.addView(single);
            LinearLayout row=new LinearLayout(this);row.setOrientation(LinearLayout.HORIZONTAL);
            row.addView(operationSingleCard("stock","close","종가베팅",settings),new LinearLayout.LayoutParams(0,dp(138),1));
            LinearLayout.LayoutParams gp=new LinearLayout.LayoutParams(0,dp(138),1);gp.setMargins(dp(8),0,0,0);row.addView(operationSingleCard("stock","gmo","수급매매",settings),gp);
            content.addView(row,new LinearLayout.LayoutParams(-1,dp(138)));
        }else{
            addFrameOperationSection("기준봉", "coin","general",settings);
            addFrameOperationSection("MA", "coin","ma",settings);
        }

        Button save=button("현재 "+("stock".equals(market)?"주식":"코인")+" 전체 계좌 설정 저장",true,this::saveOperationAll);
        save.setTextSize(14);LinearLayout.LayoutParams sv=new LinearLayout.LayoutParams(-1,dp(48));sv.setMargins(0,dp(14),0,0);content.addView(save,sv);

        try{
            JSONObject r=new JSONObject(getSharedPreferences("artitrade_operation_result",MODE_PRIVATE).getString("result","{}"));
            if(r.length()>0){
                TextView st=text(r.optBoolean("ok",false)?"엔진 계좌설정 적용 완료":"일부 계좌설정 적용 실패 · 오류 이력 확인",12);
                st.setTextColor(r.optBoolean("ok",false)?Color.rgb(79,219,170):Color.rgb(255,119,142));st.setPadding(0,dp(7),0,0);content.addView(st);
            }
        }catch(Exception ignored){}

        JSONObject live=copyCache().optJSONObject("live_orders");boolean armed=live!=null&&live.optBoolean(market,false);
        LinearLayout ctrl=card();TextView t=text("S21+ 메인 실행권한",17);t.setTypeface(null,Typeface.BOLD);ctrl.addView(t);
        LinearLayout dr=new LinearLayout(this);dr.setOrientation(LinearLayout.HORIZONTAL);dr.setPadding(0,dp(9),0,0);dr.addView(button("PHONE MAIN",false,()->setDesired("phone")),new LinearLayout.LayoutParams(0,dp(44),1));LinearLayout.LayoutParams offp=new LinearLayout.LayoutParams(0,dp(44),1);offp.setMargins(dp(8),0,0,0);dr.addView(button("자동매매 정지",false,()->setDesired("off")),offp);ctrl.addView(dr);
        TextView lt=text("실거래 자동주문",17);lt.setTypeface(null,Typeface.BOLD);lt.setPadding(0,dp(14),0,0);ctrl.addView(lt);
        LinearLayout lr=new LinearLayout(this);lr.setOrientation(LinearLayout.HORIZONTAL);lr.setPadding(0,dp(9),0,0);lr.addView(button("실거래 ON",armed,()->setLive(true)),new LinearLayout.LayoutParams(0,dp(44),1));LinearLayout.LayoutParams of=new LinearLayout.LayoutParams(0,dp(44),1);of.setMargins(dp(8),0,0,0);lr.addView(button("실거래 OFF",!armed,()->setLive(false)),of);ctrl.addView(lr);
        TextView hint=text(armed?"실거래 ON · 최신 EXE 전략 신호가 실제 주문으로 연결됨":"실거래 OFF · 시세/전략은 계속 실행, 실제 주문만 차단",12);hint.setTextColor(armed?Color.rgb(62,224,181):Color.rgb(255,194,91));hint.setPadding(0,dp(9),0,0);ctrl.addView(hint);
        LinearLayout.LayoutParams cp=new LinearLayout.LayoutParams(-1,-2);cp.setMargins(0,dp(12),0,0);content.addView(ctrl,cp);
    }

    private void renderMore(){
        TextView h=text("도구",18);h.setTypeface(null,Typeface.BOLD);h.setPadding(0,0,0,dp(8));content.addView(h);
        LinearLayout c=card();c.addView(text("키움 · 업비트 API",13));Button api=button("직접 API 키 설정",true,this::apiSettings);LinearLayout.LayoutParams ap=new LinearLayout.LayoutParams(-1,dp(42));ap.setMargins(0,dp(8),0,0);c.addView(api,ap);content.addView(c);
        LinearLayout p=card();p.addView(text("MSERVER(모바일 서버) · 토큰 등록 후 연결을 검사할 수 있음",13));Button pair=button("토큰 등록 · 연결 확인",true,this::pair);LinearLayout.LayoutParams pp=new LinearLayout.LayoutParams(-1,dp(42));pp.setMargins(0,dp(8),0,0);p.addView(pair,pp);LinearLayout.LayoutParams pc=new LinearLayout.LayoutParams(-1,-2);pc.setMargins(0,dp(8),0,0);content.addView(p,pc);
        LinearLayout b=card();
        b.addView(text("화면 꺼짐 · 자동매매 서비스 상태",15));
        android.content.SharedPreferences health=getSharedPreferences(
            "artitrade_background_health_v1",MODE_PRIVATE);
        long last=health.getLong("last_heartbeat_ms",0L);
        boolean serviceOn=health.getBoolean("service_running",false);
        boolean cpu=health.getBoolean("cpu_lock",false);
        boolean wifi=health.getBoolean("wifi_lock",false);
        long elapsed=last>0?Math.max(0,System.currentTimeMillis()-last):-1;
        String runtime=(serviceOn&&elapsed>=0&&elapsed<20000L)
            ?"서비스 작동 · "+(elapsed/1000L)+"초 전 확인":"서비스 중단 또는 응답 지연";
        TextView status=text(runtime,14);
        status.setTextColor(serviceOn&&elapsed<20000L?
            Color.rgb(103,224,184):Color.rgb(255,180,125));
        status.setPadding(0,dp(6),0,dp(2));
        b.addView(status);
        TextView locks=text("CPU 유지 "+(cpu?"적용":"확인 필요")+
            "   /   Wi-Fi 유지 "+(wifi?"적용":"확인 필요"),13);
        locks.setTextColor(Color.rgb(166,190,215));
        b.addView(locks);
        TextView caveat=text("절전 제한·강제 종료·통신 장애 시 주문 중단 가능 · 연결 이상이면 안전 차단",12);
        caveat.setTextColor(PremiumUi.MUTED);
        caveat.setPadding(0,dp(4),0,dp(3));
        b.addView(caveat);
        Button bat=button("절전 예외 설정 열기",false,()->{
            try{startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}
            catch(Exception ignored){}
        });
        LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,dp(42));
        bp.setMargins(0,dp(8),0,0);
        b.addView(bat,bp);
        LinearLayout.LayoutParams bc=new LinearLayout.LayoutParams(-1,-2);
        bc.setMargins(0,dp(8),0,0);
        content.addView(b,bc);
    }

    private void empty(String s){TextView e=text(s,13);e.setGravity(Gravity.CENTER);e.setTextColor(PremiumUi.MUTED);e.setPadding(0,dp(22),0,dp(22));content.addView(e);}

    private String strategyName(String m,String s){
        if("coin".equals(m))return "ma".equals(s)?"MA":"기준봉";
        if("ma".equals(s))return "MA";if("close".equals(s))return "종가베팅";if("gmo".equals(s))return "수급매매";return "기준봉";
    }

    private String refLabel(String forStrategy){
        return "gmo".equals(forStrategy)?"사건일":
            "close".equals(forStrategy)?"매수시각":"기준시각";
    }

    private String tradeSideOf(JSONObject row){
        String raw=row.optString("side",row.optString("type",row.optString("action","")));
        return TradeExplanation.side(raw);
    }
    /** The execution fill timestamp must outrank reference-candle/event dates. */
    private String fillTimeOf(JSONObject row,String kind,String originStrategy){
        String[] keys="executions".equals(kind)?
            new String[]{"filled_at","executed_at","fill_at","fill_time","executed_time",
                "trade_time","trade_at","time","ts","at","sold_at","sell_at",
                "buy_at","entry_at","created_at"}:
            "positions".equals(kind)?
                new String[]{"buy_filled_at","entry_filled_at","filled_at","buy_at","entry_at",
                    "entry_ts","buy_ts","time","ts","created_at"}:
                new String[]{"submitted_at","ordered_at","order_at","created_at","since_ts",
                    "buy_at","at","ts"};
        for(String key:keys){
            Object v=row.opt(key);
            if(v==null||v==JSONObject.NULL||String.valueOf(v).trim().isEmpty())continue;
            boolean explicitlyUtc=key.toLowerCase(Locale.ROOT).contains("utc")||
                "UTC".equalsIgnoreCase(row.optString("timestamp_timezone",""))||
                "UTC".equalsIgnoreCase(row.optString("time_zone",""));
            String formatted=TradeExplanation.kst(v,explicitlyUtc);
            if(!formatted.startsWith("시각 형식")&&!formatted.startsWith("체결시각 미기록"))
                return formatted;
        }
        return "체결시각 미기록";
    }
    /** A sell must show its SELL decision, not the first BUY reason on the position. */
    private String tradeReasonOf(JSONObject row,String action){
        final String[] keys;
        if("매도".equals(action)){
            keys=new String[]{"sell_reason","exit_reason","sell_cause","exit_cause",
                "fill_reason","execution_reason","order_reason","reason",
                "purpose","exit_signal","decision","cause"};
        }else if("매수".equals(action)){
            keys=new String[]{"buy_reason","entry_reason","buy_cause","entry_cause",
                "fill_reason","execution_reason","order_reason","reason",
                "purpose","signal_reason","decision","cause"};
        }else{
            keys=new String[]{"reason","decision","cause","signal_reason","purpose"};
        }
        for(String key:keys){
            Object val=row.opt(key);
            if(val==null||val==JSONObject.NULL)continue;
            String raw=String.valueOf(val).trim();
            if(raw.isEmpty()||"null".equalsIgnoreCase(raw))continue;
            String normalized=TradeExplanation.shortReason(raw,action);
            // A bare BUY signal cannot explain a later SELL execution.
            if("매도".equals(action)&&normalized.contains("매수 기준 충족"))
                continue;
            return normalized;
        }
        return TradeExplanation.shortReason("",action);
    }

    private String timeOf(JSONObject row,String forStrategy){
        String[] keys="gmo".equals(forStrategy)
            ?new String[]{"event_date","gmo_event_date","event_day","at","ts"}
            :"close".equals(forStrategy)
                ?new String[]{"buy_at","entry_at","entry_ts","buy_ts","first_buy_at","at","ts"}
                :new String[]{"ref_ts","ref_at","reference_ts","reference_at",
                    "base_candle_time","bar_at","selected_ref_at","signal_at",
                    "entry_at","buy_at","created_at","at","ts"};
        for(String key:keys){
            Object v=row.opt(key);
            if(v!=null&&v!=JSONObject.NULL&&!String.valueOf(v).isEmpty()){
                String formatted=formatKst(v);
                if(!"미기록".equals(formatted))return formatted;
            }
        }
        return "미기록";
    }

    private String refLabel(){return refLabel(strategy);}

    /** Four rapid taps on a symbol name; never triggers any trading action. */
    private void bindSymbolFourTap(TextView name,JSONObject row,String rowMarket){
        name.setOnClickListener(v->{
            String key=rowMarket+":"+row.optString("code",row.optString("market","")).trim();
            long now=android.os.SystemClock.uptimeMillis();
            if(key.equals(lastTappedSymbol)&&now-lastSymbolTapMs<=850L)
                consecutiveSymbolTaps++;
            else consecutiveSymbolTaps=1;
            lastTappedSymbol=key;lastSymbolTapMs=now;
            if(consecutiveSymbolTaps==4){
                consecutiveSymbolTaps=0;
                openSymbolInBrowser(row,rowMarket);
            }
        });
        PremiumUi.tapMotion(name);
    }

    private void openSymbolInBrowser(JSONObject row,String rowMarket){
        String raw=row.optString("code",row.optString("market","")).trim().toUpperCase(Locale.ROOT);
        final String url;
        if("stock".equals(rowMarket)){
            // Only Korean six-digit stock codes may be opened at Naver Finance.
            if(!raw.matches("[0-9]{6}"))return;
            url="https://finance.naver.com/item/main.naver?code="+raw;
        }else{
            String pair=raw;
            if(pair.startsWith("CRIX.UPBIT."))pair=pair.substring("CRIX.UPBIT.".length());
            if(!pair.matches("(KRW|BTC|USDT)-[A-Z0-9]{2,20}"))return;
            url="https://upbit.com/exchange?code=CRIX.UPBIT."+pair;
        }
        Intent browser=new Intent(Intent.ACTION_VIEW,Uri.parse(url));
        browser.addCategory(Intent.CATEGORY_BROWSABLE);
        browser.setPackage("com.android.chrome");
        try{startActivity(browser);}
        catch(android.content.ActivityNotFoundException chromeMissing){
            browser.setPackage(null);
            try{startActivity(browser);}catch(Exception e){
                new RuntimeCrashStore(this).add("symbol_browser_open",e);
            }
        }
    }

    private String nameOf(JSONObject r){
        String[] keys={"name","korean_name","symbol","ticker","code","market"};String x="";
        for(String k:keys){x=r.optString(k,"").trim();if(!x.isEmpty())break;}if(x.isEmpty())return "종목 미기록";
        if("coin".equals(market)&&!x.matches(".*[가-힣].*")){String s=x.replace("KRW-","").replace("BTC-","").replace("USDT-","").toUpperCase(Locale.ROOT);return coinName(s);}
        return x;
    }

    private String coinName(String s){
        switch(s){case "BTC":return "비트코인";case "ETH":return "이더리움";case "XRP":return "리플";case "SOL":return "솔라나";case "DOGE":return "도지코인";case "ADA":return "에이다";case "AVAX":return "아발란체";case "DOT":return "폴카닷";case "LINK":return "체인링크";case "TRX":return "트론";case "BCH":return "비트코인캐시";case "ETC":return "이더리움클래식";case "SUI":return "수이";default:return s;}
    }

    private String reasonOf(JSONObject r){return tradeReasonOf(r,"판단");}

    private String timeOf(JSONObject r){return timeOf(r,strategy);}

    private String formatKst(Object v){
        String text=TradeExplanation.kst(v,false);
        return text.startsWith("시각 형식")||text.startsWith("체결시각 미기록")
            ?"미기록":text;
    }

    private String number(double v){if(!finite(v))return "—";NumberFormat f=NumberFormat.getNumberInstance(Locale.KOREA);f.setMaximumFractionDigits(Math.abs(v)>=100?0:2);return f.format(v);}
    private String money(double v){return WonFormat.won(v);}
    private String pct(double v){return finite(v)?number(v)+"%":"—";}
    private void paintProfit(TextView t,double v){if(!finite(v))return;t.setTextColor(v>0?Color.rgb(62,224,181):v<0?Color.rgb(255,119,133):Color.rgb(240,247,255));}

    private void setDesired(String desired){
        io.execute(()->{try{new LocalHubClient(this).control(market,desired);}catch(Exception e){new RuntimeCrashStore(this).add("native_desired_"+market,e);}main.post(()->refresh(false));});
    }

    private void setLive(boolean enabled){
        if(!enabled){applyLive(false);return;}
        String name="stock".equals(market)?"주식":"코인";
        new AlertDialog.Builder(this).setTitle(name+" 실거래 자동주문 ON")
            .setMessage("최신 EXE 전략 신호가 실제 계좌 주문으로 전송됩니다. 부분체결·응답불명은 자동 정지합니다.")
            .setNegativeButton("취소",null).setPositiveButton("실거래 ON",(d,w)->applyLive(true)).show();
    }

    private void applyLive(boolean enabled){
        try{Intent i=new Intent(this,UnifiedTradingService.class);i.setAction(UnifiedTradingService.SET_LIVE);i.putExtra("market",market);i.putExtra("enabled",enabled);if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);main.postDelayed(()->refresh(false),1000);}catch(Throwable e){new RuntimeCrashStore(this).add("native_live_"+market,e);}
    }

    private String mserverErrorHelp(String raw){
        String e=(raw==null?"":raw).toLowerCase(Locale.ROOT);
        if(e.contains("unauthorized")||e.contains("401"))return "연결 토큰이 MSERVER의 토큰과 다름";
        if(e.contains("timeout")||e.contains("timed out"))return "암호화 원장 저장 응답시간 초과 (자동 재시도 대상)";
        if(e.contains("data_too_large")||e.contains("too_large")||e.contains("413"))return "원장 자료가 서버 허용 용량을 초과함";
        if(e.contains("server_storage_failed"))return "MSERVER 내부 저장 처리 실패 · 앱 오류이력 확인";
        if(e.contains("connection refused")||e.contains("connectexception"))return "MSERVER가 꺼져 있거나 8765 포트에 연결되지 않음";
        if(e.contains("revision_conflict"))return "원장 기록 버전 충돌 · 최신 버전 재확인 필요";
        if(e.contains("invalid_key"))return "원장 항목 이름이 서버에서 허용되지 않음";
        if(e.isEmpty())return "기존 실패 기록이 사라졌거나 원장 생성 대기 중";
        return "원장 게시 중 예외 발생 · 진단 코드 확인";
    }

    private void requestPcMigrationApply(){
        JSONObject live=copyCache().optJSONObject("live_orders");
        if(live!=null&&(live.optBoolean("stock",false)||live.optBoolean("coin",false))){
            new AlertDialog.Builder(this).setTitle("PC 원장 자동복원 차단")
                .setMessage("실거래 주문이 켜져 있습니다. 주문이 완전히 종료되고 실거래를 OFF로 전환한 뒤 PC 원장을 검증하십시오. 실제 체결이 남은 기기의 자동 덮어쓰기는 별도로 차단됩니다.")
                .setPositiveButton("확인",null).show();
            return;
        }
        new AlertDialog.Builder(this).setTitle("기존 PC 원장 재적용 · 검증")
            .setMessage("전략엔진을 안전하게 재시작하고 MSERVER의 PC 승계자료 또는 기기에 설치된 원장을 확인합니다. 매수·매도 주문 실행 기능을 새로 활성화하지 않습니다.")
            .setNegativeButton("취소",null)
            .setPositiveButton("원장 검증 시작",(d,w)->{
                try{
                    Intent intent=new Intent(this,UnifiedTradingService.class);
                    intent.setAction(UnifiedTradingService.APPLY_PC_MIGRATION);
                    if(Build.VERSION.SDK_INT>=26)startForegroundService(intent);else startService(intent);
                    main.postDelayed(()->refresh(false),1500);
                }catch(Throwable e){new RuntimeCrashStore(this).add("pc_migration_apply_ui",e);}
            }).show();
    }
    private void togglePcLiveSync(){
        boolean current=getSharedPreferences("artitrade_pc_live_sync_v1",
            MODE_PRIVATE).getBoolean("enabled",false);
        boolean enable=!current;
        try{
            Intent i=new Intent(this,UnifiedTradingService.class);
            i.setAction(UnifiedTradingService.SET_PC_SYNC);
            i.putExtra("enabled",enable);
            if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);
            new AlertDialog.Builder(this)
                .setTitle(enable?"PC와 실시간연동 시작":"PC와 실시간연동 중지")
                .setMessage(enable?
                    "현재 휴대폰 원장을 MSERVER로 전송하고, 이후 변경된 원장만 갱신합니다. "+
                    "PC에서는 읽기 전용 실시간 수신기를 실행해야 합니다. 매매·주문 설정에는 영향이 없습니다.":
                    "MSERVER 자동 전송을 중지합니다. 휴대폰 내부 원장 저장과 매매 기능은 계속 유지됩니다.")
                .setPositiveButton("확인",null).show();
            main.postDelayed(()->{
                refresh(false);
                if(drawerOverlay!=null){closeSettingsDrawer();main.postDelayed(this::openSettingsDrawer,220);}
            },650);
        }catch(Throwable e){
            new RuntimeCrashStore(this).add("pc_live_sync_toggle",e);
        }
    }

    private void requestLedgerRepublish(){
        try{
            Intent intent=new Intent(this,UnifiedTradingService.class);
            intent.setAction(UnifiedTradingService.RETRY_SYNC);
            if(Build.VERSION.SDK_INT>=26)startForegroundService(intent);else startService(intent);
            new AlertDialog.Builder(this).setTitle("원장 게시 재시도")
                .setMessage("연결 및 인증을 다시 확인한 뒤 기존 주식·코인 원장을 순서대로 게시합니다.\n실거래 ON/OFF 설정과 주문에는 영향을 주지 않습니다.")
                .setPositiveButton("확인",null).show();
            main.postDelayed(()->refresh(false),2300);
        }catch(Throwable e){
            new RuntimeCrashStore(this).add("mserver_retry_ui",e);
            new AlertDialog.Builder(this).setTitle("MSERVER 재시도 실패")
                .setMessage(String.valueOf(e.getMessage())).setPositiveButton("확인",null).show();
        }
    }

    private void pair(){
        EditText input=new EditText(this);
        input.setSingleLine(true);
        input.setTextSize(14);
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        input.setHint("MSERVER 64자리 연결 토큰");
        new AlertDialog.Builder(this)
            .setTitle("MSERVER(모바일 서버) 연결")
            .setMessage("MSERVER 앱의 '연결 토큰 보기'에 표시된 64자리 값을 입력하십시오. 저장 직후 실제 인증까지 확인합니다.")
            .setView(input).setNegativeButton("취소",null)
            .setPositiveButton("저장 및 인증",(d,w)->{
                String raw=input.getText().toString().trim();
                try{
                    LocalHubClient client=new LocalHubClient(this);
                    client.storePairingToken(raw);
                    io.execute(()->{
                        try{
                            JSONObject status=client.status();
                            boolean ok=status.optBoolean("ok",false);
                            String error=status.optString("error","");
                            int http=status.optInt("http_status",200);
                            main.post(()->{
                                refresh(false);
                                if(ok){
                                    // Pairing does not imply consent to continuous or one-shot transfer.
                                    new AlertDialog.Builder(this).setTitle("MSERVER 인증 정상")
                                        .setMessage("토큰 저장 및 인증이 완료되었습니다.\n이 값은 기기 로컬 보안저장소에 유지됩니다.")
                                        .setPositiveButton("확인",null).show();
                                }else{
                                    String msg=("unauthorized".equals(error)||http==401)
                                        ?"토큰 불일치입니다. 현재 실행 중인 MSERVER의 새 64자리 토큰을 다시 확인하십시오."
                                        :"MSERVER 응답 오류: "+(error.isEmpty()?"HTTP "+http:error);
                                    new AlertDialog.Builder(this).setTitle("MSERVER 인증 실패")
                                        .setMessage(msg).setPositiveButton("확인",null).show();
                                }
                            });
                        }catch(Exception e){
                            main.post(()->new AlertDialog.Builder(this).setTitle("MSERVER 연결 실패")
                                .setMessage("MSERVER가 실행 중인지 확인하십시오.\n"+e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()))
                                .setPositiveButton("확인",null).show());
                        }
                    });
                }catch(Exception e){
                    new AlertDialog.Builder(this).setTitle("토큰 형식 확인")
                        .setMessage("64자리 16진수 연결 토큰을 확인하십시오.")
                        .setPositiveButton("확인",null).show();
                }
            }).show();
    }

    private void apiSettings(){
        BrokerSecretStore store=new BrokerSecretStore(this);JSONObject saved=store.status();
        boolean stockMarket="stock".equals(market);
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(dp(12),dp(6),dp(12),dp(6));
        EditText first=new EditText(this),second=new EditText(this);
        if(stockMarket){
            first.setHint(saved.optBoolean("kiwoom_saved",false)?"App Key · "+saved.optString("kiwoom_key_masked","저장됨"):"키움 App Key");
            second.setHint(saved.optBoolean("kiwoom_saved",false)?"App Secret · 저장됨":"키움 App Secret");
        }else{
            first.setHint(saved.optBoolean("upbit_saved",false)?"Access Key · "+saved.optString("upbit_access_masked","저장됨"):"업비트 Access Key");
            second.setHint(saved.optBoolean("upbit_saved",false)?"Secret Key · 저장됨":"업비트 Secret Key");
        }
        for(EditText v:new EditText[]{first,second}){
            v.setSingleLine(true);v.setTextSize(14);v.setTextColor(Color.WHITE);v.setHintTextColor(Color.rgb(130,140,163));
            v.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
            form.addView(v,new LinearLayout.LayoutParams(-1,dp(54)));
        }
        String name=stockMarket?"키움":"업비트";
        new AlertDialog.Builder(this).setTitle(name+" 직접 API 설정")
            .setMessage("빈 칸은 기존 값을 유지합니다. 저장 직후 실제 인증 요청으로 검증합니다.")
            .setView(form).setNegativeButton("취소",null)
            .setNeutralButton(name+" 키 삭제",(d,w)->{
                if(stockMarket)store.saveKiwoom("","");else store.saveUpbit("","");
                getSharedPreferences("broker_probe_v1",MODE_PRIVATE).edit().clear().apply();restartFeeds();refresh(false);
            })
            .setPositiveButton("저장",(d,w)->{
                String x=first.getText().toString().trim(),y=second.getText().toString().trim();
                if(x.isEmpty()!=y.isEmpty()){
                    new AlertDialog.Builder(this).setTitle(name+" 키 확인").setMessage("두 키를 모두 입력하거나 둘 다 비워 기존 값을 유지하십시오.").setPositiveButton("확인",null).show();return;
                }
                if(!x.isEmpty()){
                    if(stockMarket)store.saveKiwoom(x,y);else store.saveUpbit(x,y);
                }
                runBrokerProbe(true);
            }).show();
    }

    private void autoProbeBroker(){
        if(brokerProbeIssued)return;
        BrokerSecretStore store=new BrokerSecretStore(this);
        if(!store.hasKiwoom()&&!store.hasUpbit())return;
        long last=getSharedPreferences("broker_probe_v1",MODE_PRIVATE).getLong("checked_at",0L);
        if(System.currentTimeMillis()-last<6L*60L*60L*1000L)return;
        brokerProbeIssued=true;
        io.execute(()->{
            JSONObject result=BrokerApiProbe.all(store);
            getSharedPreferences("broker_probe_v1",MODE_PRIVATE).edit().putString("result",result.toString()).putLong("checked_at",System.currentTimeMillis()).apply();
            main.post(()->refresh(false));
        });
    }

    private void runBrokerProbe(boolean restartAfter){
        final String selected=market;
        io.execute(()->{
            BrokerSecretStore store=new BrokerSecretStore(this);
            JSONObject prior;
            try{prior=new JSONObject(getSharedPreferences("broker_probe_v1",MODE_PRIVATE).getString("result","{}"));}catch(Exception e){prior=new JSONObject();}
            JSONObject one="stock".equals(selected)?BrokerApiProbe.kiwoom(store):BrokerApiProbe.upbit(store);
            try{prior.put("stock".equals(selected)?"kiwoom":"upbit",one).put("checked_at",System.currentTimeMillis());}catch(Exception ignored){}
            final JSONObject result=prior;
            getSharedPreferences("broker_probe_v1",MODE_PRIVATE).edit().putString("result",result.toString()).putLong("checked_at",System.currentTimeMillis()).apply();
            if(restartAfter)main.post(this::restartFeeds);
            main.post(()->{
                refresh(false);
                String broker="stock".equals(selected)?"키움":"업비트";
                String msg=one.optBoolean("ok",false)?"인증 정상":one.optString("message","인증 실패");
                if(one.optBoolean("ip_hint",false))msg+="\n허용 IP 불일치 응답";
                String code=one.optString("error_name","");
                if(!code.isEmpty())msg+="\n오류코드: "+code;
                new AlertDialog.Builder(this).setTitle(broker+" API 인증 검사").setMessage(msg).setPositiveButton("확인",null).show();
                if(drawerOverlay!=null){closeSettingsDrawer();main.postDelayed(this::openSettingsDrawer,220);}
            });
        });
    }

    private void restartFeeds(){try{Intent i=new Intent(this,UnifiedTradingService.class);i.setAction(UnifiedTradingService.RESTART_FEEDS);if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);}catch(Exception e){new RuntimeCrashStore(this).add("native_restart_feeds",e);}}

    private void startRuntimeLater(){
        if(runtimeStartIssued)return;runtimeStartIssued=true;
        main.postDelayed(()->{try{Intent i=new Intent(this,UnifiedTradingService.class);i.setAction(UnifiedTradingService.START);if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);}catch(Throwable e){new RuntimeCrashStore(this).add("native_runtime_start",e);}},140);
    }

    private void showFatal(Throwable e){
        if(root==null){root=new FrameLayout(this);root.setBackgroundColor(PremiumUi.BG);try{setContentView(root);}catch(Throwable ignored){}}
        root.removeAllViews();LinearLayout p=new LinearLayout(this);p.setOrientation(LinearLayout.VERTICAL);p.setGravity(Gravity.CENTER);p.setPadding(dp(28),dp(20),dp(28),dp(20));p.setBackgroundColor(PremiumUi.BG);
        TextView h=text("ARTITRADE 네이티브 안전모드",22);h.setTypeface(null,Typeface.BOLD);p.addView(h);TextView d=text(e==null?"알 수 없는 오류":e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()),11);d.setTextColor(Color.rgb(255,145,155));d.setPadding(0,dp(10),0,dp(10));p.addView(d);p.addView(button("화면 다시 구성",true,()->{try{showShell();refresh(false);}catch(Throwable x){}}),new LinearLayout.LayoutParams(dp(240),dp(44)));root.addView(p,new FrameLayout.LayoutParams(-1,-1));
    }

    @Override protected void onResume(){super.onResume();main.removeCallbacks(periodic);main.postDelayed(periodic,REFRESH_MS);if(drawerOverlay!=null){main.removeCallbacks(drawerProgressTicker);main.post(drawerProgressTicker);}}
    @Override protected void onPause(){persistNavigation();main.removeCallbacks(periodic);main.removeCallbacks(drawerProgressTicker);super.onPause();}
    @Override protected void onDestroy(){main.removeCallbacks(periodic);main.removeCallbacks(drawerProgressTicker);io.shutdownNow();super.onDestroy();}
}
