package com.artitrade.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
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
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

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
    private final Object cacheLock=new Object();
    private FrameLayout root;
    private LinearLayout content;
    private LinearLayout strategyBar;
    private TextView statusText;
    private TextView clockText;
    private JSONObject cache=new JSONObject();
    private volatile boolean refreshRunning=false;
    private volatile boolean runtimeStartIssued=false;
    private volatile boolean brokerProbeIssued=false;
    private String market="stock";
    private String strategy="at";
    private String page="home";
    private float swipeX=Float.NaN;
    private View drawerOverlay;
    private final Map<String,EditText[]> operationFields=new HashMap<>();
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

    private int dp(float v){return (int)(v*getResources().getDisplayMetrics().density+.5f);}

    private TextView text(String value,int sp){
        TextView t=new TextView(this);
        t.setText(value);
        t.setTextSize(sp);
        t.setTextColor(Color.rgb(240,247,255));
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
        c.setPadding(dp(13),dp(10),dp(13),dp(10));
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
        LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setGravity(Gravity.CENTER);
        TextView i=text(icon,18);i.setGravity(Gravity.CENTER);i.setTextColor(active?Color.rgb(164,151,255):Color.rgb(111,121,143));box.addView(i,new LinearLayout.LayoutParams(-1,dp(25)));
        TextView l=text(label,11);l.setGravity(Gravity.CENTER);l.setTypeface(null,active?Typeface.BOLD:Typeface.NORMAL);l.setTextColor(active?Color.rgb(227,224,255):Color.rgb(111,121,143));box.addView(l,new LinearLayout.LayoutParams(-1,dp(18)));
        box.setOnClickListener(v->{page=key;showShell();});return box;
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

    private void showShell(){
        root.removeAllViews();
        drawerOverlay=null;
        LinearLayout shell=new LinearLayout(this);shell.setOrientation(LinearLayout.VERTICAL);shell.setBackgroundColor(Color.rgb(9,11,17));
        root.addView(shell,new FrameLayout.LayoutParams(-1,-1));

        LinearLayout top=new LinearLayout(this);top.setOrientation(LinearLayout.HORIZONTAL);top.setGravity(Gravity.CENTER_VERTICAL);top.setPadding(dp(14),dp(7),dp(10),dp(5));
        LinearLayout brandBox=new LinearLayout(this);brandBox.setOrientation(LinearLayout.VERTICAL);brandBox.setGravity(Gravity.CENTER_VERTICAL);
        TextView brand=text("ARTITRADE",18);brand.setTypeface(null,Typeface.BOLD);brand.setLetterSpacing(.09f);brandBox.addView(brand);
        statusText=text("데이터 확인 중",10);statusText.setTextColor(Color.rgb(138,147,167));brandBox.addView(statusText);
        top.addView(brandBox,new LinearLayout.LayoutParams(0,dp(43),1));

        LinearLayout marketSwitch=new LinearLayout(this);marketSwitch.setOrientation(LinearLayout.HORIZONTAL);marketSwitch.setPadding(dp(3),dp(3),dp(3),dp(3));
        marketSwitch.setBackground(bg(Color.rgb(20,23,33),Color.rgb(42,47,63),18));
        TextView stock=segmentItem("주식","stock".equals(market),()->{market="stock";strategy="at";page="home";showShell();});
        TextView coin=segmentItem("코인","coin".equals(market),()->{market="coin";strategy="general";page="home";showShell();});
        marketSwitch.addView(stock,new LinearLayout.LayoutParams(dp(58),dp(30)));
        marketSwitch.addView(coin,new LinearLayout.LayoutParams(dp(58),dp(30)));
        top.addView(marketSwitch,new LinearLayout.LayoutParams(dp(122),dp(36)));

        clockText=text("",10);clockText.setTextColor(Color.rgb(116,126,148));clockText.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams tp=new LinearLayout.LayoutParams(dp(86),dp(34));tp.setMargins(dp(7),0,dp(4),0);top.addView(clockText,tp);
        top.addView(iconAction("☰",this::openSettingsDrawer),new LinearLayout.LayoutParams(dp(38),dp(38)));
        shell.addView(top,new LinearLayout.LayoutParams(-1,dp(56)));
        shell.addView(divider(),new LinearLayout.LayoutParams(-1,dp(1)));

        HorizontalScrollView sh=new HorizontalScrollView(this);sh.setHorizontalScrollBarEnabled(false);sh.setFillViewport(false);
        strategyBar=new LinearLayout(this);strategyBar.setOrientation(LinearLayout.HORIZONTAL);strategyBar.setGravity(Gravity.CENTER_VERTICAL);strategyBar.setPadding(dp(13),dp(5),dp(13),dp(5));
        sh.addView(strategyBar,new HorizontalScrollView.LayoutParams(-2,-1));shell.addView(sh,new LinearLayout.LayoutParams(-1,dp(44)));
        buildStrategyBar();

        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.setBackgroundColor(Color.rgb(9,11,17));
        content=new LinearLayout(this);content.setOrientation(LinearLayout.VERTICAL);content.setPadding(dp(14),dp(9),dp(14),dp(24));
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
        bottom.addView(bottomNavItem("positions","◆","보유"),new LinearLayout.LayoutParams(0,dp(48),1));
        bottom.addView(bottomNavItem("pending","◌","대기"),new LinearLayout.LayoutParams(0,dp(48),1));
        bottom.addView(bottomNavItem("executions","↔","체결"),new LinearLayout.LayoutParams(0,dp(48),1));
        bottom.addView(bottomNavItem("research","◎","연구"),new LinearLayout.LayoutParams(0,dp(48),1));
        shell.addView(bottom,new LinearLayout.LayoutParams(-1,dp(54)));

        updateClock();
        render();
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
        panel.addView(manage);

        String brokerKey="stock".equals(market)?"kiwoom":"upbit";
        String brokerName="stock".equals(market)?"키움":"업비트";
        TextView apiTitle=text(brokerName+" API 연결",13);apiTitle.setTypeface(null,Typeface.BOLD);apiTitle.setTextColor(Color.rgb(168,176,198));apiTitle.setPadding(0,dp(18),0,dp(8));panel.addView(apiTitle);
        LinearLayout api=card();TextView brokerState=text(brokerStatusText(brokerKey),12);brokerState.setTextColor(Color.rgb(205,212,229));api.addView(brokerState);
        LinearLayout ar=new LinearLayout(this);ar.setOrientation(LinearLayout.HORIZONTAL);ar.setPadding(0,dp(12),0,0);
        ar.addView(button(brokerName+" 키 설정",true,this::apiSettings),new LinearLayout.LayoutParams(0,dp(44),1));
        LinearLayout.LayoutParams prp=new LinearLayout.LayoutParams(0,dp(44),1);prp.setMargins(dp(8),0,0,0);ar.addView(button("인증 검사",false,()->runBrokerProbe(false)),prp);api.addView(ar);panel.addView(api);

        TextView srvTitle=text("폰 원장 · PC 동기화",11);srvTitle.setTypeface(null,Typeface.BOLD);srvTitle.setTextColor(Color.rgb(145,170,200));srvTitle.setPadding(0,dp(16),0,dp(7));panel.addView(srvTitle);
        LinearLayout tools=card();
        JSONObject hubPub=copyCache().optJSONObject("hub_publish");
        int failed=hubPub==null?0:hubPub.optInt("failed",0);int okPub=hubPub==null?0:hubPub.optInt("ok",0);int preserved=hubPub==null?0:hubPub.optInt("preserved",0);
        String hubMsg=preserved>0?"기존 Hub 원장 "+preserved+"개 보존 · 새 빈 원장으로 덮어쓰기 차단":failed==0&&okPub>0?"폰 원장 → Phone Hub 동기화 정상":failed>0?"Phone Hub 동기화 실패 · PC 원장 조회에 반영되지 않음":"Phone Hub 동기화 준비 중";
        TextView hubState=text(hubMsg,11);hubState.setTextColor(failed>0?Color.rgb(255,119,133):preserved>0?Color.rgb(255,194,91):okPub>0?Color.rgb(86,214,170):Color.rgb(147,160,185));hubState.setPadding(dp(2),0,dp(2),dp(8));tools.addView(hubState);
        if(failed>0){TextView he=text(hubPub.optString("last_error",""),9);he.setTextColor(Color.rgb(180,151,158));he.setPadding(dp(2),0,dp(2),dp(8));tools.addView(he);}
        tools.addView(button("폰 서버 연결 토큰 등록",false,this::pair),new LinearLayout.LayoutParams(-1,dp(40)));
        LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,dp(40));bp.setMargins(0,dp(7),0,0);tools.addView(button("절전 예외 설정",false,()->{try{startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}catch(Exception ignored){}}),bp);
        LinearLayout.LayoutParams rp=new LinearLayout.LayoutParams(-1,dp(40));rp.setMargins(0,dp(7),0,0);tools.addView(button("실시간 엔진 재연결",false,this::restartFeeds),rp);panel.addView(tools);

        FrameLayout.LayoutParams pp=new FrameLayout.LayoutParams(width,-1,Gravity.END);overlay.addView(scroll,pp);
        drawerOverlay=overlay;root.addView(overlay,new FrameLayout.LayoutParams(-1,-1));
        TranslateAnimation anim=new TranslateAnimation(width,0,0,0);anim.setDuration(220);scroll.startAnimation(anim);
    }

    private void closeSettingsDrawer(){
        if(drawerOverlay==null)return;
        View old=drawerOverlay;drawerOverlay=null;
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
            TextView tab=strategyTab(item[1],active,()->{strategy=item[0];page="home";showShell();});
            int w="close".equals(item[0])||"gmo".equals(item[0])?dp(104):dp(82);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(w,dp(32));lp.setMargins(0,0,dp(5),0);strategyBar.addView(tab,lp);
        }
    }

    private void switchStrategy(int delta){
        String[] a="stock".equals(market)?new String[]{"at","ma","close","gmo"}:new String[]{"general","ma"};
        int idx=0;for(int i=0;i<a.length;i++)if(a[i].equals(strategy))idx=i;
        int n=idx+delta;if(n<0||n>=a.length)return;strategy=a[n];showShell();
    }

    private void refresh(boolean first){
        if(refreshRunning)return;
        refreshRunning=true;
        io.execute(()->{
            JSONObject packet=new JSONObject();JSONObject records=new JSONObject();
            try{
                LocalHubClient client=new LocalHubClient(this);
                try{packet.put("status",client.status());}catch(Exception e){packet.put("status",new JSONObject().put("ok",false));}
                try{packet.put("realtime",new RealtimeState(this).all());}catch(Exception e){packet.put("realtime",new JSONObject());}
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
                    localRecords=new JSONObject(lp.getString("records","{}"));
                    packet.put("local_records",localRecords);
                    packet.put("local_records_at",lp.getLong("updated_at",0L));
                }catch(Exception e){packet.put("local_records",new JSONObject());}
                if(!meaningfulTree(localRecords)){
                    for(String[] k:RECORDS){
                        try{records.put(k[3],client.record(k[0],k[1],k[2]));}
                        catch(Exception e){records.put(k[3],new JSONObject().put("ok",false));}
                    }
                    packet.put("using_hub_fallback",true);
                }else packet.put("using_hub_fallback",false);
                try{
                    android.content.SharedPreferences hp=getSharedPreferences("artitrade_hub_publish_v075",MODE_PRIVATE);
                    packet.put("hub_publish",new JSONObject()
                        .put("at",hp.getLong("at",0L)).put("ok",hp.getInt("ok",0))
                        .put("failed",hp.getInt("failed",0)).put("preserved",hp.getInt("preserved",0))
                        .put("legacy_preserved",hp.getBoolean("legacy_preserved",false))
                        .put("last_error",hp.getString("last_error","")));
                }catch(Exception e){packet.put("hub_publish",new JSONObject());}
                packet.put("records",records);packet.put("loaded_at",System.currentTimeMillis());
            }catch(Exception e){try{packet.put("refresh_error",e.getClass().getSimpleName()+": "+e.getMessage());}catch(Exception ignored){}}
            synchronized(cacheLock){cache=packet;}
            refreshRunning=false;
            main.post(()->{updateHeader();render();});
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
        statusText.setText((live?"실시간 LIVE":"실시간 대기")+sync+(stopped?" · 주문정지":""));
        statusText.setTextColor(stopped?Color.rgb(255,119,133):live?Color.rgb(62,224,181):Color.rgb(255,201,94));
    }

    private void updateClock(){
        if(clockText==null)return;
        SimpleDateFormat f=new SimpleDateFormat("MM-dd HH:mm:ss",Locale.KOREA);f.setTimeZone(TimeZone.getTimeZone("Asia/Seoul"));
        clockText.setText(f.format(new Date())+" KST");
    }

    private JSONObject copyCache(){
        synchronized(cacheLock){try{return new JSONObject(cache.toString());}catch(Exception e){return new JSONObject();}}
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

    private JSONObject localRecord(String key){
        JSONObject all=copyCache().optJSONObject("local_records");if(all==null)return new JSONObject();
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

    private JSONObject record(String key){
        JSONObject local=localRecord(key);
        if(meaningfulRecord(local))return local;
        JSONObject p=copyCache().optJSONObject("records");
        if(p!=null){
            JSONObject r=p.optJSONObject(key);
            JSONObject d=r==null?null:r.optJSONObject("data");
            if(meaningfulRecord(d))return d;
        }
        return local==null?new JSONObject():local;
    }

    private JSONObject ledger(String m,String s){
        if("coin".equals(m))return record("ma".equals(s)?"coinMaLedger":"coinGeneralLedger");
        if("close".equals(s))return record("stockCwLedger");
        JSONObject base=record("stockAtLedger");
        if("ma".equals(s)){
            JSONObject x=base.optJSONObject("ma_subledger");return x==null?new JSONObject():x;
        }
        if("gmo".equals(s)){
            JSONObject x=base.optJSONObject("gmo");return x==null?new JSONObject():x;
        }
        return base;
    }

    private JSONObject research(String m,String s){
        if("coin".equals(m))return record("ma".equals(s)?"coinMaResearch":"coinGeneralResearch");
        if("close".equals(s))return record("stockCwResearch");
        JSONObject at=record("stockAtResearch");
        if("ma".equals(s)){
            JSONObject nested=at.optJSONObject("ma");
            if(nested!=null)return nested;
            JSONObject ma=ledger("stock","ma");JSONArray items=ma.optJSONArray("research");
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
        int candidates=0,bought=0,confirmed=0,hits=0,closed=0;
    }

    private Stats stats(String m,String s){
        Stats st=new Stats();JSONObject l=ledger(m,s);
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

        int sell=0,known=0,wins=0;
        for(int i=0;i<st.executions.length();i++){JSONObject r=st.executions.optJSONObject(i);if(r==null)continue;
            String side=r.optString("side",r.optString("type","")).toUpperCase(Locale.ROOT);
            if(side.contains("SELL")||side.contains("매도")){sell++;double p=num(r,"net_profit","net_realized","net_pnl","pnl","profit");if(finite(p)){known++;if(p>0)wins++;}}
        }
        st.closed=sell;if(known>0)st.win=wins*100.0/known;

        JSONObject rs=research(m,s);JSONArray items=rs.optJSONArray("items");
        if(items==null&&"ma".equals(s))items=l.optJSONArray("research");
        if(items==null)items=new JSONArray();
        JSONArray filtered=new JSONArray();
        for(int i=0;i<items.length();i++){JSONObject r=items.optJSONObject(i);if(r!=null&&("coin".equals(m)||"close".equals(s)||belongs(r,m,s)))filtered.put(r);}
        st.research=filtered;st.candidates=filtered.length();
        for(int i=0;i<filtered.length();i++){JSONObject r=filtered.optJSONObject(i);if(r==null)continue;
            if(bool(r,"bought","purchased","buy_completed","filled"))st.bought++;
            Boolean hit=target(r);if(hit!=null){st.confirmed++;if(hit){st.hits++;}}
        }
        if(st.confirmed>0)st.target=st.hits*100.0/st.confirmed;
        double remote=num(rs,"target_hit_rate","target_rate","goal_rate","target_touch_rate");if(finite(remote))st.target=remote;
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
        Stats all=stats(m,stg),st=new Stats();
        st.positions=filterTf(all.positions,tf);st.pending=filterTf(all.pending,tf);st.executions=filterTf(all.executions,tf);st.research=filterTf(all.research,tf);
        JSONObject l=ledger(m,stg);
        if("stock".equals(m)&&"ma".equals(stg)){
            JSONObject pf=l.optJSONObject("performance_by_frame");JSONObject p=pf==null?null:pf.optJSONObject(tf);
            if(p!=null){
                st.realized=num(p,"realized_won","realized_pnl","realized");
                st.unrealized=num(p,"holding_won","unrealized_pnl","holding");
            }
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
        st.candidates=st.research.length();
        for(int i=0;i<st.research.length();i++){
            JSONObject r=st.research.optJSONObject(i);if(r==null)continue;
            if(bool(r,"bought","purchased","buy_completed","filled"))st.bought++;
            Boolean hit=target(r);if(hit!=null){st.confirmed++;if(hit)st.hits++;}
        }
        if(st.confirmed>0)st.target=st.hits*100.0/st.confirmed;
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
        else if("errors".equals(page))renderErrors();
        else renderMore();
    }

    private void renderHome(){
        Stats selected=stats(market,strategy);
        TextView title=text(strategyName(market,strategy)+" 대시보드",22);title.setTypeface(null,Typeface.BOLD);content.addView(title);
        TextView sub=text(("stock".equals(market)?"주식":"코인")+" · 선택 전략 계좌성과 / 실시간 / 연구지표",13);
        sub.setTextColor(Color.rgb(138,147,167));sub.setPadding(0,dp(3),0,dp(10));content.addView(sub);

        String feedName="stock".equals(market)?"키움 DIRECT · KOSPI/KOSDAQ":"업비트 DIRECT · KRW TOP35";
        content.addView(feedCard(market,feedName),new LinearLayout.LayoutParams(-1,dp(64)));

        LinearLayout hero=new LinearLayout(this);hero.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout pnl=card();
        TextView pl=text("누적손익",14);pl.setTextColor(Color.rgb(150,159,180));pnl.addView(pl);
        TextView pv=text(money(selected.total),36);pv.setTypeface(null,Typeface.BOLD);autoSingle(pv,20,36);paintProfit(pv,selected.total);pv.setPadding(0,dp(3),0,dp(8));pnl.addView(pv);
        LinearLayout split=new LinearLayout(this);split.setOrientation(LinearLayout.HORIZONTAL);
        addMini(split,"실현손익",money(selected.realized));addMini(split,"평가손익",money(selected.unrealized));pnl.addView(split);
        hero.addView(pnl,new LinearLayout.LayoutParams(0,dp(178),15));

        LinearLayout winCard=card();DonutMetricView win=new DonutMetricView(this);win.setMetric("승률",selected.win,pct(selected.win));winCard.addView(win,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout.LayoutParams wp=new LinearLayout.LayoutParams(0,dp(178),8);wp.setMargins(dp(8),0,0,0);hero.addView(winCard,wp);
        LinearLayout hitCard=card();DonutMetricView hit=new DonutMetricView(this);hit.setMetric("목표도달율",selected.target,pct(selected.target));hitCard.addView(hit,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout.LayoutParams hp=new LinearLayout.LayoutParams(0,dp(178),8);hp.setMargins(dp(8),0,0,0);hero.addView(hitCard,hp);
        LinearLayout.LayoutParams heroLp=new LinearLayout.LayoutParams(-1,dp(178));heroLp.setMargins(0,dp(10),0,0);content.addView(hero,heroLp);

        LinearLayout activity=new LinearLayout(this);activity.setOrientation(LinearLayout.HORIZONTAL);
        addKpi(activity,"보유",String.valueOf(selected.positions.length()));
        addKpi(activity,"대기",String.valueOf(selected.pending.length()));
        addKpi(activity,"완료거래",String.valueOf(selected.closed));
        addKpi(activity,"연구후보",String.valueOf(selected.candidates));
        LinearLayout.LayoutParams ap=new LinearLayout.LayoutParams(-1,dp(86));ap.setMargins(0,dp(9),0,0);content.addView(activity,ap);

        boolean frames=("coin".equals(market)||"at".equals(strategy)||"ma".equals(strategy));
        if(frames){
            TextView sec=text(strategyName(market,strategy)+" · 시간봉별 계좌성과",18);sec.setTypeface(null,Typeface.BOLD);
            sec.setTextColor(Color.rgb(220,224,235));sec.setPadding(0,dp(18),0,dp(8));content.addView(sec);
            TextView guide=text("5분 · 30분 · 1시간 · 일봉을 각각 독립 계좌로 시각화",12);guide.setTextColor(Color.rgb(128,138,160));guide.setPadding(0,0,0,dp(7));content.addView(guide);

            java.util.ArrayList<TimeframePerformanceGridView.Cell> cells=new java.util.ArrayList<>();
            for(String tf:new String[]{"5m","30m","60m","day"}){
                Stats q=statsTf(market,strategy,tf);
                cells.add(new TimeframePerformanceGridView.Cell(tfLabel(tf),q.total,q.realized,q.unrealized,q.win,q.target,q.positions.length(),q.pending.length()));
            }
            TimeframePerformanceGridView grid=new TimeframePerformanceGridView(this);grid.setCells(cells);
            content.addView(grid,new LinearLayout.LayoutParams(-1,dp(410)));
        }

        TextView trendTitle=text("실현손익 추세",18);trendTitle.setTypeface(null,Typeface.BOLD);trendTitle.setTextColor(Color.rgb(220,224,235));trendTitle.setPadding(0,dp(18),0,dp(8));content.addView(trendTitle);
        LinearLayout trendCard=card();PnlSparklineView trend=new PnlSparklineView(this);
        trend.setValues(trendValues(selected.executions),strategyName(market,strategy)+" · 누적 실현손익");
        trendCard.addView(trend,new LinearLayout.LayoutParams(-1,dp(150)));
        content.addView(trendCard,new LinearLayout.LayoutParams(-1,dp(170)));

        TextView researchTitle=text("연구 진행",18);researchTitle.setTypeface(null,Typeface.BOLD);researchTitle.setTextColor(Color.rgb(220,224,235));researchTitle.setPadding(0,dp(18),0,dp(8));content.addView(researchTitle);
        LinearLayout researchCard=card();
        LinearLayout rr=new LinearLayout(this);rr.setOrientation(LinearLayout.HORIZONTAL);
        addMini(rr,"후보",String.valueOf(selected.candidates));addMini(rr,"매수완료",String.valueOf(selected.bought));addMini(rr,"판정완료",String.valueOf(selected.confirmed));researchCard.addView(rr);
        MetricProgressView rg=new MetricProgressView(this);rg.setMetric("목표도달율",selected.target);
        LinearLayout.LayoutParams rgp=new LinearLayout.LayoutParams(-1,dp(46));rgp.setMargins(0,dp(10),0,0);researchCard.addView(rg,rgp);
        content.addView(researchCard,new LinearLayout.LayoutParams(-1,dp(112)));
    }

    private View feedCard(String m,String title){
        LinearLayout c=new LinearLayout(this);c.setOrientation(LinearLayout.HORIZONTAL);c.setGravity(Gravity.CENTER_VERTICAL);c.setPadding(dp(13),0,dp(13),0);
        c.setBackground(bg(Color.rgb(15,18,27),Color.rgb(38,43,58),14));
        JSONObject rt=copyCache().optJSONObject("realtime");JSONObject r=rt==null?null:rt.optJSONObject(m);boolean on=r!=null&&r.optBoolean("connected",false);
        TextView dot=text("●",10);dot.setTextColor(on?Color.rgb(86,214,170):Color.rgb(255,176,92));c.addView(dot,new LinearLayout.LayoutParams(dp(20),dp(34)));
        LinearLayout copy=new LinearLayout(this);copy.setOrientation(LinearLayout.VERTICAL);copy.setGravity(Gravity.CENTER_VERTICAL);
        TextView t=text(title,13);t.setTypeface(null,Typeface.BOLD);t.setTextColor(Color.rgb(220,226,239));copy.addView(t);
        String d=r==null?"연결 확인 중":r.optString("detail","");int n=r==null?0:r.optInt("subscribed",0);
        TextView x=text((on?"실시간 수신":"재연결 중")+(n>0?" · "+n+"종목":"")+(d.isEmpty()?"":" · "+d),11);x.setTextColor(Color.rgb(120,130,151));copy.addView(x);
        c.addView(copy,new LinearLayout.LayoutParams(0,dp(42),1));
        TextView badge=text(on?"LIVE":"WAIT",11);badge.setGravity(Gravity.CENTER);badge.setTypeface(null,Typeface.BOLD);badge.setTextColor(on?Color.rgb(86,214,170):Color.rgb(255,176,92));badge.setBackground(bg(Color.rgb(22,26,36),Color.rgb(48,54,70),10));c.addView(badge,new LinearLayout.LayoutParams(dp(54),dp(28)));
        return c;
    }

    private void addMini(LinearLayout row,String label,String value){
        LinearLayout c=new LinearLayout(this);c.setOrientation(LinearLayout.VERTICAL);c.setGravity(Gravity.CENTER_VERTICAL);
        TextView a=text(label,12);a.setTextColor(Color.rgb(142,152,173));c.addView(a,new LinearLayout.LayoutParams(-1,dp(22)));
        TextView b=text(value,19);b.setTypeface(null,Typeface.BOLD);autoSingle(b,14,19);b.setTextColor(Color.rgb(235,238,246));c.addView(b,new LinearLayout.LayoutParams(-1,dp(30)));
        row.addView(c,new LinearLayout.LayoutParams(0,dp(54),1));
    }

    private void addKpi(LinearLayout row,String label,String value){
        LinearLayout c=card();c.setGravity(Gravity.CENTER_VERTICAL);
        TextView a=text(label,12);a.setTextColor(Color.rgb(142,152,173));c.addView(a,new LinearLayout.LayoutParams(-1,dp(22)));
        TextView b=text(value,24);b.setTypeface(null,Typeface.BOLD);autoSingle(b,16,24);b.setTextColor(Color.rgb(240,242,248));c.addView(b,new LinearLayout.LayoutParams(-1,dp(38)));
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(82),1);if(row.getChildCount()>0)p.setMargins(dp(8),0,0,0);row.addView(c,p);
    }

    private void renderList(String title,JSONArray a,String kind){
        TextView h=text(title+"  ·  "+a.length()+"건",22);h.setTypeface(null,Typeface.BOLD);h.setPadding(0,0,0,dp(8));content.addView(h);
        if(a.length()==0){empty("기록 없음");return;}
        for(int i=0;i<Math.min(100,a.length());i++){JSONObject r=a.optJSONObject(i);if(r==null)continue;LinearLayout c=card();
            TextView n=text(nameOf(r),18);n.setTypeface(null,Typeface.BOLD);c.addView(n);
            String when=timeOf(r);TextView tm=text(refLabel()+" "+when,12);tm.setTextColor(Color.rgb(135,158,187));tm.setPadding(0,dp(2),0,0);c.addView(tm);
            TextView why=text("근거: "+reasonOf(r),13);why.setTextColor(Color.rgb(191,209,228));why.setPadding(0,dp(4),0,0);c.addView(why);
            double v=Double.NaN;if("positions".equals(kind))v=num(r,"net_pnl","unrealized_pnl","pnl","paper_pnl");else if("executions".equals(kind))v=num(r,"net_profit","net_realized","net_pnl","pnl","profit");else v=num(r,"score","target_price","price");
            if(finite(v)){TextView val=text(("pending".equals(kind)?"점수/가격 ":"손익 ")+number(v),16);paintProfit(val,"pending".equals(kind)?0:v);val.setPadding(0,dp(4),0,0);c.addView(val);}
            LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.setMargins(0,0,0,dp(7));content.addView(c,p);
        }
    }

    private void renderResearch(){
        Stats st=stats(market,strategy);
        TextView h=text(strategyName(market,strategy)+" 연구이력",22);h.setTypeface(null,Typeface.BOLD);content.addView(h);
        TextView sub=text("후보 → 매수 → 판정 → 목표도달 흐름과 점수대별 결과",13);sub.setTextColor(Color.rgb(135,160,190));sub.setPadding(0,dp(3),0,dp(8));content.addView(sub);

        LinearLayout top=new LinearLayout(this);top.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout kpis=card();LinearLayout kr=new LinearLayout(this);kr.setOrientation(LinearLayout.HORIZONTAL);addMini(kr,"후보",String.valueOf(st.candidates));addMini(kr,"매수완료",String.valueOf(st.bought));addMini(kr,"판정완료",String.valueOf(st.confirmed));kpis.addView(kr);TextView note=text("최근 연구 데이터 기준",9);note.setTextColor(Color.rgb(127,151,181));note.setPadding(0,dp(7),0,0);kpis.addView(note);
        top.addView(kpis,new LinearLayout.LayoutParams(0,dp(112),2));
        LinearLayout dc=card();DonutMetricView donut=new DonutMetricView(this);donut.setMetric("목표도달율",st.target,pct(st.target));dc.addView(donut,new LinearLayout.LayoutParams(-1,0,1));LinearLayout.LayoutParams dcp=new LinearLayout.LayoutParams(0,dp(112),1);dcp.setMargins(dp(7),0,0,0);top.addView(dc,dcp);
        content.addView(top,new LinearLayout.LayoutParams(-1,dp(112)));

        TextView m=text("점수대별 목표 도달 지도",17);m.setTypeface(null,Typeface.BOLD);m.setTextColor(Color.rgb(157,181,208));m.setPadding(0,dp(13),0,dp(6));content.addView(m);
        int[][] bands="gmo".equals(strategy)?new int[][]{{40,60},{61,80},{81,100}}:new int[][]{{0,20},{21,40},{41,60},{61,80},{81,100}};
        HorizontalScrollView bandScroll=new HorizontalScrollView(this);bandScroll.setHorizontalScrollBarEnabled(false);
        LinearLayout br=new LinearLayout(this);br.setOrientation(LinearLayout.HORIZONTAL);br.setPadding(0,0,dp(4),0);
        for(int[] band:bands){
            int judged=0,hits=0;
            for(int i=0;i<st.research.length();i++){
                JSONObject r=st.research.optJSONObject(i);if(r==null)continue;
                double sc=num(r,"score","original_score","signal_score");if(!finite(sc)||sc<band[0]||sc>band[1])continue;
                Boolean hit=target(r);if(hit!=null){judged++;if(hit)hits++;}
            }
            double rate=judged>0?hits*100.0/judged:Double.NaN;
            LinearLayout c=card();TextView t=text(band[0]+"~"+band[1]+"점",14);t.setTypeface(null,Typeface.BOLD);c.addView(t);
            MetricProgressView pv=new MetricProgressView(this);pv.setMetric("목표도달",rate);c.addView(pv,new LinearLayout.LayoutParams(-1,dp(34)));
            TextView f=text(judged+"건 판정 · "+hits+"건 도달",11);f.setTextColor(Color.rgb(124,149,179));c.addView(f);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(dp(112),dp(82));if(br.getChildCount()>0)lp.setMargins(dp(5),0,0,0);br.addView(c,lp);
        }
        bandScroll.addView(br,new HorizontalScrollView.LayoutParams(-2,dp(82)));
        content.addView(bandScroll,new LinearLayout.LayoutParams(-1,dp(82)));

        TextView hist=text("최근 연구 기록",17);hist.setTypeface(null,Typeface.BOLD);hist.setTextColor(Color.rgb(157,181,208));hist.setPadding(0,dp(13),0,dp(6));content.addView(hist);
        if(st.research.length()==0){empty("연구이력 없음");return;}
        for(int i=0;i<Math.min(80,st.research.length());i++){
            JSONObject r=st.research.optJSONObject(i);if(r==null)continue;LinearLayout c=card();
            LinearLayout head=new LinearLayout(this);head.setOrientation(LinearLayout.HORIZONTAL);
            TextView n=text(nameOf(r),17);n.setTypeface(null,Typeface.BOLD);head.addView(n,new LinearLayout.LayoutParams(0,dp(28),1));
            double sc=num(r,"score","original_score","signal_score");TextView score=text(finite(sc)?number(sc)+"점":"—",15);score.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);score.setTextColor(Color.rgb(255,201,94));head.addView(score,new LinearLayout.LayoutParams(dp(70),dp(28)));c.addView(head);
            Boolean hit=target(r);String verdict=hit==null?"판정 중":hit?"목표 도달":"미도달";TextView v=text(verdict+" · "+refLabel()+" "+timeOf(r),12);v.setTextColor(hit==null?Color.rgb(156,179,205):hit?Color.rgb(62,224,181):Color.rgb(255,145,155));c.addView(v);
            TextView why=text("근거: "+reasonOf(r),13);why.setTextColor(Color.rgb(176,197,220));why.setPadding(0,dp(3),0,0);c.addView(why);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(6));content.addView(c,lp);
        }
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
        for(int i=0;i<Math.min(180,all.length());i++){
            JSONObject r=all.optJSONObject(i);if(r==null)continue;LinearLayout c=card();
            TextView n=text(r.optString("stage",r.optString("error_type",r.optString("code","오류 기록"))),12);n.setTypeface(null,Typeface.BOLD);n.setTextColor(Color.rgb(255,145,155));c.addView(n);
            TextView d=text(r.optString("message",r.optString("detail","상세 미기록")),10);d.setPadding(0,dp(4),0,0);c.addView(d);
            TextView t=text(formatKst(r.opt("at")),9);t.setTextColor(Color.rgb(130,153,182));c.addView(t);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(7));content.addView(c,lp);
        }
    }

    private JSONObject operationSettings(){
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
        JSONObject root=new JSONObject();
        try{
            JSONObject st=new JSONObject(),co=new JSONObject();
            for(String[] x:strategyList("stock"))st.put(x[0],new JSONObject().put("seed",2000000).put("max_positions",4));
            for(String[] x:strategyList("coin"))co.put(x[0],new JSONObject().put("seed",2000000).put("max_positions",4));
            root.put("stock",st).put("coin",co);
        }catch(Exception ignored){}
        return root;
    }

    private LinearLayout operationCard(String m,String stg,String label,JSONObject settings){
        JSONObject ms=settings.optJSONObject(m);JSONObject cfg=ms==null?null:ms.optJSONObject(stg);
        long seed=cfg==null?2000000:cfg.optLong("seed",2000000);long maxp=cfg==null?4:cfg.optLong("max_positions",4);
        LinearLayout c=card();TextView n=text(label,14);n.setTypeface(null,Typeface.BOLD);c.addView(n);
        TextView scope=text(("at".equals(stg)||"ma".equals(stg)||"coin".equals(m))?"5분 · 30분 · 1시간 · 일봉 계좌에 동일 적용":"전략 통합 계좌",9);scope.setTextColor(Color.rgb(134,158,188));scope.setPadding(0,dp(2),0,dp(6));c.addView(scope);
        LinearLayout fields=new LinearLayout(this);fields.setOrientation(LinearLayout.HORIZONTAL);
        EditText seedEdit=new EditText(this);seedEdit.setText(String.valueOf(seed));seedEdit.setTextColor(Color.WHITE);seedEdit.setTextSize(12);seedEdit.setHint("운용시드");seedEdit.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);seedEdit.setSingleLine(true);seedEdit.setBackground(bg(Color.rgb(10,22,38),Color.rgb(47,70,96),9));seedEdit.setPadding(dp(9),0,dp(9),0);
        EditText maxEdit=new EditText(this);maxEdit.setText(String.valueOf(maxp));maxEdit.setTextColor(Color.WHITE);maxEdit.setTextSize(12);maxEdit.setHint("최대보유");maxEdit.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);maxEdit.setSingleLine(true);maxEdit.setBackground(bg(Color.rgb(10,22,38),Color.rgb(47,70,96),9));maxEdit.setPadding(dp(9),0,dp(9),0);
        fields.addView(seedEdit,new LinearLayout.LayoutParams(0,dp(42),2));LinearLayout.LayoutParams mx=new LinearLayout.LayoutParams(0,dp(42),1);mx.setMargins(dp(7),0,0,0);fields.addView(maxEdit,mx);c.addView(fields);
        TextView base=text("기본 2,000,000원 · 최대 4종목",9);base.setTextColor(Color.rgb(108,135,166));base.setPadding(0,dp(5),0,0);c.addView(base);
        operationFields.put(m+"."+stg,new EditText[]{seedEdit,maxEdit});
        return c;
    }

    private void saveOperationAll(){
        JSONObject all=operationSettings();
        try{
            JSONObject marketObj=all.optJSONObject(market);if(marketObj==null){marketObj=new JSONObject();all.put(market,marketObj);}
            for(String[] x:strategyList(market)){
                EditText[] fields=operationFields.get(market+"."+x[0]);if(fields==null)continue;
                long seed=Long.parseLong(fields[0].getText().toString().replace(",","").trim());
                long max=Long.parseLong(fields[1].getText().toString().trim());
                if(seed<10000||seed>2000000000L||max<1||max>100)throw new IllegalArgumentException("허용범위: 시드 1만원~20억원, 최대보유 1~100");
                marketObj.put(x[0],new JSONObject().put("seed",seed).put("max_positions",max));
            }
            getSharedPreferences("unified_operation_v074",MODE_PRIVATE).edit().putString("settings",all.toString()).apply();
            Intent i=new Intent(this,UnifiedTradingService.class);i.setAction(UnifiedTradingService.APPLY_OPERATION);i.putExtra("settings",all.toString());
            if(Build.VERSION.SDK_INT>=26)startForegroundService(i);else startService(i);
            main.postDelayed(()->refresh(false),1200);
        }catch(Exception e){
            new AlertDialog.Builder(this).setTitle("운용 설정 확인").setMessage(String.valueOf(e.getMessage())).setPositiveButton("확인",null).show();
        }
    }

    private void renderOperation(){
        operationFields.clear();
        TextView h=text(("stock".equals(market)?"주식":"코인")+" 통합 운용",18);h.setTypeface(null,Typeface.BOLD);content.addView(h);
        TextView dsc=text("전략별 시드와 최대 보유종목을 한 화면에서 관리 · 기본값 2,000,000원 / 4종목",10);dsc.setTextColor(Color.rgb(134,159,189));dsc.setPadding(0,dp(3),0,dp(9));content.addView(dsc);
        JSONObject settings=operationSettings();
        LinearLayout grid=new LinearLayout(this);grid.setOrientation(LinearLayout.HORIZONTAL);
        for(String[] x:strategyList(market)){
            LinearLayout c=operationCard(market,x[0],x[1],settings);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(132),1);if(grid.getChildCount()>0)lp.setMargins(dp(7),0,0,0);grid.addView(c,lp);
        }
        content.addView(grid,new LinearLayout.LayoutParams(-1,dp(132)));
        Button save=button("전체 전략 운용설정 저장",true,this::saveOperationAll);LinearLayout.LayoutParams sv=new LinearLayout.LayoutParams(-1,dp(42));sv.setMargins(0,dp(8),0,0);content.addView(save,sv);

        try{
            JSONObject r=new JSONObject(getSharedPreferences("artitrade_operation_result",MODE_PRIVATE).getString("result","{}"));
            if(r.length()>0){TextView st=text(r.optBoolean("ok",false)?"엔진 적용 완료":"일부 엔진 적용 실패 · 오류 탭 확인",9);st.setTextColor(r.optBoolean("ok",false)?Color.rgb(62,224,181):Color.rgb(255,119,133));st.setPadding(0,dp(4),0,0);content.addView(st);}
        }catch(Exception ignored){}

        JSONObject live=copyCache().optJSONObject("live_orders");boolean armed=live!=null&&live.optBoolean(market,false);
        LinearLayout ctrl=card();TextView t=text("S21+ 메인 실행권한",13);t.setTypeface(null,Typeface.BOLD);ctrl.addView(t);
        LinearLayout dr=new LinearLayout(this);dr.setOrientation(LinearLayout.HORIZONTAL);dr.setPadding(0,dp(8),0,0);dr.addView(button("PHONE MAIN",false,()->setDesired("phone")),new LinearLayout.LayoutParams(0,dp(40),1));LinearLayout.LayoutParams offp=new LinearLayout.LayoutParams(0,dp(40),1);offp.setMargins(dp(7),0,0,0);dr.addView(button("자동매매 정지",false,()->setDesired("off")),offp);ctrl.addView(dr);
        TextView lt=text("실거래 자동주문",13);lt.setTypeface(null,Typeface.BOLD);lt.setPadding(0,dp(12),0,0);ctrl.addView(lt);
        LinearLayout lr=new LinearLayout(this);lr.setOrientation(LinearLayout.HORIZONTAL);lr.setPadding(0,dp(8),0,0);lr.addView(button("실거래 ON",armed,()->setLive(true)),new LinearLayout.LayoutParams(0,dp(40),1));LinearLayout.LayoutParams of=new LinearLayout.LayoutParams(0,dp(40),1);of.setMargins(dp(7),0,0,0);lr.addView(button("실거래 OFF",!armed,()->setLive(false)),of);ctrl.addView(lr);
        TextView hint=text(armed?"실거래 ON · 최신 EXE 전략 신호가 실제 주문으로 연결됨":"실거래 OFF · 시세/전략은 계속 실행, 실제 주문만 차단",10);hint.setTextColor(Color.rgb(141,165,194));hint.setPadding(0,dp(8),0,0);ctrl.addView(hint);
        LinearLayout.LayoutParams cp=new LinearLayout.LayoutParams(-1,-2);cp.setMargins(0,dp(10),0,0);content.addView(ctrl,cp);
    }

    private void renderMore(){
        TextView h=text("도구",18);h.setTypeface(null,Typeface.BOLD);h.setPadding(0,0,0,dp(8));content.addView(h);
        LinearLayout c=card();c.addView(text("키움 · 업비트 API",13));Button api=button("직접 API 키 설정",true,this::apiSettings);LinearLayout.LayoutParams ap=new LinearLayout.LayoutParams(-1,dp(42));ap.setMargins(0,dp(8),0,0);c.addView(api,ap);content.addView(c);
        LinearLayout p=card();p.addView(text("폰 서버",13));Button pair=button("연결 토큰 등록",true,this::pair);LinearLayout.LayoutParams pp=new LinearLayout.LayoutParams(-1,dp(42));pp.setMargins(0,dp(8),0,0);p.addView(pair,pp);LinearLayout.LayoutParams pc=new LinearLayout.LayoutParams(-1,-2);pc.setMargins(0,dp(8),0,0);content.addView(p,pc);
        LinearLayout b=card();b.addView(text("백그라운드",13));Button bat=button("절전 예외 설정 열기",false,()->{try{startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));}catch(Exception ignored){}});LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,dp(42));bp.setMargins(0,dp(8),0,0);b.addView(bat,bp);LinearLayout.LayoutParams bc=new LinearLayout.LayoutParams(-1,-2);bc.setMargins(0,dp(8),0,0);content.addView(b,bc);
    }

    private void empty(String s){TextView e=text(s,12);e.setGravity(Gravity.CENTER);e.setTextColor(Color.rgb(112,137,167));e.setPadding(0,dp(40),0,dp(40));content.addView(e);}

    private String strategyName(String m,String s){
        if("coin".equals(m))return "ma".equals(s)?"MA":"기준봉";
        if("ma".equals(s))return "MA";if("close".equals(s))return "종가베팅";if("gmo".equals(s))return "수급매매";return "기준봉";
    }

    private String refLabel(){return "gmo".equals(strategy)?"사건일":"close".equals(strategy)?"매수시각":"기준시각";}

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

    private String timeOf(JSONObject r){
        String[] keys="gmo".equals(strategy)?new String[]{"event_date","gmo_event_date","event_day"}:"close".equals(strategy)?new String[]{"buy_at","entry_at","entry_ts","buy_ts","first_buy_at"}:new String[]{"ref_ts","reference_ts","reference_at","selected_ref_at","bar_at","base_candle_time","signal_at","at","ts"};
        for(String k:keys)if(r.has(k)&&!r.isNull(k))return formatKst(r.opt(k));return "미기록";
    }

    private String formatKst(Object v){
        if(v==null||v==JSONObject.NULL)return "미기록";String raw=String.valueOf(v).trim();if(raw.isEmpty())return "미기록";
        try{long n=Long.parseLong(raw.replace(".0",""));if(raw.length()==10)n*=1000;SimpleDateFormat f=new SimpleDateFormat("yyyy-MM-dd HH:mm",Locale.KOREA);f.setTimeZone(TimeZone.getTimeZone("Asia/Seoul"));return f.format(new Date(n));}catch(Exception ignored){}
        try{Instant i=raw.endsWith("Z")?Instant.parse(raw):OffsetDateTime.parse(raw).toInstant();return DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(ZoneId.of("Asia/Seoul")).format(i);}catch(Exception ignored){}
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

    private void pair(){
        EditText input=new EditText(this);input.setSingleLine(true);input.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);input.setHint("폰 서버 64자리 연결 토큰");
        new AlertDialog.Builder(this).setTitle("폰 서버 연결").setView(input).setNegativeButton("취소",null).setPositiveButton("저장",(d,w)->{try{new LocalHubClient(this).storePairingToken(input.getText().toString().trim());refresh(false);}catch(Exception e){new AlertDialog.Builder(this).setMessage("64자리 연결 토큰을 확인하십시오.").setPositiveButton("확인",null).show();}}).show();
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

    @Override protected void onResume(){super.onResume();main.removeCallbacks(periodic);main.postDelayed(periodic,REFRESH_MS);}
    @Override protected void onPause(){main.removeCallbacks(periodic);super.onPause();}
    @Override protected void onDestroy(){main.removeCallbacks(periodic);io.shutdownNow();super.onDestroy();}
}
