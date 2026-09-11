from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### brain_attribution 全部统计")
    for r in c.execute(text("""select tier, src, count(*) n, count(*) filter (where pnl=-1.0) pnl_m1,
                                      min(created_at) mn, max(created_at) mx
                               from brain_attribution group by 1,2 order by n desc""")):
        print(dict(r._mapping))
    print("\n### pnl=-1.0 行的 position_id 范围与是否存在于 paper_positions")
    for r in c.execute(text("""select min(position_id) mn, max(position_id) mx, count(*) n, count(distinct position_id) d
                               from brain_attribution where pnl=-1.0""")):
        print(dict(r._mapping))
    for r in c.execute(text("""select count(*) from brain_attribution b
                               where b.pnl=-1.0 and exists (select 1 from paper_positions p where p.id=b.position_id)""")):
        print("  其中 position_id 真实存在:", r[0])
    print("\n### 同期 paper_positions 是否有对应仓位（8/29 14:59-18:34）")
    for r in c.execute(text("""select count(*) n from paper_positions where id between 5000 and 5100""")):
        print("  paper_positions id 5000-5100:", r[0])
    print("\n### brain_attribution 行 id 范围（pnl=-1.0）")
    for r in c.execute(text("select min(id) mn, max(id) mx from brain_attribution where pnl=-1.0")):
        print("  ", dict(r._mapping))
