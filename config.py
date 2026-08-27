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
CITIES = {
    "atlanta": {
        "name": "Атланта", "code": "ATL",
        "kalshi_series": "KXHIGHTATL", "ks_pm_offset": 1.0,
    },
    "austin": {
        "name": "Остин", "code": "AUS",
        "kalshi_series": "KXHIGHAUS", "ks_pm_offset": 0.5,
    },
    "houston": {
        "name": "Хьюстон", "code": "HOU",
        "kalshi_series": "KXHIGHTHOU", "ks_pm_offset": 1.0,
    },
    "los-angeles": {
        "name": "Лос-Анджелес", "code": "LAX",
        "kalshi_series": "KXHIGHLAX", "ks_pm_offset": 1.0,
    },
    "miami": {
        "name": "Майами", "code": "MIA",
        "kalshi_series": "KXHIGHMIA", "ks_pm_offset": 0.0,
    },
    "seattle": {
        "name": "Сиэтл", "code": "SEA",
        "kalshi_series": "KXHIGHTSEA", "ks_pm_offset": 1.0,
    },
    "san-francisco": {
        "name": "Сан-Франциско", "code": "SFO",
        "kalshi_series": "KXHIGHTSFO", "ks_pm_offset": 2.0,
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
