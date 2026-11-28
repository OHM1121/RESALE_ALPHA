"""0.6.5 / 0.6.6 engine patch (owner 2026-11-27), applied AFTER patch_v010 in CI (chain: v092, v093, v105, v008, v009, v010, v065).

1. After-hours reference volume look-back (0.6.6). `D19PaperEngine.on_extended_minute` builds the separate completed KRX POST (16:00-20:00) and NXT PRE (08:00-08:30) frames. It looked only at
   earlier frames of the SAME extended session for the volume comparison ("previous 100 five-minute / 48 sixty-minute bars"), but a POST session has 48 five-minute and 4 sixty-minute bars (PRE: 6 / 0): a 5m / 60m
   after-hours reference was structurally impossible (device export: 22,374 POST 5m frames, only 3 POST signals in a week, all 30m). The owner: KRX and NXT are one market, look at the KRX bars too.
   The previous bars are now the ONE time-ordered series of the stock: the regular-session `bars` plus the earlier extended frames; the required count (100 / 48 / 2) and every other rule are unchanged.
2. The completed POST / PRE frames are also written to `bars` (source LIVE_EXT_<venue>_<segment>), so every strategy reads one continuous series across the sessions (0.6.6).
3. KRX closing single-price auction 15:20-15:30 (0.6.5): `live_session(now,'KRX')` ends at the session end minus `krx_session_v041.CLOSING_AUCTION_MINUTES`.
4. Supply-demand (GMO) paper matcher and D+1 entry / matching clock: `in_regular_clock` -> `in_tradable_clock` (continuous 09:00-15:20 or POST 16:00-20:00) at the six session checks of projectgmo/engine.py (0.6.6).
5. MA paper engine: new entries are also allowed in the after-hours session 16:00:00-19:59:15 (the tick path above already requires an after-market entitlement), besides 09:00:00-15:19:15 (0.6.6).
6. The closing single-price auction 15:20-15:30 is never counted as missing candles / unobserved minutes (0.6.7): the candidate forward observation counts a 380 minute session (was 390, +10 "unobserved"
   every night) and the fast one-minute repair does not queue the minutes 15:20-15:29.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = [ROOT / "app/src/main/assets/strategy_v070" / f"part_{i:02d}.txt" for i in range(22)]
BRIDGE = ROOT / "app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
EXPECTED_BEFORE = "0daf09fe2753efb980f350a200a45c16caa2d5b57e75e24eede1c456dffd44fd"   # the engine after the chain v092..v010
raw = base64.b64decode("".join(p.read_text("ascii") for p in PARTS), validate=True)
old_sha = hashlib.sha256(raw).hexdigest()
assert old_sha == EXPECTED_BEFORE, ("UNEXPECTED_ENGINE_BEFORE_V065", old_sha)
source = BRIDGE.read_text("utf8")
assert source.count('CORE_SHA256="' + old_sha + '"') == 1, "Unexpected bridge hash"
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    infos = z.infolist()
    assert len(infos) == 82 and z.testzip() is None
    files = {info.filename: z.read(info.filename) for info in infos}
text = files["d_v19_portfolio.py"].decode("utf8")
def swap(old, new, count=1):
    global text
    assert text.count(old) == count and text.count(new) == 0, (old[:90], text.count(old))
    text = text.replace(old, new)


# 1. one time-ordered series: regular bars + earlier extended frames
swap("""                    prev=c.execute('''SELECT ts,open,high,low,close,volume FROM v19_venue_frames
                        WHERE code=? AND venue=? AND segment=? AND frame=? AND ts<?
                        ORDER BY ts DESC LIMIT ?''',(code,venue,period,tf,first,reference_volume_lookback(tf))).fetchall()""",
     """                    prev=c.execute('''SELECT ts,open,high,low,close,volume FROM (
                          SELECT ts,open,high,low,close,volume FROM bars WHERE code=? AND frame=? AND ts<? AND source<>'' AND source NOT LIKE '%EXT_%'
                          UNION ALL
                          SELECT ts,open,high,low,close,volume FROM v19_venue_frames WHERE code=? AND frame=? AND ts<?)
                        ORDER BY ts DESC LIMIT ?''',(code,tf,first,code,tf,first,reference_volume_lookback(tf))).fetchall()""")
# 2. completed extended frames also go to bars
swap("""                    if (tf=='5m' and offset//n<2) or rejection_reason(bar,mkt,tf):continue""",
     """                    c.execute('''INSERT OR IGNORE INTO bars(code,market,frame,ts,open,high,low,close,volume,source)
                        VALUES(?,?,?,?,?,?,?,?,?,?)''',(code,mkt,tf,first,bar.open,bar.high,bar.low,bar.close,bar.volume,'LIVE_EXT_'+venue+'_'+period))
                    if (tf=='5m' and offset//n<2) or rejection_reason(bar,mkt,tf):continue""")
# 3. closing auction
swap("        if bounds and bounds[0]<=now.hour*60+now.minute<bounds[1]:\n            return True",
     "        if bounds and bounds[0]<=now.hour*60+now.minute<bounds[1]-__import__('krx_session_v041').CLOSING_AUCTION_MINUTES:\n            return True")
