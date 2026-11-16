package com.artitrade.mobile;

import android.content.Context;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;
import okhttp3.*;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Phone-direct Upbit feed based on the latest PC FIX38 policy:
 * rank KRW markets by 24h traded value, keep TOP35, stream ticker+orderbook.
 * Optional private WS receives myOrder/myAsset when API keys are registered.
 */
final class UpbitRealtimeClient {
    private static final String REST="https://api.upbit.com";
    private static final String WS="wss://api.upbit.com/websocket/v1";
    private static final String PRIVATE_WS="wss://api.upbit.com/websocket/v1/private";
    private final OkHttpClient http;
    private final BrokerSecretStore secrets;
    private final RealtimeState state;
    private final AtomicBoolean running=new AtomicBoolean(false);
    private volatile WebSocket publicWs,privateWs;
    private volatile int subscribed=0;
    private volatile long lastFrame=0;
    private Thread supervisor;

    UpbitRealtimeClient(Context c){
        secrets=new BrokerSecretStore(c);state=new RealtimeState(c);
        http=new OkHttpClient.Builder().connectTimeout(12,TimeUnit.SECONDS).readTimeout(15,TimeUnit.SECONDS)
            .writeTimeout(12,TimeUnit.SECONDS).pingInterval(20,TimeUnit.SECONDS).build();
    }
    void start(){
        if(!running.compareAndSet(false,true))return;
        supervisor=new Thread(this::loop,"artitrade-upbit-direct");supervisor.start();
    }
    void stop(){
        running.set(false);
        WebSocket w=publicWs;if(w!=null)w.close(1000,"stop");
        w=privateWs;if(w!=null)w.close(1000,"stop");
        if(supervisor!=null)supervisor.interrupt();
        state.set("coin",false,0,lastFrame,"중지됨");
    }

    private void loop(){
        int backoff=2;
        while(running.get()){
            try{
                List<String> top=loadTop35();
                if(top.isEmpty())throw new IOException("TOP35 empty");
                subscribed=top.size();
                connectPublic(top);
                if(secrets.hasUpbit())connectPrivate();
                state.set("coin",true,subscribed,lastFrame,"업비트 직접 WebSocket 연결");
                long started=System.currentTimeMillis();
                while(running.get()&&publicWs!=null){
                    if(lastFrame>0&&System.currentTimeMillis()-lastFrame>45000)throw new IOException("public websocket stale");
                    Thread.sleep(1500);
                }
                if(System.currentTimeMillis()-started>30000)backoff=2;
            }catch(InterruptedException e){Thread.currentThread().interrupt();break;}
            catch(Exception e){
                state.set("coin",false,subscribed,lastFrame,"재연결: "+e.getClass().getSimpleName());
            }finally{
                WebSocket w=publicWs;publicWs=null;if(w!=null)w.cancel();
                w=privateWs;privateWs=null;if(w!=null)w.cancel();
            }
            if(!running.get())break;
            try{Thread.sleep(backoff*1000L);}catch(InterruptedException e){Thread.currentThread().interrupt();break;}
            backoff=Math.min(30,backoff*2);
        }
    }

    private List<String> loadTop35()throws Exception{
        Request req=new Request.Builder().url(REST+"/v1/ticker/all?quote_currencies=KRW").header("Accept","application/json").build();
        try(Response res=http.newCall(req).execute()){
            if(!res.isSuccessful()||res.body()==null)throw new IOException("ticker/all HTTP "+res.code());
            JSONArray a=new JSONArray(res.body().string());
            ArrayList<JSONObject> rows=new ArrayList<>();
            for(int i=0;i<a.length();i++){
                JSONObject o=a.optJSONObject(i);if(o==null)continue;
                String m=o.optString("market","");
                if(!m.startsWith("KRW-"))continue;
                rows.add(o);
            }
            rows.sort((x,y)->Double.compare(y.optDouble("acc_trade_price_24h",0),x.optDouble("acc_trade_price_24h",0)));
            ArrayList<String> out=new ArrayList<>();
            for(JSONObject o:rows){if(out.size()>=35)break;String m=o.optString("market","");if(!m.isEmpty())out.add(m);}
            return out;
        }
    }

