# -*- coding: utf-8 -*-
"""持仓的 ExitPolicy 快照核查（Y18）：策略在开仓时快照，老仓不受新参数影响。"""
import json
import os

from sqlalchemy import create_engine, text

eng = create_engine(os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"))
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("""
        select symbol, timeframe_tier, trade_nature, opened_at, exit_state_json
        from paper_positions where status='open' order by opened_at
    """)).fetchall():
        d = dict(r._mapping)
        es = d["exit_state_json"]
        try:
            obj = json.loads(es) if es else {}
        except Exception:
            obj = {}
        pol = obj.get("exit_policy") or {}
        print(f"{d['symbol']:<8} tier={d['timeframe_tier']:<5} nature={str(d['trade_nature']):<12} "
              f"opened={str(d['opened_at'])[:16]}")
        print(f"    快照 policy: trail={pol.get('trailing_activation_pct')}/{pol.get('trailing_callback_pct')} "
              f"sl={pol.get('sl_pct')} structural={pol.get('structural_stop')} "
              f"peak_roi={obj.get('exit_policy_peak_roi_pct')} "
              f"struct_stop_price={obj.get('structural_stop_price')}")
