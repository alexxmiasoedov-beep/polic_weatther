"""Журнал «СТОРОНА ГОРОДА» (решение владельца 11.10, бумага, не деньги).

Из deep_dive (reports/2026-10-11-deep-dive.md, таблица deep_dive_sides.md): у ряда городов есть
устойчивая СТОРОНА — куда станция систематически читает относительно вечернего/утреннего лидера PM,
пока лидер ещё стоит <70¢. Правило на город: горизонт H (часов до конца местных суток), сторона
(lead / below / above). Вход: в снапшоте за H ч лидер PM по last 25–69¢; целевая корзина — лидер,
или сосед ниже/выше; покупка по ask (ask ≤ 80), size 1.0, одна сделка на город-день.
Записи пишутся задним числом из собранных снапшотов (без заглядывания вперёд), только для рынков,
ещё не закрытых на момент записи; резолв — корзина ≥97¢ в последнем снапшоте. Старт 11.10.

Уровень A (5 городов: n=33–44, обе половины дат в плюсе) и уровень B (12 городов: одна половина в минусе, n<30 или
короткая история обзорных городов — наблюдение). Важно: в первом анализе 11.10 «выше/ниже» засчитывалось любой корзине с той стороны, а не купленной
соседней — из-за этого Даллас и SFO выглядели сильнее; здесь и в deep_dive_sides.md цифры исправлены.

  python3 side.py --update            — дописать входы и резолвы
  python3 side.py --stats [--tg файл] — итог (A/B отдельно) и по городам
  python3 side.py --backtest          — та же механика по истории (справочно)
"""
import glob, json, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import strategy_lab as L

ROOT = Path(__file__).parent
START = "2026-10-11"
KEY = "side"
RULES = [  # city, H, side, tier, основание (бэктест 27.08–10.10, покупка ИМЕННО целевой корзины по ask)
    ("atlanta", 30, "below", "A", "20/44 +148% (+170/+120)"),
    ("austin", 30, "above", "A", "11/44 +69% (+80/+54)"),
    ("miami", 24, "lead", "A", "29/44 +31% (+25/+40)"),
    ("kuala-lumpur", 18, "lead", "A", "26/38 +40% (+44/+36)"),
    ("houston", 18, "lead", "A", "20/33 +23% (+25/+21)"),
    ("dallas", 30, "above", "B", "13/37 +39% (−27/+102) — нестабильно, под наблюдением"),
    ("san-francisco", 24, "above", "B", "13/40 +49% (+102/−30) — нестабильно, под наблюдением"),
    ("denver", 30, "below", "B", "9/31 +63% (+20/+90)"),
    ("mexico-city", 24, "lead", "B", "16/27 +21% (+29/+13)"),
    ("shanghai", 18, "lead", "B", "17/25 +26% (+16/+32)"),
    # обзорные города (добавлены 11.10 решением владельца; данные с 07.09, первая половина 8–13 рынков)
    ("tokyo", 18, "below", "B", "10/24 +39% (+30/+43)"),
    ("beijing", 18, "below", "B", "12/30 +46% (+49/+45)"),
    ("taipei", 24, "above", "B", "11/31 +85% (+39/+110)"),
    ("madrid", 30, "above", "B", "12/29 +126% (+260/+44)"),
    ("chicago", 30, "above", "B", "11/29 +75% (+117/+52)"),
    ("nyc", 30, "lead", "B", "20/29 +35% (+46/+30)"),
    ("buenos-aires", 24, "lead", "B", "17/31 +31% (+38/+26)"),
]
RULE = {c: (H, side, tier, why) for c, H, side, tier, why in RULES}


def load_forecast(d):
    p = ROOT / "forecasts" / f"{d}.json"
    return (json.load(open(p, encoding="utf-8")) if p.exists() else {"date": d, "picks": {}}), p


def pick(b, side):
    keys = [k for k in b if L.price(b, k) is not None]
    if len(keys) < 2: return None
    lead = max(keys, key=lambda k: L.price(b, k)); pl = L.price(b, lead)
    if not (25 <= pl < 70): return None
    order = L.ordered(b); i = order.index(lead)
    if side == "lead": tgt = lead
    elif side == "below": tgt = order[i - 1] if i > 0 else None
    else: tgt = order[i + 1] if i + 1 < len(order) else None
    if not tgt: return None
    ask = b[tgt][2]
    if not ask or ask > 80: return None
    return dict(bucket=tgt, leader=lead, lead_last=pl, ask=ask)


def evaluate(S, slug, d):
    H, side, tier, why = RULE[slug]
    v = S.get((slug, d))
    if not v or len(v["snaps"]) < 2: return None, None, False
    snaps = v["snaps"]; tz = L.tz_of(slug)
    hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    now_hte = L.hours_to_end(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), d, tz)
    w = L.winner_of(snaps) if (hte[-1] <= 6 or now_hte <= 0) else None
    sa = L.snap_at(snaps, hte, H)
    if not sa: return None, w, w is not None
    ts, b, h = sa
    e = pick(b, side)
    if not e: return {"skip": True, "ts_utc": ts}, w, w is not None
    e.update(side=side, H=H, tier=tier, ts_utc=ts, hte=round(h, 1))
    return e, w, w is not None


