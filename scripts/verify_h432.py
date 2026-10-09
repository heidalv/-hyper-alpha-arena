# -*- coding: utf-8 -*-
"""h432 v2 部署核实：参数 + 试跑元信息 + 近期腿况。只读。"""
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
cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
m = cur.fetchone()[0]
p = m.get("params") or {}
print("exit_skew_k =", p.get("exit_skew_k"), "| vol_spread_k =", p.get("vol_spread_k"))
t = m.get("h432_trial") or {}
print("h432_trial:", json.dumps(t, ensure_ascii=False)[:220])
print("全部修复参数:",
      {k: p.get(k) for k in ("stop_maker_grace_sec", "sudden_move_cooldown_sec",
                             "jump_exit_bp", "stop_loss_vol_min",
                             "max_one_side_seconds", "exit_skew_k", "vol_spread_k")})
