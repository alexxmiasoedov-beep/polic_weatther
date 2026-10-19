"""Журнал «НИЖЕ ЛИДЕРА» — портфель по итогам разбора всех данных 19.10
(reports/2026-10-19-strategy-review.md; решение владельца 19.10: «запускай журнал
«НИЖЕ» с 20.10»). Виртуально, не деньги, size 1.0, одна сделка на рынок, все города.

Основание: при лидере PM 40–70¢ победитель — лидер 50%, корзина НИЖЕ 25%, корзина ВЫШЕ 20%,
а рынок ценит соседей одинаково (22¢ / 23¢). «Ниже» недооценена, «выше» переоценена на всех
горизонтах и во всех неделях 27.08–18.10 (1216 рынков).

Правило (вход в снапшоте за H=21 ч до конца местных суток, окно −1,5…+3 ч, покупка по ask):
  M1 — у города есть модели (wx) и СЫРАЯ медиана моделей (без bias) попадает в корзину ниже
       лидера, лидер 40–70¢ по last, ask корзины ≤ 55 → купить корзину ниже;
  D1 — иначе: лидер 40–70¢, корзина ниже 25–45¢ по last, ask ≤ 50, и «ниже» стоит не дешевле,
       чем «выше» + 8¢ (если «выше» есть) → купить корзину ниже;
  никогда: корзина выше лидера. Хвосты < 20¢ допускаются ТОЛЬКО по ветке M1 (модели прямо
       за корзину ниже): в бэктесте они дают 10/36 +49.8 из +68.5 ветки M1; без моделей (D1)
       дешевле 25¢ не покупаем.
Бэктест на всех данных 27.08–18.10 (--backtest, 19.10): 142/356 +127.14 (+35,7%/сд),
1-я половина дат +52,9%/сд, 2-я +22,0%/сд, 8/8 недель в плюсе; M1 44/120 +68.48, D1 98/236 +58.66.

Записи пишутся задним числом из снапшотов (без заглядывания вперёд), только для рынков, ещё не
закрытых на момент записи; резолв — корзина ≥97¢ в последнем снапшоте. Старт форварда — 2026-10-20.

  python3 below.py --update            — дописать входы и резолвы
  python3 below.py --stats [--tg файл] — итог, по видам (M1/D1), по дням, по городам
  python3 below.py --backtest          — та же механика по всей истории (справочно)
"""
import glob, json, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import strategy_lab as L

ROOT = Path(__file__).parent
H = 21
START = "2026-10-20"
KEY = "below"
LEAD_LO, LEAD_HI = 40, 70
M1_MAX_ASK = 55
D1_LO, D1_HI, D1_MAX_ASK, D1_GAP = 25, 45, 50, 8


def load_forecast(d):
    p = ROOT / "forecasts" / f"{d}.json"
    return (json.load(open(p, encoding="utf-8")) if p.exists() else {"date": d, "picks": {}}), p


def pick(b, wx):
    """(entry|None) по снапшоту b и моделям wx: словарь bucket/kind/… без результата."""
    keys = [k for k in b if L.price(b, k) is not None]
    if len(keys) < 2: return None
    lead = max(keys, key=lambda k: L.price(b, k)); pl = L.price(b, lead)
    if not (LEAD_LO <= pl < LEAD_HI): return None
    order = L.ordered(b); i = order.index(lead)
    if i == 0: return None
    below = order[i - 1]; above = order[i + 1] if i + 1 < len(order) else None
    pb, b_ask = L.price(b, below), b[below][2]
    pa = L.price(b, above) if above else None
    cons = None
    if wx:
        vals = [wx[m] for m in L.MODELS if isinstance(wx.get(m), (int, float))]
        if len(vals) >= 3: cons = statistics.median(vals)
    base = dict(leader=lead, lead_last=pl, below_last=pb, above_last=pa)
    if cons is not None and L.bucket_for_temp(b, cons, "F") == below and b_ask and b_ask <= M1_MAX_ASK:
        return dict(base, bucket=below, ask=b_ask, kind="M1", cons=round(cons, 1))
    if pb is not None and D1_LO <= pb <= D1_HI and b_ask and b_ask <= D1_MAX_ASK and (pa is None or pb >= pa + D1_GAP):
        return dict(base, bucket=below, ask=b_ask, kind="D1", cons=round(cons, 1) if cons is not None else None)
    return None


