"""轮43：①固定6小时 ②延迟平仓 — 全面验证。
只读 + 只落缓存，不改线上配置。
"""
import psycopg, json, os, time, urllib.request, collections, statistics as st, random

C15 = "data/_hold_cf_15m_cache.json"
CH = "data/_hold_cf_cache.json"
HARD = {"sl", "tp", "liquidation", "max_hold_timeout"}

def fetch15(sym, start_ms, limit=1000):
    url = (f"https://api.binance.com/api/v3/klines?symbol={sym}USDT"
           f"&interval=15m&startTime={start_ms}&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "alpha-audit/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

c15 = json.load(open(C15, encoding="utf-8")) if os.path.exists(C15) else {}
c1h = json.load(open(CH, encoding="utf-8"))

con = psycopg.connect('postgresql://laobao:alpha_pass@localhost:5432/alpha_arena', autocommit=False)
cur = con.cursor()
cur.execute("set local app.tenant_id='326'")
cur.execute("set local app.is_admin='on'")
cur.execute("""select id, symbol, side, opened_at, closed_at, close_reason,
       extract(epoch from (closed_at-opened_at))/3600.0 hold_h,
       coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) pnl,
       size, entry_price, sl_price, tp_price, strategy_id
from paper_positions
where timeframe_tier='mid' and closed_at is not null and opened_at is not null
order by opened_at""")
rows = cur.fetchall(); con.rollback()

def walk(bars, t0, t1, dirn, entry, sl, tp, size, px_idx=(2,3,4)):
    """在 (t0,t1] 窗口内走 bar；先触 SL/TP 按触价平，否则用最后一根收盘价。"""
    ex = None; how = "to"; last = None
    for b in bars:
        ts = b[0]/1000.0
        if ts <= t0: continue
        if ts > t1: break
        hi, lo, cl = float(b[px_idx[0]]), float(b[px_idx[1]]), float(b[px_idx[2]])
        last = cl
        if dirn > 0:
            if sl is not None and lo <= sl: return size*(sl-entry)*dirn, "sl"
            if tp is not None and hi >= tp: return size*(tp-entry)*dirn, "tp"
        else:
            if sl is not None and hi >= sl: return size*(sl-entry)*dirn, "sl"
            if tp is not None and lo <= tp: return size*(tp-entry)*dirn, "tp"
    if last is None: return None, "nodata"
    return size*(last-entry)*dirn, how

recs = []
sk15 = collections.Counter()
for r in rows:
    (pid, sym, side, op, cl, reason, hold_h, pnl, size, entry, sl, tp, strat) = r
    if not size or not entry or not op or not cl: continue
    key = f"{sym}|{int(op.timestamp())}"
    if key not in c15:
        try:
            c15[key] = fetch15(sym, int(op.timestamp())*1000, 1000)
        except Exception as e:
            c15[key] = {"__err__": str(e)[:50]}
        time.sleep(0.06)
    b15 = c15[key]
    ch = str(reason).split(":")[0].strip()
    recs.append(dict(key=key, sym=sym, side=str(side), op=op.timestamp(), cl=cl.timestamp(),
                     hold=float(hold_h or 0), pnl=float(pnl or 0), size=float(size),
                     entry=float(entry), sl=float(sl) if sl else None,
                     tp=float(tp) if tp else None, ch=ch, strat=str(strat or "?"),
                     b1h=c1h.get(key), b15=None if isinstance(b15, dict) else b15,
                     ok15=not isinstance(b15, dict)))
json.dump(c15, open(C15, "w", encoding="utf-8"))
print("mid recs:", len(recs), "| 15m cache ok:", sum(1 for x in recs if x["ok15"]),
      "| 1h cache ok:", sum(1 for x in recs if x["b1h"]))
json.dump([{k: v for k, v in x.items() if not k.startswith("b")} for x in recs],
          open("data/_hold_recs.json", "w", encoding="utf-8"), default=str)
