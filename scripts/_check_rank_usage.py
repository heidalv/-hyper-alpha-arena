import sys, os, json
sys.path.insert(0, ".")
sys.path.append("backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
from backend.database.connection import AnalyticsSessionLocal
from backend.database.models import FactorActiveSet

db = AnalyticsSessionLocal()
try:
    rows = db.query(FactorActiveSet.factor_id, FactorActiveSet.state, FactorActiveSet.expr_ast).all()
    print("total rows:", len(rows))
    for fid, state, ast_raw in rows:
        if not ast_raw:
            continue
        try:
            ast_dict = ast_raw if isinstance(ast_raw, dict) else json.loads(ast_raw) if isinstance(ast_raw, str) else None
        except Exception:
            ast_dict = None
        if ast_dict is None:
            print(fid, state, "expr_ast 不可解析，跳过")
            continue
        text = json.dumps(ast_dict)
        flags = []
        for op in ("rank", "cs_rank", "scale"):
            if f'"{op}"' in text:
                flags.append(op)
        print(fid, "|", state, "|", ("USES: " + ",".join(flags)) if flags else "clean")
finally:
    db.close()
