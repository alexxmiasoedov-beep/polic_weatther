"""Лаборатория по городам (11.10, запрос владельца: «проанализировать каждый город ещё раз,
перебрать все стратегии — время входа, цена и т.д., пока ничего не менять»).

Надстройка над strategy_lab: та же база снапшотов (observe_data + data + pilot_data),
та же механика (одна сделка на город-день на стратегию, вход по ask, победитель ≥97¢),
но шире сетка: 11 горизонтов (36…6 ч до конца местных суток), полосы цены лидера по 10¢,
фильтры к лидеру (импульс, стабильность, отрыв от второй корзины, согласие сырого
консенсуса моделей), «вторая корзина при дорогом лидере», «нижний сосед при дорогом лидере».

Честность: split-half по датам (≤21.09 / после), второй контроль (≤01.10 / после) и
walk-forward: отбор только на первой половине, проверка на второй. Пишет отчёт в
reports/<дата>-city-lab.md. Ничего в правилах/ТОП-10 не меняет.

python3 city_lab.py [--split 2026-09-21] [--split2 2026-10-01]
"""
import json, math, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import strategy_lab as L

ROOT = Path(__file__).parent
HORIZONS = [36, 30, 27, 24, 21, 18, 15, 12, 10, 8, 6]
BANDS = [(30, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 90)]


def leader(b):
    keys = [k for k in b if L.price(b, k) is not None]
    if not keys: return None, None
    ks = sorted(keys, key=lambda k: -L.price(b, k))
    return ks[0], (ks[1] if len(ks) > 1 else None)


def buy(b, k, w, max_ask):
    l, bid, a = b[k]
    if a is None or a > max_ask or a <= 0: return None
    return (k, a, L.pnl(k == w, a))


def t_lead_filtered(b, b_prev, w, kind, wx=None):
    lead, sec = leader(b)
    if not lead: return None
    p = L.price(b, lead); l, bid, a = b[lead]
    if p is None or a is None or bid is None or a - bid > 8: return None
    if kind == "mom":   # лидер вырос ≥5¢ за ~3 ч, цена 40-75
        if not b_prev or lead not in b_prev or not (40 <= p < 75): return None
        p0 = L.price(b_prev, lead)
        if p0 is None or p - p0 < 5: return None
    elif kind == "stab":  # лидер не двигался (|Δ|≤2) за ~3 ч, 40-75
        if not b_prev or lead not in b_prev or not (40 <= p < 75): return None
        p0 = L.price(b_prev, lead)
        if p0 is None or abs(p - p0) > 2: return None
    elif kind == "gap":  # отрыв от второй ≥25¢, лидер 40-70
        if not sec or not (40 <= p < 70): return None
        if p - (L.price(b, sec) or 0) < 25: return None
    elif kind == "cons":  # сырой консенсус моделей в корзине лидера, лидер 35-75
        if not wx or not (35 <= p < 75): return None
        vals = [wx[m] for m in L.MODELS if isinstance(wx.get(m), (int, float))]
        if len(vals) < 3: return None
        if L.bucket_for_temp(b, statistics.median(vals), "F") != lead: return None
    return buy(b, lead, w, 78)


def t_second_rich(b, w):
    lead, sec = leader(b)
    if not lead or not sec: return None
    p, ps = L.price(b, lead), L.price(b, sec)
    if not (70 <= p < 88) or ps is None or not (8 <= ps <= 28): return None
    return buy(b, sec, w, 32)


def t_down_rich(b, w):
    lead, _ = leader(b)
    if not lead: return None
    p = L.price(b, lead)
    if not (60 <= p < 88): return None
    order = L.ordered(b); i = order.index(lead)
    if i == 0: return None
    tgt = order[i - 1]; pt = L.price(b, tgt)
    if pt is None or not (8 <= pt <= 30): return None
    return buy(b, tgt, w, 34)


def stats(rows):
    n = len(rows)
    if not n: return dict(n=0, hit=0, pnl=0.0, per=0.0, t=0.0)
    hit = sum(1 for r in rows if r[2]); tot = sum(r[1] for r in rows)
    sd = statistics.pstdev([r[1] for r in rows]) if n > 1 else 0
    t = (tot / n) / (sd / math.sqrt(n)) if n > 1 and sd > 0 else 0
    return dict(n=n, hit=hit, pnl=round(tot, 2), per=round(tot / n * 100, 1), t=round(t, 2))


