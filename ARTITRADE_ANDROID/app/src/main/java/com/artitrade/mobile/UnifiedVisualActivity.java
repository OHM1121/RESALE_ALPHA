package com.artitrade.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
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
        return t;
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
        b.setTextColor(active?Color.rgb(4,32,25):Color.rgb(203,220,239));
        b.setBackground(bg(active?Color.rgb(62,224,181):Color.rgb(18,36,58),
            active?Color.rgb(62,224,181):Color.rgb(43,67,97),10));
        b.setPadding(dp(10),0,dp(10),0);
        b.setMinHeight(0);b.setMinimumHeight(0);
        b.setOnClickListener(v->action.run());
        return b;
    }

    private LinearLayout card(){
        LinearLayout c=new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(13),dp(10),dp(13),dp(10));
        c.setBackground(bg(Color.rgb(16,29,49),Color.rgb(38,59,86),14));
        return c;
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
        LinearLayout shell=new LinearLayout(this);shell.setOrientation(LinearLayout.VERTICAL);shell.setBackgroundColor(Color.rgb(7,16,28));
        root.addView(shell,new FrameLayout.LayoutParams(-1,-1));

        LinearLayout top=new LinearLayout(this);top.setOrientation(LinearLayout.HORIZONTAL);top.setGravity(Gravity.CENTER_VERTICAL);top.setPadding(dp(12),dp(6),dp(10),dp(6));
        LinearLayout brandBox=new LinearLayout(this);brandBox.setOrientation(LinearLayout.VERTICAL);brandBox.setGravity(Gravity.CENTER_VERTICAL);
        TextView brand=text("ARTITRADE",18);brand.setTypeface(null,Typeface.BOLD);brand.setLetterSpacing(.08f);brandBox.addView(brand);
        statusText=text("데이터 확인 중",9);statusText.setTextColor(Color.rgb(153,177,205));brandBox.addView(statusText);
        top.addView(brandBox,new LinearLayout.LayoutParams(0,dp(42),1));

        Button stock=button("주식","stock".equals(market),()->{market="stock";strategy="at";page="home";showShell();});
        Button coin=button("코인","coin".equals(market),()->{market="coin";strategy="general";page="home";showShell();});
        top.addView(stock,new LinearLayout.LayoutParams(dp(64),dp(34)));
        LinearLayout.LayoutParams cp=new LinearLayout.LayoutParams(dp(64),dp(34));cp.setMargins(dp(5),0,0,0);top.addView(coin,cp);
        clockText=text("",9);clockText.setTextColor(Color.rgb(139,164,194));clockText.setGravity(Gravity.CENTER);LinearLayout.LayoutParams tp=new LinearLayout.LayoutParams(dp(92),dp(34));tp.setMargins(dp(7),0,0,0);top.addView(clockText,tp);
        Button menu=button("☰",false,this::openSettingsDrawer);LinearLayout.LayoutParams mp=new LinearLayout.LayoutParams(dp(44),dp(34));mp.setMargins(dp(4),0,0,0);top.addView(menu,mp);
        shell.addView(top,new LinearLayout.LayoutParams(-1,dp(54)));

        HorizontalScrollView sh=new HorizontalScrollView(this);sh.setHorizontalScrollBarEnabled(false);
        strategyBar=new LinearLayout(this);strategyBar.setOrientation(LinearLayout.HORIZONTAL);strategyBar.setPadding(dp(10),dp(5),dp(10),dp(5));
        sh.addView(strategyBar,new HorizontalScrollView.LayoutParams(-2,-1));shell.addView(sh,new LinearLayout.LayoutParams(-1,dp(44)));
        buildStrategyBar();

        HorizontalScrollView nh=new HorizontalScrollView(this);nh.setHorizontalScrollBarEnabled(false);
        LinearLayout nav=new LinearLayout(this);nav.setOrientation(LinearLayout.HORIZONTAL);nav.setPadding(dp(10),dp(4),dp(10),dp(4));
        String[][] navs={{"home","대시보드"},{"positions","보유"},{"pending","대기"},{"executions","체결"},{"research","연구"},{"operation","운용"},{"errors","오류"}};
        for(String[] n:navs){
            Button b=button(n[1],n[0].equals(page),()->{page=n[0];showShell();});
            int width="home".equals(n[0])?dp(92):dp(76);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(width,dp(34));lp.setMargins(0,0,dp(5),0);nav.addView(b,lp);
        }
        nh.addView(nav,new HorizontalScrollView.LayoutParams(-2,-1));shell.addView(nh,new LinearLayout.LayoutParams(-1,dp(42)));

        ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.setBackgroundColor(Color.rgb(7,16,28));
        content=new LinearLayout(this);content.setOrientation(LinearLayout.VERTICAL);content.setPadding(dp(12),dp(10),dp(12),dp(20));
        scroll.addView(content,new ScrollView.LayoutParams(-1,-2));
        scroll.setOnTouchListener((v,e)->{
            if("operation".equals(page)||"errors".equals(page))return false;
            if(e.getAction()==MotionEvent.ACTION_DOWN)swipeX=e.getX();
            else if(e.getAction()==MotionEvent.ACTION_UP&&!Float.isNaN(swipeX)){
                float dx=e.getX()-swipeX;swipeX=Float.NaN;
                if(Math.abs(dx)>dp(70)){switchStrategy(dx<0?1:-1);return true;}
            }
            return false;
        });
        shell.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
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
        TextView title=text("설정",21);title.setTypeface(null,Typeface.BOLD);head.addView(title,new LinearLayout.LayoutParams(0,dp(42),1));
        head.addView(button("닫기",false,this::closeSettingsDrawer),new LinearLayout.LayoutParams(dp(70),dp(36)));panel.addView(head);

        TextView apiTitle=text("API 연결 상태",11);apiTitle.setTypeface(null,Typeface.BOLD);apiTitle.setTextColor(Color.rgb(145,170,200));apiTitle.setPadding(0,dp(16),0,dp(7));panel.addView(apiTitle);
        LinearLayout api=card();api.addView(text(brokerStatusText("kiwoom"),11));TextView up=text(brokerStatusText("upbit"),11);up.setPadding(0,dp(7),0,0);api.addView(up);
        LinearLayout ar=new LinearLayout(this);ar.setOrientation(LinearLayout.HORIZONTAL);ar.setPadding(0,dp(10),0,0);
        ar.addView(button("API 키 설정",true,this::apiSettings),new LinearLayout.LayoutParams(0,dp(40),1));
        LinearLayout.LayoutParams prp=new LinearLayout.LayoutParams(0,dp(40),1);prp.setMargins(dp(7),0,0,0);ar.addView(button("인증 검사",false,()->runBrokerProbe(false)),prp);api.addView(ar);panel.addView(api);

        TextView srvTitle=text("폰 서버 · 백그라운드",11);srvTitle.setTypeface(null,Typeface.BOLD);srvTitle.setTextColor(Color.rgb(145,170,200));srvTitle.setPadding(0,dp(16),0,dp(7));panel.addView(srvTitle);
        LinearLayout tools=card();tools.addView(button("폰 서버 연결 토큰 등록",false,this::pair),new LinearLayout.LayoutParams(-1,dp(40)));
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
        if("upbit".equals(which)&&p.optBoolean("ip_hint",false))msg+=" · 허용 IP 확인";
        return name+" · "+masked+" · "+msg;
    }


    private View space(){View v=new View(this);v.setBackgroundColor(Color.TRANSPARENT);return v;}

    private void buildStrategyBar(){
        if(strategyBar==null)return;
        strategyBar.removeAllViews();
        String[][] list="stock".equals(market)
            ?new String[][]{{"at","기준봉"},{"ma","MA"},{"close","종가베팅"},{"gmo","수급매매"}}
            :new String[][]{{"general","기준봉"},{"ma","MA"}};
        for(String[] s:list){
            Button b=button(s[1],s[0].equals(strategy),()->{strategy=s[0];page="home";showShell();});
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(dp(122),dp(33));lp.setMargins(0,0,dp(6),0);strategyBar.addView(b,lp);
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

                for(String[] k:RECORDS){
                    try{records.put(k[3],client.record(k[0],k[1],k[2]));}
                    catch(Exception e){records.put(k[3],new JSONObject().put("ok",false));}
                }
                packet.put("records",records);packet.put("loaded_at",System.currentTimeMillis());
            }catch(Exception e){try{packet.put("refresh_error",e.getClass().getSimpleName()+": "+e.getMessage());}catch(Exception ignored){}}
            synchronized(cacheLock){cache=packet;}
            refreshRunning=false;
            main.post(()->{updateHeader();render();});
        });
    }

    private void updateHeader(){
        if(statusText==null)return;
        JSONObject c=copyCache();JSONObject st=c.optJSONObject("status");
        boolean ok=st!=null&&st.optBoolean("ok",false);
        JSONObject rt=c.optJSONObject("strategy_runtime");
        JSONObject halted=rt==null?null:rt.optJSONObject("halted");
        String h="";
        if(halted!=null&&!halted.isNull(market)&&halted.optJSONObject(market)!=null)h=" · 실거래 자동정지";
        statusText.setText((ok?"폰 서버 연결":"폰 서버 미연결")+h);
        statusText.setTextColor(ok?Color.rgb(62,224,181):Color.rgb(255,119,133));
    }

    private void updateClock(){
        if(clockText==null)return;
        SimpleDateFormat f=new SimpleDateFormat("MM-dd HH:mm:ss",Locale.KOREA);f.setTimeZone(TimeZone.getTimeZone("Asia/Seoul"));
        clockText.setText(f.format(new Date())+" KST");
    }

    private JSONObject copyCache(){
        synchronized(cacheLock){try{return new JSONObject(cache.toString());}catch(Exception e){return new JSONObject();}}
    }

    private JSONObject record(String key){
        JSONObject p=copyCache().optJSONObject("records");if(p==null)return new JSONObject();
        JSONObject r=p.optJSONObject(key);if(r==null)return new JSONObject();
        JSONObject d=r.optJSONObject("data");return d==null?new JSONObject():d;
    }

    private JSONObject ledger(String m,String s){
        if("coin".equals(m))return record("ma".equals(s)?"coinMaLedger":"coinGeneralLedger");
        if("close".equals(s))return record("stockCwLedger");
        JSONObject base=record("stockAtLedger");
        if("ma".equals(s)){
            JSONObject x=base.optJSONObject("ma_subledger");return x==null?new JSONObject():x;
        }
        return base;
    }

    private JSONObject research(String m,String s){
        if("coin".equals(m))return record("ma".equals(s)?"coinMaResearch":"coinGeneralResearch");
        if("close".equals(s))return record("stockCwResearch");
        if("ma".equals(s)){
            JSONObject ma=ledger("stock","ma");JSONArray items=ma.optJSONArray("research");
            try{return new JSONObject().put("items",items==null?new JSONArray():items);}
            catch(Exception e){return new JSONObject();}
        }
        return record("stockAtResearch");
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
        TextView title=text("대시보드  ·  "+strategyName(market,strategy),18);title.setTypeface(null,Typeface.BOLD);content.addView(title);
        TextView sub=text("전략 성과를 계좌·시간봉·연구결과 기준으로 시각화",10);sub.setTextColor(Color.rgb(130,155,185));sub.setPadding(0,dp(2),0,dp(8));content.addView(sub);

        LinearLayout feeds=new LinearLayout(this);feeds.setOrientation(LinearLayout.HORIZONTAL);
        feeds.addView(feedCard("stock","키움 직접수신"),new LinearLayout.LayoutParams(0,dp(60),1));
        LinearLayout.LayoutParams fp=new LinearLayout.LayoutParams(0,dp(60),1);fp.setMargins(dp(7),0,0,0);feeds.addView(feedCard("coin","업비트 직접수신"),fp);
        content.addView(feeds,new LinearLayout.LayoutParams(-1,dp(60)));

        LinearLayout hero=new LinearLayout(this);hero.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout pnl=card();TextView pl=text("선택 전략 누적손익",10);pl.setTextColor(Color.rgb(135,160,190));pnl.addView(pl);
        TextView pv=text(money(selected.total),29);pv.setTypeface(null,Typeface.BOLD);paintProfit(pv,selected.total);pv.setPadding(0,dp(3),0,dp(5));pnl.addView(pv);
        TextView ps=text("실현 "+money(selected.realized)+"\n평가 "+money(selected.unrealized)+"\n보유 "+selected.positions.length()+" · 대기 "+selected.pending.length(),10);ps.setTextColor(Color.rgb(180,201,224));pnl.addView(ps);
        hero.addView(pnl,new LinearLayout.LayoutParams(0,dp(146),14));

        LinearLayout winCard=card();DonutMetricView win=new DonutMetricView(this);win.setMetric("승률",selected.win,pct(selected.win));winCard.addView(win,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout.LayoutParams wp=new LinearLayout.LayoutParams(0,dp(146),9);wp.setMargins(dp(7),0,0,0);hero.addView(winCard,wp);
        LinearLayout hitCard=card();DonutMetricView hit=new DonutMetricView(this);hit.setMetric("목표도달율",selected.target,pct(selected.target));hitCard.addView(hit,new LinearLayout.LayoutParams(-1,0,1));
        LinearLayout.LayoutParams hp=new LinearLayout.LayoutParams(0,dp(146),9);hp.setMargins(dp(7),0,0,0);hero.addView(hitCard,hp);
        LinearLayout.LayoutParams heroLp=new LinearLayout.LayoutParams(-1,dp(146));heroLp.setMargins(0,dp(8),0,0);content.addView(hero,heroLp);

        boolean frames=("coin".equals(market)||"at".equals(strategy)||"ma".equals(strategy));
        if(frames){
            TextView sec=text(strategyName(market,strategy)+" · 시간봉별 계좌성과",12);sec.setTypeface(null,Typeface.BOLD);sec.setTextColor(Color.rgb(157,181,208));sec.setPadding(0,dp(13),0,dp(6));content.addView(sec);
            HorizontalScrollView tfScroll=new HorizontalScrollView(this);tfScroll.setHorizontalScrollBarEnabled(false);
            LinearLayout row=new LinearLayout(this);row.setOrientation(LinearLayout.HORIZONTAL);row.setPadding(0,0,dp(4),0);
            for(String tf:new String[]{"5m","30m","60m","day"}){
                Stats q=statsTf(market,strategy,tf);LinearLayout c=card();
                TextView n=text(tfLabel(tf),13);n.setTypeface(null,Typeface.BOLD);c.addView(n);
                TextView tot=text(money(q.total),16);tot.setTypeface(null,Typeface.BOLD);paintProfit(tot,q.total);tot.setPadding(0,dp(2),0,dp(2));c.addView(tot);
                TextView detail=text("실현 "+money(q.realized)+"\n평가 "+money(q.unrealized)+"\n보유/대기 "+q.positions.length()+"/"+q.pending.length(),8);detail.setTextColor(Color.rgb(151,174,201));c.addView(detail);
                MetricProgressView bar=new MetricProgressView(this);bar.setMetric("목표도달",q.target);LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,dp(27));bp.setMargins(0,dp(4),0,0);c.addView(bar,bp);
                TextView wr=text("승률 "+pct(q.win),8);wr.setTextColor(Color.rgb(170,190,214));c.addView(wr);
                LinearLayout.LayoutParams cp=new LinearLayout.LayoutParams(dp(142),dp(148));if(row.getChildCount()>0)cp.setMargins(dp(6),0,0,0);row.addView(c,cp);
            }
            tfScroll.addView(row,new HorizontalScrollView.LayoutParams(-2,dp(148)));
            content.addView(tfScroll,new LinearLayout.LayoutParams(-1,dp(148)));
        }

        TextView cmp=text("전체 전략 계좌 성과 비교",12);cmp.setTypeface(null,Typeface.BOLD);cmp.setTextColor(Color.rgb(157,181,208));cmp.setPadding(0,dp(13),0,dp(6));content.addView(cmp);
        LinearLayout chartCard=card();HorizontalStrategyBarsView bars=new HorizontalStrategyBarsView(this);
        java.util.ArrayList<HorizontalStrategyBarsView.Item> items=new java.util.ArrayList<>();
        for(String[] x:strategyList(market)){Stats q=stats(market,x[0]);items.add(new HorizontalStrategyBarsView.Item(x[1],q.total));}
        bars.setItems(items);chartCard.addView(bars,new LinearLayout.LayoutParams(-1,dp(145)));content.addView(chartCard,new LinearLayout.LayoutParams(-1,dp(165)));

        LinearLayout trendCard=card();PnlSparklineView trend=new PnlSparklineView(this);trend.setValues(trendValues(selected.executions),strategyName(market,strategy)+" · 누적 실현손익 추세");trendCard.addView(trend,new LinearLayout.LayoutParams(-1,dp(105)));
        LinearLayout.LayoutParams tlp=new LinearLayout.LayoutParams(-1,dp(125));tlp.setMargins(0,dp(8),0,0);content.addView(trendCard,tlp);

        TextView acc=text("전략별 계좌성과",12);acc.setTypeface(null,Typeface.BOLD);acc.setTextColor(Color.rgb(157,181,208));acc.setPadding(0,dp(13),0,dp(6));content.addView(acc);
        HorizontalScrollView perfScroll=new HorizontalScrollView(this);perfScroll.setHorizontalScrollBarEnabled(false);
        LinearLayout perf=new LinearLayout(this);perf.setOrientation(LinearLayout.HORIZONTAL);perf.setPadding(0,0,dp(4),0);
        for(String[] x:strategyList(market)){
            Stats q=stats(market,x[0]);LinearLayout c=card();c.setOnClickListener(v->{strategy=x[0];page="home";showShell();});
            TextView n=text(x[1],12);n.setTypeface(null,Typeface.BOLD);c.addView(n);
            TextView p=text(money(q.total),15);p.setTypeface(null,Typeface.BOLD);paintProfit(p,q.total);c.addView(p);
            TextView m=text("실현 "+money(q.realized)+"\n평가 "+money(q.unrealized)+"\n승률 "+pct(q.win)+"\n보유/대기 "+q.positions.length()+"/"+q.pending.length()+"\n목표 "+pct(q.target),8);m.setTextColor(Color.rgb(151,173,200));m.setPadding(0,dp(4),0,0);c.addView(m);
            MetricProgressView g=new MetricProgressView(this);g.setMetric("목표도달",q.target);LinearLayout.LayoutParams gp=new LinearLayout.LayoutParams(-1,dp(27));gp.setMargins(0,dp(2),0,0);c.addView(g,gp);
            LinearLayout.LayoutParams cp=new LinearLayout.LayoutParams(dp(148),dp(142));if(perf.getChildCount()>0)cp.setMargins(dp(6),0,0,0);perf.addView(c,cp);
        }
        perfScroll.addView(perf,new HorizontalScrollView.LayoutParams(-2,dp(142)));
        content.addView(perfScroll,new LinearLayout.LayoutParams(-1,dp(142)));
    }

    private View feedCard(String m,String title){
        LinearLayout c=card();JSONObject rt=copyCache().optJSONObject("realtime");JSONObject r=rt==null?null:rt.optJSONObject(m);boolean on=r!=null&&r.optBoolean("connected",false);
        TextView t=text(title,11);t.setTypeface(null,Typeface.BOLD);t.setTextColor(on?Color.rgb(62,224,181):Color.rgb(255,119,133));c.addView(t);
        String d=r==null?"연결 확인 중":r.optString("detail","");int n=r==null?0:r.optInt("subscribed",0);TextView x=text((on?"연결 · ":"대기 · ")+(n>0?n+"종목 · ":"")+d,9);x.setTextColor(Color.rgb(139,164,194));c.addView(x);return c;
    }

    private void addMini(LinearLayout row,String label,String value){
        LinearLayout c=new LinearLayout(this);c.setOrientation(LinearLayout.VERTICAL);TextView a=text(label,9);a.setTextColor(Color.rgb(126,150,180));c.addView(a);TextView b=text(value,13);b.setTypeface(null,Typeface.BOLD);c.addView(b);
        row.addView(c,new LinearLayout.LayoutParams(0,dp(42),1));
    }

    private void addKpi(LinearLayout row,String label,String value){
        LinearLayout c=card();TextView a=text(label,9);a.setTextColor(Color.rgb(126,150,180));c.addView(a);TextView b=text(value,19);b.setTypeface(null,Typeface.BOLD);b.setPadding(0,dp(3),0,0);c.addView(b);
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(0,dp(68),1);if(row.getChildCount()>0)p.setMargins(dp(7),0,0,0);row.addView(c,p);
    }

    private void renderList(String title,JSONArray a,String kind){
        TextView h=text(title+"  ·  "+a.length()+"건",18);h.setTypeface(null,Typeface.BOLD);h.setPadding(0,0,0,dp(8));content.addView(h);
        if(a.length()==0){empty("기록 없음");return;}
        for(int i=0;i<Math.min(100,a.length());i++){JSONObject r=a.optJSONObject(i);if(r==null)continue;LinearLayout c=card();
            TextView n=text(nameOf(r),13);n.setTypeface(null,Typeface.BOLD);c.addView(n);
            String when=timeOf(r);TextView tm=text(refLabel()+" "+when,9);tm.setTextColor(Color.rgb(135,158,187));tm.setPadding(0,dp(2),0,0);c.addView(tm);
            TextView why=text("근거: "+reasonOf(r),10);why.setTextColor(Color.rgb(191,209,228));why.setPadding(0,dp(4),0,0);c.addView(why);
            double v=Double.NaN;if("positions".equals(kind))v=num(r,"net_pnl","unrealized_pnl","pnl","paper_pnl");else if("executions".equals(kind))v=num(r,"net_profit","net_realized","net_pnl","pnl","profit");else v=num(r,"score","target_price","price");
            if(finite(v)){TextView val=text(("pending".equals(kind)?"점수/가격 ":"손익 ")+number(v),11);paintProfit(val,"pending".equals(kind)?0:v);val.setPadding(0,dp(4),0,0);c.addView(val);}
            LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,-2);p.setMargins(0,0,0,dp(7));content.addView(c,p);
        }
    }

    private void renderResearch(){
        Stats st=stats(market,strategy);
        TextView h=text(strategyName(market,strategy)+" 연구이력",18);h.setTypeface(null,Typeface.BOLD);content.addView(h);
        TextView sub=text("후보 → 매수 → 판정 → 목표도달 흐름과 점수대별 결과",10);sub.setTextColor(Color.rgb(135,160,190));sub.setPadding(0,dp(3),0,dp(8));content.addView(sub);

        LinearLayout top=new LinearLayout(this);top.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout kpis=card();LinearLayout kr=new LinearLayout(this);kr.setOrientation(LinearLayout.HORIZONTAL);addMini(kr,"후보",String.valueOf(st.candidates));addMini(kr,"매수완료",String.valueOf(st.bought));addMini(kr,"판정완료",String.valueOf(st.confirmed));kpis.addView(kr);TextView note=text("최근 연구 데이터 기준",9);note.setTextColor(Color.rgb(127,151,181));note.setPadding(0,dp(7),0,0);kpis.addView(note);
        top.addView(kpis,new LinearLayout.LayoutParams(0,dp(112),2));
        LinearLayout dc=card();DonutMetricView donut=new DonutMetricView(this);donut.setMetric("목표도달율",st.target,pct(st.target));dc.addView(donut,new LinearLayout.LayoutParams(-1,0,1));LinearLayout.LayoutParams dcp=new LinearLayout.LayoutParams(0,dp(112),1);dcp.setMargins(dp(7),0,0,0);top.addView(dc,dcp);
        content.addView(top,new LinearLayout.LayoutParams(-1,dp(112)));

        TextView m=text("점수대별 목표 도달 지도",12);m.setTypeface(null,Typeface.BOLD);m.setTextColor(Color.rgb(157,181,208));m.setPadding(0,dp(13),0,dp(6));content.addView(m);
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
            LinearLayout c=card();TextView t=text(band[0]+"~"+band[1]+"점",10);t.setTypeface(null,Typeface.BOLD);c.addView(t);
            MetricProgressView pv=new MetricProgressView(this);pv.setMetric("목표도달",rate);c.addView(pv,new LinearLayout.LayoutParams(-1,dp(34)));
            TextView f=text(judged+"건 판정 · "+hits+"건 도달",8);f.setTextColor(Color.rgb(124,149,179));c.addView(f);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(dp(112),dp(82));if(br.getChildCount()>0)lp.setMargins(dp(5),0,0,0);br.addView(c,lp);
        }
        bandScroll.addView(br,new HorizontalScrollView.LayoutParams(-2,dp(82)));
        content.addView(bandScroll,new LinearLayout.LayoutParams(-1,dp(82)));

        TextView hist=text("최근 연구 기록",12);hist.setTypeface(null,Typeface.BOLD);hist.setTextColor(Color.rgb(157,181,208));hist.setPadding(0,dp(13),0,dp(6));content.addView(hist);
        if(st.research.length()==0){empty("연구이력 없음");return;}
        for(int i=0;i<Math.min(80,st.research.length());i++){
            JSONObject r=st.research.optJSONObject(i);if(r==null)continue;LinearLayout c=card();
            LinearLayout head=new LinearLayout(this);head.setOrientation(LinearLayout.HORIZONTAL);
            TextView n=text(nameOf(r),12);n.setTypeface(null,Typeface.BOLD);head.addView(n,new LinearLayout.LayoutParams(0,dp(28),1));
            double sc=num(r,"score","original_score","signal_score");TextView score=text(finite(sc)?number(sc)+"점":"—",11);score.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);score.setTextColor(Color.rgb(255,201,94));head.addView(score,new LinearLayout.LayoutParams(dp(70),dp(28)));c.addView(head);
            Boolean hit=target(r);String verdict=hit==null?"판정 중":hit?"목표 도달":"미도달";TextView v=text(verdict+" · "+refLabel()+" "+timeOf(r),9);v.setTextColor(hit==null?Color.rgb(156,179,205):hit?Color.rgb(62,224,181):Color.rgb(255,145,155));c.addView(v);
            TextView why=text("근거: "+reasonOf(r),9);why.setTextColor(Color.rgb(176,197,220));why.setPadding(0,dp(3),0,0);c.addView(why);
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
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(dp(12),dp(4),dp(12),dp(4));
        EditText kk=new EditText(this),ks=new EditText(this),ua=new EditText(this),us=new EditText(this);
        kk.setHint(saved.optBoolean("kiwoom_saved",false)?"키움 App Key · "+saved.optString("kiwoom_key_masked","저장됨"):"키움 App Key");
        ks.setHint(saved.optBoolean("kiwoom_saved",false)?"키움 App Secret · 저장됨":"키움 App Secret");
        ua.setHint(saved.optBoolean("upbit_saved",false)?"업비트 Access Key · "+saved.optString("upbit_access_masked","저장됨"):"업비트 Access Key");
        us.setHint(saved.optBoolean("upbit_saved",false)?"업비트 Secret Key · 저장됨":"업비트 Secret Key");
        for(EditText v:new EditText[]{kk,ks,ua,us}){
            v.setSingleLine(true);v.setTextColor(Color.WHITE);v.setHintTextColor(Color.rgb(130,154,183));
            v.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
            form.addView(v,new LinearLayout.LayoutParams(-1,dp(48)));
        }
        new AlertDialog.Builder(this).setTitle("S21+ 직접 API 설정")
            .setMessage("빈 칸은 기존 값을 유지합니다. 저장 후 키움 OAuth와 업비트 인증 REST를 즉시 검사합니다. 업비트는 발급 키의 허용 IP에 현재 S21+ 외부 IP가 포함되어야 합니다.")
            .setView(form).setNegativeButton("취소",null)
            .setNeutralButton("전체 삭제",(d,w)->{
                store.saveKiwoom("","");store.saveUpbit("","");
                getSharedPreferences("broker_probe_v1",MODE_PRIVATE).edit().clear().apply();restartFeeds();refresh(false);
            })
            .setPositiveButton("저장",(d,w)->{
                String a=kk.getText().toString().trim(),b=ks.getText().toString().trim(),c=ua.getText().toString().trim(),e=us.getText().toString().trim();
                if(a.isEmpty()!=b.isEmpty()){
                    new AlertDialog.Builder(this).setTitle("키움 키 확인").setMessage("App Key와 App Secret을 둘 다 입력하거나 둘 다 비워 기존 값을 유지하십시오.").setPositiveButton("확인",null).show();return;
                }
                if(c.isEmpty()!=e.isEmpty()){
                    new AlertDialog.Builder(this).setTitle("업비트 키 확인").setMessage("Access Key와 Secret Key를 둘 다 입력하거나 둘 다 비워 기존 값을 유지하십시오.").setPositiveButton("확인",null).show();return;
                }
                if(!a.isEmpty())store.saveKiwoom(a,b);
                if(!c.isEmpty())store.saveUpbit(c,e);
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
        io.execute(()->{
            JSONObject result=BrokerApiProbe.all(new BrokerSecretStore(this));
            getSharedPreferences("broker_probe_v1",MODE_PRIVATE).edit().putString("result",result.toString()).putLong("checked_at",System.currentTimeMillis()).apply();
            if(restartAfter)main.post(this::restartFeeds);
            main.post(()->{
                refresh(false);
                JSONObject k=result.optJSONObject("kiwoom"),u=result.optJSONObject("upbit");
                String km=k==null?"검사 실패":k.optBoolean("ok",false)?"정상":k.optString("message","인증 실패");
                String um=u==null?"검사 실패":u.optBoolean("ok",false)?"정상":u.optString("message","인증 실패");
                if(u!=null&&u.optBoolean("ip_hint",false))um+="\n허용 IP에 현재 S21+ 외부 IP가 등록되어 있는지 확인 필요";
                new AlertDialog.Builder(this).setTitle("API 인증 검사 결과").setMessage("키움: "+km+"\n\n업비트: "+um).setPositiveButton("확인",null).show();
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
