package com.artitrade.mobile;

import org.json.JSONArray;
import org.json.JSONObject;

public final class DashboardCountPolicyTest {
    private static void check(boolean ok, String what) {
        if (!ok) throw new AssertionError(what);
    }

    private static JSONObject row(String side, String code, String uid, String entry, String order) throws Exception {
        JSONObject r = new JSONObject();
        r.put("side", side);
        r.put("code", code);
        if (uid != null) r.put("position_uid", uid);
        if (entry != null) r.put("entry_date", entry);
        if (order != null) r.put("order_id", order);
        return r;
    }

    public static void main(String[] args) throws Exception {
        // 000680: 10 split buy fills of one position; 003380: 3 split buy fills of one position  -> 2 buys, not 13
        JSONArray rows = new JSONArray();
        for (int i = 0; i < 10; i++) rows.put(row("BUY", "000680", "entry:000680|2026-11-19", "2026-11-19", "PROJECTGMO-000000" + (i < 9 ? 1 : 2)));
        for (int i = 0; i < 3; i++) rows.put(row("BUY", "003380", null, "2026-11-23", "PROJECTGMO-0000003"));
        int[] c = DashboardCountPolicyV0570.gmoBuySell(rows);
        check(c[0] == 2, "buys per position, got " + c[0]);
        check(c[1] == 0, "no sells, got " + c[1]);

        // two sell fills of the same order are one sell; a different order is another; a row without an order id counts by itself
        rows.put(row("SELL", "000680", "entry:000680|2026-11-19", "2026-11-19", "PROJECTGMO-0000010"));
        rows.put(row("SELL", "000680", "entry:000680|2026-11-19", "2026-11-19", "PROJECTGMO-0000010"));
        rows.put(row("SELL", "000680", "entry:000680|2026-11-19", "2026-11-19", "PROJECTGMO-0000011"));
        rows.put(row("SELL", "003380", null, "2026-11-23", null));
        c = DashboardCountPolicyV0570.gmoBuySell(rows);
        check(c[0] == 2, "buys unchanged, got " + c[0]);
        check(c[1] == 3, "sells per order, got " + c[1]);

        // the same code bought again on another entry date is another position
        rows.put(row("BUY", "003380", null, "2026-12-01", "PROJECTGMO-0000020"));
        check(DashboardCountPolicyV0570.gmoBuySell(rows)[0] == 3, "a new entry date is a new position");

        check(DashboardCountPolicyV0570.gmoBuySell(null)[0] == 0, "null executions");
        check(DashboardCountPolicyV0570.gmoBuySell(new JSONArray())[1] == 0, "empty executions");
        System.out.println("DashboardCountPolicyTest OK");
    }
}
