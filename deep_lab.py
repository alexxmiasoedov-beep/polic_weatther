"""Глубокая лаборатория (01.11, поручение владельца): единый датасет по всем
городам и рынкам — цены PM по горизонтам, Kalshi (семёрка), модели, станция
(METAR, с 06.10) и темп прогрева, межгородские связи. Шаг 1 — build():
плоские строки (город, дата, горизонт) → research/rows.jsonl. Шаг 2 —
анализы (см. report()).

python3 deep_lab.py --build      # собрать датасет (несколько минут)
python3 deep_lab.py --report     # анализы → stdout и reports/2026-11-01-deep-lab.md
"""
import glob, json, re, sys, math, statistics, gzip
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import strategy_lab as L
import config, pilot

ROOT = Path(__file__).parent
OUT = ROOT / "research"; OUT.mkdir(exist_ok=True)
H_LIST = [36, 30, 27, 24, 21, 18, 15, 12, 10, 8, 6, 4, 3]

def station_of(slug):
    for src in (config.CITIES, pilot.PILOT_CITIES):
        if slug in src: return src[slug].get("station"), src[slug].get("unit") or ("F" if slug in config.CITIES else "C")
    o = json.load(open(ROOT / "observe_stations.json"))["cities"]
    if slug in o: return o[slug]["station"], o[slug]["unit"]
    return None, "C"

