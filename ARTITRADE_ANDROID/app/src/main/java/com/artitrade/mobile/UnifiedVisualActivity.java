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
        String masked=storage==null?"":storage.optString("kiwoom".equals(which)?"kiwoom_key_masked":"upbit_access_masked","");
        JSONObject p=probe==null?null:probe.optJSONObject(which);
        String name="kiwoom".equals(which)?"키움":"업비트";
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
            LinearLayout row=new LinearLayout(this);row.setOrientation(LinearLayout.HORIZONTAL);
            for(String tf:new String[]{"5m","30m","60m","day"}){
                Stats q=statsTf(market,strategy,tf);LinearLayout c=card();
                TextView n=text(tfLabel(tf),13);n.setTypeface(null,Typeface.BOLD);c.addView(n);
                TextView tot=text(money(q.total),16);tot.setTypeface(null,Typeface.BOLD);paintProfit(tot,q.total);tot.setPadding(0,dp(2),0,dp(2));c.addView(tot);
                TextView detail=text("실현 "+money(q.realized)+"\n평가 "+money(q.unrealized)+"\n보유/대기 "+q.positions.length()+"/"+q.pending.length(),8);detail.setTextColor(Color.rgb(151,174,201));c.addView(detail);
                MetricProgressView bar=new MetricProgressView(this);bar.setMetric("목표도달",q.target);LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,dp(27));bp.setMargins(0,dp(4),0,0);c.addView(bar,bp);
                TextView wr=text("승률 "+pct(q.win),8);wr.setTextColor(Color.rgb(170,190,214));c.addView(wr);
                LinearLayout.LayoutParams cp=new LinearLayout.LayoutParams(0,dp(148),1);if(row.getChildCount()>0)cp.setMargins(dp(6),0,0,0);row.addView(c,cp);
            }
            content.addView(row,new LinearLayout.LayoutParams(-1,dp(148)));
        }

        TextView cmp=text("전체 전략 계좌 성과 비교",12);cmp.setTypeface(null,Typeface.BOLD);cmp.setTextColor(Color.rgb(157,181,208));cmp.setPadding(0,dp(13),0,dp(6));content.addView(cmp);
        LinearLayout chartCard=card();HorizontalStrategyBarsView bars=new HorizontalStrategyBarsView(this);
        java.util.ArrayList<HorizontalStrategyBarsView.Item> items=new java.util.ArrayList<>();
        for(String[] x:strategyList(market)){Stats q=stats(market,x[0]);items.add(new HorizontalStrategyBarsView.Item(x[1],q.total));}
        bars.setItems(items);chartCard.addView(bars,new LinearLayout.LayoutParams(-1,dp(145)));content.addView(chartCard,new LinearLayout.LayoutParams(-1,dp(165)));

        LinearLayout trendCard=card();PnlSparklineView trend=new PnlSparklineView(this);trend.setValues(trendValues(selected.executions),strategyName(market,strategy)+" · 누적 실현손익 추세");trendCard.addView(trend,new LinearLayout.LayoutParams(-1,dp(105)));
        LinearLayout.LayoutParams tlp=new LinearLayout.LayoutParams(-1,dp(125));tlp.setMargins(0,dp(8),0,0);content.addView(trendCard,tlp);

        TextView acc=text("전략별 계좌성과",12);acc.setTypeface(null,Typeface.BOLD);acc.setTextColor(Color.rgb(157,181,208));acc.setPadding(0,dp(13),0,dp(6));content.addView(acc);
        LinearLayout perf=new LinearLayout(this);perf.setOrientation(LinearLayout.HORIZONTAL);
        for(String[] x:strategyList(market)){
            Stats q=stats(market,x[0]);LinearLayout c=card();c.setOnClickListener(v->{strategy=x[0];page="home";showShell();});
            TextView n=text(x[1],12);n.setTypeface(null,Typeface.BOLD);c.addView(n);
            TextView p=text(money(q.total),15);p.setTypeface(null,Typeface.BOLD);paintProfit(p,q.total);c.addView(p);
            TextView m=text("실현 "+money(q.realized)+"\n평가 "+money(q.unrealized)+"\n승률 "+pct(q.win)+"\n보유/대기 "+q.positions.length()+"/"+q.pending.length()+"\n목표 "+pct(q.target),8);m.setTextColor(Color.rgb(151,173,200));m.setPadding(0,dp(4),0,0);c.addView(m);
            MetricProgressView g=new MetricProgressView(this);g.setMetric("목표도달",q.target);LinearLayout.LayoutParams gp=new LinearLayout.LayoutParams(-1,dp(27));gp.setMargins(0,dp(2),0,0);c.addView(g,gp);
            LinearLayout.LayoutParams cp=new LinearLayout.LayoutParams(0,dp(142),1);if(perf.getChildCount()>0)cp.setMargins(dp(6),0,0,0);perf.addView(c,cp);
        }
        content.addView(perf,new LinearLayout.LayoutParams(-1,dp(142)));
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
        Stats st=stats(market,strategy);TextView h=text("리서치 결과",18);h.setTypeface(null,Typeface.BOLD);h.setPadding(0,0,0,dp(8));content.addView(h);
        LinearLayout row=new LinearLayout(this);row.setOrientation(LinearLayout.HORIZONTAL);addKpi(row,"후보",String.valueOf(st.candidates));addKpi(row,"매수완료",String.valueOf(st.bought));addKpi(row,"판정완료",String.valueOf(st.confirmed));addKpi(row,"목표도달율",pct(st.target));content.addView(row);
        TextView m=text("점수대별 목표 도달 지도",11);m.setTypeface(null,Typeface.BOLD);m.setTextColor(Color.rgb(148,171,199));m.setPadding(0,dp(13),0,dp(6));content.addView(m);
        int[][] bands="gmo".equals(strategy)?new int[][]{{40,60},{61,80},{81,100}}:new int[][]{{0,20},{21,40},{41,60},{61,80},{81,100}};
        LinearLayout br=new LinearLayout(this);br.setOrientation(LinearLayout.HORIZONTAL);
        for(int[] b:bands){int judged=0,hits=0;for(int i=0;i<st.research.length();i++){JSONObject r=st.research.optJSONObject(i);if(r==null)continue;double sc=num(r,"score","original_score","signal_score");if(!finite(sc)||sc<b[0]||sc>b[1])continue;Boolean hit=target(r);if(hit!=null){judged++;if(hit)hits++;}}
            LinearLayout c=card();TextView t=text(b[0]+"~"+b[1]+"점",10);t.setTextColor(Color.rgb(149,173,201));c.addView(t);TextView p=text(judged>0?pct(hits*100.0/judged):"—",22);p.setTypeface(null,Typeface.BOLD);c.addView(p);TextView f=text(judged+"건 판정 · "+hits+"건 도달",9);f.setTextColor(Color.rgb(124,149,179));c.addView(f);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,dp(90),1);if(br.getChildCount()>0)lp.setMargins(dp(6),0,0,0);br.addView(c,lp);
        }content.addView(br);
    }

    private void renderErrors(){
        TextView h=text("오류 기록",18);h.setTypeface(null,Typeface.BOLD);h.setPadding(0,0,0,dp(8));content.addView(h);
        JSONArray all=new JSONArray();JSONObject e=errors(market,strategy);JSONArray items=e.optJSONArray("items");if(items!=null)for(int i=0;i<items.length();i++)all.put(items.opt(i));
        JSONArray run=copyCache().optJSONArray("runtime_diagnostics");if(run!=null)for(int i=0;i<run.length();i++)all.put(run.opt(i));
        JSONObject py=copyCache().optJSONObject("strategy_runtime");JSONArray pe=py==null?null:py.optJSONArray("errors");if(pe!=null)for(int i=0;i<pe.length();i++)all.put(pe.opt(i));
        if(all.length()==0){empty("기록된 오류 없음");return;}
        for(int i=0;i<Math.min(120,all.length());i++){JSONObject r=all.optJSONObject(i);if(r==null)continue;LinearLayout c=card();TextView n=text(r.optString("stage",r.optString("error_type",r.optString("code","오류 기록"))),12);n.setTypeface(null,Typeface.BOLD);n.setTextColor(Color.rgb(255,145,155));c.addView(n);TextView d=text(r.optString("message",r.optString("detail","상세 미기록")),10);d.setPadding(0,dp(4),0,0);c.addView(d);TextView t=text(formatKst(r.opt("at")),9);t.setTextColor(Color.rgb(130,153,182));c.addView(t);LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.setMargins(0,0,0,dp(7));content.addView(c,lp);}
    }

    private void renderOperation(){
        TextView h=text(strategyName(market,strategy)+" 운용 설정",18);h.setTypeface(null,Typeface.BOLD);h.setPadding(0,0,0,dp(8));content.addView(h);
        LinearLayout c=card();EditText seed=new EditText(this),max=new EditText(this);seed.setTextColor(Color.WHITE);max.setTextColor(Color.WHITE);seed.setHintTextColor(Color.rgb(110,135,165));max.setHintTextColor(Color.rgb(110,135,165));seed.setHint("운용 시드 KRW");max.setHint("최대보유 종목");
        seed.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);max.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        try{JSONObject local=new JSONObject(getSharedPreferences("unified_operation",MODE_PRIVATE).getString(market+"_"+strategy,"{}"));if(local.has("seed"))seed.setText(String.valueOf(local.optLong("seed")));if(local.has("max_hold"))max.setText(String.valueOf(local.optLong("max_hold")));}catch(Exception ignored){}
        c.addView(seed,new LinearLayout.LayoutParams(-1,dp(48)));c.addView(max,new LinearLayout.LayoutParams(-1,dp(48)));
        Button save=button("설정 저장",true,()->{try{long a=Long.parseLong(seed.getText().toString());long b=Long.parseLong(max.getText().toString());if(a>=0&&b>=0&&b<=200)getSharedPreferences("unified_operation",MODE_PRIVATE).edit().putString(market+"_"+strategy,new JSONObject().put("seed",a).put("max_hold",b).put("saved_at",System.currentTimeMillis()).toString()).apply();}catch(Exception ignored){}});
        LinearLayout.LayoutParams sv=new LinearLayout.LayoutParams(-1,dp(40));sv.setMargins(0,dp(8),0,0);c.addView(save,sv);content.addView(c);

        JSONObject live=copyCache().optJSONObject("live_orders");boolean armed=live!=null&&live.optBoolean(market,false);
        LinearLayout d=card();TextView t=text("S21+ 메인 자동매매",13);t.setTypeface(null,Typeface.BOLD);d.addView(t);
        LinearLayout dr=new LinearLayout(this);dr.setOrientation(LinearLayout.HORIZONTAL);dr.setPadding(0,dp(8),0,0);dr.addView(button("PHONE MAIN",false,()->setDesired("phone")),new LinearLayout.LayoutParams(0,dp(40),1));LinearLayout.LayoutParams offp=new LinearLayout.LayoutParams(0,dp(40),1);offp.setMargins(dp(7),0,0,0);dr.addView(button("자동매매 정지",false,()->setDesired("off")),offp);d.addView(dr);
        TextView lt=text("실거래 자동주문",13);lt.setTypeface(null,Typeface.BOLD);lt.setPadding(0,dp(12),0,0);d.addView(lt);
        LinearLayout lr=new LinearLayout(this);lr.setOrientation(LinearLayout.HORIZONTAL);lr.setPadding(0,dp(8),0,0);lr.addView(button("실거래 ON",armed,()->setLive(true)),new LinearLayout.LayoutParams(0,dp(40),1));LinearLayout.LayoutParams of=new LinearLayout.LayoutParams(0,dp(40),1);of.setMargins(dp(7),0,0,0);lr.addView(button("실거래 OFF",!armed,()->setLive(false)),of);d.addView(lr);
        TextView hint=text(armed?"실거래 ON · 최신 EXE 전략 신호를 실제 주문으로 연결":"실거래 OFF · 시세/전략은 계속 실행되고 실제 주문만 차단",10);hint.setTextColor(Color.rgb(141,165,194));hint.setPadding(0,dp(8),0,0);d.addView(hint);
        LinearLayout.LayoutParams dpv=new LinearLayout.LayoutParams(-1,-2);dpv.setMargins(0,dp(8),0,0);content.addView(d,dpv);
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
        BrokerSecretStore store=new BrokerSecretStore(this);LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(dp(12),dp(4),dp(12),dp(4));
        EditText kk=new EditText(this),ks=new EditText(this),ua=new EditText(this),us=new EditText(this);
        kk.setHint(store.hasKiwoom()?"키움 App Key · 저장됨":"키움 App Key");ks.setHint(store.hasKiwoom()?"키움 Secret Key · 저장됨":"키움 Secret Key");ua.setHint(store.hasUpbit()?"업비트 Access Key · 저장됨":"업비트 Access Key");us.setHint(store.hasUpbit()?"업비트 Secret Key · 저장됨":"업비트 Secret Key");
        for(EditText v:new EditText[]{kk,ks,ua,us}){v.setSingleLine(true);v.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);form.addView(v,new LinearLayout.LayoutParams(-1,dp(48)));}
        new AlertDialog.Builder(this).setTitle("S21+ 직접 API 설정").setMessage("빈 칸은 기존 값을 유지합니다. 키는 Android Keystore로 암호화합니다.").setView(form).setNegativeButton("취소",null)
            .setNeutralButton("전체 삭제",(d,w)->{store.saveKiwoom("","");store.saveUpbit("","");restartFeeds();})
            .setPositiveButton("저장",(d,w)->{String a=kk.getText().toString().trim(),b=ks.getText().toString().trim(),c=ua.getText().toString().trim(),e=us.getText().toString().trim();if((!a.isEmpty()||!b.isEmpty())&&(!a.isEmpty()&&!b.isEmpty()))store.saveKiwoom(a,b);if((!c.isEmpty()||!e.isEmpty())&&(!c.isEmpty()&&!e.isEmpty()))store.saveUpbit(c,e);restartFeeds();main.postDelayed(()->refresh(false),900);}).show();
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
