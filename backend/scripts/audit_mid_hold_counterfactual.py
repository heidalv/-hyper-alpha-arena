"""中线持仓时长反事实：持到 T 小时（期间先触 SL/TP 则提前平），对比实际 PnL。
只做验证，不改任何线上配置。
"""
import psycopg, json, os, time, urllib.request, collections, statistics as st

CACHE = "data/_hold_cf_cache.json"
FEE_RT = 0.0010   # 往返手续费(含滑点) 10bp，仅用于净额参考

def fetch_bars(symbol, start_ms, hours=48):
    url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}USDT"
           f"&interval=1h&startTime={start_ms}&limit={hours}")
    req = urllib.request.Request(url, headers={"User-Agent": "alpha-audit/1.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())

cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}

c = psycopg.connect('postgresql://laobao:alpha_pass@localhost:5432/alpha_arena', autocommit=False)
cur = c.cursor()
cur.execute("set local app.tenant_id='326'")
cur.execute("set local app.is_admin='on'")
cur.execute("""
select id, symbol, side, opened_at, closed_at, close_reason,
       extract(epoch from (closed_at-opened_at))/3600.0 as hold_h,
       coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0) as pnl,
       size, entry_price, close_price, sl_price, tp_price, margin, leverage, peak_pnl_pct
from paper_positions
where timeframe_tier='mid' and closed_at is not null and opened_at is not null
order by opened_at
""")
rows = cur.fetchall()
c.rollback()
print("mid closed:", len(rows))

TS = [6, 9, 12, 18, 24, 30, 36]
res = {t: {"gross": 0.0, "n": 0, "win": 0, "sl": 0, "tp": 0, "to": 0} for t in TS}
actual = {"gross": 0.0, "n": 0, "win": 0}
skipped = collections.Counter()
detail = []

for r in rows:
    (pid, sym, side, op, cl, reason, hold_h, pnl, size, entry, cpx, sl, tp, margin, lev, peak) = r
    if not size or not entry or not op:
        skipped["no_size_entry"] += 1; continue
    entry = float(entry); size = float(size); sl = float(sl) if sl else None; tp = float(tp) if tp else None
    dirn = 1.0 if str(side).lower() in ("long", "buy") else -1.0
    key = f"{sym}|{int(op.timestamp())}"
    if key not in cache:
        try:
            cache[key] = fetch_bars(sym, int(op.timestamp()) * 1000, 48)
        except Exception as e:
            cache[key] = {"__err__": str(e)[:60]}
        time.sleep(0.06)
    bars = cache[key]
    if isinstance(bars, dict) or not bars:
        skipped[f"fetch_fail:{sym}"] += 1; continue
    if len(bars) < 7:
        skipped["too_few_bars"] += 1; continue
    actual["gross"] += float(pnl or 0); actual["n"] += 1
    if float(pnl or 0) > 0: actual["win"] += 1
    for T in TS:
        exit_px = None; how = "to"
        for b in bars:
            ts = b[0] / 1000.0
            if ts > op.timestamp() + T * 3600: break
            hi, lo, clp = float(b[2]), float(b[3]), float(b[4])
            if dirn > 0:
                if sl is not None and lo <= sl: exit_px, how = sl, "sl"; break
                if tp is not None and hi >= tp: exit_px, how = tp, "tp"; break
            else:
                if sl is not None and hi >= sl: exit_px, how = sl, "sl"; break
                if tp is not None and lo <= tp: exit_px, how = tp, "tp"; break
            exit_px = clp
        if exit_px is None: continue
        g = size * (exit_px - entry) * dirn
        res[T]["gross"] += g; res[T]["n"] += 1
        if g > 0: res[T]["win"] += 1
        res[T][how] += 1
        if T == 24:
            detail.append({"sym": sym, "side": str(side), "hold_actual": round(hold_h, 2),
                           "pnl_actual": round(float(pnl or 0), 2), "pnl_24h": round(g, 2),
                           "how": how, "reason": str(reason)[:28]})

json.dump(cache, open(CACHE, "w", encoding="utf-8"))
json.dump(detail, open("data/_hold_cf_detail.json", "w", encoding="utf-8"),
          ensure_ascii=False, default=str)

print(f"skipped: {dict(skipped)}")
print(f"\n{'variant':10s} | {'n':4s} | {'gross_sum':>10s} | {'mean':>8s} | {'win%':>5s} | SL/TP/timeout")
print(f"{'ACTUAL':10s} | {actual['n']:4d} | {actual['gross']:10.2f} | {actual['gross']/max(1,actual['n']):8.2f} | "
      f"{100*actual['win']/max(1,actual['n']):5.1f} | -")
for T in TS:
    d = res[T]
    if not d["n"]: continue
    print(f"hold{T:>3d}h    | {d['n']:4d} | {d['gross']:10.2f} | {d['gross']/d['n']:8.2f} | "
          f"{100*d['win']/d['n']:5.1f} | {d['sl']}/{d['tp']}/{d['to']}")
