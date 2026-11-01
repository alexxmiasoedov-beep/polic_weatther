"""«ОТКАТ ЛИДЕРА» — форвард-журнал новой стратегии (глубокая лаборатория 01.11,
reports/2026-11-01-deep-lab.md, раздел «Стратегия»). Виртуально, не деньги.

Правило (предрегистрировано 01.11, менять только решением владельца):
  • снапшот в окне 16–20 ч до конца локальных суток, ближайший к H=18
    (один вход на рынок-день);
  • лидер PM по last в [40, 70), ask ≤ 72, стакан (ask−bid) ≤ 8;
  • тот же лидер был лидером и ~6 ч раньше (окно h+4.5..h+7.5);
  • last лидера упал на ≥3¢ против снапшота ~3 ч раньше (окно h+1.5..h+4.5);
  • покупка лидера по ask, size 1.0; резолв — корзина ≥97¢ в последнем снапшоте.
Все 41 город. Бэктест 27.08–01.11: см. --backtest (на момент записи n=133, 63%, +23.4%/сд, t=2.8,
9 недель из 10 в плюсе; по тонкой сетке горизонтов эффект +4…+20%/сд на H=13…23,
отрицателен на H≥25 — поэтому окно 16–20).

python3 pullback.py --update            # входы/резолвы с START
python3 pullback.py --stats [--tg файл]
python3 pullback.py --backtest
"""
import json, sys, glob, math, statistics, datetime as dt
from collections import defaultdict
from pathlib import Path
import strategy_lab as L

ROOT = Path(__file__).parent
START = "2026-11-01"
H = 18; WIN = (16.0, 20.0)
KEY = "pullback"


def signal(snaps, hte):
    cand = [(t, b, h) for (t, b), h in zip(snaps, hte) if WIN[0] <= h <= WIN[1]]
    if not cand: return None
    ts, b, h = min(cand, key=lambda x: abs(x[2] - H))
    keys = [k for k in b if L.price(b, k) is not None]
    if not keys: return None
    lead = max(keys, key=lambda k: L.price(b, k)); lp = L.price(b, lead); bid, ask = b[lead][1], b[lead][2]
    if not (40 <= lp < 70) or ask is None or ask > 72: return None
    if bid is not None and ask - bid > 8: return None
    p3c = [(t2, b2, h2) for (t2, b2), h2 in zip(snaps, hte) if h + 1.5 <= h2 <= h + 4.5 and lead in b2 and L.price(b2, lead) is not None]
    if not p3c: return None
    _, b3, _ = min(p3c, key=lambda x: abs(x[2] - (h + 3))); lp3 = L.price(b3, lead)
    if lp - lp3 > -3: return None
    c6 = [(t2, b2, h2) for (t2, b2), h2 in zip(snaps, hte) if h + 4.5 <= h2 <= h + 7.5]
    if not c6: return None
    _, b6, _ = min(c6, key=lambda x: abs(x[2] - (h + 6)))
    k6 = [k for k in b6 if L.price(b6, k) is not None]
    if not k6 or max(k6, key=lambda k: L.price(b6, k)) != lead: return None
    return dict(bucket=lead, last=lp, bid=bid, ask=ask, last3=lp3, hte=round(h, 1), ts_utc=ts)


