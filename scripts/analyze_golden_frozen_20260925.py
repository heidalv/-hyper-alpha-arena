import json, sys
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import SessionLocal
with SessionLocal() as s:
    print("=== 被关学习的 8 个'有交易'策略：genome 证据 ===")
    for x in s.execute(text("""
        SELECT strategy_id, status, learning_enabled, genome
        FROM ai_strategies
        WHERE strategy_id IN ('tpl_mid_range_c1126e','tpl_mid_range_253ba5','tpl_mid_range_5226c6',
                              'tpl_mid_range_c3e85c','tpl_mid_range_814602','auto_efd68a88ca',
                              'auto_dfa9e3b348','tpl_mid_range_0d4016')
        ORDER BY strategy_id
    """)):
        g = x[3]
        try:
            gd = json.loads(g) if isinstance(g, str) else (g or {})
        except Exception:
            gd = {}
        tags = gd.get("tags")
        print(f"  {x[0]:26s} status={str(x[1])[:8]:8s} learn={x[2]} tags={tags} "
              f"graduation_status={gd.get('graduation_status')} graduated_at={str(gd.get('graduated_at'))[:19]}")
    print("\n=== 全库：golden_frozen 标记 与 learning_enabled 的关系 ===")
    for x in s.execute(text("""
        SELECT (genome::text LIKE '%golden_frozen%') has_tag, learning_enabled, count(*)
        FROM ai_strategies GROUP BY 1,2 ORDER BY 3 DESC
    """)):
        print(f"  golden_frozen={x[0]}  learning_enabled={x[1]}  n={x[2]}")
