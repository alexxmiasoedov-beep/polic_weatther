"""Часовые METAR всех станций проекта — в базу (06.10, по решению владельца).

До 06.10 факт станции брался живьём только на срезах и в файлы не
попадал; калибровка снимала суточный максимум задним числом. Теперь
каждый запуск сборщика дописывает новые часовые (и SPECI) отсчёты в
metar_data/<дата-UTC>.jsonl: по одной строке на (станция, время
наблюдения), без дублей. Температура — с десятыми из T-группы, как
её печатает aviationweather (поле temp).

Запуск: python metar.py  (или из collector.py в фоне)
"""
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import config

OUT_DIR = Path(__file__).parent / "metar_data"
API = "https://aviationweather.gov/api/data/metar"


def stations() -> list:
    st = [c["station"] for c in config.CITIES.values()]
    try:
        import pilot
        st += [c["station"] for c in pilot.PILOT_CITIES.values() if c.get("station")]
    except Exception as e:  # noqa: BLE001
        print(f"WARN metar: pilot stations: {e}", file=sys.stderr)
    seen, out = set(), []
    for s in st:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def fetch(ids: list, hours: int = 3) -> list:
    url = f"{API}?ids={','.join(ids)}&format=json&hours={hours}"
    last_err = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "weather-monitor/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r) or []
        except Exception as e:  # noqa: BLE001
            last_err = e
    print(f"WARN metar: {url}: {last_err}", file=sys.stderr)
    return []


def _known(path: Path) -> set:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            keys.add((r["station"], r["obs_utc"]))
        except (ValueError, KeyError):
            continue
    return keys


def collect(hours: int = 3) -> int:
    OUT_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    obs = fetch(stations(), hours)
    by_day: dict = {}
    for o in obs:
        t = o.get("reportTime") or ""
        if not t:
            continue
        obs_utc = t[:19].replace(" ", "T") + "Z"
        by_day.setdefault(obs_utc[:10], []).append({
            "ts_utc": now, "station": o.get("icaoId"), "obs_utc": obs_utc,
            "temp_c": o.get("temp"), "dewp_c": o.get("dewp"),
            "speci": (o.get("rawOb") or "").startswith("SPECI"),
            "raw": o.get("rawOb"),
        })
    added = 0
    for day, rows in sorted(by_day.items()):
        path = OUT_DIR / f"{day}.jsonl"
        known = _known(path)
        new = [r for r in rows if (r["station"], r["obs_utc"]) not in known]
        new.sort(key=lambda r: (r["obs_utc"], r["station"]))
        if not new:
            continue
        with path.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        added += len(new)
    print(f"metar: {len(obs)} отсчётов с API, новых {added}")
    return added


if __name__ == "__main__":
    collect()
