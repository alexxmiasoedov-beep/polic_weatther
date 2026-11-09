"""Постмортем 09.11 (поручение владельца: «все стратегии минусуют — найти ошибки
анализа/бэктеста и разработать новую стратегию»).

Четыре блока:
 1. calib   — калибровка рынка по децилям ask (куплено по ask+1¢ проскальзывания),
              по горизонтам; показывает, где рынок дорог, где дёшев.
 2. walk    — честная проверка метода «фавориты --rank»: отбор городов по прошлым
              3 неделям → результат следующей недели (walk-forward), против «все города».
 3. agree   — насколько макс. по часовым METAR совпадает с победителем рынка
              (проверка источника резолва).
 4. lockin  — новая стратегия «ФИКСАЦИЯ»: после полудня, когда станция уже упала
              от дневного максимума ≥2 чтения подряд, покупаем корзину дневного
              максимума; и «СТАНЦИЯ ВЫШЕ ЛИДЕРА» днём. Контроль — покупка лидера в тот же
              момент. Всё по ask+1¢, по неделям (out-of-sample по времени).

python3 research/postmortem.py            # всё → stdout
python3 research/postmortem.py --md       # + reports/2026-11-09-postmortem-data.md
"""
import sys, glob, json, re, statistics, collections
from pathlib import Path
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import strategy_lab as L, deep_lab as D, config, pilot

ROOT = Path(__file__).resolve().parent.parent
SLIP = 1.0  # ¢ проскальзывания к ask
OUT = []
def P(*a):
    s = " ".join(str(x) for x in a); print(s); OUT.append(s)

def pnl(ask, win): a = ask + SLIP; return (100 / a - 1) if win else -1.0
def week(d): return datetime.fromisoformat(d).strftime("%G-W%V")

def load_metar_full():
    M = collections.defaultdict(dict)
    for fn in glob.glob(str(ROOT / "metar_data" / "*.jsonl")):
        for line in open(fn, encoding="utf-8"):
            try: r = json.loads(line)
            except Exception: continue
            if r.get("temp_c") is None: continue
            M[r["station"]][r["obs_utc"]] = (r["temp_c"], bool(r.get("speci")))
    return {st: sorted((L.parse_ts(t), v[0], v[1]) for t, v in d.items()) for st, d in M.items()}

S = L.load(); M = load_metar_full()
MKTS = []
for (slug, d), v in S.items():
    snaps = v["snaps"]
    if len(snaps) < 3: continue
    w = L.winner_of(snaps)
    if not w: continue
    tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    if hte[-1] > 14: continue  # рынок часто резолвится (≥97) уже в 15:00 local — не выкидываем
    st, unit = D.station_of(slug)
    MKTS.append(dict(slug=slug, d=d, snaps=snaps, hte=hte, w=w, tz=tz, st=st, unit=unit))
MKTS.sort(key=lambda m: (m["d"], m["slug"]))
P(f"рынков с победителем: {len(MKTS)}, дат {len(set(m['d'] for m in MKTS))}, городов {len(set(m['slug'] for m in MKTS))}")

def agg(rows):
    n = len(rows); h = sum(1 for r in rows if r["win"]); p = sum(pnl(r["ask"], r["win"]) for r in rows)
    return n, h, p
def fmt(rows, label=""):
    n, h, p = agg(rows)
    if not n: return f"{label:28s} —"
    return f"{label:28s} n={n:4d} hit={h/n*100:5.1f}%  pnl={p:+7.2f} ({p/n*100:+5.1f}%/сд)"

# ------------------------------------------------------------ 1. калибровка
def calib():
    P("\n## 1. Калибровка рынка по ask (все рынки, одна точка на рынок и горизонт, ask+1¢)")
    bins = [(36, 48), (20, 36), (12, 20), (6, 12), (3, 6), (1, 3)]
    for lo, hi in bins:
        rows = []
        for m in MKTS:
            c = [(b, h) for (t, b), h in zip(m["snaps"], m["hte"]) if lo <= h < hi]
            if not c: continue
            b, h = min(c, key=lambda x: abs(x[1] - (lo + hi) / 2))
            for k, (last, bid, ask) in b.items():
                if ask is None or ask <= 0 or ask >= 100: continue
                rows.append(dict(ask=ask, win=(k == m["w"])))
        P(f"\nгоризонт {lo}-{hi} ч до закрытия, n корзин={len(rows)}")
        P(f"{'ask':>8s} {'n':>5s} {'факт%':>7s} {'ожид%':>7s} {'pnl/сд':>8s}")
        for a0, a1 in [(1, 5), (5, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 95), (95, 100)]:
            rr = [r for r in rows if a0 <= r["ask"] < a1]
            if len(rr) < 5: continue
            n, h, p = agg(rr); ea = statistics.mean(r["ask"] for r in rr)
            P(f"{a0:3d}-{a1:<3d} {n:5d} {h/n*100:6.1f}% {ea:6.1f}% {p/n*100:+7.1f}%")

