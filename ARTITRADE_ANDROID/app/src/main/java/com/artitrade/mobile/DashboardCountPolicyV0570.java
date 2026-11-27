package com.artitrade.mobile;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.HashSet;
import java.util.Locale;

/**
 * 0.5.7 (#174): trade counts of the supply-demand (gmo) dashboard.
 * A position is bought in up to three split rungs and every partial fill is a row; counting rows showed 13 buys for 2 positions.
 * BUY = once per position (position uid, else code + entry date); SELL = once per sell order id (rows without an order id count each).
 */
final class DashboardCountPolicyV0570 {
    private DashboardCountPolicyV0570() {}

    static int[] gmoBuySell(JSONArray executions) {
        HashSet<String> positions = new HashSet<>();
        HashSet<String> orders = new HashSet<>();
        int buy = 0, sell = 0;
        if (executions == null) return new int[]{0, 0};
        for (int i = 0; i < executions.length(); i++) {
            JSONObject r = executions.optJSONObject(i);
            if (r == null) continue;
            String side = r.optString("side", r.optString("type", "")).toUpperCase(Locale.ROOT);
            if (side.contains("BUY") || side.contains("매수")) {
                String who = r.optString("position_uid", "").trim();
                if (who.isEmpty()) who = r.optString("code", "") + "|" + r.optString("entry_date", "");
                if (positions.add(who)) buy++;
            }
            if (side.contains("SELL") || side.contains("매도")) {
                String order = r.optString("order_id", "").trim();
                if (order.isEmpty() || orders.add(order)) sell++;
            }
        }
        return new int[]{buy, sell};
    }
}