def update(today):
    S = L.load(); log = []
    dates = sorted({d for (_, d) in S if START <= d <= today})
    for d in dates:
        f, p = load_forecast(d); J = f.setdefault(KEY, {}); changed = False
        for slug in RULE:
            rec = J.get(slug)
            if rec and rec.get("winner") is not None: continue
            e, w, closed = evaluate(S, slug, d)
            if rec is None:
                if e is None: continue
                if e.get("skip"):
                    if closed: J[slug] = {"skip": True, "winner": w, "side": RULE[slug][1], "tier": RULE[slug][2]}; changed = True
                    continue
                if closed: continue  # закрыт без записи — форвард не нарушаем
                J[slug] = dict(e, winner=None, hit=None, pnl=None); changed = True; rec = J[slug]
                log.append(f"{d} {slug} [{e['tier']}] {e['side']}@{e['H']}: {e['bucket']} по {e['ask']}¢ (лидер {e['leader']} {e['lead_last']}¢)")
            if rec and not rec.get("skip") and rec.get("winner") is None and w:
                hit = rec["bucket"] == w
                rec.update(winner=w, hit=hit, pnl=round((100 / rec["ask"] - 1) if hit else -1.0, 3)); changed = True
                log.append(f"{d} {slug}: {rec['bucket']} → {w} {'✅' if hit else '❌'} {rec['pnl']:+.2f}")
        if changed: json.dump(f, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return log


def stats(tg=None):
    per = defaultdict(lambda: [0, 0, 0.0, 0]); tier = defaultdict(lambda: [0, 0, 0.0]); open_n = 0
    for p in sorted(glob.glob(str(ROOT / "forecasts" / "*.json"))):
        d = Path(p).stem
        if d < START: continue
        for slug, r in json.load(open(p, encoding="utf-8")).get(KEY, {}).items():
            if r.get("skip"): per[slug][3] += 1; continue
            if r.get("winner") is None: open_n += 1; continue
            per[slug][0] += 1; per[slug][1] += bool(r["hit"]); per[slug][2] += r["pnl"]
            t = tier[r.get("tier", RULE.get(slug, (0, 0, "?"))[2])]; t[0] += 1; t[1] += bool(r["hit"]); t[2] += r["pnl"]
    lines = ["🧭 <b>СТОРОНА ГОРОДА</b> — бумажный журнал с 11.10 (17 городов: 5 уровня A, 12 уровня B; вход по ask, лидер &lt;70¢)"]
    T = [sum(v[0] for v in tier.values()), sum(v[1] for v in tier.values()), sum(v[2] for v in tier.values())]
    lines.append(f"Итого: {T[1]}/{T[0]} ({T[1]/T[0]*100:.0f}%), P&L {T[2]:+.2f} ({T[2]/T[0]*100:+.0f}%/сд)" if T[0] else "Итого: закрытых сделок пока нет")
    for k in ("A", "B"):
        if tier[k][0]: lines.append(f"уровень {k}: {tier[k][1]}/{tier[k][0]} {tier[k][2]:+.2f}")
    if open_n: lines.append(f"открытых входов: {open_n}")
    for c, H, side, t, why in RULES:
        n, h, pn, sk = per[c]
        lines.append(f"• {c} {side}@{H} [{t}]: " + (f"{h}/{n} {pn:+.2f}" if n else "сделок нет") + (f" (пропусков {sk})" if sk else ""))
    text = "\n".join(lines); print(text)
    if tg: Path(tg).write_text(text, encoding="utf-8")
    return text


def backtest():
    S = L.load()
    for c, H, side, t, why in RULES:
        rows = []
        for (slug, d), v in S.items():
            if slug != c or len(v["snaps"]) < 3: continue
            w = L.winner_of(v["snaps"])
            if not w: continue
            tz = L.tz_of(slug); hte = [L.hours_to_end(ts, d, tz) for ts, _ in v["snaps"]]
            now_hte = L.hours_to_end(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), d, tz)
            if hte[-1] > 6 and now_hte > 0: continue
            sa = L.snap_at(v["snaps"], hte, H)
            if not sa: continue
            e = pick(sa[1], side)
            if not e: continue
            rows.append((d, (100 / e["ask"] - 1) if e["bucket"] == w else -1.0))
        n = len(rows); h = sum(1 for _, p in rows if p > 0); tot = sum(p for _, p in rows)
        a = [p for d, p in rows if d <= "2026-09-21"]; b = [p for d, p in rows if d > "2026-09-21"]
        print(f"{c:15s} {side}@{H} [{t}] n={n} ✓{h} {tot:+.2f} ({tot/n*100 if n else 0:+.0f}%/сд) | ≤21.09 {sum(a)/len(a)*100 if a else 0:+.0f}% n={len(a)} | >21.09 {sum(b)/len(b)*100 if b else 0:+.0f}% n={len(b)}   [{why}]")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--update" in a:
        for line in update(datetime.now(timezone.utc).date().isoformat()): print(line)
    if "--stats" in a: stats(a[a.index("--tg") + 1] if "--tg" in a else None)
    if "--backtest" in a: backtest()
    if not a: print(__doc__)
