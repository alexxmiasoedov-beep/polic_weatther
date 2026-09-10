"""Вечерний портфель «входы на завтра» (ревизия 10.09 №2).

Бэктест 02-08.09 (walk-forward): медиана всех калиброванных моделей
вечером накануне попадает ~60-65% при цене корзины ~40¢ — заметно
дешевле, чем к дневным срезам. Скрипт берёт последний снапшот
завтрашнего рынка (бухгалтерия 19:20 снимает его с wx), считает
калиброванную медиану всех моделей (NBM — арбитр при споре корзин),
и печатает вердикт по каждому городу семёрки.

  python3 evening.py            — завтрашний рынок, таблица
  python3 evening.py --write    — + записать evening_entries в forecasts
  python3 evening.py --resolve YYYY-MM-DD — проставить winner/pnl по picks

Вход: цена корзины 15-60¢ по last, лимит = min(mid, bid+2).
"""
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

ROOT = Path(__file__).parent
MODELS = ["om_ncep_nbm_conus", "nws", "om_best_match", "om_gfs_seamless",
          "om_icon_seamless", "om_ukmo_seamless", "om_ecmwf_ifs025",
          "om_ecmwf_aifs025_single", "om_gfs_hrrr"]
SKIP = {"seattle"}  # выведен из прогнозов 10.09
MIN_PRICE, MAX_PRICE = 15, 60
PROFILES = json.loads((ROOT / "city_profiles.json").read_text(encoding="utf-8"))["cities"]


def bucket_of(buckets, t):
    for b in buckets:
        r = parse_range(b["bucket"])
        if r and r[0] <= round(t) <= r[1]:
            return b["bucket"]
    return None


def parse_range(name):
    """Корзина → (lo, hi): «88-89°F», «33°C», «79°F or below», «94°F or higher»."""
    m = re.match(r"(\d+)-(\d+)", name)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d+)°[FC] or below", name)
    if m:
        return -999, int(m.group(1))
    m = re.match(r"(\d+)°[FC] or higher", name)
    if m:
        return int(m.group(1)), 999
    m = re.match(r"(\d+)°C$", name)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None


def neighbor(buckets, bk, t):
    """Соседняя корзина со стороны, куда тянет медиана (пара корзин).

    Бэктест 10.09: пара при |медиана − центр корзины| ≥ 0.6 бьёт 9/10
    вечером и 6/7 утром, но ROI +24..38% против +100% у одиночной —
    это режим меньших просадок, не большей прибыли.
    """
    r = parse_range(bk)
    if not r or r[0] < -900 or r[1] > 900:
        return None, False
    center = (r[0] + r[1]) / 2
    near_edge = abs(t - center) >= 0.6
    want = r[1] + 1 if t >= center else r[0] - 1
    for b in buckets:
        rr = parse_range(b["bucket"])
        if rr and (rr[0] == want or rr[1] == want):
            return b["bucket"], near_edge
    return None, near_edge


def latest_snapshot(d):
    f = ROOT / "data" / f"{d.isoformat()}.jsonl"
    if not f.exists():
        return None
    snaps = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
    snaps = [s for s in snaps if s.get("market_date") == d.isoformat()]
    return snaps[-1] if snaps else None


def wx_for(d, slug, snap):
    """wx из этого снапшота или последнего до него."""
    c = snap["cities"].get(slug, {})
    if c.get("wx"):
        return c["wx"]
    f = ROOT / "data" / f"{d.isoformat()}.jsonl"
    for line in reversed(f.read_text(encoding="utf-8").splitlines()):
        s = json.loads(line)
        if s.get("market_date") == d.isoformat() and s["cities"].get(slug, {}).get("wx"):
            return s["cities"][slug]["wx"]
    return None


def pilot_cities(d):
    """{slug: {code, polymarket{buckets}, wx}} из pilot_data — последняя запись на дату."""
    import pilot as _p
    f = ROOT / "pilot_data" / f"{d.isoformat()}.jsonl"
    if not f.exists():
        return {}
    out, last_wx = {}, {}
    for line in f.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        slug = r["city"]
        if r.get("wx"):
            last_wx[slug] = r["wx"]
        out[slug] = {"code": _p.PILOT_CITIES.get(slug, {}).get("code", slug), "polymarket": r["pm"],
                     "wx": last_wx.get(slug), "ts": r["ts_utc"]}
    return out


