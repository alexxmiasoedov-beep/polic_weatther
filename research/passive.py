"""Пассивная покупка (маркет-мейкинг одной стороной), 16.11.
Вместо покупки по ask ставим бид = текущий лучший bid (или bid+1) и ждём, пока
кто-то продаст в нас. Прокси исполнения по снапшотам: в следующие W минут
появился last ≤ нашей цены (сделка прошла на нашем уровне или ниже).
Исход — удержание до резолва. Сравниваем с покупкой по ask в тот же момент.
python3 research/passive.py [W=60] [горизонт_lo=18] [горизонт_hi=36]
"""
import sys, statistics, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy_lab as L, deep_lab as D, config

W = int(sys.argv[1]) if len(sys.argv) > 1 else 60
HLO = int(sys.argv[2]) if len(sys.argv) > 2 else 18
HHI = int(sys.argv[3]) if len(sys.argv) > 3 else 36
S = L.load()
res = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0.0, 0.0]))  # группа → [ставок, исполнено, pnl_bid, pnl_ask]
def add(g, filled, win, bid, ask):
    r = res[g]; r["n"][0] += 1
    if filled:
        r["n"][1] += 1; r["n"][2] += (100 / bid - 1) if win else -1.0
    if ask: r["n"][3] += (100 / ask - 1) if win else -1.0
mk = 0
for (slug, d), v in S.items():
    snaps = v["snaps"]
    if len(snaps) < 3: continue
    w = L.winner_of(snaps)
    if not w: continue
    tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    if hte[-1] > 14: continue
    mk += 1
    T = [L.parse_ts(t) for t, _ in snaps]
    # одна ставка на рынок и корзину: первый снапшот в окне горизонтов
    done = set()
    for i, ((ts, b), h) in enumerate(zip(snaps, hte)):
        if not (HLO <= h < HHI): continue
        keys = [k for k in b if L.price(b, k) is not None]
        if not keys: continue
        lead = max(keys, key=lambda k: L.price(b, k))
        for k, (last, bid, ask) in b.items():
            if k in done or bid is None or bid < 3 or ask is None: continue
            done.add(k)
            my = bid  # присоединяемся к лучшему биду
            # исполнение: last ≤ my в снапшотах следующих W минут
            filled = any((b2.get(k) or (None,))[0] is not None and b2[k][0] <= my for (t2, b2) in snaps[i + 1:] if (L.parse_ts(t2) - T[i]).total_seconds() <= W * 60)
            win = (k == w)
            grp_price = "лидер" if k == lead else "не лидер"
            band = "bid<10" if bid < 10 else "10-30" if bid < 30 else "30-50" if bid < 50 else "50-70" if bid < 70 else "70+"
            add((grp_price, band), filled, win, my, ask)
            add((grp_price, "все"), filled, win, my, ask)
            add(("US" if slug in config.CITIES else "intl", grp_price), filled, win, my, ask)
print(f"рынков {mk}; окно исполнения {W} мин; горизонт {HLO}-{HHI} ч; одна ставка на рынок-корзину")
print(f"{'группа':28s} {'ставок':>7s} {'исполн.':>8s} {'доля':>6s} {'pnl@bid(исп.)':>14s} {'/сд':>7s} | {'pnl@ask(все)':>13s} {'/сд':>7s}")
for g in sorted(res):
    n, f, pb, pa = res[g]["n"]
    print(f"{str(g):28s} {n:7d} {f:8d} {f/max(n,1)*100:5.1f}% {pb:+14.2f} {pb/max(f,1)*100:+6.1f}% | {pa:+13.2f} {pa/max(n,1)*100:+6.1f}%")
