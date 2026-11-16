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

def valid_resolve(e):
    w = e.get("winner") or ""
    return e.get("type") == "resolve" and bool(re.search(r"\d", w)) and ("°" in w or "below" in w or "higher" in w or "above" in w)

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

def signal_for(slug, info, now, b, ts, obs=None):
    tz = ZoneInfo(info["tz"]); lt = now.astimezone(tz); d = lt.date().isoformat()
    if obs is None: obs = metar_day(info["station"], tz, d, until=now - timedelta(minutes=2))
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
    have = {(e["slug"], e["d"]) for e in load_journal() if e.get("type") == "entry" and e.get("rule", "station") == "station"}
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
    done = {(e["slug"], e["d"], e.get("rule", "station")) for e in E if valid_resolve(e)}
    new = []
    for e in E:
        if e.get("type") != "entry" or (e["slug"], e["d"], e.get("rule", "station")) in done or e["slug"] not in U: continue
        info = U[e["slug"]]; tz = ZoneInfo(info["tz"])
        end = datetime.fromisoformat(e["d"]).replace(tzinfo=tz) + timedelta(days=1, hours=3)
        if now < end: continue
        w = gamma_winner(e["slug"], e["d"])  # первичный источник — Gamma (сборщик мог остановиться на ложном ≥97)
        w = [w] if w else None
        if not w:
            snaps = snapshots(e["slug"], info, e["d"])
            if not snaps: continue
            ts, b = snaps[-1]
            w = [k for k, (l, bid, a) in b.items() if (l or 0) >= 97 or (bid or 0) >= 97]
            if len(w) != 1: continue
        hit = (w[0] == e["bucket"]); pnl = round((100 / e["entry"] - 1) * e["size"], 3) if hit else -1.0 * e["size"]
        new.append(dict(type="resolve", rule=e.get("rule", "station"), slug=e["slug"], d=e["d"], winner=w[0], hit=hit, pnl=pnl, ts_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ")))
        done.add((e["slug"], e["d"], e.get("rule", "station")))
    if new: append(new)
    print(f"station_edge: закрыто {len(new)}")
    return new

def stats(tg=None):
    E = load_journal(); L = []
    for rule, title in (("station", "📡 <b>СТАНЦИЯ ВЫШЕ ЛИДЕРА</b> (бумага, с 09.11)"), ("nws5", "⏱ <b>МИНУТКА США</b> (бумага, с 16.11; 5-мин NWS опережает часовую печать)")):
        ent = {(e["slug"], e["d"]): e for e in E if e.get("type") == "entry" and e.get("rule", "station") == rule}
        res = list({(e["slug"], e["d"]): e for e in E if valid_resolve(e) and e.get("rule", "station") == rule and (e["slug"], e["d"]) in ent}.values())
        if rule == "nws5" and not ent: continue
        n = len(res); h = sum(1 for r in res if r["hit"]); p = sum(r["pnl"] for r in res)
        openn = [e for k, e in ent.items() if k not in {(r["slug"], r["d"]) for r in res}]
        L.append(f"{title}: {h}/{n} {p:+.2f}" + (f" ({p/n*100:+.0f}%/сд)" if n else ""))
        for r in sorted(res, key=lambda r: (r["d"], r["slug"]))[-8:]:
            e = ent[(r["slug"], r["d"])]
            L.append(f"{'✅' if r['hit'] else '❌'} {r['d'][8:10]}.{r['d'][5:7]} {r['slug']} {e['bucket']} @{e['entry']:.0f} → {r['winner']} {r['pnl']:+.2f}" + (f" стакан {e.get('book_best_size')}/{e.get('book_depth5')}" if e.get("book_best_size") is not None else ""))
        if openn: L.append("открыто: " + ", ".join(f"{e['slug']} {e['bucket']} @{e['entry']:.0f}" for e in openn))
    txt = "\n".join(L); print(txt)
    if tg: Path(tg).write_text(txt, encoding="utf-8")



# ---------------------------------------------------------------- ретро-журнал пропущенных сигналов (15.11)
MISSED = ROOT / "station_edge" / "missed.jsonl"

def retro(d):
    """Сигналы по правилу на снапшотах дня d, которых нет в журнале (цикл Actions
    не успел: METAR дошёл до сборщика позже, чем рынок переоценился). Пишет
    station_edge/missed.jsonl — теневой журнал «что поймал бы минутный опрос»;
    цена — ask снапшота, в котором сигнал виден впервые (не гарантирована)."""
    have = {(e["slug"], e["d"]) for e in load_journal() if e.get("type") == "entry"}
    seen = set()
    if MISSED.exists():
        for line in open(MISSED, encoding="utf-8"):
            try: e = json.loads(line); seen.add((e["slug"], e["d"]))
            except Exception: pass
    out = []
    for slug, info in universe().items():
        if (slug, d) in have or (slug, d) in seen: continue
        for ts, b in snapshots(slug, info, d):
            now = parse_ts(ts) + timedelta(seconds=30); lt = now.astimezone(ZoneInfo(info["tz"]))
            if lt.date().isoformat() != d or not (H_LO <= lt.hour < H_HI): continue
            try: e = signal_for(slug, info, now, b, ts)
            except Exception: e = None
            if e:
                w = gamma_winner(slug, d)
                e.update(type="missed", winner=w, hit=(w == e["bucket"]) if w else None,
                         pnl=(round(100 / e["entry"] - 1, 3) if w == e["bucket"] else -1.0) if w else None)
                out.append(e); break
    if out:
        MISSED.parent.mkdir(exist_ok=True)
        with open(MISSED, "a", encoding="utf-8") as f:
            for e in out: f.write(json.dumps(e, ensure_ascii=False) + "\n")
    for e in out: print(f"ПРОПУЩЕН {e['slug']} {e['d']} {e['local']} {e['bucket']} (лидер {e['lead']}) ask {e['ask']} → {e.get('winner')}")
    return out

def missed_stats():
    E = []
    if MISSED.exists():
        for line in open(MISSED, encoding="utf-8"):
            try: E.append(json.loads(line))
            except Exception: pass
    R = [e for e in E if e.get("hit") is not None]
    n = len(R); h = sum(1 for e in R if e["hit"]); p = sum(e["pnl"] for e in R)
    return f"теневой журнал пропущенных (минутный опрос поймал бы): {h}/{n} {p:+.2f}" + (f" ({p/n*100:+.0f}%/сд)" if n else "")


# ---------------------------------------------------------------- минутный контур (16.11, решение владельца)
METAR_API = "https://aviationweather.gov/api/data/metar"
CLOB_API = "https://clob.polymarket.com"

def _get(url, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "polic_weatther"})
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)

