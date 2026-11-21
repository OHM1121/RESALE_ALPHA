"""0.2.2: a stopped engine is retried while the screen is open; every OS process exit is logged."""
import re
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
JAVA = BASE / 'app/src/main/java/com/artitrade/mobile'
UI = (JAVA / 'UnifiedVisualActivity.java').read_text(encoding='utf-8')
HEALTH = (JAVA / 'ProcessHealthV087.java').read_text(encoding='utf-8')


class Watchdog(unittest.TestCase):
    def test_periodic_refresh_runs_the_watchdog(self):
        periodic = UI.split('private final Runnable periodic=', 1)[1].split('};', 1)[0]
        self.assertIn('backgroundWatchdogTick();', periodic)

    def test_watchdog_retries_start_and_escalates_once_in_a_while(self):
        body = UI.split('private void backgroundWatchdogTick(){', 1)[1].split('private void ensureBackgroundServiceOnResume', 1)[0]
        self.assertIn('ensureBackgroundServiceOnResume();', body)       # idempotent START retry
        self.assertIn('restartFeeds();', body)                          # escalation
        self.assertIn('STALE_BEFORE_FEED_RESTART_MS', body)
        self.assertIn('FEED_RESTART_COOLDOWN_MS', body)                 # never a restart loop
        self.assertIn('ENGINE_HEARTBEAT_STALE_SECONDS=', body)          # evidence in the next export
        self.assertIn('ENGINE_HEARTBEAT_RECOVERED_AFTER_SECONDS=', body)

    def test_thresholds_are_sane(self):
        stale = int(re.search(r'STALE_BEFORE_FEED_RESTART_MS=([0-9_]+)L', UI).group(1).replace('_', ''))
        cool = int(re.search(r'FEED_RESTART_COOLDOWN_MS=([0-9_]+)L', UI).group(1).replace('_', ''))
        self.assertGreaterEqual(stale, 120_000)     # longer than a slow start-up
        self.assertGreaterEqual(cool, 300_000)

    def test_watchdog_never_arms_live_orders(self):
        body = UI.split('private void backgroundWatchdogTick(){', 1)[1].split('private void ensureBackgroundServiceOnResume', 1)[0]
        for forbidden in ('SET_LIVE', 'live_stock', 'live_coin', 'MANUAL_PAPER_SELL'):
            self.assertNotIn(forbidden, body)


class ExitLog(unittest.TestCase):
    def test_every_exit_is_recorded_except_self_exit_and_update(self):
        block = HEALTH.split('int reason=x.getReason();', 1)[1].split('String prior=', 1)[0]
        self.assertIn('REASON_EXIT_SELF', block)
        self.assertNotIn('REASON_LOW_MEMORY', block)        # the old allow-list is gone
        for name in ('USER_REQUESTED_OR_BATTERY_MANAGER', 'OS_FREEZER', 'OS_OTHER', 'USER_STOPPED'):
            self.assertIn(name, HEALTH)


if __name__ == '__main__':
    unittest.main()
