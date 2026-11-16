"""Модель против рынка (16.11). Вероятность каждой корзины из ансамбля моделей
(медиана ± разброс), с поправкой смещения и масштаба ошибки ПО ГОРОДУ,
оценённой только на ПРОШЛЫХ днях (walk-forward). Сделка — любая корзина,
где p_model − ask/100 ≥ порог, покупка по ask+1¢. Оценка по неделям.

python3 research/model_edge.py [H=20] [порог=0.10]
"""
import sys, math, json, statistics, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy_lab as L, deep_lab as D

H = int(sys.argv[1]) if len(sys.argv) > 1 else 20
THR = float(sys.argv[2]) if len(sys.argv) > 2 else 0.10
MODELS = L.MODELS
S = L.load()

def bounds(label):
    n = D.num(label)
    if "below" in label: return (-1e9, n[0] + 0.5)
    if "higher" in label or "above" in label: return (n[0] - 0.5, 1e9)
    return (n[0] - 0.5, n[-1] + 0.5)

def Phi(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))

rows = []
for (slug, d), v in S.items():
    snaps = v["snaps"]
    if len(snaps) < 3 or not v["wx"]: continue
    w = L.winner_of(snaps)
    if not w: continue
    tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    if hte[-1] > 14: continue
    sa = L.snap_at(snaps, hte, H)
    if not sa: continue
    ts, b, h = sa
    wx = v["wx"].get(ts) or max(((t, x) for t, x in v["wx"].items() if t <= ts), default=(None, None))[1]
    if not wx: continue
    m = [wx[k] for k in MODELS if isinstance(wx.get(k), (int, float))]
    if len(m) < 4: continue
    wc = D.bucket_center(w)
    if wc is None: continue
    rows.append(dict(slug=slug, d=d, ts=ts, b=b, w=w, wc=wc, med=statistics.median(m), mean=statistics.mean(m), sd=statistics.pstdev(m), n=len(m), unit=("F" if slug in __import__("config").CITIES else "C")))
rows.sort(key=lambda r: r["d"])
print(f"H={H} рынков с моделями: {len(rows)}, городов {len(set(r['slug'] for r in rows))}, дат {len(set(r['d'] for r in rows))}")

# walk-forward: для дня d параметры ошибки по городу из прошлых дней
by_city = collections.defaultdict(list)
for r in rows: by_city[r["slug"]].append(r)
trades = []; cal = collections.defaultdict(lambda: [0, 0])
for r in rows:
    hist = [x for x in by_city[r["slug"]] if x["d"] < r["d"]]
    errs = [x["wc"] - x["med"] for x in hist]
    pooled = [x["wc"] - x["med"] for x in rows if x["d"] < r["d"] and x["unit"] == r["unit"]]
    if len(errs) < 8 or len(pooled) < 40: continue
    bias = statistics.median(errs); mad = statistics.median(abs(e - bias) for e in errs) * 1.4826
    pb = statistics.median(pooled); pmad = statistics.median(abs(e - pb) for e in pooled) * 1.4826
    k = len(errs) / (len(errs) + 15)  # сжатие к общему
    bias = k * bias + (1 - k) * pb; sigma = max(k * mad + (1 - k) * pmad, 0.6 if r["unit"] == "C" else 1.0)
    sigma = math.sqrt(sigma ** 2 + (0.5 * r["sd"]) ** 2)  # разброс ансамбля добавляет неопределённости
    mu = r["med"] + bias
    for key, (last, bid, ask) in r["b"].items():
        lo, hi = bounds(key); p = Phi((hi - mu) / sigma) - Phi((lo - mu) / sigma)
        if ask is None or ask <= 0 or ask >= 100: continue
        cal[round(p * 10)][0] += 1; cal[round(p * 10)][1] += (key == r["w"])
        edge = p - ask / 100
        if edge >= THR and ask <= 85:
            win = key == r["w"]
            trades.append(dict(slug=r["slug"], d=r["d"], key=key, p=round(p, 3), ask=ask, edge=round(edge, 3), win=win, pnl=(100 / (ask + 1) - 1) if win else -1.0, lead=(max(r["b"], key=lambda k: L.price(r["b"], k) or 0) == key)))

print("\nКалибровка модели (p_model → доля побед):")
for k in sorted(cal):
    n, h = cal[k]
    if n >= 20: print(f"  p≈{k/10:.1f} n={n:5d} факт {h/n*100:5.1f}%")
def agg(T): n = len(T); h = sum(t["win"] for t in T); p = sum(t["pnl"] for t in T); return n, h, p
def fmt(T, lab):
    n, h, p = agg(T); return f"{lab:26s} n={n:4d} hit={h/max(n,1)*100:5.1f}% pnl={p:+8.2f} ({p/max(n,1)*100:+6.1f}%/сд)"
print(f"\nСделки edge≥{THR}, ask≤85, по ask+1:"); print(fmt(trades, "всего"))
byw = collections.defaultdict(list)
for t in trades: byw[__import__("datetime").datetime.fromisoformat(t["d"]).strftime("%G-W%V")].append(t)
for wk in sorted(byw): print("  " + fmt(byw[wk], wk))
print("по edge:")
for e0, e1 in [(0.10, 0.15), (0.15, 0.2), (0.2, 0.3), (0.3, 0.5), (0.5, 1)]:
    print("  " + fmt([t for t in trades if e0 <= t["edge"] < e1], f"edge {e0}-{e1}"))
print("по ask:")
for a0, a1 in [(1, 10), (10, 25), (25, 45), (45, 65), (65, 86)]:
    print("  " + fmt([t for t in trades if a0 <= t["ask"] < a1], f"ask {a0}-{a1}"))
print("лидер / не лидер:"); print("  " + fmt([t for t in trades if t["lead"]], "корзина = лидер")); print("  " + fmt([t for t in trades if not t["lead"]], "корзина ≠ лидер"))
print("US / intl:"); print("  " + fmt([t for t in trades if t["slug"] in __import__("config").CITIES], "US")); print("  " + fmt([t for t in trades if t["slug"] not in __import__("config").CITIES], "intl"))
bc = collections.defaultdict(list)
for t in trades: bc[t["slug"]].append(t)
print("по городам:")
for s in sorted(bc, key=lambda s: -len(bc[s])): print("  " + fmt(bc[s], s))
