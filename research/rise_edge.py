"""«Остаток прогрева» (16.11): модель финального максимума из ФАКТА станции.
Для города-дня в локальный час h известны max_sofar и t_now (часовые METAR без
SPECI). Эмпирическое распределение остатка Δ = final_max − max_sofar берётся из
ПРОШЛЫХ дней (walk-forward), сгруппировано по единице (°F/°C), часу h и признаку
«сейчас на максимуме» (t_now ≥ max_sofar − 0.5). Вероятность корзины =
доля Δ, попадающих в корзину. Сделка: p − ask/100 ≥ порог, ask ≤ 85, по ask+1.

python3 research/rise_edge.py [порог=0.15]
"""
import sys, json, glob, statistics, collections
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy_lab as L, deep_lab as D, config

THR = float(sys.argv[1]) if len(sys.argv) > 1 else 0.15
ROOT = Path(__file__).resolve().parent.parent
BAD = {"hong-kong", "nyc", "denver"}

def load_metar():
    M = collections.defaultdict(dict)
    for fn in glob.glob(str(ROOT / "metar_data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("temp_c") is None or r.get("speci"): continue
            M[r["station"]][r["obs_utc"]] = r["temp_c"]
    return {st: sorted((L.parse_ts(t), v) for t, v in d.items()) for st, d in M.items()}

S = L.load(); M = load_metar()
def bounds(label):
    n = D.num(label)
    if "below" in label: return (-1e9, n[0] + 0.5)
    if "higher" in label or "above" in label: return (n[0] - 0.5, 1e9)
    return (n[0] - 0.5, n[-1] + 0.5)

# 1. станционные дни: max_sofar/t_now по часам, финальный максимум
days = []  # dict(slug,d,unit,final,hours:{h:(max_sofar,t_now)})
for (slug, d), v in S.items():
    if slug in BAD: continue
    snaps = v["snaps"]
    if len(snaps) < 3: continue
    w = L.winner_of(snaps)
    if not w: continue
    tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    if hte[-1] > 14: continue
    st, unit = D.station_of(slug)
    ser = M.get(st, [])
    day0 = datetime.fromisoformat(d).replace(tzinfo=tz); day1 = day0 + timedelta(days=1)
    conv = (lambda c: c * 9 / 5 + 32) if unit == "F" else (lambda c: c)
    obs = [(t.astimezone(tz), conv(c)) for t, c in ser if day0 <= t.astimezone(tz) < day1]
    if len(obs) < 18: continue
    final = max(x for _, x in obs)
    wc = D.bucket_center(w)
    if wc is None or abs(wc - final) > 1.01: continue  # станция и победитель не сошлись — не учим на мусоре
    hours = {}
    for h in range(9, 18):
        cut = day0 + timedelta(hours=h)
        before = [x for t, x in obs if t <= cut]
        if before: hours[h] = (max(before), before[-1])
    days.append(dict(slug=slug, d=d, unit=unit, final=final, hours=hours, snaps=snaps, hte=hte, w=w, tz=tz))
days.sort(key=lambda x: x["d"])
print("городо-дней со станцией:", len(days), "городов", len(set(x["slug"] for x in days)))

# 2. walk-forward
trades = []; cal = collections.defaultdict(lambda: [0, 0]); nsig = 0
for i, D_ in enumerate(days):
    hist = [x for x in days if x["d"] < D_["d"] and x["unit"] == D_["unit"]]
    if len(hist) < 60: continue
    for h in range(11, 17):
        if h not in D_["hours"]: continue
        ms, tn = D_["hours"][h]; atmax = tn >= ms - 0.5
        # эмпирическое распределение остатка: по часу и признаку «на максимуме», своя станция ×3 вес
        deltas = []
        for x in hist:
            if h not in x["hours"]: continue
            xm, xt = x["hours"][h]
            if (xt >= xm - 0.5) != atmax: continue
            dd = x["final"] - xm
            deltas += [dd] * (3 if x["slug"] == D_["slug"] else 1)
        if len(deltas) < 40: continue
        # снапшот рынка в этот час (первый после h:00 местного)
        cut = datetime.fromisoformat(D_["d"]).replace(tzinfo=D_["tz"]) + timedelta(hours=h)
        cand = [(t, b) for t, b in D_["snaps"] if cut <= L.parse_ts(t) <= cut + timedelta(minutes=20)]
        if not cand: continue
        ts, b = cand[0]
        for key, (last, bid, ask) in b.items():
            lo, hi = bounds(key); p = sum(1 for dd in deltas if lo <= ms + dd < hi) / len(deltas)
            if ask is None or ask <= 0 or ask >= 100: continue
            cal[round(p * 10)][0] += 1; cal[round(p * 10)][1] += (key == D_["w"])
            edge = p - ask / 100
            if edge >= THR and ask <= 85:
                nsig += 1
                win = key == D_["w"]
                trades.append(dict(slug=D_["slug"], d=D_["d"], h=h, key=key, p=round(p, 2), ask=ask, edge=round(edge, 2), win=win, pnl=(100 / (ask + 1) - 1) if win else -1.0, unit=D_["unit"]))
print("\nКалибровка (p → доля побед):")
for k in sorted(cal):
    n, hh = cal[k]
    if n >= 20: print(f"  p≈{k/10:.1f} n={n:5d} факт {hh/n*100:5.1f}%")
def fmt(T, lab):
    n = len(T); hh = sum(t["win"] for t in T); p = sum(t["pnl"] for t in T)
    return f"{lab:26s} n={n:4d} hit={hh/max(n,1)*100:5.1f}% pnl={p:+8.2f} ({p/max(n,1)*100:+6.1f}%/сд)"
# одна сделка на город-день-корзину: берём первый час
seen = set(); T1 = []
for t in sorted(trades, key=lambda t: (t["d"], t["slug"], t["h"])):
    k = (t["slug"], t["d"], t["key"])
    if k in seen: continue
    seen.add(k); T1.append(t)
print(f"\nСделки edge≥{THR}, ask≤85, по ask+1 (первый час с сигналом на корзину):"); print(fmt(T1, "всего"))
byw = collections.defaultdict(list)
for t in T1: byw[datetime.fromisoformat(t["d"]).strftime("%G-W%V")].append(t)
for wk in sorted(byw): print("  " + fmt(byw[wk], wk))
print("по часу:")
for h in range(11, 17): print("  " + fmt([t for t in T1 if t["h"] == h], f"{h}:00"))
print("по ask:")
for a0, a1 in [(1, 10), (10, 25), (25, 45), (45, 65), (65, 86)]: print("  " + fmt([t for t in T1 if a0 <= t["ask"] < a1], f"ask {a0}-{a1}"))
print("по edge:")
for e0, e1 in [(0.15, 0.25), (0.25, 0.4), (0.4, 0.6), (0.6, 1.01)]: print("  " + fmt([t for t in T1 if e0 <= t["edge"] < e1], f"edge {e0}-{e1}"))
print("US / intl:"); print("  " + fmt([t for t in T1 if t["unit"] == "F"], "US")); print("  " + fmt([t for t in T1 if t["unit"] == "C"], "intl"))
bc = collections.defaultdict(list)
for t in T1: bc[t["slug"]].append(t)
print("по городам (n≥5):")
for s in sorted(bc, key=lambda s: -len(bc[s])):
    if len(bc[s]) >= 5: print("  " + fmt(bc[s], s))
json.dump(T1, open(ROOT / "research" / "rise_trades.json", "w"), ensure_ascii=False, indent=0)
