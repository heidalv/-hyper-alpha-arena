from sqlalchemy import create_engine, text
import json
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("### decision_source='brain' 行数")
    print("  n =", c.execute(text("select count(*) from ai_decision_logs where decision_source='brain'")).scalar())
    for r in c.execute(text("""select symbol, decision_time, decision_snapshot from ai_decision_logs
                               where decision_source='brain' order by id desc limit 3""")):
        d=dict(r._mapping); snap=json.loads(d["decision_snapshot"])
        print(f"\n  {d['symbol']} @ {d['decision_time']}")
        for k in ("tier","direction","dir_src","llm_qual","fw_mean","alignment_score","consensus_score",
                  "recommend_open","accepted","regime","llm_direction_require_fw_agree"):
            print(f"     {k} = {snap.get(k)}")
