"""Бэктест монитора переворотов по факту (запрос владельца 11.10).

Сетап А «fact_flip»: станция (часовой METAR с десятыми, IEM-архив) уже
показывает максимум дня в корзине, отличной от лидера PM; корзина стоит
в коридоре цен; запас до верхней границы корзины ≥ m °F; тренд станции
(последние два часовых отсчёта) — любой / плато-или-вниз.
Сетап Б «kalshi_lag»: Kalshi (с офсетом) ≥K¢ на корзине, которая на PM
не лидер и стоит в коридоре; станция ещё растёт (или любой тренд).

Вход — по ask первого подходящего снапшота в окне локального времени,
одна сделка на город-день, size 1. Победитель — корзина ≥97¢ в последнем
снапшоте. Данные: data/*.jsonl (семёрка, PM+Kalshi), pilot_data (Даллас,
только PM), METAR — CSV из IEM (station,valid,tmpf,metar), путь аргументом.

  python3 monitor_backtest.py <iem.csv> [--since 2026-09-01]
"""
import csv, glob, json, re, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = __file__.rsplit('/', 1)[0] if '/' in __file__ else '.'
ST = {"MIA": "miami", "HOU": "houston", "DAL": "dallas", "LAX": "los-angeles",
      "SFO": "san-francisco", "ATL": "atlanta", "AUS": "austin"}
TZ = {"miami": "America/New_York", "atlanta": "America/New_York", "houston": "America/Chicago",
      "dallas": "America/Chicago", "austin": "America/Chicago", "los-angeles": "America/Los_Angeles",
      "san-francisco": "America/Los_Angeles"}
KOFF = {"atlanta": 1.0, "houston": 1.0, "austin": 0.5, "miami": 0.0}  # PM = Kalshi − offset


def pts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def brange(name):
    m = re.match(r"(\d+)-(\d+)", name)
    if m: return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d+)°F? or below", name)
    if m: return -999, int(m.group(1))
    m = re.match(r"(\d+)°F? or (higher|above)", name)
    if m: return int(m.group(1)), 999
    m = re.match(r"(\d+)° to (\d+)°", name)
    if m: return int(m.group(1)), int(m.group(2))
    return None


def bucket_of(buckets, temp_f):
    t = int(temp_f + 0.5)  # округление максимума до целого °F
    for b in buckets:
        r = brange(b)
        if r and r[0] <= t <= r[1]: return b
    return None


def load_series():
    S = {}
    for p in sorted(glob.glob(f"{ROOT}/data/*.jsonl")):
        for l in open(p, encoding="utf-8"):
            try: r = json.loads(l)
            except ValueError: continue
            d = r.get("market_date", p[-16:-6])
            for s, c in r["cities"].items():
                pm = (c.get("polymarket") or {}).get("buckets")
                if not pm: continue
                k = {b["bucket"]: b.get("last") for b in (c.get("kalshi") or {}).get("buckets", [])}
                S.setdefault((s, d), []).append((r["ts_utc"], {b["bucket"]: (b.get("last"), b.get("bid"), b.get("ask")) for b in pm}, k))
    for p in sorted(glob.glob(f"{ROOT}/pilot_data/*.jsonl")):
        for l in open(p, encoding="utf-8"):
            try: r = json.loads(l)
            except ValueError: continue
            if r.get("city") != "dallas": continue
            pm = r["pm"].get("buckets") if isinstance(r.get("pm"), dict) else r.get("pm")
            if pm: S.setdefault(("dallas", r["market_date"]), []).append((r["ts_utc"], {b["bucket"]: (b.get("last"), b.get("bid"), b.get("ask")) for b in pm}, {}))
    for v in S.values(): v.sort()
    return S


def load_metar(path):
    """{slug: [(ts_utc, tempF)]} — десятые из T-группы, иначе tmpf."""
    obs = defaultdict(list)
    for row in csv.DictReader(open(path, encoding="utf-8")):
        slug = ST.get(row["station"])
        if not slug: continue
        m = re.search(r"\bT(\d)(\d{3})\d{4}\b", row["metar"])
        if m:
            tc = int(m.group(2)) / 10 * (-1 if m.group(1) == "1" else 1)
            tf = tc * 9 / 5 + 32
        else:
            try: tf = float(row["tmpf"])
            except ValueError: continue
        ts = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        obs[slug].append((ts, round(tf, 1)))
    for v in obs.values(): v.sort()
    return obs