def evaluate(S, slug, d):
    """(entry|None, winner|None, closed)."""
    v = S.get((slug, d))
    if not v or len(v["snaps"]) < 2: return None, None, False
    snaps = v["snaps"]; tz = L.tz_of(slug)
    hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    now_hte = L.hours_to_end(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), d, tz)
    w = L.winner_of(snaps) if (hte[-1] <= 6 or now_hte <= 0) else None
    sa = L.snap_at(snaps, hte, H)
    if not sa: return None, w, w is not None
    ts, b, h = sa
    wx = v["wx"].get(ts) or (max(((t, x) for t, x in v["wx"].items() if t <= ts), default=(None, None))[1])
    e = pick(b, wx)
    if not e: return {"skip": True, "ts_utc": ts}, w, w is not None
    e.update(ts_utc=ts, hte=round(h, 1))
    return e, w, w is not None


def update(today):
    S = L.load(); log = []
    # рынки до послезавтра включительно: для городов восточнее UTC снапшот за 21 ч до конца
    # местных суток попадает на предыдущую дату UTC (до 19.10 азиатские рынки пропускались — баг)
    from datetime import date, timedelta
    upto = (date.fromisoformat(today) + timedelta(days=2)).isoformat()
    dates = sorted({d for (_, d) in S if START <= d <= upto})
    for d in dates:
        f, p = load_forecast(d); J = f.setdefault(KEY, {}); changed = False
        for slug in sorted({s for (s, dd) in S if dd == d}):
            rec = J.get(slug)
            if rec and rec.get("winner") is not None: continue
            e, w, closed = evaluate(S, slug, d)
            if rec is None:
                if e is None: continue
                if e.get("skip"):
                    if closed: J[slug] = {"skip": True, "winner": w}; changed = True
                    continue
                if closed: continue  # закрыт без записи — задним числом не пишем (форвард)
                J[slug] = dict(e, winner=None, hit=None, pnl=None); changed = True; rec = J[slug]
                log.append(f"{d} {slug} [{e['kind']}]: {e['bucket']} по {e['ask']}¢ (лидер {e['leader']} {e['lead_last']}¢"
                           + (f", модели {e['cons']}" if e.get('cons') is not None else "") + ")")
            if rec and not rec.get("skip") and rec.get("winner") is None and w:
                hit = rec["bucket"] == w
                rec.update(winner=w, hit=hit, pnl=round((100 / rec["ask"] - 1) if hit else -1.0, 3)); changed = True
                log.append(f"{d} {slug} [{rec.get('kind')}]: {rec['bucket']} → {w} {'✅' if hit else '❌'} {rec['pnl']:+.2f}")
        if changed: json.dump(f, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return log


def stats(tg=None):
    per = defaultdict(lambda: [0, 0, 0.0]); byday = defaultdict(lambda: [0, 0, 0.0]); kind = defaultdict(lambda: [0, 0, 0.0]); open_n = 0
    for p in sorted(glob.glob(str(ROOT / "forecasts" / "*.json"))):
        d = Path(p).stem
        if d < START: continue
        for slug, r in json.load(open(p, encoding="utf-8")).get(KEY, {}).items():
            if r.get("skip"): continue
            if r.get("winner") is None: open_n += 1; continue
            for k in (per[slug], byday[d], kind[r.get("kind", "?")]):
                k[0] += 1; k[1] += bool(r["hit"]); k[2] += r["pnl"]
    T = [sum(v[0] for v in byday.values()), sum(v[1] for v in byday.values()), sum(v[2] for v in byday.values())]
    lines = ["⬇️ <b>НИЖЕ ЛИДЕРА</b> — форвард-журнал с 20.10 (решение владельца 19.10; все города, вход за 21 ч по ask, виртуально)"]
    lines.append(f"Итого: {T[1]}/{T[0]} ({T[1]/T[0]*100:.0f}%), P&L {T[2]:+.2f} ({T[2]/T[0]*100:+.0f}%/сд)" if T[0] else "Итого: закрытых сделок пока нет")
    for k in ("M1", "D1"):
        if kind[k][0]: lines.append(f"{k} ({'модели за «ниже»' if k == 'M1' else 'сосед 25-45¢'}): {kind[k][1]}/{kind[k][0]} {kind[k][2]:+.2f}")
    if open_n: lines.append(f"открытых входов: {open_n}")
    for d in sorted(byday)[-3:]:
        n, h, pn = byday[d]; lines.append(f"• {d[8:10]}.{d[5:7]}: {h}/{n} {pn:+.2f}")
    worst = sorted(per.items(), key=lambda kv: kv[1][2])[:3]; best = sorted(per.items(), key=lambda kv: -kv[1][2])[:3]
    if per: lines.append("лучшие: " + ", ".join(f"{c} {v[1]}/{v[0]} {v[2]:+.1f}" for c, v in best) + "; худшие: " + ", ".join(f"{c} {v[1]}/{v[0]} {v[2]:+.1f}" for c, v in worst))
    text = "\n".join(lines); print(text)
    if tg: Path(tg).write_text(text, encoding="utf-8")
    return text


def backtest():
    S = L.load(); rows = []
    for (slug, d), v in S.items():
        snaps = v["snaps"]
        if len(snaps) < 3: continue
        w = L.winner_of(snaps)
        if not w: continue
        tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
        if hte[-1] > 6: continue
        sa = L.snap_at(snaps, hte, H)
        if not sa: continue
        ts, b, h = sa
        wx = v["wx"].get(ts) or (max(((t, x) for t, x in v["wx"].items() if t <= ts), default=(None, None))[1])
        e = pick(b, wx)
        if not e: continue
        rows.append((d, slug, e["kind"], e["ask"], (100 / e["ask"] - 1) if e["bucket"] == w else -1.0))
    def st(rs):
        n = len(rs); h = sum(1 for r in rs if r[4] > 0); t = sum(r[4] for r in rs)
        return f"{h}/{n} {t:+.2f} ({t/n*100 if n else 0:+.1f}%/сд)"
    ds = sorted({r[0] for r in rows}); mid = ds[len(ds) // 2]
    print(f"НИЖЕ ЛИДЕРА бэктест (H={H}): {st(rows)} | ≤{mid} {st([r for r in rows if r[0] <= mid])} | >{mid} {st([r for r in rows if r[0] > mid])}")
    for k in ("M1", "D1"): print(f"  {k}: {st([r for r in rows if r[2] == k])}")
    byw = defaultdict(list)
    for r in rows:
        from datetime import date
        y, m, dd = map(int, r[0].split("-")); byw[(date(y, m, dd) - date(2026, 8, 27)).days // 7].append(r)
    print("  по неделям: " + " ".join(f"н{w} {sum(r[4] for r in byw[w]):+.1f}/{len(byw[w])}" for w in sorted(byw)))
    per = defaultdict(list)
    for r in rows: per[r[1]].append(r)
    for c, rs in sorted(per.items(), key=lambda kv: -sum(r[4] for r in kv[1])): print(f"  {c:15s} {st(rs)}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--update" in a:
        for line in update(datetime.now(timezone.utc).date().isoformat()): print(line)
    if "--stats" in a: stats(a[a.index("--tg") + 1] if "--tg" in a else None)
    if "--backtest" in a: backtest()
    if not a: print(__doc__)
