# -*- coding: utf-8 -*-
"""[轮156] 把 DB 里的 DeepSeek 绑定改成官方规范名 `deepseek-flash`（V4.1 Flash）。

依据：官方更新日志 2026-09-10 —— "Change the model name to `deepseek-flash` to call the
latest V4.1 Flash model. The previous-generation models V4 Flash and V4 Flash Vision Exp
have been retired; for compatibility, the model names `deepseek-v4-flash` and
`deepseek-v4-flash-vision-exp` are temporarily routed to V4.1 Flash."

只改 provider=deepseek 且 model='deepseek-v4-flash' 的行（即 id=17），其它不动。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import engine  # noqa: E402

with engine.begin() as c:
    rows = c.execute(text(
        "select id, name, model from llm_configurations "
        "where provider='deepseek' and model='deepseek-v4-flash' order by id")).fetchall()
    print(f"待更新行数 = {len(rows)}")
    for r in rows:
        print(f"   id={r[0]} {r[1]} : {r[2]} → deepseek-flash")
    if rows:
        c.execute(text(
            "update llm_configurations set model='deepseek-flash' "
            "where provider='deepseek' and model='deepseek-v4-flash'"))
    print("\n更新后：")
    for r in c.execute(text(
            "select id, name, model, is_default from llm_configurations "
            "where provider='deepseek' order by id")):
        print(f"   id={r[0]} {r[1][:34]:<34} model={r[2]:<16} default={r[3]}")
