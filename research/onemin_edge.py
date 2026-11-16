"""Минутные данные ASOS (IEM) против рынка PM, 7 городов США (16.11).
Идея: часовой METAR печатается в :53; минутные данные показывают температуру
за 5–15 минут ДО печати. Если текущая минутная температура уже лежит в корзине
выше корзины текущего часового максимума, следующая печать с большой
вероятностью перенесёт максимум туда — и мы знаем это раньше рынка.
Данные: iem1/<ST>.csv (tmpf по минутам, UTC). Часовая печать = минутное
значение в :53 (прокси METAR). Снапшоты PM — data/.
python3 research/onemin_edge.py [путь к iem1]
"""
import sys, csv, json, glob, collections, statistics
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy_lab as L, deep_lab as D, config

IEM = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/claude-0/-home-user-romanovdecor-site/79f142ae-660e-52f3-b282-1f6631a9cf2a/scratchpad/iem1")
ST = {"austin": "AUS", "atlanta": "ATL", "houston": "HOU", "los-angeles": "LAX", "miami": "MIA", "seattle": "SEA", "san-francisco": "SFO"}
one = {}
for slug, st in ST.items():
    rows = {}
    for r in csv.DictReader(open(IEM / f"{st}.csv")):
        try: rows[datetime.strptime(r["valid(UTC)"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)] = float(r["tmpf"])
        except Exception: continue
    one[slug] = rows
print({s: len(v) for s, v in one.items()})
S = L.load()
def bounds(label):
    n = D.num(label)
    if "below" in label: return (-1e9, n[0] + 0.5)
    if "higher" in label or "above" in label: return (n[0] - 0.5, 1e9)
    return (n[0] - 0.5, n[-1] + 0.5)
def bucket_of(b, t):
    for k in b:
        lo, hi = bounds(k)
        if lo <= t < hi: return k
    return None

trades = []; agree = [0, 0]
for (slug, d), v in S.items():
    if slug not in ST: continue
    snaps = v["snaps"]
    if len(snaps) < 3: continue
    w = L.winner_of(snaps)
    if not w: continue
    tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    if hte[-1] > 14: continue
    day0 = datetime.fromisoformat(d).replace(tzinfo=tz); day1 = day0 + timedelta(days=1)
    m1 = {t: x for t, x in one[slug].items() if day0 <= t.astimezone(tz) < day1}
    if len(m1) < 1000: continue
    # часовые печати (:53) и их дневной максимум — проверка против победителя
    prints = {t: x for t, x in m1.items() if t.minute == 53}
    if not prints: continue
    hmax = max(prints.values()); agree[0] += 1; agree[1] += (bucket_of(snaps[-1][1], hmax) == w)
    done = set()
    for (ts, b), h in zip(snaps, hte):
        t = L.parse_ts(ts); lt = t.astimezone(tz)
        if not (10 <= lt.hour < 17): continue
        # максимум часовых печатей к этому моменту и текущая минутная температура (последняя ≤ t)
        hp = [x for tt, x in prints.items() if tt <= t]
        if not hp: continue
        hm = max(hp); cur = [x for tt, x in m1.items() if t - timedelta(minutes=3) <= tt <= t]
        if not cur: continue
        c = cur[-1]
        bh, bc = bucket_of(b, hm), bucket_of(b, c)
        if not bh or not bc or bc == bh: continue
        o = L.ordered(b)
        if o.index(bc) <= o.index(bh): continue  # только выше часового максимума
        if bc in done: continue
        last, bid, ask = b[bc]
        if ask is None or ask <= 0 or ask > 85: continue
        done.add(bc)
        keys = [k for k in b if L.price(b, k) is not None]; lead = max(keys, key=lambda k: L.price(b, k))
        win = (bc == w)
        trades.append(dict(slug=slug, d=d, lh=round(lt.hour + lt.minute / 60, 2), minute=lt.minute, bucket=bc, hourly_bucket=bh, lead=lead, cur=c, hm=hm, ask=ask, win=win, pnl=(100 / (ask + 1) - 1) if win else -1.0, above_lead=(o.index(bc) > o.index(lead))))
print(f"совпадение победителя с максимумом часовых печатей (:53 из минутных): {agree[1]}/{agree[0]}")
def fmt(T, lab):
    n = len(T); hh = sum(t["win"] for t in T); p = sum(t["pnl"] for t in T)
    return f"{lab:30s} n={n:4d} hit={hh/max(n,1)*100:5.1f}% pnl={p:+8.2f} ({p/max(n,1)*100:+6.1f}%/сд)"
print("\nСигнал: минутная температура в корзине ВЫШЕ корзины часового максимума; покупка этой корзины по ask+1 (одна на рынок-корзину):")
print(fmt(trades, "всего"))
print(fmt([t for t in trades if t["above_lead"]], "  корзина выше лидера PM"))
print(fmt([t for t in trades if not t["above_lead"]], "  корзина не выше лидера"))
print("по минуте часа (до печати :53):")
for m0, m1_ in [(0, 20), (20, 40), (40, 53), (53, 60)]: print("  " + fmt([t for t in trades if m0 <= t["minute"] < m1_], f"минуты {m0}-{m1_}"))
print("по ask:")
for a0, a1 in [(1, 15), (15, 35), (35, 60), (60, 86)]: print("  " + fmt([t for t in trades if a0 <= t["ask"] < a1], f"ask {a0}-{a1}"))
print("по часу:")
for hh in range(10, 17): print("  " + fmt([t for t in trades if int(t["lh"]) == hh], f"{hh}:00"))
print("по запасу (минутная − граница корзины):")
for t in trades: t["margin"] = t["cur"] - bounds(t["bucket"])[0]
for g0, g1 in [(0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 9)]: print("  " + fmt([t for t in trades if g0 <= t["margin"] < g1], f"запас {g0}-{g1}°F"))
print("по неделям:")
byw = collections.defaultdict(list)
for t in trades: byw[datetime.fromisoformat(t["d"]).strftime("%G-W%V")].append(t)
for wk in sorted(byw): print("  " + fmt(byw[wk], wk))
print("по городам:")
bc_ = collections.defaultdict(list)
for t in trades: bc_[t["slug"]].append(t)
for s in sorted(bc_): print("  " + fmt(bc_[s], s))
json.dump(trades, open(Path(__file__).resolve().parent / "onemin_trades.json", "w"), ensure_ascii=False, indent=0)
