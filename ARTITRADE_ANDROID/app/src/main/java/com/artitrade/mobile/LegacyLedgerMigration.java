package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * One-time migration of the latest Windows EXE paper ledger into the phone-main runtime.
 *
 * The PC helper uploads a consistent SQLite-backup archive to Phone Hub. This class
 * downloads it over loopback before Python starts, refuses to overwrite phone trades,
 * backs up any empty/new phone files, and installs the normalized EXE databases.
 */
final class LegacyLedgerMigration {
    private static final String PREF="artitrade_pc_ledger_import_v1";
    private static final String SCHEMA="ARTITRADE_PC_LEDGER_MIGRATION_V1";
    private static final Set<String> ALLOWED=new HashSet<>(Arrays.asList(
        "manifest.json",
        "stock/ARTITRADE_D.sqlite3",
        "stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
        "stock/AT_CLOSE/AT_CLOSE_PAPER.sqlite3",
        "stock/PROJECTGMO_V1.0_research.sqlite3",
        "stock/PROJECTGMO_V1.0_account_state.json",
        "coin/base/upbit_paper.sqlite3",
        "coin/ma/upbit_paper.sqlite3"
    ));

    private final Context context;
    private final RuntimeCrashStore crashes;
    LegacyLedgerMigration(Context c,RuntimeCrashStore crashes){
        this.context=c.getApplicationContext();this.crashes=crashes;
    }
    private SharedPreferences prefs(){return context.getSharedPreferences(PREF,Context.MODE_PRIVATE);}
    private File runtimeRoot(){return new File(context.getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");}
    private static JSONObject obj(Object... pairs){
        JSONObject o=new JSONObject();try{for(int i=0;i<pairs.length;i+=2)o.put((String)pairs[i],pairs[i+1]);}catch(Exception ignored){}return o;
    }
    private static String sha256(File file)throws Exception{
        MessageDigest md=MessageDigest.getInstance("SHA-256");byte[] buf=new byte[65536];
        try(FileInputStream in=new FileInputStream(file)){int n;while((n=in.read(buf))!=-1)md.update(buf,0,n);}
        StringBuilder b=new StringBuilder(64);for(byte x:md.digest())b.append(String.format(Locale.ROOT,"%02x",x&255));return b.toString();
    }
    private static String sha256(byte[] data)throws Exception{
        MessageDigest md=MessageDigest.getInstance("SHA-256");byte[] d=md.digest(data);StringBuilder b=new StringBuilder(64);
        for(byte x:d)b.append(String.format(Locale.ROOT,"%02x",x&255));return b.toString();
    }
    private static void copy(File source,File target)throws Exception{
        File parent=target.getParentFile();if(parent!=null&&!parent.exists()&&!parent.mkdirs())throw new IOException("cannot create "+parent);
        try(FileInputStream in=new FileInputStream(source);FileOutputStream out=new FileOutputStream(target,false)){
            byte[] buf=new byte[65536];int n;while((n=in.read(buf))!=-1)out.write(buf,0,n);out.flush();out.getFD().sync();
        }
    }
    private static void deleteTree(File f){
        if(f==null||!f.exists())return;if(f.isDirectory()){File[] xs=f.listFiles();if(xs!=null)for(File x:xs)deleteTree(x);}f.delete();
    }
    private static long count(File db,String table){
        if(db==null||!db.isFile())return 0;
        SQLiteDatabase c=null;Cursor q=null;
        try{
            c=SQLiteDatabase.openDatabase(db.getAbsolutePath(),null,SQLiteDatabase.OPEN_READONLY);
            q=c.rawQuery("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",new String[]{table});
            if(!q.moveToFirst()||q.getLong(0)==0){q.close();q=null;return 0;}
            q.close();q=c.rawQuery("SELECT COUNT(*) FROM "+table,null);return q.moveToFirst()?q.getLong(0):0;
        }catch(Exception ignored){return 0;}
        finally{if(q!=null)q.close();if(c!=null)c.close();}
    }
    private boolean phoneHasTrades(){
        File r=runtimeRoot();long total=0;
        total+=count(new File(r,"stock/ARTITRADE_D.sqlite3"),"executions");
        total+=count(new File(r,"stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3"),"executions");
        total+=count(new File(r,"stock/AT_CLOSE/AT_CLOSE_PAPER.sqlite3"),"executions");
        total+=count(new File(r,"coin/base/upbit_paper.sqlite3"),"executions");
        total+=count(new File(r,"coin/ma/upbit_paper.sqlite3"),"executions");
        File gmo=new File(r,"stock/PROJECTGMO_V1.0_account_state.json");
        if(gmo.isFile()){
            try(FileInputStream in=new FileInputStream(gmo)){
                ByteArrayOutputStream out=new ByteArrayOutputStream((int)Math.min(gmo.length(),4_000_000));
                byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1&&out.size()<4_000_000)out.write(b,0,n);
                JSONObject x=new JSONObject(new String(out.toByteArray(),StandardCharsets.UTF_8));
                JSONObject pos=x.optJSONObject("positions");JSONArray fills=x.optJSONArray("fills");
                if((pos!=null&&pos.length()>0)||(fills!=null&&fills.length()>0))total++;
            }catch(Exception ignored){}
        }
        return total>0;
    }
    private JSONObject extract(File archive,File staging)throws Exception{
        deleteTree(staging);if(!staging.mkdirs()&&!staging.isDirectory())throw new IOException("staging unavailable");
        JSONObject manifest=null;String root=staging.getCanonicalPath()+File.separator;
        try(ZipInputStream zin=new ZipInputStream(new BufferedInputStream(new FileInputStream(archive)))){
            ZipEntry e;byte[] buf=new byte[65536];
            while((e=zin.getNextEntry())!=null){
                String name=e.getName().replace('\\','/');
                if(e.isDirectory()){zin.closeEntry();continue;}
                if(!ALLOWED.contains(name))throw new SecurityException("unexpected migration entry: "+name);
                File out=new File(staging,name);String canonical=out.getCanonicalPath();
                if(!canonical.startsWith(root))throw new SecurityException("invalid migration path");
                File parent=out.getParentFile();if(parent!=null&&!parent.exists()&&!parent.mkdirs())throw new IOException("cannot create staging parent");
                try(FileOutputStream fos=new FileOutputStream(out,false)){int n;while((n=zin.read(buf))!=-1)fos.write(buf,0,n);}
                if("manifest.json".equals(name)){
                    try(FileInputStream min=new FileInputStream(out)){
                        ByteArrayOutputStream m=new ByteArrayOutputStream();int n;while((n=min.read(buf))!=-1)m.write(buf,0,n);
                        manifest=new JSONObject(new String(m.toByteArray(),StandardCharsets.UTF_8));
                    }
                }
                zin.closeEntry();
            }
        }
        if(manifest==null||!SCHEMA.equals(manifest.optString("schema")))throw new SecurityException("migration manifest mismatch");
        return manifest;
    }
    private void install(File staging,String sha)throws Exception{
        File root=runtimeRoot();if(!root.exists()&&!root.mkdirs())throw new IOException("runtime root unavailable");
        String stamp=String.valueOf(System.currentTimeMillis());
        for(String rel:ALLOWED){
            if("manifest.json".equals(rel))continue;
            File source=new File(staging,rel);if(!source.isFile())continue;
            File target=new File(root,rel);
            if(target.isFile()){
                File backup=new File(target.getParentFile(),target.getName()+".pre_pc_import_"+stamp);
                copy(target,backup);
            }
            copy(source,target);
        }
        prefs().edit().putString("imported_sha256",sha).putLong("imported_at",System.currentTimeMillis()).commit();
    }

    JSONObject importIfReady(){
        try{
            LocalHubClient hub=new LocalHubClient(context);
            JSONObject status=hub.migrationStatus();
            if(!status.optBoolean("ok")||!status.optBoolean("ready")||!status.optBoolean("archive_exists",false))
                return obj("ok",true,"status","NO_PC_ARCHIVE");
            String session=status.optString("session"),sha=status.optString("archive_sha256");
            if(sha.equalsIgnoreCase(prefs().getString("imported_sha256",""))){
                try{hub.migrationAck(session,sha);}catch(Exception ignored){}
                return obj("ok",true,"status","ALREADY_IMPORTED","sha256",sha);
            }
            if(phoneHasTrades()){
                JSONObject blocked=obj("ok",false,"status","PHONE_HAS_TRADES","message","Phone ledger already contains executions; automatic PC overwrite blocked","sha256",sha);
                prefs().edit().putString("last_status",blocked.toString()).commit();return blocked;
            }
            int chunks=status.optInt("read_chunks",0);long total=status.optLong("total_bytes",-1);
            if(chunks<1||total<1||total>250L*1024L*1024L)throw new SecurityException("invalid migration size");
            File archive=new File(context.getCacheDir(),"pc_ledger_import_"+session+".zip");
            try(FileOutputStream out=new FileOutputStream(archive,false)){
                long wrote=0;
                for(int i=0;i<chunks;i++){
                    JSONObject part=hub.migrationChunk(session,i);
                    if(!part.optBoolean("ok")||part.optInt("index",-1)!=i)throw new IOException("migration chunk "+i+" unavailable");
                    byte[] data=Base64.decode(part.optString("data_b64"),Base64.DEFAULT);
                    if(!sha256(data).equalsIgnoreCase(part.optString("sha256")))throw new SecurityException("migration chunk hash mismatch");
                    out.write(data);wrote+=data.length;
                }
                out.flush();out.getFD().sync();
                if(wrote!=total)throw new IOException("migration byte count mismatch");
            }
            if(!sha256(archive).equalsIgnoreCase(sha))throw new SecurityException("migration archive hash mismatch");
            File staging=new File(context.getCacheDir(),"pc_ledger_stage_"+session);
            JSONObject manifest=extract(archive,staging);
            install(staging,sha);
            deleteTree(staging);archive.delete();
            JSONObject ack=hub.migrationAck(session,sha);
            JSONObject result=obj("ok",true,"status","IMPORTED","sha256",sha,"manifest",manifest,"ack",ack);
            prefs().edit().putString("last_status",result.toString()).commit();
            return result;
        }catch(Throwable e){
            crashes.add("pc_ledger_migration",e);
            JSONObject result=obj("ok",false,"status","IMPORT_FAILED","type",e.getClass().getSimpleName(),"message",String.valueOf(e.getMessage()));
            prefs().edit().putString("last_status",result.toString()).commit();return result;
        }
    }
    JSONObject status(){
        try{
            String last=prefs().getString("last_status","");
            JSONObject o=last.isEmpty()?new JSONObject():new JSONObject(last);
            o.put("imported_sha256",prefs().getString("imported_sha256","")).put("imported_at",prefs().getLong("imported_at",0L));
            return o;
        }catch(Exception e){return new JSONObject();}
    }
}
