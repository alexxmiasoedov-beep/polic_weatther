"""Снимок цен рынков «Highest temperature in …» на Polymarket и Kalshi.

Запускается каждый час в :05 (GitHub Actions). Пишет одну JSON-строку
в data/<дата-рынка>.jsonl со всеми городами и корзинами обеих площадок.
Цены — в центах (0–100), как в ручной таблице.

Запуск: python collector.py [YYYY-MM-DD]  (без аргумента — дата по правилу 19:05)
"""
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, date
from pathlib import Path

import config


def fetch_json(url: str, tries: int = 3):
    last_err = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "weather-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 — сеть, ретраим
            last_err = e
    print(f"WARN: {url}: {last_err}", file=sys.stderr)
    return None


def cents(x):
    """Доли доллара (str|float) -> центы, округлённые до 0.01."""
    if x is None:
        return None
    try:
        return round(float(x) * 100, 2)
    except (TypeError, ValueError):
        return None


def fetch_polymarket(city_slug: str, d: date):
    slug = config.pm_event_slug(city_slug, d)
    events = fetch_json(f"{config.GAMMA_API}/events?slug={slug}")
    if not events:
        return None
    buckets = []
    for m in events[0].get("markets", []):
        try:
            yes_price = json.loads(m.get("outcomePrices") or "[]")
            yes_price = float(yes_price[0]) if yes_price else None
        except (ValueError, IndexError):
            yes_price = None
        buckets.append({
            "bucket": m.get("groupItemTitle") or m.get("question"),
            "last": cents(m.get("lastTradePrice")),
            "bid": cents(m.get("bestBid")),
            "ask": cents(m.get("bestAsk")),
            "mid": cents(yes_price),
            "volume": m.get("volumeNum"),
            "closed": m.get("closed"),
        })
    return {"slug": slug, "buckets": buckets}


def fetch_kalshi(series: str, d: date):
    ticker = config.kalshi_event_ticker(series, d)
    data = fetch_json(f"{config.KALSHI_API}/markets?event_ticker={ticker}&limit=100")
    if not data or not data.get("markets"):
        return None
    buckets = []
    for m in data["markets"]:
        buckets.append({
            "bucket": m.get("yes_sub_title"),
            "last": cents(m.get("last_price_dollars")),
            "bid": cents(m.get("yes_bid_dollars")),
            "ask": cents(m.get("yes_ask_dollars")),
            "floor": m.get("floor_strike"),
            "cap": m.get("cap_strike"),
            "strike_type": m.get("strike_type"),
            "volume": m.get("volume_fp"),
            "status": m.get("status"),
        })
    # упорядочить по температуре
    buckets.sort(key=lambda b: (b["floor"] if b["floor"] is not None
                                else (b["cap"] - 1 if b["cap"] is not None else 999)))
    return {"event_ticker": ticker, "buckets": buckets}


def main():
    if len(sys.argv) > 1:
        d = date.fromisoformat(sys.argv[1])
    else:
        d = config.target_date()
    now_utc = datetime.now(timezone.utc)
    snapshot = {
        "ts_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_minsk": config.minsk_now().strftime("%Y-%m-%d %H:%M"),
        "market_date": d.isoformat(),
        "cities": {},
    }
    for slug, c in config.CITIES.items():
        pm = fetch_polymarket(slug, d)
        ks = fetch_kalshi(c["kalshi_series"], d)
        snapshot["cities"][slug] = {
            "name": c["name"], "code": c["code"],
            "polymarket": pm, "kalshi": ks,
        }
        print(f"{c['code']}: PM {len(pm['buckets']) if pm else 0} корзин, "
              f"KS {len(ks['buckets']) if ks else 0} корзин")

    out = Path(__file__).parent / "data" / f"{d.isoformat()}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    print(f"OK: снапшот {snapshot['ts_utc']} -> {out}")


if __name__ == "__main__":
    main()
