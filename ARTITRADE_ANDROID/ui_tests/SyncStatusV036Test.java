package com.artitrade.mobile;
public final class SyncStatusV036Test {
    private static void eq(String expected,String got){
        if(!expected.equals(got))throw new AssertionError(expected+" != "+got);
    }
    public static void main(String[] args){
        eq("미완료",SyncStatusV036.phase(false,false,0,0,false));
        eq("진행중",SyncStatusV036.phase(false,false,23,100,true));
        eq("완료",SyncStatusV036.phase(true,false,100,100,true));
        eq("오류",SyncStatusV036.phase(false,true,42,100,true));
        eq("23%",SyncStatusV036.percent(23,100));
        eq("0%",SyncStatusV036.percent(0,0));
        eq("100%",SyncStatusV036.percent(100,100));
        System.out.println("MSERVER_COMPACT_STATUS_PASS");
    }
}
