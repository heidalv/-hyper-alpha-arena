import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.source_attribution import _record_attribution_row
from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
def count():
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        return c.execute(text("select count(*) from brain_attribution where position_id=999999")).scalar()
print("before:", count())
for i in range(3):
    _record_attribution_row(position_id=999999, src="llm", nature="swing", symbol="TEST",
                            pnl=-1.0+i, fee=0.0, net=-1.0+i, win=False,
                            close_reason="unit_test", tier="mid")
    print(f"after write {i+1}:", count())
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    r=c.execute(text("select pnl, net, close_reason from brain_attribution where position_id=999999")).mappings().first()
    print("final row:", dict(r))
    c.execute(text("delete from brain_attribution where position_id=999999"))
    c.commit()
print("cleaned up")