def winner_of(v):
    _, pm, _ = v[-1]
    w = [b for b, (l, bid, a) in pm.items() if (l or 0) >= 97 or (bid or 0) >= 97]
    return w[0] if len(w) == 1 else None


def kalshi_lead(k, slug, pm_buckets):
    """лучшая корзина Kalshi, отображённая на корзину PM через офсет; (pm_bucket, price)"""
    if not k or slug not in KOFF: return None, 0
    b, p = max(k.items(), key=lambda kv: kv[1] or 0)
    r = brange(b)
    if not r or not p: return None, 0
    lo, hi = r
    mid = (max(lo, hi - 1) + min(hi, lo + 1)) / 2 if lo > -999 and hi < 999 else (hi if lo == -999 else lo)
    return bucket_of(pm_buckets, mid - KOFF[slug]), p


def simulate(S, obs, cfg, since):
    kind = cfg["kind"]; res = []
    for (slug, d), v in S.items():
        if d < since or slug not in TZ: continue
        w = winner_of(v)
        if not w: continue
        tz = ZoneInfo(TZ[slug]); day = datetime.fromisoformat(d).date()
        series = [(t, f) for t, f in obs.get(slug, []) if t.astimezone(tz).date() == day]
        if len(series) < 6: continue
        for ts, pm, k in v:
            t = pts(ts); lt = t.astimezone(tz)
            if lt.date() != day or not (cfg["h0"] <= lt.hour < cfg["h1"]): continue
            seen = [(o, f) for o, f in series if o <= t - timedelta(minutes=4)]
            if len(seen) < 2: continue
            daymax = max(f for _, f in seen)
            rising = seen[-1][1] > seen[-2][1]
            leader = max(pm, key=lambda b: pm[b][0] or 0)
            if kind == "A":
                tgt = bucket_of(pm, daymax)
                if not tgt or tgt == leader: continue
                r = brange(tgt); margin = (r[1] + 0.5) - daymax if r[1] < 999 else 9
                if margin < cfg["m"]: continue
                if cfg["trend"] == "flat" and rising: continue
                kl, kp = kalshi_lead(k, slug, pm)
                if cfg["kcheck"] and kl and kl != tgt and kp >= 65: continue
            else:
                kl, kp = kalshi_lead(k, slug, pm)
                if not kl or kl == leader or kp < cfg["K"]: continue
                if cfg["trend"] == "rising" and not rising: continue
                tgt = kl
            last, bid, ask = pm[tgt]
            price = ask if ask else last
            if not price or not (cfg["lo"] <= price <= cfg["hi"]): continue
            hit = tgt == w
            res.append((d, slug, lt.strftime("%H:%M"), tgt, leader, round(price, 1), round(daymax, 1), hit, (100 / price - 1) if hit else -1.0))
            break
    return res


def summ(res):
    n = len(res); h = sum(1 for r in res if r[7]); p = sum(r[8] for r in res)
    return n, h, p


