"""轮43-F：决定性分期 —— 在"当前规则体系"（09-16 起，1.5%止损帽+容量放宽已生效）下 cap 是否仍有效"""
import json, collections, random, datetime as dt, statistics as st
C1H = json.load(open("data/_hold_cf_cache.json", encoding="utf-8"))
import psycopg
con = psycopg.connect('postgresql://laobao:alpha_pass@localhost:5432/alpha_arena', autocommit=False)
cur = con.cursor(); cur.execute("set local app.tenant_id='326'"); cur.execute("set local app.is_admin='on'")
cur.execute("""select symbol, side, opened_at, closed_at, close_reason,
   extract(epoch from (closed_at-opened_at))/3600.0,
   coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0), size, entry_price, sl_price, tp_price
from paper_positions where timeframe_tier='mid' and closed_at is not null and opened_at is not null""")
recs = []
for (sym, side, op, cl, rsn, hold, pnl, size, entry, sl, tp) in cur.fetchall():
    if not size or not entry or not op or not cl: continue
    k = f"{sym}|{int(op.timestamp())}"
    b = C1H.get(k)
    if not isinstance(b, list): continue
    recs.append(dict(sym=sym, side=str(side).lower(), op=op.timestamp(), cl=cl.timestamp(),
                     hold=float(hold or 0), pnl=float(pnl or 0), size=float(size), entry=float(entry),
                     sl=float(sl) if sl else None, tp=float(tp) if tp else None,
                     ch=str(rsn).split(":")[0].strip(), b1=b))
con.rollback()

def walk(bars, t0, t1, d, e, sl, tp, s):
    last = None
    for b in bars:
        ts = b[0]/1000.0
        if ts <= t0: continue
        if ts > t1: break
        hi, lo, cl = float(b[2]), float(b[3]), float(b[4]); last = cl
        if d > 0:
            if sl is not None and lo <= sl: return s*(sl-e)*d
            if tp is not None and hi >= tp: return s*(tp-e)*d
        else:
            if sl is not None and hi >= sl: return s*(sl-e)*d
            if lo <= tp if tp else False: return s*(tp-e)*d
    return s*(last-e)*d if last is not None else None

def boot(xs, n=5000, seed=31):
    if len(xs) < 6: return 0.0, 0.0
    r = random.Random(seed); m = len(xs); ms = []
    for _ in range(n): ms.append(sum(xs[r.randrange(m)] for _ in range(m))/m)
    ms.sort(); return ms[int(.025*n)], ms[int(.975*n)]

def cap(r, T):
    d = 1.0 if r["side"] in ("long", "buy") else -1.0
    if r["hold"] <= T: return 0.0
    g = walk(r["b1"], r["op"], r["op"]+T*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
    return (g - r["pnl"]) if g is not None else 0.0

def per(r):
    m = dt.datetime.fromtimestamp(r["op"], dt.timezone.utc)
    return m.strftime("%m-%d")

segs = [("08-16~08-31", dt.datetime(2026,8,16).timestamp(), dt.datetime(2026,9,1).timestamp()),
        ("09-01~09-13", dt.datetime(2026,9,1).timestamp(), dt.datetime(2026,9,14).timestamp()),
        ("09-14~09-15", dt.datetime(2026,9,14).timestamp(), dt.datetime(2026,9,16).timestamp()),
        ("09-16~now  ", dt.datetime(2026,9,16).timestamp(), 9e18)]
print(f"{'区间':14s} {'n':>4} {'实际':>9} | " + " | ".join(f"cap{T}h".rjust(9) for T in (3,4,5,6,8)))
for name, a, b in segs:
    sub = [r for r in recs if a <= r["op"] < b]
    if not sub: continue
    row = " | ".join(f"{sum(cap(r,T) for r in sub):9.2f}" for T in (3,4,5,6,8))
    print(f"{name:14s} {len(sub):4d} {sum(r['pnl'] for r in sub):9.2f} | {row}")

print("\n[当前体系 09-16 起] cap 各档显著性")
cur_seg = [r for r in recs if r["op"] >= dt.datetime(2026,9,16).timestamp()]
print(f"n={len(cur_seg)} 实际={sum(r['pnl'] for r in cur_seg):.2f}")
for T in (3,4,5,6,8,12):
    xs = [cap(r,T) for r in cur_seg]
    aff = [x for x in xs if x != 0]
    lo, hi = boot(xs)
    sig = "显著>0" if lo>0 else ("显著<0" if hi<0 else "不显著")
    print(f"  cap{T:2d}h: delta={sum(xs):7.2f} 受影响={len(aff):2d} "
          f"改善{sum(1 for x in aff if x>0)}/恶化{sum(1 for x in aff if x<0)} "
          f"CI95=[{lo:6.2f},{hi:6.2f}] {sig}")

print("\n[当前体系 09-16 起] 被 cap6 截断的每笔明细")
for r in cur_seg:
    x = cap(r, 6)
    if x != 0:
        print(f"  {per(r)} {str(r['sym'])[:8]:8s} {r['side']:5s} hold={r['hold']:5.1f}h "
              f"实际={r['pnl']:8.2f} cap6={r['pnl']+x:8.2f} delta={x:7.2f} ch={r['ch'][:20]}")