# ------------------------------------------------------------ 2. walk-forward фаворитов
def fav_trade(m, lo_h=20, hi_h=48, plo=50, phi=70, max_ask=72):
    c = [(b, h) for (t, b), h in zip(m["snaps"], m["hte"]) if lo_h <= h < hi_h]
    if not c: return None
    b, h = c[-1]  # первый по времени = наибольший hte… снапы по времени ⇒ первый в окне
    b, h = min(c, key=lambda x: -x[1])
    keys = [k for k in b if b[k][0] is not None]
    if not keys: return None
    lead = max(keys, key=lambda k: b[k][0]); last, bid, ask = b[lead]
    if not (plo <= last < phi) or not ask or ask > max_ask: return None
    return dict(slug=m["slug"], d=m["d"], ask=ask, win=(lead == m["w"]))

def walk():
    P("\n## 2. Фавориты: правило «лидер 50-70¢ за 20-48 ч, ask≤72» по неделям, и отбор городов по прошлому")
    T = [t for t in (fav_trade(m) for m in MKTS) if t]
    byw = collections.defaultdict(list)
    for t in T: byw[week(t["d"])].append(t)
    weeks = sorted(byw)
    P(fmt(T, "все города, весь период"))
    for wk in weeks: P(fmt(byw[wk], f"  {wk}"))
    P("\nwalk-forward: топ-5 городов по pnl за предыдущие 3 недели (≥4 сделок) → следующая неделя")
    allsel = []; allrest = []
    for i, wk in enumerate(weeks):
        prev = [t for t in T if week(t["d"]) in weeks[max(0, i - 3):i]]
        if len(set(week(t["d"]) for t in prev)) < 2: continue
        bc = collections.defaultdict(list)
        for t in prev: bc[t["slug"]].append(t)
        rank = sorted(((agg(v)[2], s) for s, v in bc.items() if len(v) >= 4), reverse=True)
        top = [s for _, s in rank[:5]]
        if not top: continue
        sel = [t for t in byw[wk] if t["slug"] in top]; rest = [t for t in byw[wk] if t["slug"] not in top]
        allsel += sel; allrest += rest
        P(f"  {wk}: топ-5={','.join(top)}")
        P("    " + fmt(sel, "выбранные")); P("    " + fmt(rest, "остальные"))
    P(fmt(allsel, "ИТОГО выбранные по прошлому")); P(fmt(allrest, "ИТОГО остальные"))
    # то же для «стабильного лидера» (движение ≤2¢ за 3 ч) — просто справка о доле
    P("\nпо цене входа (все города):")
    for a0, a1 in [(50, 55), (55, 60), (60, 65), (65, 70), (70, 73)]:
        P(fmt([t for t in T if a0 <= t["ask"] < a1], f"  ask {a0}-{a1}"))

# ------------------------------------------------------------ 3. станция vs победитель
def obs_day(m, routine_only=True):
    ser = M.get(m["st"], [])
    tz = m["tz"]; day0 = datetime.fromisoformat(m["d"]).replace(tzinfo=tz); day1 = day0 + timedelta(days=1)
    conv = (lambda c: c * 9 / 5 + 32) if m["unit"] == "F" else (lambda c: c)
    return [(t, conv(c)) for t, c, sp in ser if day0 <= t.astimezone(tz) < day1 and (not routine_only or not sp)]

BAD = set()
def agree():
    P("\n## 3. Макс по часовым METAR (без SPECI) vs победитель рынка")
    bc = collections.defaultdict(lambda: [0, 0, collections.Counter()])
    for m in MKTS:
        od = obs_day(m)
        if len(od) < 18: continue
        last_b = m["snaps"][-1][1]
        tmax = max(x[1] for x in od); bk = L.bucket_for_temp(last_b, tmax, m["unit"])
        r = bc[m["slug"]]; r[0] += 1; r[1] += (bk == m["w"])
        if bk != m["w"]:
            o = L.ordered(last_b); r[2][(o.index(m["w"]) - o.index(bk)) if (bk in o and m["w"] in o) else None] += 1
    tot = [0, 0]
    for s in sorted(bc, key=lambda s: -bc[s][0]):
        n, h, c = bc[s]; tot[0] += n; tot[1] += h
        P(f"  {s:15s} дней={n:3d} совпало={h/n*100:5.1f}%  расхождения(победитель−станция): {dict(c) if c else ''}")
    P(f"  ВСЕГО дней={tot[0]} совпало={tot[1]/max(tot[0],1)*100:.1f}%")
    for s_, (n, h, c) in bc.items():
        if n < 5 or h / n < 0.90: BAD.add(s_)
    P(f"  исключаем из правил (совпадение <90% или <5 дней): {sorted(BAD)}")

