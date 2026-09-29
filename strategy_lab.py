"""Лаборатория стратегий (29.09, по просьбе владельца: «перебери все варианты
и отбери города и стратегии, которые стабильно дают плюс»).

Механические правила, без суждений. Одна сделка на город-день на стратегию,
вход по ask, победитель = корзина ≥97¢ в последнем снапшоте.

Стратегии (H = часов до конца локальных суток, вход в ближайшем снапшоте
не позже H, окно [H−1.5, H+3]):
  lead:{lo}-{hi}@{H}   — купить лидера PM, если last в [lo,hi), ask ≤ hi+2, стакан ≤ 8
  second@{H}           — купить вторую корзину, если лидер 50-70 и вторая ≥ 20
  up@{H} / down@{H}    — купить соседнюю корзину выше/ниже лидера (лидер 40-70, сосед 15-45)
  mom{d}@{H}           — купить корзину, выросшую на ≥d¢ за последние ~3 ч (цена 30-70)
  cons@{H}             — купить корзину сырой медианы моделей (семёрка, без bias — без утечки)
  nbm@{H}              — купить корзину NBM
  lock:{lo}-{hi}@{H}   — поздний лок лидера (85-97) — маленький эдж, высокая точность
  tail@{H}             — дешёвые хвосты 10-30¢ (проверка калибровки, ожидаем минус)

Устойчивость: n, попадания, P&L/сделку, доля плюсовых дней, t-стат, split-half
(первая/вторая половина дат) и walk-forward: отбор на датах ≤ SPLIT, проверка после.

python3 strategy_lab.py [--split 2026-09-21] [--min-n 6]
"""
import glob, json, re, sys, math, statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config, pilot

ROOT = Path(__file__).parent
TZ_FALLBACK = {
    "ankara": "Europe/Istanbul", "beijing": "Asia/Shanghai",
    "buenos-aires": "America/Argentina/Buenos_Aires", "busan": "Asia/Seoul",
    "cape-town": "Africa/Johannesburg", "chicago": "America/Chicago",
    "chongqing": "Asia/Shanghai", "hong-kong": "Asia/Hong_Kong",
    "madrid": "Europe/Madrid", "manila": "Asia/Manila", "munich": "Europe/Berlin",
    "nyc": "America/New_York", "paris": "Europe/Paris", "sao-paulo": "America/Sao_Paulo",
    "seoul": "Asia/Seoul", "taipei": "Asia/Taipei", "tokyo": "Asia/Tokyo",
    "warsaw": "Europe/Warsaw", "wuhan": "Asia/Shanghai",
}
HORIZONS = [30, 24, 18, 12, 8, 6, 4]
MODELS = ["nws", "om_best_match", "om_ecmwf_ifs025", "om_gfs_seamless", "om_icon_seamless",
          "om_ecmwf_aifs025_single", "om_ncep_nbm_conus", "om_ukmo_seamless", "om_gfs_hrrr"]


def tz_of(slug):
    for src in (config.CITIES, pilot.PILOT_CITIES):
        if slug in src:
            return ZoneInfo(src[slug]["tz"])
    return ZoneInfo(TZ_FALLBACK.get(slug, "UTC"))


