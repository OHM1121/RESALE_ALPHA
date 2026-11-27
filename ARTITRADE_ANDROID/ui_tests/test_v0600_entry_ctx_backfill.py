"""0.6.0: the BUY-time context (MA 5/10/20/60/120 above/below + slope, trend) of every stock position is completed after the close from the stored bars."""
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path

import entry_ctx_backfill_v0600 as bf

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 27, 21, 0, tzinfo=KST)


class Store:
    def __init__(self, path):
        self.path, self.lock = path, threading.RLock()

    def connect(self):
        cx = sqlite3.connect(self.path, timeout=5)
        cx.row_factory = sqlite3.Row
        return cx


def d_engine_end(ts):
    from d_engine import bar_end
    return bar_end(ts, "30m")


def slots(n, last_day=dt.date(2026, 11, 20)):
    """n consecutive 30m regular bars ending with the bar that starts 15:00 of last_day (oldest first), as stamps."""
    out, day = [], last_day
    per_day = ["%02d%02d00" % (m // 60, m % 60) for m in range(9 * 60, 15 * 60 + 1, 30)]
    while len(out) < n:
        if day.weekday() < 5:
            out = [day.strftime("%Y%m%d") + s for s in per_day] + out
        day -= dt.timedelta(days=1)
    return out[-n:]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "stock" / "MA_EPOCH_V067" / "STOCK_MA_ENGINE_V1").mkdir(parents=True)
        (self.root / "stock" / "trend").mkdir(parents=True)
        main = self.root / "stock" / "ARTITRADE_D.sqlite3"
        cx = sqlite3.connect(main)
        cx.executescript("""CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT);
            CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,code TEXT,entry_ts TEXT,entry_price INTEGER);
            CREATE TABLE nl1118_reference_positions(position_id INTEGER PRIMARY KEY,policy TEXT,market TEXT,frame TEXT,symbol TEXT,intent_json TEXT,updated_at TEXT);""")
        cx.commit()
        cx.close()
        self.store = Store(str(main))
        ma = sqlite3.connect(self.root / "stock" / "MA_EPOCH_V067" / "STOCK_MA_ENGINE_V1" / "ma_paper.sqlite3")
        ma.executescript("""CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,code TEXT,entry_at TEXT,entry INTEGER);
            CREATE TABLE ma_v067_positions(position_id INTEGER PRIMARY KEY,kind TEXT,frame TEXT,code TEXT,state_json TEXT,created_at TEXT,updated_at TEXT);""")
        ma.commit()
        ma.close()
        tr = sqlite3.connect(self.root / "stock" / "trend" / "market_trend_wallet_v054.sqlite3")
        tr.executescript("CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,symbol TEXT,entry_at TEXT,entry REAL,payload TEXT,updated_at TEXT);")
        tr.commit()
        tr.close()
        self.rt = types.SimpleNamespace(stock_runner=types.SimpleNamespace(store=self.store), root=str(self.root))

    def bars(self, code, closes, last_day=dt.date(2026, 11, 20), source="FIX15_VERIFIED_KIWOOM_REST"):
        with self.store.connect() as db:
            for ts, close in zip(slots(len(closes), last_day), closes):
                db.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)", (code, "KOSPI", "30m", ts, close, close, close, close, 10, source))

    def at(self, pid, code, entry_ts, entry, sidecar=True, extra=None):
        with self.store.connect() as db:
            db.execute("INSERT INTO positions VALUES(?,?,?,?,?)", (pid, "30m", code, entry_ts, entry))
            if sidecar:
                body = {"entry": float(entry), "frame": "30m", "symbol": code, "rr_size": {"ratio": 0.47, "gain_pct": 3.0, "loss_pct": 6.3, "stop_source": "AT_REFERENCE_HARD_STOP"}}
                body.update(extra or {})
                db.execute("INSERT INTO nl1118_reference_positions VALUES(?,?,?,?,?,?,?)", (pid, "NEW_LEGACY_1118", "stock", "30m", code, json.dumps(body), ""))

    def read(self, pid):
        with self.store.connect() as db:
            return json.loads(db.execute("SELECT intent_json FROM nl1118_reference_positions WHERE position_id=?", (pid,)).fetchone()[0])


