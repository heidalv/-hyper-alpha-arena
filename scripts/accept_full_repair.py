# -*- coding: utf-8 -*-
"""全面修复验收核查：硬约束 + 运行时状态 + 参数一致性（部署后必读运行时）。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

print("== ① 硬约束：腿速 ==")
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE ts >= now() - interval '30 minutes'),
           COUNT(*) FILTER (WHERE ts >= now() - interval '2 hours')
    FROM lane_ledger WHERE event='fill'
""")
n30, n2h = cur.fetchone()
print(f"  近 30min {n30} 腿 = {n30*2}/h {'✓' if n30*2 >= 60 else '⚠ 破线'}"
      f" | 近 2h {n2h} 腿 = {n2h/2:.0f}/h")

print("\n== ② 硬约束：持仓时长（30s–5min）==")
cur.execute("""
    SELECT ts, symbol, meta_json->>'side', (meta_json->>'qty')::float8
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '2 hours'
    ORDER BY ts
""")
rows = cur.fetchall()
by = {}
for ts, sym, side, qty in rows:
    by.setdefault(sym, []).append((ts, side, qty or 0.0))
durs, open_now = [], []
for sym, fl in by.items():
    inv, start = 0.0, None
    for ts, side, qty in fl:
        d = qty if side == "buy" else -qty
        prev = inv
        inv += d
        if abs(prev) < 1e-12 and abs(inv) > 1e-12:
            start = ts
        if start and abs(inv) < 1e-12:
            durs.append((ts - start).total_seconds())
            start = None
    if start:
        open_now.append((sym, inv, (rows[-1][0] - start).total_seconds()))
durs.sort()
if durs:
    print(f"  完成周期 {len(durs)}：中位 {durs[len(durs)//2]:.0f}s 最大 {durs[-1]:.0f}s"
          f" | 超 300s 占比 {sum(1 for d in durs if d > 300)/len(durs)*100:.0f}%")
print(f"  当前未平仓 {len(open_now)} 个" +
      ("".join(f"\n    {s} qty={q:.4f} 已持 {a:.0f}s" for s, q, a in open_now) if open_now else ""))

print("\n== ③ 运行时状态 vs 注册表（部署必须读运行时）==")
st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
lim, p = j.get("limits") or {}, j.get("params") or {}
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
rp = m.get("params") or {}
checks = [("ofi_confirm_threshold", lim, rp), ("trend_only_q", lim, rp),
          ("timeout_hard_taker_sec", lim, rp), ("stop_ref_last_leg", lim, rp),
          ("trail_lock_bp", lim, rp), ("post_stop_decay", lim, rp),
          ("ofi_flatten_threshold", lim, rp), ("vwap_revert_bp", lim, rp),
          ("exit_skew_k", p, rp), ("vol_spread_k", p, rp),
          ("compound_ratio", rp, rp)]
for k, src, other in checks:
    wk = src.get(k)
    rv = rp.get(k)
    ok = "✓" if (wk is not None and rv is not None and abs(float(wk) - float(rv)) < 1e-6) else "?"
    print(f"  [{ok}] {k:<26} worker={wk}  registry={rv}")

cur.execute("""
    SELECT symbol, state_json FROM lane_runtime_state WHERE lane_id='mm_asterdex'
    ORDER BY symbol LIMIT 1
""")
sym, sj = cur.fetchone()
s = sj if isinstance(sj, dict) else json.loads(sj or "{}")
print(f"  ar300_hist 长度（{sym}）= {len(s.get('ar300_hist') or [])}"
      f" {'✓ 自适应闸在线' if len(s.get('ar300_hist') or []) >= 60 else '（累积中）'}")

print("\n== ④ 近 2h 盈亏与出场路径 ==")
cur.execute("""
    SELECT COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'), COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric,3)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '2 hours'
    GROUP BY 1 ORDER BY 3
""")
for r in cur.fetchall():
    print(f"  {str(r[0]):<24} n={r[1]:>4} Σ=${r[2]:>+7}")
print(f"\n  equity={j.get('equity')} ok={j.get('ok')}")
