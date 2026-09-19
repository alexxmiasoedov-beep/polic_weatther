"""Портфель ФАВОРИТОВ (ревизия 16.09) — единственная механическая стратегия денег.

Калибровка рынка по 273 город-дням (favorites_backtest.py): за 20-48 ч до
закрытия лидер PM по 50-70¢ выигрывает 66% и даёт +15% на сделку по ask;
корзины 10-30¢ переоценены (−15…−30%). Поэтому: каждый вечер 19:20 Минска
покупаем лидера каждого рынка на завтра, если он стоит 50-70¢, по ASK,
одинаковым размером, без анализа моделей, Kalshi и профилей.

  python3 favorites.py --write [YYYY-MM-DD]   — записать входы (по умолчанию завтра)
  python3 favorites.py --resolve YYYY-MM-DD   — проставить winner/hit/pnl
  python3 favorites.py --stats                — журнал по дням и городам
  python3 favorites.py --tg <файл> [дата]     — текст для Telegram (с --write)
  python3 favorites.py --rank                 — пересчитать активный список городов (понедельник)

Ревизия 19.09: вход только по городам из calibration/favorites_cities.json
(скользящий бэктест: ≥7 сделок и ≥+8%/сделку); остальные — reference.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
LO, HI, MAX_ASK, MAX_SPREAD = 50, 70, 72, 8
SIZE = 1.0
CITIES_FILE = ROOT / "calibration" / "favorites_cities.json"
MIN_N, MIN_ROI = 7, 0.08  # город активен, если в скользящем бэктесте ≥7 сделок и ≥+8%/сделку


def active_cities():
    """Ревизия 19.09: деньги только по городам из calibration/favorites_cities.json;
    остальные пишутся как reference (в журнал денег не входят)."""
    if CITIES_FILE.exists():
        return set(json.loads(CITIES_FILE.read_text(encoding="utf-8")).get("active", []))
    return None  # файла нет — все города активны (поведение до 19.09)


def snapshots(d):
    """{slug: (ts, buckets)} — последняя запись на дату из data/ и pilot_data/."""
    out = {}
    f = ROOT / "data" / f"{d}.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("market_date", d) != d:
                continue
            for slug, c in r.get("cities", {}).items():
                b = (c.get("polymarket") or {}).get("buckets")
                if b:
                    out[slug] = (r["ts_utc"], b)
    f = ROOT / "pilot_data" / f"{d}.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("market_date", d) != d:
                continue
            b = (r.get("pm") or {}).get("buckets")
            if b:
                out[r["city"]] = (r["ts_utc"], b)
    return out


def winners(d):
    w = {}
    for slug, (ts, b) in snapshots(d).items():
        ws = [x["bucket"] for x in b if (x.get("last") or 0) >= 97 or (x.get("bid") or 0) >= 97]
        if len(ws) == 1:
            w[slug] = ws[0]
    return w


def load(d):
    f = ROOT / "forecasts" / f"{d}.json"
    return f, (json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"date": d, "picks": {}})


def write(d):
    f, data = load(d)
    fav = data.setdefault("favorites", {})
    rows = []
    act = active_cities()
    for slug, (ts, b) in sorted(snapshots(d).items()):
        if slug in fav and fav[slug].get("verdict") in ("enter", "reference"):
            rows.append((slug, fav[slug]))
            continue  # не перезаписывать уже сделанный вход
        lead = max(b, key=lambda x: x.get("last") or 0)
        last, bid, ask = lead.get("last"), lead.get("bid"), lead.get("ask")
        wide = bool(ask and bid and ask - bid > MAX_SPREAD)
        enter = bool(last and LO <= last < HI and ask and ask <= MAX_ASK and not wide)
        reason = "" if enter else ("широкий стакан" if wide else "дорого" if (last or 0) >= HI
                                   else "лидер дешевле 50¢ — рынок не определился")
        verdict = "enter" if enter else "skip"
        if enter and act is not None and slug not in act:
            verdict, reason = "reference", "город вне активного списка (ревизия 19.09)"
        e = {"bucket": lead["bucket"], "last": last, "bid": bid, "ask": ask,
             "verdict": verdict, "reason": reason,
             "entry_price": ask if enter else None, "size": SIZE if verdict == "enter" else 0,
             "cut": "19:20 Минск накануне", "ts_utc": ts, "winner": None, "hit": None, "pnl": None}
        fav[slug] = e
        rows.append((slug, e))
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(1 for _, e in rows if e["verdict"] == "enter")
    print(f"{d}: фавориты записаны — входов {n} из {len(rows)} рынков")
    return rows


def tg_text(d, rows):
    L = [f"🌙 <b>ФАВОРИТЫ НА {d[8:10]}.{d[5:7]}</b> (лидер 50-70¢ по ask, size 1.0)"]
    ent = [(s, e) for s, e in rows if e["verdict"] == "enter"]
    ref = [(s, e) for s, e in rows if e["verdict"] == "reference"]
    skp = [(s, e) for s, e in rows if e["verdict"] == "skip"]
    for s, e in ent:
        L.append(f"✅ <b>{s}</b> — {e['bucket']} по {e['ask']:.0f}¢ ({e['bid']:.0f}/{e['ask']:.0f})")
    if not ent:
        L.append("сегодня входов нет")
    if ref:
        L.append("— справочно (вне списка): " + ", ".join(f"{s} {e['bucket']} {e['ask']:.0f}¢" for s, e in ref))
    if skp:
        L.append("— пропуск: " + ", ".join(f"{s} {e['bucket']} {e['last']:.0f}¢" for s, e in skp))
    return "\n".join(L)


def resolve(d):
    f, data = load(d)
    fav = data.get("favorites", {})
    if not fav:
        print(f"{d}: favorites нет")
        return
    w = winners(d)
    n = hits = 0
    pnl = 0.0
    for slug, e in fav.items():
        if e.get("winner") is None and slug in w:
            e["winner"] = w[slug]
        if not e.get("winner"):
            continue
        e["hit"] = e["bucket"] == e["winner"]
        if e["verdict"] == "enter":
            e["pnl"] = round((100 / e["entry_price"] - 1) * e["size"], 3) if e["hit"] else -e["size"]
            n += 1; hits += e["hit"]; pnl += e["pnl"]
        elif e["verdict"] == "reference" and e.get("ask"):
            e["pnl_ref"] = round(100 / e["ask"] - 1, 3) if e["hit"] else -1.0
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    unresolved = [s for s, e in fav.items() if e["verdict"] == "enter" and not e.get("winner")]
    print(f"{d}: фавориты {hits}/{n}, P&L {pnl:+.2f}" + (f"; не резолвлены: {unresolved}" if unresolved else ""))


def stats():
    byday, bycity, ref = {}, {}, [0, 0, 0.0]
    for f in sorted((ROOT / "forecasts").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        for slug, e in data.get("favorites", {}).items():
            if e.get("verdict") == "reference" and e.get("pnl_ref") is not None:
                ref[0] += 1; ref[1] += e["hit"]; ref[2] += e["pnl_ref"]
            if e.get("verdict") != "enter" or e.get("pnl") is None:
                continue
            for k, dct in ((f.stem, byday), (slug, bycity)):
                s = dct.setdefault(k, [0, 0, 0.0])
                s[0] += 1; s[1] += e["hit"]; s[2] += e["pnl"]
    if ref[0]:
        print(f"справочно, города вне списка: {ref[1]}/{ref[0]} {ref[2]:+.2f}")
    T = [0, 0, 0.0]
    for d, (n, h, p) in sorted(byday.items()):
        T[0] += n; T[1] += h; T[2] += p
        print(f"{d}  {h}/{n}  {p:+.2f}")
    if T[0]:
        print(f"ИТОГО {T[1]}/{T[0]} ({T[1]/T[0]*100:.0f}%)  {T[2]:+.2f}  ({T[2]/T[0]*100:+.1f}%/сделку)")
    for c, (n, h, p) in sorted(bycity.items(), key=lambda kv: -kv[1][2]):
        print(f"  {c:15s} {h}/{n} {p:+.2f}")
    return T


def rank(write_file=True):
    """Скользящий бэктест по живой механике (снапшот 16:20 UTC накануне) по каждому городу;
    активны города с ≥MIN_N сделок и ≥MIN_ROI на сделку. Запускать по понедельникам."""
    import glob
    series = {}
    for path in sorted(glob.glob(str(ROOT / "data" / "*.jsonl"))) + sorted(glob.glob(str(ROOT / "pilot_data" / "*.jsonl"))):
        d = Path(path).stem
        for line in open(path, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            md = r.get("market_date", d)
            if "cities" in r:
                for slug, c in r["cities"].items():
                    b = (c.get("polymarket") or {}).get("buckets")
                    if b:
                        series.setdefault((md, slug), []).append((r["ts_utc"], b))
            elif r.get("pm"):
                b = r["pm"].get("buckets")
                if b:
                    series.setdefault((md, r["city"]), []).append((r["ts_utc"], b))
    today = datetime.now(timezone.utc).date().isoformat()
    stat = {}
    for (d, slug), v in series.items():
        if d >= today:
            continue
        v.sort()
        wb = [x["bucket"] for x in v[-1][1] if (x.get("last") or 0) >= 97 or (x.get("bid") or 0) >= 97]
        if len(wb) != 1:
            continue
        target = datetime.fromisoformat(d) - timedelta(days=1)
        target = target.replace(hour=16, minute=20)
        dist, ts, b = min((abs((datetime.fromisoformat(ts.replace("Z", "")) - target).total_seconds()), ts, b) for ts, b in v)
        if dist > 7200:
            continue
        lead = max(b, key=lambda x: x.get("last") or 0)
        p, a, bd = lead.get("last"), lead.get("ask"), lead.get("bid")
        if not (p and a and bd and LO <= p < HI and a <= MAX_ASK and a - bd <= MAX_SPREAD):
            continue
        hit = lead["bucket"] == wb[0]
        s = stat.setdefault(slug, [0, 0, 0.0])
        s[0] += 1; s[1] += hit; s[2] += (100 / a - 1) if hit else -1
    active = sorted(c for c, (n, h, pnl) in stat.items() if n >= MIN_N and pnl / n >= MIN_ROI)
    for c, (n, h, pnl) in sorted(stat.items(), key=lambda kv: -kv[1][2] / max(kv[1][0], 1)):
        print(f"{'*' if c in active else ' '} {c:16s} {n:3d} {h:3d} {pnl:+6.2f} {pnl/n*100:+6.1f}%")
    print("active:", active)
    if write_file:
        CITIES_FILE.write_text(json.dumps({"updated": today, "rule": f"n>={MIN_N} and roi>={MIN_ROI} in rolling live-mechanic backtest",
                                           "active": active, "stats": {c: {"n": v[0], "hit": v[1], "pnl": round(v[2], 2)} for c, v in stat.items()}},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"записано {CITIES_FILE.name}")
    return active


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--rank":
        rank(); sys.exit(0)
    if not a:
        print(__doc__); sys.exit(0)
    if a[0] == "--resolve":
        resolve(a[1])
    elif a[0] == "--stats":
        stats()
    else:
        dates = [x for x in a if len(x) == 10 and x[4] == "-"]
        d = dates[0] if dates else (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        rows = write(d) if "--write" in a else [(s, e) for s, e in []]
        if "--tg" in a:
            out = a[a.index("--tg") + 1]
            Path(out).write_text(tg_text(d, rows), encoding="utf-8")
            print(f"tg → {out}")
