# -*- coding: utf-8 -*-
"""手动同步最新冠军 TP/SL → RuntimeGovernor evolution_gc 意图。

背景：独立进化进程在模板2预计算时被外部环境终止，run-end 的
_sync_champion_to_v5_gates 未触发。冠军参数已持久化在 backtest_runs，
此处按同一逻辑补发意图（夹紧 [V5_MIN_RISK_REWARD, 2.5]，TTL 7 天）。
"""
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

_env = {}
for _line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        _env[_k] = _v

eng = create_engine(_env["DATABASE_URL"], pool_pre_ping=True)
with eng.connect() as c:
    c.execute(text("SET app.is_admin='on'"))
    c.execute(text("SET app.tenant_id=326"))
    row = c.execute(text(
        "SELECT run_id, strategy_config FROM backtest_runs "
        "WHERE is_champion ORDER BY completed_at DESC LIMIT 1"
    )).fetchone()

if not row:
    print("无冠军记录")
    sys.exit(1)

run_id, cfg = row[0], row[1]
if isinstance(cfg, str):
    cfg = json.loads(cfg)

sl = float(cfg.get("stop_loss_pct") or 0)
tp = float(cfg.get("take_profit_pct") or 0)
print(f"champion {run_id}: sl={sl:.4f} tp={tp:.4f}")

if sl <= 0 or tp <= 0:
    print("冠军无 TP/SL，跳过")
    sys.exit(0)

try:
    from backend.config.settings import V5_MIN_RISK_REWARD, V5_MAX_RUNTIME_MIN_RR
    rr_floor = float(V5_MIN_RISK_REWARD)
    rr_cap = float(V5_MAX_RUNTIME_MIN_RR)
except Exception:
    rr_floor, rr_cap = 1.8, 2.5

derived_rr = round(max(rr_floor, min(rr_cap, tp / sl)), 2)
print(f"derived min_risk_reward = {derived_rr} (tp/sl={tp/sl:.3f})")

from backend.services.runtime_governor import runtime_governor as gov
gov.submit_intent(
    "min_risk_reward", derived_rr, source="evolution_gc",
    confidence=0.5,
    reason=(
        f"champion {run_id} TP {tp:.1%}/SL {sl:.1%} → rr={derived_rr} "
        f"(手动补发：独立进化进程被环境终止，run-end 同步未触发)"
    ),
)
print("意图已提交 RuntimeGovernor")
