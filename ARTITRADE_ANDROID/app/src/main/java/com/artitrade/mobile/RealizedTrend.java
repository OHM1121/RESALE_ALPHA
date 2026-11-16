package com.artitrade.mobile;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

/** Pure JVM history→dated cumulative realized P/L; never writes a trade ledger. */
final class RealizedTrend {
    static final ZoneId KST=ZoneId.of("Asia/Seoul");
    static final class Event {
        final long at;
        final double amount;
        Event(long at,double amount){this.at=at;this.amount=amount;}
    }
    static final class Series {
        final long[] timestamps;
        final double[] values;
        Series(long[] timestamps,double[] values){this.timestamps=timestamps;this.values=values;}
    }
    static long time(Object raw){
        if(raw==null)return 0;
        String s=String.valueOf(raw).trim();
        if(s.isEmpty()||"null".equalsIgnoreCase(s))return 0;
        try{
            if(s.matches("\\d{13}"))return Long.parseLong(s);
            if(s.matches("\\d{10}"))return Long.parseLong(s)*1000L;
            if(s.matches("\\d{8}"))
                return LocalDate.parse(s,DateTimeFormatter.BASIC_ISO_DATE).atStartOfDay(KST).toInstant().toEpochMilli();
            if(s.matches("\\d{14}"))
                return LocalDateTime.parse(s,DateTimeFormatter.ofPattern("yyyyMMddHHmmss")).atZone(KST).toInstant().toEpochMilli();
            if(s.matches("\\d{12}"))
                return LocalDateTime.parse(s,DateTimeFormatter.ofPattern("yyyyMMddHHmm")).atZone(KST).toInstant().toEpochMilli();
        }catch(Exception ignored){}
        try{return Instant.parse(s).toEpochMilli();}catch(Exception ignored){}
        try{return OffsetDateTime.parse(s).toInstant().toEpochMilli();}catch(Exception ignored){}
        String normalized=s.replace('T',' ');
        for(String pattern:new String[]{"yyyy-MM-dd HH:mm:ss.SSS","yyyy-MM-dd HH:mm:ss","yyyy-MM-dd HH:mm","yyyy/MM/dd HH:mm:ss","yyyy/MM/dd HH:mm"}){
            try{return LocalDateTime.parse(normalized,DateTimeFormatter.ofPattern(pattern,Locale.ROOT)).atZone(KST).toInstant().toEpochMilli();}
            catch(Exception ignored){}
        }
        try{return LocalDate.parse(s,DateTimeFormatter.ISO_LOCAL_DATE).atStartOfDay(KST).toInstant().toEpochMilli();}
        catch(Exception ignored){}
        return 0;
    }
    static Series cumulative(List<Event> source,int maxPoints){
        if(source==null||source.isEmpty())return new Series(new long[0],new double[0]);
        ArrayList<Event> events=new ArrayList<>();
        for(Event e:source)if(e!=null&&e.at>0&&!Double.isNaN(e.amount)&&!Double.isInfinite(e.amount))events.add(e);
        events.sort(Comparator.comparingLong(e->e.at));
        final int max=Math.max(1,maxPoints),len=events.size(),start=Math.max(0,len-max);
        long[] ts=new long[len-start];
        double[] values=new double[len-start];
        double sum=0;
        for(int i=0;i<len;i++){
            sum+=events.get(i).amount;
            if(i>=start){ts[i-start]=events.get(i).at;values[i-start]=sum;}
        }
        return new Series(ts,values);
    }
}
