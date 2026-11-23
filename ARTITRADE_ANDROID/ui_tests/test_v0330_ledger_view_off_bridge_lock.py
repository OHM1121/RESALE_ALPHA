"""0.3.3 regression (issue #148): the read-only ledger VIEW build no longer holds the bridge monitor.

Evidence: error export 2026-11-23 10:31 (app 0.3.2). All 14 engine-hang dumps showed the monitor loop
(the heartbeat) parked on the StrategyRuntimeBridge monitor behind `hubRecordsUi()` - a display-only
ledger build that took 100-500+ s on the phone - so the 0.3.1 supervisor restarted the process six
times (01:06, 01:16, 09:19, 09:42, 09:53, 10:23) and the Upbit WebSocket dropped on ping timeouts.
The next build started right after the previous one ended, keeping the GIL and the CPU saturated."""
import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
JAVA = BASE / "app/src/main/java/com/artitrade/mobile"


def read(name):
    return (JAVA / name).read_text(encoding="utf-8")


def method(src, signature, end_marker):
    i = src.index(signature)
    return src[i:src.index(end_marker, i)]


class BridgeLock(unittest.TestCase):
    def test_view_builders_are_not_synchronized_on_the_bridge(self):
        src = read("StrategyRuntimeBridge.java")
        self.assertNotIn("synchronized JSONObject hubRecordsUi", src)
        self.assertNotIn("synchronized JSONObject hubRecords(", src)
        self.assertIn("JSONObject hubRecordsUi(){", src)
        self.assertIn("JSONObject hubRecords(){", src)

    def test_view_builders_share_their_own_lock_and_a_local_module(self):
        src = read("StrategyRuntimeBridge.java")
        self.assertIn("private final Object hubRecordsLock=new Object();", src)
        for sig, call in (("JSONObject hubRecordsUi(){", 'callAttr("hub_records_ui")'),
                          ("JSONObject hubRecords(){", 'callAttr("hub_records")')):
            body = method(src, sig, "\n    }\n")
            self.assertIn("PyObject current=module;", body)
            self.assertIn("synchronized(hubRecordsLock)", body)
            self.assertIn("current." + call, body)
            self.assertNotIn("module.callAttr", body)

    def test_trading_paths_stay_on_the_bridge_monitor(self):
        src = read("StrategyRuntimeBridge.java")
        for name in ("JSONObject snapshot()", "JSONObject restart()", "JSONObject manualPaperSell(",
                     "JSONObject setLive(", "void stop()"):
            self.assertIn("synchronized " + name, src)

    def test_no_bridge_method_takes_the_view_lock_except_the_two_builders(self):
        src = read("StrategyRuntimeBridge.java")
        self.assertEqual(src.count("synchronized(hubRecordsLock)"), 2)


class Pacing(unittest.TestCase):
    def test_schedule_uses_the_duration_aware_policy(self):
        src = read("UnifiedTradingService.java")
        body = method(src, "private void scheduleUiRecordsIfDue(", "private void priceOnlyLoop()")
        self.assertIn("UiRecordPacingV0330.due(now,visible,uiRecordLastStartedMs,", body)
        self.assertNotIn("long interval=visible?6_500L:17_000L;", body)
        fin = body[body.index("}finally{"):]
        self.assertLess(fin.index("uiRecordLastDurationMs="), fin.index("uiRecordCollecting.set(false)"))
        self.assertLess(fin.index("uiRecordLastFinishedMs="), fin.index("uiRecordCollecting.set(false)"))

    def test_policy_constants(self):
        src = read("UiRecordPacingV0330.java")
        for needle in ("VISIBLE_GAP_MS=6_500L", "HIDDEN_GAP_MS=17_000L", "DURATION_FACTOR=4L",
                       "MAX_QUIET_MS=300_000L"):
            self.assertIn(needle, src)
        self.assertNotIn("import android", src)

    def test_user_sell_still_refreshes_at_once(self):
        src = read("UnifiedTradingService.java")
        i = src.index("uiRecordLastStartedMs=0L;")
        self.assertIn("uiRecordLastFinishedMs=0L;", src[i:i + 200])


class NetworkEnvironment(unittest.TestCase):
    def test_probe_is_read_only_and_stores_no_identity(self):
        src = read("NetworkEnvironmentV0330.java")
        for word in ("getSSID", "WifiManager", "getHostAddress", "getPrivateDnsServerName().", "openConnection",
                     "Socket", "InetAddress.getByName", "getDnsServers().get("):
            self.assertNotIn(word, src)
        for needle in ("isIgnoringBatteryOptimizations", "getRestrictBackgroundStatus",
                       "NET_CAPABILITY_VALIDATED", "isPrivateDnsActive"):
            self.assertIn(needle, src)

    def test_recorded_only_on_change_from_the_monitor_loop(self):
        src = read("UnifiedTradingService.java")
        self.assertIn("recordNetworkEnvironmentIfChanged();", src)
        body = method(src, "private void recordNetworkEnvironmentIfChanged(){", "\n    }\n")
        self.assertIn("if(env.equals(lastNetworkEnvironment))return;", body)
        self.assertIn("60_000L", body)
        self.assertIn('crashes.add("network_environment_v0330"', body)


class Version(unittest.TestCase):
    def test_unified_version_is_numeric_and_at_least_0_3_3(self):
        g = (BASE / "app/build.gradle").read_text(encoding="utf-8")
        m = re.search(r"unified \{\s*dimension 'market'\s*versionCode (\d+)\s*versionName '([0-9.]+)'", g)
        self.assertGreaterEqual(int(m.group(1)), 166)
        self.assertGreaterEqual(tuple(map(int, m.group(2).split("."))), (0, 3, 3))


if __name__ == "__main__":
    unittest.main()
