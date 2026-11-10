"""«СТАНЦИЯ ВЫШЕ ЛИДЕРА» — бумажный журнал новой стратегии (постмортем 09.11.2026).

Правило ЗАМОРОЖЕНО 09.11.2026 до ≥60 сделок (предрегистрация; см.
reports/2026-11-09-postmortem.md):
  • города: все с надёжной станцией резолва (совпадение макс. METAR и
    победителя ≥90% за ≥5 дней) — исключены hong-kong (резолв по HKO),
    nyc (резолв не по KLGA), denver;
  • окно: 12:00–16:59 местного времени рынка;
  • станция: только регулярные (не SPECI) наблюдения локальных суток;
    t_max — максимум к текущему моменту; максимум, случившийся до 06:00
    местного (ночной, перед фронтом), сигналом не считается;
  • корзина_max — корзина, содержащая t_max (°F для США, °C иначе);
  • лидер — корзина с максимальным last;
  • сигнал: корзина_max СТРОГО ВЫШЕ лидера по порядку корзин и её ask
    в пределах 1–85¢;
  • бумажная покупка корзины_max по ask+1¢, size 1.0, одна сделка на
    город-день; резолв — корзина ≥97¢ (last или bid) в последнем снапшоте
    после конца местных суток +3 ч.
Бэктест на снапшотах 10.10–08.11 (30 сигналов): 26/30, +115 ед. при
ask+1¢ — почти весь выигрыш на входах ≤50¢, рынок переоценивается за
10–60 минут. Глубина стакана в снапшотах неизвестна — главный риск.

Журнал — station_edge/journal.jsonl, только добавление строк (сливается
union-merge в Actions): {"type":"entry",...} и {"type":"resolve",...}.

python3 station_edge.py --scan       # сигналы сейчас (в цикле Actions каждые 10 мин)
python3 station_edge.py --resolve    # закрыть сделки по завершившимся рынкам
python3 station_edge.py --stats [--tg файл]
"""
import json, sys, glob, re
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import config, pilot

ROOT = Path(__file__).parent
JOURNAL = ROOT / "station_edge" / "journal.jsonl"
EXCLUDE = {"hong-kong", "nyc", "denver"}
H_LO, H_HI = 12, 17
ASK_LO, ASK_HI = 1, 85
SLIP = 1.0
SIZE = 1.0
NOCTURNAL_H = 6

def universe():
    U = {}
    for slug, c in config.CITIES.items():
        U[slug] = dict(station=c["station"], tz=c["tz"], unit="F", src="data")
    for slug, c in pilot.PILOT_CITIES.items():
        if c.get("station"): U[slug] = dict(station=c["station"], tz=c["tz"], unit=c.get("unit", "C"), src="pilot")
    try:
        o = json.loads((ROOT / "observe_stations.json").read_text(encoding="utf-8"))["cities"]
        for slug, c in o.items():
            if slug not in U and not c.get("proxy"): U[slug] = dict(station=c["station"], tz=c["tz"], unit=c["unit"], src="observe")
    except Exception as e:  # noqa: BLE001
        print(f"WARN station_edge: observe_stations: {e}", file=sys.stderr)
    for s in EXCLUDE: U.pop(s, None)
    return U

def parse_ts(ts): return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def snapshots(slug, info, d):
    """[(ts, {bucket:(last,bid,ask)})] рынка d для города"""
    out = []
    if info["src"] == "data":
        fn = ROOT / "data" / f"{d}.jsonl"
        if fn.exists():
            for line in open(fn, encoding="utf-8"):
                try: r = json.loads(line)
                except Exception: continue
                if r.get("market_date", d) != d: continue
                b = ((r.get("cities", {}).get(slug) or {}).get("polymarket") or {}).get("buckets")
                if b: out.append((r["ts_utc"], b))
    elif info["src"] == "pilot":
        fn = ROOT / "pilot_data" / f"{d}.jsonl"
        if fn.exists():
            for line in open(fn, encoding="utf-8"):
                try: r = json.loads(line)
                except Exception: continue
                if r.get("city") != slug or r.get("market_date", d) != d: continue
                b = (r.get("pm") or {}).get("buckets")
                if b: out.append((r["ts_utc"], b))
    else:
        fn = ROOT / "observe_data" / f"{d}.jsonl"
        if fn.exists():
            for line in open(fn, encoding="utf-8"):
                try: r = json.loads(line)
                except Exception: continue
                b = (r.get("cities", {}).get(slug) or {}).get("buckets")
                if b: out.append((r["ts_utc"], b))
    out.sort(key=lambda x: x[0])
    return [(ts, {x["bucket"]: (x.get("last"), x.get("bid"), x.get("ask")) for x in b}) for ts, b in out]

