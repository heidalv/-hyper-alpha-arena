from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("""select factor_id, source, expr_ast, expr_id from factor_active_set
                               where source like 'seed%' or source like 'rev%' or source like 'mom%' or source like 'vol%' or source like 'ts_rank%' or source like 'vp_corr%'
                               order by source""")):
        d=dict(r._mapping)
        print(f"  {d['source']:<18} {str(d['factor_id']):<18} {json.dumps(d['expr_ast'], ensure_ascii=False) if d['expr_ast'] else None}")
