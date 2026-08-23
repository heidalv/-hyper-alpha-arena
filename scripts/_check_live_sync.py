# -*- coding: utf-8 -*-
"""核查：哪些运行中策略使用 tpl_mid_bull_momentum（冠军参数同步目标）。"""
import sys
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
    # genome 字段里带 source_template_id 的策略
    rows = c.execute(text(
        "SELECT strategy_id FROM ai_strategies WHERE genome->>'source_template_id' = 'tpl_mid_bull_momentum'"
    )).fetchall()
    print("source_template_id match:", rows)
    # template 名引用
    rows2 = c.execute(text(
        "SELECT strategy_id FROM ai_strategies WHERE genome->>'template_id' = 'tpl_mid_bull_momentum'"
    )).fetchall()
    print("template_id match:", rows2)
    # 中周期动量追踪 模板相关的 ai_strategies（名字相似）
    rows3 = c.execute(text(
        "SELECT strategy_id, status FROM ai_strategies WHERE strategy_id LIKE '%mid_bull%' LIMIT 8"
    )).fetchall()
    print("mid_bull strategies:", rows3)
