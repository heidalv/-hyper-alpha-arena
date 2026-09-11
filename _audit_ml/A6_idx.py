from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### brain_attribution 索引/约束")
    for r in c.execute(text("""select indexname, indexdef from pg_indexes where tablename='brain_attribution'""")):
        print("  ", r[0]); print("     ", r[1])
    for r in c.execute(text("""select conname, pg_get_constraintdef(oid) from pg_constraint
                               where conrelid='brain_attribution'::regclass""")):
        print("  CONSTRAINT", r[0], r[1])
    print("\n### 重复 (position_id, src, tier) 情况")
    for r in c.execute(text("""select count(*) total, count(distinct (position_id, src, tier)) uniq
                               from brain_attribution""")):
        print("  total=", r[0], "uniq=", r[1])
    for r in c.execute(text("""select position_id, src, tier, count(*) n from brain_attribution
                               group by 1,2,3 having count(*)>1 order by n desc limit 8""")):
        print("  ", dict(r._mapping))
    print("\n### 同 position_id 是否 pnl 相同")
    for r in c.execute(text("""select position_id, src, tier, count(*) n,
                                      count(distinct pnl) d_pnl, min(pnl) mn, max(pnl) mx
                               from brain_attribution group by 1,2,3 having count(*)>1
                               order by n desc limit 6""")):
        print("  ", dict(r._mapping))
