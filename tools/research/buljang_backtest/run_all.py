"""Run the full Buljang scenario backtest grid. Output: research_data/backtests/results.json (gitignored)."""
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import bt_lib as B

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT"]
OUT = Path(__file__).resolve().parents[3] / "research_data" / "backtests"
OUT.mkdir(parents=True, exist_ok=True)
PERIODS = {"P1_2020-2022": ("2020-01-01", "2023-01-01"), "P2_2023-2024": ("2023-01-01", "2025-01-01"), "P3_2025-2026": ("2025-01-01", "2027-01-01")}
_cache = {}


def ms(d):
    import pandas as pd
    return int(pd.Timestamp(d).timestamp() * 1000)


def get(sym, tf, W=200, n=10):
    k = (sym, tf, W, n)
    if k not in _cache:
        _cache[k] = B.add_indicators(B.load_klines(sym, tf), W=W, n=n)
    return _cache[k]


_fund = {s: B.load_funding(s) for s in SYMS}
REG = B.cycle_regime(B.load_klines("BTCUSDT", "1h"))
rng_boot = np.random.default_rng(7)


def pooled(trades_all):
    if not trades_all:
        return {"n": 0}
    pr = np.array([t.pnl_r for t in trades_all])
    boots = [rng_boot.choice(pr, len(pr)).mean() for _ in range(2000)]
    gp, gl = pr[pr > 0].sum(), -pr[pr <= 0].sum()
    return {"n": len(pr), "win": float((pr > 0).mean()), "avg_r": float(pr.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "pf": float(gp / gl) if gl > 0 else None, "tp_rate": float(np.mean([t.reason == "tp" for t in trades_all])),
            "stop_rate": float(np.mean([t.reason.startswith("stop") for t in trades_all]))}


def run_set(tf, p, W=200, n=10, syms=SYMS, **kw):
    allt, per = [], {}
    for sym in syms:
        df = get(sym, tf, W, n)
        tr, eq = B.run(df, _fund[sym], p, regime=REG, **kw)
        allt += tr
        per[sym] = B.summarize(tr, eq, p.start_equity)
    return allt, per


def block(trades):
    d = pooled(trades)
    d["by_side"] = B.by_key(trades, lambda t: {1: "long", -1: "short"}[t.side])
    d["by_regime"] = B.by_key(trades, lambda t: {1.0: "bull", -1.0: "bear", 0.0: "na"}[t.regime])
    d["by_side_regime"] = B.by_key(trades, lambda t: f"{'long' if t.side == 1 else 'short'}_{ {1.0: 'bull', -1.0: 'bear', 0.0: 'na'}[t.regime] }")
    d["by_scen"] = B.by_key(trades, lambda t: t.scen)
    d["by_year"] = B.by_key(trades, lambda t: __import__("pandas").Timestamp(t.t_in, unit="ms").year)
    return d


if __name__ == "__main__":
    res = {"primary": {}, "periods": {}, "sens": {}, "null": {}}
    t0 = time.time()
    # ---------- primary grid: video values fixed
    for tf in ("1h", "15m"):
        for line in ("vw100", "sma50", "sma200"):
            for mode in ("angle", "naive"):
                p = B.Params(line=line, side="both", use_angle=(mode == "angle"))
                tr, per = run_set(tf, p)
                res["primary"][f"{tf}|{line}|{mode}"] = {**block(tr), "per_symbol": per}
                print(tf, line, mode, {k: res["primary"][f"{tf}|{line}|{mode}"][k] for k in ("n", "win", "avg_r", "ci95", "tp_rate")}, f"{time.time()-t0:.0f}s", flush=True)
    # ---------- periods for the vw100 primary
    for tf in ("1h", "15m"):
        for name, (a, b) in PERIODS.items():
            for mode in ("angle", "naive"):
                p = B.Params(line="vw100", side="both", use_angle=(mode == "angle"))
                tr, _ = run_set(tf, p, t_from=ms(a), t_to=ms(b))
                res["periods"][f"{tf}|vw100|{mode}|{name}"] = pooled(tr)
    print("periods done", f"{time.time()-t0:.0f}s", flush=True)
    # ---------- sensitivity on 1h vw100 (exploratory, no selection)
    base = B.Params(line="vw100", side="both")
    variants = {
        "base": (base, 200, 10),
        "exit_0.5/2.5": (replace(base, stop_pct=.005, tp_pct=.025), 200, 10), "exit_1/3": (replace(base, tp_pct=.03), 200, 10),
        "exit_1.5/4.5": (replace(base, stop_pct=.015, tp_pct=.045), 200, 10), "exit_2/6": (replace(base, stop_pct=.02, tp_pct=.06), 200, 10),
        "W100": (base, 100, 10), "W400": (base, 400, 10), "n5": (base, 200, 5), "n20": (base, 200, 20),
        "k0.3": (replace(base, k=.3), 200, 10), "k0.7": (replace(base, k=.7), 200, 10),
        "scen1only": (replace(base, scen_set=(1,)), 200, 10), "scen2only": (replace(base, scen_set=(2,)), 200, 10), "scen3only": (replace(base, scen_set=(3,)), 200, 10),
        "scen12": (replace(base, scen_set=(1, 2)), 200, 10),
        "plan12": (replace(base, plan_len=12), 200, 10), "plan48": (replace(base, plan_len=48), 200, 10),
        "flat10": (replace(base, flat_deg=10.0), 200, 10), "flat20": (replace(base, flat_deg=20.0), 200, 10),
        "novol": (replace(base, vol_mult=0.0), 200, 10), "time24": (replace(base, max_bars=24), 200, 10), "time96": (replace(base, max_bars=96), 200, 10),
        "long_only": (replace(base, side="long"), 200, 10), "short_only": (replace(base, side="short"), 200, 10),
        "nocost": (replace(base, fee=0.0, slip=0.0), 200, 10),
    }
    for name, (p, W, n) in variants.items():
        tr, _ = run_set("1h", p, W=W, n=n)
        res["sens"][name] = pooled(tr)
    print("sens done", f"{time.time()-t0:.0f}s", flush=True)
    # ---------- null model (random entries) for 1h and 15m vw100 primary
    for tf in ("1h", "15m"):
        key = f"{tf}|vw100|angle"
        prim = res["primary"][key]
        sims = []
        for sd in range(60):
            allt = []
            for sym in SYMS:
                df = get(sym, tf)
                n_bars = len(df)
                # match trade count per symbol
                n_sym = res["primary"][key]["per_symbol"][sym].get("n", 0)
                avgb = res["primary"][key]["per_symbol"][sym].get("avg_bars", 15)
                prob = n_sym / max(1, n_bars - n_sym * avgb)
                tr, _ = B.run(df, _fund[sym], B.Params(line="vw100", side="both", rand_prob=prob, seed=1000 * sd + SYMS.index(sym)), regime=REG)
                allt += tr
            if allt:
                sims.append(float(np.mean([t.pnl_r for t in allt])))
        sims = np.array(sims)
        res["null"][key] = {"sims": len(sims), "mean_avg_r": float(sims.mean()), "p5": float(np.percentile(sims, 5)), "p95": float(np.percentile(sims, 95)),
                            "actual_avg_r": prim["avg_r"], "share_of_null_ge_actual": float((sims >= prim["avg_r"]).mean())}
        print("null", key, res["null"][key], f"{time.time()-t0:.0f}s", flush=True)
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    print("saved", OUT / "results.json")
