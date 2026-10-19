"""Журнал «КОРЗИНА НИЖЕ ЛИДЕРА» — down@21 (решение владельца 11.10: «добавь отдельно,
начнём собирать статистику»). Виртуально, не деньги, отдельно от ТОП-10.

Основание — city_lab.py 11.10: по всем 41 городам корзина НИЖЕ лидера за 21 ч до конца
местных суток (03:00 местного) выигрывает 146/477 и даёт +9,1%/сд в обеих половинах дат,
корзина ВЫШЕ лидера — 120/499, −20%/сд. Проверяем: это смещение рынка или шум.

Правило (как trade_adjacent в strategy_lab): в снапшоте за H=21 ч до конца местных суток
лидер PM стоит 40-70¢ по last, соседняя корзина НИЖЕ стоит 15-45¢ и ask ≤ 50 → покупка
нижней по ask, size 1.0, одна сделка на город-день. Все города (observe_data + data +
pilot_data). Записи пишутся задним числом из собранных снапшотов (без заглядывания вперёд),
только для рынков, ещё не закрытых на момент записи; резолв — корзина ≥97¢ в последнем
снапшоте. Старт форварда — 2026-10-11.

  python3 down21.py --update            — дописать входы и резолвы
  python3 down21.py --stats [--tg файл] — итог и по городам
  python3 down21.py --backtest          — та же механика по всей истории (справочно)
"""
import glob, json, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import strategy_lab as L

ROOT = Path(__file__).parent
H = 21
START = "2026-10-11"
KEY = "down21"


def load_forecast(d):
    p = ROOT / "forecasts" / f"{d}.json"
    return (json.load(open(p, encoding="utf-8")) if p.exists() else {"date": d, "picks": {}}), p


def evaluate(S, slug, d):
    """(entry|None, winner|None, closed) — entry без результата; closed = рынок закрыт."""
    v = S.get((slug, d))
    if not v or len(v["snaps"]) < 2: return None, None, False
    snaps = v["snaps"]; tz = L.tz_of(slug)
    hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    now_hte = L.hours_to_end(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), d, tz)
    w = L.winner_of(snaps) if (hte[-1] <= 6 or now_hte <= 0) else None
    sa = L.snap_at(snaps, hte, H)
    if not sa: return None, w, w is not None
    ts, b, h = sa
    tr = L.trade_adjacent(b, w or "", -1)
    if not tr: return {"skip": True, "ts_utc": ts}, w, w is not None
    bucket, ask, _ = tr
    lead = max(b, key=lambda k: L.price(b, k) or 0)
    return {"bucket": bucket, "leader": lead, "lead_last": L.price(b, lead), "ask": ask, "ts_utc": ts, "hte": round(h, 1)}, w, w is not None


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
                if closed and not e.get("skip"):
                    continue  # рынок уже закрыт, а записи не было — задним числом не пишем (форвард)
                if e.get("skip"):
                    if closed: J[slug] = {"skip": True, "winner": w}; changed = True
                    continue
                J[slug] = dict(e, winner=None, hit=None, pnl=None); changed = True; rec = J[slug]
                log.append(f"{d} {slug}: ниже лидера {rec['leader']} ({rec['lead_last']}¢) → {rec['bucket']} по {rec['ask']}¢")
            if rec and not rec.get("skip") and rec.get("winner") is None and w:
                hit = rec["bucket"] == w
                rec.update(winner=w, hit=hit, pnl=round((100 / rec["ask"] - 1) if hit else -1.0, 3)); changed = True
                log.append(f"{d} {slug}: {rec['bucket']} → {w} {'✅' if hit else '❌'} {rec['pnl']:+.2f}")
        if changed: json.dump(f, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return log


def stats(tg=None):
    per = defaultdict(lambda: [0, 0, 0.0]); byday = defaultdict(lambda: [0, 0, 0.0]); open_n = 0
    for p in sorted(glob.glob(str(ROOT / "forecasts" / "*.json"))):
        d = Path(p).stem
        if d < START: continue
        for slug, r in json.load(open(p, encoding="utf-8")).get(KEY, {}).items():
            if r.get("skip"): continue
            if r.get("winner") is None: open_n += 1; continue
            per[slug][0] += 1; per[slug][1] += bool(r["hit"]); per[slug][2] += r["pnl"]
            byday[d][0] += 1; byday[d][1] += bool(r["hit"]); byday[d][2] += r["pnl"]
    T = [0, 0, 0.0]
    for n, h, pn in byday.values(): T[0] += n; T[1] += h; T[2] += pn
    lines = ["⬇️ <b>НИЖЕ ЛИДЕРА down@21</b> — отдельный журнал с 11.10 (виртуально, по ask, все города)"]
    lines.append(f"Итого: {T[1]}/{T[0]} ({T[1]/T[0]*100:.0f}%), P&L {T[2]:+.2f} ({T[2]/T[0]*100:+.0f}%/сд)" if T[0] else "Итого: закрытых сделок пока нет") 
    if open_n: lines.append(f"открытых входов: {open_n}")
    for d in sorted(byday)[-3:]:
        n, h, pn = byday[d]; lines.append(f"• {d[8:10]}.{d[5:7]}: {h}/{n} {pn:+.2f}")
    worst = sorted(per.items(), key=lambda kv: kv[1][2])[:3]; best = sorted(per.items(), key=lambda kv: -kv[1][2])[:3]
    if per: lines.append("лучшие: " + ", ".join(f"{c} {v[1]}/{v[0]} {v[2]:+.1f}" for c, v in best) + "; худшие: " + ", ".join(f"{c} {v[1]}/{v[0]} {v[2]:+.1f}" for c, v in worst))
    text = "\n".join(lines); print(text)
    if tg: Path(tg).write_text(text, encoding="utf-8")
    return text


def backtest():
    S = L.load(); per = defaultdict(lambda: [0, 0, 0.0]); T = [0, 0, 0.0]; half = defaultdict(lambda: [0, 0, 0.0])
    for (slug, d), v in S.items():
        snaps = v["snaps"]
        if len(snaps) < 3: continue
        w = L.winner_of(snaps)
        if not w: continue
        tz = L.tz_of(slug); hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
        if hte[-1] > 6: continue
        sa = L.snap_at(snaps, hte, H)
        if not sa: continue
        tr = L.trade_adjacent(sa[1], w, -1)
        if not tr: continue
        hit = tr[2] > 0
        for k in (per[slug], T, half["≤2026-09-21" if d <= "2026-09-21" else ">2026-09-21"]):
            k[0] += 1; k[1] += hit; k[2] += tr[2]
    print(f"down@21 бэктест: {T[1]}/{T[0]} {T[2]:+.2f} ({T[2]/T[0]*100:+.1f}%/сд)")
    for k, v in half.items(): print(f"  {k}: {v[1]}/{v[0]} {v[2]:+.2f} ({v[2]/v[0]*100:+.1f}%/сд)")
    for c, v in sorted(per.items(), key=lambda kv: -kv[1][2]): print(f"  {c:15s} {v[1]}/{v[0]} {v[2]:+.2f}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--update" in a:
        for line in update(datetime.now(timezone.utc).date().isoformat()): print(line)
    if "--stats" in a:
        stats(a[a.index("--tg") + 1] if "--tg" in a else None)
    if "--backtest" in a:
        backtest()
    if not a: print(__doc__)
