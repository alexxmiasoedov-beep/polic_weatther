"""Конфигурация мониторинга рынков максимальной температуры.

Города, идентификаторы рынков на Polymarket и Kalshi, правила выбора
целевой даты. Все времена — UTC, «домашняя» таймзона — Europe/Minsk (UTC+3).
"""
from datetime import datetime, timedelta, timezone, date

MINSK_UTC_OFFSET = 3  # Минск круглый год UTC+3, DST нет

# ks_pm_offset — на сколько °F резолв Kalshi исторически выше резолва
# Polymarket (медиана по ручной истории 21–26 августа 2026,
# history/manual_history.json). Источники резолва у площадок разные
# (Kalshi — The Weather Company, Polymarket — климатический отчёт NWS),
# поэтому смещение устойчиво не нулевое.
# lat/lon — аэропортовые климатические станции NWS (по ним резолвится
# Polymarket); nws_grid — готовый gridpoint-URL прогноза NWS для станции.
CITIES = {
    "atlanta": {
        "station": "KATL", "tz": "America/New_York",
        "name": "Атланта", "code": "ATL",
        "kalshi_series": "KXHIGHTATL", "ks_pm_offset": 1.0,
        "lat": 33.630, "lon": -84.442,
        "nws_grid": "https://api.weather.gov/gridpoints/FFC/49,81/forecast",
    },
    "austin": {
        # Бергстром (site=kaus в описании рынка). Факт считать ТОЛЬКО по
        # часовым METAR (:53): 5-минутные отсчёты идут в целых °C и
        # завышают максимум (38°C=100.4°F при METAR-максимуме 99.0°F)
        "station": "KAUS", "tz": "America/Chicago",
        "name": "Остин", "code": "AUS",
        "kalshi_series": "KXHIGHAUS", "ks_pm_offset": 0.5,
        "lat": 30.183, "lon": -97.680,
        "nws_grid": "https://api.weather.gov/gridpoints/EWX/158,87/forecast",
    },
    "houston": {
        # Hobby, не IAH (site=khou в описании рынка; факт 31.08 90.0°F
        # сошёлся с корзиной 90-91, у KIAH было 95.0°F)
        "station": "KHOU", "tz": "America/Chicago",
        "name": "Хьюстон", "code": "HOU",
        "kalshi_series": "KXHIGHTHOU", "ks_pm_offset": 1.0,
        "lat": 29.638, "lon": -95.282,
        "nws_grid": "https://api.weather.gov/gridpoints/HGX/66,89/forecast",
    },
    "los-angeles": {
        "station": "KLAX", "tz": "America/Los_Angeles",
        "name": "Лос-Анджелес", "code": "LAX",
        "kalshi_series": "KXHIGHLAX", "ks_pm_offset": 1.0,
        "lat": 33.938, "lon": -118.389,
        "nws_grid": "https://api.weather.gov/gridpoints/LOX/149,41/forecast",
    },
    "miami": {
        "station": "KMIA", "tz": "America/New_York",
        "name": "Майами", "code": "MIA",
        "kalshi_series": "KXHIGHMIA", "ks_pm_offset": 0.0,
        "lat": 25.788, "lon": -80.317,
        "nws_grid": "https://api.weather.gov/gridpoints/MFL/105,51/forecast",
    },
    "seattle": {
        "station": "KSEA", "tz": "America/Los_Angeles",
        "name": "Сиэтл", "code": "SEA",
        "kalshi_series": "KXHIGHTSEA", "ks_pm_offset": 1.0,
        "lat": 47.445, "lon": -122.314,
        "nws_grid": "https://api.weather.gov/gridpoints/SEW/124,60/forecast",
    },
    "san-francisco": {
        "station": "KSFO", "tz": "America/Los_Angeles",
        "name": "Сан-Франциско", "code": "SFO",
        "kalshi_series": "KXHIGHTSFO", "ks_pm_offset": 2.0,
        "lat": 37.620, "lon": -122.365,
        "nws_grid": "https://api.weather.gov/gridpoints/MTR/85,98/forecast",
    },
}

GAMMA_API = "https://gamma-api.polymarket.com"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"

MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july",
             "august", "september", "october", "november", "december"]
MONTHS_KALSHI = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG",
                 "SEP", "OCT", "NOV", "DEC"]


def minsk_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=MINSK_UTC_OFFSET)


def target_date(now: datetime | None = None) -> date:
    """Дата рынка, чьи цены собираем в данный момент.

    Цикл сбора по дате D идёт с 19:05 Минска дня D-1 до 19:05 дня D.
    """
    now = now or minsk_now()
    return (now - timedelta(hours=19, minutes=5)).date() + timedelta(days=1)


def analysis_date(now: datetime | None = None) -> date:
    """Дата рынка, по которому в 19:05+ делается прогноз (цикл только
    что завершился, рынок резолвится сегодня вечером по US-времени)."""
    return target_date(now) - timedelta(days=1)


def pm_event_slug(city_slug: str, d: date) -> str:
    return f"highest-temperature-in-{city_slug}-on-{MONTHS_EN[d.month - 1]}-{d.day}-{d.year}"


def kalshi_event_ticker(series: str, d: date) -> str:
    return f"{series}-{d.year % 100}{MONTHS_KALSHI[d.month - 1]}{d.day:02d}"