def run(split, split2):
    S = L.load(); trades = defaultdict(list); days = defaultdict(set)
    for (slug, d), v in S.items():
        snaps = v["snaps"]
        if len(snaps) < 3: continue
        w = L.winner_of(snaps)
        if not w: continue
        tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
        if hte[-1] > 6: continue
        days[slug].add(d)
        for H in HORIZONS:
            sa = L.snap_at(snaps, hte, H)
            if not sa: continue
            ts, b, h = sa
            wx = v["wx"].get(ts) or (max(((t, x) for t, x in v["wx"].items() if t <= ts), default=(None, None))[1])
            prev = [(t2, b2) for (t2, b2), h2 in zip(snaps, hte) if h + 2 <= h2 <= h + 4.5]
            bp = prev[-1][1] if prev else None
            c = {}
            for lo, hi in BANDS: c[f"lead:{lo}-{hi}"] = L.trade_leader(b, w, lo, hi)
            for kind in ("mom", "stab", "gap"): c[f"lead_{kind}"] = t_lead_filtered(b, bp, w, kind)
            if wx: c["lead_cons"] = t_lead_filtered(b, bp, w, "cons", wx); c["cons"] = L.trade_model(b, w, wx, "cons"); c["nbm"] = L.trade_model(b, w, wx, "nbm")
            c["second"] = L.trade_second(b, w); c["second_rich"] = t_second_rich(b, w)
            c["down"] = L.trade_adjacent(b, w, -1); c["down_rich"] = t_down_rich(b, w); c["up"] = L.trade_adjacent(b, w, +1)
            c["tail"] = L.trade_tail(b, w); c["lock:85-97"] = L.trade_leader(b, w, 85, 97, spread=6)
            for name, tr in c.items():
                if tr: trades[(f"{name}@{H}", slug)].append((d, tr[2], tr[2] > 0, tr[1]))
    combos = []
    for (name, slug), rows in trades.items():
        a = [r for r in rows if r[0] <= split]; o = [r for r in rows if r[0] > split]
        a2 = [r for r in rows if r[0] <= split2]; o2 = [r for r in rows if r[0] > split2]
        combos.append(dict(strategy=name, city=slug, all=stats(rows), ins=stats(a), oos=stats(o), ins2=stats(a2), oos2=stats(o2), days=len(days[slug])))
    return combos, trades, days


def robust(r):
    """оценка устойчивости: минимум из %/сд обеих половин при n≥4 в каждой, иначе −∞"""
    if r["ins"]["n"] < 4 or r["oos"]["n"] < 4: return -999
    return min(r["ins"]["per"], r["oos"]["per"])


def fmt(r):
    A, I, O, O2 = r["all"], r["ins"], r["oos"], r["oos2"]
    return (f"{r['strategy']:18s} n={A['n']:3d} {A['hit']:3d}✓ {A['per']:+6.1f}%/сд t={A['t']:+4.1f} | "
            f"≤21.09 n={I['n']:2d} {I['per']:+6.1f}% | >21.09 n={O['n']:2d} {O['per']:+6.1f}% | >01.10 n={O2['n']:2d} {O2['per']:+6.1f}%")