class AtPositions(Base):
    def test_fills_ma_above_below_slope_and_trend_from_completed_bars_before_the_buy(self):
        closes = [100.0 + i * 0.5 for i in range(130)]                    # a steady uptrend
        self.bars("000001", closes)
        self.at(1, "000001", "20261123160332", 150)                         # entry after the last stored bar
        report = bf.run(self.rt, NOW)
        self.assertEqual(report["sources"]["AT"]["filled"], 1)
        ctx = self.read(1)["entry_ctx"]
        self.assertEqual(ctx["bars"], 130)
        self.assertEqual(ctx["trend"], "UP")
        self.assertEqual(ctx["ma"]["5"]["entry_vs_ma"], "BELOW")             # 150 < MA5 of 162.5..164.5
        self.assertEqual(ctx["ma"]["120"]["entry_vs_ma"], "ABOVE")           # 150 > MA120 (about 134.5)
        self.assertAlmostEqual(ctx["ma"]["20"]["slope_pct"], (sum(closes[-20:]) / sum(closes[-21:-1]) - 1) * 100, 5)
        self.assertEqual(ctx["market_score"], {"score": None, "basis": "NOT_RECORDED_AT_BUY_BACKFILL"})
        self.assertEqual(ctx["rr"]["ratio"], 0.47)
        self.assertEqual(ctx["backfill"]["version"], bf.VERSION)

    def test_a_candle_still_forming_at_the_buy_is_never_used(self):
        self.bars("000001", [100.0] * 130)
        entry = "%s100500" % (dt.date(2026, 11, 20).strftime("%Y%m%d"))     # 10:05 of the last stored day: the 10:00-10:30 candle forms
        self.at(1, "000001", entry, 100)
        bf.run(self.rt, NOW)
        with self.store.connect() as db:
            before = db.execute("SELECT COUNT(*) FROM bars WHERE code='000001' AND ts<'20261120100000'").fetchone()[0]
        self.assertEqual(self.read(1)["entry_ctx"]["bars"], before)

    def test_other_sidecar_fields_are_untouched_and_the_run_is_idempotent(self):
        self.bars("000001", [100.0] * 130)
        self.at(1, "000001", "20261123160332", 100, extra={"stage": 1, "qty": 7.0})
        bf.run(self.rt, NOW)
        first = self.read(1)
        self.assertEqual((first["stage"], first["qty"], first["entry"]), (1, 7.0, 100.0))
        report = bf.run(self.rt, NOW + dt.timedelta(minutes=5))
        self.assertEqual((report["sources"]["AT"]["filled"], report["sources"]["AT"]["improved"], report["sources"]["AT"]["already_complete"]), (0, 0, 1))
        self.assertEqual(self.read(1), first)

    def test_a_context_is_improved_only_when_more_bars_exist_later(self):
        self.bars("000001", [100.0] * 60, last_day=dt.date(2026, 11, 20))
        self.at(1, "000001", "20261123160332", 100)
        bf.run(self.rt, NOW)
        self.assertEqual(self.read(1)["entry_ctx"]["bars"], 60)
        self.bars("000001", [100.0] * 60, last_day=dt.date(2026, 11, 10))            # the weekend audit adds older candles
        report = bf.run(self.rt, NOW + dt.timedelta(hours=1))
        self.assertEqual(report["sources"]["AT"]["improved"], 1)
        self.assertEqual(self.read(1)["entry_ctx"]["bars"], 120)

    def test_an_existing_context_with_as_many_bars_is_kept(self):
        self.bars("000001", [100.0] * 130)
        keep = {"version": "ENTRY_CTX_V0370", "bars": 140, "trend": "DOWN", "market_score": {"score": -12.0}}
        self.at(1, "000001", "20261123160332", 100, extra={"entry_ctx": keep})
        bf.run(self.rt, NOW)
        self.assertEqual(self.read(1)["entry_ctx"], keep)

    def test_every_unresolved_position_is_listed_with_its_reason(self):
        self.bars("000001", [100.0] * 130)
        self.bars("000003", [100.0] * 30)                                   # too few for MA120
        self.at(1, "000001", "20261123160332", 100, sidecar=False)          # no sidecar row
        self.at(2, "000002", "20261123160332", 100)                          # no bars at all
        self.at(3, "000003", "20261123160332", 100)                          # partial
        self.at(4, "000001", "", 100)                                        # no entry time
        report = bf.run(self.rt, NOW)["sources"]["AT"]
        reasons = {u["id"]: u["reason"] for u in report["unresolved"]}
        self.assertEqual(reasons[1], "NO_SIDECAR_ROW")
        self.assertEqual(reasons[2], "NO_COMPLETED_BARS_BEFORE_BUY")
        self.assertEqual(reasons[3], "PARTIAL_30_OF_121_BARS")
        self.assertEqual(report["scanned"], 4)
        self.assertEqual((report["no_sidecar"], report["no_bars"], report["filled"]), (1, 1, 1))
        self.assertEqual(self.read(3)["entry_ctx"]["ma"]["60"]["ma"], None)    # missing evidence is None, never a guess
        saved = json.loads((self.root / "stock" / bf.STATE_FILE).read_text("utf8"))
        self.assertEqual(saved["sources"]["AT"]["scanned"], 4)
        lines = (self.root / "stock" / bf.LOG_FILE).read_text("utf8").strip().splitlines()
        self.assertEqual(len(lines), 1)