# 7 (0.6.8). ONE freshness rule (fresh_rule_v0680) instead of the fixed 3 second tests: order book, trade print
swap("        if age<-.5 or age>BOOK_MAX_AGE_SEC:return None,'STALE_ORDERBOOK'",
     "        if not __import__('fresh_rule_v0680').book_ok(age,'stock',BOOK_MAX_AGE_SEC):return None,'STALE_ORDERBOOK'")
swap("if px!=int(bid) or not -.5<=(now-when).total_seconds()<=BOOK_MAX_AGE_SEC:return 0",
     "if px!=int(bid) or not __import__('fresh_rule_v0680').book_ok((now-when).total_seconds(),'stock',BOOK_MAX_AGE_SEC):return 0")
swap("or (when-record[3]).total_seconds()>BOOK_MAX_AGE_SEC:",
     "or not __import__('fresh_rule_v0680').book_ok((when-record[3]).total_seconds(),'stock',BOOK_MAX_AGE_SEC):")
swap("        if (when.tzinfo is None or abs((now_kst()-when).total_seconds())>3\n",
     "        if (when.tzinfo is None or not __import__('fresh_rule_v0680').print_ok(abs((now_kst()-when).total_seconds()))\n")
files["d_v19_portfolio.py"] = text.encode("utf8")

# 4. supply-demand clock
gmo = files["projectgmo/engine.py"].decode("utf8")
assert gmo.count("__import__('krx_session_v041').in_regular_clock(") == 6, gmo.count("__import__('krx_session_v041').in_regular_clock(")
gmo = gmo.replace("__import__('krx_session_v041').in_regular_clock(", "__import__('krx_session_v041').in_tradable_clock(")
ast.parse(gmo)
files["projectgmo/engine.py"] = gmo.encode("utf8")

# 5. MA paper engine: entries in the after-hours session too
ma = files["stock_ma_engine.py"].decode("utf8")
ma_old = "            if '090000'<=now.strftime('%H%M%S')<'151915' and not after:"
ma_new = "            if ('090000'<=now.strftime('%H%M%S')<'151915' and not after) or (after and '160000'<=now.strftime('%H%M%S')<'195915'):"
assert ma.count(ma_old) == 1 and ma.count(ma_new) == 0
ma = ma.replace(ma_old, ma_new)
ast.parse(ma)
files["stock_ma_engine.py"] = ma.encode("utf8")
# 6. owner 2026-11-28: the closing single-price auction 15:20-15:30 is not "missing candles".
# (a) candidate forward observation counted a 390 minute session (09:00-15:30): every night added ~10 "unobserved" minutes -> now 380 (09:00-15:20)
ch = files["d_candidate_history.py"].decode("utf8")
for old, new, n in (("min (390 ,(int (t [8 :10 ])*60 +int (t [10 :12 ]))-(9 *60 ))", "min (380 ,(int (t [8 :10 ])*60 +int (t [10 :12 ]))-(9 *60 ))", 1),
                    ("total =390 -_mins_intraday (start )", "total =380 -_mins_intraday (start )", 1),
                    ("if day .weekday ()<5 :total +=390 ", "if day .weekday ()<5 :total +=380 ", 1)):
    assert ch.count(old) == n and ch.count(new) == 0, (old, ch.count(old))
    ch = ch.replace(old, new)
ast.parse(ch)
files["d_candidate_history.py"] = ch.encode("utf8")
# (b) the fast one-minute repair never queues a minute of the closing auction (15:20-15:29): nothing trades there, the official record cannot exist
mr = files["d_minute_repair_fast.py"].decode("utf8")
mr_old = "(when.hour==15 and when.minute<30)"
mr_new = "(when.hour==15 and when.minute<20)"
assert mr.count(mr_old) == 1 and mr.count(mr_new) == 0
mr = mr.replace(mr_old, mr_new)
ast.parse(mr)
files["d_minute_repair_fast.py"] = mr.encode("utf8")
# 7b (0.6.8). the same rule in the other vendor files (stock feed, supply-demand, close-betting, coin)
def edit_file(name, pairs):
    body = files[name].decode("utf8")
    for old, new in pairs:
        assert body.count(old) == 1 and body.count(new) == 0, (name, old[:80], body.count(old))
        body = body.replace(old, new)
    ast.parse(body)
    files[name] = body.encode("utf8")

FR = "__import__('fresh_rule_v0680')"
edit_file("d_minute_repair_fast.py", [(
    "        return -0.5<=er<=max_age and -0.5<=rr<=max_age and (r-e).total_seconds()<=max_age",
    "        fr=__import__('fresh_rule_v0680')\n        return fr.print_ok(er,'stock',max_age) and fr.print_ok(rr,'stock',max_age) and (r-e).total_seconds()<=max_age")])
