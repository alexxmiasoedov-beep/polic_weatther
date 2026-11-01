"""Провал лидера: тонкая сетка горизонтов (из сырых снапшотов)."""
import sys, json, statistics
sys.path.insert(0, '.')
import strategy_lab as L
from collections import defaultdict
S = L.load()
dates = sorted({d for (_, d) in S}); split = dates[len(dates)//2]
res = defaultdict(list)
for (slug, d), v in S.items():
    snaps = v["snaps"]
    if len(snaps) < 3: continue
    w = L.winner_of(snaps)
    if not w: continue
    tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    if hte[-1] > 6: continue
    for H in [26, 25, 23, 22, 20, 19, 18, 17, 16, 14, 13]:
        sa = L.snap_at(snaps, hte, H)
        if not sa: continue
        ts, b, h = sa
        keys = [k for k in b if L.price(b, k) is not None]
        if not keys: continue
        lead = max(keys, key=lambda k: L.price(b, k)); lp = L.price(b, lead); ask = b[lead][2]
        if not (40 <= lp < 70) or not ask or ask > 72: continue
        prev = [(t2, b2, h2) for (t2, b2), h2 in zip(snaps, hte) if h + 1.5 <= h2 <= h + 4.5 and lead in b2]
        if not prev: continue
        t2, b2, h2 = min(prev, key=lambda x: abs(x[2] - (h + 3))); p3 = L.price(b2, lead)
        if p3 is None: continue
        dlt = lp - p3; win = lead == w; pnl = (100/ask - 1) if win else -1.0
        res[(H, 'dip' if dlt <= -3 else ('flat' if dlt < 3 else 'rise'))].append((d, pnl, slug))
def fmt(v):
    n = len(v); s = sum(x[1] for x in v); h = sum(1 for x in v if x[1] > 0)
    return f"n={n:3d} hit={h/n*100:3.0f}% {s/n*100:+6.1f}%/сд" if n else "n=0"
for H in [26, 25, 23, 22, 20, 19, 18, 17, 16, 14, 13]:
    out = []
    for k in ['dip', 'flat', 'rise']:
        v = res[(H, k)]; a = [x for x in v if x[0] <= split]; b = [x for x in v if x[0] > split]
        out.append(f"{k}: {fmt(v)} [{fmt(a)} | {fmt(b)}]")
    print(f"H={H:2d}  " + "  ||  ".join(out))
# dip at 16-20 pooled: by city and by month
pool = [x for H in [20, 19, 18, 17, 16] for x in res[(H, 'dip')]]
bc = defaultdict(list); bm = defaultdict(list)
for d, p, s in pool: bc[s].append(p); bm[d[:7]].append(p)
print("\nDIP H16-20 по месяцам:", {m: (len(v), round(sum(v), 1)) for m, v in sorted(bm.items())})
print("DIP H16-20 по городам (n≥8):")
for s, v in sorted(bc.items(), key=lambda kv: -sum(kv[1])):
    if len(v) >= 8: print(f"  {s:14s} n={len(v):3d} hit={sum(1 for x in v if x>0)/len(v)*100:3.0f}% pnl={sum(v):+6.2f}")
