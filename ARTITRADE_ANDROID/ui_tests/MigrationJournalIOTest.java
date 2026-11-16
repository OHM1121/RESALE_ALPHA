package com.artitrade.mobile;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.Random;

/** Repeated crash-window journal replacement, purely on a JVM. */
public final class MigrationJournalIOTest {
    public static void main(String[] args)throws Exception{
        File root=Files.createTempDirectory("artitrade-meta-").toFile();
        try{
            File meta=new File(root,"meta.json");
            byte[] last="initial".getBytes(StandardCharsets.UTF_8);
            MigrationJournalIO.replace(meta,last);
            Random random=new Random(20261113L);
            for(int i=0;i<512;i++){
                // Simulate a crash leaving an old incomplete staged successor.
                File next=new File(root,"meta.json.next");
                Files.write(next.toPath(),new byte[]{7,3,1});
                if(!java.util.Arrays.equals(Files.readAllBytes(meta.toPath()),last))
                    throw new AssertionError("old committed metadata disappeared");
                byte[] update=new byte[64+random.nextInt(2048)];
                random.nextBytes(update);
                MigrationJournalIO.replace(meta,update);
                if(!java.util.Arrays.equals(update,Files.readAllBytes(meta.toPath())))
                    throw new AssertionError("atomic replacement failed iteration "+i);
                if(next.exists())throw new AssertionError("staging left after success");
                last=update;
            }
            System.out.println("MIGRATION_ATOMIC_JOURNAL_512_PASS");
        }finally{
            File[] entries=root.listFiles();
            if(entries!=null)for(File f:entries)if(!f.delete())throw new IllegalStateException("test cleanup");
            if(!root.delete())throw new IllegalStateException("test cleanup root");
        }
    }
}