def main():
    path = sys.argv[1]; since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2026-08-27"
    S = load_series(); obs = load_metar(path)
    print(f"рынков с победителем: {sum(1 for v in S.values() if winner_of(v))}, METAR станций: {len(obs)}")
    rows = []
    for lo, hi in [(10, 45), (15, 55), (15, 65), (20, 55), (20, 70), (30, 70)]:
        for m in [0.0, 0.5, 1.0, 1.5]:
            for trend in ["any", "flat"]:
                for h0 in [11, 13, 15]:
                    for kc in [True, False]:
                        cfg = dict(kind="A", lo=lo, hi=hi, m=m, trend=trend, h0=h0, h1=19, kcheck=kc)
                        res = simulate(S, obs, cfg, since); n, h, p = summ(res)
                        if n >= 5: rows.append((p / n, n, h, p, cfg, res))
    rows.sort(key=lambda x: -x[3])
    print("\n== СЕТАП А fact_flip — топ по суммарному P&L (n≥5) ==")
    for per, n, h, p, cfg, _ in rows[:15]:
        print(f"lo{cfg['lo']}-hi{cfg['hi']} m≥{cfg['m']}°F {cfg['trend']:4s} с {cfg['h0']}:00 kalshi_chk={int(cfg['kcheck'])}  n={n:3d} hit={h:3d} ({h/n*100:3.0f}%) pnl={p:+6.2f} ({per*100:+5.1f}%/сд)")
    print("\n== СЕТАП А — худшие ==")
    for per, n, h, p, cfg, _ in rows[-5:]:
        print(f"lo{cfg['lo']}-hi{cfg['hi']} m≥{cfg['m']}°F {cfg['trend']:4s} с {cfg['h0']}:00 kalshi_chk={int(cfg['kcheck'])}  n={n:3d} hit={h:3d} pnl={p:+6.2f} ({per*100:+5.1f}%/сд)")
    # текущее правило: 15-55, запас 0.9°F (0.5°C), любой тренд, с 13:00, kalshi не против
    cur = dict(kind="A", lo=15, hi=55, m=0.9, trend="any", h0=13, h1=19, kcheck=True)
    res = simulate(S, obs, cur, since); n, h, p = summ(res)
    print(f"\n== ТЕКУЩЕЕ ПРАВИЛО А (15-55¢, запас ≥0.9°F, kalshi не против, с 13:00): n={n} hit={h} pnl={p:+.2f} ==")
    for r in res: print("  ", r)
    for lab, c in [("без запаса", dict(cur, m=0.0)), ("только плато", dict(cur, m=0.0, trend="flat")), ("плато + запас 0.9", dict(cur, trend="flat")), ("с 15:00 без запаса", dict(cur, m=0.0, h0=15))]:
        res = simulate(S, obs, c, since); n, h, p = summ(res)
        print(f"  вариант «{lab}»: n={n} hit={h} pnl={p:+.2f}")
        byc = defaultdict(lambda: [0, 0, 0.0])
        for r in res: byc[r[1]][0] += 1; byc[r[1]][1] += r[7]; byc[r[1]][2] += r[8]
        print("     по городам:", {k: f"{v[1]}/{v[0]} {v[2]:+.1f}" for k, v in sorted(byc.items())})
    print("\n== СЕТАП Б kalshi_lag (MIA/HOU/ATL/AUS) ==")
    rowsB = []
    for K in [55, 65, 75, 85]:
        for lo, hi in [(15, 55), (15, 70), (20, 60)]:
            for trend in ["rising", "any"]:
                for h0 in [11, 13, 15]:
                    cfg = dict(kind="B", K=K, lo=lo, hi=hi, trend=trend, h0=h0, h1=19)
                    res = simulate(S, obs, cfg, since); n, h, p = summ(res)
                    if n >= 5: rowsB.append((n, h, p, cfg, res))
    rowsB.sort(key=lambda x: -x[2])
    for n, h, p, cfg, _ in rowsB[:12] + rowsB[-4:]:
        print(f"K≥{cfg['K']} lo{cfg['lo']}-hi{cfg['hi']} {cfg['trend']:6s} с {cfg['h0']}:00  n={n:3d} hit={h:3d} ({h/n*100:3.0f}%) pnl={p:+6.2f} ({p/n*100:+5.1f}%/сд)")
    curB = dict(kind="B", K=55, lo=15, hi=55, trend="rising", h0=13, h1=19)
    res = simulate(S, obs, curB, since); n, h, p = summ(res)
    print(f"\n== ТЕКУЩЕЕ ПРАВИЛО Б (Kalshi≥55, PM 15-55, растёт, с 13:00): n={n} hit={h} pnl={p:+.2f} ==")
    byc = defaultdict(lambda: [0, 0, 0.0])
    for r in res: byc[r[1]][0] += 1; byc[r[1]][1] += r[7]; byc[r[1]][2] += r[8]
    print("   по городам:", {k: f"{v[1]}/{v[0]} {v[2]:+.1f}" for k, v in sorted(byc.items())})
    for r in res[-12:]: print("  ", r)


if __name__ == "__main__":
    main()
