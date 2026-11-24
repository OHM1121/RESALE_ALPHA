"""Buljang scenario backtest library (research draft, not trading advice).

Spec: docs/artitrade/research/STRATEGY_BULJANG_2026-11-23.md section 3-A.
All signals at bar t use data up to and including bar t (causal). Entries are
filled at the close of the confirmation bar, exits are intrabar with the
stop assumed to trigger first when stop and target are both inside one bar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3] / "research_data" / "market" / "binance_um"
KST = 9 * 3600 * 1000


# ----------------------------------------------------------------- data
def load_klines(sym: str, tf: str) -> pd.DataFrame:
    parts = []
    cols = ["open_time", "open", "high", "low", "close", "volume"]
    for f in sorted((ROOT / sym / tf).glob("*.csv")):
        has_header = f.open().read(4) == "open"
        d = pd.read_csv(f, header=0 if has_header else None, usecols=range(6))
        d.columns = cols
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    t = df["open_time"].astype("int64").to_numpy()
    df["ts"] = np.where(t > 10**14, t // 1000, t)  # microseconds -> milliseconds
    df = df.drop(columns="open_time").drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype("float64")
    return df


def load_funding(sym: str) -> tuple[np.ndarray, np.ndarray]:
    parts = []
    for f in sorted((ROOT / sym / "funding").glob("*.csv")):
        parts.append(pd.read_csv(f))
    if not parts:
        return np.array([], dtype="int64"), np.array([], dtype="float64")
    d = pd.concat(parts).sort_values("calc_time")
    return d["calc_time"].astype("int64").to_numpy(), d["last_funding_rate"].astype("float64").to_numpy()


# ------------------------------------------------------------ indicators
def add_indicators(df: pd.DataFrame, W: int = 200, n: int = 10) -> pd.DataFrame:
    """Rolling-only (causal) indicators and the normalised angle of each line."""
    d = df.copy()
    c, v = d["close"], d["volume"]
    d["sma50"] = c.rolling(50).mean()
    d["sma200"] = c.rolling(200).mean()
    d["vw100"] = (c * v).rolling(100).sum() / v.rolling(100).sum()
    d["volma"] = v.rolling(20).mean()
    d["rng"] = d["high"].rolling(W).max() - d["low"].rolling(W).min()
    for line in ("sma50", "sma200", "vw100"):
        slope = (d[line] - d[line].shift(n)) / d["rng"] / (n / W)
        d["ang_" + line] = np.degrees(np.arctan(slope))
    d.attrs["W"], d.attrs["n"] = W, n
    return d


def cycle_regime(df_1h: pd.DataFrame) -> pd.Series:
    """+1 bull / -1 bear from BTC daily SMA50 vs SMA200 (causal). Index = ts of 1h bars."""
    d = df_1h.set_index(pd.to_datetime(df_1h["ts"], unit="ms"))
    day = d["close"].resample("1D").last().dropna()
    reg = np.sign(day.rolling(50).mean() - day.rolling(200).mean()).shift(1)  # known at the day open
    reg = reg.reindex(d.index, method="ffill")
    return pd.Series(reg.to_numpy(), index=df_1h["ts"].to_numpy())


# --------------------------------------------------------------- params
@dataclass
class Params:
    line: str = "vw100"
    side: str = "long"          # long | short | both
    W: int = 200
    n: int = 10
    flat_deg: float = 15.0      # |theta| <= flat_deg  -> "gentle" (user rule)
    crash_deg: float = 65.0     # >= -> steep (user rule)
    k: float = 0.5              # scenario 2 slope factor (proposal)
    plan_len: int = 24          # bars a frozen projection stays valid
    cooldown: int = 6
    vol_mult: float = 1.2       # reaction candle volume vs 20-bar mean
    wick_frac: float = 0.4      # lower wick share of the bar range
    tol: float = 0.003          # invalidation tolerance below the lowest line
    stop_pct: float = 0.01      # video example value
    tp_pct: float = 0.05        # video example value
    max_bars: int = 48          # time stop
    risk_frac: float = 0.01     # R, 1 percent of equity (video 0.5-2 percent)
    lev_cap: float = 5.0
    fee: float = 0.0005         # per side
    slip: float = 0.0002        # per side
    day_loss: float = 0.10      # user decision: video value
    month_loss: float = 0.30
    max_consec_stops: int = 3
    scen_set: tuple = (1, 2, 3)  # which projected lines may trigger an entry
    use_angle: bool = True       # False -> naive "touch the real line" baseline
    start_equity: float = 10000.0
    rand_prob: float = 0.0       # >0: null model, random entries with the same exits/costs
    seed: int = 0


@dataclass
class Trade:
    side: int
    t_in: int
    t_out: int
    p_in: float
    p_out: float
    scen: int
    reason: str
    ret: float          # net return on notional
    pnl_r: float        # net pnl in units of planned risk
    bars: int
    theta0: float = 0.0
    regime: float = 0.0


# ---------------------------------------------------------------- engine
def run(df: pd.DataFrame, fund: tuple[np.ndarray, np.ndarray], p: Params,
        regime: pd.Series | None = None, t_from: int | None = None, t_to: int | None = None):
    ts = df["ts"].to_numpy()
    o, h, l, c, v = (df[x].to_numpy() for x in ("open", "high", "low", "close", "volume"))
    X = df[p.line].to_numpy()
    th = df["ang_" + p.line].to_numpy()
    rng = df["rng"].to_numpy()
    volma = df["volma"].to_numpy()
    reg = regime.reindex(ts, method="ffill").to_numpy() if regime is not None else np.zeros(len(ts))
    ftime, frate = fund
    sides = {"long": (1,), "short": (-1,), "both": (1, -1)}[p.side]
    tan = math.tan
    W = p.W
    eq = p.start_equity
    trades: list[Trade] = []
    equity_curve = []
    pos = None
    plan = {1: None, -1: None}
    cool = {1: -1, -1: -1}
    day_key = None
    day_start_eq = eq
    day_stop = False
    consec = 0
    mon_key = None
    mon_start_eq = eq
    mon_stop = False
    rnd = np.random.default_rng(p.seed)
    N = len(ts)
    lo_i = 0 if t_from is None else int(np.searchsorted(ts, t_from))
    hi_i = N if t_to is None else int(np.searchsorted(ts, t_to))
    for i in range(max(lo_i, 1), hi_i):
        # ---- limits roll-over (KST day / month)
        k_day = (ts[i] + KST) // 86400000
        if k_day != day_key:
            day_key, day_start_eq, day_stop, consec = k_day, eq, False, 0
        k_mon = pd.Timestamp(ts[i] + KST, unit="ms").strftime("%Y-%m")
        if k_mon != mon_key:
            mon_key, mon_start_eq, mon_stop = k_mon, eq, False
        # ---- manage open position (intrabar, stop first)
        if pos is not None:
            s = pos["s"]
            stop, tp = pos["stop"], pos["tp"]
            exit_p, reason = None, None
            if s == 1:
                if o[i] <= stop:
                    exit_p, reason = o[i], "stop_gap"
                elif l[i] <= stop:
                    exit_p, reason = stop, "stop"
                elif h[i] >= tp:
                    exit_p, reason = tp, "tp"
            else:
                if o[i] >= stop:
                    exit_p, reason = o[i], "stop_gap"
                elif h[i] >= stop:
                    exit_p, reason = stop, "stop"
                elif l[i] <= tp:
                    exit_p, reason = tp, "tp"
            if exit_p is None and i - pos["i"] >= p.max_bars:
                exit_p, reason = c[i], "time"
            if exit_p is not None:
                gross = s * (exit_p - pos["p"]) / pos["p"]
                cost = 2 * (p.fee + p.slip)
                if ftime.size:
                    a, b = np.searchsorted(ftime, [pos["ts"], ts[i]], side="right")
                    fund_cost = s * float(frate[a:b].sum())
                else:
                    fund_cost = 0.0
                ret = gross - cost - fund_cost
                pnl = pos["notional"] * ret
                eq += pnl
                trades.append(Trade(s, pos["ts"], int(ts[i]), pos["p"], float(exit_p), pos["scen"], reason, ret,
                                    pnl / pos["risk_amt"], i - pos["i"], pos["th0"], pos["reg"]))
                consec = consec + 1 if reason.startswith("stop") else 0
                cool[s] = i + p.cooldown if reason.startswith("stop") else cool[s]
                pos = None
                if eq <= day_start_eq * (1 - p.day_loss) or consec >= p.max_consec_stops:
                    day_stop = True
                if eq <= mon_start_eq * (1 - p.month_loss):
                    mon_stop = True
        equity_curve.append(eq)
        if pos is not None or day_stop or mon_stop:
            continue
        if not (np.isfinite(X[i]) and np.isfinite(th[i]) and rng[i] > 0 and np.isfinite(volma[i])):
            continue
        # ---- null model: random side/time, identical exits and costs
        if p.rand_prob > 0:
            if rnd.random() < p.rand_prob:
                s = sides[int(rnd.integers(len(sides)))]
                pin = c[i] * (1 + s * p.slip)
                risk_amt = eq * p.risk_frac
                pos = {"s": s, "i": i, "ts": int(ts[i]), "p": pin, "stop": pin * (1 - s * p.stop_pct),
                       "tp": pin * (1 + s * p.tp_pct), "notional": min(risk_amt / p.stop_pct, eq * p.lev_cap),
                       "risk_amt": risk_amt, "scen": 0, "th0": 0.0, "reg": float(reg[i]) if np.isfinite(reg[i]) else 0.0}
            continue
        # ---- entries (signal on bar i close)
        for s in sides:
            if i < cool[s]:
                continue
            ths = s * th[i]                         # favourable-orientation angle now
            pl = plan[s]
            if p.use_angle:
                if ths <= p.flat_deg:               # only trend-with-us regimes trade (flat box is separate)
                    plan[s] = None
                    continue
                if pl is None or i - pl["i0"] >= p.plan_len:
                    pl = plan[s] = {"i0": i, "X0": X[i], "rng0": rng[i], "th0": ths}
                # frozen projected lines in favourable orientation
                thetas = {1: pl["th0"], 2: p.k * pl["th0"], 3: 0.0}
                inval_slope = tan(math.radians(-p.flat_deg))
                lines = {}
                for q in p.scen_set:
                    sl = s * tan(math.radians(thetas[q])) * pl["rng0"] / W
                    lines[q] = pl["X0"] + (i - pl["i0"]) * sl
                inval = pl["X0"] + (i - pl["i0"]) * s * inval_slope * pl["rng0"] / W
                if s * (c[i] - inval) < -p.tol * c[i]:
                    plan[s] = None
                    cool[s] = i + p.cooldown
                    continue
                cand = sorted(lines.items(), key=lambda kv: -s * kv[1])  # most favourable level first
            else:
                cand = [(0, X[i])]
                pl = {"th0": ths}
            bar = h[i] - l[i]
            if bar <= 0 or v[i] < p.vol_mult * volma[i]:
                continue
            for q, L in cand:
                touched = (l[i] <= L) if s == 1 else (h[i] >= L)
                held = (c[i] > L) if s == 1 else (c[i] < L)
                wick = ((c[i] - l[i]) if s == 1 else (h[i] - c[i])) / bar >= p.wick_frac
                came_from_fav = (c[i - 1] > L) if s == 1 else (c[i - 1] < L)
                if touched and held and wick and came_from_fav:
                    pin = c[i] * (1 + s * (p.slip))
                    risk_amt = eq * p.risk_frac
                    notional = min(risk_amt / p.stop_pct, eq * p.lev_cap)
                    pos = {"s": s, "i": i, "ts": int(ts[i]), "p": pin,
                           "stop": pin * (1 - s * p.stop_pct), "tp": pin * (1 + s * p.tp_pct),
                           "notional": notional, "risk_amt": risk_amt, "scen": q,
                           "th0": float(pl["th0"]), "reg": float(reg[i]) if np.isfinite(reg[i]) else 0.0}
                    plan[s] = None
                    break
            if pos is not None:
                break
    return trades, np.array(equity_curve)


# -------------------------------------------------------------- metrics
def summarize(trades: list[Trade], eq_curve: np.ndarray, start_eq: float) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t.ret for t in trades])
    pr = np.array([t.pnl_r for t in trades])
    wins = pr > 0
    gp, gl = pr[wins].sum(), -pr[~wins].sum()
    peak = np.maximum.accumulate(eq_curve) if eq_curve.size else np.array([start_eq])
    mdd = float(((eq_curve - peak) / peak).min()) if eq_curve.size else 0.0
    # longest losing streak
    streak = cur = 0
    for x in pr:
        cur = cur + 1 if x <= 0 else 0
        streak = max(streak, cur)
    return {"n": len(trades), "win": float(wins.mean()), "avg_r": float(pr.mean()), "pf": float(gp / gl) if gl > 0 else float("inf"),
            "total_ret": float(eq_curve[-1] / start_eq - 1) if eq_curve.size else 0.0, "mdd": mdd, "max_loss_streak": streak,
            "avg_bars": float(np.mean([t.bars for t in trades]))}


def by_key(trades: list[Trade], keyf) -> dict:
    out: dict = {}
    for t in trades:
        out.setdefault(keyf(t), []).append(t.pnl_r)
    return {k: {"n": len(v), "win": float(np.mean(np.array(v) > 0)), "avg_r": float(np.mean(v))} for k, v in sorted(out.items())}
