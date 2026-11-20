package com.artitrade.mobile;

import android.content.Context;
import android.util.AtomicFile;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.ByteArrayOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;

/** Atomic private file: no 2.5-second multi-MB SharedPreferences XML copies. */
final class LargeRecordStoreV087 {
    // A display snapshot is NOT the original database. Never truncate source
    // SQLite/history; fail the display update if too large for a safe refresh.
    // 5M-character Python UI window; stale pre-v104 oversized snapshots
    // must not be read into the Samsung 256MB Java heap.
    static final int MAX_UI_RECORD_CHARS=6_000_000;
    private static final long MAX_UI_RECORD_BYTES=12_000_000L;
    private static final String NAME="artitrade_view_record_snapshot_v087.json";
    private LargeRecordStoreV087(){}

    static AtomicFile store(Context ctx){
        return new AtomicFile(new File(ctx.getApplicationContext().getFilesDir(),NAME));
    }
    static String version(Context ctx){
        File file=store(ctx).getBaseFile();
        return file.isFile()?file.lastModified()+":"+file.length():"";
    }
    static boolean exists(Context ctx){
        return store(ctx).getBaseFile().isFile();
    }
    static void write(Context ctx,String json)throws IOException{
        if(json==null||json.isEmpty())throw new IOException("UI_RECORD_EMPTY");
        if(json.length()>MAX_UI_RECORD_CHARS)throw new IOException(
            "UI_RECORD_TOO_LARGE_SOURCE_DATABASE_PRESERVED:chars="+json.length());
        AtomicFile atomic=store(ctx);
        FileOutputStream output=atomic.startWrite();
        try{
            OutputStreamWriter writer=new OutputStreamWriter(
                output,StandardCharsets.UTF_8);
            writer.write(json);
            writer.flush();
            atomic.finishWrite(output);
        }catch(Exception e){
            atomic.failWrite(output);
            if(e instanceof IOException)throw (IOException)e;
            throw new IOException("Atomic UI record save failed",e);
        }
    }
    static String read(Context ctx)throws IOException{
        AtomicFile atomic=store(ctx);
        if(atomic.getBaseFile().length()>MAX_UI_RECORD_BYTES)
            throw new IOException("UI_RECORD_PREVIOUS_OVERSIZE_REBUILD_REQUIRED");
        try(FileInputStream input=atomic.openRead();
            ByteArrayOutputStream buffer=new ByteArrayOutputStream(65536)){
            byte[] bytes=new byte[16384];
            int n;long read=0;
            while((n=input.read(bytes))!=-1){
                read+=n;
                if(read>MAX_UI_RECORD_BYTES)throw new IOException(
                    "UI_RECORD_FILE_CORRUPT_OR_OVERSIZE");
                buffer.write(bytes,0,n);
            }
            return buffer.toString(StandardCharsets.UTF_8.name());
        }
    }
}