def bnum(label):
    m = re.findall(r"(?<!\d)-?\d+", label)
    return int(m[0]) if m else None

def ordered(b): return sorted(b.keys(), key=lambda k: (bnum(k) if bnum(k) is not None else -999))

def price(b, k):
    l, bid, a = b[k]
    return l if l is not None else ((bid or 0) + (a or 0)) / 2 if (bid or a) else None

def bucket_for_temp(b, t):
    for k in ordered(b):
        n = bnum(k)
        if n is None: continue
        if "below" in k and t <= n + 0.999: return k
        if "higher" in k or "above" in k:
            if t >= n - 0.001: return k
            continue
        m = re.findall(r"(?<!\d)-?\d+", k); lo = int(m[0]); hi = int(m[1]) if len(m) > 1 else lo
        if lo - 0.5 <= t < hi + 0.5: return k
    return None

def metar_day(station, tz, d, until=None):
    """регулярные наблюдения станции за местные сутки d до момента until: [(obs_dt, temp_c)]"""
    day0 = datetime.fromisoformat(d).replace(tzinfo=tz); day1 = day0 + timedelta(days=1)
    seen = {}
    for ud in {day0.astimezone(timezone.utc).date(), day1.astimezone(timezone.utc).date()}:
        fn = ROOT / "metar_data" / f"{ud.isoformat()}.jsonl"
        if not fn.exists(): continue
        for line in open(fn, encoding="utf-8"):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("station") != station or r.get("temp_c") is None or r.get("speci"): continue
            t = parse_ts(r["obs_utc"])
            if day0 <= t.astimezone(tz) < day1 and (until is None or t <= until): seen[r["obs_utc"]] = (t, r["temp_c"])
    return sorted(seen.values())

def load_journal():
    E = []
    if JOURNAL.exists():
        for line in open(JOURNAL, encoding="utf-8"):
            line = line.strip()
            if not line: continue
            try: E.append(json.loads(line))
            except Exception: continue
    return E

def append(rows):
    JOURNAL.parent.mkdir(exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")

def signal_for(slug, info, now, b, ts):
    tz = ZoneInfo(info["tz"]); lt = now.astimezone(tz); d = lt.date().isoformat()
    obs = metar_day(info["station"], tz, d, until=now - timedelta(minutes=2))
    if len(obs) < 3: return None
    conv = (lambda c: c * 9 / 5 + 32) if info["unit"] == "F" else (lambda c: c)
    t_max = max(conv(c) for _, c in obs); i = max(i for i, (_, c) in enumerate(obs) if conv(c) == t_max)
    t_of_max = obs[i][0].astimezone(tz)
    if t_of_max.hour < NOCTURNAL_H: return None
    bk = bucket_for_temp(b, t_max)
    if not bk or bk not in b: return None
    keys = [k for k in b if price(b, k) is not None]
    if not keys: return None
    lead = max(keys, key=lambda k: price(b, k)); o = ordered(b)
    if bk not in o or lead not in o or o.index(bk) <= o.index(lead): return None
    ask = b[bk][2]
    if ask is None or not (ASK_LO <= ask <= ASK_HI): return None
    return dict(type="entry", slug=slug, d=d, ts_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), snap_ts=ts,
                local=lt.strftime("%H:%M"), bucket=bk, lead=lead, lead_last=b[lead][0], last=b[bk][0], bid=b[bk][1], ask=ask,
                entry=round(ask + SLIP, 2), size=SIZE, t_max=round(t_max, 1), t_now=round(conv(obs[-1][1]), 1),
                t_of_max_local=t_of_max.strftime("%H:%M"), lag_min=round((now - obs[i][0]).total_seconds() / 60), unit=info["unit"])

