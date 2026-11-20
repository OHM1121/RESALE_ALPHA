"""Synthetic regression for tools/artitrade/at_pending_stall_audit.py.

The fixture reproduces the SANITIZED AGGREGATE shape reported in issue #104
(stock AT: 181 PENDING = 30m 111 / 60m 26 / day 44, zero BUY executions,
204 BLOCKED higher-frame audits, 1534 GAP_GUARDED and 306 LIMIT_TOUCH events).
Every symbol/price below is fabricated. No device data is used.
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools/artitrade/at_pending_stall_audit.py"
sys.path.insert(0, str(TOOL.parent))
import at_pending_stall_audit as audit  # noqa: E402

# DDL copied from the original engine (d_v19_portfolio / fix13 / fix14 / d_storage).
DDL = """
CREATE TABLE v19_orders(signal_id INTEGER PRIMARY KEY,code TEXT NOT NULL,tf TEXT NOT NULL,
  venue TEXT NOT NULL,score REAL NOT NULL,limit_won INTEGER NOT NULL,
  since_ts TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING');
CREATE TABLE v19_gap_holds(tf TEXT NOT NULL,code TEXT NOT NULL,venue TEXT NOT NULL,
  ref_ts TEXT NOT NULL,detected_at TEXT NOT NULL,missing_json TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'BLOCKED',resolved_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(tf,code,venue)) WITHOUT ROWID;
CREATE TABLE v19_events(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,code TEXT NOT NULL,
  event TEXT NOT NULL,detail TEXT NOT NULL);
CREATE TABLE v19_higher_frame_audit14(signal_id INTEGER PRIMARY KEY,code TEXT NOT NULL,tf TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('BLOCKED','VERIFIED','EXPIRED')),
  checked_at TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL DEFAULT '');
CREATE TABLE v19_pending_5m_audit(signal_id INTEGER PRIMARY KEY,code TEXT NOT NULL,venue TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('BLOCKED','VERIFIED','EXPIRED')),
  checked_at TEXT NOT NULL,verified_through TEXT NOT NULL DEFAULT '',detail TEXT NOT NULL DEFAULT '');
CREATE TABLE executions(id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE NOT NULL,
  tf TEXT NOT NULL,code TEXT NOT NULL,name TEXT NOT NULL,position_id INTEGER NOT NULL,ts TEXT NOT NULL,
  side TEXT NOT NULL,qty INTEGER NOT NULL,price INTEGER NOT NULL,gross INTEGER NOT NULL,fees INTEGER NOT NULL,
  tax INTEGER NOT NULL,cash_after INTEGER NOT NULL,reason TEXT NOT NULL,fill_method TEXT NOT NULL,
  seen_at TEXT NOT NULL);
