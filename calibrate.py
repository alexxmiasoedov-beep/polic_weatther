"""Калибровка погодных моделей по станциям резолва.

Раз в сутки (из ежедневного Routine) записывает по каждому городу:
- факт: максимум температуры за локальные сутки по наблюдениям станции
  NWS, по которой резолвится Polymarket;
- ошибки: прогноз каждой модели из последнего wx-снапшота этой даты
  минус факт.

Копится в calibration/actuals.jsonl; скользящие поправки (средняя
ошибка за последние 14 дней, минимум 5 дней данных) — в
calibration/bias.json. Анализ вычитает поправку из прогноза модели.

Запуск: python3 calibrate.py [YYYY-MM-DD]
Без аргумента — позавчерашний рынок (analysis_date - 1): его локальные
сутки в США уже закончились к моменту ежедневного анализа.
"""
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config

ROLL_DAYS = 14   # окно скользящей поправки
MIN_DAYS = 5     # раньше этого поправки не применяются


def fetch_json(url: str, tries: int = 3):
    last_err = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "weather-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last_err = e
    print(f"WARN: {url}: {last_err}", file=sys.stderr)
    return None


def actual_max_f(city_cfg: dict, d: date):
    """Максимум за локальные сутки d по наблюдениям станции, °F."""
    tz = ZoneInfo(city_cfg["tz"])
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = start + timedelta(days=1)
    o = fetch_json(
        f"https://api.weather.gov/stations/{city_cfg['station']}/observations"
        f"?start={start.isoformat()}&end={end.isoformat()}&limit=500")
    if not o:
        return None
    temps = [f["properties"]["temperature"]["value"] for f in o.get("features", [])
             if f["properties"]["temperature"]["value"] is not None]
    if not temps:
        return None
    return round(max(temps) * 9 / 5 + 32, 1)


def forecast_wx(d: date):
    """wx из последнего снапшота даты d, где он есть (≈19:05 Минска)."""
    f = Path(__file__).parent / "data" / f"{d.isoformat()}.jsonl"
    if not f.exists():
        return {}
    result = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        snap = json.loads(line)
        if snap.get("market_date") != d.isoformat():
            continue
        for slug, c in snap.get("cities", {}).items():
            if c.get("wx"):
                result[slug] = c["wx"]
    return result


def main():
    if len(sys.argv) > 1:
        d = date.fromisoformat(sys.argv[1])
    else:
        d = config.analysis_date() - timedelta(days=1)
    cal_dir = Path(__file__).parent / "calibration"
    cal_dir.mkdir(exist_ok=True)
    actuals_f = cal_dir / "actuals.jsonl"

    existing = set()
    if actuals_f.exists():
        for line in actuals_f.read_text(encoding="utf-8").splitlines():
            existing.add(json.loads(line)["date"])
    if d.isoformat() in existing:
        print(f"{d}: уже записано, пропуск")
    else:
        wx_all = forecast_wx(d)
        rec = {"date": d.isoformat(), "cities": {}}
        for slug, c in config.CITIES.items():
            actual = actual_max_f(c, d)
            wx = wx_all.get(slug) or {}
            errors = {m: round(v - actual, 1) for m, v in wx.items()
                      if actual is not None and v is not None}
            rec["cities"][slug] = {"actual_max_f": actual, "forecast": wx, "error": errors}
            print(f"{c['code']}: факт {actual}°F, ошибки {errors}")
        with actuals_f.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # скользящие поправки
    records = [json.loads(l) for l in actuals_f.read_text(encoding="utf-8").splitlines()]
    records = sorted(records, key=lambda r: r["date"])[-ROLL_DAYS:]
    bias = {}
    for slug in config.CITIES:
        per_model = {}
        for r in records:
            for m, e in r["cities"].get(slug, {}).get("error", {}).items():
                per_model.setdefault(m, []).append(e)
        bias[slug] = {m: {"bias": round(sum(v) / len(v), 1), "days": len(v)}
                      for m, v in per_model.items() if len(v) >= MIN_DAYS}
    (cal_dir / "bias.json").write_text(
        json.dumps({"updated": date.today().isoformat(), "roll_days": ROLL_DAYS,
                    "note": "bias = средняя (прогноз - факт); калиброванный прогноз = прогноз - bias",
                    "cities": bias}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: bias.json обновлён ({len(records)} дней в окне)")


if __name__ == "__main__":
    main()
