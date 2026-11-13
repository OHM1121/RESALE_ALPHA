package com.artitrade.mobile;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;

/**
 * Durable same-directory metadata replacement for the PC->phone migration journal.
 * Never unlink the previous committed journal before its successor is durable.
 * Independent of Android runtime so repeated JVM filesystem tests can exercise it.
 */
final class MigrationJournalIO {
    private MigrationJournalIO(){}

    static void replace(File target,byte[] bytes)throws IOException{
        if(target==null||bytes==null)throw new IllegalArgumentException("journal arguments missing");
        File parent=target.getAbsoluteFile().getParentFile();
        if(parent==null||(!parent.isDirectory()&&!parent.mkdirs()))
            throw new IOException("Cannot create journal parent");
        File next=new File(parent,target.getName()+".next");
        try(FileOutputStream out=new FileOutputStream(next,false)){
            out.write(bytes);
            out.flush();
            out.getFD().sync();
        }
        try{
            Files.move(next.toPath(),target.toPath(),
                StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING);
        }catch(AtomicMoveNotSupportedException noAtomic){
            // Both files are on the same private Android filesystem.
            // REPLACE_EXISTING is still safer than deleting the original first.
            Files.move(next.toPath(),target.toPath(),StandardCopyOption.REPLACE_EXISTING);
        }
    }
}
