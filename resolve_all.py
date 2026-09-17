"""Резолв всех незакрытых picks/entries/monitor_bets/evening_entries по снапшотам ≥97¢."""
import json,glob,os,datetime as dt
def snaps(d):
    out={}
    for f,key in ((f'data/{d}.jsonl','cities'),(f'pilot_data/{d}.jsonl','pm')):
        if not os.path.exists(f): continue
        for l in open(f):
            try:r=json.loads(l)
            except: continue
            if r.get('market_date',d)!=d: continue
            if key=='cities':
                for s,c in r.get('cities',{}).items():
                    b=(c.get('polymarket') or {}).get('buckets')
                    if b: out.setdefault(s,[]).append((r['ts_utc'],b))
            else:
                b=(r.get('pm') or {}).get('buckets')
                if b: out.setdefault(r['city'],[]).append((r['ts_utc'],b))
    return out
def winner(series):
    ts,b=sorted(series)[-1]
    w=[x['bucket'] for x in b if (x.get('last') or 0)>=97 or (x.get('bid') or 0)>=97]
    return w[0] if len(w)==1 else None
def cut_utc(d,cut):
    try:
        h,m=map(int,cut.split()[0].split(':')); return (dt.datetime.fromisoformat(d)+dt.timedelta(hours=h-3,minutes=m)).strftime('%Y-%m-%dT%H:%M:%SZ')
    except: return None
def fill(series,bucket,after,limit):
    for ts,b in sorted(series):
        if after and ts<=after: continue
        for x in b:
            if x['bucket']==bucket and ((x.get('ask') or 999)<=limit or (x.get('last') or 999)<=limit): return True
    return False
for f in sorted(glob.glob('forecasts/*.json')):
    d=os.path.basename(f)[:-5]; data=json.load(open(f)); ch=False; S=snaps(d)
    for slug,p in data.get('picks',{}).items():
        if p.get('winner') or slug not in S: continue
        w=winner(S[slug])
        if not w: continue
        p['winner']=w; p['hit']=(p['bucket']==w); p['pnl_pick']=round(100/p['last']-1,3) if p['hit'] else -1.0; ch=True
        bt=p.get('bet')
        if bt:
            sz=bt.get('size') or 1.0; bt['hit']=(bt['bucket']==w); bt['pnl']=round((100/bt['limit']-1)*sz,3) if bt['hit'] else -sz
            bt['fill_confirmed']=fill(S[slug],bt['bucket'],cut_utc(d,p.get('cut','')),bt['limit']); p['pnl_bet']=bt['pnl']
        print(d,slug,p['bucket'],p['last'],'→',w,'✅' if p['hit'] else '✗',('bet',bt['bucket'],bt['limit'],'✅' if bt['hit'] else '✗','fill',bt['fill_confirmed']) if bt else '')
    for key in ('entries','monitor_bets'):
        for slug,e in data.get(key,{}).items():
            if e.get('winner') or slug not in S: continue
            w=winner(S[slug])
            if not w: continue
            e['winner']=w; e['hit']=(e['bucket']==w); sz=e.get('size') or 1.0; price=e.get('entry_price') or e.get('limit')
            if (key=='monitor_bets' or e.get('verdict')=='enter') and price:
                e['pnl']=round((100/price-1)*sz,3) if e['hit'] else -sz
                if key=='monitor_bets': e['fill_confirmed']=fill(S[slug],e['bucket'],e.get('ts_utc'),price)
            ch=True; print(d,key,slug,e['bucket'],price,'→',w,'✅' if e['hit'] else '✗')
    for slug,e in data.get('evening_entries',{}).items():
        if not e.get('winner') and slug in S:
            w=winner(S[slug])
            if w: e['winner']=w; ch=True
    if ch: json.dump(data,open(f,'w'),ensure_ascii=False,indent=1)
