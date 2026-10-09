"""轮131 运行时验证：混合打分影子 + 风控官判定 + 辩论周期裁决 是否都在活链路发生。"""
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

log = Path("logs/brain_subprocess.log")
print("等待活主脑跑几轮（最多 240s）…")
blend_lines, debate_lines = [], []
for _ in range(24):
    time.sleep(10)
    txt = log.read_text(encoding="utf-8", errors="replace")
    blend_lines = [ln for ln in txt.splitlines() if "混合打分" in ln]
    debate_lines = [ln for ln in txt.splitlines() if "辩论" in ln]
    if blend_lines and debate_lines:
        break

print(f"\n[混合打分] 行数={len(blend_lines)}")
for ln in blend_lines[-4:]:
    print("  ", ln.split("混合打分", 1)[-1][:170])
print(f"\n[辩论] 行数={len(debate_lines)}")
for ln in debate_lines[-3:]:
    print("  ", ln.split("辩论", 1)[-1][:170])

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

with analytics_engine.connect() as c:
    for tbl, label in (("analyst_blend_shadow", "混合打分影子"),
                       ("risk_officer_decisions", "风控官判定"),
                       ("mlto_debate_log", "辩论落库"),
                       ("analyst_signals", "六域信号")):
        try:
            n = c.execute(text(f"select count(*) from {tbl}")).scalar()
            newest = c.execute(text(f"select max(ts) from {tbl}")).scalar()
            print(f"\n{label}（{tbl}）：{n} 行；最新 {newest}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n{label}（{tbl}）：[warn] {type(exc).__name__}: {str(exc)[:80]}")

    print("\n影子对照（近 2h）：")
    try:
        for r in c.execute(text(
                "select symbol, tier, conviction_before, analyst_score, blended, would_change "
                "from analyst_blend_shadow where ts >= now() - interval '2 hours' "
                "order by id desc limit 6")):
            print(f"   {r[0]:<8} {r[1]:<5} conv {r[2]} → {r[4]}  analyst={r[3]:+.3f}  "
                  f"would_change={r[5]}")
    except Exception as exc:  # noqa: BLE001
        print(f"   [warn] {type(exc).__name__}")

    print("\n风控官判定（近 2h）：")
    try:
        rows = c.execute(text(
            "select symbol, side, allow, reason, inputs_json from risk_officer_decisions "
            "where ts >= now() - interval '2 hours' order by id desc limit 6")).fetchall()
        if not rows:
            print("   （近 2h 无开仓尝试 —— 风控官只在有开仓动作时被调用）")
        for r in rows:
            import json as _j
            inp = _j.loads(r[4] or "{}")
            print(f"   {r[0]:<8} {r[1]:<5} allow={r[2]} reason={str(r[3])[:60]} "
                  f"名义=${inp.get('planned_notional_usd')} 权益=${inp.get('equity_usd')} 杠杆={inp.get('leverage')}")
    except Exception as exc:  # noqa: BLE001
        print(f"   [warn] {type(exc).__name__}")
