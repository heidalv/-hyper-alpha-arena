# -*- coding: utf-8 -*-
"""临时核查：进化策略 59a768 的 ai_strategies 行与 master_prompt 绑定。"""
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
        "SELECT strategy_id, master_prompt_template_id, prompt_version, account_id "
        "FROM ai_strategies WHERE strategy_id = 'tpl_pro_075b93de_59a768'"
    )).fetchall()
    print("exact match:", rows)
    like = c.execute(text(
        "SELECT strategy_id, master_prompt_template_id, prompt_version, account_id "
        "FROM ai_strategies WHERE strategy_id LIKE 'tpl_pro_075b93de%'"
    )).fetchall()
    for r in like:
        print("like:", r)
    # 有 master_prompt 的那 1 个策略是谁
    mp = c.execute(text(
        "SELECT strategy_id, master_prompt_template_id, prompt_version "
        "FROM ai_strategies WHERE master_prompt_template_id IS NOT NULL"
    )).fetchall()
    for r in mp:
        print("master_prompt:", r)
