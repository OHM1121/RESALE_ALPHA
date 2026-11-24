"""Smoke tests: causality (no lookahead), angle sign, engine runs on synthetic data.
run: python3 -I tools/research/buljang_backtest/test_bt_smoke.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bt_lib as B  # noqa: E402


def synth(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0002, 0.004, n)
    c = 100 * np.exp(np.cumsum(ret))
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + rng.uniform(0, 0.003, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 0.003, n))
    v = rng.uniform(100, 1000, n)
    return pd.DataFrame({"ts": 1_600_000_000_000 + np.arange(n) * 3_600_000, "open": o, "high": hi, "low": lo, "close": c, "volume": v})


def test_causal():
    df = synth()
    full = B.add_indicators(df)
    cut = 2000
    part = B.add_indicators(df.iloc[: cut + 1].copy())
    for col in ("sma50", "sma200", "vw100", "volma", "rng", "ang_sma50", "ang_vw100"):
        a, b = full[col].iloc[cut], part[col].iloc[cut]
        assert np.isclose(a, b, equal_nan=True), (col, a, b)


def test_angle_sign():
    n = 600
    up = pd.DataFrame({"ts": np.arange(n) * 3_600_000, "open": 1 + np.arange(n) * .01, "high": 1.005 + np.arange(n) * .01,
                       "low": .995 + np.arange(n) * .01, "close": 1 + np.arange(n) * .01, "volume": np.ones(n)})
    d = B.add_indicators(up)
    assert d["ang_sma50"].iloc[-1] > 15
    dn = up.copy()
    for c in ("open", "high", "low", "close"):
        dn[c] = 10 - dn[c]
    d2 = B.add_indicators(dn)
    assert d2["ang_sma50"].iloc[-1] < -15


def test_engine_runs_and_is_deterministic():
    df = B.add_indicators(synth(6000, 3))
    p = B.Params(line="sma50", side="both")
    t1, e1 = B.run(df, (np.array([], dtype="int64"), np.array([])), p)
    t2, e2 = B.run(df, (np.array([], dtype="int64"), np.array([])), p)
    assert [t.pnl_r for t in t1] == [t.pnl_r for t in t2]
    for t in t1:
        assert t.t_out >= t.t_in and np.isfinite(t.ret)


def test_prefix_equals_full_run():
    """Trades that closed before the cut must be identical when the future is removed."""
    df = B.add_indicators(synth(6000, 5))
    nf = (np.array([], dtype="int64"), np.array([]))
    p = B.Params(line="vw100", side="both")
    full, _ = B.run(df, nf, p)
    cut_ts = int(df["ts"].iloc[3500])
    part, _ = B.run(df, nf, p, t_to=cut_ts)
    done = [(t.t_in, t.t_out, t.p_in, t.p_out) for t in full if t.t_out < cut_ts]
    got = [(t.t_in, t.t_out, t.p_in, t.p_out) for t in part if t.t_out < cut_ts]
    assert done == got


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
            print("PASS", name)
