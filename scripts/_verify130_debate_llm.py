"""轮130 验证：带 LLM 的牛熊辩论真能跑通并落库（不是装饰）。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.analysis import context_pack as cp  # noqa: E402
from backend.services.mlto import brain_debate as BD  # noqa: E402

print("闸门状态（跑之前）：", json.dumps({k: v for k, v in BD.status().items()
                                          if k in ("enabled", "llm", "apply", "transport",
                                                   "hourly_cap", "cooldown_s")}, ensure_ascii=False))

pack = cp.build("midlong_thesis", symbols=["BTC", "ETH"])
res = BD.run_debate_for_thesis(
    symbol="BTC", tier="mid", conviction=55.0, direction="long",
    pack=pack, extras={}, regime="up", thesis_id="t-verify129", session_id="s-verify",
)
if not res:
    print("被闸门拦下（检查 MIDLONG_DEBATE_* 与灰区）")
    raise SystemExit(0)

print(f"\n裁决 = {res['verdict']}  net={res['net_sentiment']}  consensus={res['consensus_confidence']}"
      f"  used_llm={res['used_llm']}  用时={res['elapsed_s']}s  落库行={res['persist_rows']}")
print(f"\n★ 分周期裁决（主周期={res['primary_horizon']}={res['primary_verdict']}，"
      f"周期冲突={res['horizon_conflict']}，风险共识 min={res['risk_min']}）")
for key, h in (res.get("horizons") or {}).items():
    net_txt = "n/a" if h.get("net") is None else f"{h['net']:+.3f}"
    print(f"  【{h['cn']}】{h['verdict']:<13} net={net_txt}  "
          f"牛={h['bull']:.2f}({h['bull_stance']}{'*推导' if (h.get('derived') or {}).get('bull') else ''}) "
          f"熊={h['bear']:.2f}({h['bear_stance']}{'*推导' if (h.get('derived') or {}).get('bear') else ''})")
    if h.get("bull_arg"):
        print(f"      牛：{h['bull_arg'][:150]}")
    if h.get("bear_arg"):
        print(f"      熊：{h['bear_arg'][:150]}")
print("\n各周期证据：")
for k, evs in (res.get("evidence_by_horizon") or {}).items():
    print(f"  【{BD.HORIZON_CN.get(k, k)}】")
    for e in evs:
        print("     -", str(e)[:130])
print("\n牛方总论点：")
for a in res["bull"]:
    print("  ·", str(a)[:200])
print("熊方总论点：")
for a in res["bear"]:
    print("  ·", str(a)[:200])
print("风险角色（默认规则裁决）：")
for r in res["risk"]:
    print(f"  · {r['role']:<18} conf={r['conf']:.2f}  {r['arg'][:110]}")
print("\n辩论证据（来自真实上下文层）：")
for e in res["evidence"]:
    print("  -", str(e)[:150])

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402
with analytics_engine.connect() as c:
    n = c.execute(text("select count(*) from mlto_debate_log")).scalar()
    print(f"\nmlto_debate_log 行数 = {n}（此前 0）")
    for row in c.execute(text("select side, ts, left(content_json,150) from mlto_debate_log "
                              "order by id desc limit 2")):
        print(f"  {row[0]:<5} {row[1]} | {row[2]}")
print("\n闸门状态（跑之后）：", json.dumps(BD.status()["stats"], ensure_ascii=False))