def live_metar(stations):
    """{station: [(obs_dt, temp_c)]} — регулярные METAR за 3 ч прямо с API (без файлов)"""
    out = {}
    try: data = _get(f"{METAR_API}?ids={','.join(stations)}&format=json&hours=3", 30) or []
    except Exception as ex:  # noqa: BLE001
        print(f"WARN fast metar: {ex}", file=sys.stderr); return out
    for o in data:
        t = o.get("reportTime") or ""
        if not t or o.get("temp") is None or (o.get("rawOb") or "").startswith("SPECI"): continue
        out.setdefault(o.get("icaoId"), []).append((parse_ts(t[:19].replace(" ", "T") + "Z"), o["temp"]))
    return out

def live_market(slug, d):
    """Gamma: {bucket:(last,bid,ask)}, {bucket: yes_token_id}"""
    ev = config.pm_event_slug(slug, date.fromisoformat(d))
    r = _get(f"{config.GAMMA_API}/events?slug={ev}")
    b = {}; tok = {}
    for m in (r[0].get("markets", []) if r else []):
        k = m.get("groupItemTitle")
        if not k: continue
        f = lambda x: round(float(x) * 100, 2) if x is not None else None
        b[k] = (f(m.get("lastTradePrice")), f(m.get("bestBid")), f(m.get("bestAsk")))
        try: tok[k] = json.loads(m["clobTokenIds"])[0]
        except Exception: pass
    return b, tok

