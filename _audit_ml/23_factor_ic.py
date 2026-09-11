from sqlalchemy import create_engine, text
import statistics as st
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### factor_active_set full")
    for r in c.execute(text("""select factor_id, source, state, icir, last_net_ic, turnover, period,
                                      activated_at, deactivated_at, evaluated_cycles
                               from factor_active_set order by state, factor_id""")):
        print(dict(r._mapping))
    print("\n### IC by category (recent 30 days, non-null)")
    for r in c.execute(text("""select factor_category, count(*) n, round(avg(ic_value)::numeric,4) avg_ic,
                                      round(stddev(ic_value)::numeric,4) sd, min(recorded_at), max(recorded_at)
                               from factor_performance_logs where ic_value is not null
                                 and recorded_at > now() - interval '30 days'
                               group by 1 order by n desc""")):
        print(r)
    print("\n### top |IC| factors (30d)")
    for r in c.execute(text("""select factor_name, factor_category, count(*) n, round(avg(ic_value)::numeric,4) avg_ic,
                                      round(avg(abs(ic_value))::numeric,4) avg_abs, round(stddev(ic_value)::numeric,4) sd
                               from factor_performance_logs where ic_value is not null and recorded_at > now() - interval '30 days'
                               group by 1,2 having count(*)>10 order by abs(avg(ic_value)) desc limit 25""")):
        print(r)
    print("\n### IC by timeframe")
    for r in c.execute(text("""select timeframe, count(*) n, round(avg(ic_value)::numeric,4) avg_ic, round(avg(abs(ic_value))::numeric,4) avg_abs
                               from factor_performance_logs where ic_value is not null and recorded_at > now() - interval '30 days'
                               group by 1 order by n desc""")):
        print(r)
