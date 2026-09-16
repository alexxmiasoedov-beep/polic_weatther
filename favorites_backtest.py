"""Калибровка рынка и бэктест «купить лидера 50-70¢ за 20-48 ч до закрытия по ask».
Одна сделка на город-день, первый снапшот в окне. Победитель = корзина ≥97 в последнем снапшоте.
python3 favorites_backtest.py
"""
import json,glob,os,collections,datetime as dt,sys
series=collections.defaultdict(list)
for f in sorted(glob.glob('data/*.jsonl')):
    d=os.path.basename(f)[:-6]
    for l in open(f):
        try:r=json.loads(l)
        except:continue
        for slug,c in r.get('cities',{}).items():
            b=(c.get('polymarket') or {}).get('buckets')
            if b: series[(d,slug)].append((r['ts_utc'],b))
for f in sorted(glob.glob('pilot_data/*.jsonl')):
    d=os.path.basename(f)[:-6]
    for l in open(f):
        try:r=json.loads(l)
        except:continue
        b=(r.get('pm') or {}).get('buckets')
        if b: series[(r.get('market_date') or d,r['city'])].append((r['ts_utc'],b))
today=dt.date.today().isoformat()
win={}
for k,v in series.items():
    if k[0]>=today: continue
    v.sort(); ts,b=v[-1]
    w=[x['bucket'] for x in b if (x.get('last') or 0)>=97 or (x.get('bid') or 0)>=97]
    if len(w)==1: win[k]=w[0]
def hrs(a,b):
    fa=dt.datetime.fromisoformat(a.replace('Z','+00:00'));fb=dt.datetime.fromisoformat(b.replace('Z','+00:00'))
    return (fb-fa).total_seconds()/3600
lo_h,hi_h=(int(sys.argv[1]),int(sys.argv[2])) if len(sys.argv)>2 else (20,48)
plo,phi=(int(sys.argv[3]),int(sys.argv[4])) if len(sys.argv)>4 else (50,70)
byday=collections.defaultdict(lambda:[0,0,0.0]); bycity=collections.defaultdict(lambda:[0,0,0.0])
for k,w in win.items():
    v=sorted(series[k]); end=v[-1][0]
    cand=[(ts,b) for ts,b in v if lo_h<=hrs(ts,end)<hi_h]
    if not cand: continue
    ts,b=cand[0]
    lead=max(b,key=lambda x:x.get('last') or 0)
    p=lead.get('last'); a=lead.get('ask') or p
    if p and plo<=p<phi and a<=phi+2:
        hit=lead['bucket']==w; pnl=(100/a-1) if hit else -1
        for s in (byday[k[0]],bycity[k[1]]): s[0]+=1; s[1]+=hit; s[2]+=pnl
T=[0,0,0.0]
for d in sorted(byday):
    n,h,p=byday[d]; T[0]+=n;T[1]+=h;T[2]+=p
    print(f'{d}  n={n:2d} hit={h:2d}  pnl@ask={p:+6.2f}')
print(f'TOTAL n={T[0]} hit={T[1]} ({T[1]/max(T[0],1)*100:.0f}%) pnl@ask={T[2]:+.2f} ({T[2]/max(T[0],1)*100:+.1f}%/trade)')
for c,v in sorted(bycity.items(),key=lambda kv:-kv[1][2]): print(f'  {c:15s} {v[0]:2d} {v[1]:2d} {v[2]:+.2f}')
