"""Количественные проверки под новую стратегию (16.11, поручение владельца):
 1. Арбитраж суммы корзин: Σask<100 (купить все YES) и Σbid>100 (купить все NO).
 2. Продажа хвостов: покупка NO на корзины с YES-bid ≤ порога — EV по децилям.
 3. Kalshi vs PM: распределение (победитель Kalshi − победитель PM) по центрам
    корзин для 7 городов США и цена Kalshi на «корзину выше PM-лидера».
python3 research/arb.py
"""
import sys, json, collections, statistics
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy_lab as L, deep_lab as D

S = L.load()
MK = {}
for (slug, d), v in S.items():
    snaps = v["snaps"]
    if len(snaps) < 3: continue
    w = L.winner_of(snaps)
    if not w: continue
    tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    if hte[-1] > 14: continue
    MK[(slug, d)] = dict(snaps=snaps, hte=hte, w=w)
print("рынков", len(MK))

# ---------------- 1. арбитраж суммы
print("\n## 1. Сумма корзин по снапшотам (все корзины с bid и ask)")
rows = []
for (slug, d), m in MK.items():
    for (ts, b), h in zip(m["snaps"], m["hte"]):
        if h < 1 or h > 48: continue
        vals = list(b.values())
        if any(x[2] is None for x in vals): continue
        A = sum(x[2] for x in vals); B = sum((x[1] or 0) for x in vals)
        rows.append((slug, d, h, A, B, len(vals)))
print("снапшотов", len(rows))
for lo, hi in [(24, 48), (12, 24), (6, 12), (1, 6)]:
    rr = [r for r in rows if lo <= r[2] < hi]
    if not rr: continue
    A = [r[3] for r in rr]; B = [r[4] for r in rr]
    print(f"  {lo:2d}-{hi:2d}ч n={len(rr):5d} Σask медиана {statistics.median(A):6.1f}  Σask<100: {sum(a<100 for a in A)/len(A)*100:4.1f}%  <98: {sum(a<98 for a in A)/len(A)*100:4.1f}%  <95: {sum(a<95 for a in A)/len(A)*100:4.1f}% | Σbid медиана {statistics.median(B):6.1f}  Σbid>100: {sum(b>100 for b in B)/len(B)*100:4.1f}%  >102: {sum(b>102 for b in B)/len(B)*100:4.1f}%")
# по городам: доля Σask<98
bc = collections.defaultdict(list)
for r in rows: bc[r[0]].append(r[3])
print("  города с Σask<98 чаще 10%:", {s: f"{sum(a<98 for a in v)/len(v)*100:.0f}% (n={len(v)}, мин {min(v):.0f})" for s, v in bc.items() if sum(a<98 for a in v)/len(v) > 0.10})
best = sorted(rows, key=lambda r: r[3])[:8]
print("  самые дешёвые Σask:", [(r[0], r[1], round(r[2]), round(r[3], 1)) for r in best])

# ---------------- 2. продажа хвостов (NO)
print("\n## 2. Покупка NO на хвосты (цена NO = 100 − YES bid; одна точка на рынок и горизонт)")
for lo, hi in [(24, 48), (12, 24), (6, 12)]:
    buckets = []
    for (slug, d), m in MK.items():
        c = [(b, h) for (t, b), h in zip(m["snaps"], m["hte"]) if lo <= h < hi]
        if not c: continue
        b, h = min(c, key=lambda x: abs(x[1] - (lo + hi) / 2))
        for k, (last, bid, ask) in b.items():
            if bid is None or bid <= 0: continue
            buckets.append((bid, k == m["w"], slug))
    print(f"  горизонт {lo}-{hi}ч, корзин с bid: {len(buckets)}")
    for b0, b1 in [(0.1, 1), (1, 2), (2, 3), (3, 5), (5, 8), (8, 12), (12, 20)]:
        rr = [x for x in buckets if b0 <= x[0] < b1]
        if len(rr) < 30: continue
        n = len(rr); lose = sum(1 for x in rr if x[1])  # YES выиграл → NO проиграл
        cost = statistics.mean(100 - x[0] for x in rr)
        pnl = sum(((100 / (100 - x[0]) - 1) if not x[1] else -1.0) for x in rr)
        print(f"    YES bid {b0:4.1f}-{b1:<4.1f} n={n:5d} NO цена {cost:5.1f}  YES выиграл {lose/n*100:5.2f}% (подразум. {100-cost:5.2f}%)  pnl NO {pnl:+8.2f} ({pnl/n*100:+5.2f}%/сд)")

# ---------------- 3. Kalshi vs PM
print("\n## 3. Kalshi vs PM (7 городов США): победители по центрам корзин")
K = D.load_kalshi()
diffs = collections.defaultdict(list); rowsk = []
for (slug, d), m in MK.items():
    kd = K.get((slug, d))
    if not kd: continue
    kts = sorted(kd)
    kb = kd[kts[-1]]
    kw = [k for k, (l, bid, a) in kb.items() if (l or 0) >= 97]
    if len(kw) != 1: continue
    pc, kc = D.bucket_center(m["w"]), D.bucket_center(kw[0])
    if pc is None or kc is None: continue
    diffs[slug].append(kc - pc)
    # цена Kalshi ~20ч до конца на корзине, соответствующей PM-лидеру и на следующей выше
    sa = L.snap_at(m["snaps"], m["hte"], 20)
    if not sa: continue
    ts, b, h = sa
    keys = [k for k in b if L.price(b, k) is not None]
    if not keys: continue
    lead = max(keys, key=lambda k: L.price(b, k)); lc = D.bucket_center(lead)
    cands = [(t, x) for t, x in kd.items() if abs((L.parse_ts(t) - L.parse_ts(ts)).total_seconds()) <= 5400]
    if not cands: continue
    t, kb2 = min(cands, key=lambda x: abs((L.parse_ts(x[0]) - L.parse_ts(ts)).total_seconds()))
    def kprice(center):
        for k, (l, bid, a) in kb2.items():
            if D.bucket_center(k) is not None and abs(D.bucket_center(k) - center) < 0.01: return l
        return None
    rowsk.append(dict(slug=slug, d=d, pm_lead=lead, lc=lc, pm_w=pc, k_w=kc, k_same=kprice(lc), k_up1=kprice(lc + 1), k_up2=kprice(lc + 2), k_down1=kprice(lc - 1), k_down2=kprice(lc - 2)))
for s, v in diffs.items():
    c = collections.Counter(v); print(f"  {s:14s} n={len(v):3d} Kalshi−PM центр: {dict(sorted(c.items()))}")
print("  (Kalshi резолвится по CLI/минутным данным, PM — по часовым METAR; границы Kalshi сдвинуты на 1°F)")
print("\n  Цена Kalshi за ~20ч на корзинах относительно центра PM-лидера и исход (центр победителя Kalshi − центр PM-лидера):")
for s in sorted(set(r["slug"] for r in rowsk)):
    rr = [r for r in rowsk if r["slug"] == s]
    out = collections.Counter(round(r["k_w"] - r["lc"]) for r in rr)
    def avg(key):
        v = [r[key] for r in rr if r[key] is not None]; return f"{statistics.mean(v):5.1f}" if v else "  -  "
    print(f"  {s:14s} n={len(rr):3d} исход K−PMлидер: {dict(sorted(out.items()))} | цена K: −2:{avg('k_down2')} −1:{avg('k_down1')} 0:{avg('k_same')} +1:{avg('k_up1')} +2:{avg('k_up2')}")
