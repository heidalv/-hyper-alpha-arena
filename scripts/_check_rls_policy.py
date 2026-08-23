# -*- coding: utf-8 -*-
"""核查：prompt_training_records 的 RLS 策略定义。"""
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
        "SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies "
        "WHERE tablename='prompt_training_records'"
    )).fetchall()
    for r in rows:
        print(r)
