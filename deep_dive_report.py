"""Отчёт глубокого разбора по городам: читает reports/deep_dive_rows.json (deep_dive.py),
для каждого города и горизонта ищет триггеры.

Правило = (признак, порог по квантилю, направление ≥/≤) → целевая корзина (лидер / ниже /
выше лидера); вход по ask целевой корзины; одна сделка на город-день; оценка — n, попадания,
P&L/сд, split-half по дате (≤21.09 / после). Чтобы не утонуть в шуме отбора: признак должен
иметь ≥10 заполненных строк, правило — n≥8 и обе половины ≥+5%/сд; показываем ≤6 правил на
город, плюс базовые частоты (как часто побеждает лидер / ниже / выше при лидере <70¢).
python3 deep_dive_report.py
"""
import json, statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SPLIT = "2026-09-21"
FEATS = ["lead_price", "gap", "n_live", "spread", "mom3", "mom6", "vol_share", "vol_total", "kalshi_rel", "kalshi_price",
         "cons_minus_lead", "model_spread", "models_in_lead", "models_below", "models_above", "nbm_minus_lead", "precip_prob",
         "prevmax_minus_lead", "max_trend", "t06_minus_lead", "t09_minus_lead", "t12_minus_lead", "dew09", "wind09", "cloud09", "rain09",
         "todaymax_minus_lead", "below_price", "above_price"]
NAMES = {"lead_price": "цена лидера", "gap": "отрыв лидера от 2-й", "n_live": "корзин ≥10¢", "spread": "стакан лидера",
         "mom3": "ход лидера за 3 ч", "mom6": "ход лидера за 6 ч", "vol_share": "доля объёма лидера", "vol_total": "объём рынка",
         "kalshi_rel": "лидер Kalshi относительно PM (−1 ниже/0/+1 выше)", "kalshi_price": "цена лидера Kalshi",
         "cons_minus_lead": "медиана моделей − центр лидера", "model_spread": "разброс моделей", "models_in_lead": "доля моделей в корзине лидера",
         "models_below": "доля моделей ниже лидера", "models_above": "доля моделей выше лидера", "nbm_minus_lead": "NBM − центр лидера",
         "precip_prob": "вероятность осадков", "prevmax_minus_lead": "вчерашний максимум − центр лидера", "max_trend": "тренд максимумов (вчера − позавчера)",
         "t06_minus_lead": "станция 06:00 − центр лидера", "t09_minus_lead": "станция 09:00 − центр лидера", "t12_minus_lead": "станция 12:00 − центр лидера",
         "dew09": "точка росы 09:00", "wind09": "ветер 09:00, уз", "cloud09": "облачно (BKN/OVC) в 09:00", "rain09": "дождь в METAR 09:00",
         "todaymax_minus_lead": "максимум станции к моменту − центр лидера", "below_price": "цена корзины ниже", "above_price": "цена корзины выше"}
TARGETS = {"lead": 0, "below": -1, "above": 1}


def pnl(r, tgt):
    # попадание: для lead — победил лидер; для below/above — победила ИМЕННО соседняя корзина (её и покупаем)
    hit = (r["outcome"] == 0) if tgt == "lead" else (r.get("winner") == r.get(tgt))
    ask = {"lead": r.get("lead_ask"), "below": r.get("below_ask"), "above": r.get("above_ask")}[tgt]
    if not ask or ask <= 0 or ask > 80: return None
    return (100 / ask - 1) if hit else -1.0


def summ(rows, tgt):
    vals = [(r["date"], pnl(r, tgt)) for r in rows]; vals = [(d, p) for d, p in vals if p is not None]
    if not vals: return None
    n = len(vals); h = sum(1 for _, p in vals if p > 0); tot = sum(p for _, p in vals)
    a = [p for d, p in vals if d <= SPLIT]; b = [p for d, p in vals if d > SPLIT]
    return dict(n=n, hit=h, per=tot / n * 100, pnl=tot, ins=(sum(a) / len(a) * 100 if a else None, len(a)), oos=(sum(b) / len(b) * 100 if b else None, len(b)))


def quantiles(xs):
    xs = sorted(xs); q = []
    for f in (0.2, 0.35, 0.5, 0.65, 0.8):
        q.append(xs[min(len(xs) - 1, int(f * len(xs)))])
    return sorted(set(q))


def mine(rows):
    out = []
    for feat in FEATS:
        have = [r for r in rows if isinstance(r.get(feat), (int, float))]
        if len(have) < 10: continue
        for thr in quantiles([r[feat] for r in have]):
            for sign in ("ge", "le"):
                sel = [r for r in have if (r[feat] >= thr if sign == "ge" else r[feat] <= thr)]
                if len(sel) < 8 or len(sel) > len(have) - 3: continue
                for tgt in TARGETS:
                    s = summ(sel, tgt)
                    if not s or s["n"] < 8: continue
                    ins, oos = s["ins"], s["oos"]
                    if ins[0] is None or oos[0] is None or ins[1] < 3 or oos[1] < 3: continue
                    if ins[0] < 5 or oos[0] < 5: continue
                    out.append(dict(feat=feat, thr=thr, sign=sign, tgt=tgt, **s, robust=min(ins[0], oos[0])))
    # дедуп: по (feat, tgt) оставить лучшее
    best = {}
    for r in out:
        k = (r["feat"], r["tgt"], r["sign"])
        if k not in best or r["robust"] > best[k]["robust"]: best[k] = r
    return sorted(best.values(), key=lambda r: -r["robust"])