def load_kalshi():
    K = defaultdict(dict)
    for fn in glob.glob(str(ROOT / "data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            try: s = json.loads(line)
            except Exception: continue
            d = s.get("market_date") or Path(fn).stem
            for slug, c in s.get("cities", {}).items():
                kb = (c.get("kalshi") or {}).get("buckets")
                if kb: K[(slug, d)][s["ts_utc"]] = {b["bucket"]: (b.get("last"), b.get("bid"), b.get("ask")) for b in kb}
    return K

def load_metar():
    """{station: sorted [(obs_utc datetime, temp_c)]}"""
    M = defaultdict(dict)
    for fn in glob.glob(str(ROOT / "metar_data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("temp_c") is None: continue
            M[r["station"]][r["obs_utc"]] = r["temp_c"]
    return {st: sorted((L.parse_ts(t), v) for t, v in d.items()) for st, d in M.items()}

def num(label):
    m = re.findall(r"(?<!\d)-?\d+", label)
    return [int(x) for x in m]

def bucket_center(label):
    n = num(label)
    if not n: return None
    if "below" in label: return n[0] - 0.5
    if "higher" in label or "above" in label: return n[0] + 0.5
    return (n[0] + n[-1]) / 2

def rel_index(order, lead, w):
    """позиция победителя относительно лидера: 0 = лидер, −1 ниже, +1 выше (по порядку корзин)"""
    if w not in order or lead not in order: return None
    return order.index(w) - order.index(lead)

def build():
    S = L.load(); K = load_kalshi(); M = load_metar()
    rows = []; days = defaultdict(dict)
    for (slug, d), v in S.items():
        snaps = v["snaps"]
        if len(snaps) < 3: continue
        w = L.winner_of(snaps)
        if not w: continue
        tz = L.tz_of(slug)
        hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
        if hte[-1] > 6: continue
        st, unit = station_of(slug)
        ser = M.get(st, [])
        # локальные сутки d: от 00:00 до 24:00 local
        day0 = datetime.fromisoformat(d).replace(tzinfo=tz); day1 = day0 + timedelta(days=1)
        ser_day = [(t, c) for t, c in ser if day0 <= t.astimezone(tz) < day1]
        conv = (lambda c: c * 9 / 5 + 32) if unit == "F" else (lambda c: c)
        day_max = max((conv(c) for _, c in ser_day), default=None)
        # вчера
        y0 = day0 - timedelta(days=1)
        ser_y = [(t, c) for t, c in ser if y0 <= t.astimezone(tz) < day0]
        y_max = max((conv(c) for _, c in ser_y), default=None)
        order = L.ordered(snaps[-1][1])
        for H in H_LIST:
            sa = L.snap_at(snaps, hte, H)
            if not sa: continue
            ts, b, h = sa
            keys = [k for k in b if L.price(b, k) is not None]
            if not keys: continue
            lead = max(keys, key=lambda k: L.price(b, k))
            lp = L.price(b, lead); lbid, lask = b[lead][1], b[lead][2]
            # цена лидера 3 ч и 6 ч назад
            def price_at(hh, key):
                c = [(t2, b2, h2) for (t2, b2), h2 in zip(snaps, hte) if hh - 1.5 <= h2 <= hh + 1.5 and key in b2]
                if not c: return None
                t2, b2, h2 = min(c, key=lambda x: abs(x[2] - hh)); return L.price(b2, key)
            lp3 = price_at(h + 3, lead); lp6 = price_at(h + 6, lead)
            # лидер 6 ч назад (другой?)
            c6 = [(t2, b2, h2) for (t2, b2), h2 in zip(snaps, hte) if h + 4.5 <= h2 <= h + 7.5]
            lead6 = None; lead6_p = None; lead6_ask = None
            if c6:
                t2, b2, h2 = min(c6, key=lambda x: abs(x[2] - (h + 6)))
                k6 = [k for k in b2 if L.price(b2, k) is not None]
                if k6:
                    lead6 = max(k6, key=lambda k: L.price(b2, k))
                    if lead6 in b: lead6_p = L.price(b, lead6); lead6_ask = b[lead6][2]
            o = [k for k in order if k in b]
            i = o.index(lead) if lead in o else None
            below = o[i - 1] if i is not None and i > 0 else None
            above = o[i + 1] if i is not None and i + 1 < len(o) else None
            def pa(k): return (L.price(b, k), b[k][1], b[k][2]) if k and k in b else (None, None, None)
            # Kalshi в тот же момент (ближайший снапшот ≤ 1.5 ч)
            kal = None; kal_lead = None; kal_lead_p = None; kal_same = None
            kd = K.get((slug, d))
            if kd:
                cands = [(t, kb) for t, kb in kd.items() if abs((L.parse_ts(t) - L.parse_ts(ts)).total_seconds()) <= 5400]
                if cands:
                    t, kb = min(cands, key=lambda x: abs((L.parse_ts(x[0]) - L.parse_ts(ts)).total_seconds()))
                    kk = [k for k in kb if L.price(kb, k) is not None]
                    if kk:
                        kal_lead = max(kk, key=lambda k: L.price(kb, k)); kal_lead_p = L.price(kb, kal_lead)
                        # центр корзины Kalshi-лидера vs центр PM-лидера
                        kc, pc = bucket_center(kal_lead), bucket_center(lead)
                        kal_same = None if kc is None or pc is None else (kc - pc)
            # модели
            wx = v["wx"].get(ts) or (max(((t, x) for t, x in v["wx"].items() if t <= ts), default=(None, None))[1]) or {}
            mvals = [wx[m] for m in L.MODELS if isinstance(wx.get(m), (int, float))]
            cons = statistics.median(mvals) if len(mvals) >= 3 else None
            nbm = wx.get("om_ncep_nbm_conus") if isinstance(wx.get("om_ncep_nbm_conus"), (int, float)) else None
            # станция в момент снапшота
            t_now = None; t_max_sofar = None; rate2 = None; t_prev2 = None
            tsd = L.parse_ts(ts)
            before = [(t, conv(c)) for t, c in ser_day if t <= tsd]
            if before:
                t_now = before[-1][1]; t_max_sofar = max(x[1] for x in before)
                p2 = [x for x in before if x[0] <= tsd - timedelta(hours=1.5)]
                if p2: t_prev2 = p2[-1][1]; rate2 = (t_now - t_prev2) / max((tsd - p2[-1][0]).total_seconds() / 3600, 0.5)
            lc, wc = bucket_center(lead), bucket_center(w)
            rows.append(dict(slug=slug, d=d, H=H, hte=round(h, 2), ts=ts, lead=lead, lp=lp, lbid=lbid, lask=lask,
                             lp3=lp3, lp6=lp6, lead6_same=(lead6 == lead) if lead6 else None, lead6=lead6, lead6_p=lead6_p, lead6_ask=lead6_ask, lead6_win=(lead6 == w) if lead6 else None,
                             below=below, bp=pa(below)[0], bask=pa(below)[2], above=above, ap=pa(above)[0], aask=pa(above)[2],
                             w=w, rel=rel_index(o, lead, w), win=(lead == w),
                             kal_lead=kal_lead, kal_lead_p=kal_lead_p, kal_diff=kal_same,
                             cons=cons, nbm=nbm, lc=lc, wc=wc,
                             t_now=t_now, t_max=t_max_sofar, rate2=rate2, day_max=day_max, y_max=y_max,
                             unit=unit, nb=len(o)))
            if H in (24, 12):
                days[d][(slug, H)] = rel_index(o, lead, w)
    with gzip.open(OUT / "rows.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    json.dump({d: {f"{s}@{H}": v for (s, H), v in dd.items()} for d, dd in days.items()}, open(OUT / "days.json", "w"), ensure_ascii=False)
    print("rows", len(rows), "days", len(days))

if __name__ == "__main__":
    if "--build" in sys.argv: build()


# ---------------------------------------------------------------- анализы
def load_rows():
    with gzip.open(OUT / "rows.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(l) for l in f]

def pnl_buy(ask, win): return (100 / ask - 1) if win else -1.0

def agg(rows, key_fn, val_fn, min_n=1):
    g = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is None: continue
        v = val_fn(r)
        if v is None: continue
        g[k].append(v)
    return {k: v for k, v in g.items() if len(v) >= min_n}

def fmt_pnl(vals):
    n = len(vals); s = sum(vals); h = sum(1 for v in vals if v > 0)
    return f"n={n:3d} hit={h/n*100:3.0f}% pnl={s:+7.2f} ({s/n*100:+5.1f}%/сд)"

def pbin(p):
    if p is None: return None
    for lo, hi in [(20,30),(30,40),(40,50),(50,60),(60,70),(70,80),(80,90),(90,97)]:
        if lo <= p < hi: return f"{lo}-{hi}"
    return None

def section(lines, title): lines.append(f"\n## {title}")

def report():
    R = load_rows(); lines = ["# Глубокая лаборатория — отчёт 01.11.2026", f"строк {len(R)}, городов {len({r['slug'] for r in R})}, дат {len({r['d'] for r in R})}"]
    dates = sorted({r['d'] for r in R}); split = dates[len(dates)//2]
    lines.append(f"даты {dates[0]}…{dates[-1]}, медиана для walk-forward: {split}")

    # 1. калибровка лидера по горизонту и цене
    section(lines, "1. Лидер PM: попадания против цены по горизонтам (все города)")
    lines.append("edge = доля побед − средняя цена ask (в п.п.); плюс = лидер недооценён")
    for H in H_LIST:
        rr = [r for r in R if r['H'] == H and r['lask']]
        parts = []
        for pb in ["30-40","40-50","50-60","60-70","70-80","80-90","90-97"]:
            g = [r for r in rr if pbin(r['lp']) == pb]
            if len(g) < 25: continue
            wr = sum(r['win'] for r in g) / len(g) * 100; ask = statistics.mean(r['lask'] for r in g)
            parts.append(f"{pb}: n={len(g)} win={wr:.0f}% ask={ask:.0f} edge={wr-ask:+.0f}")
        lines.append(f"H={H:2d}: " + " | ".join(parts))
    # распределение исхода относительно лидера
    section(lines, "1б. Куда уходит победитель относительно лидера (лидер 40-70¢)")
    for H in [30, 24, 18, 12, 8, 6]:
        g = [r for r in R if r['H'] == H and r['lp'] and 40 <= r['lp'] < 70 and r['rel'] is not None]
        if not g: continue
        c = defaultdict(int)
        for r in g: c[max(-2, min(2, r['rel']))] += 1
        n = len(g); lines.append(f"H={H:2d} n={n}: ≤−2 {c[-2]/n*100:.0f}% | −1 {c[-1]/n*100:.0f}% | лидер {c[0]/n*100:.0f}% | +1 {c[1]/n*100:.0f}% | ≥+2 {c[2]/n*100:.0f}%")
    # по городам: смещение исхода (н/в) при лидере 40-70 на H=24 и 12
    section(lines, "1в. Смещение по городам (лидер 40-70¢): доля «ниже» / «лидер» / «выше», обе половины дат")
    for H in [24, 12]:
        lines.append(f"H={H}")
        bycity = defaultdict(list)
        for r in R:
            if r['H'] == H and r['lp'] and 40 <= r['lp'] < 70 and r['rel'] is not None: bycity[r['slug']].append(r)
        out = []
        for s, g in bycity.items():
            if len(g) < 12: continue
            def frac(gg, cond): return sum(1 for r in gg if cond(r['rel'])) / len(gg) * 100 if gg else float('nan')
            a = [r for r in g if r['d'] <= split]; b = [r for r in g if r['d'] > split]
            out.append((s, len(g), frac(g, lambda x: x < 0), frac(g, lambda x: x == 0), frac(g, lambda x: x > 0), frac(a, lambda x: x < 0), frac(b, lambda x: x < 0), frac(a, lambda x: x > 0), frac(b, lambda x: x > 0)))
        out.sort(key=lambda t: -(t[2] - t[4]))
        for s, n, lo, mid, hi, lo1, lo2, hi1, hi2 in out:
            lines.append(f"  {s:14s} n={n:3d} ниже {lo:3.0f}% лидер {mid:3.0f}% выше {hi:3.0f}% | ниже по половинам {lo1:3.0f}/{lo2:3.0f} | выше {hi1:3.0f}/{hi2:3.0f}")

    # 2. моментум цены лидера
    section(lines, "2. Динамика цены лидера за 3 ч (лидер 40-70¢): попадание по ask")
    for H in [24, 18, 12, 8, 6]:
        g = [r for r in R if r['H'] == H and r['lp'] and 40 <= r['lp'] < 70 and r['lp3'] is not None and r['lask']]
        parts = []
        for name, cond in [("падал ≤−8", lambda x: x <= -8), ("−8..−3", lambda x: -8 < x <= -3), ("ровно", lambda x: -3 < x < 3), ("+3..+8", lambda x: 3 <= x < 8), ("рос ≥+8", lambda x: x >= 8)]:
            gg = [r for r in g if cond(r['lp'] - r['lp3'])]
            if len(gg) < 15: continue
            v = [pnl_buy(r['lask'], r['win']) for r in gg]
            parts.append(f"{name}: {fmt_pnl(v)}")
        lines.append(f"H={H:2d}\n   " + "\n   ".join(parts))
    section(lines, "2б. Лидер не менялся 6 ч vs сменился (лидер 40-70¢), покупка по ask")
    for H in [24, 18, 12, 8]:
        g = [r for r in R if r['H'] == H and r['lp'] and 40 <= r['lp'] < 70 and r['lead6_same'] is not None and r['lask']]
        a = [pnl_buy(r['lask'], r['win']) for r in g if r['lead6_same']]; b = [pnl_buy(r['lask'], r['win']) for r in g if not r['lead6_same']]
        if a and b: lines.append(f"H={H:2d} стабилен: {fmt_pnl(a)} | сменился: {fmt_pnl(b)}")

    # 3. Kalshi (семёрка США)
    section(lines, "3. Kalshi против PM (7 городов США)")
    for H in [24, 18, 12, 8, 6, 4]:
        g = [r for r in R if r['H'] == H and r['kal_lead'] and r['kal_diff'] is not None and r['lp'] and r['lp'] < 85]
        if len(g) < 20: continue
        same = [r for r in g if abs(r['kal_diff']) < 1.5]; up = [r for r in g if r['kal_diff'] >= 1.5]; dn = [r for r in g if r['kal_diff'] <= -1.5]
        def wr(gg): return f"n={len(gg)} PM-лидер побеждает {sum(r['win'] for r in gg)/len(gg)*100:.0f}%" if gg else "n=0"
        lines.append(f"H={H:2d}: Kalshi согласен → {wr(same)} | Kalshi выше → {wr(up)}, выше побеждает {sum(1 for r in up if r['rel']==1)/len(up)*100 if up else 0:.0f}% | Kalshi ниже → {wr(dn)}, ниже побеждает {sum(1 for r in dn if r['rel']==-1)/len(dn)*100 if dn else 0:.0f}%")
        # покупка корзины Kalshi-лидера на PM, если она соседняя и стоит 15-60
        trades = []
        for r in up + dn:
            k = 'above' if r['kal_diff'] >= 1.5 else 'below'
            ask = r['aask'] if k == 'above' else r['bask']; p = r['ap'] if k == 'above' else r['bp']
            if ask and p and 15 <= p <= 60 and ask <= 65:
                win = (r['rel'] == 1) if k == 'above' else (r['rel'] == -1)
                trades.append((r['d'], r['slug'], pnl_buy(ask, win), r['kal_lead_p']))
        if len(trades) >= 10:
            v = [t[2] for t in trades]; strong = [t[2] for t in trades if t[3] and t[3] >= 65]
            lines.append(f"      → купить на PM корзину Kalshi-лидера (15-60¢): {fmt_pnl(v)}" + (f" | Kalshi ≥65¢: {fmt_pnl(strong)}" if len(strong) >= 8 else ""))
    section(lines, "3б. Kalshi против PM по городам (все горизонты ≤12, покупка корзины Kalshi-лидера 15-60¢)")
    bc = defaultdict(list)
    for r in R:
        if r['H'] > 12 or not r['kal_lead'] or r['kal_diff'] is None or not r['lp'] or r['lp'] >= 85: continue
        if abs(r['kal_diff']) < 1.5: continue
        k = 'above' if r['kal_diff'] >= 1.5 else 'below'
        ask = r['aask'] if k == 'above' else r['bask']; p = r['ap'] if k == 'above' else r['bp']
        if ask and p and 15 <= p <= 60 and ask <= 65:
            win = (r['rel'] == 1) if k == 'above' else (r['rel'] == -1)
            bc[r['slug']].append(pnl_buy(ask, win))
    for s, v in sorted(bc.items(), key=lambda kv: -sum(kv[1])):
        if len(v) >= 8: lines.append(f"  {s:14s} {fmt_pnl(v)}")

    # 4. станция
    section(lines, "4. Станция: максимум к моменту среза относительно лидера (дневные горизонты)")
    lines.append("dmax = t_max_sofar − центр корзины лидера (°F для США, °C иначе; ширина корзины 2°F / 1°C)")
    for H in [12, 10, 8, 6, 4, 3]:
        g = [r for r in R if r['H'] == H and r['t_max'] is not None and r['lc'] is not None and r['lp'] and r['lp'] < 90 and r['rel'] is not None]
        if len(g) < 20: continue
        parts = []
        for name, cond in [("макс уже выше корзины", lambda r: r['t_max'] - r['lc'] > (1.0 if r['unit']=='F' else 0.5)), ("макс в корзине", lambda r: abs(r['t_max'] - r['lc']) <= (1.0 if r['unit']=='F' else 0.5)), ("макс ниже на 1 корзину", lambda r: -(3.0 if r['unit']=='F' else 1.5) <= r['t_max'] - r['lc'] < -(1.0 if r['unit']=='F' else 0.5)), ("макс ниже ≥2 корзин", lambda r: r['t_max'] - r['lc'] < -(3.0 if r['unit']=='F' else 1.5))]:
            gg = [r for r in g if cond(r)]
            if len(gg) < 10: continue
            n = len(gg); parts.append(f"{name}: n={n} лидер {sum(r['win'] for r in gg)/n*100:.0f}% выше {sum(1 for r in gg if r['rel']>0)/n*100:.0f}% ниже {sum(1 for r in gg if r['rel']<0)/n*100:.0f}% (ср. цена лидера {statistics.mean(r['lp'] for r in gg):.0f})")
        lines.append(f"H={H:2d}\n   " + "\n   ".join(parts))
    section(lines, "4б. Темп прогрева за ~2 ч перед срезом при «макс ниже на 1 корзину» (лидер 40-85¢): кто побеждает")
    for H in [10, 8, 6]:
        g = [r for r in R if r['H'] == H and r['t_max'] is not None and r['lc'] is not None and r['rate2'] is not None and r['lp'] and 40 <= r['lp'] < 85 and r['rel'] is not None
             and -(3.0 if r['unit']=='F' else 1.5) <= r['t_max'] - r['lc'] < -(1.0 if r['unit']=='F' else 0.5)]
        parts = []
        for name, cond in [("остывает/ровно (≤0)", lambda x: x <= 0), ("медленно (0..1.5/ч)", lambda x: 0 < x <= 1.5), ("быстро (>1.5/ч)", lambda x: x > 1.5)]:
            gg = [r for r in g if cond(r['rate2'] if r['unit']=='C' else r['rate2']/1.8)]
            if len(gg) < 8: continue
            n = len(gg); parts.append(f"{name}: n={n} лидер {sum(r['win'] for r in gg)/n*100:.0f}% ниже {sum(1 for r in gg if r['rel']<0)/n*100:.0f}% выше {sum(1 for r in gg if r['rel']>0)/n*100:.0f}%")
        if parts: lines.append(f"H={H:2d}\n   " + "\n   ".join(parts))
    section(lines, "4в. Вчерашний максимум относительно сегодняшнего лидера (H=24, лидер 40-70¢)")
    g = [r for r in R if r['H'] == 24 and r['y_max'] is not None and r['lc'] is not None and r['lp'] and 40 <= r['lp'] < 70 and r['rel'] is not None and r['lask']]
    for name, cond in [("вчера ≥ корзина выше", lambda r: r['y_max'] - r['lc'] > (1.0 if r['unit']=='F' else 0.5)), ("вчера в корзине лидера", lambda r: abs(r['y_max'] - r['lc']) <= (1.0 if r['unit']=='F' else 0.5)), ("вчера ≤ корзина ниже", lambda r: r['y_max'] - r['lc'] < -(1.0 if r['unit']=='F' else 0.5))]:
        gg = [r for r in g if cond(r)]
        if len(gg) < 10: continue
        n = len(gg); lines.append(f"  {name}: n={n} лидер {sum(r['win'] for r in gg)/n*100:.0f}% выше {sum(1 for r in gg if r['rel']>0)/n*100:.0f}% ниже {sum(1 for r in gg if r['rel']<0)/n*100:.0f}% | лидер по ask {fmt_pnl([pnl_buy(r['lask'], r['win']) for r in gg])}")
    section(lines, "4г. Сырая медиана моделей против лидера (H=24/30, лидер 40-70¢)")
    for H in [30, 24]:
        g = [r for r in R if r['H'] == H and r['cons'] is not None and r['lc'] is not None and r['lp'] and 40 <= r['lp'] < 70 and r['rel'] is not None]
        if len(g) < 20: continue
        parts = []
        for name, cond in [("модели выше лидера", lambda r: r['cons'] - r['lc'] > (1.0 if r['unit']=='F' else 0.5)), ("модели в корзине", lambda r: abs(r['cons'] - r['lc']) <= (1.0 if r['unit']=='F' else 0.5)), ("модели ниже", lambda r: r['cons'] - r['lc'] < -(1.0 if r['unit']=='F' else 0.5))]:
            gg = [r for r in g if cond(r)]
            if len(gg) < 10: continue
            n = len(gg); parts.append(f"{name}: n={n} лидер {sum(r['win'] for r in gg)/n*100:.0f}% выше {sum(1 for r in gg if r['rel']>0)/n*100:.0f}% ниже {sum(1 for r in gg if r['rel']<0)/n*100:.0f}%")
        lines.append(f"H={H:2d}\n   " + "\n   ".join(parts))

    # 5. межгородские связи
    section(lines, "5. Межгородские связи: одинаков ли знак исхода (ниже/лидер/выше) в один день, H=24")
    D = json.load(open(OUT / "days.json"))
    cities = sorted({k.split('@')[0] for dd in D.values() for k in dd})
    pairs = []
    for i, a in enumerate(cities):
        for b in cities[i+1:]:
            both = [(dd.get(f"{a}@24"), dd.get(f"{b}@24")) for dd in D.values() if dd.get(f"{a}@24") is not None and dd.get(f"{b}@24") is not None]
            both = [(x, y) for x, y in both if x != 0 or y != 0]  # хотя бы один не-лидер
            if len(both) < 15: continue
            agree = sum(1 for x, y in both if (x > 0 and y > 0) or (x < 0 and y < 0)); opp = sum(1 for x, y in both if x * y < 0)
            pairs.append((a, b, len(both), agree, opp))
    pairs.sort(key=lambda t: -(t[3] - t[4]) / t[2])
    lines.append("пара: n дней (хотя бы один не-лидер), совпал знак, противоположный знак")
    for a, b, n, ag, op in pairs[:15]: lines.append(f"  {a}–{b}: n={n} совпал {ag} ({ag/n*100:.0f}%) против {op}")
    lines.append("  … самые противоположные:")
    for a, b, n, ag, op in pairs[-5:]: lines.append(f"  {a}–{b}: n={n} совпал {ag} против {op} ({op/n*100:.0f}%)")
    # базовая частота: доля «выше» среди не-лидеров
    allrel = [v for dd in D.values() for k, v in dd.items() if k.endswith('@24') and v is not None and v != 0]
    lines.append(f"базово среди не-лидерских дней: выше {sum(1 for v in allrel if v>0)/len(allrel)*100:.0f}%, ниже {sum(1 for v in allrel if v<0)/len(allrel)*100:.0f}%")
    section(lines, "5б. Лаг Азия/Европа → США: доля «выше» у США в день, когда ≥60% закрывшихся раньше городов ушли выше (и наоборот)")
    US = ["atlanta","austin","houston","los-angeles","miami","san-francisco","dallas","chicago","nyc","denver","seattle"]
    EARLY = [c for c in cities if c not in US and c not in ("mexico-city","toronto","sao-paulo","buenos-aires")]
    rows5 = []
    for d, dd in D.items():
        e = [dd.get(f"{c}@24") for c in EARLY if dd.get(f"{c}@24") is not None]; e = [v for v in e if v != 0]
        u = [dd.get(f"{c}@12") for c in US if dd.get(f"{c}@12") is not None]
        if len(e) < 4 or len(u) < 3: continue
        fe = sum(1 for v in e if v > 0) / len(e)
        rows5.append((fe, u))
    for name, cond in [("ранние ≥60% выше", lambda f: f >= 0.6), ("ранние ≤40% выше", lambda f: f <= 0.4), ("между", lambda f: 0.4 < f < 0.6)]:
        us = [v for fe, u in rows5 if cond(fe) for v in u]
        if us: lines.append(f"  {name}: дней {sum(1 for fe,u in rows5 if cond(fe))}, США n={len(us)}: выше {sum(1 for v in us if v>0)/len(us)*100:.0f}% лидер {sum(1 for v in us if v==0)/len(us)*100:.0f}% ниже {sum(1 for v in us if v<0)/len(us)*100:.0f}%")

    text = "\n".join(lines)
    print(text)
    (ROOT / "reports" / "2026-11-01-deep-lab.md").write_text(text + "\n", encoding="utf-8")

if __name__ == "__main__" and "--report" in sys.argv:
    report()