def scan():
    now = datetime.now(timezone.utc)
    have = {(e["slug"], e["d"]) for e in load_journal() if e.get("type") == "entry"}
    new = []
    for slug, info in universe().items():
        tz = ZoneInfo(info["tz"]); lt = now.astimezone(tz)
        if not (H_LO <= lt.hour < H_HI): continue
        d = lt.date().isoformat()
        if (slug, d) in have: continue
        snaps = snapshots(slug, info, d)
        if not snaps: continue
        ts, b = snaps[-1]
        if (now - parse_ts(ts)).total_seconds() > 720: continue  # снапшот старше одного цикла (12 мин) — цена могла уйти (урок KL 10.11)
        try: e = signal_for(slug, info, now, b, ts)
        except Exception as ex:  # noqa: BLE001
            print(f"WARN station_edge {slug}: {ex}", file=sys.stderr); continue
        if e: new.append(e)
    if new:
        append(new)
        for e in new: print(f"СИГНАЛ {e['slug']} {e['d']} {e['local']} {e['bucket']} (лидер {e['lead']}) ask {e['ask']} t_max {e['t_max']}")
    else: print("station_edge: сигналов нет")
    return new

def gamma_winner(slug, d):
    """победитель по Gamma API: корзина с outcomePrices[0] ≥ 0.97; None, если не определён"""
    import urllib.request
    try:
        ev = config.pm_event_slug(slug, date.fromisoformat(d))
        req = urllib.request.Request(f"{config.GAMMA_API}/events?slug={ev}", headers={"User-Agent": "polic_weatther"})
        r = json.load(urllib.request.urlopen(req, timeout=20))
        w = []
        for m in r[0].get("markets", []):
            op = m.get("outcomePrices")
            if isinstance(op, str): op = json.loads(op)
            if op and float(op[0]) >= 0.97: w.append(m.get("groupItemTitle"))
        return w[0] if len(w) == 1 else None
    except Exception as ex:  # noqa: BLE001
        print(f"WARN station_edge gamma {slug} {d}: {ex}", file=sys.stderr); return None

def resolve():
    E = load_journal(); U = universe(); now = datetime.now(timezone.utc)
    done = {(e["slug"], e["d"]) for e in E if e.get("type") == "resolve"}
    new = []
    for e in E:
        if e.get("type") != "entry" or (e["slug"], e["d"]) in done or e["slug"] not in U: continue
        info = U[e["slug"]]; tz = ZoneInfo(info["tz"])
        end = datetime.fromisoformat(e["d"]).replace(tzinfo=tz) + timedelta(days=1, hours=3)
        if now < end: continue
        w = gamma_winner(e["slug"], e["d"])  # первичный источник — Gamma (сборщик мог остановиться на ложном ≥97)
        if not w:
            snaps = snapshots(e["slug"], info, e["d"])
            if not snaps: continue
            ts, b = snaps[-1]
            w = [k for k, (l, bid, a) in b.items() if (l or 0) >= 97 or (bid or 0) >= 97]
            if len(w) != 1: continue
        hit = (w[0] == e["bucket"]); pnl = round((100 / e["entry"] - 1) * e["size"], 3) if hit else -1.0 * e["size"]
        new.append(dict(type="resolve", slug=e["slug"], d=e["d"], winner=w[0], hit=hit, pnl=pnl, ts_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ")))
        done.add((e["slug"], e["d"]))
    if new: append(new)
    print(f"station_edge: закрыто {len(new)}")
    return new

def stats(tg=None):
    E = load_journal()
    ent = {(e["slug"], e["d"]): e for e in E if e.get("type") == "entry"}
    res = [e for e in E if e.get("type") == "resolve" and (e["slug"], e["d"]) in ent]
    n = len(res); h = sum(1 for r in res if r["hit"]); p = sum(r["pnl"] for r in res)
    openn = [e for k, e in ent.items() if k not in {(r["slug"], r["d"]) for r in res}]
    L = [f"📡 <b>СТАНЦИЯ ВЫШЕ ЛИДЕРА</b> (бумага, с 09.11): {h}/{n} {p:+.2f}" + (f" ({p/n*100:+.0f}%/сд)" if n else "")]
    for r in sorted(res, key=lambda r: (r["d"], r["slug"]))[-8:]:
        e = ent[(r["slug"], r["d"])]
        L.append(f"{'✅' if r['hit'] else '❌'} {r['d'][8:10]}.{r['d'][5:7]} {r['slug']} {e['bucket']} @{e['entry']:.0f} → {r['winner']} {r['pnl']:+.2f}")
    if openn: L.append("открыто: " + ", ".join(f"{e['slug']} {e['bucket']} @{e['entry']:.0f}" for e in openn))
    txt = "\n".join(L); print(txt)
    if tg: Path(tg).write_text(txt, encoding="utf-8")

if __name__ == "__main__":
    a = sys.argv[1:]
    if "--scan" in a: scan()
    if "--resolve" in a: resolve()
    if "--stats" in a or not a: stats(a[a.index("--tg") + 1] if "--tg" in a else None)
