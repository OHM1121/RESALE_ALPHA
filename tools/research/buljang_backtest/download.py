"""Binance USD-M perpetual public data downloader (data.binance.vision).

Public, unauthenticated monthly zips: klines and funding rates.
Output goes under research_data/market/binance_um/ (gitignored, never commit).

usage: python3 download.py [--symbols BTCUSDT,ETHUSDT] [--tfs 15m,1h] [--start 2020-01] [--end 2026-10]
"""
import argparse
import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

BASE = "https://data.binance.vision/data/futures/um/monthly"
ROOT = Path(__file__).resolve().parents[3] / "research_data" / "market" / "binance_um"


def months(start: str, end: str):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def fetch(job):
    kind, sym, tf, mon = job
    if kind == "klines":
        url = f"{BASE}/klines/{sym}/{tf}/{sym}-{tf}-{mon}.zip"
        out = ROOT / sym / tf / f"{mon}.csv"
    else:
        url = f"{BASE}/fundingRate/{sym}/{sym}-fundingRate-{mon}.zip"
        out = ROOT / sym / "funding" / f"{mon}.csv"
    if out.exists() and out.stat().st_size > 0:
        return job, "cached"
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 404:
                return job, "404"
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                data = z.read(z.namelist()[0])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            return job, "ok"
        except Exception as e:  # noqa: BLE001
            err = str(e)
    return job, f"fail:{err}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--tfs", default="15m,1h")
    ap.add_argument("--start", default="2020-01")
    ap.add_argument("--end", default="2026-10")
    a = ap.parse_args()
    jobs = []
    for sym in a.symbols.split(","):
        for mon in months(a.start, a.end):
            for tf in a.tfs.split(","):
                jobs.append(("klines", sym, tf, mon))
            jobs.append(("funding", sym, "", mon))
    stat = {}
    with ThreadPoolExecutor(8) as ex:
        for job, st in ex.map(fetch, jobs):
            stat[st.split(":")[0]] = stat.get(st.split(":")[0], 0) + 1
            if st.startswith("fail"):
                print(job, st, file=sys.stderr)
    print(stat)


if __name__ == "__main__":
    main()
