from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### lane_ledger 全量样本（87 行，按 net_bp 升序前 12）")
    for r in c.execute(text("""select lane_id,symbol,event,ts,notional,spread_bp,price_bp,fee_bp,net_bp,meta_json
                               from lane_ledger order by net_bp asc limit 12""")):
        d=dict(r._mapping)
        print(f"  {d['symbol']:<5} {d['event']:<8} notional={float(d['notional'] or 0):>8.2f} "
              f"spread={d['spread_bp']} price={d['price_bp']} fee={d['fee_bp']} net={d['net_bp']}")
        print(f"        meta={str(d['meta_json'])[:220]}")
    print("\n### 按 event 汇总")
    for r in c.execute(text("""select event, count(*) n, round(avg(net_bp)::numeric,3) avg_net,
                                      round(sum(net_bp*notional/10000.0)::numeric,4) pnl_usd
                               from lane_ledger group by 1 order by n desc""")):
        print("  ", dict(r._mapping))
    print("\n### lane_runtime_state / lane_shadow_report")
    for t in ("lane_runtime_state","lane_shadow_report","lane_registry"):
        try:
            for r in c.execute(text(f'select * from "{t}" order by 1 desc limit 2')):
                d=dict(r._mapping)
                print(f"  [{t}]", {k:(str(v)[:150] if v is not None else None) for k,v in d.items()})
        except Exception as e:
            print(f"  [{t}] ERR {str(e)[:80]}")
