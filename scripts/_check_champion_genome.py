# -*- coding: utf-8 -*-
"""核查：冠军 genome 是否含 TP/SL 参数（决定 gates 同步是否触发）。"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text

_env = {}
for _line in Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        _env[_k] = _v

eng = create_engine(_env["DATABASE_URL"], pool_pre_ping=True)
with eng.connect() as c:
    c.execute(text("SET app.is_admin='on'"))
    c.execute(text("SET app.tenant_id=326"))
    r = c.execute(text(
        "SELECT strategy_config FROM backtest_runs WHERE run_id='champ_3846025d'"
    )).fetchone()
    cfg = r[0] if r else None
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    keys = sorted((cfg or {}).keys())
    print("champion config keys:", keys)
    print("stop_loss_pct =", cfg.get("stop_loss_pct"), "take_profit_pct =", cfg.get("take_profit_pct"))
    pp = cfg.get("pipeline_params")
    print("pipeline_params keys:", sorted((pp or {}).keys())[:30] if isinstance(pp, dict) else type(pp))