def book_depth(token_id, ask):
    """размер на лучшем ask и суммарный размер ask ≤ ask+5¢ (в долях/контрактах)"""
    try: bk = _get(f"{CLOB_API}/book?token_id={token_id}")
    except Exception as ex:  # noqa: BLE001
        print(f"WARN fast book: {ex}", file=sys.stderr); return None, None
    asks = [(float(a["price"]) * 100, float(a["size"])) for a in bk.get("asks", [])]
    if not asks: return 0.0, 0.0
    best = min(p for p, _ in asks)
    return round(sum(sz for p, sz in asks if abs(p - best) < 0.05), 1), round(sum(sz for p, sz in asks if p <= best + 5), 1)

# ---------------------------------------------------------------- правило B «МИНУТКА США» (16.11, предрегистрация)
# Бэктест на минутных ASOS (IEM) 27.08–16.11, 7 городов: текущая минутная температура в
# корзине ВЫШЕ и корзины часового максимума, и лидера PM; 12:00–16:59 местного; ask 1–85 →
# 121 сделка, 48 %, +82 ед. по ask+1 (+68 %/сд), 8 из 9 последних недель в плюс.
# Живые источники (16.11, владелец: «подключи минутные данные IEM»): минутный архив IEM
# отстаёт на ~сутки и годится только для теневой проверки (--retro-us). В реальном времени
# доступны 5-минутные HFMETAR: NWS api.weather.gov (свежее, целые °C) и IEM obhistory
# (та же лента, в RMK T-группа с 0,1 °C). Берём самое свежее ≤20 мин; запас от нижней
# границы корзины ≥0,5 °C для целых и ≥0,2 °C для десятых.
NWS_API = "https://api.weather.gov/stations/{st}/observations/latest"
IEM_OBHIST = "https://mesonet.agron.iastate.edu/api/1/obhistory.json?station={st3}&network={net}&date={d}"
IEM_NET = {"KAUS": "TX_ASOS", "KHOU": "TX_ASOS", "KATL": "GA_ASOS", "KLAX": "CA_ASOS", "KSFO": "CA_ASOS", "KMIA": "FL_ASOS", "KSEA": "WA_ASOS", "KDAL": "TX_ASOS", "KORD": "IL_ASOS"}

def nws_latest(station):
    try: o = _get(NWS_API.format(st=station), 20)["properties"]
    except Exception as ex:  # noqa: BLE001
        print(f"WARN fast nws {station}: {ex}", file=sys.stderr); return None
    t = o.get("temperature", {}).get("value"); ts = o.get("timestamp")
    if t is None or not ts: return None
    return datetime.fromisoformat(ts).astimezone(timezone.utc), float(t), "whole"

