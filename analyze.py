"""Анализ суточного движения цен и базовый (алгоритмический) прогноз.

Читает data/<дата>.jsonl, по каждому городу строит:
  - траектории цен корзин Polymarket и Kalshi за сутки;
  - перенос вероятностей Kalshi на сетку корзин Polymarket с учётом
    исторического смещения резолвов (config.CITIES[..]["ks_pm_offset"]);
  - комбинированную оценку вероятности победы каждой корзины PM
    с поправкой на момент (движение цены за последние ~3 часа).

Запуск: python analyze.py [YYYY-MM-DD] [--json]
Без даты берётся config.analysis_date() — день, чей цикл сбора
завершился в 19:05 по Минску.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

import config

W_PM = 0.6          # вес текущих цен Polymarket
W_KS = 0.4          # вес перенесённых вероятностей Kalshi
W_MOMENTUM = 0.6    # сила поправки на движение цены за ~3 часа
OPEN_TAIL = (0.6, 0.25, 0.15)  # распределение массы открытой корзины (≤/≥) вглубь хвоста


def parse_pm_bucket(label: str):
    """'88-89°F' -> (88, 89); '≤81°F' -> (None, 81); '100°F+'/'≥100°F' -> (100, None)."""
    s = label.replace("°F", "").replace("°", "").replace("–", "-").replace("—", "-").strip()
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(?:≤|<=|under\s*)\s*(\d+)$|^(\d+)\s*or below$", s, re.I)
    if m:
        return None, int(next(g for g in m.groups() if g))
    m = re.match(r"^(?:≥|>=)\s*(\d+)$|^(\d+)\s*\+$|^(\d+)\s*or higher$", s, re.I)
    if m:
        return int(next(g for g in m.groups() if g)), None
    m = re.match(r"^(\d+)$", s)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


def ks_bucket_temps(b):
    """Диапазон целых температур корзины Kalshi по floor/cap страйкам."""
    floor, cap, st = b.get("floor"), b.get("cap"), b.get("strike_type")
    if st == "between" and floor is not None and cap is not None:
        return list(range(int(floor), int(cap) + 1)), False
    if floor is None and cap is not None:      # "X or below"
        return [int(cap) - i for i in range(len(OPEN_TAIL))], True
    if cap is None and floor is not None:      # "X or above" (greater: строго выше floor)
        lo = int(floor) + (1 if st == "greater" else 0)
        return [lo + i for i in range(len(OPEN_TAIL))], True
    return [], False


def price_of(b):
    """Рабочая цена корзины в центах: середина bid/ask, иначе last."""
    bid, ask, last = b.get("bid"), b.get("ask"), b.get("last")
    if bid is not None and ask is not None and ask > 0:
        return (bid + ask) / 2
    return last if last is not None else 0.0


def load_snapshots(d: date):
    path = Path(__file__).parent / "data" / f"{d.isoformat()}.jsonl"
    if not path.exists():
        sys.exit(f"Нет данных: {path}")
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def series_by_bucket(snapshots, city_slug, platform):
    """{bucket_label: {'series': [цены по снапшотам], 'meta': последняя запись}}"""
    out = {}
    for i, snap in enumerate(snapshots):
        plat = (snap["cities"].get(city_slug) or {}).get(platform)
        if not plat:
            continue
        for b in plat["buckets"]:
            e = out.setdefault(b["bucket"], {"series": [None] * len(snapshots), "meta": b})
            e["series"][i] = price_of(b)
            e["meta"] = b
    return out


def last_value(series, back=0):
    vals = [v for v in series if v is not None]
    if not vals:
        return 0.0
    idx = max(0, len(vals) - 1 - back)
    return vals[idx]


def analyze_city(snapshots, city_slug):
    cfg = config.CITIES[city_slug]
    pm = series_by_bucket(snapshots, city_slug, "polymarket")
    ks = series_by_bucket(snapshots, city_slug, "kalshi")

    # --- Kalshi -> вероятности по целым температурам, сдвиг на offset ---
    temp_mass = {}
    ks_total = sum(last_value(e["series"]) for e in ks.values()) or 1.0
    for e in ks.values():
        p = last_value(e["series"]) / ks_total
        temps, is_open = ks_bucket_temps(e["meta"])
        if not temps:
            continue
        weights = OPEN_TAIL if is_open else [1 / len(temps)] * len(temps)
        for t, w in zip(temps, weights):
            shifted = t - cfg["ks_pm_offset"]
            lo_t, frac = int(shifted // 1), shifted % 1
            if frac == 0:
                temp_mass[lo_t] = temp_mass.get(lo_t, 0) + p * w
            else:  # дробный сдвиг (offset 0.5) — делим между соседними градусами
                temp_mass[lo_t] = temp_mass.get(lo_t, 0) + p * w * (1 - frac)
                temp_mass[lo_t + 1] = temp_mass.get(lo_t + 1, 0) + p * w * frac

    rows = []
    for label, e in pm.items():
        lo, hi = parse_pm_bucket(label)
        p_now = last_value(e["series"])
        p_3h = last_value(e["series"], back=3)
        # вероятность Kalshi, попадающая в диапазон этой корзины PM
        if lo is None and hi is not None:
            ks_p = sum(v for t, v in temp_mass.items() if t <= hi)
        elif hi is None and lo is not None:
            ks_p = sum(v for t, v in temp_mass.items() if t >= lo)
        elif lo is not None:
            ks_p = sum(v for t, v in temp_mass.items() if lo <= t <= hi)
        else:
            ks_p = 0.0
        rows.append({
            "bucket": label, "lo": lo, "hi": hi,
            "series": e["series"],
            "price_now": round(p_now, 2),
            "price_3h_ago": round(p_3h, 2),
            "momentum": round(p_now - p_3h, 2),
            "ks_mapped_pct": round(ks_p * 100, 1),
            "last_trade": e["meta"].get("last"),
            "bid": e["meta"].get("bid"), "ask": e["meta"].get("ask"),
        })

    pm_total = sum(r["price_now"] for r in rows) or 1.0
    for r in rows:
        base = W_PM * (r["price_now"] / pm_total) + W_KS * (r["ks_mapped_pct"] / 100)
        mom = max(-0.5, min(0.5, W_MOMENTUM * r["momentum"] / 100))
        r["score"] = base * (1 + mom)
    total = sum(r["score"] for r in rows) or 1.0
    for r in rows:
        r["prob_pct"] = round(100 * r["score"] / total, 1)
        del r["score"]
    rows.sort(key=lambda r: (r["lo"] if r["lo"] is not None
                             else (r["hi"] - 1 if r["hi"] is not None else 999)))

    ks_rows = []
    for label, e in ks.items():
        ks_rows.append({
            "bucket": label,
            "series": e["series"],
            "price_now": round(last_value(e["series"]), 2),
            "momentum": round(last_value(e["series"]) - last_value(e["series"], back=3), 2),
        })

    pick = max(rows, key=lambda r: r["prob_pct"]) if rows else None
    return {"name": cfg["name"], "code": cfg["code"],
            "pm": rows, "ks": ks_rows, "pick": pick}


def fmt_series(series):
    return "→".join("·" if v is None else f"{v:g}" for v in series)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    d = date.fromisoformat(args[0]) if args else config.analysis_date()
    snapshots = load_snapshots(d)
    times = [s["ts_minsk"] for s in snapshots]

    result = {"date": d.isoformat(), "snapshots": len(snapshots),
              "times_minsk": times, "cities": {}}
    for slug in config.CITIES:
        result["cities"][slug] = analyze_city(snapshots, slug)

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return

    print(f"=== Анализ рынков за {d.isoformat()} ({len(snapshots)} снапшотов, "
          f"{times[0]} … {times[-1]} Минск) ===")
    for slug, c in result["cities"].items():
        print(f"\n--- {c['name']} ({c['code']}) ---")
        print("  Polymarket (цена ¢: траектория | сейчас | Δ3ч | Kalshi→PM % | оценка %):")
        for r in c["pm"]:
            print(f"    {r['bucket']:>10}: {fmt_series(r['series'])} | {r['price_now']:g} | "
                  f"{r['momentum']:+g} | {r['ks_mapped_pct']:g}% | {r['prob_pct']:g}%")
        print("  Kalshi:")
        for r in c["ks"]:
            print(f"    {r['bucket']:>14}: {fmt_series(r['series'])} | {r['price_now']:g} | {r['momentum']:+g}")
        if c["pick"]:
            p = c["pick"]
            print(f"  >> Алгоритм: {p['bucket']} — {p['prob_pct']:g}% "
                  f"(цена сейчас {p['price_now']:g}¢, bid {p['bid']} / ask {p['ask']})")


if __name__ == "__main__":
    main()
