import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import bt_lib as B

for sym in ("BTCUSDT", "ETHUSDT"):
    for tf in ("1h", "15m"):
        d = B.add_indicators(B.load_klines(sym, tf))
        print(sym, tf, len(d), pd_start := None)
        for line in ("vw100", "sma50", "sma200"):
            a = d["ang_" + line].dropna().to_numpy()
            pct = np.percentile(a, [1, 5, 25, 50, 75, 95, 99])
            print(f"  {line:7s} pct1/5/25/50/75/95/99:", np.round(pct, 1),
                  f"|th|<=15: {np.mean(np.abs(a)<=15):.2f}  th>15: {np.mean(a>15):.2f}  th<-15: {np.mean(a<-15):.2f}  th>=65: {np.mean(a>=65):.3f}  th<=-65: {np.mean(a<=-65):.3f}")
