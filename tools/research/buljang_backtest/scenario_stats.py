"""Part A (descriptive, uses the future on purpose): how often does each of the five
scenario assumptions describe what the moving line actually did over the next H bars?

Windows are non-overlapping (index step = H) to limit autocorrelation.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import bt_lib as B

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT"]
K = 0.5
REP = {3: 0.0, 4: -40.0, 5: -75.0}  # representative favourable-orientation angles for 3/4/5 (1 and 2 depend on theta0)
CONT = 0.75  # realised angle >= CONT*theta0 counts as "current angle continued"


def bucket(thr, th0):
    if thr >= CONT * th0:
        return 1
    if thr > 15:
        return 2
    if thr >= -15:
        return 3
    if thr > -65:
        return 4
    return 5


def run_one(sym, tf, line, H, reg, W=200, n=10):
    df = B.add_indicators(B.load_klines(sym, tf), W=W, n=n)
    X = df[line].to_numpy(); th = df["ang_" + line].to_numpy(); rng = df["rng"].to_numpy(); c = df["close"].to_numpy()
    ts = df["ts"].to_numpy(); rg = reg.reindex(ts, method="ffill").to_numpy()
    rows = []
    for s in (1, -1):
        for i in range(W + 50, len(df) - H, H):
            if not (np.isfinite(th[i]) and rng[i] > 0 and np.isfinite(X[i + H]) and np.isfinite(rg[i])):
                continue
            t0 = s * th[i]
            if t0 <= 15:
                continue
            slope_real = (X[i + H] - X[i]) / H
            thr = s * math.degrees(math.atan(slope_real / (rng[i] / W)))
            b = bucket(thr, t0)
            reps = {1: t0, 2: K * t0, **REP}
            err = {q: abs(X[i] + s * H * math.tan(math.radians(a)) * rng[i] / W - X[i + H]) / c[i] for q, a in reps.items()}
            best = min(err, key=err.get)
            rows.append((s, rg[i], t0, b, best, err))
    return rows


def summarize(rows):
    out = {}
    def agg(sel):
        if not sel:
            return None
        nb = np.bincount([r[3] for r in sel], minlength=6)[1:] / len(sel)
        nbest = np.bincount([r[4] for r in sel], minlength=6)[1:] / len(sel)
        mae = {q: float(np.mean([r[5][q] for r in sel]) * 100) for q in range(1, 6)}
        return {"n": len(sel), "realised_bucket": [round(float(x), 3) for x in nb], "closest_proj": [round(float(x), 3) for x in nbest],
                "mae_pct": {q: round(v, 3) for q, v in mae.items()}}
    out["all"] = agg(rows)
    for name, f in (("up_bull", lambda r: r[0] == 1 and r[1] == 1), ("up_bear", lambda r: r[0] == 1 and r[1] == -1),
                    ("down_bull", lambda r: r[0] == -1 and r[1] == 1), ("down_bear", lambda r: r[0] == -1 and r[1] == -1)):
        out[name] = agg([r for r in rows if f(r)])
    for lo, hi in ((15, 30), (30, 45), (45, 90)):
        out[f"th0_{lo}_{hi}"] = agg([r for r in rows if lo < r[2] <= hi])
    return out


if __name__ == "__main__":
    reg = B.cycle_regime(B.load_klines("BTCUSDT", "1h"))
    res = {}
    for tf, Hs in (("1h", (24, 96)), ("15m", (96, 384))):
        for line in ("vw100", "sma50", "sma200"):
            for H in Hs:
                rows = []
                for sym in SYMS:
                    rows += run_one(sym, tf, line, H, reg)
                res[f"{tf}_{line}_H{H}"] = summarize(rows)
                print(tf, line, H, res[f"{tf}_{line}_H{H}"]["all"], flush=True)
    out = Path(__file__).resolve().parents[3] / "research_data" / "backtests"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scenario_stats.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
