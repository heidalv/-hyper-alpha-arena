# -*- coding: utf-8 -*-
"""频率与防线总检（自适应闸生效后）。只读。"""
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
cur.execute("""
    SELECT COUNT(*) FILTER (WHERE ts >= now() - interval '25 minutes'),
           COUNT(*) FILTER (WHERE ts >= now() - interval '60 minutes'),
           ROUND(SUM(net_bp*notional/1e4) FILTER (WHERE ts >= now() - interval '60 minutes')::numeric, 3)
    FROM lane_ledger WHERE event='fill'
""")
n25, n60, usd60 = cur.fetchone()
print(f"近 25min: {n25} 腿 = {n25*2.4:.0f}/h")
print(f"近 60min: {n60} 腿 = {n60}/h，Σ=${usd60}")

st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
lim = j.get("limits") or {}
p = j.get("params") or {}
hist = (j.get("states") or {}).get("BNB") or {}
print(f"\n参数：trend_only_q={lim.get('trend_only_q')} trend_only_bp={lim.get('trend_only_bp')} "
      f"vwap_revert_bp={lim.get('vwap_revert_bp')} ofi_confirm={lim.get('ofi_confirm_threshold')} "
      f"exit_skew_k={p.get('exit_skew_k')} vol_spread_k={p.get('vol_spread_k')}")
print(f"ar300_hist 长度={len(hist.get('ar300_hist') or [])}（≥60 才启用分位门槛）")
print(f"worker ok={j.get('ok')} equity={j.get('equity')}")
sk = j.get("skip_counts") or {}
print("skip 相关:", {k: v for k, v in sk.items()
                    if k in ("trend_only_flat", "vwap_revert_up", "vol_regime",
                             "trend_up", "trend_down", "sudden_move")})
print("\n判定:", "✓ 频率达标" if n25 * 2.4 >= 60 else "⚠ 仍低于 60/h")
