# -*- coding: utf-8 -*-
"""exchange_credentials 加积分开关列（幂等）。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
from sqlalchemy import create_engine, text

e = create_engine(
    "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena",
    isolation_level="AUTOCOMMIT",
)
db = e.connect()
db.execute(text(
    "ALTER TABLE exchange_credentials ADD COLUMN IF NOT EXISTS "
    "points_enabled BOOLEAN NOT NULL DEFAULT false"
))
db.execute(text(
    "ALTER TABLE exchange_credentials ADD COLUMN IF NOT EXISTS points_config JSON"
))
cols = [r[0] for r in db.execute(text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name='exchange_credentials' AND column_name LIKE 'points%'"
))]
print("columns:", cols)
print("DONE")
