"""轮43 分析：①固定6小时(固定目标 / 上限截断) ②延迟平仓。读取缓存，只读不改线上。"""
import json, os, collections, statistics as st, random

C15 = json.load(open("data/_hold_cf_15m_cache.json", encoding="utf-8"))
C1H = json.load(open("data/_hold_cf_cache.json", encoding="utf-8"))
HARD = {"sl", "tp", "liquidation", "max_hold_timeout"}
GRID = [3, 4, 5, 6, 7, 8, 9, 10, 12, 18, 24, 36]
DELAYS = [0.25, 0.5, 1, 2, 3, 4, 6]

def load(tier):
    import psycopg
    con = psycopg.connect('postgresql://laobao:alpha_pass@localhost:5432/alpha_arena', autocommit=False)
    cur = con.cursor()
    cur.execute("set local app.tenant_id='326'")
    cur.execute("set local app.is_admin='on'")
    cur.execute("""select symbol, side, opened_at, closed_at, close_reason,
           extract(epoch from (closed_at-opened_at))/3600.0,
           coalesce(partial_realized_pnl,0)+coalesce(unrealized_pnl,0),
           size, entry_price, sl_price, tp_price
        from paper_positions
        where timeframe_tier=%s and closed_at is not null and opened_at is not null
        order by opened_at""", (tier,))
    out = []
    for (sym, side, op, cl, reason, hold, pnl, size, entry, sl, tp) in cur.fetchall():
        if not size or not entry or not op or not cl: continue
        key = f"{sym}|{int(op.timestamp())}"
        b1 = C1H.get(key); b15 = C15.get(key)
        out.append(dict(sym=sym, side=str(side).lower(), op=op.timestamp(), cl=cl.timestamp(),
                        hold=float(hold or 0), pnl=float(pnl or 0), size=float(size),
                        entry=float(entry), sl=float(sl) if sl else None, tp=float(tp) if tp else None,
                        ch=str(reason).split(":")[0].strip(), b1=b1 if isinstance(b1, list) else None,
                        b15=b15 if isinstance(b15, list) else None))
    con.rollback()
    return out

def walk(bars, t0, t1, d, entry, sl, tp, size):
    last = None
    for b in bars:
        ts = b[0]/1000.0
        if ts <= t0: continue
        if ts > t1: break
        hi, lo, cl = float(b[2]), float(b[3]), float(b[4]); last = cl
        if d > 0:
            if sl is not None and lo <= sl: return size*(sl-entry)*d, "sl"
            if tp is not None and hi >= tp: return size*(tp-entry)*d, "tp"
        else:
            if sl is not None and hi >= sl: return size*(sl-entry)*d, "sl"
            if tp is not None and lo <= tp: return size*(tp-entry)*d, "tp"
    return (size*(last-entry)*d, "to") if last is not None else (None, "nodata")

def boot(deltas, n=4000, seed=7):
    if len(deltas) < 8: return (0.0, 0.0)
    rnd = random.Random(seed); m = len(deltas); means = []
    for _ in range(n):
        means.append(sum(deltas[rnd.randrange(m)] for _ in range(m))/m)
    means.sort()
    return means[int(0.025*n)], means[int(0.975*n)]

