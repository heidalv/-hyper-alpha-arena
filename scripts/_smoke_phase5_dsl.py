"""阶段5冒烟：rank/cs_rank/scale 单序列禁用生效。"""
import os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

from backend.services.factor_engine.expr.audit import audit
from backend.services.factor_engine.expr.parser import ExprError, parse

for op in ("rank", "cs_rank", "scale"):
    r = audit({"op": op, "args": [{"f": "close"}, {"c": 1.0}]})
    print(f"① audit({op}) ok={r.ok} errors={r.errors[:1]}")
try:
    parse({"op": "rank", "args": [{"f": "close"}]})
    print("② parse(rank) 未拦截（异常！）")
except ExprError as e:
    print("② parse(rank) → ExprError（拦截成功）")
r2 = audit({"op": "ts_rank", "args": [{"f": "close"}, {"c": 20}]})
print(f"③ audit(ts_rank) ok={r2.ok}（滚动算子放行）")