class MaAndTrend(Base):
    def test_ma_and_trend_sidecars_are_completed_too_and_missing_ones_reported(self):
        self.bars("000005", [100.0 + i * 0.3 for i in range(130)])
        ma = sqlite3.connect(self.root / "stock" / "MA_EPOCH_V067" / "STOCK_MA_ENGINE_V1" / "ma_paper.sqlite3")
        ma.execute("INSERT INTO positions VALUES(1,'30m','000005','2026-11-23T16:03:32+09:00',130)")
        ma.execute("INSERT INTO ma_v067_positions VALUES(1,'REVERSAL_UP','30m','000005',?,'','')", (json.dumps({"entry": 130.0, "rr_size": {"ratio": 1.2}}),))
        ma.execute("INSERT INTO positions VALUES(2,'30m','000005','2026-11-23T16:03:32+09:00',130)")          # legacy MA position without a v067 sidecar
        ma.commit()
        ma.close()
        tr = sqlite3.connect(self.root / "stock" / "trend" / "market_trend_wallet_v054.sqlite3")
        tr.execute("INSERT INTO positions VALUES(1,'30m','000005','2026-11-23T16:03:32+09:00',130,?,'')", (json.dumps({"symbol": "000005", "entry": 130.0}),))
        tr.commit()
        tr.close()
        report = bf.run(self.rt, NOW)["sources"]
        self.assertEqual(report["MA"]["filled"], 1)
        self.assertEqual([u["reason"] for u in report["MA"]["unresolved"] if u["id"] == 2], ["NO_SIDECAR_ROW"])
        self.assertEqual(report["TREND"]["filled"], 1)
        with sqlite3.connect(self.root / "stock" / "MA_EPOCH_V067" / "STOCK_MA_ENGINE_V1" / "ma_paper.sqlite3") as db:
            state = json.loads(db.execute("SELECT state_json FROM ma_v067_positions WHERE position_id=1").fetchone()[0])
        self.assertEqual(state["entry_ctx"]["bars"], 130)
        self.assertEqual(state["rr_size"], {"ratio": 1.2})
        with sqlite3.connect(self.root / "stock" / "trend" / "market_trend_wallet_v054.sqlite3") as db:
            payload = json.loads(db.execute("SELECT payload FROM positions WHERE id=1").fetchone()[0])
        self.assertEqual(payload["entry_ctx"]["trend"], "UP")

    def test_missing_databases_are_not_an_error(self):
        (self.root / "stock" / "trend" / "market_trend_wallet_v054.sqlite3").unlink()
        report = bf.run(self.rt, NOW)
        self.assertNotIn("error", report)
        self.assertEqual(report["sources"]["TREND"]["scanned"], 0)

    def test_never_raises_without_a_runner(self):
        self.assertEqual(bf.run(types.SimpleNamespace(stock_runner=None, root=None), NOW)["sources"], {})


if __name__ == "__main__":
    unittest.main()
