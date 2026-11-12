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
import java.util.zip.ZipFile;

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
    private static final long MAX_MIGRATION_ZIP=4L*1024L*1024L*1024L;
    private static final long MAX_EXPANDED_DATA=16L*1024L*1024L*1024L;
    private static final long MIN_FREE_SPACE=128L*1024L*1024L;
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
    private SharedPreferences progressPrefs(){return context.getSharedPreferences("artitrade_pc_ledger_import_progress_v078",Context.MODE_PRIVATE);}
    private File runtimeRoot(){return new File(context.getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");}
    private void progress(String stage,String detail,long done,long total,boolean complete,String error){
        int percent=total>0?(int)Math.max(0,Math.min(100,(done*100L)/total)):(complete?100:0);
        progressPrefs().edit()
            .putString("stage",stage==null?"":stage)
            .putString("detail",detail==null?"":detail)
            .putLong("done",done).putLong("total",total).putInt("percent",percent)
            .putLong("updated_at",System.currentTimeMillis())
            .putBoolean("complete",complete).putString("error",error==null?"":error)
            .apply();
    }
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
    private boolean phoneHasRealBrokerFills(){
        File journal=new File(runtimeRoot(),"broker/live_order_journal.sqlite3");
        if(!journal.isFile())return false;
        SQLiteDatabase c=null;Cursor q=null;
        try{
            c=SQLiteDatabase.openDatabase(journal.getAbsolutePath(),null,SQLiteDatabase.OPEN_READONLY);
            q=c.rawQuery("SELECT COUNT(*) FROM live_orders WHERE status='FILLED'",null);
            return q.moveToFirst()&&q.getLong(0)>0;
        }catch(Exception ignored){return false;}
        finally{if(q!=null)q.close();if(c!=null)c.close();}
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
        if(manifest==null||!SCHEMA.equals(manifest.optString("schema")))
            throw new SecurityException("migration manifest mismatch");
        JSONObject files=manifest.optJSONObject("files");
        if(files==null)throw new SecurityException("migration file hashes missing");
        java.util.Iterator<String> names=files.keys();
        while(names.hasNext()){
            String name=names.next();
            if(!ALLOWED.contains(name))throw new SecurityException("invalid manifest entry");
            JSONObject entry=files.optJSONObject(name);
            File saved=new File(staging,name);
            if(entry==null||!saved.isFile()||entry.optLong("size",-1)!=saved.length()||
                !sha256(saved).equalsIgnoreCase(entry.optString("sha256")))
                throw new SecurityException("Migration integrity check failed: "+name);
        }
        return manifest;
    }
    private long expandedArchiveBytes(File archive)throws Exception{
        long total=0;Set<String> seen=new HashSet<>();
        try(ZipFile zip=new ZipFile(archive)){
            Enumeration<? extends ZipEntry> entries=zip.entries();
            while(entries.hasMoreElements()){
                ZipEntry e=entries.nextElement();
                if(e.isDirectory())continue;
                String name=e.getName().replace('\\','/');
                if(!ALLOWED.contains(name)||!seen.add(name))
                    throw new SecurityException("Unexpected or duplicate archive entry: "+name);
                long size=e.getSize();
                if(size<0||total>MAX_EXPANDED_DATA-size)
                    throw new SecurityException("Migration archive decompressed size exceeded safe limit");
                total+=size;
            }
        }
        for(String required:new String[]{"stock/ARTITRADE_D.sqlite3",
                "stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
                "coin/base/upbit_paper.sqlite3","coin/ma/upbit_paper.sqlite3"})
            if(!seen.contains(required))throw new SecurityException("Required PC ledger missing: "+required);
        return total;
    }
    private long oldFilesBackupBytes(){
        long bytes=0;File root=runtimeRoot();
        for(String rel:ALLOWED){
            if("manifest.json".equals(rel))continue;
            File f=new File(root,rel);
            if(f.isFile())bytes+=f.length();
        }
        return bytes;
    }
    private static long freeStorageBytes(File base){
        return new android.os.StatFs(base.getAbsolutePath()).getAvailableBytes();
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
            // Same-app cache and runtime files share the filesystem. A verified
            // atomic rename avoids a second full raw-DB copy during migration.
            try{
                java.nio.file.Files.move(source.toPath(),target.toPath(),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING,
                    java.nio.file.StandardCopyOption.ATOMIC_MOVE);
            }catch(java.nio.file.AtomicMoveNotSupportedException e){
                java.nio.file.Files.move(source.toPath(),target.toPath(),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            }
        }
        prefs().edit().putString("imported_sha256",sha).putLong("imported_at",System.currentTimeMillis()).commit();
    }

    JSONObject importIfReady(){
        try{
            progress("CHECK","MSERVER 승계자료 확인",0,1,false,"");
            LocalHubClient hub=new LocalHubClient(context);
            JSONObject status=hub.migrationStatus();
            if(!status.optBoolean("ok")||!status.optBoolean("ready")||!status.optBoolean("archive_exists",false)){
                progress("WAITING","PC EXE 원장 승계자료 대기",0,1,false,"");
                return obj("ok",true,"status","NO_PC_ARCHIVE");
            }
            String session=status.optString("session"),sha=status.optString("archive_sha256");
            if(sha.equalsIgnoreCase(prefs().getString("imported_sha256",""))){
                try{hub.migrationAck(session,sha);}catch(Exception ignored){}
                progress("COMPLETE","기존 PC EXE 원장 승계 완료",1,1,true,"");
                return obj("ok",true,"status","ALREADY_IMPORTED","sha256",sha);
            }
            if(phoneHasRealBrokerFills()){
                JSONObject blocked=obj("ok",false,"status","PHONE_HAS_REAL_FILLS",
                    "message","Actual broker FILLED orders exist; automatic historical replacement blocked for reconciliation","sha256",sha);
                prefs().edit().putString("last_status",blocked.toString()).commit();
                progress("BLOCKED","실제 브로커 체결 존재 · 자동 원장 교체 차단",0,1,false,"PHONE_HAS_REAL_FILLS");
                return blocked;
            }
            int chunks=status.optInt("read_chunks",0);long total=status.optLong("total_bytes",-1);
            if(chunks<1||total<1||total>MAX_MIGRATION_ZIP||
               chunks!=(total+240000L-1)/240000L)
                throw new SecurityException("Invalid migration chunk manifest");
            long initialFree=freeStorageBytes(context.getCacheDir());
            if(initialFree<total+MIN_FREE_SPACE)
                throw new IOException("S21+ 저장공간 부족 · ZIP 수신 필요 "+
                    ((total+MIN_FREE_SPACE+1048575)/1048576)+"MB, 여유 "+
                    (initialFree/1048576)+"MB");
            File archive=new File(context.getCacheDir(),"pc_ledger_import_"+session+".zip");
            progress("DOWNLOAD","MSERVER에서 PC EXE 원장 수신",0,chunks,false,"");
            try(FileOutputStream out=new FileOutputStream(archive,false)){
                long wrote=0;
                for(int i=0;i<chunks;i++){
                    JSONObject part=hub.migrationChunk(session,i);
                    if(!part.optBoolean("ok")||part.optInt("index",-1)!=i)throw new IOException("migration chunk "+i+" unavailable");
                    byte[] data=Base64.decode(part.optString("data_b64"),Base64.DEFAULT);
                    if(!sha256(data).equalsIgnoreCase(part.optString("sha256")))throw new SecurityException("migration chunk hash mismatch");
                    out.write(data);wrote+=data.length;
                    progress("DOWNLOAD","PC EXE 원장 수신 "+(i+1)+"/"+chunks,i+1,chunks,false,"");
                }
                out.flush();out.getFD().sync();
                if(wrote!=total)throw new IOException("migration byte count mismatch");
            }
            progress("VERIFY","PC EXE 원장 무결성 검사",0,1,false,"");
            if(!sha256(archive).equalsIgnoreCase(sha))throw new SecurityException("migration archive hash mismatch");
            long expanded=expandedArchiveBytes(archive);
            long remain=freeStorageBytes(context.getCacheDir());
            long needed=expanded+oldFilesBackupBytes()+MIN_FREE_SPACE;
            if(remain<needed)
                throw new IOException("S21+ 저장공간 부족 · 압축 해제/기존 데이터 백업 필요 "+
                    ((needed+1048575)/1048576)+"MB, 여유 "+(remain/1048576)+"MB");
            progress("EXTRACT","PC EXE 원장 압축 해제",0,1,false,"");
            File staging=new File(context.getCacheDir(),"pc_ledger_stage_"+session);
            JSONObject manifest=extract(archive,staging);
            progress("INSTALL","기존 PC EXE 원장을 폰 전략 DB로 승계",0,1,false,"");
            install(staging,sha);
            deleteTree(staging);archive.delete();
            JSONObject ack=hub.migrationAck(session,sha);
            JSONObject result=obj("ok",true,"status","IMPORTED","sha256",sha,"manifest",manifest,"ack",ack);
            prefs().edit().putString("last_status",result.toString()).commit();
            progress("COMPLETE","기존 PC EXE 원장 승계 완료",1,1,true,"");
            return result;
        }catch(Throwable e){
            crashes.add("pc_ledger_migration",e);
            JSONObject result=obj("ok",false,"status","IMPORT_FAILED","type",e.getClass().getSimpleName(),"message",String.valueOf(e.getMessage()));
            prefs().edit().putString("last_status",result.toString()).commit();
            progress("FAILED","PC EXE 원장 승계 실패",0,1,false,e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()));
            return result;
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
