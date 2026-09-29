"""ТОП-10 — форвард-журнал пар «город → стратегия» из strategy_lab (одобрено
владельцем 29.09). Виртуально, деньгами не считается до отдельного решения.

Правила зафиксированы в calibration/strategy_candidates.json (candidates[]).
Для каждой пары и каждой даты рынка берём снапшот на горизонте H (часов до
конца локальных суток, окно [H−1.5, H+3]) и применяем правило механически —
без суждений, без моделей кроме cons@, вход по ask. Запись делается один раз
и не переписывается (в forecasts/<дата>.json ключ top10{slug}).

python3 top10.py --update            # дописать входы/резолвы за все даты с START
python3 top10.py --stats [--tg файл] # кумулятив по парам
python3 top10.py --backtest          # та же механика на всей истории (справочно)
"""
import json, sys, glob
from collections import defaultdict
from pathlib import Path
import strategy_lab as L

ROOT = Path(__file__).parent
START = "2026-09-30"
CAND = json.load(open(ROOT / "calibration" / "strategy_candidates.json", encoding="utf-8"))["candidates"]


def parse_strategy(name):
    kind, H = name.split("@"); H = int(H)
    band = None
    if ":" in kind:
        kind, b = kind.split(":"); lo, hi = b.split("-"); band = (int(lo), int(hi))
    return kind, band, H


def apply_rule(name, b, w, wx):
    kind, band, H = parse_strategy(name)
    if kind == "lead": return L.trade_leader(b, w, *band)
    if kind == "lock": return L.trade_leader(b, w, *band, spread=6)
    if kind == "down": return L.trade_adjacent(b, w, -1)
    if kind == "up": return L.trade_adjacent(b, w, +1)
    if kind == "second": return L.trade_second(b, w)
    if kind == "tail": return L.trade_tail(b, w)
    if kind == "cons": return L.trade_model(b, w, wx, "cons")
    if kind == "nbm": return L.trade_model(b, w, wx, "nbm")
    return None


def evaluate(S, slug, d, name):
    """(entry dict | None, winner | None). entry без результата, если рынок не закрыт."""
    v = S.get((slug, d))
    if not v or len(v["snaps"]) < 2: return None, None
    snaps = v["snaps"]; tz = L.tz_of(slug)
    hte = [L.hours_to_end(t, d, tz) for t, _ in snaps]
    kind, band, H = parse_strategy(name)
    sa = L.snap_at(snaps, hte, H)
    if not sa: return None, None
    ts, b, h = sa
    wx = v["wx"].get(ts) or (max(((t, x) for t, x in v["wx"].items() if t <= ts), default=(None, None))[1])
    w = L.winner_of(snaps) if hte[-1] <= 6 else None
    tr = apply_rule(name, b, w or "", wx)
    if not tr: return {"skip": True}, w
    bucket, ask, _ = tr
    return {"strategy": name, "bucket": bucket, "ask": ask, "ts_utc": ts, "hte": round(h, 1)}, w


def load_forecast(d):
    p = ROOT / "forecasts" / f"{d}.json"
    return (json.load(open(p, encoding="utf-8")) if p.exists() else {}), p


def update(today):
    S = L.load()
    dates = sorted({d for (_, d) in S if START <= d <= today})
    log = []
    for d in dates:
        f, p = load_forecast(d); top = f.setdefault("top10", {}); changed = False
        for c in CAND:
            slug, name = c["city"], c["strategy"]
            rec = top.get(slug)
            if rec and rec.get("winner") is not None: continue
            e, w = evaluate(S, slug, d, name)
            if rec is None and e is not None:
                if e.get("skip"):
                    # фиксируем пропуск только когда рынок закрыт (чтобы не путать с «ещё нет снапшота»)
                    if w: top[slug] = {"strategy": name, "skip": True, "winner": w}; changed = True
                else:
                    top[slug] = dict(e, winner=None, hit=None, pnl=None); changed = True; rec = top[slug]
                    log.append(f"{d} {slug} {name}: {e['bucket']} по {e['ask']}¢ (−{e['hte']}ч)")
            if rec and not rec.get("skip") and rec.get("winner") is None and w:
                hit = rec["bucket"] == w
                rec.update(winner=w, hit=hit, pnl=round((100 / rec["ask"] - 1) if hit else -1.0, 3)); changed = True
                log.append(f"{d} {slug}: {rec['bucket']} → {w} {'✅' if hit else '❌'} {rec['pnl']:+.2f}")
        if changed:
            json.dump(f, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return log


def stats(tg=None):
    per = defaultdict(lambda: [0, 0, 0.0, 0]); byday = defaultdict(lambda: [0, 0, 0.0])
    for p in sorted(glob.glob(str(ROOT / "forecasts" / "*.json"))):
        d = Path(p).stem
        if d < START: continue
        f = json.load(open(p, encoding="utf-8"))
        for slug, r in f.get("top10", {}).items():
            k = f"{slug} {r['strategy']}"
            if r.get("skip"): per[k][3] += 1; continue
            if r.get("winner") is None: continue
            per[k][0] += 1; per[k][1] += bool(r["hit"]); per[k][2] += r["pnl"]
            byday[d][0] += 1; byday[d][1] += bool(r["hit"]); byday[d][2] += r["pnl"]
    T = [0, 0, 0.0]
    lines = ["🔟 <b>ТОП-10</b> — форвард-журнал с 30.09 (виртуально, по ask)"]
    for d in sorted(byday):
        n, h, pn = byday[d]; T[0] += n; T[1] += h; T[2] += pn
    if T[0]:
        lines.append(f"Итого: {T[1]}/{T[0]} ({T[1]/T[0]*100:.0f}%), P&L {T[2]:+.2f} ({T[2]/T[0]*100:+.0f}%/сд)")
    for k, v in sorted(per.items(), key=lambda kv: -kv[1][2]):
        n, h, pn, sk = v
        lines.append(f"• {k}: {h}/{n} {pn:+.2f}" + (f" (пропусков {sk})" if sk else "") if n else f"• {k}: сделок нет" + (f", пропусков {sk}" if sk else ""))
    text = "\n".join(lines)
    print(text)
    if tg: Path(tg).write_text(text, encoding="utf-8")
    return text


def backtest():
    S = L.load()
    for c in CAND:
        rows = []
        for (slug, d) in sorted(S):
            if slug != c["city"]: continue
            e, w = evaluate(S, slug, d, c["strategy"])
            if e and not e.get("skip") and w:
                hit = e["bucket"] == w; rows.append((100 / e["ask"] - 1) if hit else -1.0)
        n = len(rows)
        print(f"{c['city']:14s} {c['strategy']:16s} n={n:2d} hit={sum(1 for r in rows if r>0):2d} pnl={sum(rows):+.2f}")


if __name__ == "__main__":
    a = sys.argv[1:]
    import datetime as dt
    today = dt.date.today().isoformat()
    if "--update" in a:
        for l in update(today): print(l)
    if "--stats" in a:
        stats(a[a.index("--tg") + 1] if "--tg" in a else None)
    if "--backtest" in a:
        backtest()
    if not a:
        print(__doc__)