CREATE TABLE positions(id INTEGER PRIMARY KEY AUTOINCREMENT,tf TEXT NOT NULL,code TEXT NOT NULL);
CREATE TABLE runtime_error_summary(signature TEXT PRIMARY KEY,context TEXT NOT NULL,error_type TEXT NOT NULL,
  message TEXT NOT NULL,occurrences INTEGER NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
  sample_traceback TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE stream_health(id INTEGER PRIMARY KEY AUTOINCREMENT,seen_at TEXT NOT NULL,state TEXT NOT NULL,details TEXT NOT NULL);
"""
SYMBOL = "990013"      # fabricated
PRICE = "1234567"      # fabricated
NOW = "20261119101500"


def build(path, buys=0, with_tables=True):
    db = sqlite3.connect(path)
    if with_tables:
        db.executescript(DDL)
        n = 0
        # 30m: 11 above the 200k cap; 60m: 3 above 250k (and one exactly AT the
        # cap, which is affordable); day: none above 500k.
        above = {"30m": 11, "60m": 3, "day": 0}
        for tf, count in (("30m", 111), ("60m", 26), ("day", 44)):
            for i in range(count):
                n += 1
                limit = {"30m": 200_001, "60m": 250_001, "day": 500_001}[tf] if i < above[tf] else \
                        {"30m": 150_000, "60m": 250_000, "day": 400_000}[tf]
                db.execute("INSERT INTO v19_orders VALUES(?,?,?,?,?,?,?,?)",
                           (n, SYMBOL, tf, "KRX", 55.0, limit, "20261119083000", "PENDING"))
        for _ in range(23):
            n += 1
            db.execute("INSERT INTO v19_orders VALUES(?,?,?,?,?,?,?,?)",
                       (n, SYMBOL, "30m", "KRX", 55.0, 900_000, "20261118090000", "EXPIRED_FIX13_SESSION"))
        for i in range(204):
            db.execute("INSERT INTO v19_higher_frame_audit14 VALUES(?,?,?,?,?,?)",
                       (i + 1, SYMBOL, "30m", "BLOCKED", NOW, "LIVE_PARENT_GAP"))
        for i in range(1534):
            db.execute("INSERT INTO v19_events(at,code,event,detail) VALUES(?,?,?,?)",
                       ("20261119095000", SYMBOL, "FIX14_LIVE_HIGHER_FRAME_GAP_GUARDED", "p=" + PRICE))
        reasons = ([("BELOW_ONE_SHARE", 200), ("FIX14_HIGHER_FRAME_REST_UNVERIFIED", 70),
                    ("LIMIT_TOUCH_INSUFFICIENT_EXECUTED_QUANTITY", 36)])
        for reason, count in reasons:
            for i in range(count):
                detail = json.dumps({"tf": "30m", "venue": "KRX", "reason": reason,
                                     "observed_trade_price": int(PRICE), "code_hint": SYMBOL,
                                     "pending_limit_price": int(PRICE), "paper_only": True})
                db.execute("INSERT INTO v19_events(at,code,event,detail) VALUES(?,?,?,?)",
                           ("20261119100000", SYMBOL, "LIMIT_TOUCH_NOT_BOUGHT_FIX6", detail))
        db.execute("INSERT INTO runtime_error_summary VALUES(?,?,?,?,?,?,?,?)",
                   ("sig", "KIWOOM_REST_" + SYMBOL, "gaierror", "name resolution " + SYMBOL, 401, "a", "b", "tb"))
        for i in range(buys):
            db.execute("INSERT INTO executions(event_key,tf,code,name,position_id,ts,side,qty,price,gross,fees,tax,"
                       "cash_after,reason,fill_method,seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("k%d" % i, "30m", SYMBOL, "FIXTURE", 1, NOW, "BUY", 1, 1, 1, 0, 0, 1, "r", "m", NOW))
    db.commit()
    db.close()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AtPendingStallAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fixture.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_reproduces_reported_aggregate_and_flags_the_pattern(self):
        build(self.db)
        r = audit.audit(self.db)
        self.assertEqual(r["pending_total"], 181)
        self.assertEqual(r["orders_by_status_tf"]["PENDING"], {"30m": 111, "60m": 26, "day": 44})
        self.assertEqual(r["orders_by_status_tf"]["EXPIRED_FIX13_SESSION"], {"30m": 23})
        self.assertEqual(r["higher_frame_audit_state_tf"]["BLOCKED"], {"30m": 204})
        self.assertEqual(r["executions_side_tf"], {})
        self.assertEqual(r["events_all_time_top"]["FIX14_LIVE_HIGHER_FRAME_GAP_GUARDED"], 1534)
        self.assertEqual(r["events_all_time_top"]["LIMIT_TOUCH_NOT_BOUGHT_FIX6"], 306)
        self.assertEqual(r["pending_above_per_symbol_cap_by_tf"], {"30m": 11, "60m": 3})
        self.assertEqual(r["limit_touch_not_bought_reasons"], {
            "BELOW_ONE_SHARE": 200, "FIX14_HIGHER_FRAME_REST_UNVERIFIED": 70,
            "LIMIT_TOUCH_INSUFFICIENT_EXECUTED_QUANTITY": 36})
        hints = " | ".join(r["verdict_hints_INFERRED"])
        self.assertIn("PENDING_WITHOUT_ANY_BUY", hints)
        self.assertIn("PARENT_CONTINUITY_BLOCKS_PRESENT", hints)
        self.assertIn("GAP_GUARD_DOMINANT", hints)
        self.assertIn("FEED_ERRORS_COEXIST", hints)
        self.assertIn("UNAFFORDABLE_PENDING_PRESENT: 14 of 181", hints)
        self.assertIn("TOUCH_REJECT_TOP_REASON: BELOW_ONE_SHARE (200 events)", hints)

    def test_age_buckets_use_the_exported_file_time_not_the_wall_clock(self):
        build(self.db)
        r = audit.audit(self.db)
        # newest row is 10:00 vs since 08:30 -> 1.5 h -> lt_6h
        self.assertEqual(r["pending_age_buckets_by_tf"]["30m"], {"lt_6h": 111})
        old = audit.audit(self.db, now=audit._stamp("20261125000000"))
        self.assertEqual(old["pending_age_buckets_by_tf"]["day"], {"ge_3d": 44})

    def test_output_is_sanitized(self):
        build(self.db)
        text = json.dumps(audit.audit(self.db), ensure_ascii=False)
        self.assertNotIn(SYMBOL, text)
        self.assertNotIn(PRICE, text)
        self.assertNotIn("name resolution", text)
        self.assertFalse(audit.audit(self.db)["contains_symbols_prices_or_messages"])

    def test_caps_override_changes_the_unaffordable_count(self):
        build(self.db)
        r = audit.audit(self.db, caps={"5m": 1, "30m": 100_000, "60m": 250_000, "day": 500_000})
        self.assertEqual(r["pending_above_per_symbol_cap_by_tf"]["30m"], 111)
        r = audit.audit(self.db, caps={"30m": 10**9, "60m": 10**9, "day": 10**9})
        self.assertEqual(r["pending_above_per_symbol_cap_by_tf"], {})
        done = subprocess.run([sys.executable, "-I", str(TOOL), str(self.db), "--caps",
                               "30m=200000,60m=250000,day=500000"], capture_output=True, text=True)
        self.assertEqual(json.loads(done.stdout)["pending_above_per_symbol_cap_by_tf"], {"30m": 11, "60m": 3})
        bad = subprocess.run([sys.executable, "-I", str(TOOL), str(self.db), "--caps", "oops"],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)

    def test_reason_extraction_never_leaks_other_detail_fields(self):
        build(self.db)
        text = json.dumps(audit.audit(self.db))
        self.assertNotIn("code_hint", text)
        self.assertNotIn("observed_trade_price", text)
        self.assertEqual(audit._reason_code('{"reason":"lower case free text 990013"}'), None)
        self.assertEqual(audit._reason_code("not json"), None)
        self.assertEqual(audit._reason_code({"reason": "BELOW_ONE_SHARE"}), "BELOW_ONE_SHARE")

    def test_strictly_read_only(self):
        build(self.db)
        before = digest(self.db)
        audit.audit(self.db)
        subprocess.run([sys.executable, "-I", str(TOOL), str(self.db)], check=True,
                       capture_output=True)
        self.assertEqual(digest(self.db), before)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ["fixture.sqlite3"])

    def test_missing_tables_are_absent_not_zero(self):
        build(self.db, with_tables=False)
        r = audit.audit(self.db)
        self.assertEqual(r["pending_total"], audit.ABSENT)
        self.assertEqual(r["executions_side_tf"], audit.ABSENT)
        self.assertIn("EVIDENCE_TABLES_ABSENT", " ".join(r["verdict_hints_INFERRED"]))
        self.assertNotIn("PENDING_WITHOUT_ANY_BUY", " ".join(r["verdict_hints_INFERRED"]))

    def test_healthy_empty_is_distinct_from_absent(self):
        db = sqlite3.connect(self.db)
        db.executescript(DDL)
        db.commit()
        db.close()
        r = audit.audit(self.db)
        self.assertEqual(r["pending_total"], 0)
        self.assertIn("NO_PENDING_ORDERS", " ".join(r["verdict_hints_INFERRED"]))

    def test_a_buy_removes_the_no_buy_hint(self):
        build(self.db, buys=2)
        r = audit.audit(self.db)
        self.assertEqual(r["executions_side_tf"], {"BUY": {"30m": 2}})
        self.assertNotIn("PENDING_WITHOUT_ANY_BUY", " ".join(r["verdict_hints_INFERRED"]))

    def test_cli_reports_a_missing_file_without_traceback(self):
        done = subprocess.run([sys.executable, "-I", str(TOOL), str(Path(self.tmp.name) / "none.sqlite3")],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertFalse(json.loads(done.stdout)["ok"])
        self.assertNotIn("Traceback", done.stderr)


if __name__ == "__main__":
    unittest.main()