def main():
    a = sys.argv[1:]
    split = a[a.index("--split") + 1] if "--split" in a else "2026-09-21"
    split2 = a[a.index("--split2") + 1] if "--split2" in a else "2026-10-01"
    combos, trades, days = run(split, split2)
    top10 = {c["city"]: c["strategy"] for c in json.load(open(ROOT / "calibration" / "strategy_candidates.json"))["candidates"]}
    out = [f"# Лаборатория по городам — {datetime.now(timezone.utc).date()}", "",
           f"База: {len(days)} городов, {sum(len(v) for v in days.values())} закрытых рынков; стратегий×горизонтов на город ≈ {len(set(k[0] for k in trades))}.",
           f"Split-half: ≤{split} / после; второй контроль ≤{split2} / после. Вход по ask, одна сделка на город-день.", ""]
    # глобальная устойчивость стратегий (по имени, без горизонта и с горизонтом)
    byname = defaultdict(list)
    for (name, slug), rows in trades.items(): byname[name].extend(rows)
    out += ["## Стратегии глобально (все города), n≥40, обе половины ≥0", "", "| стратегия@H | n | попад. | %/сд | ≤21.09 | >21.09 | >01.10 |", "|---|---|---|---|---|---|---|"]
    g = []
    for name, rows in byname.items():
        A = stats(rows); I = stats([r for r in rows if r[0] <= split]); O = stats([r for r in rows if r[0] > split]); O2 = stats([r for r in rows if r[0] > split2])
        if A["n"] >= 40 and I["pnl"] >= 0 and O["pnl"] >= 0: g.append((min(I["per"], O["per"]), name, A, I, O, O2))
    for _, name, A, I, O, O2 in sorted(g, reverse=True)[:25]:
        out.append(f"| {name} | {A['n']} | {A['hit']} | {A['per']:+.1f} | {I['per']:+.1f} (n={I['n']}) | {O['per']:+.1f} (n={O['n']}) | {O2['per']:+.1f} (n={O2['n']}) |")
    # по городам
    out += ["", "## По городам — топ-5 устойчивых (n≥8, обе половины ≥4 сделок), плюс текущая пара ТОП-10 и механика фаворитов", ""]
    cities = sorted(days, key=lambda c: -len(days[c]))
    proposals = []
    for c in cities:
        rows = [r for r in combos if r["city"] == c and r["all"]["n"] >= 8]
        rows.sort(key=lambda r: (-robust(r), -r["all"]["t"]))
        out.append(f"### {c} — {len(days[c])} рынков" + (f" · в ТОП-10: {top10[c]}" if c in top10 else ""))
        out.append("```")
        for r in rows[:5]:
            out.append(fmt(r))
            if robust(r) >= 10 and r["all"]["t"] >= 1.5 and r["all"]["n"] >= 10: proposals.append(r)
        cur = [r for r in combos if r["city"] == c and r["strategy"] == top10.get(c)]
        if cur: out.append("ТОП-10 сейчас:  " + fmt(cur[0]))
        fav = [r for r in combos if r["city"] == c and r["strategy"] == "lead:50-60@30"] + [r for r in combos if r["city"] == c and r["strategy"] == "lead:60-70@30"]
        for r in fav: out.append("фавориты (≈): " + fmt(r))
        out.append("```")
    # предложение списка (не менять без владельца)
    out += ["", "## Кандидаты на замену в ТОП-10 (n≥10, обе половины ≥+10%/сд и ≥4 сделок, t≥1.5) — отсортированы по устойчивости", ""]
    seen = set(); out.append("```")
    for r in sorted(proposals, key=lambda r: -robust(r)):
        if r["city"] in seen: continue
        seen.add(r["city"]); out.append(f"{r['city']:15s} " + fmt(r) + ("   ← уже в ТОП-10" if top10.get(r["city"]) == r["strategy"] else ""))
    out.append("```")
    # walk-forward по этим критериям
    for sp, lab in ((split, "≤21.09"), (split2, "≤01.10")):
        key_i, key_o = ("ins", "oos") if sp == split else ("ins2", "oos2")
        wf = [r for r in combos if r[key_i]["n"] >= 5 and r[key_i]["per"] >= 10 and r[key_i]["t"] >= 1.5]
        # одна лучшая пара на город по ins
        best = {}
        for r in wf:
            if r["city"] not in best or r[key_i]["t"] > best[r["city"]][key_i]["t"]: best[r["city"]] = r
        n = sum(r[key_o]["n"] for r in best.values()); p = sum(r[key_o]["pnl"] for r in best.values()); h = sum(r[key_o]["hit"] for r in best.values())
        out += ["", f"Walk-forward: отбор лучшей пары на город только по датам {lab} → после: {len(best)} городов, n={n}, попаданий {h}, P&L {p:+.2f} ({(p/n*100 if n else 0):+.1f}%/сд)"]
    # текущие ТОП-10 пары целиком
    out += ["", "## Текущие пары ТОП-10 в этой сетке", "", "```"]
    for c, s in top10.items():
        r = [x for x in combos if x["city"] == c and x["strategy"] == s]
        out.append(f"{c:15s} " + (fmt(r[0]) if r else f"{s}: нет сделок в сетке"))
    out.append("```")
    txt = "\n".join(out)
    p = ROOT / "reports" / f"{datetime.now(timezone.utc).date()}-city-lab.md"
    p.write_text(txt, encoding="utf-8"); print(txt)


if __name__ == "__main__":
    main()
