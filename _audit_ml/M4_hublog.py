from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### decision_source='hub' 行数（重启后）")
    n = c.execute(text("select count(*) from ai_decision_logs where decision_source='hub'")).scalar()
    print("  n =", n)
    for r in c.execute(text("""select id, symbol, decision_time, operation, decision_snapshot
                               from ai_decision_logs where decision_source='hub'
                               order by id desc limit 4""")):
        d = dict(r._mapping)
        snap = json.loads(d["decision_snapshot"]) if isinstance(d["decision_snapshot"], str) else d["decision_snapshot"]
        print(f"\n  {d['symbol']} @ {d['decision_time']} op={d['operation']}")
        for k in ("direction","dir_src","llm_qual","fw_mean","hub_action","hub_adjusted","hub_mode",
                  "ai_governed_weight","llm_direction_require_fw_agree","regime","tier"):
            if k in (snap or {}):
                print(f"     {k} = {snap[k]}")
