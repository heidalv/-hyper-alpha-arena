import os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.mlto.hub_decision_log import persist_brain_decision
from sqlalchemy import create_engine, text
import json
# 用真实库做一次落库验证（symbol 用测试占位，随后清理）
persist_brain_decision(account_id=14, symbol="ZZTEST", tier="mid", direction="long",
                       conviction=62, recommend_open=True, accepted=True, consensus_score=0.71,
                       alignment_score=11, regime="ranging", session_id="unit",
                       thesis_id="unit-thesis", market_summary={}, reasoning="unit test")
eng = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    r = c.execute(text("""select decision_source, decision_snapshot from ai_decision_logs
                          where symbol='ZZTEST' order by id desc limit 1""")).mappings().first()
    if r:
        snap=json.loads(r["decision_snapshot"])
        print("decision_source:", r["decision_source"])
        for k in ("direction","dir_src","llm_qual","llm_conviction","fw_mean","alignment_score",
                  "consensus_score","recommend_open","accepted","regime","llm_direction_require_fw_agree"):
            print(f"   {k} = {snap.get(k)}")
    c.execute(text("delete from ai_decision_logs where symbol='ZZTEST'")); c.commit()
    print("cleaned")
