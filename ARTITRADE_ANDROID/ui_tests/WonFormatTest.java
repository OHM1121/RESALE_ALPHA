package com.artitrade.mobile;

/** Pure JVM regression: money never shows 만/억 or drops the 원 suffix. */
final class WonFormatTest {
    private static void eq(String wanted,String actual){
        if(!wanted.equals(actual))throw new AssertionError("expected="+wanted+", got="+actual);
        if(actual.contains("만")||actual.contains("억"))
            throw new AssertionError("abbreviated KRW: "+actual);
    }
    public static void main(String[] args){
        eq("51,000원",WonFormat.won(51000));
        eq("+51,000원",WonFormat.signed(51000));
        eq("-12,500원",WonFormat.signed(-12500));
        eq("0원",WonFormat.won(0));
        eq("123,456,789원",WonFormat.won(123456789));
        eq("—",WonFormat.won(Double.NaN));
        eq("—",WonFormat.won(Double.POSITIVE_INFINITY));
        System.out.println("FULL_KRW_FORMAT_TEST_PASS");
    }
}
