"""One-shot: create VIP coin-select tables/columns."""
from sqlalchemy import inspect, text

from backend.database.connection import engine
from backend.database.models import (  # noqa: F401
    Account,
    CoinSelectAdoption,
    CoinSelectCandidate,
    CoinSelectScan,
    User,
)

CoinSelectScan.__table__.create(bind=engine, checkfirst=True)
CoinSelectCandidate.__table__.create(bind=engine, checkfirst=True)
CoinSelectAdoption.__table__.create(bind=engine, checkfirst=True)

insp = inspect(engine)
ucols = {c["name"] for c in insp.get_columns("users")}
acols = {c["name"] for c in insp.get_columns("accounts")}
alters = []
if "coin_select_enabled" not in ucols:
    alters.append("ALTER TABLE users ADD COLUMN coin_select_enabled VARCHAR(10) DEFAULT 'false'")
if "coin_select_auto_follow" not in ucols:
    alters.append("ALTER TABLE users ADD COLUMN coin_select_auto_follow VARCHAR(10) DEFAULT 'false'")
if "coin_select_default_session" not in ucols:
    alters.append("ALTER TABLE users ADD COLUMN coin_select_default_session VARCHAR(64)")
if "ai_coin_select_enabled" not in acols:
    alters.append("ALTER TABLE accounts ADD COLUMN ai_coin_select_enabled VARCHAR(10) DEFAULT 'false'")

with engine.begin() as conn:
    for sql in alters:
        conn.execute(text(sql))
        print("added:", sql)

print(
    "tables:",
    insp.has_table("coin_select_scans"),
    insp.has_table("coin_select_candidates"),
    insp.has_table("coin_select_adoptions"),
)

from backend.services.coin_select_platform_service import list_board

print("board:", list_board())
