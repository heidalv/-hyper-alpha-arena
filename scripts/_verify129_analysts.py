"""轮129 自检：六分析师信号层 → 主脑上下文（context_pack）是否真的接通。"""
import ast
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for f in ("backend/services/analysis/context_pack.py", "backend/main.py",
          "backend/services/analysts/service.py", "backend/services/analysts/scorers.py",
          "backend/services/analysts/signals.py", "backend/config/env_registry.py"):
    ast.parse(Path(f).read_text(encoding="utf-8"))
    print("AST OK", f)

from backend.services.analysis import context_pack as cp  # noqa: E402
from backend.services.analysts import contract_report, latest_signals, run_once  # noqa: E402

print("\n=== 1) 跑一轮信号（幂等）===")
r = run_once(["BTC", "ETH", "SOL"])
print("  signals =", r["signals"], "written =", r["written"], "undelivered =", r["undelivered_domains"])

print("\n=== 2) 契约报告（承诺 vs 交付）===")
rep = contract_report()
print("  declared =", [d["domain"] for d in rep["declared_domains"]])
print("  delivered =", {k: {kk: vv for kk, vv in v.items() if kk != 'last_ts'} for k, v in rep["delivered"].items()})
print("  undelivered =", rep["undelivered"])
for d, reasons in (rep.get("missing_reasons") or {}).items():
    print(f"   ! {d}: {reasons[0][:110]}")

print("\n=== 3) 主脑上下文层 ===")
pack = cp.build("midlong_thesis", symbols=["BTC", "ETH", "SOL"])
print("  layers =", sorted(pack.layers.keys()))
an = pack.layers.get("analysts")
print("  analysts 层存在 =", bool(an))
if an:
    print("  global =", json.dumps(an.get("global"), ensure_ascii=False))
    print("  symbols =", list((an.get("symbols") or {}).keys()))
    print("  missing =", an.get("missing"))
    print("  BTC 信号 =", json.dumps((an.get("symbols") or {}).get("BTC"), ensure_ascii=False))
txt = pack.to_prompt_text(200000)
print("  prompt 长度 =", len(txt), "| 含 'analysts' =", "analysts" in txt)
print("  errors =", (pack.errors or [])[:3])

print("\n=== 4) 落库明细（前 6 条，含 evidence）===")
for s in latest_signals(["BTC"], within_hours=12)[:6]:
    print(f"  {s['domain']:<12} {s['symbol']:<6} score={s['score']:+.3f} conf={s['confidence']:.2f} "
          f"q={s['data_quality']:<7} as_of={s['as_of']}")
    ev = {k: v for k, v in list((s.get("evidence") or {}).items())[:4]}
    print(f"      evidence={json.dumps(ev, ensure_ascii=False)[:150]}")
