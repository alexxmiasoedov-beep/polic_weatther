"""Глубокий разбор по городам (запрос владельца 11.10: «к каждому городу свой подход,
ищи нетривиальные триггеры, которые показывают верную корзину ДО того, как цена ушла выше 70¢»).

Для каждого из 22 городов со известной станцией строим таблицу «город-день» с признаками,
известными на момент T (горизонт H часов до конца местных суток, пока лидер PM < 70¢),
и исходом: победитель относительно лидера (−1 ниже / 0 лидер / +1 выше).

Признаки (всё — без заглядывания вперёд):
  рынок:   цена лидера, отрыв от второй, число корзин >10¢ (размазанность), объём лидера /
           объём всех, движение лидера за 3 ч и за 6 ч, bid-ask лидера, Kalshi-лидер vs PM (США)
  модели:  медиана сырых моделей − центр корзины лидера (в °), разброс моделей (max−min),
           NBM − лидер, доля моделей в корзине лидера, precip_prob
  станция: максимум ПРЕДЫДУЩЕГО дня − центр лидера; тренд максимумов (вчера − позавчера);
           температура в фиксированный местный час (06/09/12) − центр лидера; точка росы
           и ветер утром (морской бриз), облачность утром (BKN/OVC), осадки в METAR утром
Для каждого признака — по городам: средний исход и доля побед лидера при признаке в нижней/
верхней трети, плюс лучшие «правила» вида «если признак ≥/≤ порог → корзина X», с
split-half проверкой. Вывод в reports/<дата>-deep-dive.md.

python3 deep_dive.py <iem_all.csv> [--H 27]
"""
import csv, json, math, re, statistics, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config, pilot
import strategy_lab as L

ROOT = Path(__file__).parent
ST = {s: c["station"] for src in (config.CITIES, pilot.PILOT_CITIES) for s, c in src.items()}
UNIT = {s: c.get("unit", "F") for src in (config.CITIES, pilot.PILOT_CITIES) for s, c in src.items()}
IEM_ID = {v: (v[1:] if v.startswith("K") else v) for v in ST.values()}
KOFF = {"atlanta": 1.0, "houston": 1.0, "austin": 0.5, "miami": 0.0}


def center(label, unit):
    m = re.findall(r"(?<!\d)-?\d+", label)  # «70-71°F» → 70, 71
    if not m: return None
    lo = int(m[0]); hi = int(m[1]) if len(m) > 1 else lo
    if "below" in label: return lo - (1 if unit == "F" else 0.5)
    if "higher" in label or "above" in label: return lo + (1 if unit == "F" else 0.5)
    return (lo + hi) / 2


def load_metar(path):
    obs = defaultdict(list)
    inv = {v: k for k, v in IEM_ID.items()}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        icao = inv.get(r["station"])
        if not icao: continue
        m = re.search(r"\bT(\d)(\d{3})\d{4}\b", r["metar"])
        if m:
            tc = int(m.group(2)) / 10 * (-1 if m.group(1) == "1" else 1)
        else:
            try: tc = (float(r["tmpf"]) - 32) * 5 / 9
            except ValueError: continue
        try: dw = (float(r["dwpf"]) - 32) * 5 / 9
        except ValueError: dw = None
        try: wind = float(r["sknt"])
        except ValueError: wind = None
        sky = r["skyc1"]; met = r["metar"]
        rain = bool(re.search(r"\b(-|\+)?(RA|TSRA|SHRA|DZ)\b", met.split("RMK")[0]))
        ts = datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        obs[icao].append((ts, tc, dw, wind, sky, rain))
    for v in obs.values(): v.sort()
    return obs


def temp_in_unit(tc, unit):
    return tc * 9 / 5 + 32 if unit == "F" else tc


def day_series(obs, tz, day):
    return [o for o in obs if o[0].astimezone(tz).date() == day]


