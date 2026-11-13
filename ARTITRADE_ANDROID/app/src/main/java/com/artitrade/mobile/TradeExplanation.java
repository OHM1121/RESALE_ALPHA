package com.artitrade.mobile;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

/** Short Korean labels for existing EXE trade records. Never invents a fill or decision. */
final class TradeExplanation {
    static final ZoneId KST=ZoneId.of("Asia/Seoul");
    private static final DateTimeFormatter OUT=
        DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss",Locale.KOREA);
    private TradeExplanation(){}

    static String side(String raw){
        String x=clean(raw).toUpperCase(Locale.ROOT);
        if(x.equals("B")||x.contains("BUY")||x.contains("매수")||x.contains("ENTRY"))return "매수";
        if(x.equals("S")||x.contains("SELL")||x.contains("매도")||x.contains("EXIT"))return "매도";
        return "체결";
    }

    private static String clean(String raw){
        return raw==null?"":raw.trim();
    }
    private static boolean has(String raw,String... fragments){
        for(String f:fragments)if(raw.contains(f))return true;
        return false;
    }

    static String shortReason(String original,String event){
        String raw=clean(original);
        if(raw.isEmpty()||"null".equalsIgnoreCase(raw))return
            "매도".equals(event)?"원장에 매도 사유가 기록되지 않음":
            "매수".equals(event)?"원장에 매수 사유가 기록되지 않음":"원장에 판단 사유가 기록되지 않음";
        String x=raw.toUpperCase(Locale.ROOT).replace('-','_').replace(' ','_');
        // Most distinctive sell outcomes first. Never interpret a target HIGH as a fill.
        if(has(x,"THIRD_TARGET","THIRD_TP","TP3","3차매도","3차_목표","3차목표"))
            return "3차 목표가 도달";
        if(has(x,"SECOND_TARGET","SECOND_TP","TP2","2차매도","2차_목표","2차목표"))
            return "2차 목표가 도달";
        if(has(x,"FIRST_TARGET","FIRST_TP","TP_FIRST","TP1","1차매도","1차_목표","1차목표")){
            if(has(x,"40PCT","40_PERCENT","40%"))return "1차 목표가 도달 · 40% 분할";
            return "1차 목표가 도달";
        }
        if(has(x,"MIDPOINT","MID_POINT","MIDDLE_EXIT","HALF_RANGE","CENTER_LINE","중간가","중앙값"))
            return "기준봉 중간가격 이탈";
        if(has(x,"TRAILING_STOP","TRAIL_STOP","TRAILING_EXIT","TRAILING","추적매도","추적_손절"))
            return "고점 이후 하락 · 추적매도";
        if(has(x,"STOP_LOSS","STOPLOSS","STOP_EXIT","HARD_STOP","STOPSELL","손절"))
            return "손절 기준 도달";
        if(has(x,"TIME_EXIT","TIME_STOP","TIMEOUT_EXIT","HOLD_TIMEOUT","만기청산","시간청산"))
            return "보유시간 종료";
        if(has(x,"FORCED_LIQUIDATION","FORCED_EXIT","FORCE_SELL","RISK_OFF","RISK_EXIT","강제매도","위험관리"))
            return "위험관리 기준에 따른 매도";
        if(has(x,"MANUAL_SELL","MANUAL_EXIT","MANUAL_CLOSE","수동매도"))
            return "수동 매도";
        if(has(x,"EOD_EXIT","MARKET_CLOSE","SESSION_CLOSE","장마감"))
            return "장 마감에 따른 매도";
        if(has(x,"PARTIAL_FILL","PARTIAL_SELL","부분체결"))
            return "주문 일부만 체결";
        if(has(x,"FIRST_TARGET_40PCT"))return "1차 목표가 도달 · 40% 분할";
        if(has(x,"NO_BID","NO_ASK","INSUFFICIENT_BOOK","ORDERBOOK","호가부족","호가_부족"))
            return "주문호가 부족 · 체결 대기";
        if(has(x,"INSUFFICIENT_CASH","NO_CASH","INSUFFICIENT_BALANCE","잔고부족"))
            return "가용 잔고 부족";
        if(has(x,"CANCEL","CANCELED","CANCELLED","취소"))
            return "주문 취소";
        if(has(x,"MA_CROSS","MOVING_AVERAGE","이동평균","이평선"))
            return "이동평균선 조건 충족";
        if(has(x,"BREAKOUT","REF_BODY","BASE_CANDLE","기준봉","돌파"))
            return "기준봉 매수 조건 충족";
        if(has(x,"GMO","FLOW_SCORE","SUPPLY_DEMAND","수급점수","수급매매"))
            return "수급매매 점수 조건 충족";
        if(has(x,"ENTRY_SIGNAL","BUY_SIGNAL","ENTRY_OK","BUY_OK","매수조건","매수_조건"))
            return "매수 기준 충족";
        if(has(x,"VIRTUAL","SHADOW","PAPER","가상"))
            return "가상매매 관측 기록";
        if(has(x,"SELL_FILL","SELL_EXECUTED"))return "매도주문 체결";
        if(has(x,"BUY_FILL","BUY_EXECUTED"))return "매수주문 체결";
        if(raw.contains("도달")||raw.contains("이탈")||raw.contains("돌파")||
            raw.contains("선정")||raw.contains("조건")||raw.contains("하락")||
            raw.contains("상승")||raw.contains("매수")||raw.contains("매도")){
            // This is the literal EXE observation, not a generated explanation.
            String sentence=raw.replace('_',' ').replaceAll("\\s+"," ");
            return sentence.length()>76?sentence.substring(0,73)+"…":sentence;
        }
        // A code/feature JSON is preserved verbatim in the exported original DB;
        // showing an invented Korean meaning here would be misleading.
        return "원본 판단코드 확인 필요 · 연구자료에 원문 보존";
    }