def iem_latest(station):
    """последнее 5-мин наблюдение IEM obhistory с T-группой (0,1 °C); None, если нет"""
    net = IEM_NET.get(station)
    if not net: return None
    now = datetime.now(timezone.utc); best = None
    for d in (now.date(), (now - timedelta(days=1)).date()):
        try: rows = _get(IEM_OBHIST.format(st3=station[1:], net=net, d=d.isoformat()), 25).get("data", [])
        except Exception as ex:  # noqa: BLE001
            print(f"WARN fast iem {station}: {ex}", file=sys.stderr); return None
        for r in rows:
            m = re.search(r"\bT(\d)(\d{3})(\d)(\d{3})\b", r.get("raw") or "")
            if not m or not r.get("utc_valid"): continue
            c = int(m.group(2)) / 10 * (-1 if m.group(1) == "1" else 1)
            t = datetime.strptime(r["utc_valid"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
            if best is None or t > best[0]: best = (t, c, "tenth")
        if best: break
    return best

def us_current(station, now):
    """самое свежее наблюдение из NWS и IEM, не старше 20 мин: (t, temp_c, precision)"""
    c = [x for x in (nws_latest(station), iem_latest(station)) if x and (now - x[0]).total_seconds() <= 1200]
    if not c: return None
    # при равной свежести предпочитаем десятые
    return max(c, key=lambda x: (x[0], x[2] == "tenth"))

def bucket_lower_f(label):
    m = re.findall(r"(?<!\d)-?\d+", label)
    if not m or "below" in label: return None
    return int(m[0]) - 0.5

def signal_us(slug, info, now, b, obs):
    """правило B для США: obs — часовые регулярные наблюдения дня (°C)"""
    if info["unit"] != "F" or not obs: return None
    tz = ZoneInfo(info["tz"]); lt = now.astimezone(tz); d = lt.date().isoformat()
    cur = us_current(info["station"], now)
    if not cur: return None
    t_obs, c, prec = cur
    hmax_f = max(x * 9 / 5 + 32 for _, x in obs)
    cur_f = c * 9 / 5 + 32
    bk_h = bucket_for_temp(b, hmax_f); bk_c = bucket_for_temp(b, cur_f)
    if not bk_h or not bk_c or bk_c == bk_h: return None
    o = ordered(b)
    keys = [k for k in b if price(b, k) is not None]
    if not keys: return None
    lead = max(keys, key=lambda k: price(b, k))
    if o.index(bk_c) <= o.index(bk_h) or o.index(bk_c) <= o.index(lead): return None
    lo = bucket_lower_f(bk_c)
    if lo is not None and cur_f - lo < (0.9 if prec == "whole" else 0.4): return None
    ask = b[bk_c][2]
    if ask is None or not (ASK_LO <= ask <= ASK_HI): return None
    return dict(type="entry", rule="nws5", slug=slug, d=d, ts_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"), snap_ts=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                local=lt.strftime("%H:%M"), bucket=bk_c, lead=lead, lead_last=b[lead][0], last=b[bk_c][0], bid=b[bk_c][1], ask=ask,
                entry=round(ask + SLIP, 2), size=SIZE, t_cur=round(cur_f, 1), t_hmax=round(hmax_f, 1), obs_local=t_obs.astimezone(tz).strftime("%H:%M"),
                obs_src=prec, obs_age_min=round((now - t_obs).total_seconds() / 60), unit="F")

# ---------------------------------------------------------------- теневая проверка B по минутному архиву IEM
IEM_1MIN = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?station={st3}&tz=UTC&year1={y1}&month1={m1}&day1={d1}&year2={y2}&month2={m2}&day2={d2}&vars=tmpf&sample=1min&what=download&delim=comma&gis=no"
MISSED_US = ROOT / "station_edge" / "shadow_us.jsonl"

def retro_us(d):
    """Правило B на минутном архиве IEM за день d (архив отстаёт на ~сутки) против снапшотов
    PM → station_edge/shadow_us.jsonl (type=shadow_us). Это «что увидел бы минутный контур»."""
    import csv, io, urllib.request
    seen = set()
    if MISSED_US.exists():
        for line in open(MISSED_US, encoding="utf-8"):
            try: e = json.loads(line); seen.add((e["slug"], e["d"]))
            except Exception: pass
    out = []
    d0 = date.fromisoformat(d); d1 = d0 + timedelta(days=2)
    for slug, info in universe().items():
        if info["unit"] != "F" or info["station"] not in IEM_NET or (slug, d) in seen: continue
        tz = ZoneInfo(info["tz"]); day0 = datetime.fromisoformat(d).replace(tzinfo=tz); day1 = day0 + timedelta(days=1)
        url = IEM_1MIN.format(st3=info["station"][1:], y1=d0.year, m1=d0.month, d1=d0.day, y2=d1.year, m2=d1.month, d2=d1.day)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "polic_weatther"})
            txt = urllib.request.urlopen(req, timeout=120).read().decode()
        except Exception as ex:  # noqa: BLE001
            print(f"WARN retro_us {slug}: {ex}", file=sys.stderr); continue
        m1 = {}
        for r in csv.DictReader(io.StringIO(txt)):
            try:
                t = datetime.strptime(r["valid(UTC)"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                if day0 <= t.astimezone(tz) < day1: m1[t] = float(r["tmpf"])
            except Exception: continue
        if len(m1) < 900: print(f"retro_us {slug} {d}: минутных данных {len(m1)} — архив ещё не полон"); continue
        prints = {t: x for t, x in m1.items() if t.minute == 53}
        snaps = snapshots(slug, info, d)
        if not snaps or not prints: continue
        for ts, b in snaps:
            t = parse_ts(ts); lt = t.astimezone(tz)
            if not (H_LO <= lt.hour < H_HI): continue
            hp = [x for tt, x in prints.items() if tt <= t]
            cur = [x for tt, x in sorted(m1.items()) if t - timedelta(minutes=3) <= tt <= t]
            if not hp or not cur: continue
            hm = max(hp); c = cur[-1]
            bh, bc = bucket_for_temp(b, hm), bucket_for_temp(b, c)
            if not bh or not bc or bc == bh: continue
            o = ordered(b); keys = [k for k in b if price(b, k) is not None]
            if not keys: continue
            lead = max(keys, key=lambda k: price(b, k))
            if o.index(bc) <= o.index(bh) or o.index(bc) <= o.index(lead): continue
            ask = b[bc][2]
            if ask is None or not (ASK_LO <= ask <= ASK_HI): continue
            w = gamma_winner(slug, d)
            out.append(dict(type="shadow_us", rule="nws5", slug=slug, d=d, ts_utc=ts, local=lt.strftime("%H:%M"), bucket=bc, lead=lead, ask=ask, entry=round(ask + SLIP, 2),
                            t_cur=c, t_hmax=hm, winner=w, hit=(w == bc) if w else None, pnl=((round(100 / (ask + SLIP) - 1, 3) if w == bc else -1.0) if w else None)))
            break
    if out:
        MISSED_US.parent.mkdir(exist_ok=True)
        with open(MISSED_US, "a", encoding="utf-8") as f:
            for e in out: f.write(json.dumps(e, ensure_ascii=False) + "\n")
    for e in out: print(f"ТЕНЬ США {e['slug']} {e['d']} {e['local']} {e['bucket']} (лидер {e['lead']}) ask {e['ask']} → {e.get('winner')}")
    return out

def shadow_us_stats():
    E = []
    if MISSED_US.exists():
        for line in open(MISSED_US, encoding="utf-8"):
            try: E.append(json.loads(line))
            except Exception: pass
    R = [e for e in E if e.get("hit") is not None]
    n = len(R); h = sum(1 for e in R if e["hit"]); p = sum(e["pnl"] for e in R)
    return f"тень «минутки США» по архиву IEM (минутный контур увидел бы): {h}/{n} {p:+.2f}" + (f" ({p/n*100:+.0f}%/сд)" if n else "")

def fast(seconds):
    """Минутный опрос в окне 12:00–16:59 местного: METAR с API + цены Gamma; сигнал по
    тому же замороженному правилу, запись сразу (src=fast) с глубиной стакана CLOB."""
    t_end = datetime.now(timezone.utc) + timedelta(seconds=int(seconds))
    U = universe(); n_iter = 0
    while datetime.now(timezone.utc) < t_end:
        n_iter += 1; t0 = datetime.now(timezone.utc)
        J = load_journal()
        have = {(e["slug"], e["d"]) for e in J if e.get("type") == "entry" and e.get("rule", "station") == "station"}
        have_b = {(e["slug"], e["d"], "nws5") for e in J if e.get("type") == "entry" and e.get("rule") == "nws5"}
        active = {}
        for slug, info in U.items():
            lt = t0.astimezone(ZoneInfo(info["tz"])); d_ = lt.date().isoformat()
            if H_LO <= lt.hour < H_HI and ((slug, d_) not in have or (info["unit"] == "F" and (slug, d_, "nws5") not in have_b)): active[slug] = (info, d_)
        if active:
            metar = live_metar(sorted({i["station"] for i, _ in active.values()}))
            new = []
            for slug, (info, d) in active.items():
                tz = ZoneInfo(info["tz"]); day0 = datetime.fromisoformat(d).replace(tzinfo=tz)
                obs = {t: c for t, c in metar_day(info["station"], tz, d, until=t0)}  # история из файлов
                for t, c in metar.get(info["station"], []):
                    if day0 <= t.astimezone(tz) < day0 + timedelta(days=1) and t <= t0: obs[t] = c
                obs = sorted(obs.items())
                if len(obs) < 3: continue
                try: b, tok = live_market(slug, d)
                except Exception as ex:  # noqa: BLE001
                    print(f"WARN fast gamma {slug}: {ex}", file=sys.stderr); continue
                if not b: continue
                found = []
                if (slug, d) not in have:
                    try: e = signal_for(slug, info, t0, b, t0.strftime("%Y-%m-%dT%H:%M:%SZ"), obs=obs)
                    except Exception as ex:  # noqa: BLE001
                        print(f"WARN fast signal {slug}: {ex}", file=sys.stderr); e = None
                    if e: e["rule"] = "station"; found.append(e); have.add((slug, d))
                if (slug, d, "nws5") not in have_b and info["unit"] == "F":
                    try: e2 = signal_us(slug, info, t0, b, obs)
                    except Exception as ex:  # noqa: BLE001
                        print(f"WARN fast nws5 {slug}: {ex}", file=sys.stderr); e2 = None
                    if e2: found.append(e2); have_b.add((slug, d, "nws5"))
                for e in found:
                    e["src"] = "fast"
                    if tok.get(e["bucket"]):
                        e["book_best_size"], e["book_depth5"] = book_depth(tok[e["bucket"]], e["ask"])
                    new.append(e)
            if new:
                append(new)
                for e in new: print(f"FAST СИГНАЛ {e['slug']} {e['d']} {e['local']} {e['bucket']} (лидер {e['lead']}) ask {e['ask']} глубина {e.get('book_best_size')}/{e.get('book_depth5')}", flush=True)
        # пульс для контроля из бухгалтерии: коммитится следующим тиком вместе с station_edge/
        try:
            (ROOT / "station_edge" / "fast_heartbeat.json").write_text(json.dumps(dict(
                ts_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), iteration=n_iter, active=sorted(active),
                metar_stations=len(metar) if active else 0, iter_seconds=round((datetime.now(timezone.utc) - t0).total_seconds(), 1)), ensure_ascii=False))
        except Exception as ex:  # noqa: BLE001
            print(f"WARN fast heartbeat: {ex}", file=sys.stderr)
        # пауза до следующей минуты
        rest = 60 - (datetime.now(timezone.utc) - t0).total_seconds()
        if datetime.now(timezone.utc) + timedelta(seconds=max(rest, 0)) >= t_end: break
        if rest > 0: __import__("time").sleep(rest)
    print(f"fast: {n_iter} итераций, активных городов в последней: {len(active) if 'active' in dir() else 0}")

if __name__ == "__main__":
    a = sys.argv[1:]
    if "--scan" in a: scan()
    if "--resolve" in a: resolve()
    if "--retro" in a: retro(a[a.index("--retro") + 1]); print(missed_stats())
    if "--fast" in a: fast(a[a.index("--fast") + 1])
    if "--retro-us" in a: retro_us(a[a.index("--retro-us") + 1]); print(shadow_us_stats())
    if "--stats" in a or not a:
        tg = a[a.index("--tg") + 1] if "--tg" in a else None
        stats(tg)
        if tg and MISSED.exists():
            with open(tg, "a", encoding="utf-8") as f: f.write("\n" + missed_stats())
        if tg and MISSED_US.exists():
            with open(tg, "a", encoding="utf-8") as f: f.write("\n" + shadow_us_stats())