def update(today):
    S = L.load(); log = []
    for d in sorted({d for (_, d) in S if START <= d <= today}):
        p = ROOT / "forecasts" / f"{d}.json"
        f = json.load(open(p, encoding="utf-8")) if p.exists() else {}
        J = f.setdefault(KEY, {}); changed = False
        for (slug, dd), v in S.items():
            if dd != d or len(v["snaps"]) < 3: continue
            snaps = v["snaps"]; tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
            rec = J.get(slug)
            if rec is None:
                if hte[-1] > WIN[0]: continue  # окно ещё не прошло целиком
                sig = signal(snaps, hte)
                if sig:
                    J[slug] = dict(sig, winner=None, hit=None, pnl=None); changed = True; rec = J[slug]
                    log.append(f"{d} {slug}: {sig['bucket']} по {sig['ask']}¢ (last {sig['last']} ← {sig['last3']}, −{sig['hte']}ч)")
            if rec and rec.get("winner") is None:
                day_over = L.hours_to_end(dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), d, tz) <= 0
                w = L.winner_of(snaps) if (hte[-1] <= 6 or day_over) else None
                if w:
                    hit = rec["bucket"] == w
                    rec.update(winner=w, hit=hit, pnl=round((100 / rec["ask"] - 1) if hit else -1.0, 3)); changed = True
                    log.append(f"{d} {slug}: {rec['bucket']} → {w} {'✅' if hit else '❌'} {rec['pnl']:+.2f}")
        if changed:
            json.dump(f, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return log


def stats(tg=None):
    rows = []
    for p in sorted(glob.glob(str(ROOT / "forecasts" / "*.json"))):
        d = Path(p).stem
        if d < START: continue
        for slug, r in json.load(open(p, encoding="utf-8")).get(KEY, {}).items():
            rows.append((d, slug, r))
    done = [(d, s, r) for d, s, r in rows if r.get("winner") is not None]
    lines = ["↩️ <b>ОТКАТ ЛИДЕРА</b> — форвард-журнал с 01.11 (все города, окно 16–20 ч, лидер 40-70¢ просел ≥3¢ за 3 ч; виртуально, по ask)"]
    if done:
        n = len(done); h = sum(1 for _, _, r in done if r["hit"]); pn = sum(r["pnl"] for _, _, r in done)
        lines.append(f"Итого: {h}/{n} ({h/n*100:.0f}%), P&L {pn:+.2f} ({pn/n*100:+.0f}%/сд)")
    lines.append(f"открытых входов: {len(rows) - len(done)}")
    byday = defaultdict(lambda: [0, 0, 0.0])
    for d, _, r in done: byday[d][0] += 1; byday[d][1] += bool(r["hit"]); byday[d][2] += r["pnl"]
    for d in sorted(byday)[-5:]:
        n, h, pn = byday[d]; lines.append(f"• {d[8:]}.{d[5:7]}: {h}/{n} {pn:+.2f}")
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
        sig = signal(snaps, hte)
        if sig: rows.append((d, slug, sig["bucket"] == w, (100 / sig["ask"] - 1) if sig["bucket"] == w else -1.0))
    rows.sort()
    n = len(rows); h = sum(1 for r in rows if r[2]); pn = sum(r[3] for r in rows)
    sd = statistics.pstdev([r[3] for r in rows]); t = (pn / n) / (sd / math.sqrt(n)) if sd else 0
    print(f"ВСЕГО n={n} hit={h} ({h/n*100:.0f}%) pnl={pn:+.2f} ({pn/n*100:+.1f}%/сд) t={t:+.1f}")
    wk = defaultdict(list)
    for d, s, hit, p in rows: wk[(dt.date.fromisoformat(d) - dt.date(2026, 8, 27)).days // 7].append(p)
    for k in sorted(wk): print(f"  неделя {k} (с {dt.date(2026,8,27)+dt.timedelta(days=7*k)}): n={len(wk[k]):2d} {sum(wk[k]):+6.2f}")
    bc = defaultdict(list)
    for d, s, hit, p in rows: bc[s].append(p)
    print("  города в плюсе:", sum(1 for v in bc.values() if sum(v) > 0), "/", len(bc))
    for s, v in sorted(bc.items(), key=lambda kv: -sum(kv[1])): print(f"    {s:14s} n={len(v):2d} {sum(v):+6.2f}")


if __name__ == "__main__":
    a = sys.argv[1:]; today = dt.date.today().isoformat()
    if "--update" in a:
        for l in update(today): print(l)
    if "--stats" in a: stats(a[a.index("--tg") + 1] if "--tg" in a else None)
    if "--backtest" in a: backtest()
    if not a: print(__doc__)
