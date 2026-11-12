package com.artitrade.mobile;

import android.content.Context;
import org.json.JSONArray;
import org.json.JSONObject;
import okhttp3.*;

import java.io.IOException;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Phone-direct Kiwoom REST + WebSocket feed.
 * Mirrors the latest PC stock engine universe: KOSPI top100 + KOSDAQ top100,
 * and subscribes 0B trade + 0D orderbook on one token/session.
 */
final class KiwoomRealtimeClient {
    private static final String REST="https://api.kiwoom.com";
    private static final String[] WS={
        "wss://api.kiwoom.com:10000/api/dostk/websocket",
        "wss://api.kiwoom.com:10000/api/websocket"
    };
    private final OkHttpClient http;
    private final BrokerSecretStore secrets;
    private final RealtimeState state;
    private final AtomicBoolean running=new AtomicBoolean(false);
    private volatile WebSocket ws;
    private volatile int subscribed=0;
    private volatile long lastFrame=0;
    private volatile int wsChoice=0;
    private Thread supervisor;

    KiwoomRealtimeClient(Context c){
        secrets=new BrokerSecretStore(c);state=new RealtimeState(c);
        http=new OkHttpClient.Builder().connectTimeout(15,TimeUnit.SECONDS).readTimeout(15,TimeUnit.SECONDS)
            .writeTimeout(12,TimeUnit.SECONDS).pingInterval(20,TimeUnit.SECONDS).build();
    }
    void start(){
        if(!running.compareAndSet(false,true))return;
        supervisor=new Thread(this::loop,"artitrade-kiwoom-direct");supervisor.start();
    }
    void stop(){
        running.set(false);WebSocket w=ws;if(w!=null)w.close(1000,"stop");
        if(supervisor!=null)supervisor.interrupt();
        state.set("stock",false,0,lastFrame,"중지됨");
    }

    private void loop(){
        int backoff=2;
        while(running.get()){
            try{
                if(!secrets.hasKiwoom()){
                    state.set("stock",false,0,lastFrame,"키움 App Key/Secret 등록 필요");
                    Thread.sleep(3000);continue;
                }
                String token=accessToken();
                LinkedHashSet<String> universe=new LinkedHashSet<>();
                universe.addAll(loadRank(token,"001",100));
                universe.addAll(loadRank(token,"101",100));
                if(universe.isEmpty())throw new IOException("거래대금 상위 종목 0건");
                ArrayList<String> codes=new ArrayList<>(universe);
                if(codes.size()>200)codes=new ArrayList<>(codes.subList(0,200));
                subscribed=codes.size();
                connect(token,codes);
                state.set("stock",false,subscribed,lastFrame,"키움 WebSocket 로그인 중");
                long opened=System.currentTimeMillis();
                while(running.get()&&ws!=null){
                    if(lastFrame>0&&System.currentTimeMillis()-lastFrame>60000)throw new IOException("stock websocket stale");
                    Thread.sleep(1500);
                }
                if(System.currentTimeMillis()-opened>30000)backoff=2;
            }catch(InterruptedException e){Thread.currentThread().interrupt();break;}
            catch(Exception e){
                state.set("stock",false,subscribed,lastFrame,"재연결: "+e.getClass().getSimpleName());
            }finally{
                WebSocket w=ws;ws=null;if(w!=null)w.cancel();
            }
            if(!running.get())break;
            try{Thread.sleep(backoff*1000L);}catch(InterruptedException e){Thread.currentThread().interrupt();break;}
            backoff=Math.min(30,backoff*2);wsChoice=(wsChoice+1)%WS.length;
        }
    }

    private String accessToken()throws Exception{
        JSONObject body=new JSONObject().put("grant_type","client_credentials")
            .put("appkey",secrets.kiwoomKey()).put("secretkey",secrets.kiwoomSecret());
        Request req=new Request.Builder().url(REST+"/oauth2/token")
            .post(RequestBody.create(body.toString(),MediaType.parse("application/json; charset=utf-8")))
            .header("Accept","application/json").build();
        try(Response res=http.newCall(req).execute()){
            if(!res.isSuccessful()||res.body()==null)throw new IOException("token HTTP "+res.code());
            JSONObject o=new JSONObject(res.body().string());
            String token=o.optString("token","");
            if(token.isEmpty()||(!"0".equals(String.valueOf(o.opt("return_code")))&&o.has("return_code")))
                throw new IOException("token rejected: "+o.optString("return_msg",""));
            return token;
        }
    }