    /** True KST rendering of epoch, ISO + offset/Z, or a documented naive source zone. */
    static String kst(Object raw,boolean naiveUtc){
        if(raw==null)return "체결시각 미기록";
        String s=String.valueOf(raw).trim();
        if(s.isEmpty()||s.equalsIgnoreCase("null"))return "체결시각 미기록";
        try{
            if(s.matches("[0-9]{13}"))return OUT.format(Instant.ofEpochMilli(Long.parseLong(s)).atZone(KST))+" KST";
            if(s.matches("[0-9]{10}"))return OUT.format(Instant.ofEpochSecond(Long.parseLong(s)).atZone(KST))+" KST";
            if(s.matches("[0-9]{8}"))return OUT.format(LocalDate.parse(s,DateTimeFormatter.BASIC_ISO_DATE).atStartOfDay(KST))+" KST";
            if(s.matches("[0-9]{14}"))return OUT.format(LocalDateTime.parse(s,
                DateTimeFormatter.ofPattern("yyyyMMddHHmmss")).atZone(naiveUtc?ZoneOffset.UTC:KST)
                .withZoneSameInstant(KST))+" KST";
            if(s.matches("[0-9]{12}"))return OUT.format(LocalDateTime.parse(s,
                DateTimeFormatter.ofPattern("yyyyMMddHHmm")).atZone(naiveUtc?ZoneOffset.UTC:KST)
                .withZoneSameInstant(KST))+" KST";
        }catch(Exception ignored){}
        String explicit=s.replace(' ','T');
        if(explicit.endsWith("TUTC"))explicit=explicit.substring(0,explicit.length()-4)+"Z";
        if(explicit.endsWith("TKST"))explicit=explicit.substring(0,explicit.length()-4)+"+09:00";
        try{return OUT.format(Instant.parse(explicit).atZone(KST))+" KST";}catch(Exception ignored){}
        try{return OUT.format(OffsetDateTime.parse(explicit).atZoneSameInstant(KST))+" KST";}catch(Exception ignored){}
        String local=s.replace('T',' ');
        for(String pattern:new String[]{"yyyy-MM-dd HH:mm:ss.SSSSSS","yyyy-MM-dd HH:mm:ss.SSS",
            "yyyy-MM-dd HH:mm:ss","yyyy-MM-dd HH:mm",
            "yyyy/MM/dd HH:mm:ss","yyyy/MM/dd HH:mm"}){
            try{
                LocalDateTime when=LocalDateTime.parse(local,DateTimeFormatter.ofPattern(pattern,Locale.ROOT));
                return OUT.format(when.atZone(naiveUtc?ZoneOffset.UTC:KST)
                    .withZoneSameInstant(KST))+" KST";
            }catch(Exception ignored){}
        }
        // Never mislabel an unknown timestamp as converted KST.
        return "시각 형식 확인 필요";
    }
}
