from sqlalchemy import create_engine, text
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### peak_pnl_pct / trough_pnl_pct 分布（mid/long 全部）")
    for r in c.execute(text("""select count(*) n,
                                      count(*) filter (where peak_pnl_pct is null) null_peak,
                                      count(*) filter (where peak_pnl_pct = 0) zero_peak,
                                      count(*) filter (where peak_pnl_pct <> 0) nonzero_peak,
                                      count(*) filter (where peak_unrealized_pnl is null) null_peaku,
                                      count(*) filter (where peak_unrealized_pnl <> 0) nonzero_peaku,
                                      count(*) filter (where health_score is not null) has_health,
                                      count(*) filter (where exit_state_json is not null) has_state
                               from paper_positions where timeframe_tier in ('mid','long')""")):
        print(dict(r._mapping))
    print("\n### 谁在写 peak_pnl_pct / peak_unrealized_pnl")
    for r in c.execute(text("""select id,symbol,side,status,peak_pnl_pct,peak_unrealized_pnl,unrealized_pnl,
                                      opened_at,closed_at from paper_positions
                               where timeframe_tier in ('mid','long') and (peak_pnl_pct<>0 or peak_unrealized_pnl<>0)
                               order by id desc limit 8""")):
        print(dict(r._mapping))
    print("\n### 全库 peak_pnl_pct 非零比例")
    for r in c.execute(text("""select count(*) n, count(*) filter (where peak_pnl_pct<>0) nz,
                                      count(*) filter (where peak_pnl_pct is null) nul
                               from paper_positions""")):
        print(dict(r._mapping))