def main():
    rows = json.load(open(ROOT / "reports" / "deep_dive_rows.json", encoding="utf-8"))
    cities = sorted({r["slug"] for r in rows}, key=lambda c: -len([r for r in rows if r["slug"] == c]))
    out = ["# Глубокий разбор по городам — подробности (генерируется deep_dive_report.py)", "",
           "Данные: PM/Kalshi снапшоты (27.08–10.10), модели Open-Meteo/NWS, METAR IEM по 22 станциям. Строка = город-день-горизонт, "
           "лидер PM на момент входа стоит 25–69¢ (рынок ещё не определился). Исход — победитель относительно лидера. "
           "Правило показано, если n≥8 и обе половины дат ≥+5%/сд; ранжировано по худшей половине. "
           "Чем меньше n, тем больше это шум — ждать стоит половину заявленного.", ""]
    agg_rules = defaultdict(list)
    for c in cities:
        R = [r for r in rows if r["slug"] == c]
        out.append(f"## {c} — {len({r['date'] for r in R})} рынков с лидером <70¢ на каком-либо горизонте")
        out.append("")
        out.append("| H | n | лидер побеждает | ниже | выше | ср. цена лидера |")
        out.append("|---|---|---|---|---|---|")
        for H in (30, 27, 24, 21, 18, 15, 12):
            RH = [r for r in R if r["H"] == H]
            if len(RH) < 5: continue
            n = len(RH); o = [r["outcome"] for r in RH]
            out.append(f"| {H} | {n} | {o.count(0)/n*100:.0f}% | {o.count(-1)/n*100:.0f}% | {o.count(1)/n*100:.0f}% | {statistics.mean(r['lead_price'] for r in RH):.0f}¢ |")
        out.append("")
        # общие сдвиги признаков между исходами (на H=24)
        R24 = [r for r in R if r["H"] in (27, 24, 21)]
        hints = []
        for feat in ("cons_minus_lead", "nbm_minus_lead", "prevmax_minus_lead", "max_trend", "gap", "mom3", "vol_share", "models_below", "models_above", "kalshi_rel", "precip_prob"):
            by = defaultdict(list)
            for r in R24:
                if isinstance(r.get(feat), (int, float)): by[r["outcome"]].append(r[feat])
            if all(len(by[k]) >= 4 for k in (-1, 0, 1)):
                hints.append(f"{NAMES[feat]}: ниже {statistics.median(by[-1]):+.2f} / лидер {statistics.median(by[0]):+.2f} / выше {statistics.median(by[1]):+.2f}")
        if hints:
            out.append("Медианы признаков по исходу (H 21–27): " + "; ".join(hints)); out.append("")
        rules = []
        for H in (30, 27, 24, 21, 18, 15, 12):
            RH = [r for r in R if r["H"] == H]
            if len(RH) < 10: continue
            for rl in mine(RH)[:4]:
                rl["H"] = H; rules.append(rl)
        rules.sort(key=lambda r: -r["robust"])
        if rules:
            out.append("Устойчивые триггеры (вход по ask целевой корзины):"); out.append("")
            out.append("| H | триггер | цель | n | ✓ | %/сд | ≤21.09 | >21.09 |"); out.append("|---|---|---|---|---|---|---|---|")
            for r in rules[:6]:
                tg = {"lead": "лидер", "below": "корзина НИЖЕ", "above": "корзина ВЫШЕ"}[r["tgt"]]
                out.append(f"| {r['H']} | {NAMES[r['feat']]} {'≥' if r['sign']=='ge' else '≤'} {r['thr']:.2f} | {tg} | {r['n']} | {r['hit']} | {r['per']:+.0f}% | {r['ins'][0]:+.0f}% (n={r['ins'][1]}) | {r['oos'][0]:+.0f}% (n={r['oos'][1]}) |")
                agg_rules[(r["feat"], r["sign"], r["tgt"])].append(c)
        else:
            out.append("Устойчивых триггеров не найдено (ни одно правило не держит плюс в обеих половинах при n≥8).")
        out.append("")
    out.append("## Какие триггеры повторяются в нескольких городах (это ценнее одиночных)")
    out.append("")
    for (feat, sign, tgt), cs in sorted(agg_rules.items(), key=lambda kv: -len(kv[1])):
        if len(cs) >= 2:
            out.append(f"- {NAMES[feat]} {'≥' if sign=='ge' else '≤'} порог → {tgt}: {', '.join(cs)}")
    txt = "\n".join(out)
    p = ROOT / "reports" / "2026-10-11-deep-dive-cities.md"  # подробности по городам; сводный отчёт — 2026-10-11-deep-dive.md (вручную)
    p.write_text(txt, encoding="utf-8"); print(txt)


if __name__ == "__main__":
    main()
