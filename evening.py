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


def bucket_of(buckets, t):
    for b in buckets:
        name = b["bucket"]
        m = re.match(r"(\d+)-(\d+)", name)
        if m and int(m.group(1)) <= round(t) <= int(m.group(2)):
            return name
        m = re.match(r"(\d+)°F or below", name)
        if m and round(t) <= int(m.group(1)):
            return name
        m = re.match(r"(\d+)°F or higher", name)
        if m and round(t) >= int(m.group(1)):
            return name
    return None


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


def evaluate(d):
    snap = latest_snapshot(d)
    if not snap:
        print(f"нет снапшота рынка {d}")
        return {}
    bias = json.loads((ROOT / "calibration" / "bias.json").read_text(encoding="utf-8"))["cities"]
    out = {}
    print(f"Рынок {d}, снапшот {snap['ts_utc']}")
    for slug, c in snap["cities"].items():
        if slug in SKIP:
            continue
        wx = wx_for(d, slug, snap)
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
        verdict = "enter" if last is not None and MIN_PRICE <= last < MAX_PRICE else "skip"
        reason = ("" if verdict == "enter" else
                  "дорого" if (last or 0) >= MAX_PRICE else "неликвид/дёшево")
        precip = wx.get("precip_prob")
        print(f"  {c['code']}: медиана {med:.1f} → {bk} @{last} (bid {bid}/ask {ask}); "
              f"NBM {nbm} → {nbm_bk}; лидер PM {lead['bucket']} {lead.get('last')}¢; "
              f"осадки {precip}% → {'ВХОД лимит ' + str(limit) if verdict == 'enter' else 'ПРОПУСК ' + reason}")
        out[slug] = {"bucket": bk, "consensus": round(med, 1), "nbm_bucket": nbm_bk,
                     "models": cal, "last": last, "bid": bid, "ask": ask,
                     "entry_price": limit if verdict == "enter" else None,
                     "pm_leader": lead["bucket"], "pm_leader_price": lead.get("last"),
                     "precip_prob": precip, "verdict": verdict, "reason": reason,
                     "cut": "19:20 Минск накануне", "ts_utc": snap["ts_utc"], "winner": None}
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