    private List<String> loadRank(String token,String market,int limit)throws Exception{
        ArrayList<String> out=new ArrayList<>();String cont="N",next="";
        for(int page=0;page<12&&out.size()<limit;page++){
            JSONObject body=new JSONObject().put("mrkt_tp",market).put("mang_stk_incls","0").put("stex_tp","1");
            Request.Builder rb=new Request.Builder().url(REST+"/api/dostk/rkinfo")
                .post(RequestBody.create(body.toString(),MediaType.parse("application/json; charset=utf-8")))
                .header("authorization","Bearer "+token).header("api-id","ka10032")
                .header("cont-yn",cont).header("next-key",next).header("Accept","application/json");
            try(Response res=http.newCall(rb.build()).execute()){
                if(!res.isSuccessful()||res.body()==null)throw new IOException("rank HTTP "+res.code());
                JSONObject o=new JSONObject(res.body().string());
                JSONArray rows=o.optJSONArray("trde_prica_upper");
                if(rows!=null)for(int i=0;i<rows.length()&&out.size()<limit;i++){
                    JSONObject row=rows.optJSONObject(i);if(row==null)continue;
                    String code=row.optString("stk_cd","").replaceFirst("^A","");
                    if(code.matches("\\d{6}")&&!out.contains(code))out.add(code);
                }
                cont=res.header("cont-yn","N");next=res.header("next-key","");
                if(!"Y".equalsIgnoreCase(cont)||next.isEmpty())break;
            }
        }
        return out;
    }

    private void connect(String token,List<String> codes){
        Request req=new Request.Builder().url(WS[wsChoice]).build();
        ws=http.newWebSocket(req,new WebSocketListener(){
            @Override public void onOpen(WebSocket socket,Response response){
                try{socket.send(new JSONObject().put("trnm","LOGIN").put("token",token).toString());}
                catch(Exception ignored){}
            }
            @Override public void onMessage(WebSocket socket,String text){handle(socket,text,codes);}
            @Override public void onMessage(WebSocket socket,okio.ByteString bytes){handle(socket,bytes.utf8(),codes);}
            @Override public void onFailure(WebSocket socket,Throwable t,Response r){ws=null;state.set("stock",false,subscribed,lastFrame,"WS 오류: "+t.getClass().getSimpleName());}
            @Override public void onClosed(WebSocket socket,int code,String reason){ws=null;}
        });
    }

    private void handle(WebSocket socket,String raw,List<String> codes){
        try{
            JSONObject o=new JSONObject(raw);String tr=o.optString("trnm","");
            if("LOGIN".equals(tr)){
                if(!"0".equals(String.valueOf(o.opt("return_code"))))throw new IOException("LOGIN "+o.optString("return_msg",""));
                for(int g=0;g<2;g++){
                    int from=g*100,to=Math.min(codes.size(),from+100);if(from>=to)continue;
                    JSONArray items=new JSONArray();for(int i=from;i<to;i++)items.put(codes.get(i));
                    JSONArray types=new JSONArray().put("0B").put("0D");
                    JSONObject reg=new JSONObject().put("trnm","REG").put("grp_no",String.valueOf(g+1)).put("refresh","1")
                        .put("data",new JSONArray().put(new JSONObject().put("item",items).put("type",types)));
                    socket.send(reg.toString());
                }
                JSONObject acct=new JSONObject().put("trnm","REG").put("grp_no","9").put("refresh","1")
                    .put("data",new JSONArray().put(new JSONObject().put("item",new JSONArray().put(""))
                    .put("type",new JSONArray().put("00"))));
                socket.send(acct.toString());
                state.set("stock",true,codes.size(),lastFrame,"키움 0B/0D 직접 구독");
                return;
            }
            if(!"REAL".equals(tr))return;
            JSONArray data=o.optJSONArray("data");if(data==null)return;
            for(int i=0;i<data.length();i++){
                JSONObject row=data.optJSONObject(i);if(row==null)continue;
                String type=row.optString("type","");JSONObject v=row.optJSONObject("values");
                if("00".equals(type)){
                    lastFrame=System.currentTimeMillis();
                    state.set("stock",true,subscribed,lastFrame,"키움 주문/체결 00 실시간 수신");
                    continue;
                }
                if(v==null||(!"0B".equals(type)&&!"0D".equals(type)))continue;
                String code=row.optString("item",v.optString("9001","")).replaceFirst("^[A-Z_]+","").replaceFirst("^A","");
                lastFrame=System.currentTimeMillis();
                String detail;
                if("0B".equals(type)){
                    detail=code+" P "+abs(v.optString("10",""))+" A "+abs(v.optString("27",""))+" / B "+abs(v.optString("28",""));
                }else{
                    detail=code+" A "+abs(v.optString("41",""))+" / B "+abs(v.optString("51",""));
                }
                state.set("stock",true,subscribed,lastFrame,detail);
            }
        }catch(Exception e){
            state.set("stock",false,subscribed,lastFrame,"프레임 오류: "+e.getClass().getSimpleName());
        }
    }
    private static String abs(String s){
        if(s==null)return "";String x=s.trim().replace(",","");while(x.startsWith("+")||x.startsWith("-"))x=x.substring(1);return x;
    }
}
