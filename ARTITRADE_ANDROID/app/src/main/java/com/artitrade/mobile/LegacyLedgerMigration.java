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

    // A migration may contain every saved research/data file under known EXE roots.
    // Match the PC migrator's allow rules; never allow traversal, hidden files or credentials.
    private static final String[] SECRET_MARKS={"credential","secret","token","password","apikey",
        "api_key","private_key","access_key","refresh_key","keyring","dpapi","auth_cache","broker_key"};
    private static boolean allowedDataPath(String name){
        if("manifest.json".equals(name))return true;
        if(ALLOWED.contains(name))return true;
        if(name==null||name.length()>320||name.indexOf('\\')>=0||name.indexOf(':')>=0
            ||name.startsWith("/")||name.contains("//"))return false;
        if(!(name.startsWith("stock/")||name.startsWith("coin/base/")||name.startsWith("coin/ma/")))
            return false;
        String[] parts=name.split("/");
        if(parts.length<2||parts.length>12)return false;
        for(String part:parts){
            if(part.isEmpty()||part.equals(".")||part.equals("..")||part.startsWith("."))return false;
            String lower=part.toLowerCase(Locale.ROOT);
            for(String mark:SECRET_MARKS)if(lower.contains(mark))return false;
            if(lower.equals("cache")||lower.equals("tmp")||lower.equals("temp"))return false;
        }
        String lower=name.toLowerCase(Locale.ROOT);
        if(lower.contains(".pre_pc_import_"))return false;
        for(String ext:new String[]{".sqlite3",".db",".json",".jsonl",".csv",".parquet"})
            if(lower.endsWith(ext))return true;
        return false;
    }

    // Older PC migration EXEs accidentally included SQLite backup sidecars in ZIP.
    // The backup API already produced a complete main DB; these transient files
    // MUST NEVER be installed, but must not prevent first-import recovery.
    private static boolean isSnapshotSidecar(String name){
        for(String suffix:new String[]{"-shm","-wal","-journal"}){
            if(name.endsWith(suffix)){
                String base=name.substring(0,name.length()-suffix.length());
                return base.endsWith(".sqlite3")&&ALLOWED.contains(base);
            }
        }
        return false;
    }

    private final Context context;
    private final RuntimeCrashStore crashes;
    LegacyLedgerMigration(Context c,RuntimeCrashStore crashes){
        this.context=c.getApplicationContext();this.crashes=crashes;
    }
    private SharedPreferences prefs(){return context.getSharedPreferences(PREF,Context.MODE_PRIVATE);}
    private SharedPreferences progressPrefs(){return context.getSharedPreferences("artitrade_pc_ledger_import_progress_v078",Context.MODE_PRIVATE);}
    private File runtimeRoot(){return new File(context.getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");}
    // PC ZIP name is AT_CLOSE; the embedded phone strategy actually opens stock/close.
    private File deployedFile(String rel){
        return new File(runtimeRoot(),"stock/AT_CLOSE/AT_CLOSE_PAPER.sqlite3".equals(rel)
            ?"stock/close/AT_CLOSE_PAPER.sqlite3":rel);
    }
    private static void backupWithWal(File db,File backup)throws Exception{
        copy(db,backup);
        for(String ext:new String[]{"-wal","-shm"}){
            File side=new File(db.getAbsolutePath()+ext);
            if(side.isFile())copy(side,new File(backup.getAbsolutePath()+ext));
        }
    }
    private static void removeStaleWal(File db)throws Exception{
        for(String ext:new String[]{"-wal","-shm"}){
            File side=new File(db.getAbsolutePath()+ext);
            if(side.exists()&&!side.delete())throw new IOException("SQLite WAL still in use: "+side);
        }
    }
    private static long historyCount(File db){
        return count(db,"positions")+count(db,"executions")+count(db,"signals")
            +count(db,"candidates")+count(db,"research_candidates");
    }
    // Fix old app installs which kept CLOSE DB where the phone strategy never looked.
    private void repairLegacyClosePath()throws Exception{
        File old=new File(runtimeRoot(),"stock/AT_CLOSE/AT_CLOSE_PAPER.sqlite3");
        if(!old.isFile())return;
        File target=deployedFile("stock/AT_CLOSE/AT_CLOSE_PAPER.sqlite3");
        if(target.isFile()&&historyCount(target)>0){
            // The previous release left the PC CLOSE database at the old path.
            // If the active runtime now contains trades, never replace it and
            // never abort the restoration of the other five strategy ledgers.
            prefs().edit().putBoolean("close_path_conflict_preserved",true).apply();
            return;
        }
        File parent=target.getParentFile();
        if(!parent.isDirectory()&&!parent.mkdirs())throw new IOException("Cannot create CLOSE ledger folder");
        if(target.isFile()){
            backupWithWal(target,new File(parent,"AT_CLOSE_PAPER.sqlite3.pre_close_repair_"+System.currentTimeMillis()));
            removeStaleWal(target);
        }
        java.nio.file.Files.move(old.toPath(),target.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        for(String side:new String[]{"-wal","-shm"}){
            File oldSide=new File(old.getAbsolutePath()+side);
            if(oldSide.isFile()){
                File newSide=new File(target.getAbsolutePath()+side);
                java.nio.file.Files.move(oldSide.toPath(),newSide.toPath(),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            }
        }
    }
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
        }catch(Exception ignored){return true;} // Unknown live-fill journal state must block replacement.
        finally{if(q!=null)q.close();if(c!=null)c.close();}
    }
    private JSONObject extract(File archive,File staging)throws Exception{
        deleteTree(staging);if(!staging.mkdirs()&&!staging.isDirectory())throw new IOException("staging unavailable");
        JSONObject manifest=null;String root=staging.getCanonicalPath()+File.separator;
        int entriesSeen=0;
        try(ZipInputStream zin=new ZipInputStream(new BufferedInputStream(new FileInputStream(archive)))){
            ZipEntry e;byte[] buf=new byte[65536];
            while((e=zin.getNextEntry())!=null){
                String name=e.getName().replace('\\','/');
                if(e.isDirectory()){zin.closeEntry();continue;}
                if(++entriesSeen>2001)throw new SecurityException("too many migration members");
                if(isSnapshotSidecar(name)){
                    // Ignore the transient sidecar of a known SQLite snapshot.
                    zin.closeEntry();continue;
                }
                if(!allowedDataPath(name))throw new SecurityException("unexpected migration entry: "+name);
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
        if(files==null||files.length()>2000)throw new SecurityException("migration file hashes missing/too many");
        java.util.Iterator<String> names=files.keys();
        while(names.hasNext()){
            String name=names.next();
            if(!allowedDataPath(name)||"manifest.json".equals(name))throw new SecurityException("invalid manifest entry");
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
                if(!(allowedDataPath(name)||isSnapshotSidecar(name))||!seen.add(name))
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
        JSONObject listed=storedManifest()==null?null:storedManifest().optJSONObject("files");
        Set<String> entries=new HashSet<>(ALLOWED);
        if(listed!=null){Iterator<String> keys=listed.keys();while(keys.hasNext())entries.add(keys.next());}
        for(String rel:entries){
            if("manifest.json".equals(rel)||!allowedDataPath(rel))continue;
            File f=deployedFile(rel);
            if(f.isFile()){
                bytes+=f.length();
                for(String ext:new String[]{"-wal","-shm"}){
                    File side=new File(f.getAbsolutePath()+ext);
                    if(side.isFile())bytes+=side.length();
                }
            }
        }
        return bytes;
    }
    private static long freeStorageBytes(File base){
        return new android.os.StatFs(base.getAbsolutePath()).getAvailableBytes();
    }
    private static void collectStageFiles(File base,File current,List<String> paths)throws Exception{
        File[] entries=current.listFiles();
        if(entries==null)throw new IOException("Cannot list migrated files");
        for(File child:entries){
            if(child.isDirectory()){collectStageFiles(base,child,paths);continue;}
            String rel=base.toPath().relativize(child.toPath()).toString().replace(File.separatorChar,'/');
            if(!allowedDataPath(rel))throw new SecurityException("Unexpected staged research file: "+rel);
            paths.add(rel);
        }
    }
    private void install(File staging,String sha)throws Exception{
        File root=runtimeRoot();if(!root.exists()&&!root.mkdirs())throw new IOException("runtime root unavailable");
        String stamp=String.valueOf(System.currentTimeMillis());
        File[] ignored=null; // install uses manifest-validated stage entries, not a legacy fixed file list.
        ArrayList<String> entries=new ArrayList<>();
        collectStageFiles(staging,staging,entries);
        for(String rel:entries){
            if("manifest.json".equals(rel))continue;
            File source=new File(staging,rel);if(!source.isFile())continue;
            File target=deployedFile(rel);
            File parent=target.getParentFile();
            if(parent!=null&&!parent.isDirectory()&&!parent.mkdirs())
                throw new IOException("Cannot create restored ledger directory: "+parent);
            if(target.isFile()){
                File backup=new File(target.getParentFile(),target.getName()+".pre_pc_import_"+stamp);
                backupWithWal(target,backup);
                removeStaleWal(target);
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
        // Do not mark import successful here: the Python strategy must read the installed DBs first.
    }

    private JSONObject storedManifest(){
        try{
            String saved=prefs().getString("verified_manifest","");
            if(!saved.isEmpty())return new JSONObject(saved);
            String installed=prefs().getString("installed_manifest","");
            if(!installed.isEmpty())return new JSONObject(installed);
            JSONObject previous=new JSONObject(prefs().getString("last_status","{}"));
            return previous.optJSONObject("manifest");
        }catch(Exception ignored){return null;}
    }
    private static boolean historyTable(String name){
        String n=name.toLowerCase(Locale.ROOT);
        for(String part:new String[]{"position","execution","order","candidate","signal","shadow",
                "research","event","trade","fill","history","restoration"})
            if(n.contains(part))return true;
        return false;
    }
    private JSONObject verifyInstalled(JSONObject manifest,boolean exactSnapshot)throws Exception{
        if(manifest==null||!SCHEMA.equals(manifest.optString("schema")))
            throw new SecurityException("Restored PC manifest is unavailable");
        JSONObject files=manifest.optJSONObject("files");
        if(files==null||files.length()==0)throw new SecurityException("PC manifest has no database files");
        int verified=0,checkedTables=0;
        long sourceRows=0,verifiedRows=0;
        Iterator<String> names=files.keys();
        while(names.hasNext()){
            String rel=names.next();
            if(!allowedDataPath(rel)||"manifest.json".equals(rel))
                throw new SecurityException("Invalid PC ledger path: "+rel);
            JSONObject expected=files.optJSONObject(rel);
            File target=deployedFile(rel);
            if(expected==null||!target.isFile())throw new IOException("PC ledger missing: "+rel);
            // A running SQLite engine can modify a valid DB and checkpoint its WAL.
            if(exactSnapshot&&(target.length()!=expected.optLong("size",-1)
                ||!sha256(target).equalsIgnoreCase(expected.optString("sha256"))))
                throw new IOException("PC ledger install SHA mismatch: "+rel);
            verified++;
            JSONObject counts=expected.optJSONObject("counts");
            if(counts==null)continue;
            Iterator<String> tables=counts.keys();
            while(tables.hasNext()){
                String table=tables.next();
                if(!historyTable(table))continue;
                long want=counts.optLong(table,-1);
                if(want<0)continue;
                sourceRows+=want;
                // The ZIP's source table counts were captured with Python's sqlite3 backup API.
                // Android's platform SQLite engine may be older than the bundled Chaquopy
                // SQLite and can return zero even for a readable database. Verify bytes
                // before engine start and use the SAME Python SQLite as the strategy
                // to verify actual per-table counts after startup.
                if(exactSnapshot){verifiedRows+=want;checkedTables++;}
                else if(!rel.endsWith(".sqlite3"))verifiedRows+=want;
            }
        }
        return obj("files_verified",verified,"source_rows",sourceRows,
            "verified_rows",verifiedRows,"checked_tables",checkedTables,
            "verification_phase",exactSnapshot?"BEFORE_ENGINE":"AFTER_ENGINE");
    }
    private static long runtimeRows(Object node){
        if(!(node instanceof JSONObject))return 0;
        JSONObject data=(JSONObject)node;long total=0;
        Iterator<String> keys=data.keys();
        while(keys.hasNext()){
            String key=keys.next();Object value=data.opt(key);
            if(value instanceof JSONObject)total+=runtimeRows(value);
            else if(value instanceof JSONArray&&Arrays.asList("positions","pending","executions",
                "items","candidate_rows","score_items","restorations","signals","research_candidates").contains(key))
                total+=((JSONArray)value).length();
        }
        return total;
    }
    private static String keyForFile(String rel){
        if(rel.startsWith("coin/base/"))return "coin/general";
        if(rel.startsWith("coin/ma/"))return "coin/ma";
        if(rel.contains("STOCK_MA_ENGINE_V1"))return "stock/ma";
        if(rel.contains("AT_CLOSE"))return "stock/close";
        if(rel.contains("PROJECTGMO"))return "stock/gmo";
        return "stock/at";
    }
    private static JSONObject runtimeSegment(JSONObject records,String market,String name){
        JSONObject marketRecord=records==null?null:records.optJSONObject(market);
        String rootStrategy="stock".equals(market)
            ?("close".equals(name)?"cw":"at"):name;
        JSONObject root=marketRecord==null?null:marketRecord.optJSONObject(rootStrategy);
        if(root==null)return new JSONObject();
        JSONObject ledger=root.optJSONObject("ledger"),research=root.optJSONObject("research");
        if("stock".equals(market)&&("ma".equals(name)||"gmo".equals(name))){
            ledger=ledger==null?null:ledger.optJSONObject("ma".equals(name)?"ma_subledger":"gmo");
            research=research==null?null:research.optJSONObject(name);
        }
        return obj("ledger",ledger==null?new JSONObject():ledger,
            "research",research==null?new JSONObject():research);
    }
    private JSONObject strategyCounts(JSONObject manifest,JSONObject records)throws Exception{
        JSONObject files=manifest.optJSONObject("files"),report=new JSONObject();
        if(files==null)throw new IOException("Missing source history manifest");
        Iterator<String> names=files.keys();
        while(names.hasNext()){
            String rel=names.next();JSONObject entry=files.optJSONObject(rel);
            JSONObject counts=entry==null?null:entry.optJSONObject("counts");
            if(counts==null)continue;
            String strategy=keyForFile(rel);
            JSONObject row=report.optJSONObject(strategy);
            if(row==null){row=obj("pc_rows",0L);report.put(strategy,row);}
            long from=row.optLong("pc_rows",0L);
            Iterator<String> tables=counts.keys();
            while(tables.hasNext()){
                String t=tables.next();
                if(historyTable(t))from+=Math.max(0L,counts.optLong(t,0L));
            }
            row.put("pc_rows",from);
        }
        for(String key:new String[]{"stock/at","stock/ma","stock/close","stock/gmo",
                "coin/general","coin/ma"}){
            JSONObject row=report.optJSONObject(key);if(row==null)continue;
            String[] parts=key.split("/");
            long rows=runtimeRows(runtimeSegment(records,parts[0],parts[1]));
            row.put("runtime_rows",rows);
            if(row.optLong("pc_rows",0L)>0L&&rows==0L)
                throw new IOException("PC_LEDGER_NOT_VISIBLE: "+key+" (source="+
                    row.optLong("pc_rows")+" runtime=0)");
        }
        return report;
    }
    JSONObject finishRuntimeVerification(JSONObject installResult,JSONObject engineResult,
                                           JSONObject records,JSONObject pythonAudit){
        String initial=installResult==null?"":installResult.optString("status","");
        if(!"FILES_INSTALLED".equals(initial)&&!"LOCAL_FILES_PRESENT".equals(initial))
            return installResult==null?obj("ok",false,"status","IMPORT_RESULT_MISSING"):installResult;
        try{
            if(engineResult==null||!engineResult.optBoolean("ok",true))
                throw new IllegalStateException("PYTHON_STRATEGY_START_FAILED: "+
                    (engineResult==null?"missing result":engineResult.toString()));
            JSONObject manifest=installResult.optJSONObject("manifest");
            JSONObject verification=verifyInstalled(manifest,false);
            if(pythonAudit==null||!pythonAudit.optBoolean("ok",false))
                throw new IOException("PHONE_SQLITE_AUDIT_FAILED: "+
                    (pythonAudit==null?"audit missing":pythonAudit.optString("message",pythonAudit.toString())));
            verification.put("python_db_audit",pythonAudit);
            verification.put("by_strategy",strategyCounts(manifest,records));
            long sourceRows=verification.optLong("source_rows",0L);
            long visibleRows=runtimeRows(records);
            verification.put("runtime_rows",visibleRows);
            if(sourceRows>0&&visibleRows==0)
                throw new IllegalStateException("RUNTIME_RECORDS_EMPTY: source="+sourceRows+
                    " rows but strategy output has no positions/pending/executions/research; PC archive retained");
            if(sourceRows==0&&visibleRows==0)
                throw new IllegalStateException("PC_SOURCE_EMPTY: PC ledger contains no historical trading or research rows");
            String session=installResult.optString("session","");
            String sha=installResult.optString("sha256","");
            // Durable, first-success-only checkpoint BEFORE consuming the server archive:
            // a crash or ACK timeout must not cause phone trades to be replaced later.
            if(!prefs().edit().putString("imported_sha256",sha)
                .putLong("imported_at",System.currentTimeMillis())
                .putString("verified_manifest",manifest.toString()).commit())
                throw new IOException("Cannot persist verified import checkpoint");
            String ackWarning="";
            if(!session.isEmpty()){
                try{
                    JSONObject ack=new LocalHubClient(context).migrationAck(session,sha);
                    if(!ack.optBoolean("ok",false))ackWarning="MSERVER_ARCHIVE_ACK_PENDING: "+
                        ack.optString("error","acknowledge_failed");
                }catch(Exception e){
                    ackWarning="MSERVER_ARCHIVE_ACK_PENDING: "+e.getClass().getSimpleName();
                }
            }
            JSONObject result=obj("ok",true,
                "status","FILES_INSTALLED".equals(initial)?"IMPORTED":"ALREADY_IMPORTED",
                "sha256",sha,"manifest",manifest,"verification",verification,
                "archive_cleanup",ackWarning.isEmpty()?"ACKED":"PENDING",
                "archive_cleanup_warning",ackWarning);
            prefs().edit().putString("last_status",result.toString()).commit();
            progress("COMPLETE","PC 원장 복원 및 전략엔진 반영 확인 · "+visibleRows+"건",1,1,true,"");
            return result;
        }catch(Throwable e){
            crashes.add("pc_ledger_runtime_verify",e);
            JSONObject failed=obj("ok",false,"status","RUNTIME_VERIFY_FAILED",
                "type",e.getClass().getSimpleName(),"message",String.valueOf(e.getMessage()),
                "sha256",installResult.optString("sha256",""),
                "manifest",installResult.optJSONObject("manifest"),
                "python_db_audit",pythonAudit==null?new JSONObject():pythonAudit);
            prefs().edit().putString("last_status",failed.toString()).commit();
            progress("FAILED","PC 파일 복원 후 전략엔진 검증 실패",0,1,false,String.valueOf(e.getMessage()));
            return failed;
        }
    }

    /** Additive second PC archive: only paths absent in the first verified import.
     * Existing phone SQLite, GMO paper state, and broker fills are NEVER replaced.
     * This is the ordinary automatic PC-import step, not a separate GMO feature.
     */
    private JSONObject importOnlyMissingResearch(JSONObject previous,String previousSha)throws Exception{
        LocalHubClient hub=new LocalHubClient(context);
        JSONObject remote=hub.migrationStatus();
        if(!remote.optBoolean("ok")||!remote.optBoolean("ready")||
           !remote.optBoolean("archive_exists",false))return null;
        String incoming=remote.optString("archive_sha256","");
        if(incoming.isEmpty()||incoming.equalsIgnoreCase(previousSha)||
           incoming.equalsIgnoreCase(prefs().getString("last_supplement_sha256","")))return null;
        int chunks=remote.optInt("read_chunks",0);
        int readBytes=remote.optInt("read_chunk_bytes",240000);
        long total=remote.optLong("total_bytes",-1);
        if(chunks<1||total<1||total>MAX_MIGRATION_ZIP||readBytes<1||readBytes>1000000||
           chunks!=(total+readBytes-1)/readBytes)
            throw new SecurityException("Invalid supplemental migration transfer");
        long free=freeStorageBytes(context.getCacheDir());
        if(free<total+MIN_FREE_SPACE)
            throw new IOException("Insufficient storage to inspect research supplement");
        String session=remote.optString("session","");
        File archive=new File(context.getCacheDir(),"pc_data_supplement_"+session+".zip");
        File staging=new File(context.getCacheDir(),"pc_data_supplement_stage_"+session);
        try{
            progress("DOWNLOAD","PC 누락 연구자료 자동 확인",0,chunks,false,"");
            try(FileOutputStream out=new FileOutputStream(archive,false)){
                long received=0;
                for(int i=0;i<chunks;i++){
                    JSONObject part=hub.migrationChunk(session,i);
                    if(!part.optBoolean("ok")||part.optInt("index",-1)!=i)
                        throw new IOException("Supplement chunk missing: "+i);
                    byte[] data=Base64.decode(part.optString("data_b64"),Base64.DEFAULT);
                    if(!sha256(data).equalsIgnoreCase(part.optString("sha256")))
                        throw new SecurityException("Supplement chunk SHA mismatch: "+i);
                    out.write(data);received+=data.length;
                    progress("DOWNLOAD","PC 누락 연구자료 확인",i+1,chunks,false,"");
                }
                out.flush();out.getFD().sync();
                if(received!=total)throw new IOException("Supplement size mismatch");
            }
            if(!sha256(archive).equalsIgnoreCase(incoming))
                throw new SecurityException("Supplement archive SHA mismatch");
            long expanded=expandedArchiveBytes(archive);
            if(freeStorageBytes(context.getCacheDir())<expanded+MIN_FREE_SPACE)
                throw new IOException("Insufficient space to expand research supplement");
            JSONObject candidate=extract(archive,staging);
            JSONObject candidateFiles=candidate.optJSONObject("files");
            JSONObject previousFiles=previous.optJSONObject("files");
            if(candidateFiles==null||previousFiles==null)
                throw new SecurityException("Missing original/supplemental file manifest");
            JSONObject combined=new JSONObject(previous.toString());
            JSONObject combinedFiles=combined.getJSONObject("files");
            Iterator<String> iter=candidateFiles.keys();
            int added=0,retained=0;
            while(iter.hasNext()){
                String rel=iter.next();
                if(!allowedDataPath(rel)||"manifest.json".equals(rel))
                    throw new SecurityException("Invalid supplemental path: "+rel);
                File target=deployedFile(rel);
                // A phone-created file, an active GMO account, or a previously
                // migrated DB wins unconditionally. Never replace or merge it.
                if(target.exists()||previousFiles.has(rel)){retained++;continue;}
                File source=new File(staging,rel);
                File parent=target.getParentFile();
                if(parent!=null&&!parent.exists()&&!parent.mkdirs())
                    throw new IOException("Cannot create supplement destination");
                java.nio.file.Files.move(source.toPath(),target.toPath());
                combinedFiles.put(rel,candidateFiles.get(rel));
                added++;
            }
            if(added==0){
                prefs().edit().putString("last_supplement_sha256",incoming).apply();
                return null;
            }
            // Files are installed, but do NOT acknowledge the PC archive before
            // strategy runtime verification has read the new original records.
            if(!prefs().edit().putString("installed_manifest",combined.toString())
                .putString("installed_sha256",incoming)
                .putString("installed_session",session).commit())
                throw new IOException("Supplemental checkpoint save failed");
            progress("VERIFY_RUNTIME","기존 계좌 보존 · 연구자료 "+added+"개 추가, "+retained+"개 유지",
                0,1,false,"");
            return obj("ok",true,"status","FILES_INSTALLED",
                "sha256",incoming,"session",session,"manifest",combined,
                "verification",verifyInstalled(combined,false),"extra_files_added",added,
                "existing_files_kept",retained);
        }finally{
            deleteTree(staging);
            if(archive.exists())archive.delete();
        }
    }

    JSONObject importIfReady(){
        try{
            progress("CHECK","MSERVER 승계자료 확인",0,1,false,"");
            String imported=prefs().getString("imported_sha256","");
            JSONObject previous=storedManifest();
            SharedPreferences live=context.getSharedPreferences("unified_live_orders",Context.MODE_PRIVATE);
            boolean armed=live.getBoolean("live_stock",false)||live.getBoolean("live_coin",false);
            if(!armed&&!phoneHasRealBrokerFills())repairLegacyClosePath();
            // Once PC history has been verified, it is now the PHONE's active DB.
            // A new, different PC ZIP is NOT a merge; reinstalling it would erase
            // later phone paper trades. Never overwrite on any subsequent startup.
            // Check local history before contacting MSERVER so offline restarts work.
            if(!imported.isEmpty()){
                if(previous==null){
                    JSONObject blocked=obj("ok",false,"status","PC_IMPORT_CHECKPOINT_INCOMPLETE",
                        "message","Original PC import checkpoint exists but manifest is unavailable. Local DB retained; no automatic replacement.",
                        "sha256",imported);
                    progress("BLOCKED","기존 폰 원장 보존 · 원본 검증정보 확인 필요",0,1,false,"PC_IMPORT_CHECKPOINT_INCOMPLETE");
                    return blocked;
                }
                try{
                    JSONObject verification=verifyInstalled(previous,false);
                    // Ordinary startup also checks for extra EXE research files.
                    // This never overwrites an already existing phone file.
                    if(!armed&&!phoneHasRealBrokerFills()){
                        try{
                            JSONObject extras=importOnlyMissingResearch(previous,imported);
                            if(extras!=null)return extras;
                        }catch(Exception supplementalError){
                            crashes.add("pc_research_supplement",supplementalError);
                        }
                    }
                    progress("VERIFY_RUNTIME","승계 완료 원장 재사용 · 기존 계좌 원장 유지",0,1,false,"");
                    return obj("ok",true,"status","LOCAL_FILES_PRESENT","sha256",imported,
                        "manifest",previous,"verification",verification,"pc_import_locked",true);
                }catch(Exception e){
                    JSONObject blocked=obj("ok",false,"status","PHONE_LEDGER_VERIFY_FAILED",
                        "message","Phone ledger retained; manual reconciliation required: "+e.getClass().getSimpleName()+
                            ": "+String.valueOf(e.getMessage()),"sha256",imported);
                    progress("BLOCKED","기존 폰 원장 검증 실패 · 자동 덮어쓰기 차단",0,1,false,"PHONE_LEDGER_VERIFY_FAILED");
                    return blocked;
                }
            }
            // FILES_INSTALLED (or a prior RUNTIME_VERIFY_FAILED) means the DB bytes
            // already reached this phone. Never download the same ZIP again, as
            // the running phone may have added paper or real trade records.
            String pendingSha=prefs().getString("installed_sha256","");
            String pendingSession=prefs().getString("installed_session","");
            if(pendingSha.isEmpty()){
                JSONObject oldResult=new JSONObject(prefs().getString("last_status","{}"));
                String oldStatus=oldResult.optString("status","");
                if("FILES_INSTALLED".equals(oldStatus)||
                   "RUNTIME_VERIFY_FAILED".equals(oldStatus)){
                    pendingSha=oldResult.optString("sha256","");
                    pendingSession=oldResult.optString("session","");
                }
            }
            if(!pendingSha.isEmpty()){
                if(previous==null)return obj("ok",false,"status","PC_IMPORT_CHECKPOINT_INCOMPLETE",
                    "message","Unverified PC DB already installed. Keep all phone files; manifest missing.",
                    "sha256",pendingSha);
                try{
                    JSONObject verification=verifyInstalled(previous,false);
                    progress("VERIFY_RUNTIME","기기 내 PC 원장 재검증 · 파일 재설치 없음",0,1,false,"");
                    return obj("ok",true,"status","FILES_INSTALLED",
                        "sha256",pendingSha,"session",pendingSession,
                        "manifest",previous,"verification",verification,"reuse_existing_files",true);
                }catch(Exception unsafe){
                    JSONObject blocked=obj("ok",false,"status","PHONE_LEDGER_VERIFY_FAILED",
                        "message","Existing DB kept, no overwrite: "+unsafe.getClass().getSimpleName()+
                            ": "+String.valueOf(unsafe.getMessage()),"sha256",pendingSha);
                    progress("BLOCKED","설치된 원장 검증 실패 · 기존 기록 보존",0,1,false,
                        String.valueOf(unsafe.getMessage()));
                    return blocked;
                }
            }
            LocalHubClient hub=new LocalHubClient(context);
            JSONObject status=hub.migrationStatus();
            boolean ready=status.optBoolean("ok")&&status.optBoolean("ready")
                &&status.optBoolean("archive_exists",false);
            if(!ready){
                if(!imported.isEmpty()&&previous!=null){
                    JSONObject verification=verifyInstalled(previous,false);
                    progress("VERIFY_RUNTIME","설치된 PC 원장 전략엔진 조회 검사",0,1,false,"");
                    return obj("ok",true,"status","LOCAL_FILES_PRESENT","sha256",imported,
                        "manifest",previous,"verification",verification);
                }
                progress("WAITING","PC EXE 원장 승계자료 대기",0,1,false,"");
                return obj("ok",true,"status","NO_PC_ARCHIVE");
            }
            String session=status.optString("session"),sha=status.optString("archive_sha256");
            if(armed){
                JSONObject blocked=obj("ok",false,"status","LIVE_TRADING_ACTIVE",
                    "message","Disable live stock and coin orders before installing PC ledger files");
                progress("BLOCKED","실거래 활성화 · PC 원장 덮어쓰기 차단",0,1,false,"LIVE_TRADING_ACTIVE");
                return blocked;
            }
            if(!sha.isEmpty()&&sha.equalsIgnoreCase(imported)&&previous!=null){
                try{
                    JSONObject verification=verifyInstalled(previous,false);
                    progress("VERIFY_RUNTIME","기존 PC DB 재조회 · 원장 반영 확인",0,1,false,"");
                    return obj("ok",true,"status","LOCAL_FILES_PRESENT","sha256",sha,
                        "session",session,"manifest",previous,"verification",verification);
                }catch(Exception invalid){
                    crashes.add("pc_previous_ledger_invalid",invalid);
                    // The server still has the archive: reinstall instead of claiming success.
                }
            }
            if(phoneHasRealBrokerFills()){
                JSONObject blocked=obj("ok",false,"status","PHONE_HAS_REAL_FILLS",
                    "message","Actual broker FILLED orders exist; automatic historical replacement blocked for reconciliation","sha256",sha);
                prefs().edit().putString("last_status",blocked.toString()).commit();
                progress("BLOCKED","실제 브로커 체결 존재 · 자동 원장 교체 차단",0,1,false,"PHONE_HAS_REAL_FILLS");
                return blocked;
            }
            int chunks=status.optInt("read_chunks",0);long total=status.optLong("total_bytes",-1);
            int readBytes=status.optInt("read_chunk_bytes",240000);
            if(chunks<1||total<1||total>MAX_MIGRATION_ZIP||readBytes<1||readBytes>1_000_000||
               chunks!=(total+readBytes-1)/readBytes)
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
            if(!prefs().edit().putString("installed_sha256",sha)
                .putString("installed_manifest",manifest.toString())
                .putString("installed_session",session)
                .putLong("installed_at",System.currentTimeMillis()).commit())
                throw new IOException("Cannot persist local installed snapshot checkpoint");
            // Verify original bytes while the strategy engine is still stopped.
            JSONObject verification=verifyInstalled(manifest,true);
            deleteTree(staging);archive.delete();
            JSONObject result=obj("ok",true,"status","FILES_INSTALLED",
                "sha256",sha,"session",session,"manifest",manifest,"verification",verification);
            prefs().edit().putString("last_status",result.toString()).commit();
            progress("VERIFY_RUNTIME","PC DB 설치 확인 · 전략엔진 데이터 반영 검사 중",0,1,false,"");
            // ACK is intentionally delayed until the running strategy reports real history.
            return result;
        }catch(Throwable e){
            crashes.add("pc_ledger_migration",e);
            String stage="IMPORT_FAILED";
            if(e instanceof java.net.ConnectException||e instanceof java.net.SocketTimeoutException)
                stage="MSERVER_UNREACHABLE";
            JSONObject result=obj("ok",false,"status",stage,"type",e.getClass().getSimpleName(),
                "message",String.valueOf(e.getMessage()));
            prefs().edit().putString("last_status",result.toString()).commit();
            progress("MSERVER_UNREACHABLE".equals(stage)?"WAITING":"FAILED",
                "MSERVER_UNREACHABLE".equals(stage)?"PC 원장 서버 연결 대기":"PC EXE 원장 승계 실패",
                0,1,false,e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()));
            return result;
        }
    }
    JSONObject status(){
        try{
            String last=prefs().getString("last_status","");
            JSONObject o=last.isEmpty()?new JSONObject():new JSONObject(last);
            o.put("imported_sha256",prefs().getString("imported_sha256",""))
                .put("installed_sha256",prefs().getString("installed_sha256",""))
                .put("imported_at",prefs().getLong("imported_at",0L))
                .put("close_path_conflict_preserved",prefs().getBoolean("close_path_conflict_preserved",false));
            return o;
        }catch(Exception e){return new JSONObject();}
    }
}
