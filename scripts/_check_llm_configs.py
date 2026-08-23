# -*- coding: utf-8 -*-
"""核查：租户326 的 LLM 配置（factor_mining 用）。"""
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
    rows = c.execute(text(
        "SELECT id, name, provider, is_active, is_default, usage_scope, "
        "length(api_key)>0 AS has_key, tenant_id "
        "FROM llm_configurations ORDER BY id"
    )).fetchall()
    for r in rows:
        print(r)