print("=" * 78)
print("第 1 部分：固定 6 小时（mid）")
print("=" * 78)
mid = load("mid")
midok = [r for r in mid if r["b1"]]
act = sum(r["pnl"] for r in midok)
print(f"样本 n={len(midok)}（有1hK线）  实际合计={act:.2f}  均值={act/len(midok):.2f}")
print(f"\n{'T(h)':>5} | {'固定目标合计':>12} {'均值':>7} | {'上限截断合计':>13} {'均值':>7} | {'截断影响笔数':>10} | {'截断delta':>9}")
res = {}
for T in GRID:
    f_sum = 0.0; c_sum = 0.0; cut = 0; cut_delta = 0.0
    for r in midok:
        d = 1.0 if r["side"] in ("long", "buy") else -1.0
        g, _ = walk(r["b1"], r["op"], r["op"] + T*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
        if g is not None: f_sum += g
        if r["hold"] <= T:
            c_sum += r["pnl"]
        else:
            gg, _ = walk(r["b1"], r["op"], r["op"] + T*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
            if gg is not None:
                c_sum += gg; cut += 1; cut_delta += gg - r["pnl"]
    n = len(midok)
    res[T] = (f_sum, c_sum, cut, cut_delta)
    print(f"{T:5d} | {f_sum:12.2f} {f_sum/n:7.2f} | {c_sum:13.2f} {c_sum/n:7.2f} | {cut:10d} | {cut_delta:9.2f}")

# 显著性：cap6 vs 实际（配对）
T = 6
deltas = []; 
for r in midok:
    d = 1.0 if r["side"] in ("long", "buy") else -1.0
    if r["hold"] <= T:
        deltas.append(0.0)
    else:
        gg, _ = walk(r["b1"], r["op"], r["op"] + T*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
        deltas.append((gg - r["pnl"]) if gg is not None else 0.0)
nz = [x for x in deltas if x != 0]
lo, hi = boot(deltas)
print(f"\n[cap6 显著性] 受影响 {len(nz)} 笔 / 全样本 {len(deltas)}")
print(f"  delta 合计={sum(deltas):.2f} 均值={sum(deltas)/len(deltas):.2f} "
      f"改善 {sum(1 for x in nz if x>0)} 恶化 {sum(1 for x in nz if x<0)}")
print(f"  bootstrap 95%CI(均值) = [{lo:.3f}, {hi:.3f}]  {'显著>0' if lo>0 else ('显著<0' if hi<0 else '不显著(跨0)')}")

# 分期稳健性
print("\n[cap6 分期稳健性] 按半月")
byw = collections.defaultdict(lambda: [0.0, 0.0, 0])
import datetime as dt
for r in midok:
    k = dt.datetime.utcfromtimestamp(r["op"]).strftime("%m-%d")[:5]
    byw[k][0] += r["pnl"]
    d = 1.0 if r["side"] in ("long", "buy") else -1.0
    if r["hold"] <= 6: byw[k][1] += r["pnl"]
    else:
        gg, _ = walk(r["b1"], r["op"], r["op"] + 6*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
        byw[k][1] += gg if gg is not None else r["pnl"]
    byw[k][2] += 1
half = collections.defaultdict(lambda: [0.0, 0.0, 0])
for r in midok:
    m = dt.datetime.utcfromtimestamp(r["op"])
    k = f"{m.year}-{'H1' if m.day <= 15 else 'H2'}{m.month:02d}"
    half[k][0] += r["pnl"]
    d = 1.0 if r["side"] in ("long", "buy") else -1.0
    if r["hold"] <= 6: half[k][1] += r["pnl"]
    else:
        gg, _ = walk(r["b1"], r["op"], r["op"] + 6*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
        half[k][1] += gg if gg is not None else r["pnl"]
    half[k][2] += 1
for k in sorted(half):
    a, b, n = half[k]
    print(f"  {k}: n={n:3d}  实际={a:8.2f}  cap6={b:8.2f}  delta={b-a:7.2f}")

# 分方向
print("\n[cap6 分方向]")
for sd in ("long", "short"):
    sub = [r for r in midok if r["side"] == sd]
    a = sum(r["pnl"] for r in sub); b = 0.0
    for r in sub:
        d = 1.0 if sd == "long" else -1.0
        if r["hold"] <= 6: b += r["pnl"]
        else:
            gg, _ = walk(r["b1"], r["op"], r["op"] + 6*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
            b += gg if gg is not None else r["pnl"]
    print(f"  {sd:5s}: n={len(sub):3d} 实际={a:8.2f} cap6={b:8.2f} delta={b-a:7.2f}")

# 分通道（只列受影响笔数>3）
print("\n[cap6 分通道] 仅列被截断笔数>=3 的通道")
chd = collections.defaultdict(lambda: [0, 0.0, 0.0])
for r in midok:
    if r["hold"] <= 6: continue
    d = 1.0 if r["side"] in ("long", "buy") else -1.0
    gg, _ = walk(r["b1"], r["op"], r["op"] + 6*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
    if gg is None: continue
    e = chd[r["ch"]]; e[0] += 1; e[1] += r["pnl"]; e[2] += gg
for k, (n, a, b) in sorted(chd.items(), key=lambda x: x[1][2]-x[1][1]):
    if n >= 3: print(f"  {k[:26]:26s} n={n:3d} 实际={a:8.2f} cap6={b:8.2f} delta={b-a:7.2f}")

# 长线交叉验证
lg = [r for r in load("long") if r["b1"]]
if lg:
    print(f"\n[长线 tier 交叉验证] n={len(lg)} 实际={sum(r['pnl'] for r in lg):.2f}")
    for T in (6, 12, 24, 36):
        f = 0.0; cc = 0.0; cut = 0
        for r in lg:
            d = 1.0 if r["side"] in ("long", "buy") else -1.0
            g, _ = walk(r["b1"], r["op"], r["op"] + T*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
            if g is not None: f += g
            if r["hold"] <= T: cc += r["pnl"]
            else: cut += 1
        print(f"  T={T:3d}h 固定={f:8.2f} 截断={cc:8.2f} 截断笔数={cut}")

print("\n" + "=" * 78)
print("第 2 部分：延迟平仓（mid）")
print("=" * 78)
ok15 = [r for r in midok if r["b15"]]
disc = [r for r in ok15 if r["ch"] not in HARD]
print(f"有15mK线 n={len(ok15)}  其中裁量型平仓(排除 sl/tp/liq/max_hold) n={len(disc)}"
      f"  实际合计={sum(r['pnl'] for r in disc):.2f}")
print(f"\n{'延后':>6} | {'裁量型 delta合计':>15} {'均值':>7} {'改善/恶化':>10} | {'全样本 delta合计':>16}")
for dl in DELAYS:
    dtot = 0.0; dimp = 0; dworse = 0; atot = 0.0
    for r in ok15:
        d = 1.0 if r["side"] in ("long", "buy") else -1.0
        g, _ = walk(r["b15"], r["cl"], r["cl"] + dl*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
        if g is None: continue
        atot += g - r["pnl"]
    for r in disc:
        d = 1.0 if r["side"] in ("long", "buy") else -1.0
        g, _ = walk(r["b15"], r["cl"], r["cl"] + dl*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
        if g is None: continue
        dd = g - r["pnl"]; dtot += dd
        if dd > 0: dimp += 1
        elif dd < 0: dworse += 1
    print(f"{dl:5.2f}h | {dtot:15.2f} {dtot/len(disc):7.2f} {dimp:4d}/{dworse:<5d} | {atot:16.2f}")

# 延迟显著性 + 分通道
for dl in (1, 2, 4):
    deltas = []
    for r in disc:
        d = 1.0 if r["side"] in ("long", "buy") else -1.0
        g, _ = walk(r["b15"], r["cl"], r["cl"] + dl*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
        if g is not None: deltas.append(g - r["pnl"])
    lo, hi = boot(deltas)
    print(f"\n[延迟 {dl}h 显著性] n={len(deltas)} 合计={sum(deltas):.2f} 均值={sum(deltas)/len(deltas):.2f} "
          f"bootstrap95%CI=[{lo:.2f},{hi:.2f}] {'显著>0' if lo>0 else ('显著<0' if hi<0 else '不显著(跨0)')}")

print("\n[延迟 2h 分通道] 仅列 n>=5")
chdd = collections.defaultdict(lambda: [0, 0.0, 0.0, 0, 0])
for r in disc:
    d = 1.0 if r["side"] in ("long", "buy") else -1.0
    g, _ = walk(r["b15"], r["cl"], r["cl"] + 2*3600, d, r["entry"], r["sl"], r["tp"], r["size"])
    if g is None: continue
    e = chdd[r["ch"]]; e[0] += 1; e[1] += r["pnl"]; e[2] += g
    if g > r["pnl"]: e[3] += 1
    else: e[4] += 1
for k, (n, a, b, i, w) in sorted(chdd.items(), key=lambda x: x[1][2]-x[1][1]):
    if n >= 5:
        print(f"  {k[:24]:24s} n={n:3d} 实际={a:8.2f} 延后2h={b:8.2f} delta={b-a:7.2f} 改善{i}/恶化{w}")
