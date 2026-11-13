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
    private FrameLayout root;
    private LinearLayout content;
    private LinearLayout strategyBar;
    private TextView statusText;
    private TextView clockText;
    private volatile boolean refreshRunning=false;
    private volatile boolean runtimeStartIssued=false;
    private volatile boolean brokerProbeIssued=false;
    private String market="stock";
    private String strategy="at";
    private String page="home";
    private String historyFilterStock="current",historyFilterCoin="current";
    // History is never truncated; only the visible Android Views are paged.
    private String historyContext="";
    private int historyOffset=0;
    private float swipeX=Float.NaN;
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
    private final Map<String,EditText[]> operationFields=new HashMap<>();
    // Unsaved drafts are isolated from the live strategy settings. Only Save applies them.
    private final Map<String,String> operationDraftValues=new HashMap<>();
    private boolean operationDraftLoaded=false;
    private static final int REQUEST_EXPORT_RESEARCH=7711;
    private byte[] pendingResearchExport;
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
            getWindow().setStatusBarColor(Color.rgb(7,16,28));
            getWindow().setNavigationBarColor(Color.rgb(7,16,28));
            root=new FrameLayout(this);
            root.setBackgroundColor(Color.rgb(7,16,28));
            setContentView(root);
            showBoot();
            // Guarantee a native first frame before any server/Python/broker work.
            main.postDelayed(()->{
                try{
                    showShell();
                    refresh(true);
                    startRuntimeLater();
                    main.postDelayed(this::autoProbeBroker,900);
                }catch(Throwable e){
                    new RuntimeCrashStore(this).add("native_shell",e);
                    showFatal(e);
                }
            },350);
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
        // v2 keys deliberately reset the legacy v0.7.17 global 'all' default.
        historyFilterStock=prefs.getString("stock_filter_v2","current");
        historyFilterCoin=prefs.getString("coin_filter_v2","current");
        if(!"stock".equals(market)&&!"coin".equals(market))market="stock";
        boolean validStrategy=false;
        for(String[] item:strategyList(market))if(item[0].equals(strategy))validStrategy=true;
        if(!validStrategy)strategy="stock".equals(market)?"at":"general";
        java.util.HashSet<String> validPages=new java.util.HashSet<>(
            java.util.Arrays.asList("home","positions","pending","executions",
                "research","operation","scores","errors","policyHistory","more"));
        if(!validPages.contains(page))page="home";
    }

    private void persistNavigation(){
        getSharedPreferences("artitrade_ui_navigation_v1",MODE_PRIVATE).edit()
            .putString("market",market).putString("strategy",strategy).putString("page",page)
            .putString("stock_filter_v2",historyFilterStock)
            .putString("coin_filter_v2",historyFilterCoin)
            .apply();
    }

    @Override protected void onSaveInstanceState(Bundle state){
        state.putString("market",market);
        state.putString("strategy",strategy);
        state.putString("page",page);
        persistNavigation();
        super.onSaveInstanceState(state);
    }

    private int dp(float v){return (int)(v*getResources().getDisplayMetrics().density+.5f);}

    private TextView text(String value,int sp){
        TextView t=new TextView(this);
        t.setText(value);
        t.setTextSize(sp);
        t.setTextColor(colorByStatus(value,Color.rgb(240,247,255)));
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

    private Button button(String label,boolean active,Runnable action){
        Button b=new Button(this);
        b.setAllCaps(false);
        b.setText(label);
        b.setTextSize(11);
        b.setTypeface(null,Typeface.BOLD);
        b.setTextColor(active?Color.WHITE:Color.rgb(203,210,228));
        b.setBackground(bg(active?Color.rgb(105,91,232):Color.rgb(22,25,36),
            active?Color.rgb(129,116,248):Color.rgb(48,52,69),11));
        b.setPadding(dp(10),0,dp(10),0);
        b.setMinHeight(0);b.setMinimumHeight(0);
        b.setOnClickListener(v->action.run());
        return b;
    }

    private LinearLayout card(){
        LinearLayout c=new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(10),dp(9),dp(10),dp(9));
        c.setBackground(bg(Color.rgb(18,21,31),Color.rgb(42,48,66),16));
        return c;
    }

    private TextView segmentItem(String label,boolean active,Runnable action){
        TextView t=text(label,13);t.setGravity(Gravity.CENTER);t.setTypeface(null,Typeface.BOLD);
        t.setTextColor(active?Color.WHITE:Color.rgb(137,146,166));
        t.setBackground(bg(active?Color.rgb(92,78,220):Color.TRANSPARENT,0,14));
        t.setOnClickListener(v->action.run());return t;
    }

    private TextView strategyTab(String label,boolean active,Runnable action){
        TextView t=text(label,14);t.setGravity(Gravity.CENTER);t.setTypeface(null,active?Typeface.BOLD:Typeface.NORMAL);
        t.setTextColor(active?Color.rgb(194,185,255):Color.rgb(133,142,163));
        if(active)t.setBackground(bg(Color.rgb(27,24,46),Color.rgb(92,78,220),12));
        else t.setBackgroundColor(Color.TRANSPARENT);
        t.setOnClickListener(v->action.run());return t;
    }

    private TextView iconAction(String glyph,Runnable action){
        TextView t=text(glyph,20);t.setGravity(Gravity.CENTER);t.setTextColor(Color.rgb(211,217,232));
        t.setBackground(bg(Color.rgb(21,24,34),Color.rgb(48,52,68),19));t.setOnClickListener(v->action.run());return t;
    }

    private LinearLayout bottomNavItem(String key,String icon,String label){
        boolean active=key.equals(page);
        int accent="stock".equals(market)?Color.rgb(77,124,254):Color.rgb(0,196,164);
        LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setGravity(Gravity.CENTER);
        box.setPadding(dp(4),dp(3),dp(4),dp(3));
        if(active)box.setBackground(bg(Color.argb(46,Color.red(accent),Color.green(accent),Color.blue(accent)),accent,13));
        TextView i=text(icon,19);i.setGravity(Gravity.CENTER);i.setTypeface(null,active?Typeface.BOLD:Typeface.NORMAL);
        i.setTextColor(active?accent:Color.rgb(106,115,137));box.addView(i,new LinearLayout.LayoutParams(-1,dp(25)));
        TextView l=text(label,11);l.setGravity(Gravity.CENTER);l.setTypeface(null,active?Typeface.BOLD:Typeface.NORMAL);
        l.setTextColor(active?Color.WHITE:Color.rgb(118,127,148));box.addView(l,new LinearLayout.LayoutParams(-1,dp(18)));
        box.setOnClickListener(v->{page=key;persistNavigation();showShell();});return box;
    }

    private View divider(){
        View v=new View(this);v.setBackgroundColor(Color.rgb(35,39,53));return v;
    }

    private void showBoot(){
        root.removeAllViews();
        LinearLayout p=new LinearLayout(this);
        p.setOrientation(LinearLayout.VERTICAL);p.setGravity(Gravity.CENTER);
        p.setPadding(dp(32),dp(24),dp(32),dp(24));
        p.setBackgroundColor(Color.rgb(7,16,28));
        TextView brand=text("ARTITRADE",28);brand.setTypeface(null,Typeface.BOLD);brand.setLetterSpacing(.12f);p.addView(brand);
        TextView sub=text("S21+ PHONE MAIN · NATIVE SAFEBOOT",12);sub.setTextColor(Color.rgb(139,164,194));sub.setPadding(0,dp(8),0,dp(18));p.addView(sub);
        ProgressBar bar=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);bar.setIndeterminate(true);
        p.addView(bar,new LinearLayout.LayoutParams(dp(320),dp(10)));
        TextView note=text("화면을 먼저 구성한 뒤 API · 전략엔진을 지연 시작합니다.",11);note.setTextColor(Color.rgb(116,143,178));note.setPadding(0,dp(14),0,0);p.addView(note);
        root.addView(p,new FrameLayout.LayoutParams(-1,-1));
    }

    private String marketVersionLabel(){
        return "stock".equals(market)?"STOCK CLOSE PNL FIX2":"COIN FIX38 SR1 POLICY";
    }

    private void showShell(){
        root.removeAllViews();
        drawerOverlay=null;
        stopDrawerProgressTicker();
        LinearLayout shell=new LinearLayout(this);shell.setOrientation(LinearLayout.VERTICAL);shell.setBackgroundColor(Color.rgb(9,11,17));
        root.addView(shell,new FrameLayout.LayoutParams(-1,-1));

        LinearLayout top=new LinearLayout(this);top.setOrientation(LinearLayout.HORIZONTAL);top.setGravity(Gravity.CENTER_VERTICAL);top.setPadding(dp(14),dp(7),dp(10),dp(5));
        LinearLayout brandBox=new LinearLayout(this);brandBox.setOrientation(LinearLayout.VERTICAL);brandBox.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout brandLine=new LinearLayout(this);brandLine.setOrientation(LinearLayout.HORIZONTAL);brandLine.setGravity(Gravity.CENTER_VERTICAL);
        TextView brand=text("ARTITRADE",18);brand.setTypeface(null,Typeface.BOLD);brand.setLetterSpacing(.09f);brandLine.addView(brand);
        TextView ver=text("  ·  "+marketVersionLabel(),10);ver.setTypeface(null,Typeface.BOLD);ver.setTextColor("stock".equals(market)?Color.rgb(96,145,255):Color.rgb(0,205,171));
        brandLine.addView(ver,new LinearLayout.LayoutParams(-2,dp(25)));brandBox.addView(brandLine,new LinearLayout.LayoutParams(-1,dp(25)));
        statusText=text("데이터 확인 중",10);statusText.setTextColor(Color.rgb(138,147,167));brandBox.addView(statusText,new LinearLayout.LayoutParams(-1,dp(17)));
        top.addView(brandBox,new LinearLayout.LayoutParams(0,dp(43),1));

        LinearLayout marketSwitch=new LinearLayout(this);marketSwitch.setOrientation(LinearLayout.HORIZONTAL);marketSwitch.setPadding(dp(3),dp(3),dp(3),dp(3));
        marketSwitch.setBackground(bg(Color.rgb(20,23,33),Color.rgb(42,47,63),18));
        // Keep the bottom navigation context across both markets (e.g. positions -> positions).
        TextView stock=segmentItem("주식","stock".equals(market),()->{if(!"stock".equals(market)){market="stock";strategy="at";resetHistoryFilter();}persistNavigation();showShell();});
        TextView coin=segmentItem("코인","coin".equals(market),()->{if(!"coin".equals(market)){market="coin";strategy="general";resetHistoryFilter();}persistNavigation();showShell();});
        marketSwitch.addView(stock,new LinearLayout.LayoutParams(dp(58),dp(30)));
        marketSwitch.addView(coin,new LinearLayout.LayoutParams(dp(58),dp(30)));
        top.addView(marketSwitch,new LinearLayout.LayoutParams(dp(122),dp(36)));

        clockText=text("",10);clockText.setTextColor(Color.rgb(116,126,148));clockText.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams tp=new LinearLayout.LayoutParams(dp(86),dp(34));tp.setMargins(dp(7),0,dp(4),0);top.addView(clockText,tp);
        top.addView(iconAction("☰",this::openSettingsDrawer),new LinearLayout.LayoutParams(dp(38),dp(38)));
        shell.addView(top,new LinearLayout.LayoutParams(-1,dp(56)));
        shell.addView(divider(),new LinearLayout.LayoutParams(-1,dp(1)));

        HorizontalScrollView sh=new HorizontalScrollView(this);sh.setHorizontalScrollBarEnabled(false);sh.setFillViewport(true);
        strategyBar=new LinearLayout(this);strategyBar.setOrientation(LinearLayout.HORIZONTAL);strategyBar.setGravity(Gravity.CENTER_VERTICAL);strategyBar.setPadding(dp(13),dp(5),dp(13),dp(5));
        sh.addView(strategyBar,new HorizontalScrollView.LayoutParams(-1,-1));shell.addView(sh,new LinearLayout.LayoutParams(-1,dp(44)));
        buildStrategyBar();

        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.setBackgroundColor(Color.rgb(9,11,17));
        content=new LinearLayout(this);content.setOrientation(LinearLayout.VERTICAL);content.setPadding(dp(6),dp(8),dp(6),dp(18));
        scroll.addView(content,new ScrollView.LayoutParams(-1,-2));
        scroll.setOnTouchListener((v,e)->{
            if("operation".equals(page)||"errors".equals(page))return false;
            if(e.getAction()==MotionEvent.ACTION_DOWN)swipeX=e.getX();
            else if(e.getAction()==MotionEvent.ACTION_UP&&!Float.isNaN(swipeX)){
                float dx=e.getX()-swipeX;swipeX=Float.NaN;
                if(Math.abs(dx)>dp(78)){switchStrategy(dx<0?1:-1);return true;}
            }
            return false;
        });
        shell.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));

        shell.addView(divider(),new LinearLayout.LayoutParams(-1,dp(1)));
        LinearLayout bottom=new LinearLayout(this);bottom.setOrientation(LinearLayout.HORIZONTAL);bottom.setGravity(Gravity.CENTER);bottom.setPadding(dp(6),dp(3),dp(6),dp(3));bottom.setBackgroundColor(Color.rgb(13,15,22));
        bottom.addView(bottomNavItem("home","⌂","대시보드"),new LinearLayout.LayoutParams(0,dp(48),1));
        bottom.addView(bottomNavItem("positions","◆","보유종목"),new LinearLayout.LayoutParams(0,dp(48),1));
        bottom.addView(bottomNavItem("pending","◌","매수대기"),new LinearLayout.LayoutParams(0,dp(48),1));
        bottom.addView(bottomNavItem("executions","↔","체결이력"),new LinearLayout.LayoutParams(0,dp(48),1));
        bottom.addView(bottomNavItem("research","◎","연구자료"),new LinearLayout.LayoutParams(0,dp(48),1));
        shell.addView(bottom,new LinearLayout.LayoutParams(-1,dp(54)));

        updateClock();
        render();
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
        String stage=state==null?"대기":state.optString("stage","대기");
        String current=state==null?"":state.optString("current",state.optString("detail",""));
        String eta=etaText(state);
        views.detail.setText(stage+(current.isEmpty()?"":" · "+current)+(eta.isEmpty()?"":" · "+eta));
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

    private void openSettingsDrawer(){
        if(drawerOverlay!=null)return;
        FrameLayout overlay=new FrameLayout(this);overlay.setBackgroundColor(Color.argb(125,0,0,0));
        View dim=new View(this);dim.setBackgroundColor(Color.TRANSPARENT);dim.setOnClickListener(v->closeSettingsDrawer());
        overlay.addView(dim,new FrameLayout.LayoutParams(-1,-1));

        int width=Math.min(dp(380),(int)(getResources().getDisplayMetrics().widthPixels*.88f));
        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.setBackgroundColor(Color.rgb(10,22,38));
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
        policyRow.setOnClickListener(v->{page="policyHistory";closeSettingsDrawer();main.postDelayed(this::showShell,190);});
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

        TextView srvTitle=text("MSERVER · 기준원장 동기화",11);srvTitle.setTypeface(null,Typeface.BOLD);srvTitle.setTextColor(Color.rgb(145,170,200));srvTitle.setPadding(0,dp(16),0,dp(7));panel.addView(srvTitle);
        LinearLayout tools=card();
        JSONObject snapshot=copyCache();
        JSONObject hubPub=snapshot.optJSONObject("hub_publish");
        JSONObject mAuth=snapshot.optJSONObject("mserver_auth");
        JSONObject syncProgress=snapshot.optJSONObject("mserver_sync");
        JSONObject importProgress=effectiveImportProgress(snapshot.optJSONObject("pc_import_progress"),snapshot);
        JSONObject migrationServer=snapshot.optJSONObject("mserver_migration");
        int failed=hubPub==null?0:hubPub.optInt("failed",0);int okPub=hubPub==null?0:hubPub.optInt("ok",0);int preserved=hubPub==null?0:hubPub.optInt("preserved",0);
        boolean authOk=mAuth!=null&&mAuth.optBoolean("ok",false);
        String authError=mAuth==null?"":mAuth.optString("error","");
        int authHttp=mAuth==null?0:mAuth.optInt("http_status",0);

        String mserverMsg;
        if(!authOk&&("unauthorized".equals(authError)||authHttp==401))mserverMsg="MSERVER 인증 실패 · 연결 토큰 불일치";
        else if(!authOk&&authError.startsWith("UNREACHABLE:"))mserverMsg="MSERVER 연결 실패 · 모바일 서버 실행상태 확인";
        else if(syncProgress!=null&&syncProgress.optBoolean("complete",false))mserverMsg="폰 기준원장 → MSERVER 게시 완료";
        else if(failed>0)mserverMsg="MSERVER 원장 게시 실패 · "+failed+"건 (아래 원인 확인)";
        else mserverMsg="MSERVER 원장 게시 진행/대기";
        TextView mserverState=text(mserverMsg,12);
        boolean bad=!authOk||failed>0;
        mserverState.setTextColor(bad?Color.rgb(255,119,133):syncProgress!=null&&syncProgress.optBoolean("complete",false)?Color.rgb(86,214,170):Color.rgb(255,194,91));
        mserverState.setPadding(dp(2),0,dp(2),dp(5));tools.addView(mserverState);
        if(!authOk&&!authError.isEmpty()){
            TextView ae=text("인증/연결: "+authError+(authHttp>0?" · HTTP "+authHttp:""),10);ae.setTextColor(Color.rgb(210,151,161));ae.setPadding(dp(2),0,dp(2),dp(5));tools.addView(ae);
        }
        drawerPublishProgress=addProgressBlock(tools,"① 폰 기준원장 → MSERVER 게시",syncProgress,Color.rgb(0,196,164));
        if(syncProgress!=null&&syncProgress.optInt("total",0)>0){
            int processed=syncProgress.optInt("processed",syncProgress.optInt("done",0));
            int successful=syncProgress.optInt("successful",Math.max(0,processed-syncProgress.optInt("failed",0)));
            int all=syncProgress.optInt("total",0);
            TextView audit=text("전체 "+all+"건 · 처리 "+processed+"건 · 성공 "+successful+
                "건 · 실패 "+syncProgress.optInt("failed",0)+"건",11);
            audit.setTextColor(syncProgress.optInt("failed",0)>0?Color.rgb(255,153,164):Color.rgb(168,186,209));
            audit.setPadding(dp(2),0,0,dp(8));tools.addView(audit);
        }
        if(failed>0&&hubPub!=null){
            String detail=hubPub.optString("last_error","");
            String where=hubPub.optString("last_market","")+"/"+hubPub.optString("last_strategy","")+"/"+hubPub.optString("last_bucket","");
            String explanation=mserverErrorHelp(detail);
            TextView he=text("게시 실패 위치: "+(where.equals("//")?"서버 인증 단계":where)+"\n"
                +"원인: "+explanation+"\n"+"진단 코드: "+detail,12);
            he.setTextColor(Color.rgb(255,163,172));he.setPadding(dp(2),dp(5),dp(2),dp(9));
            he.setMaxLines(6);tools.addView(he);
        }

        UiSnapshot readyState=uiSnapshot;
        TextView preloadNote=text("화면 사전로딩: "+
            (readyState.reused?"기존 결과 재사용":"전략별 집계")+
            " · "+readyState.preloadMs+"ms · 화면용 기록 "+readyState.preloadRows+
            "건 · 전략 "+readyState.totals.size()+"개",10);
        preloadNote.setTextColor(readyState.preloadRows>0?Color.rgb(62,224,181):
            Color.rgb(255,194,91));
        preloadNote.setPadding(dp(2),dp(6),dp(2),dp(6));
        tools.addView(preloadNote);
        TextView preloadExplain=text("※ 이 시간은 화면 표시자료 준비시간이며 PC SQLite 전체 검증시간이 아님",9);
        preloadExplain.setTextColor(Color.rgb(141,159,183));tools.addView(preloadExplain);

        JSONObject migState=snapshot.optJSONObject("pc_ledger_migration");
        drawerImportProgress=addProgressBlock(tools,"② 기존 PC EXE 원장 → S21+ 승계",importProgress,Color.rgb(77,124,254));

        if(migState!=null&&migState.length()>0){
            String ms=migState.optString("status","");
            String mt="IMPORTED".equals(ms)||"ALREADY_IMPORTED".equals(ms)?"기존 PC EXE 원장 복원·엔진 반영 확인":
                "FILES_INSTALLED".equals(ms)||"LOCAL_FILES_PRESENT".equals(ms)?"PC DB 설치됨 · 엔진 반영 확인 중":
                "PHONE_HAS_REAL_FILLS".equals(ms)?"실제 브로커 체결 존재 · 자동 원장교체 차단":
                "RUNTIME_VERIFY_FAILED".equals(ms)?"PC DB 존재하나 전략엔진 반영 실패":
                "IMPORT_FAILED".equals(ms)?"기존 PC EXE 원장 승계 실패":
                "MSERVER_UNREACHABLE".equals(ms)?"MSERVER 연결 불가 · 원장 승계 대기":
                "PHONE_LEDGER_VERIFY_FAILED".equals(ms)?"기존 폰 원장 보존 · 재검증 실패":
                "PC_IMPORT_CHECKPOINT_INCOMPLETE".equals(ms)?"설치된 원장 검증정보 불일치 · 덮어쓰기 금지":
                "NO_PC_ARCHIVE".equals(ms)?"기존 PC EXE 원장 승계자료 대기":"기존 PC EXE 원장: "+ms;
            TextView mv=text(mt,11);
            mv.setTextColor(("IMPORTED".equals(ms)||"ALREADY_IMPORTED".equals(ms))?Color.rgb(86,214,170):
                ("PHONE_HAS_REAL_FILLS".equals(ms)||"IMPORT_FAILED".equals(ms))?Color.rgb(255,119,133):Color.rgb(147,160,185));
            mv.setPadding(dp(2),dp(3),dp(2),dp(7));tools.addView(mv);
            JSONObject verification=migState.optJSONObject("verification");
            if(verification!=null){
                TextView counts=text("원본 기록 "+verification.optLong("source_rows",0)+"건 · 검증된 DB/파일 "+
                    verification.optInt("files_verified",0)+"개 · 엔진 반영 "+
                    verification.optLong("runtime_rows",0)+"건",11);
                counts.setTextColor(Color.rgb(162,181,209));counts.setPadding(dp(2),0,0,dp(5));tools.addView(counts);
                JSONObject strategyCounts=verification.optJSONObject("by_strategy");
                if(strategyCounts!=null){
                    for(String k:new String[]{"stock/at","stock/ma","stock/close","stock/gmo","coin/general","coin/ma"}){
                        JSONObject row=strategyCounts.optJSONObject(k);if(row==null)continue;
                        TextView detail=text(k+" · PC "+row.optLong("pc_rows",0)+"건 / 엔진 "+
                            row.optLong("runtime_rows",0)+"건",10);
                        detail.setTextColor(row.optLong("pc_rows",0)>0&&row.optLong("runtime_rows",0)==0
                            ?Color.rgb(255,143,158):Color.rgb(160,183,210));
                        tools.addView(detail);
                    }
                }
            }
            String importError=migState.optString("message","");
            if(migState.optBoolean("close_path_conflict_preserved",false)){
                TextView warn=text("과거/현재 종가베팅 원장이 모두 존재 · 원본을 보존하고 자동 덮어쓰기 차단",10);
                warn.setTextColor(Color.rgb(255,184,119));tools.addView(warn);
            }
            JSONObject dbAudit=migState.optJSONObject("python_db_audit");
            if(dbAudit==null&&verification!=null)dbAudit=verification.optJSONObject("python_db_audit");
            if(dbAudit!=null&&!dbAudit.optBoolean("ok",true)){
                TextView check=text("SQLite 전략검증: "+dbAudit.optString("message","원본과 실제 DB 비교 실패"),10);
                check.setTextColor(Color.rgb(255,153,164));check.setMaxLines(3);tools.addView(check);
            }
            if(!importError.isEmpty()){
                TextView issue=text("복원 검증: "+importError,10);
                issue.setTextColor(Color.rgb(255,153,164));issue.setPadding(dp(2),0,0,dp(8));tools.addView(issue);
            }
        }
        if("stock".equals(market)){
            JSONObject gmo=ledger(snapshot,"stock","gmo");
            JSONObject gmoDiagnostic=gmo.optJSONObject("load_diagnostic");
            long pcGmoRows=0,engineGmoRows=0;
            JSONObject mv=migState==null?null:migState.optJSONObject("verification");
            JSONObject byStrategy=mv==null?null:mv.optJSONObject("by_strategy");
            JSONObject gmoVerify=byStrategy==null?null:byStrategy.optJSONObject("stock/gmo");
            if(gmoVerify!=null){
                pcGmoRows=gmoVerify.optLong("pc_rows",0);
                engineGmoRows=gmoVerify.optLong("runtime_rows",0);
            }
            Stats localGmo=uiSnapshot.totals.get("stock/gmo");
            int shown=localGmo==null?0:localGmo.positions.length()+
                localGmo.executions.length()+localGmo.research.length();
            String phase=gmoDiagnostic==null?"":gmoDiagnostic.optString("stage","");
            String gmoMessage="PC 수급매매 승계 확인: 원본 "+pcGmoRows+
                "건 · 검증된 엔진 "+engineGmoRows+"건 · 화면 "+shown+"건"+
                (phase.isEmpty()?"":" · "+phase);
            TextView gm=text(gmoMessage,11);
            gm.setTextColor(pcGmoRows>0&&shown==0?Color.rgb(255,119,133):
                shown>0?Color.rgb(62,224,181):Color.rgb(255,194,91));
            gm.setPadding(dp(2),dp(7),dp(2),dp(7));
            tools.addView(gm);
        }
        TextView explain=text("※ 'MSERVER 게시 완료'와 'PC 과거원장 승계 완료'는 서로 다른 상태임.",10);
        explain.setTextColor(Color.rgb(255,194,91));explain.setPadding(dp(2),0,dp(2),dp(7));tools.addView(explain);
        JSONObject pairState=copyCache().optJSONObject("hub_pairing");
        if(pairState!=null){
            String ps=pairState.optBoolean("saved",false)?"연결 토큰 기기 저장됨 · "+pairState.optString("masked",""):"연결 토큰 미저장";
            TextView pst=text(ps,11);pst.setTextColor(pairState.optBoolean("saved",false)?Color.rgb(86,214,170):Color.rgb(255,176,92));pst.setPadding(dp(2),0,dp(2),dp(8));tools.addView(pst);
        }
        tools.addView(button("MSERVER 연결 토큰 등록",false,this::pair),new LinearLayout.LayoutParams(-1,dp(40)));
        LinearLayout.LayoutParams importLp=new LinearLayout.LayoutParams(-1,dp(43));importLp.setMargins(0,dp(7),0,0);
        tools.addView(button("PC 원장 적용 · 실제 데이터 재검증",true,this::requestPcMigrationApply),importLp);
        LinearLayout.LayoutParams retryLp=new LinearLayout.LayoutParams(-1,dp(43));retryLp.setMargins(0,dp(7),0,0);
        tools.addView(button("원장 다시 게시 · 연결 재확인",true,this::requestLedgerRepublish),retryLp);
        LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,dp(40));bp.setMargins(0,dp(7),0,0);tools.addView(button("절전 예외 설정",false,()->{try{startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}catch(Exception ignored){}}),bp);
        LinearLayout.LayoutParams rp=new LinearLayout.LayoutParams(-1,dp(40));rp.setMargins(0,dp(7),0,0);tools.addView(button("실시간 엔진 재연결",false,this::restartFeeds),rp);panel.addView(tools);

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
                if(!meaningfulLocalRecords){
                    for(String[] k:RECORDS){
                        try{records.put(k[3],client.record(k[0],k[1],k[2]));}
                        catch(Exception e){records.put(k[3],new JSONObject().put("ok",false));}
                    }
                    packet.put("using_hub_fallback",true);
                }else packet.put("using_hub_fallback",false);
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
            long preloadStart=android.os.SystemClock.elapsedRealtime();
            UiSnapshot previous=uiSnapshot;
            Map<String,Stats> preparedTotals=new HashMap<>();
            Map<String,Stats> preparedFrames=new HashMap<>();
            // Preload summaries for every strategy in the worker, before a tab can draw them.
            // No stock/coin order, SQLite write or strategy state is changed here.
            if(reusedLocalRecords&&meaningfulLocalRecords&&!previous.totals.isEmpty()){
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
                updateHeader();
                if(!"operation".equals(page)
                    &&(ledgerChanged||"home".equals(page)||"errors".equals(page)))render();
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
        SimpleDateFormat f=new SimpleDateFormat("MM-dd HH:mm:ss",Locale.KOREA);f.setTimeZone(TimeZone.getTimeZone("Asia/Seoul"));
        clockText.setText(f.format(new Date())+" KST");
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
        Object ts=firstValue(row,"filled_at","executed_at","exit_at","sold_at",
            "closed_at","at","ts","timestamp","created_at","buy_at","entry_at",
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
                String frame=timeFrameOf(row);
                if(grouped.containsKey(frame))grouped.get(frame).add(event);
            }
            for(Map.Entry<String,java.util.List<RealizedTrend.Event>> e:grouped.entrySet())
                ready.put(StrategyDashboardScope.trend(m,strategyName,e.getKey()),
                    RealizedTrend.cumulative(e.getValue(),100));
        }
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
        selection.setTextSize(11);
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
        if(meaningfulRecord(local))return local;
        JSONObject p=snapshot.optJSONObject("records");
        if(p!=null){
            JSONObject r=p.optJSONObject(key);
            JSONObject d=r==null?null:r.optJSONObject("data");
            if(meaningfulRecord(d))return d;
        }
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
        if(actual!=null)st.target=num(actual,"rate_pct");
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
            Object v=r.opt(k);if(v instanceof Boolean)return (Boolean)v;
            String s=String.valueOf(v).toLowerCase(Locale.ROOT);if("1".equals(s)||s.contains("true")||s.contains("yes")||s.contains("fill")||s.contains("완료"))return true;
        }return false;
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
        if("home".equals(page))renderHome();
        else if("positions".equals(page))renderList("보유 종목",stats(market,strategy).positions,"positions");
        else if("pending".equals(page))renderList("매수 대기",stats(market,strategy).pending,"pending");
        else if("executions".equals(page))renderList("체결 이력",stats(market,strategy).executions,"executions");
        else if("operation".equals(page))renderOperation();
        else if("research".equals(page))renderResearch();
        else if("scores".equals(page))renderScores();
        else if("errors".equals(page))renderErrors();
        else if("policyHistory".equals(page))renderPolicyHistory();
        else renderMore();
    }

    private void renderHome(){
        Stats selected=stats(market,strategy);
        if("stock".equals(market)&&"gmo".equals(strategy)&&
           selected.positions.length()+selected.executions.length()+selected.research.length()==0){
            JSONObject diag=ledger(market,strategy).optJSONObject("load_diagnostic");
            String detail=diag==null?"수급매매 엔진에 표시할 원장이 없음":
                diag.optString("stage","수급 원장 확인 필요");
            TextView warn=text("수급매매 원장 확인 필요 · "+detail+
                " · 왼쪽 설정에서 PC 원본/엔진 건수 확인",12);
            warn.setTextColor(Color.rgb(255,119,133));
            warn.setPadding(dp(5),dp(6),dp(5),dp(6));
            content.addView(warn);
        }
        TextView title=text(strategyName(market,strategy)+" 대시보드",22);title.setTypeface(null,Typeface.BOLD);content.addView(title);
        TextView sub=text(("stock".equals(market)?"주식":"코인")+" · 실제 계좌 원장 / 실제 체결 기준 성과",13);
        sub.setTextColor(Color.rgb(138,147,167));sub.setPadding(0,dp(3),0,dp(10));content.addView(sub);

        String feedName="stock".equals(market)?"키움 DIRECT · KOSPI/KOSDAQ":"업비트 DIRECT · KRW TOP35";
        content.addView(feedCard(market,feedName),new LinearLayout.LayoutParams(-1,dp(64)));

        // Full-width card dedicates readable space to strategy-scoped P/L values.
        LinearLayout pnl=card();
        LinearLayout header=new LinearLayout(this);header.setOrientation(LinearLayout.HORIZONTAL);
        TextView pl=text("선택 전략 누적손익",14);pl.setTextColor(Color.rgb(151,173,203));
        header.addView(pl,new LinearLayout.LayoutParams(0,dp(23),1));
        TextView stg=text(strategyName(market,strategy),13);stg.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
        stg.setTextColor(Color.rgb(146,186,231));header.addView(stg,new LinearLayout.LayoutParams(0,dp(23),1));
        pnl.addView(header);
        TextView pv=text(money(selected.total),34);pv.setTypeface(null,Typeface.BOLD);
        autoSingle(pv,23,34);paintProfit(pv,selected.total);
        pnl.addView(pv,new LinearLayout.LayoutParams(-1,dp(51)));
        LinearLayout split=new LinearLayout(this);split.setOrientation(LinearLayout.HORIZONTAL);
        addLargeMoneyMetric(split,"실현",selected.realized);
        addLargeMoneyMetric(split,"평가",selected.unrealized);
        pnl.addView(split,new LinearLayout.LayoutParams(-1,dp(58)));
        LinearLayout.LayoutParams pnlLp=new LinearLayout.LayoutParams(-1,dp(167));
        pnlLp.setMargins(0,dp(10),0,0);content.addView(pnl,pnlLp);

        LinearLayout hero=new LinearLayout(this);hero.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout winCard=card();DonutMetricView win=new DonutMetricView(this);
        win.setMetric("실제 승률",selected.win,pct(selected.win));
        winCard.addView(win,new LinearLayout.LayoutParams(-1,-1));
        hero.addView(winCard,new LinearLayout.LayoutParams(0,dp(142),1));
        LinearLayout hitCard=card();DonutMetricView hit=new DonutMetricView(this);
        hit.setMetric("실제 목표도달율",selected.target,pct(selected.target));
        hitCard.addView(hit,new LinearLayout.LayoutParams(-1,-1));
        LinearLayout.LayoutParams hp=new LinearLayout.LayoutParams(0,dp(142),1);
        hp.setMargins(dp(8),0,0,0);hero.addView(hitCard,hp);
        LinearLayout.LayoutParams heroLp=new LinearLayout.LayoutParams(-1,dp(142));
        heroLp.setMargins(0,dp(9),0,0);content.addView(hero,heroLp);

        LinearLayout activity=new LinearLayout(this);activity.setOrientation(LinearLayout.HORIZONTAL);
        addKpi(activity,"보유중",String.valueOf(selected.positions.length()));
        addKpi(activity,"매수대기",String.valueOf(selected.pending.length()));
        addKpi(activity,"매수체결",String.valueOf(selected.flowBought));
        addKpi(activity,"매도체결",String.valueOf(selected.flowSold));
        LinearLayout.LayoutParams ap=new LinearLayout.LayoutParams(-1,dp(86));ap.setMargins(0,dp(9),0,0);content.addView(activity,ap);

        boolean frames=StrategyDashboardScope.showFrameCharts(strategy);
        if(frames){
            TextView sec=text(strategyName(market,strategy)+" · 시간봉별 실제 계좌성과",18);sec.setTypeface(null,Typeface.BOLD);
            sec.setTextColor(Color.rgb(220,224,235));sec.setPadding(0,dp(18),0,dp(8));content.addView(sec);
            TextView guide=text("연구용 가상진입 제외 · 실제 매수대기 → 매수완료 → 보유중 → 매도완료",12);guide.setTextColor(Color.rgb(128,138,160));guide.setPadding(0,0,0,dp(7));content.addView(guide);

            for(String tf:new String[]{"5m","30m","60m","day"}){
                Stats q=statsTf(market,strategy,tf);
                TimeframeAccountFlowView flow=new TimeframeAccountFlowView(this);
                flow.setData(tfLabel(tf),q.total,q.realized,q.unrealized,q.win,q.target,
                    q.flowPending,q.flowBought,q.flowHolding,q.flowSold);
                LinearLayout.LayoutParams fp=new LinearLayout.LayoutParams(-1,dp(180));
                if(!"5m".equals(tf))fp.setMargins(0,dp(9),0,0);
                content.addView(flow,fp);
            }
        }

        TextView trendTitle=text("실현손익 추세",18);trendTitle.setTypeface(null,Typeface.BOLD);
        trendTitle.setTextColor(Color.rgb(220,224,235));
        trendTitle.setPadding(0,dp(18),0,dp(8));content.addView(trendTitle);
        // Never merge other strategies' realized P/L into the selected strategy dashboard.
        addTrendCard("all",strategyName(market,strategy)+" 누적",null);
        if(StrategyDashboardScope.showFrameCharts(strategy)){
            addTrendCard("5m","5분봉", "30m");
            addTrendCard("60m","1시간봉","day");
        }
    }

    private LinearLayout chartView(String frame,String label){
        LinearLayout panel=card();
        PnlSparklineView chart=new PnlSparklineView(this);
        RealizedTrend.Series series=uiSnapshot.trends.get(
            StrategyDashboardScope.trend(market,strategy,frame));
        if(series==null)series=new RealizedTrend.Series(new long[0],new double[0]);
        chart.setSeries(series,label);
        panel.addView(chart,new LinearLayout.LayoutParams(-1,dp(160)));
        return panel;
    }

    private void addTrendCard(String frame,String label,String pairedFrame){
        if(pairedFrame==null){
            LinearLayout full=chartView(frame,label);
            LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,dp(182));
            params.setMargins(0,dp(8),0,0);
            content.addView(full,params);
            return;
        }
        LinearLayout row=new LinearLayout(this);row.setOrientation(LinearLayout.HORIZONTAL);
        row.addView(chartView(frame,label),new LinearLayout.LayoutParams(0,dp(182),1));
        LinearLayout.LayoutParams second=new LinearLayout.LayoutParams(0,dp(182),1);
        second.setMargins(dp(7),0,0,0);
        row.addView(chartView(pairedFrame,tfLabel(pairedFrame)),second);
        LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,dp(182));
        params.setMargins(0,dp(8),0,0);
        content.addView(row,params);
    }

    private void addLargeMoneyMetric(LinearLayout row,String label,double amount){
        LinearLayout tile=new LinearLayout(this);tile.setOrientation(LinearLayout.VERTICAL);
        tile.setPadding(dp(8),dp(4),dp(6),dp(4));
        tile.setBackground(bg(Color.rgb(22,30,43),Color.rgb(43,62,82),11));
        TextView cap=text(label,13);cap.setTextColor(Color.rgb(149,173,201));
        tile.addView(cap,new LinearLayout.LayoutParams(-1,dp(21)));
        TextView val=text(money(amount),22);val.setTypeface(null,Typeface.BOLD);
        autoSingle(val,16,22);paintProfit(val,amount);
        tile.addView(val,new LinearLayout.LayoutParams(-1,dp(30)));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(58),1);
        if(row.getChildCount()>0)lp.setMargins(dp(8),0,0,0);
        row.addView(tile,lp);
    }

    private View feedCard(String m,String title){
        LinearLayout c=new LinearLayout(this);c.setOrientation(LinearLayout.HORIZONTAL);c.setGravity(Gravity.CENTER_VERTICAL);c.setPadding(dp(13),0,dp(13),0);
        c.setBackground(bg(Color.rgb(15,18,27),Color.rgb(38,43,58),14));
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
        TextView x=text((on?"실시간 수신":"시세 대기")+(n>0?" · 등록 "+n+"종목":"")+(d.isEmpty()?"":" · "+d),11);
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
        LinearLayout c=card();c.setGravity(Gravity.CENTER_VERTICAL);
        TextView a=text(label,12);a.setTextColor(Color.rgb(142,152,173));c.addView(a,new LinearLayout.LayoutParams(-1,dp(22)));
        TextView b=text(value,24);b.setTypeface(null,Typeface.BOLD);autoSingle(b,16,24);b.setTextColor(Color.rgb(240,242,248));c.addView(b,new LinearLayout.LayoutParams(-1,dp(38)));
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(82),1);if(row.getChildCount()>0)p.setMargins(dp(8),0,0,0);row.addView(c,p);
    }

    private double usefulNumber(JSONObject r,String... fields){
        return num(r,fields);
    }
    private String scenarioSummary(JSONObject r){
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
            case "a":return "구분 "+r.optString("side",r.optString("type","미기록"));
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
        TextView t=text(value,12);
        int standard=Color.rgb(202,215,234);
        if(value.startsWith("순손익 ")||value.startsWith("현재 손익률 ")){
            double v=Double.NaN;
            try{v=Double.parseDouble(value.replaceAll("[^0-9.\\-]",""));}catch(Exception ignored){}
            t.setTextColor(Double.isNaN(v)?standard:v<0?Color.rgb(255,119,133):
                v>0?Color.rgb(62,224,181):Color.rgb(255,194,91));
        }else t.setTextColor(colorByStatus(value,standard));
        t.setPadding(dp(2),dp(5),dp(2),dp(5));t.setMaxLines(2);
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
        navigation.setPadding(0,dp(7),0,dp(10));
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

    private void renderList(String title,JSONArray a,String kind){
        boolean marketWide="positions".equals(kind)||"pending".equals(kind)||
            "executions".equals(kind);
        java.util.List<HistoryRow> history=marketWide?selectedHistory(kind):null;
        int total=marketWide?history.size():a.length();
        LinearLayout header=new LinearLayout(this);header.setGravity(Gravity.CENTER_VERTICAL);
        if(marketWide)header.addView(historyFilterButton(),new LinearLayout.LayoutParams(dp(116),dp(42)));
        TextView h=text(title+" · "+total+"건",18);h.setTypeface(null,Typeface.BOLD);
        header.addView(h,new LinearLayout.LayoutParams(0,dp(43),1));
        if("positions".equals(kind)){
            Button policyButton=button("정책이력",false,()->{page="policyHistory";persistNavigation();render();});policyButton.setTextSize(11);
            header.addView(policyButton,new LinearLayout.LayoutParams(dp(92),dp(39)));
        }
        content.addView(header,new LinearLayout.LayoutParams(-1,dp(45)));
        if(total==0){empty(uiSnapshot.totals.isEmpty()?"원장 화면 사전로딩 중":"선택한 목록에 기록 없음");return;}
        int first=historyStart(kind,total),last=HistoryPager.end(first,total);
        addHistoryPager(kind,total);
        for(int i=first;i<last;i++){
            HistoryRow item=marketWide?history.get(i):null;
            JSONObject r=marketWide?item.row:a.optJSONObject(i);
            if(r==null)continue;
            LinearLayout c=card();
            LinearLayout topRow=new LinearLayout(this);topRow.setOrientation(LinearLayout.HORIZONTAL);topRow.setGravity(Gravity.CENTER_VERTICAL);
            String tagged=marketWide?nameOf(r)+" · "+HistorySelection.strategyTag(item.source,item.frame):nameOf(r)+" · "+HistorySelection.strategyTag(strategy,timeFrameOf(r));
            TextView n=text(tagged,15);n.setTypeface(null,Typeface.BOLD);n.setMaxLines(1);
            topRow.addView(n,new LinearLayout.LayoutParams(0,dp(29),1));
            double profit=usefulNumber(r,"net_pnl","unrealized_pnl","paper_pnl","net_profit","net_realized","pnl");
            if(!"pending".equals(kind)&&finite(profit)){
                TextView p=text(money(profit),14);p.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
                paintProfit(p,profit);topRow.addView(p,new LinearLayout.LayoutParams(-2,dp(29)));
            }
            c.addView(topRow);
            TextView when=text((marketWide?refLabel(item.source):refLabel())+" "+
                (marketWide?timeOf(r,item.source):timeOf(r)),11);
            when.setTextColor(Color.rgb(139,163,192));c.addView(when);
            LinearLayout firstRow=new LinearLayout(this);firstRow.setOrientation(LinearLayout.HORIZONTAL);
            addGridCell(firstRow,cellValue(r,kind,"a"));addGridCell(firstRow,cellValue(r,kind,"b"));c.addView(firstRow);
            LinearLayout secondRow=new LinearLayout(this);secondRow.setOrientation(LinearLayout.HORIZONTAL);
            addGridCell(secondRow,cellValue(r,kind,"c"));addGridCell(secondRow,cellValue(r,kind,"d"));c.addView(secondRow);
            if("positions".equals(kind)){
                TextView scenario=text(scenarioSummary(r),12);scenario.setTextColor(Color.rgb(93,218,183));
                scenario.setPadding(dp(2),dp(5),0,dp(4));c.addView(scenario);
                LinearLayout actions=new LinearLayout(this);actions.setOrientation(LinearLayout.HORIZONTAL);
                Button sell=button("강제매도",false,()->showManualSellInfo(r));sell.setTextSize(12);
                actions.addView(sell,new LinearLayout.LayoutParams(0,dp(39),1));
                Button policy=button("정책이력",false,()->{page="policyHistory";persistNavigation();render();});policy.setTextSize(12);
                LinearLayout.LayoutParams pl=new LinearLayout.LayoutParams(0,dp(39),1);pl.setMargins(dp(6),0,0,0);actions.addView(policy,pl);c.addView(actions);
            }
            TextView why=text("근거 · "+reasonOf(r),11);why.setTextColor(Color.rgb(151,176,202));why.setMaxLines(3);
            why.setPadding(dp(2),dp(5),dp(2),0);c.addView(why);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(5));content.addView(c,lp);
        }
        addHistoryPager(kind,total);
    }
    private void showManualSellInfo(JSONObject position){
        String identity=nameOf(position);
        new AlertDialog.Builder(this).setTitle(identity+" · 강제매도")
            .setMessage("이 버전에서는 강제매도 버튼이 실제 주문을 발행하지 않습니다. 현재 EXE의 가상 수동매도와 Android 실거래 주문 상태머신이 동일한 결과를 보장하는지 확인되지 않았습니다.\n\n오주문 방지를 위해 강제매도를 차단합니다. 실거래 중에는 증권사/거래소 앱에서 직접 매도하십시오.")
            .setPositiveButton("확인",null).show();
    }
    private void renderPolicyHistory(){
        TextView h=text(strategyName(market,strategy)+" · 정책이력",21);h.setTypeface(null,Typeface.BOLD);content.addView(h);
        TextView guide=text("실제 체결·매도 근거로 확인 가능한 정책 판단 기록임. 정책 버전 적용시각은 내부 기록에서 관리함.",12);
        guide.setTextColor(Color.rgb(145,166,191));guide.setPadding(0,dp(5),0,dp(12));content.addView(guide);
        JSONArray executions=stats(market,strategy).executions;
        if(executions.length()==0){empty("기록된 체결·매도 정책 판단 없음");return;}
        int first=historyStart("policyHistory",executions.length());
        int last=HistoryPager.end(first,executions.length());
        addHistoryPager("policyHistory",executions.length());
        for(int i=first;i<last;i++){
            JSONObject r=executions.optJSONObject(i);if(r==null)continue;
            LinearLayout c=card();
            TextView name=text(nameOf(r)+" · "+r.optString("side",r.optString("type","체결")),15);
            name.setTypeface(null,Typeface.BOLD);c.addView(name);
            TextView time=text(formatKst(r.opt("at"))+" · "+reasonOf(r),12);
            time.setTextColor(Color.rgb(171,194,219));c.addView(time);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(6));content.addView(c,lp);
        }
        addHistoryPager("policyHistory",executions.length());
    }

    private void zipPut(ZipOutputStream zip,String name,String text)throws Exception{
        ZipEntry e=new ZipEntry(name);zip.putNextEntry(e);
        zip.write((text==null?"":text).getBytes(StandardCharsets.UTF_8));zip.closeEntry();
    }

    private void exportResearch(Stats st){
        try{
            JSONObject rs=research(market,strategy),lg=ledger(market,strategy);
            JSONObject manifest=new JSONObject()
                .put("schema","ARTITRADE_RESEARCH_BUNDLE_V078")
                .put("market",market).put("strategy",strategy).put("strategy_name",strategyName(market,strategy))
                .put("market_version",marketVersionLabel()).put("exported_at",System.currentTimeMillis())
                .put("note","대시보드 실제계좌 성과와 연구용 가상진입 결과를 분리한 연구 번들");
            ByteArrayOutputStream buf=new ByteArrayOutputStream();
            try(ZipOutputStream zip=new ZipOutputStream(buf)){
                zipPut(zip,"manifest.json",manifest.toString(2));
                zipPut(zip,"research/research_record.json",rs.toString(2));
                zipPut(zip,"research/items.json",String.valueOf(rs.optJSONArray("items")==null?new JSONArray():rs.optJSONArray("items")));
                zipPut(zip,"research/score_items.json",String.valueOf(rs.optJSONArray("score_items")==null?new JSONArray():rs.optJSONArray("score_items")));
                zipPut(zip,"ledger/account_snapshot.json",lg.toString(2));
                JSONObject diag=new JSONObject().put("runtime",copyCache().optJSONArray("runtime_diagnostics"))
                    .put("strategy_runtime",copyCache().optJSONObject("strategy_runtime"));
                zipPut(zip,"diagnostics/runtime.json",diag.toString(2));
            }
            pendingResearchExport=buf.toByteArray();
            String stamp=new SimpleDateFormat("yyyyMMdd_HHmmss",Locale.KOREA).format(new Date());
            Intent i=new Intent(Intent.ACTION_CREATE_DOCUMENT);
            i.addCategory(Intent.CATEGORY_OPENABLE);i.setType("application/zip");
            i.putExtra(Intent.EXTRA_TITLE,"ARTITRADE_"+market+"_"+strategy+"_RESEARCH_FULL_"+stamp+".zip");
            startActivityForResult(i,REQUEST_EXPORT_RESEARCH);
        }catch(Exception e){
            pendingResearchExport=null;
            new AlertDialog.Builder(this).setTitle("연구자료 추출 실패").setMessage(String.valueOf(e.getMessage())).setPositiveButton("확인",null).show();
        }
    }

    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data){
        super.onActivityResult(requestCode,resultCode,data);
        if(requestCode!=REQUEST_EXPORT_RESEARCH)return;
        byte[] payload=pendingResearchExport;pendingResearchExport=null;
        if(resultCode!=RESULT_OK||payload==null||data==null)return;
        Uri target=data.getData();if(target==null)return;
        try(OutputStream out=getContentResolver().openOutputStream(target,"w")){
            if(out==null)throw new IllegalStateException("저장 위치를 열 수 없습니다.");
            out.write(payload);out.flush();
            new AlertDialog.Builder(this).setTitle("연구자료 추출 완료")
                .setMessage("연구요약·실제/가상 연구행·점수후보·계좌원장 스냅샷을 ZIP으로 저장했습니다.")
                .setPositiveButton("확인",null).show();
        }catch(Exception e){
            new AlertDialog.Builder(this).setTitle("연구자료 저장 실패").setMessage(String.valueOf(e.getMessage())).setPositiveButton("확인",null).show();
        }
    }

    private void renderResearch(){
        String filter=activeHistoryFilter();
        String scope="gmo".equals(filter)||"close".equals(filter)?filter:strategy;
        boolean combined="all".equals(filter);
        Stats st=stats(market,scope);
        JSONObject rs=research(market,scope);
        java.util.List<HistoryRow> entries=selectedHistory("research");
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
        export.setTextSize(11);head.addView(export,new LinearLayout.LayoutParams(dp(92),dp(38)));
        content.addView(head,new LinearLayout.LayoutParams(-1,dp(44)));

        String loadStatus=rs.optString("load_status","확인 전");
        int loaded=rs.optInt("loaded_items",st.research.length());
        int sourceCount=rs.optInt("source_total",-1);
        int maxShown=loaded;
        boolean shortRead=sourceCount>maxShown;
        String subtitle=(combined?"전체 전략":strategyName(market,scope))+
            "  ·  원본 "+(sourceCount<0?"?":String.valueOf(sourceCount))+
            "  /  표시 "+entries.size()+"  ·  "+loadStatus;
        TextView origin=text(subtitle,11);
        origin.setTextColor("ERROR".equals(loadStatus)||shortRead?Color.rgb(255,119,133):
            "OK".equals(loadStatus)?Color.rgb(62,224,181):Color.rgb(255,194,91));
        origin.setPadding(dp(2),dp(3),dp(2),dp(8));content.addView(origin);
        JSONObject sourceEvidence=rs.optJSONObject("evidence_counts");
        if(sourceEvidence!=null&&sourceEvidence.length()>0){
            long evidenceRows=0;
            StringBuilder evidenceDetail=new StringBuilder();
            java.util.Iterator<String> tables=sourceEvidence.keys();
            while(tables.hasNext()){
                String tableName=tables.next();
                long count=sourceEvidence.optLong(tableName,0);
                evidenceRows+=Math.max(0,count);
                evidenceDetail.append(tableName).append(": ").append(count).append("행\n");
            }
            LinearLayout sourceKpis=new LinearLayout(this);
            sourceKpis.setOrientation(LinearLayout.HORIZONTAL);
            addKpi(sourceKpis,"후보 원본",sourceCount<0?"—":String.valueOf(sourceCount));
            addKpi(sourceKpis,"관련 원장 행",String.valueOf(evidenceRows));
            addKpi(sourceKpis,"화면 게시",String.valueOf(loaded));
            LinearLayout.LayoutParams auditLp=new LinearLayout.LayoutParams(-1,dp(82));
            auditLp.setMargins(0,dp(4),0,dp(8));
            content.addView(sourceKpis,auditLp);
            final String detail=evidenceDetail.toString();
            sourceKpis.setOnClickListener(v->new AlertDialog.Builder(this)
                .setTitle("PC 승계 원본 연구·체결 증거 테이블")
                .setMessage(detail+"\n※ 원본 테이블 행수이며 화면에 모두 게시됐다는 뜻이 아님")
                .setPositiveButton("확인",null).show());
        }
        if(shortRead||"ERROR".equals(loadStatus)){
            String msg="ERROR".equals(loadStatus)?"원장 조회 오류: "+rs.optString("load_error","")+
                " · 다른 전략 리서치는 별도로 확인":
                "전체 원본 중 일부만 화면에 게시됨 · PC 원본 DB/휴대폰 SQLite 전체 기록 검증 필요";
            TextView warn=text(msg,11);warn.setTextColor(Color.rgb(255,119,133));
            warn.setPadding(dp(4),dp(4),dp(4),dp(8));content.addView(warn);
        }

        double actualRate=tally.actualRate(),virtualRate=tally.virtualRate();
        LinearLayout summary=new LinearLayout(this);summary.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout actualCard=card();DonutMetricView actual=new DonutMetricView(this);
        actual.setMetric("실제 목표도달률",actualRate,pct(actualRate));
        actualCard.addView(actual,new LinearLayout.LayoutParams(-1,dp(122)));
        TextView a=text(tally.actualHit+"/"+(tally.actualHit+tally.actualMiss)+
            "  · 대기 "+tally.actualPending,12);
        a.setGravity(Gravity.CENTER);actualCard.addView(a,new LinearLayout.LayoutParams(-1,dp(24)));
        summary.addView(actualCard,new LinearLayout.LayoutParams(0,dp(154),1));
        LinearLayout virtualCard=card();DonutMetricView v=new DonutMetricView(this);
        v.setMetric("가상 목표도달률",virtualRate,pct(virtualRate));
        virtualCard.addView(v,new LinearLayout.LayoutParams(-1,dp(122)));
        TextView vCounts=text(tally.virtualHit+"/"+(tally.virtualHit+tally.virtualMiss)+
            "  · 대기 "+tally.virtualPending,12);
        vCounts.setGravity(Gravity.CENTER);
        virtualCard.addView(vCounts,new LinearLayout.LayoutParams(-1,dp(24)));
        LinearLayout.LayoutParams vp=new LinearLayout.LayoutParams(0,dp(154),1);
        vp.setMargins(dp(8),0,0,0);summary.addView(virtualCard,vp);
        content.addView(summary,new LinearLayout.LayoutParams(-1,dp(154)));

        LinearLayout kpis=new LinearLayout(this);kpis.setOrientation(LinearLayout.HORIZONTAL);
        addKpi(kpis,"전체 신호",String.valueOf(tally.signals));
        addKpi(kpis,"실제",String.valueOf(tally.actualCount));
        addKpi(kpis,"가상",String.valueOf(tally.virtualEntered));
        addKpi(kpis,"미진입",String.valueOf(tally.virtualNotEntered));
        LinearLayout.LayoutParams klp=new LinearLayout.LayoutParams(-1,dp(82));
        klp.setMargins(0,dp(7),0,0);content.addView(kpis,klp);

        TextView section=text("시간봉 × 점수대  |  실제 / 가상 목표도달률",17);
        section.setTypeface(null,Typeface.BOLD);
        section.setTextColor(Color.rgb(207,226,247));
        section.setPadding(dp(2),dp(16),dp(2),dp(7));content.addView(section);

        String[] frames=StrategyDashboardScope.showFrameCharts(scope)
            ?new String[]{"5m","30m","60m","day"}:new String[]{scope};
        for(String tf:frames){
            if(!combined&&HistorySelection.frame(filter).length()>0
                &&!HistorySelection.frame(filter).equals(tf))continue;
            String label="gmo".equals(tf)?"수급매매":"close".equals(tf)?"종가베팅":tfLabel(tf);
            ResearchScoreMatrixView matrix=new ResearchScoreMatrixView(this);
            matrix.setGrid(grid,tf,label);
            int height=grid.cell(tf,ResearchScoreGrid.NO_SCORE).signals>0?265:234;
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,dp(height));
            lp.setMargins(0,dp(6),0,0);content.addView(matrix,lp);
        }
        TextView note=text("분모 = 확정(HIT + MISS) · 관측중/가상 미진입 제외 · 기록 없음 = —",10);
        note.setTextColor(Color.rgb(150,170,195));
        note.setPadding(dp(3),dp(7),dp(3),dp(4));content.addView(note);

        TextView recent=text("개별 연구·매수매도 판단 근거  · "+entries.size()+"건",16);
        recent.setTypeface(null,Typeface.BOLD);
        recent.setPadding(dp(2),dp(12),dp(2),dp(7));content.addView(recent);
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
            title.addView(symbol,new LinearLayout.LayoutParams(0,dp(26),1));
            double sc=num(row,"score","original_score","gmo_score_at_signal");
            TextView score=text(finite(sc)?number(sc):"—",19);
            score.setTextColor(Color.rgb(252,193,99));score.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);
            title.addView(score,new LinearLayout.LayoutParams(dp(66),dp(26)));
            panel.addView(title);
            String outcome=row.optString("outcome","");
            if(outcome.isEmpty()){Boolean reached=target(row);
                outcome=reached==null?"PENDING":reached?"HIT":"MISS";}
            boolean actualEntry=bool(row,"bought");
            String tag=actualEntry?"실제":"가상";
            TextView status=text(tag+"  ·  "+outcome+"  ·  "+timeOf(row,item.source),12);
            status.setTextColor("HIT".equals(outcome)?Color.rgb(62,224,181):
                "MISS".equals(outcome)?Color.rgb(255,119,133):Color.rgb(255,194,91));
            panel.addView(status);
            String basis=reasonOf(row);
            TextView detail=text("판단근거  "+basis,11);
            detail.setTextColor(Color.rgb(177,199,223));
            detail.setMaxLines(4);detail.setPadding(dp(2),dp(5),0,0);panel.addView(detail);
            LinearLayout.LayoutParams itemLp=new LinearLayout.LayoutParams(-1,-2);
            itemLp.setMargins(0,0,0,dp(6));content.addView(panel,itemLp);
        }
        addHistoryPager("research",entries.size());
    }

    private void renderScores(){
        JSONObject rs=research(market,strategy);
        JSONArray items=rs.optJSONArray("score_items");if(items==null)items=rs.optJSONArray("items");if(items==null)items=new JSONArray();
        TextView h=text(strategyName(market,strategy)+" · 엔진 점수 비교",22);h.setTypeface(null,Typeface.BOLD);content.addView(h);
        TextView sub=text("미선정 후보 포함 · 후보 당시 점수와 판정 상태 · 실제 계좌성과와 분리",13);sub.setTextColor(Color.rgb(135,160,190));sub.setPadding(0,dp(3),0,dp(9));content.addView(sub);
        LinearLayout k=card();LinearLayout kr=new LinearLayout(this);kr.setOrientation(LinearLayout.HORIZONTAL);
        addMini(kr,"후보",String.valueOf(items.length()));
        int hi=0,mid=0,low=0;for(int i=0;i<items.length();i++){JSONObject x=items.optJSONObject(i);double sc=x==null?Double.NaN:num(x,"score","diverse_11_score","gmo_score_at_signal");if(!finite(sc))continue;if(sc>=61)hi++;else if(sc>=41)mid++;else low++;}
        addMini(kr,"61점+",String.valueOf(hi));addMini(kr,"41~60",String.valueOf(mid));addMini(kr,"40점↓",String.valueOf(low));k.addView(kr);content.addView(k,new LinearLayout.LayoutParams(-1,dp(78)));
        if(items.length()==0){empty("점수 비교 후보 없음");return;}
        int first=historyStart("scores",items.length()),last=HistoryPager.end(first,items.length());
        addHistoryPager("scores",items.length());
        for(int i=first;i<last;i++){
            JSONObject r=items.optJSONObject(i);if(r==null)continue;LinearLayout c=card();
            LinearLayout hr=new LinearLayout(this);hr.setOrientation(LinearLayout.HORIZONTAL);
            TextView n=text(nameOf(r),16);n.setTypeface(null,Typeface.BOLD);hr.addView(n,new LinearLayout.LayoutParams(0,dp(28),1));
            double sc=num(r,"score","diverse_11_score","gmo_score_at_signal");TextView sv=text(finite(sc)?number(sc)+"점":"점수 미기록",15);sv.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);sv.setTextColor(Color.rgb(255,201,94));hr.addView(sv,new LinearLayout.LayoutParams(dp(110),dp(28)));c.addView(hr);
            String dec=r.optString("decision",r.optString("status",r.optString("verdict","판정 미기록")));
            TextView d=text("판정: "+dec+" · "+refLabel()+" "+timeOf(r),12);d.setTextColor(Color.rgb(153,175,201));c.addView(d);
            TextView why=text("근거: "+reasonOf(r),12);why.setTextColor(Color.rgb(184,201,221));c.addView(why);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(6));content.addView(c,lp);
        }
        addHistoryPager("scores",items.length());
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

    private void renderErrors(){
        JSONArray all=marketErrors();
        TextView h=text(("stock".equals(market)?"주식":"코인")+" 전체 오류이력",18);h.setTypeface(null,Typeface.BOLD);content.addView(h);
        TextView note=text("선택 전략과 무관하게 해당 시장의 실시간·전략·주문·원장 오류를 통합 표시",10);note.setTextColor(Color.rgb(135,160,190));note.setPadding(0,dp(3),0,dp(9));content.addView(note);
        LinearLayout summary=new LinearLayout(this);summary.setOrientation(LinearLayout.HORIZONTAL);
        addKpi(summary,"전체",String.valueOf(all.length()));
        int auth=0,feed=0,order=0;
        for(int i=0;i<all.length();i++){JSONObject r=all.optJSONObject(i);String x=r==null?"":r.toString().toLowerCase(Locale.ROOT);if(x.contains("auth")||x.contains("token")||x.contains("key")||x.contains("oauth"))auth++;if(x.contains("websocket")||x.contains("feed")||x.contains("0b")||x.contains("0d"))feed++;if(x.contains("order")||x.contains("fill")||x.contains("체결")||x.contains("주문"))order++;}
        addKpi(summary,"인증",String.valueOf(auth));addKpi(summary,"실시간",String.valueOf(feed));addKpi(summary,"주문/체결",String.valueOf(order));content.addView(summary);
        if(all.length()==0){empty("기록된 오류 없음");return;}
        int first=historyStart("errors",all.length()),last=HistoryPager.end(first,all.length());
        addHistoryPager("errors",all.length());
        for(int i=first;i<last;i++){
            JSONObject r=all.optJSONObject(i);if(r==null)continue;LinearLayout c=card();
            TextView n=text(r.optString("stage",r.optString("error_type",r.optString("code","오류 기록"))),12);n.setTypeface(null,Typeface.BOLD);n.setTextColor(Color.rgb(255,145,155));c.addView(n);
            TextView d=text(r.optString("message",r.optString("detail","상세 미기록")),10);d.setPadding(0,dp(4),0,0);c.addView(d);
            TextView t=text(formatKst(r.opt("at")),9);t.setTextColor(Color.rgb(130,153,182));c.addView(t);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(7));content.addView(c,lp);
        }
        addHistoryPager("errors",all.length());
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
        e.setBackground(bg(Color.rgb(13,16,24),Color.rgb(49,54,72),10));
        e.setPadding(dp(10),0,dp(10),0);return e;
    }

    private LinearLayout operationFrameCard(String m,String stg,String tf,JSONObject settings){
        JSONObject cfg=operationPair(settings,m,stg,tf);
        long seed=cfg.optLong("seed",2000000),maxp=cfg.optLong("max_positions",4);
        LinearLayout c=card();
        LinearLayout head=new LinearLayout(this);head.setOrientation(LinearLayout.HORIZONTAL);head.setGravity(Gravity.CENTER_VERTICAL);
        TextView tfv=text(tfLabel(tf),18);tfv.setTypeface(null,Typeface.BOLD);tfv.setTextColor(Color.rgb(218,214,255));head.addView(tfv,new LinearLayout.LayoutParams(0,dp(28),1));
        TextView acct=text("독립계좌",10);acct.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);acct.setTextColor(Color.rgb(113,123,145));head.addView(acct,new LinearLayout.LayoutParams(dp(64),dp(28)));c.addView(head);
        TextView sl=text("운용시드",11);sl.setTextColor(Color.rgb(137,147,169));sl.setPadding(0,dp(5),0,dp(3));c.addView(sl);
        String key=m+"."+stg+"."+tf;
        EditText seedEdit=operationInput(operationDraft(key,"seed",String.valueOf(seed)),"2,000,000");
        trackOperationDraft(seedEdit,key+".seed");
        c.addView(seedEdit,new LinearLayout.LayoutParams(-1,dp(40)));
        TextView ml=text("최대 보유종목",11);ml.setTextColor(Color.rgb(137,147,169));ml.setPadding(0,dp(6),0,dp(3));c.addView(ml);
        EditText maxEdit=operationInput(operationDraft(key,"max_positions",String.valueOf(maxp)),"4");
        trackOperationDraft(maxEdit,key+".max_positions");
        c.addView(maxEdit,new LinearLayout.LayoutParams(-1,dp(40)));
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
                LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(202),1);
                if(col>0)lp.setMargins(dp(8),0,0,0);line.addView(card,lp);
            }
            LinearLayout.LayoutParams rp=new LinearLayout.LayoutParams(-1,dp(202));if(row>0)rp.setMargins(0,dp(8),0,0);content.addView(line,rp);
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
        LinearLayout b=card();b.addView(text("백그라운드",13));Button bat=button("절전 예외 설정 열기",false,()->{try{startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}catch(Exception ignored){}});LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,dp(42));bp.setMargins(0,dp(8),0,0);b.addView(bat,bp);LinearLayout.LayoutParams bc=new LinearLayout.LayoutParams(-1,-2);bc.setMargins(0,dp(8),0,0);content.addView(b,bc);
    }

    private void empty(String s){TextView e=text(s,12);e.setGravity(Gravity.CENTER);e.setTextColor(Color.rgb(112,137,167));e.setPadding(0,dp(40),0,dp(40));content.addView(e);}

    private String strategyName(String m,String s){
        if("coin".equals(m))return "ma".equals(s)?"MA":"기준봉";
        if("ma".equals(s))return "MA";if("close".equals(s))return "종가베팅";if("gmo".equals(s))return "수급매매";return "기준봉";
    }

    private String refLabel(String forStrategy){
        return "gmo".equals(forStrategy)?"사건일":
            "close".equals(forStrategy)?"매수시각":"기준시각";
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

    private String nameOf(JSONObject r){
        String[] keys={"name","korean_name","symbol","ticker","code","market"};String x="";
        for(String k:keys){x=r.optString(k,"").trim();if(!x.isEmpty())break;}if(x.isEmpty())return "종목 미기록";
        if("coin".equals(market)&&!x.matches(".*[가-힣].*")){String s=x.replace("KRW-","").replace("BTC-","").replace("USDT-","").toUpperCase(Locale.ROOT);return coinName(s);}
        return x;
    }

    private String coinName(String s){
        switch(s){case "BTC":return "비트코인";case "ETH":return "이더리움";case "XRP":return "리플";case "SOL":return "솔라나";case "DOGE":return "도지코인";case "ADA":return "에이다";case "AVAX":return "아발란체";case "DOT":return "폴카닷";case "LINK":return "체인링크";case "TRX":return "트론";case "BCH":return "비트코인캐시";case "ETC":return "이더리움클래식";case "SUI":return "수이";default:return s;}
    }

    private String reasonOf(JSONObject r){String[] k={"reason","entry_reason","exit_reason","buy_reason","sell_reason","decision","cause","signal_reason"};for(String x:k){String v=r.optString(x,"").trim();if(!v.isEmpty())return v.length()>120?v.substring(0,120):v;}return "근거 미기록";}

    private String timeOf(JSONObject r){return timeOf(r,strategy);}

    private String formatKst(Object v){
        if(v==null||v==JSONObject.NULL)return "미기록";
        String raw=String.valueOf(v).trim();if(raw.isEmpty())return "미기록";
        long time=RealizedTrend.time(raw);
        if(time>0){
            SimpleDateFormat f=new SimpleDateFormat("yyyy-MM-dd HH:mm",Locale.KOREA);
            f.setTimeZone(TimeZone.getTimeZone("Asia/Seoul"));
            return f.format(new Date(time));
        }
        return raw.length()>19?raw.substring(0,19).replace('T',' '):raw.replace('T',' ');
    }

    private String number(double v){if(!finite(v))return "—";NumberFormat f=NumberFormat.getNumberInstance(Locale.KOREA);f.setMaximumFractionDigits(Math.abs(v)>=100?0:2);return f.format(v);}
    private String money(double v){return finite(v)?number(v)+"원":"—";}
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
                                    requestLedgerRepublish();
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
        main.postDelayed(()->{try{Intent i=new Intent(this,UnifiedTradingService.class);i.setAction(UnifiedTradingService.START);if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);}catch(Throwable e){new RuntimeCrashStore(this).add("native_runtime_start",e);}},1800);
    }

    private void showFatal(Throwable e){
        if(root==null){root=new FrameLayout(this);root.setBackgroundColor(Color.rgb(7,16,28));try{setContentView(root);}catch(Throwable ignored){}}
        root.removeAllViews();LinearLayout p=new LinearLayout(this);p.setOrientation(LinearLayout.VERTICAL);p.setGravity(Gravity.CENTER);p.setPadding(dp(28),dp(20),dp(28),dp(20));p.setBackgroundColor(Color.rgb(7,16,28));
        TextView h=text("ARTITRADE 네이티브 안전모드",22);h.setTypeface(null,Typeface.BOLD);p.addView(h);TextView d=text(e==null?"알 수 없는 오류":e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()),11);d.setTextColor(Color.rgb(255,145,155));d.setPadding(0,dp(10),0,dp(10));p.addView(d);p.addView(button("화면 다시 구성",true,()->{try{showShell();refresh(false);}catch(Throwable x){}}),new LinearLayout.LayoutParams(dp(240),dp(44)));root.addView(p,new FrameLayout.LayoutParams(-1,-1));
    }

    @Override protected void onResume(){super.onResume();main.removeCallbacks(periodic);main.postDelayed(periodic,REFRESH_MS);if(drawerOverlay!=null){main.removeCallbacks(drawerProgressTicker);main.post(drawerProgressTicker);}}
    @Override protected void onPause(){persistNavigation();main.removeCallbacks(periodic);main.removeCallbacks(drawerProgressTicker);super.onPause();}
    @Override protected void onDestroy(){main.removeCallbacks(periodic);main.removeCallbacks(drawerProgressTicker);io.shutdownNow();super.onDestroy();}
}
