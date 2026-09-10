"""Ленивость рынков — главная метрика денег (10.09, по мысли владельца:
«цель заработать, а не угадать рынок»).

По каждому городу и каждому резолвленному рынку (победитель = корзина
≥97¢ в последнем снапшоте) считаем, почём рынок отдавал ПОБЕДИТЕЛЯ за
T часов до конца локальных суток: −30 ч (вечер накануне), −18, −12, −8,
−4. Чем ниже цена победителя на раннем горизонте, тем больше денег
даёт правильный прогноз. Плюс: доля побед лидера на каждом горизонте
и EV слепой покупки лидера (<70¢).

Источники: observe_data/ (41 город, только цены), data/ (семёрка),
pilot_data/ (пилоты). Запуск: python3 lazy.py [--min-days N]
"""
import glob
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config
import pilot

ROOT = Path(__file__).parent
HORIZONS = [30, 18, 12, 8, 4]  # часов до конца локальных суток
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


def tz_of(slug):
    for src in (config.CITIES, pilot.PILOT_CITIES):
        if slug in src:
            return ZoneInfo(src[slug]["tz"])
    return ZoneInfo(TZ_FALLBACK.get(slug, "UTC"))


def load_series():
    """{(slug, market_date): [(ts_utc, {bucket: last})...]} из всех источников."""
    series = defaultdict(list)
    for fn in glob.glob(str(ROOT / "observe_data" / "*.jsonl")):
        d = Path(fn).stem
        for line in open(fn, encoding="utf-8"):
            s = json.loads(line)
            for slug, c in s["cities"].items():
                series[(slug, d)].append((s["ts_utc"], {b["bucket"]: b.get("last") for b in c["buckets"]}))
    for fn in glob.glob(str(ROOT / "data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            s = json.loads(line)
            d = s.get("market_date")
            for slug, c in s["cities"].items():
                pm = c.get("polymarket") or {}
                series[(slug, d)].append((s["ts_utc"], {b["bucket"]: b.get("last") for b in pm.get("buckets", [])}))
    for fn in glob.glob(str(ROOT / "pilot_data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            r = json.loads(line)
            series[(r["city"], r["market_date"])].append((r["ts_utc"], {b["bucket"]: b.get("last") for b in r["pm"]["buckets"]}))
    for k in series:
        series[k].sort()
    return series


def analyse(min_days=2):
    series = load_series()
    per_city = defaultdict(list)
    for (slug, d), snaps in series.items():
        if not snaps:
            continue
        last_ts, last_prices = snaps[-1]
        winners = [b for b, p in last_prices.items() if (p or 0) >= 97]
        if len(winners) != 1:
            continue
        w = winners[0]
        tz = tz_of(slug)
        day_end = datetime.fromisoformat(d).replace(tzinfo=tz) + timedelta(days=1)
        row = {"date": d, "winner": w}
        for h in HORIZONS:
            cutoff = (day_end - timedelta(hours=h)).astimezone(timezone.utc)
            before = [(ts, pr) for ts, pr in snaps
                      if datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= cutoff]
            if not before:
                continue
            ts, pr = before[-1]
            lead = max(pr.items(), key=lambda kv: kv[1] or 0)
            row[h] = {"wprice": pr.get(w), "leader": lead[0], "lprice": lead[1], "lead_won": lead[0] == w}
        per_city[slug].append(row)
    out = []
    for slug, rows in per_city.items():
        if len(rows) < min_days:
            continue
        rec = {"city": slug, "days": len(rows)}
        for h in HORIZONS:
            hr = [r[h] for r in rows if h in r and r[h]["wprice"] is not None]
            if not hr:
                continue
            wp = [x["wprice"] for x in hr]
            lw = sum(1 for x in hr if x["lead_won"])
            ev = sum(((100 / x["lprice"] - 1) if x["lead_won"] else -1)
                     for x in hr if x["lprice"] and 5 <= x["lprice"] < 70)
            rec[h] = {"n": len(hr), "wprice_med": statistics.median(wp),
                      "lead_win": lw / len(hr), "lead_ev": round(ev, 2)}
        out.append(rec)
    return out


def main():
    min_days = 2
    if "--min-days" in sys.argv:
        min_days = int(sys.argv[sys.argv.index("--min-days") + 1])
    out = analyse(min_days)
    # ключ денег: медианная цена победителя за 18 ч (утро/вечер накануне)
    key = lambda r: r.get(18, r.get(30, {})).get("wprice_med", 100)
    out.sort(key=key)
    print(f"{'город':16s} дн |" + "".join(f"  −{h:2d}ч: цена-поб. лидер-побед EV |" for h in HORIZONS))
    for r in out:
        line = f"{r['city']:16s} {r['days']:2d} |"
        for h in HORIZONS:
            x = r.get(h)
            line += (f" {x['wprice_med']:5.0f}¢ {100*x['lead_win']:4.0f}% {x['lead_ev']:+6.2f} |" if x
                     else "        —           |")
        print(line)
    (ROOT / "calibration" / "lazy.json").write_text(
        json.dumps({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "horizons_h": HORIZONS,
                    "note": "wprice_med — медианная цена победителя за h часов до конца локальных суток; lead_win — доля побед лидера; lead_ev — EV слепой покупки лидера <70¢ (ед.)",
                    "cities": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nсохранено calibration/lazy.json")


if __name__ == "__main__":
    main()
