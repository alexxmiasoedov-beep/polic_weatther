"""Пилот: 4 дополнительных рынка Polymarket (без Kalshi).

Города выбраны скринингом 44 рынков 01.09 (см. CLAUDE.md): Сингапур,
Куала-Лумпур, Тель-Авив (σ дневного максимума 1.0–1.4°C — мировой топ
предсказуемости) и Даллас (персистентность корзины 50%).

Каждый почасовой запуск collector.py снимает по каждому пилотному городу
цены PM и прогнозы моделей для всех нерезолвленных рынков «локальное
сегодня» и «локальное завтра». Строки пишутся в
pilot_data/<market_date>.jsonl: {"ts_utc", "city", "market_date",
"pm": {...}, "wx": {...}}.

Международные корзины — по 1°C (напр. «33°C»), резолв по часовым METAR
станции (NOAA wrh/timeseries), как и у США. У intl-городов нет NWS и
NBM/HRRR — только глобальные модели Open-Meteo.
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config

PILOT_CITIES = {
    "singapore": {
        "name": "Сингапур", "code": "SIN", "unit": "C",
        "station": "WSSS", "tz": "Asia/Singapore",
        "lat": 1.350, "lon": 103.994, "nws_grid": None,
    },
    "kuala-lumpur": {
        "name": "Куала-Лумпур", "code": "KUL", "unit": "C",
        "station": "WMKK", "tz": "Asia/Kuala_Lumpur",
        "lat": 2.746, "lon": 101.707, "nws_grid": None,
    },
    "tel-aviv": {
        "name": "Тель-Авив", "code": "TLV", "unit": "C",
        "station": "LLBG", "tz": "Asia/Jerusalem",
        "lat": 32.000, "lon": 34.885, "nws_grid": None,
    },
    "dallas": {
        # Love Field, не DFW (site=kdal в описании рынка)
        "name": "Даллас", "code": "DAL", "unit": "F",
        "station": "KDAL", "tz": "America/Chicago",
        "lat": 32.847, "lon": -96.852,
        "nws_grid": "https://api.weather.gov/gridpoints/FWD/87,107/forecast",
    },
    # --- Тихий сбор (наблюдение, БЕЗ прогнозов; добавлены 07.09 по
    # запросу владельца): σ за 45 дней у всех плохая (WLG 2.4/перс 9%,
    # MOW 4.2/2%, MIL 3.1/32%), решение о прогнозах — после 2 недель
    # накопления bias и наблюдения за ленивостью рынков. У Веллингтона
    # объём ~$90k/день — главный кандидат.
    "wellington": {
        "name": "Веллингтон", "code": "WLG", "unit": "C",
        "station": "NZWN", "tz": "Pacific/Auckland",
        "lat": -41.327, "lon": 174.805, "nws_grid": None,
    },
    "moscow": {
        # Резолв по Внуково (site в описании рынка)
        "name": "Москва", "code": "MOW", "unit": "C",
        "station": "UUWW", "tz": "Europe/Moscow",
        "lat": 55.596, "lon": 37.267, "nws_grid": None,
    },
    "milan": {
        # Резолв по Мальпенсе
        "name": "Милан", "code": "MIL", "unit": "C",
        "station": "LIMC", "tz": "Europe/Rome",
        "lat": 45.630, "lon": 8.723, "nws_grid": None,
    },
    # --- Тихий сбор №2 (10.09): кандидаты из обзорного сбора 41 города —
    # утренний лидер побеждал 3/3 (2/2 Денвер) при цене 39-66¢. Выборка
    # крошечная (3 дня) — цель: копить bias и ленивость, решение через
    # 2 недели. Станции резолва СВЕРЕНЫ с описаниями рынков PM 10.09
    # (weather.gov/wrh/timeseries?site=…): Лондон — London City EGLC (НЕ
    # Хитроу), Денвер — Buckley SFB KBKF (НЕ KDEN); остальные — главные
    # аэропорты. IEM отдаёт METAR по BKF и EGLC.
    "toronto": {
        "name": "Торонто", "code": "YYZ", "unit": "C",
        "station": "CYYZ", "tz": "America/Toronto",
        "lat": 43.677, "lon": -79.631, "nws_grid": None,
    },
    "london": {
        "name": "Лондон", "code": "LCY", "unit": "C",
        "station": "EGLC", "tz": "Europe/London",
        "lat": 51.505, "lon": 0.055, "nws_grid": None,
    },
    "amsterdam": {
        "name": "Амстердам", "code": "AMS", "unit": "C",
        "station": "EHAM", "tz": "Europe/Amsterdam",
        "lat": 52.310, "lon": 4.768, "nws_grid": None,
    },
    "helsinki": {
        "name": "Хельсинки", "code": "HEL", "unit": "C",
        "station": "EFHK", "tz": "Europe/Helsinki",
        "lat": 60.317, "lon": 24.963, "nws_grid": None,
    },
    "denver": {
        "name": "Денвер", "code": "BKF", "unit": "F",
        "station": "KBKF", "tz": "America/Denver",
        "lat": 39.702, "lon": -104.752,
        "nws_grid": "https://api.weather.gov/gridpoints/BOU/71,59/forecast",
    },
}

OM_MODELS_GLOBAL = ("best_match,ecmwf_ifs025,gfs_seamless,icon_seamless,"
                    "ecmwf_aifs025_single,ukmo_seamless")
OM_MODELS_US = OM_MODELS_GLOBAL + ",ncep_nbm_conus"


def fetch_json(url: str, tries: int = 3):
    last_err = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "weather-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last_err = e
    print(f"WARN pilot: {url}: {last_err}", file=sys.stderr)
    return None


def fetch_pm(city_slug: str, d) -> dict | None:
    ev = fetch_json(f"{config.GAMMA_API}/events?slug={config.pm_event_slug(city_slug, d)}")
    if not ev:
        return None
    buckets = []
    for m in ev[0].get("markets", []):
        last = m.get("lastTradePrice")
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        buckets.append({
            "bucket": m.get("groupItemTitle"),
            "last": round(last * 100, 2) if last is not None else None,
            "bid": round(bid * 100, 2) if bid is not None else None,
            "ask": round(ask * 100, 2) if ask is not None else None,
        })
    return {"buckets": buckets} if buckets else None


def fetch_wx(c: dict, d) -> dict | None:
    wx = {}
    if c["nws_grid"]:
        nws = fetch_json(c["nws_grid"])
        if nws:
            for p in nws.get("properties", {}).get("periods", []):
                if p.get("isDaytime") and p.get("startTime", "")[:10] == d.isoformat():
                    wx["nws"] = p.get("temperature")
                    break
    unit = "fahrenheit" if c["unit"] == "F" else "celsius"
    models = OM_MODELS_US if c["nws_grid"] else OM_MODELS_GLOBAL
    om = fetch_json(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={c['lat']}&longitude={c['lon']}"
        f"&daily=temperature_2m_max&temperature_unit={unit}"
        "&timezone=auto&cell_selection=land"
        f"&start_date={d.isoformat()}&end_date={d.isoformat()}"
        f"&models={models}")
    if om:
        for k, v in om.get("daily", {}).items():
            if k.startswith("temperature_2m_max") and v:
                model = k.replace("temperature_2m_max", "").lstrip("_") or "best_match"
                wx["om_" + model] = v[0]
    return wx or None


def market_resolved(city_slug: str, d) -> bool:
    f = Path(__file__).parent / "pilot_data" / f"{d.isoformat()}.jsonl"
    if not f.exists():
        return False
    for line in reversed(f.read_text(encoding="utf-8").splitlines()):
        rec = json.loads(line)
        if rec["city"] == city_slug:
            pm = rec.get("pm") or {}
            return any((b.get("last") or 0) >= 97 for b in pm.get("buckets", []))
    return False


def collect(with_wx: bool = True):
    out_dir = Path(__file__).parent / "pilot_data"
    out_dir.mkdir(exist_ok=True)
    now_utc = datetime.now(timezone.utc)
    for slug, c in PILOT_CITIES.items():
        local_today = now_utc.astimezone(ZoneInfo(c["tz"])).date()
        for d in (local_today, local_today + timedelta(days=1)):
            if d < local_today or market_resolved(slug, d):
                continue
            pm = fetch_pm(slug, d)
            if not pm:
                continue  # рынок ещё не создан или уже убран
            rec = {
                "ts_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ts_minsk": config.minsk_now().strftime("%Y-%m-%d %H:%M"),
                "city": slug, "code": c["code"], "market_date": d.isoformat(),
                "pm": pm, "wx": fetch_wx(c, d) if with_wx else None,
            }
            with (out_dir / f"{d.isoformat()}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"pilot {c['code']} {d}: PM {len(pm['buckets'])} корзин")


if __name__ == "__main__":
    collect()
