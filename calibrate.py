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
    """Максимум за локальные сутки d по станции, целые °F.

    Рынок резолвится по колонке Temp «Show Hourly Data» на
    weather.gov/wrh/timeseries — это ЧАСОВЫЕ METAR (точность 0.1°C),
    округлённые до целых °F. 5-минутные отсчёты API идут в целых °C и
    завышают максимум (38°C=100.4°F при METAR-максимуме 99.0°F, кейс
    KAUS 31.08), поэтому берём только наблюдения с rawMessage-METAR.
    """
    tz = ZoneInfo(city_cfg["tz"])
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = start + timedelta(days=1)
    o = fetch_json(
        f"https://api.weather.gov/stations/{city_cfg['station']}/observations"
        f"?start={start.isoformat()}&end={end.isoformat()}&limit=500")
    if not o:
        return None
    temps = []
    for f in o.get("features", []):
        p = f["properties"]
        t = p["temperature"]["value"]
        raw = p.get("rawMessage") or ""
        if t is not None and raw and not raw.startswith("SPECI"):
            temps.append(t)
    if not temps:
        return None
    return round(max(temps) * 9 / 5 + 32)


def actual_max_intl_c(city_cfg: dict, d: date):
    """Максимум за локальные сутки по METAR intl-станции (IEM), целые °C."""
    tz = ZoneInfo(city_cfg["tz"])
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
           f"station={city_cfg['station']}&data=tmpc"
           f"&year1={d.year}&month1={d.month}&day1={max(d.day - 1, 1)}"
           f"&year2={d.year}&month2={d.month}&day2={d.day + 1 if d.day < 28 else d.day}"
           "&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&trace=T&report_type=3")
    last_err = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "weather-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                text = r.read().decode()
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    else:
        print(f"WARN: IEM {city_cfg['station']}: {last_err}", file=sys.stderr)
        return None
    best = None
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 3 or parts[2] in ("M", ""):
            continue
        try:
            t = float(parts[2])
            dt = datetime.strptime(parts[1], "%Y-%m-%d %H:%M").replace(
                tzinfo=ZoneInfo("UTC")).astimezone(tz)
        except ValueError:
            continue
        if dt.date() == d:
            best = t if best is None else max(best, t)
    return round(best) if best is not None else None


def forecast_wx(d: date):
    """wx из последних снапшотов даты d: основные города + пилотные."""
    result = {}
    f = Path(__file__).parent / "data" / f"{d.isoformat()}.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            snap = json.loads(line)
            if snap.get("market_date") != d.isoformat():
                continue
            for slug, c in snap.get("cities", {}).items():
                if c.get("wx"):
                    result[slug] = c["wx"]
    pf = Path(__file__).parent / "pilot_data" / f"{d.isoformat()}.jsonl"
    if pf.exists():
        for line in pf.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("wx"):
                result[rec["city"]] = rec["wx"]
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
        import pilot
        wx_all = forecast_wx(d)
        rec = {"date": d.isoformat(), "cities": {}}
        all_cities = list(config.CITIES.items()) + list(pilot.PILOT_CITIES.items())
        for slug, c in all_cities:
            unit = c.get("unit", "F")
            if unit == "F":
                actual = actual_max_f(c, d)
            else:
                actual = actual_max_intl_c(c, d)
            wx = wx_all.get(slug) or {}
            errors = {m: round(v - actual, 1) for m, v in wx.items()
                      if actual is not None and v is not None}
            rec["cities"][slug] = {f"actual_max_{unit.lower()}": actual,
                                   "unit": unit, "forecast": wx, "error": errors}
            print(f"{c['code']}: факт {actual}°{unit}, ошибки {errors}")
        with actuals_f.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # скользящие поправки
    records = [json.loads(l) for l in actuals_f.read_text(encoding="utf-8").splitlines()]
    records = sorted(records, key=lambda r: r["date"])[-ROLL_DAYS:]
    import pilot as _p
    bias = {}
    for slug in list(config.CITIES) + list(_p.PILOT_CITIES):
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