    private void connectPublic(List<String> codes){
        Request req=new Request.Builder().url(WS).build();
        publicWs=http.newWebSocket(req,new WebSocketListener(){
            @Override public void onOpen(WebSocket ws,Response response){
                try{
                    JSONArray msg=new JSONArray();
                    msg.put(new JSONObject().put("ticket","ARTITRADE_S21_"+UUID.randomUUID()));
                    msg.put(new JSONObject().put("type","ticker").put("codes",new JSONArray(codes)).put("is_only_realtime",true));
                    msg.put(new JSONObject().put("type","orderbook").put("codes",new JSONArray(codes)).put("is_only_realtime",true));
                    msg.put(new JSONObject().put("format","DEFAULT"));
                    ws.send(msg.toString());
                    state.set("coin",true,codes.size(),lastFrame,"업비트 TOP35 실시간 수신");
                }catch(Exception ignored){}
            }
            @Override public void onMessage(WebSocket ws,String text){handlePublic(text);}
            @Override public void onMessage(WebSocket ws,okio.ByteString bytes){handlePublic(bytes.utf8());}
            @Override public void onFailure(WebSocket ws,Throwable t,Response r){publicWs=null;state.set("coin",false,subscribed,lastFrame,"WS 오류: "+t.getClass().getSimpleName());}
            @Override public void onClosed(WebSocket ws,int code,String reason){publicWs=null;}
        });
    }

    private void handlePublic(String raw){
        try{
            JSONObject o=new JSONObject(raw);
            String type=o.optString("type",""),code=o.optString("code","");
            if(!("ticker".equals(type)||"orderbook".equals(type)))return;
            lastFrame=System.currentTimeMillis();
            String detail;
            if("ticker".equals(type))detail=code+" "+o.optDouble("trade_price",0);
            else{
                JSONArray u=o.optJSONArray("orderbook_units");JSONObject first=u!=null?u.optJSONObject(0):null;
                detail=code+(first==null?"":" A "+first.optDouble("ask_price",0)+" / B "+first.optDouble("bid_price",0));
            }
            state.set("coin",true,subscribed,lastFrame,detail);
        }catch(Exception ignored){}
    }

    private void connectPrivate(){
        try{
            String jwt=jwt(secrets.upbitAccess(),secrets.upbitSecret());
            Request req=new Request.Builder().url(PRIVATE_WS).header("Authorization","Bearer "+jwt).build();
            privateWs=http.newWebSocket(req,new WebSocketListener(){
                @Override public void onOpen(WebSocket ws,Response response){
                    try{
                        JSONArray msg=new JSONArray();
                        msg.put(new JSONObject().put("ticket","ARTITRADE_ACCOUNT_"+UUID.randomUUID()));
                        msg.put(new JSONObject().put("type","myOrder"));
                        msg.put(new JSONObject().put("type","myAsset"));
                        ws.send(msg.toString());
                    }catch(Exception ignored){}
                }
                @Override public void onMessage(WebSocket ws,String text){
                    try{
                        JSONObject o=new JSONObject(text);
                        String type=o.optString("type","");
                        if("myOrder".equals(type)||"myAsset".equals(type)){
                            lastFrame=System.currentTimeMillis();
                            state.set("coin",true,subscribed,lastFrame,"업비트 "+("myOrder".equals(type)?"주문/체결":"자산")+" 실시간 수신");
                        }
                    }catch(Exception ignored){}
                }
                @Override public void onMessage(WebSocket ws,okio.ByteString bytes){onMessage(ws,bytes.utf8());}
                @Override public void onFailure(WebSocket ws,Throwable t,Response r){privateWs=null;}
                @Override public void onClosed(WebSocket ws,int code,String reason){privateWs=null;}
            });
        }catch(Exception ignored){}
    }

    private static String jwt(String access,String secret)throws Exception{
        String head=b64("{\"alg\":\"HS512\",\"typ\":\"JWT\"}");
        JSONObject p=new JSONObject().put("access_key",access).put("nonce",UUID.randomUUID().toString());
        String body=b64(p.toString()),input=head+"."+body;
        Mac mac=Mac.getInstance("HmacSHA512");mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA512"));
        return input+"."+Base64.encodeToString(mac.doFinal(input.getBytes(StandardCharsets.UTF_8)),Base64.URL_SAFE|Base64.NO_WRAP|Base64.NO_PADDING);
    }
    private static String b64(String s){return Base64.encodeToString(s.getBytes(StandardCharsets.UTF_8),Base64.URL_SAFE|Base64.NO_WRAP|Base64.NO_PADDING);}
}