edit_file("d_live.py", [
    ("                if delay > 3 or len(tick) < 9 or (not exchange_stamp_is_fresh(tick[8], tick[6])):",
     "                if (not __import__('fresh_rule_v0680').print_ok(delay)) or len(tick) < 9 or (not exchange_stamp_is_fresh(tick[8], tick[6])):"),
    ("                if -0.5<=age<=3.0 and int(book[2])>0:",
     "                if __import__('fresh_rule_v0680').book_ok(age) and int(book[2])>0:"),
    ("                if lag>3.0 and kind!='flush':",
     "                if (not __import__('fresh_rule_v0680').print_ok(lag)) and kind!='flush':"),
    ("        entry=(self._feed_generation,kind,payload,time.monotonic(),time.time())",
     "        if kind=='raw':__import__('fresh_rule_v0680').touch('stock')      # 0.6.8: the line is alive (any message)\n        entry=(self._feed_generation,kind,payload,time.monotonic(),time.time())"),
])
edit_file("d_gmo_runtime.py", [
    ("abs((datetime.now(KST) - seen).total_seconds()) > 3.0:",
     "not __import__('fresh_rule_v0680').print_ok(abs((datetime.now(KST) - seen).total_seconds())):"),
    ("        if b and abs((at-b[4]).total_seconds()) <= 3.0:",
     "        if b and __import__('fresh_rule_v0680').book_ok(abs((at-b[4]).total_seconds())):"),
    ("        if book and abs((seen - book[4]).total_seconds()) <= 3.0:",
     "        if book and __import__('fresh_rule_v0680').book_ok(abs((seen - book[4]).total_seconds())):"),
])
edit_file("at_close_engine.py", [(
    "f['book_age_sec']>3.0:reasons.append('NO_FRESH_ORDERBOOK')",
    "(not __import__('fresh_rule_v0680').book_ok(f['book_age_sec'])):reasons.append('NO_FRESH_ORDERBOOK')")])
edit_file("at_close_runner.py", [
    ("        if book['age']>2 or book['ask']<=0 or book['ask_qty']<=0 or trade_age>3:return",
     "        if (not __import__('fresh_rule_v0680').book_ok(book['age'],'stock',2.0)) or book['ask']<=0 or book['ask_qty']<=0 or (not __import__('fresh_rule_v0680').print_ok(trade_age)):return"),
    ("            if trade_age<=3:r['price']=live['price']",
     "            if __import__('fresh_rule_v0680').print_ok(trade_age):r['price']=live['price']"),
    ("                if trade_age>3:\n                    self.store.event('SCAN_STALE_TRADE'",
     "                if not __import__('fresh_rule_v0680').print_ok(trade_age):\n                    self.store.event('SCAN_STALE_TRADE'"),
])
edit_file("at_close_bridge.py", [(
    "                time.monotonic()-float(arrived_at)<=3.0)",
    "                __import__('fresh_rule_v0680').print_ok(time.monotonic()-float(arrived_at)))")])
edit_file("d_upbit.py", [
    ("                            if trade_age < -0.5 or trade_age > 10.0:",
     "                            if not __import__('fresh_rule_v0680').print_ok(trade_age,'coin',10.0):"),
    ("                                abs(time.time()-timestamp/1000)>MAX_BOOK_AGE_SEC):",
     "                                not __import__('fresh_rule_v0680').book_ok(abs(time.time()-timestamp/1000),'coin',MAX_BOOK_AGE_SEC)):"),
    ("abs(now.timestamp()-float(event_ts)/1000)>MAX_BOOK_AGE_SEC:return 0",
     "not __import__('fresh_rule_v0680').book_ok(abs(now.timestamp()-float(event_ts)/1000),'coin',MAX_BOOK_AGE_SEC):return 0"),
    ("                if age<0 or age>0.75:",
     "                if age<0 or not __import__('fresh_rule_v0680').print_ok(age,'coin',0.75):"),
    ("                        self._ws_last_frame=time.monotonic()\n                        item=json.loads(",
     "                        self._ws_last_frame=time.monotonic();__import__('fresh_rule_v0680').touch('coin')      # 0.6.8: the line is alive\n                        item=json.loads("),
])
ast.parse(text)
files["d_v19_portfolio.py"] = text.encode("utf8")
out = io.BytesIO()
with zipfile.ZipFile(out, "w") as z:
    for info in infos:
        z.writestr(info, files[info.filename])
updated = out.getvalue()
with zipfile.ZipFile(io.BytesIO(updated)) as z:
    assert z.testzip() is None and len(z.namelist()) == 82
    assert all(z.read(i.filename) == files[i.filename] for i in infos)
new_sha = hashlib.sha256(updated).hexdigest()
assert new_sha != old_sha
encoded = base64.b64encode(updated).decode("ascii")
width = (len(encoded) + 21) // 22
for i, p in enumerate(PARTS):
    p.write_text(encoded[i * width:(i + 1) * width], "ascii")
BRIDGE.write_text(source.replace('CORE_SHA256="' + old_sha + '"', 'CORE_SHA256="' + new_sha + '"'), "utf8")
print("NEW_SHA256=" + new_sha)
print("V065_POST_SESSION_LOOKBACK_PATCH_PASS")
print("UNCHANGED_ORIGINAL_MODULES=81")
print("PAPER_EXECUTION_POLICY_UNCHANGED=true")