def features(slug, d, v, obs_all, H):
    """признаки на горизонте H для рынка (slug, d); None если нет снапшота"""
    tz = L.tz_of(slug); unit = UNIT[slug]
    snaps = v["snaps"]; hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    w = L.winner_of(snaps)
    now_hte = L.hours_to_end(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), d, tz)
    if not w or (hte[-1] > 6 and now_hte > 0): return None
    sa = L.snap_at(snaps, hte, H)
    if not sa: return None
    ts, b, h = sa
    keys = [k for k in b if L.price(b, k) is not None]
    if len(keys) < 2: return None
    ks = sorted(keys, key=lambda k: -L.price(b, k)); lead, sec = ks[0], ks[1]
    pl = L.price(b, lead)
    if pl is None or pl >= 70 or pl < 25: return None  # интересуют рынки, где лидер ещё не убежал
    order = L.ordered(b); li = order.index(lead); wi = order.index(w) if w in order else None
    if wi is None: return None
    outcome = 0 if wi == li else (1 if wi > li else -1)
    below = order[li - 1] if li > 0 else None; above = order[li + 1] if li + 1 < len(order) else None
    f = {"slug": slug, "date": d, "H": H, "lead": lead, "winner": w, "outcome": outcome, "lead_price": pl,
         "lead_ask": b[lead][2], "gap": pl - L.price(b, sec), "n_live": sum(1 for k in keys if L.price(b, k) >= 10),
         "spread": (b[lead][2] - b[lead][1]) if b[lead][2] is not None and b[lead][1] is not None else None,
         "below_ask": b[below][2] if below and b[below][2] else None, "above_ask": b[above][2] if above and b[above][2] else None,
         "below_price": L.price(b, below) if below else None, "above_price": L.price(b, above) if above else None}
    # движение лидера за 3 и 6 ч
    for back in (3, 6):
        prev = [(t2, b2) for (t2, b2), h2 in zip(snaps, hte) if h + back - 1 <= h2 <= h + back + 1.5]
        f[f"mom{back}"] = (pl - L.price(prev[-1][1], lead)) if prev and lead in prev[-1][1] and L.price(prev[-1][1], lead) is not None else None
    # объём
    vol = v.get("vol", {}).get(ts)
    if vol:
        tot = sum(vol.values()) or 0
        f["vol_share"] = vol.get(lead, 0) / tot if tot else None; f["vol_total"] = tot
    # Kalshi (США): лидер Kalshi, отображённый на PM, выше/ниже/равен
    kal = v.get("kalshi", {}).get(ts)
    if not kal and v.get("kalshi"):
        kts = min(v["kalshi"], key=lambda t: abs((L.parse_ts(t) - L.parse_ts(ts)).total_seconds()))
        if abs((L.parse_ts(kts) - L.parse_ts(ts)).total_seconds()) < 5400: kal = v["kalshi"][kts]
    if kal and slug in KOFF:
        kb, kp = max(kal.items(), key=lambda kv: kv[1] or 0)
        m = re.findall(r"-?\d+", kb)
        if m and kp:
            lo = int(m[0]); hi = int(m[1]) if len(m) > 1 else lo
            mid = (lo + hi) / 2 - KOFF[slug]
            if "below" in kb: mid = lo - 1 - KOFF[slug]
            if "above" in kb: mid = lo + 1 - KOFF[slug]
            kpm = L.bucket_for_temp(b, mid, "F")
            if kpm in order:
                f["kalshi_rel"] = (order.index(kpm) - li); f["kalshi_price"] = kp
    # модели
    wx = v["wx"].get(ts) or (max(((t, x) for t, x in v["wx"].items() if t <= ts), default=(None, None))[1])
    c_lead = center(lead, unit)
    if wx and c_lead is not None:
        vals = [wx[m] for m in L.MODELS if isinstance(wx.get(m), (int, float))]
        if len(vals) >= 3:
            f["cons_minus_lead"] = statistics.median(vals) - c_lead
            f["model_spread"] = max(vals) - min(vals)
            f["models_in_lead"] = sum(1 for t in vals if L.bucket_for_temp(b, t, unit) == lead) / len(vals)
            f["models_below"] = sum(1 for t in vals if (lambda k: k in order and order.index(k) < li)(L.bucket_for_temp(b, t, unit))) / len(vals)
            f["models_above"] = sum(1 for t in vals if (lambda k: k in order and order.index(k) > li)(L.bucket_for_temp(b, t, unit))) / len(vals)
        if isinstance(wx.get("om_ncep_nbm_conus"), (int, float)): f["nbm_minus_lead"] = wx["om_ncep_nbm_conus"] - c_lead
        if isinstance(wx.get("precip_prob"), (int, float)): f["precip_prob"] = wx["precip_prob"]
    # станция
    obs = obs_all.get(ST[slug], [])
    day = datetime.fromisoformat(d).date()
    t_entry = L.parse_ts(ts)
    prevday = day_series(obs, tz, day - timedelta(days=1)); prev2 = day_series(obs, tz, day - timedelta(days=2))
    if prevday and c_lead is not None and all(o[0] <= t_entry for o in prevday[-1:]):
        mx1 = max(temp_in_unit(o[1], unit) for o in prevday); f["prevmax_minus_lead"] = mx1 - c_lead
        if prev2: f["max_trend"] = mx1 - max(temp_in_unit(o[1], unit) for o in prev2)
    today = [o for o in day_series(obs, tz, day) if o[0] <= t_entry - timedelta(minutes=4)]
    for hr in (6, 9, 12):
        cand = [o for o in today if o[0].astimezone(tz).hour == hr]
        if cand and c_lead is not None:
            o = cand[0]; f[f"t{hr:02d}_minus_lead"] = temp_in_unit(o[1], unit) - c_lead
            if hr == 9:
                f["dew09"] = temp_in_unit(o[2], unit) if o[2] is not None else None
                f["wind09"] = o[3]; f["cloud09"] = 1 if o[4] in ("BKN", "OVC") else 0; f["rain09"] = int(o[5])
    if today and c_lead is not None:
        f["todaymax_minus_lead"] = max(temp_in_unit(o[1], unit) for o in today) - c_lead
        f["n_obs_today"] = len(today)
    return f


