package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;

/** Encrypted broker API credentials for the unified S21+ main engine. */
final class BrokerSecretStore {
    private static final String PREF="artitrade_broker_secrets_v1";
    private final SharedPreferences prefs;
    private final SecureBox box;
    BrokerSecretStore(Context c){prefs=c.getApplicationContext().getSharedPreferences(PREF,Context.MODE_PRIVATE);box=new SecureBox(c);}

    private void put(String key,String value){
        String v=value==null?"":value.trim();
        SharedPreferences.Editor e=prefs.edit();
        if(v.isEmpty())e.remove(key);else e.putString(key,box.seal(v));
        if(!e.commit())throw new IllegalStateException("credential storage failed");
    }
    private String get(String key){
        String v=prefs.getString(key,"");
        if(v==null||v.isEmpty())return "";
        try{return box.unseal(v);}catch(Exception e){return "";}
    }
    void saveKiwoom(String appKey,String secret){put("kiwoom_key",appKey);put("kiwoom_secret",secret);}
    void saveUpbit(String accessKey,String secret){put("upbit_access",accessKey);put("upbit_secret",secret);}
    String kiwoomKey(){return get("kiwoom_key");}
    String kiwoomSecret(){return get("kiwoom_secret");}
    String upbitAccess(){return get("upbit_access");}
    String upbitSecret(){return get("upbit_secret");}
    boolean hasKiwoom(){return !kiwoomKey().isEmpty()&&!kiwoomSecret().isEmpty();}
    boolean hasUpbit(){return !upbitAccess().isEmpty()&&!upbitSecret().isEmpty();}
    private static String masked(String v){
        if(v==null||v.isEmpty())return "";
        int n=v.length();return (n<=4?"****":"••••"+v.substring(n-4));
    }
    JSONObject status(){
        JSONObject o=new JSONObject();
        try{
            String kk=kiwoomKey(),ks=kiwoomSecret(),ua=upbitAccess(),us=upbitSecret();
            o.put("kiwoom_saved",!kk.isEmpty()&&!ks.isEmpty());
            o.put("upbit_saved",!ua.isEmpty()&&!us.isEmpty());
            o.put("kiwoom_key_masked",masked(kk));
            o.put("upbit_access_masked",masked(ua));
            o.put("kiwoom_key_len",kk.length());
            o.put("upbit_access_len",ua.length());
        }catch(Exception ignored){}
        return o;
    }
}
