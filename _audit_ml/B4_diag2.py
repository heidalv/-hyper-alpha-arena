import os, sys, logging
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(name)s %(message)s')
from sqlalchemy import create_engine, text
from backend.services.source_attribution import _record_attribution_row
from backend.database.connection import SessionLocal
PID=999_993
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
def clean():
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        c.execute(text("delete from brain_attribution where position_id=:p"), {"p":PID}); c.commit()
clean()
for i in range(3):
    _record_attribution_row(position_id=PID, src="llm", nature="swing", symbol="TEST",
                            pnl=float(i), fee=0.0, net=float(i), win=False, close_reason="unit", tier="mid")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    rows=[tuple(r) for r in c.execute(text("select id,pnl,net,created_at from brain_attribution where position_id=:p order by id"), {"p":PID})]
    print("rows:", rows)
    print("count:", len(rows))
clean()
