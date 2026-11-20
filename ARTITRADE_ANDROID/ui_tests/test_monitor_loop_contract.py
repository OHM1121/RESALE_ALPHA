"""Source contract for UnifiedTradingService.monitorLoop (0.0.18 follow-up).

Static contract only: it pins the interfaces whose violation caused the
observed defects (probe hammering, duplicate synchronized Python snapshots,
fault-log flooding) and the fail-closed paths that must survive the cleanup.
It is NOT proof of device behaviour.
"""
import re
import unittest
from pathlib import Path

BASE = Path(__file__).parents[1]
JAVA = BASE / "app/src/main/java/com/artitrade/mobile"
SERVICE = (JAVA / "UnifiedTradingService.java").read_text(encoding="utf8")
STORE = (JAVA / "RuntimeCrashStore.java").read_text(encoding="utf8")
BRIDGE = (JAVA / "StrategyRuntimeBridge.java").read_text(encoding="utf8")


def method_body(source, signature, end_marker):
    start = source.index(signature)
    return source[start:source.index(end_marker, start)]


MONITOR = method_body(SERVICE, "private void monitorLoop(){",
                      "private void disarmIfAndroidFlagsArmed(")
DISARM = method_body(SERVICE, "private void disarmUnverifiedMarkets(",
                     "private void applyTrendManual(")


class OptionalHubProbe(unittest.TestCase):
    def test_hub_status_is_only_called_when_probe_is_due(self):
        self.assertEqual(MONITOR.count("hub.status()"), 1)
        guard = MONITOR.index("if(hubProbe.due(")
        self.assertLess(guard, MONITOR.index("hub.status()"))

    def test_backoff_is_paced_by_a_monotonic_clock(self):
        self.assertIn("android.os.SystemClock.elapsedRealtime()", MONITOR)
        self.assertRegex(SERVICE, r"new ProbeBackoff\(2_500L,\s*30_000L\)")

    def test_success_resets_and_every_failure_path_records_failure(self):
        self.assertEqual(MONITOR.count("hubProbe.onSuccess()"), 1)
        self.assertEqual(MONITOR.count("hubProbe.onFailure(probeNowMs)"), 2)

    def test_unreachable_fault_row_is_rate_limited_not_removed(self):
        self.assertIn('if(hubProbe.shouldReport())crashes.add("mserver_monitor",e);',
                      MONITOR)
        self.assertEqual(MONITOR.count('crashes.add("mserver_monitor"'), 1)

    def test_ui_still_receives_the_unreachable_reason(self):
        # UnifiedVisualActivity maps this stored text to a user message.
        self.assertIn('"UNREACHABLE:"+e.getClass().getSimpleName()', MONITOR)
        self.assertIn('"artitrade_mserver_auth_v077"', MONITOR)


class FailClosedSurvives(unittest.TestCase):
    def test_both_failure_paths_still_zero_the_epochs(self):
        self.assertGreaterEqual(MONITOR.count("stockEpoch=0;coinEpoch=0;"), 2)
        self.assertIn("lastMserverAuthOk=false;", MONITOR)

    def test_failure_paths_still_disarm_when_android_flags_are_armed(self):
        for reason in ("MSERVER_STATUS_UNVERIFIED", "MSERVER_STATUS_UNREACHABLE"):
            self.assertIn('disarmIfAndroidFlagsArmed("%s")' % reason, MONITOR)
        helper = method_body(SERVICE, "private void disarmIfAndroidFlagsArmed(",
                             "private void disarmUnverifiedMarkets(")
        self.assertIn('strategy.liveEnabled("stock")||strategy.liveEnabled("coin")',
                      helper)
        self.assertIn("disarmUnverifiedMarkets(reason,null)", helper)

    def test_end_of_turn_disarm_still_runs_for_unconfirmed_lease(self):
        self.assertRegex(
            MONITOR,
            r"if\(stockEpoch<=0\|\|coinEpoch<=0\)\s*"
            r'disarmUnverifiedMarkets\("LEASE_UNCONFIRMED",p\);')

    def test_disarm_decision_rule_is_unchanged(self):
        self.assertIn("stockEpoch<=0||!lastMserverAuthOk", DISARM)
        self.assertIn("coinEpoch<=0||!lastMserverAuthOk", DISARM)
        self.assertIn('strategy.setLive("stock",false)', DISARM)
        self.assertIn('strategy.setLive("coin",false)', DISARM)
        self.assertIn("knownState!=null?knownState:strategy.snapshot()", DISARM)

    def test_live_arming_is_still_refused_everywhere(self):
        self.assertIn('if(enabled)return error("SIMULATED_ACCOUNT_ONLY_NO_BROKER_ORDERS"',
                      BRIDGE)


class NoRedundantPythonSnapshots(unittest.TestCase):
    def test_exactly_one_snapshot_per_loop_turn(self):
        self.assertEqual(MONITOR.count("strategy.snapshot()"), 1)

    def test_snapshot_is_still_written_every_turn_for_ui_freshness(self):
        # UnifiedVisualActivity rejects startup_preload_v081 older than 8.5-10 s.
        self.assertIn('getSharedPreferences("artitrade_runtime_snapshot",MODE_PRIVATE)',
                      MONITOR)
        self.assertIn("Thread.sleep(2500)", MONITOR)

    def test_notification_is_deduplicated_but_refreshed(self):
        self.assertEqual(MONITOR.count("nm.notify("), 1)
        self.assertIn("notificationText.equals(lastNotificationText)", MONITOR)
        self.assertIn("NOTIFICATION_REFRESH_MS", MONITOR)


class FaultStoreContract(unittest.TestCase):
    def test_store_delegates_to_the_tested_policy(self):
        self.assertIn("FaultLogPolicy.merge(", STORE)
        self.assertNotIn("out.length()<30", STORE)

    def test_exported_schema_keys_are_unchanged(self):
        policy = (JAVA / "FaultLogPolicy.java").read_text(encoding="utf8")
        for key in ("at", "app_version", "stage", "type", "message", "stack"):
            self.assertRegex(policy, r'\.put\("%s"' % re.escape(key))


if __name__ == "__main__":
    unittest.main()