def load_all():
    """как strategy_lab.load, но ещё объёмы и Kalshi по снапшотам для наших 22 городов"""
    S = L.load()
    import glob
    for fn in glob.glob(str(ROOT / "data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            try: s = json.loads(line)
            except Exception: continue
            d = s.get("market_date") or Path(fn).stem
            for slug, c in s.get("cities", {}).items():
                key = (slug, d)
                if key not in S: continue
                pm = (c.get("polymarket") or {}).get("buckets") or []
                S[key].setdefault("vol", {})[s["ts_utc"]] = {x["bucket"]: (x.get("volume") or 0) for x in pm}
                k = (c.get("kalshi") or {}).get("buckets") or []
                if k: S[key].setdefault("kalshi", {})[s["ts_utc"]] = {x["bucket"]: x.get("last") for x in k}
    for fn in glob.glob(str(ROOT / "pilot_data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            try: r = json.loads(line)
            except Exception: continue
            key = (r["city"], r.get("market_date") or Path(fn).stem)
            if key not in S: continue
            pm = (r.get("pm") or {}).get("buckets") or []
            S[key].setdefault("vol", {})[r["ts_utc"]] = {x["bucket"]: (x.get("volume") or 0) for x in pm}
    return S


def main():
    path = sys.argv[1]; Hs = [int(x) for x in (sys.argv[sys.argv.index("--H") + 1].split(",") if "--H" in sys.argv else ["30", "24", "18", "12"])]
    S = load_all(); obs = load_metar(path)
    rows = []
    for (slug, d), v in S.items():
        if slug not in ST: continue
        for H in Hs:
            f = features(slug, d, v, obs, H)
            if f: rows.append(f)
    out = ROOT / "reports" / "deep_dive_rows.json"
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"строк город-день-горизонт: {len(rows)} → {out}")


if __name__ == "__main__":
    main()