# ------------------------------------------------------------ 4. фиксация / станция выше лидера
def station_state(od, ts, unit):
    """по наблюдениям ≤ ts−3мин: (t_now, t_max, n_fall, t_of_max)"""
    cut = L.parse_ts(ts) - timedelta(minutes=3)
    before = [(t, x) for t, x in od if t <= cut]
    if not before: return None
    drop = 2.0 if unit == "F" else 1.0
    tmax = max(x for _, x in before); i_max = max(i for i, (_, x) in enumerate(before) if x == tmax)
    tail = before[i_max + 1:]
    n_fall = 0
    for _, x in tail:
        if x <= tmax - drop + 1e-9: n_fall += 1
        else: n_fall = 0
    # n_fall — длина хвоста подряд ниже порога (с конца)
    n_fall = 0
    for _, x in reversed(tail):
        if x <= tmax - drop + 1e-9: n_fall += 1
        else: break
    return dict(t_now=before[-1][1], t_max=tmax, n_fall=n_fall, t_of_max=before[i_max][0], n_obs=len(before))

def lockin():
    P("\n## 4. Новые правила на данных станции (ask+1¢, одна сделка на рынок, первый сигнал)")
    A = []; A_ctrl = []; B = []; B_ctrl = []; A2 = []; A2_ctrl = []
    for m in MKTS:
        if m["slug"] in BAD: continue
        od = obs_day(m)
        if len(od) < 12: continue
        tz = m["tz"]; doneA = doneB = doneA2 = False
        for i_s, ((ts, b), h) in enumerate(zip(m["snaps"], m["hte"])):
            nxt = m["snaps"][i_s + 1][1] if i_s + 1 < len(m["snaps"]) else {}
            lt = L.parse_ts(ts).astimezone(tz); lh = lt.hour + lt.minute / 60
            if lt.date().isoformat() != m["d"]: continue
            ss = station_state(od, ts, m["unit"])
            if not ss: continue
            bk = L.bucket_for_temp(b, ss["t_max"], m["unit"])
            if not bk or bk not in b: continue
            keys = [k for k in b if L.price(b, k) is not None]
            if not keys: continue
            lead = max(keys, key=lambda k: L.price(b, k)); lask = b[lead][2]
            ask = b[bk][2]
            # A: фиксация
            # A2: фиксация почти-наверняка: та же станция, ask 93-99 → малый выигрыш; контроль — лидер ≥93 в тот же час БЕЗ условия станции
            if not doneA2 and lh >= 14 and ss["n_fall"] >= 2 and ask and 93 <= ask <= 99:
                A2.append(dict(slug=m["slug"], d=m["d"], ask=ask, win=(bk == m["w"]), lh=round(lh, 1), tmax=ss["t_max"], same=(bk == lead), lead_ask=lask, unit=m["unit"]))
                doneA2 = True
            if not doneA and lh >= 14 and ss["n_fall"] >= 2 and ask and 1 <= ask <= 93:
                A.append(dict(slug=m["slug"], d=m["d"], ask=ask, win=(bk == m["w"]), lh=round(lh, 1), tmax=ss["t_max"], same=(bk == lead), lead_ask=lask, unit=m["unit"]))
                if lask: A_ctrl.append(dict(slug=m["slug"], d=m["d"], ask=lask, win=(lead == m["w"])))
                doneA = True
            # B: станция уже выше лидера днём
            o = L.ordered(b)
            if not doneB and 12 <= lh < 17 and bk in o and lead in o and o.index(bk) > o.index(lead) and ask and 1 <= ask <= 85:
                lag = round((L.parse_ts(ts) - ss["t_of_max"]).total_seconds() / 60)
                B.append(dict(slug=m["slug"], d=m["d"], ts=ts, bucket=bk, lead=lead, ask=ask, bid=b[bk][1], win=(bk == m["w"]), lh=round(lh, 1), tmax=ss["t_max"], t_now=ss["t_now"], lead_ask=lask, unit=m["unit"], ask_next=(nxt.get(bk) or (None, None, None))[2], lag_min=lag))
                if lask: B_ctrl.append(dict(slug=m["slug"], d=m["d"], ask=lask, win=(lead == m["w"])))
                doneB = True
    # контроль для A2: лидер с ask 93-99 в первый снапшот после 14:00 local, без станции
    for m in MKTS:
        if m["slug"] in BAD: continue
        for (ts, b), h in zip(m["snaps"], m["hte"]):
            lt = L.parse_ts(ts).astimezone(m["tz"]); lh = lt.hour + lt.minute / 60
            if lt.date().isoformat() != m["d"] or lh < 14: continue
            keys = [k for k in b if L.price(b, k) is not None]
            if not keys: continue
            lead = max(keys, key=lambda k: L.price(b, k)); lask = b[lead][2]
            if lask and 93 <= lask <= 99:
                A2_ctrl.append(dict(slug=m["slug"], d=m["d"], ask=lask, win=(lead == m["w"]))); break
    for name, R, C in (("A. ФИКСАЦИЯ (≥14:00 local, 2 чтения ниже max−2°F/1°C, ask 1-93)", A, A_ctrl),
                       ("A2. ФИКСАЦИЯ-ДОБОР (то же, ask 93-99)", A2, A2_ctrl),
                       ("B. СТАНЦИЯ ВЫШЕ ЛИДЕРА (12-17 local, ask 1-85)", B, B_ctrl)):
        P(f"\n### {name}")
        P(fmt(R, "правило")); P(fmt(C, "контроль: лидер в тот же момент"))
        P("  по неделям:")
        byw = collections.defaultdict(list)
        for r in R: byw[week(r["d"])].append(r)
        for wk in sorted(byw): P("    " + fmt(byw[wk], wk))
        P("  по цене входа:")
        for a0, a1 in [(1, 30), (30, 50), (50, 70), (70, 80), (80, 90), (90, 93), (93, 95), (95, 97), (97, 100)]:
            P("    " + fmt([r for r in R if a0 <= r["ask"] < a1], f"ask {a0}-{a1}"))
        P("  US (°F) vs intl (°C):")
        P("    " + fmt([r for r in R if r["unit"] == "F"], "US")); P("    " + fmt([r for r in R if r["unit"] != "F"], "intl"))
        if R and "same" in R[0]:
            P("    " + fmt([r for r in R if r["same"]], "корзина = лидер")); P("    " + fmt([r for r in R if not r["same"]], "корзина ≠ лидер"))
        P("  по часу сигнала:")
        for h0, h1 in [(9, 12), (12, 14), (14, 16), (16, 18), (18, 24)]:
            P("    " + fmt([r for r in R if h0 <= r["lh"] < h1], f"{h0}-{h1}h"))
        P("  по городам (n≥5):")
        bc = collections.defaultdict(list)
        for r in R: bc[r["slug"]].append(r)
        for s in sorted(bc, key=lambda s: -len(bc[s])):
            if len(bc[s]) >= 5: P("    " + fmt(bc[s], s))
        P("  проигрыши (город дата ask tmax час):")
        for r in R:
            if not r["win"]: P(f"    {r['slug']} {r['d']} ask={r['ask']:.0f} tmax={r['tmax']:.1f} {r['lh']}h")
        if R and "ask_next" in R[0]:
            P("  все сделки B (город дата час корзина лидер bid/ask → ask через час; t_now/tmax; лаг от макс-наблюдения):")
            for r in R: P(f"    {'✅' if r['win'] else '❌'} {r['slug']:13s} {r['d']} {r['lh']:4.1f}h {r['bucket']:>14s} лидер {r['lead']:>14s} {r['bid'] if r['bid'] is not None else '-'}/{r['ask']:.0f} → {r['ask_next'] if r['ask_next'] is not None else '-'}  t={r['t_now']:.1f}/{r['tmax']:.1f} лаг {r['lag_min']}мин")
    return A, A2, B

if __name__ == "__main__":
    calib(); walk(); agree(); A, A2, B = lockin()
    if "--md" in sys.argv:
        (ROOT / "reports" / "2026-11-09-postmortem-data.md").write_text("# Постмортем 09.11 — данные\n\n```\n" + "\n".join(OUT) + "\n```\n", encoding="utf-8")
        json.dump(dict(A=A, A2=A2, B=B), open(ROOT / "research" / "lockin_trades.json", "w"), ensure_ascii=False, indent=0, default=str)