def evaluate(d):
    snap = latest_snapshot(d)
    cities = {}
    if snap:
        cities.update({k: dict(v, ts=snap["ts_utc"]) for k, v in snap["cities"].items()})
    cities.update(pilot_cities(d))
    if not cities:
        print(f"нет снапшота рынка {d}")
        return {}
    bias = json.loads((ROOT / "calibration" / "bias.json").read_text(encoding="utf-8"))["cities"]
    out = {}
    print(f"Рынок {d}")
    for slug, c in cities.items():
        if slug in SKIP:
            continue
        wx = c.get("wx") if slug not in (snap or {}).get("cities", {}) else wx_for(d, slug, snap)
        if not wx:
            print(f"  {c['code']}: нет wx"); continue
        cal = {}
        for m in MODELS:
            if wx.get(m) is None:
                continue
            b = bias.get(slug, {}).get(m, {}).get("bias")
            if b is None:
                continue  # без поправки модель не используем (сырой NBM 10/48)
            cal[m] = round(wx[m] - b, 1)
        if len(cal) < 3:
            print(f"  {c['code']}: мало калиброванных моделей ({len(cal)})"); continue
        med = statistics.median(cal.values())
        bs = c["polymarket"]["buckets"]
        bk = bucket_of(bs, med)
        nbm = cal.get("om_ncep_nbm_conus")
        nbm_bk = bucket_of(bs, nbm) if nbm is not None else None
        b = next((x for x in bs if x["bucket"] == bk), None) or {}
        last, bid, ask = b.get("last"), b.get("bid"), b.get("ask")
        mid = (bid + ask) / 2 if bid is not None and ask is not None else last
        limit = None
        if bid is not None and mid is not None:
            limit = round(min(mid, bid + 2))
        lead = max(bs, key=lambda x: x.get("last") or 0)
        prof = PROFILES.get(slug, {}); strat = prof.get("strategy", "evening")
        max_price = prof.get("max_price", MAX_PRICE)
        if strat == "evening_leader":
            # рынок ленивее нашего консенсуса — берём лидера PM (KUL, SIN)
            if bk != lead["bucket"] and abs(med - (parse_range(lead["bucket"]) or (med, med))[0]) >= 2:
                bk, b = None, {}
            else:
                bk = lead["bucket"]; b = lead
                last, bid, ask = b.get("last"), b.get("bid"), b.get("ask")
                mid = (bid + ask) / 2 if bid is not None and ask is not None else last
                limit = round(min(mid, bid + 2)) if bid is not None and mid is not None else None
        verdict = "enter" if bk and last is not None and MIN_PRICE <= last < max_price else "skip"
        reason = ("" if verdict == "enter" else
                  "консенсус спорит с лидером на 2+" if bk is None else
                  "дорого" if (last or 0) >= max_price else "неликвид/дёшево")
        if strat in ("late", "skip", "observe"):
            verdict, reason = "skip", {"late": "профиль: поздний рынок (факт)", "skip": "профиль: вне торговли",
                                       "observe": "профиль: наблюдение"}[strat]
        elif strat == "deviation_only" and bk == lead["bucket"]:
            verdict, reason = "skip", "профиль: только отклонение от лидера"
        elif strat == "morning" and verdict == "enter":
            reason = "профиль: лучше утром (17:05), вечером справочно"
        size = prof.get("size", 1.0)
        precip = wx.get("precip_prob")
        nb, near_edge = neighbor(bs, bk, med)
        nbp = next((x for x in bs if x["bucket"] == nb), None) or {}
        pair = None
        if nb and near_edge and last is not None and nbp.get("last") is not None:
            pair_cost = round(last + nbp["last"])
            if pair_cost < 85:
                pair = {"buckets": [bk, nb], "cost": pair_cost, "neighbor_last": nbp["last"]}
        print(f"  {c['code']}: медиана {med:.1f} → {bk} @{last} (bid {bid}/ask {ask}); "
              f"NBM {nbm} → {nbm_bk}; лидер PM {lead['bucket']} {lead.get('last')}¢; "
              f"осадки {precip}% → {'ВХОД лимит ' + str(limit) + (' size ' + str(size) if size != 1.0 else '') + (' (' + reason + ')' if reason else '') if verdict == 'enter' else 'ПРОПУСК ' + reason}"
              + (f"; ПАРА {bk}+{nb} за {pair['cost']}¢ (медиана у границы)" if pair else ""))
        out[slug] = {"bucket": bk, "consensus": round(med, 1), "nbm_bucket": nbm_bk,
                     "models": cal, "last": last, "bid": bid, "ask": ask,
                     "entry_price": limit if verdict == "enter" else None,
                     "pm_leader": lead["bucket"], "pm_leader_price": lead.get("last"),
                     "precip_prob": precip, "verdict": verdict, "reason": reason,
                     "pair": pair, "strategy": strat, "size": size,
                     "cut": "19:20 Минск накануне", "ts_utc": c.get("ts"), "winner": None}
    return out


def write(d, entries):
    f = ROOT / "forecasts" / f"{d.isoformat()}.json"
    data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"date": d.isoformat(), "picks": {}}
    data.setdefault("evening_entries", {}).update(entries)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"записано {len(entries)} в {f.name} (evening_entries)")


def resolve(d):
    f = ROOT / "forecasts" / f"{d.isoformat()}.json"
    data = json.loads(f.read_text(encoding="utf-8"))
    ev = data.get("evening_entries", {})
    n = hits = 0
    pnl = 0.0
    for slug, e in ev.items():
        w = data.get("picks", {}).get(slug, {}).get("winner")
        if not w:
            continue
        e["winner"] = w
        e["hit"] = e["bucket"] == w
        if e.get("verdict") == "enter" and e.get("entry_price"):
            e["pnl_entry"] = round(100 / e["entry_price"] - 1, 3) if e["hit"] else -1.0
            n += 1; hits += e["hit"]; pnl += e["pnl_entry"]
        pr = e.get("pair")
        if pr:
            e["pair_hit"] = w in pr["buckets"]
            e["pnl_pair"] = round(100 / pr["cost"] - 1, 3) if e["pair_hit"] else -1.0
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{d}: вечерние входы {hits}/{n}, P&L {pnl:+.2f}; попадания медианы всего "
          f"{sum(1 for e in ev.values() if e.get('hit'))}/{sum(1 for e in ev.values() if e.get('winner'))}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--resolve":
        resolve(datetime.fromisoformat(args[1]).date())
    else:
        d = (datetime.now(timezone.utc) + timedelta(days=1)).date()
        if args and re.match(r"\d{4}-\d{2}-\d{2}", args[-1]):
            d = datetime.fromisoformat(args[-1]).date()
        entries = evaluate(d)
        if "--write" in args and entries:
            write(d, entries)
