"""Turn research_data/backtests/*.json into markdown tables (printed to stdout)."""
import json
from pathlib import Path

D = Path(__file__).resolve().parents[3] / "research_data" / "backtests"
R = json.loads((D / "results.json").read_text())
S = json.loads((D / "scenario_stats.json").read_text())
f = lambda x, d=2: "-" if x is None else f"{x:.{d}f}"
pct = lambda x: "-" if x is None else f"{100*x:.0f}%"


def row_main(name, b):
    ci = b["ci95"]
    return f"| {name} | {b['n']} | {pct(b['win'])} | {pct(b['tp_rate'])} | {f(b['avg_r'],3)} | {f(ci[0],3)} ~ {f(ci[1],3)} | {f(b['pf'])} |"


print("### B-1. 주 결과 (영상 값 고정, 6종목 합산, 롱·숏 모두)\n")
print("| 프레임 / 선 / 방식 | 거래 | 승률 | 익절률 | 평균 R | 95% 구간 | PF |\n|---|---|---|---|---|---|---|")
for k, b in R["primary"].items():
    tf, line, mode = k.split("|")
    print(row_main(f"{tf} / {line} / {'각도 시나리오' if mode=='angle' else '단순 터치(비교군)'}", b))

print("\n### B-2. 기간별 (VWMA100)\n")
print("| 프레임 / 방식 / 기간 | 거래 | 승률 | 평균 R | 95% 구간 |\n|---|---|---|---|---|")
for k, b in R["periods"].items():
    if b["n"]:
        print(f"| {k.replace('|vw100|',' / ').replace('|',' / ')} | {b['n']} | {pct(b['win'])} | {f(b['avg_r'],3)} | {f(b['ci95'][0],3)} ~ {f(b['ci95'][1],3)} |")

for key in ("1h|vw100|angle", "15m|vw100|angle"):
    b = R["primary"][key]
    print(f"\n### B-3. 분해 ({key})\n")
    print("| 구분 | 값 | 거래 | 승률 | 평균 R |\n|---|---|---|---|---|")
    for grp in ("by_side", "by_regime", "by_side_regime", "by_scen", "by_year"):
        for kk, v in b[grp].items():
            print(f"| {grp} | {kk} | {v['n']} | {pct(v['win'])} | {f(v['avg_r'],3)} |")

print("\n### B-4. 민감도 (1h, VWMA100; 탐색용, 선택하지 않음)\n")
print("| 변형 | 거래 | 승률 | 익절률 | 평균 R | 95% 구간 |\n|---|---|---|---|---|---|")
for k, b in R["sens"].items():
    if b["n"]:
        print(f"| {k} | {b['n']} | {pct(b['win'])} | {pct(b['tp_rate'])} | {f(b['avg_r'],3)} | {f(b['ci95'][0],3)} ~ {f(b['ci95'][1],3)} |")

print("\n### B-5. 무작위 진입 대조 (같은 청산·비용)\n")
print("| 키 | 시뮬 | 무작위 평균 R | 5~95% | 실제 | 무작위가 실제 이상인 비율 |\n|---|---|---|---|---|---|")
for k, b in R["null"].items():
    print(f"| {k.replace(chr(124), chr(32)+chr(47)+chr(32))} | {b['sims']} | {f(b['mean_avg_r'],3)} | {f(b['p5'],3)} ~ {f(b['p95'],3)} | {f(b['actual_avg_r'],3)} | {pct(b['share_of_null_ge_actual'])} |")

print("\n### A. 시나리오 실현 빈도 (현재 각도 > 15°인 상승·거울 하락 합산, 겹치지 않는 창)\n")
print("실현 구간 = 이후 H봉 동안 선이 실제로 움직인 각도가 ①~⑤ 중 어디에 속했는지. 가장 가까운 투영 = 5개 투영선 중 실제 선에 가장 가까웠던 것.\n")
print("| 프레임 / 선 / H | 표본 | ① | ② | ③ | ④ | ⑤ | 가장 가까운 투영 ①②③④⑤ |\n|---|---|---|---|---|---|---|---|")
for k, v in S.items():
    a = v["all"]
    if a:
        rb = a["realised_bucket"]; cp = a["closest_proj"]
        print(f"| {k.replace('_',' / ')} | {a['n']} | " + " | ".join(pct(x) for x in rb) + " | " + " / ".join(pct(x) for x in cp) + " |")
print("\n상승장·하락장 구분 (BTC 일봉 50/200선 정배열=상승 사이클, 역배열=하락 사이클) — 1h VWMA100, H=24\n")
print("| 구분 | 표본 | ① | ② | ③ | ④ | ⑤ |\n|---|---|---|---|---|---|---|")
for g in ("up_bull", "up_bear", "down_bull", "down_bear", "th0_15_30", "th0_30_45", "th0_45_90"):
    a = S["1h_vw100_H24"][g]
    if a:
        print(f"| {g} | {a['n']} | " + " | ".join(pct(x) for x in a["realised_bucket"]) + " |")
print("\n상승장·하락장 구분 — 1h VWMA100, H=96\n")
print("| 구분 | 표본 | ① | ② | ③ | ④ | ⑤ |\n|---|---|---|---|---|---|---|")
for g in ("up_bull", "up_bear", "down_bull", "down_bear"):
    a = S["1h_vw100_H96"][g]
    if a:
        print(f"| {g} | {a['n']} | " + " | ".join(pct(x) for x in a["realised_bucket"]) + " |")