def bnum(label):
    m = re.search(r"-?\d+", label)
    return int(m.group()) if m else None


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load():
    """{(slug, date): {'snaps': [(ts, {bucket:(last,bid,ask)})], 'wx': {ts: wx}}}"""
    S = defaultdict(lambda: {"snaps": [], "wx": {}})
    def add(slug, d, ts, buckets, wx=None):
        if not buckets or not d:
            return
        S[(slug, d)]["snaps"].append((ts, {b["bucket"]: (b.get("last"), b.get("bid"), b.get("ask")) for b in buckets}))
        if wx:
            S[(slug, d)]["wx"][ts] = wx
    for fn in glob.glob(str(ROOT / "observe_data" / "*.jsonl")):
        d = Path(fn).stem
        for line in open(fn, encoding="utf-8"):
            try: s = json.loads(line)
            except Exception: continue
            for slug, c in s.get("cities", {}).items():
                add(slug, d, s["ts_utc"], c.get("buckets"))
    for fn in glob.glob(str(ROOT / "data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            try: s = json.loads(line)
            except Exception: continue
            d = s.get("market_date") or Path(fn).stem
            for slug, c in s.get("cities", {}).items():
                pm = c.get("polymarket") or {}
                add(slug, d, s["ts_utc"], pm.get("buckets"), c.get("wx"))
    for fn in glob.glob(str(ROOT / "pilot_data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            try: r = json.loads(line)
            except Exception: continue
            add(r["city"], r.get("market_date") or Path(fn).stem, r["ts_utc"], (r.get("pm") or {}).get("buckets"), r.get("wx"))
    for k, v in S.items():
        # дедуп по ts
        seen = {}
        for ts, b in v["snaps"]:
            seen[ts] = b
        v["snaps"] = sorted(seen.items())
    return S


def winner_of(snaps):
    ts, b = snaps[-1]
    w = [k for k, (l, bid, a) in b.items() if (l or 0) >= 97 or (bid or 0) >= 97]
    return w[0] if len(w) == 1 else None


def hours_to_end(ts, d, tz):
    end_local = datetime.fromisoformat(d).replace(tzinfo=tz) + timedelta(days=1)
    return (end_local.astimezone(timezone.utc) - parse_ts(ts)).total_seconds() / 3600


def snap_at(snaps, hte, H):
    """последний снапшот с hte ≥ H−1.5, но не дальше H+3"""
    cand = [(t, b, h) for (t, b), h in zip(snaps, hte) if H - 1.5 <= h <= H + 3]
    if not cand:
        return None
    return min(cand, key=lambda x: abs(x[2] - H))


def ordered(b):
    return sorted(b.keys(), key=lambda k: (bnum(k) if bnum(k) is not None else -999))


def price(b, k):
    l, bid, a = b[k]
    return l if l is not None else ((bid or 0) + (a or 0)) / 2 if (bid or a) else None


def pnl(win, ask):
    return (100 / ask - 1) if win else -1.0


def trade_leader(b, w, lo, hi, spread=8):
    keys = [k for k in b if price(b, k) is not None]
    if not keys: return None
    lead = max(keys, key=lambda k: price(b, k))
    p = price(b, lead); l, bid, a = b[lead]
    a = a if a is not None else p
    if p is None or not (lo <= p < hi) or a is None or a > hi + 2: return None
    if bid is not None and a - bid > spread: return None
    return (lead, a, pnl(lead == w, a))


def trade_second(b, w):
    keys = [k for k in b if price(b, k) is not None]
    if len(keys) < 2: return None
    ks = sorted(keys, key=lambda k: -price(b, k))
    lead, sec = ks[0], ks[1]
    if not (50 <= price(b, lead) < 70): return None
    l, bid, a = b[sec]; p = price(b, sec)
    if p is None or p < 20 or a is None or a > 55: return None
    return (sec, a, pnl(sec == w, a))


def trade_adjacent(b, w, direction):
    keys = [k for k in b if price(b, k) is not None]
    if not keys: return None
    lead = max(keys, key=lambda k: price(b, k))
    if not (40 <= price(b, lead) < 70): return None
    order = ordered(b); i = order.index(lead); j = i + direction
    if j < 0 or j >= len(order): return None
    tgt = order[j]; p = price(b, tgt); l, bid, a = b[tgt]
    if p is None or not (15 <= p <= 45) or a is None or a > 50: return None
    return (tgt, a, pnl(tgt == w, a))


def trade_momentum(b_now, b_prev, w, d):
    best = None
    for k in b_now:
        if k not in b_prev: continue
        p0, p1 = price(b_prev, k), price(b_now, k)
        if p0 is None or p1 is None: continue
        if p1 - p0 >= d and 30 <= p1 < 70:
            if best is None or p1 - p0 > best[1]:
                best = (k, p1 - p0)
    if not best: return None
    k = best[0]; l, bid, a = b[k] if False else b_now[k]
    if a is None or a > 72: return None
    return (k, a, pnl(k == w, a))


def bucket_for_temp(b, t, unit):
    """корзина, содержащая температуру t (°F для US, °C иначе)"""
    order = ordered(b)
    for k in order:
        n = bnum(k)
        if n is None: continue
        if "below" in k and t <= n + 0.999: return k
        if "higher" in k or "above" in k:
            if t >= n - 0.001: return k
            continue
        m = re.findall(r"-?\d+", k)
        lo = int(m[0]); hi = int(m[1]) if len(m) > 1 else lo
        if lo - 0.5 <= t < hi + 0.5: return k
    return None


def trade_model(b, w, wx, which):
    if not wx: return None
    if which == "nbm":
        t = wx.get("om_ncep_nbm_conus")
        if t is None: return None
    else:
        vals = [wx[m] for m in MODELS if isinstance(wx.get(m), (int, float))]
        if len(vals) < 3: return None
        t = statistics.median(vals)
    k = bucket_for_temp(b, t, "F")
    if not k or k not in b: return None
    p = price(b, k); l, bid, a = b[k]
    if p is None or a is None or p > 70 or a > 72 or p < 10: return None
    return (k, a, pnl(k == w, a))


def trade_tail(b, w):
    keys = [k for k in b if price(b, k) is not None and 10 <= price(b, k) <= 30]
    if not keys: return None
    keys = [k for k in keys if b[k][2] is not None and b[k][2] <= 32]
    if not keys: return None
    # соседний с лидером хвост (самый дорогой из хвостов)
    k = max(keys, key=lambda k: price(b, k)); a = b[k][2]
    return (k, a, pnl(k == w, a))


def run(split, min_n):
    S = load()
    trades = defaultdict(list)  # (strategy, slug) -> [(date, pnl, hit)]
    days_total = defaultdict(set)
    for (slug, d), v in S.items():
        snaps = v["snaps"]
        if len(snaps) < 3: continue
        w = winner_of(snaps)
        if not w: continue
        tz = tz_of(slug)
        hte = [hours_to_end(t, d, tz) for t, _ in snaps]
        if hte[-1] > 6:  # последний снапшот далеко до конца суток — рынок не дожил
            continue
        days_total[slug].add(d)
        for H in HORIZONS:
            sa = snap_at(snaps, hte, H)
            if not sa: continue
            ts, b, h = sa
            wx = v["wx"].get(ts) or (max(((t, x) for t, x in v["wx"].items() if t <= ts), default=(None, None))[1])
            cands = {}
            for lo, hi in [(30, 50), (40, 60), (50, 70), (60, 80), (70, 90)]:
                cands[f"lead:{lo}-{hi}@{H}"] = trade_leader(b, w, lo, hi)
            cands[f"lock:85-97@{H}"] = trade_leader(b, w, 85, 97, spread=6)
            cands[f"second@{H}"] = trade_second(b, w)
            cands[f"up@{H}"] = trade_adjacent(b, w, +1)
            cands[f"down@{H}"] = trade_adjacent(b, w, -1)
            cands[f"tail@{H}"] = trade_tail(b, w)
            prev = [(t2, b2) for (t2, b2), h2 in zip(snaps, hte) if h + 2 <= h2 <= h + 4.5]
            if prev:
                for dlt in (10, 20):
                    cands[f"mom{dlt}@{H}"] = trade_momentum(b, prev[-1][1], w, dlt)
            if wx:
                cands[f"cons@{H}"] = trade_model(b, w, wx, "cons")
                cands[f"nbm@{H}"] = trade_model(b, w, wx, "nbm")
            for name, tr in cands.items():
                if tr:
                    trades[(name, slug)].append((d, tr[2], tr[2] > 0, tr[1]))

    def stats(rows):
        n = len(rows); hit = sum(1 for r in rows if r[2]); tot = sum(r[1] for r in rows)
        pnls = [r[1] for r in rows]
        sd = statistics.pstdev(pnls) if n > 1 else 0
        t = (tot / n) / (sd / math.sqrt(n)) if n > 1 and sd > 0 else 0
        return dict(n=n, hit=hit, pnl=round(tot, 2), per=round(tot / n * 100, 1) if n else 0, t=round(t, 2),
                    avg_ask=round(sum(r[3] for r in rows) / n, 1) if n else 0)

    out = {"split": split, "min_n": min_n, "combos": [], "strategies": {}, "cities": {}}
    # глобально по стратегиям (все города)
    by_strat = defaultdict(list)
    for (name, slug), rows in trades.items():
        by_strat[name].extend(rows)
    for name, rows in by_strat.items():
        a = [r for r in rows if r[0] <= split]; bb = [r for r in rows if r[0] > split]
        out["strategies"][name] = dict(all=stats(rows), ins=stats(a), oos=stats(bb))

    # пары город-стратегия
    for (name, slug), rows in trades.items():
        if len(rows) < min_n: continue
        a = [r for r in rows if r[0] <= split]; bb = [r for r in rows if r[0] > split]
        st = stats(rows)
        rec = dict(strategy=name, city=slug, **st, ins=stats(a), oos=stats(bb),
                   days=len(days_total[slug]), pos_days=sum(1 for r in rows if r[1] > 0),
                   neg_days=sum(1 for r in rows if r[1] < 0))
        out["combos"].append(rec)
    out["combos"].sort(key=lambda r: (-r["t"], -r["per"]))
    # отбор: n≥min_n, per≥+10%, обе половины ≥0, t≥1.0, ins n≥3 и oos n≥2
    sel = [r for r in out["combos"] if r["per"] >= 10 and r["t"] >= 1.0 and r["ins"]["n"] >= 3 and r["oos"]["n"] >= 2
           and r["ins"]["pnl"] >= 0 and r["oos"]["pnl"] >= 0]
    # walk-forward: отбор ТОЛЬКО по ins (per≥10, n≥4, t≥1), затем смотрим oos
    wf = [r for r in out["combos"] if r["ins"]["n"] >= 4 and r["ins"]["per"] >= 10 and r["ins"]["t"] >= 1.0]
    wf_oos_n = sum(r["oos"]["n"] for r in wf); wf_oos_pnl = sum(r["oos"]["pnl"] for r in wf)
    wf_oos_hit = sum(r["oos"]["hit"] for r in wf)
    out["selected"] = sel
    out["walk_forward"] = dict(n_combos=len(wf), oos_n=wf_oos_n, oos_hit=wf_oos_hit, oos_pnl=round(wf_oos_pnl, 2),
                               oos_per=round(wf_oos_pnl / wf_oos_n * 100, 1) if wf_oos_n else None,
                               combos=[dict(strategy=r["strategy"], city=r["city"], ins=r["ins"], oos=r["oos"]) for r in wf])
    # лучшая стратегия на город (по отбору sel; иначе — none)
    best = {}
    for r in sel:
        c = r["city"]
        if c not in best or (r["t"], r["per"]) > (best[c]["t"], best[c]["per"]):
            best[c] = r
    out["cities"] = {c: dict(strategy=r["strategy"], n=r["n"], hit=r["hit"], per=r["per"], t=r["t"], ins=r["ins"], oos=r["oos"]) for c, r in best.items()}
    return out


def main():
    split = "2026-09-21"; min_n = 6
    a = sys.argv[1:]
    if "--split" in a: split = a[a.index("--split") + 1]
    if "--min-n" in a: min_n = int(a[a.index("--min-n") + 1])
    out = run(split, min_n)
    print(f"== СТРАТЕГИИ ГЛОБАЛЬНО (все города), split={split}: all | ins | oos ==")
    rows = sorted(out["strategies"].items(), key=lambda kv: -kv[1]["all"]["per"])
    for name, s in rows:
        A, I, O = s["all"], s["ins"], s["oos"]
        if A["n"] < 15: continue
        print(f"{name:16s} n={A['n']:3d} hit={A['hit']/A['n']*100:3.0f}% {A['per']:+6.1f}%/сд t={A['t']:+.1f} | ins n={I['n']:3d} {I['per']:+6.1f}% | oos n={O['n']:3d} {O['per']:+6.1f}%")
    print(f"\n== ОТБОР город→стратегия (n≥{min_n}, ≥+10%/сд, t≥1, обе половины ≥0) ==")
    for r in out["selected"]:
        print(f"{r['city']:15s} {r['strategy']:16s} n={r['n']:2d} hit={r['hit']:2d} {r['per']:+6.1f}% t={r['t']:+.1f} | ins {r['ins']['n']:2d} {r['ins']['per']:+6.1f}% | oos {r['oos']['n']:2d} {r['oos']['per']:+6.1f}%")
    wf = out["walk_forward"]
    print(f"\n== WALK-FORWARD: отбор по датам ≤{split} ({wf['n_combos']} пар), проверка после: n={wf['oos_n']} hit={wf['oos_hit']} pnl={wf['oos_pnl']:+.2f} ({wf['oos_per']}%/сд) ==")
    for r in wf["combos"][:40]:
        print(f"  {r['city']:15s} {r['strategy']:16s} ins n={r['ins']['n']:2d} {r['ins']['per']:+6.1f}% → oos n={r['oos']['n']:2d} {r['oos']['per']:+6.1f}%")
    print("\n== ЛУЧШАЯ СТРАТЕГИЯ НА ГОРОД ==")
    for c, r in sorted(out["cities"].items(), key=lambda kv: -kv[1]["t"]):
        print(f"  {c:15s} {r['strategy']:16s} n={r['n']:2d} {r['hit']}/{r['n']} {r['per']:+6.1f}% t={r['t']:+.1f}")
    (ROOT / "calibration" / "strategy_lab.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("\nсохранено calibration/strategy_lab.json")


if __name__ == "__main__":
    main()
