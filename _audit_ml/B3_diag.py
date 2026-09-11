import os, sys, logging
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
logging.basicConfig(level=logging.DEBUG)
from sqlalchemy import create_engine, text
from backend.database.connection import SessionLocal
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
PID=999_992
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    c.execute(text("delete from brain_attribution where position_id=:p"), {"p":PID}); c.commit()
db = SessionLocal()
try:
    sql = ("INSERT INTO brain_attribution "
           "(position_id, src, nature, symbol, pnl, fee, net, win, close_reason, tier, created_at) "
           "VALUES (:pid, :src, :nat, :sym, :pnl, :fee, :net, :win, :reason, :tier, now()) "
           "ON CONFLICT (position_id, src, tier) DO UPDATE SET pnl = EXCLUDED.pnl, net = EXCLUDED.net")
    for i in range(3):
        try:
            db.execute(text(sql), {"pid":PID,"src":"llm","nat":"swing","sym":"TEST","pnl":float(i),
                                   "fee":0.0,"net":float(i),"win":False,"reason":"unit","tier":"mid"})
            db.commit()
            print(f"write {i} ok")
        except Exception as e:
            print(f"write {i} ERR: {type(e).__name__}: {str(e)[:300]}")
            db.rollback()
finally:
    db.close()
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("select id,pnl,net from brain_attribution where position_id=:p order by id"), {"p":PID}):
        print("row:", tuple(r))
    c.execute(text("delete from brain_attribution where position_id=:p"), {"p":PID}); c.commit()
