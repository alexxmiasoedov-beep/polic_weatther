"""Обзорный сбор ВСЕХ температурных рынков Polymarket (запрос владельца 07.09).

Цель — за 2-3 недели увидеть по каждому городу PM: предсказуемость,
«ленивость» рынка (как часто утренний лидер побеждает, когда цена
пересекает 70¢) — и найти новые города для торговли.

Экономия минут Actions: только цены (без погоды), только сегодняшний
локальный рынок, параллельные запросы (8 потоков ~15-20 сек на всё),
запуск раз в 2 часа (чётный час UTC, вызов из collector.py). Список
городов автообновляется раз в сутки через Gamma API (discover) и
кэшируется в observe_cities.json. Данные — observe_data/<дата UTC>.jsonl,
одна строка на снапшот со всеми городами.

Города основной семёрки и пилота здесь не дублируются — у них свой
плотный сбор.
"""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import config
import pilot

CITIES_CACHE = Path(__file__).parent / "observe_cities.json"
OUT_DIR = Path(__file__).parent / "observe_data"
SKIP = set(config.CITIES) | set(pilot.PILOT_CITIES)


def fetch_json(url: str, tries: int = 2):
    last_err = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "weather-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last_err = e
    print(f"WARN observe: {url}: {last_err}", file=sys.stderr)
    return None


# Кандидаты (скрининг 44 городов 01.09 + расширение); листинг Gamma
# капится на 100 событий, поэтому дискавери — пробой слагов на сегодня.
CANDIDATES = [
    "atlanta", "austin", "houston", "los-angeles", "miami", "seattle",
    "san-francisco", "dallas", "nyc", "chicago", "denver", "phoenix",
    "boston", "philadelphia", "washington-dc", "las-vegas",
    "singapore", "kuala-lumpur", "tel-aviv", "hong-kong", "taipei",
    "seoul", "busan", "tokyo", "osaka", "beijing", "shanghai", "wuhan",
    "chongqing", "bangkok", "manila", "jakarta", "mumbai", "delhi",
    "dubai", "riyadh", "istanbul", "ankara", "moscow", "warsaw",
    "helsinki", "stockholm", "oslo", "copenhagen", "berlin", "munich",
    "amsterdam", "brussels", "paris", "london", "dublin", "madrid",
    "barcelona", "lisbon", "milan", "rome", "athens", "vienna",
    "prague", "zurich", "toronto", "vancouver", "montreal",
    "mexico-city", "sao-paulo", "rio-de-janeiro", "buenos-aires",
    "santiago", "lima", "bogota", "sydney", "melbourne", "brisbane",
    "perth", "auckland", "wellington", "cairo", "lagos", "nairobi",
    "johannesburg", "cape-town",
]


def discover() -> list:
    """Города с активным рынком на сегодня — пробой слагов кандидатов."""
    d = datetime.now(timezone.utc).date()
    found = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for slug, pm in ex.map(fetch_city, [(c, d) for c in CANDIDATES]):
            if pm:
                found.append(slug)
    return sorted(found)


def load_cities() -> list:
    today = datetime.now(timezone.utc).date().isoformat()
    if CITIES_CACHE.exists():
        cached = json.loads(CITIES_CACHE.read_text(encoding="utf-8"))
        if cached.get("updated") == today and cached.get("cities"):
            return cached["cities"]
    cities = discover()
    if cities:
        CITIES_CACHE.write_text(json.dumps({"updated": today, "cities": cities},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
        return cities
    if CITIES_CACHE.exists():  # сеть подвела — работаем по старому списку
        return json.loads(CITIES_CACHE.read_text(encoding="utf-8")).get("cities", [])
    return []


def fetch_city(args):
    slug, d = args
    ev = fetch_json(f"{config.GAMMA_API}/events?slug={config.pm_event_slug(slug, d)}")
    if not ev:
        return slug, None
    buckets = []
    for m in ev[0].get("markets", []):
        last, bid, ask = m.get("lastTradePrice"), m.get("bestBid"), m.get("bestAsk")
        buckets.append({
            "bucket": m.get("groupItemTitle"),
            "last": round(last * 100, 2) if last is not None else None,
            "bid": round(bid * 100, 2) if bid is not None else None,
            "ask": round(ask * 100, 2) if ask is not None else None,
        })
    return slug, ({"buckets": buckets} if buckets else None)


def collect():
    now = datetime.now(timezone.utc)
    # Дата рынка: для обзора берём дату UTC — почти у всех городов их
    # «сегодня» в чётные часы UTC совпадает или отстаёт на сутки; для
    # скрининга ленивости этого достаточно, резолв виден по 99¢.
    d = now.date()
    cities = [c for c in load_cities() if c not in SKIP]
    if not cities:
        print("WARN observe: список городов пуст", file=sys.stderr)
        return
    snap = {"ts_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cities": {}}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for slug, pm in ex.map(fetch_city, [(c, d) for c in cities]):
            if pm:
                snap["cities"][slug] = pm
    OUT_DIR.mkdir(exist_ok=True)
    with (OUT_DIR / f"{d.isoformat()}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    print(f"observe: {len(snap['cities'])} городов -> observe_data/{d.isoformat()}.jsonl")


if __name__ == "__main__":
    collect()
