# -*- coding: utf-8 -*-
"""四系统耦合 · 追问三件事（只读）：
  ① 981 次 LLM 调用到底是谁在用（caller 分布）
  ② 主脑 skip-open 理由在"解冻 A 之前/之后"的对照
  ③ 重启后论题方向分布（long vs short）——方向生成是否仍与行情相反
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

p = ROOT / "logs" / "backend.log"
with open(p, "rb") as f:
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - 30_000_000))
    raw = f.read().decode("utf-8", errors="replace").splitlines()

marks = [ln[:19] for ln in raw if "Application startup complete." in ln]
RS = marks[-1] if marks else ""
BOUNDARY_A = "2026-09-18 13:16:44"      # A 生效（第二次重启）
post = [ln for ln in raw if ln[:19] >= BOUNDARY_A]
pre = [ln for ln in raw if "2026-09-18 12:00" <= ln[:19] < BOUNDARY_A]

print("=" * 96)
print(f"重启边界 = {RS}   A 生效边界 = {BOUNDARY_A}")
print(f"窗口：解冻后 {len(post):,} 行 / 解冻前 {len(pre):,} 行")
print("=" * 96)

# ── ① LLM caller 分布 ──
print("\n【① 谁在调用 LLM（caller 分布，解冻后窗口）】")
callers = Counter()
for ln in post:
    m = re.search(r"caller=([^\s\)]+)", ln)
    if m and ("LLM sync" in ln or "流式调用" in ln):
        callers[m.group(1)] += 1
for k, v in callers.most_common(20):
    print(f"   {v:5d}  {k}")
if not callers:
    print("   （未解析到 caller 字段）")

# 备用 LLM 决策车道的标志词
print("\n   —— LLM 决策车道（备用/主控）标志词 ——")
for kw in ("call_ai_for_decision_with_fallback", "execute_ai_decisions", "PromptContextBuilder",
           "prompt_context.builder", "synthesize", "MasterController", "ai_decision_service",
           "trading_commands", "fusion_verdicts", "ai_decisions"):
    n_post = sum(1 for ln in post if kw in ln)
    n_pre = sum(1 for ln in pre if kw in ln)
    print(f"   {kw:38s} 解冻前={n_pre:5d}  解冻后={n_post:5d}")

# ── ② 主脑 skip-open 理由：解冻前 vs 后 ──
def skip_reasons(rows):
    c = Counter()
    for ln in rows:
        if "MidLongBrain] skip open" in ln:
            m = re.search(r"reason=([^\s]+)", ln)
            if m:
                c[m.group(1).split(":")[0]] += 1
    return c

pre_s, post_s = skip_reasons(pre), skip_reasons(post)
keys = sorted(set(pre_s) | set(post_s), key=lambda k: -(pre_s[k] + post_s[k]))
print("\n【② 主脑 skip-open 理由对照（按大类）】")
print(f"   {'reason':44s} {'解冻前':>7s} {'解冻后':>7s}")
for k in keys:
    print(f"   {k:44s} {pre_s[k]:>7d} {post_s[k]:>7d}")

# 位置闸硬否决对照
for label, rows in (("解冻前", pre), ("解冻后", post)):
    hard = sum(1 for ln in rows if "追高天花板" in ln and "硬否决" in ln)
    shr = sum(1 for ln in rows if "位置闸 paper 缩仓" in ln or ("paper 缩仓" in ln and "放行" in ln))
    lg = sum(1 for ln in rows if "location_gate" in ln or "位置闸" in ln)
    print(f"\n   [{label}] 位置闸相关={lg}  缩仓放行={shr}  **硬否决={hard}**")

# ── ③ 论题方向分布 ──
print("\n【③ 论题方向分布（重启后，Analytics 库）】")
try:
    from sqlalchemy import text
    from backend.database.connection import AnalyticsSessionLocal
    db = AnalyticsSessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        rows = db.execute(text("""
            SELECT tier, direction, accepted, recommend_open, COUNT(*)
            FROM mlto_thesis
            WHERE created_at >= now() - interval '30 minutes'
            GROUP BY 1,2,3,4 ORDER BY 5 DESC
        """)).fetchall()
        if not rows:
            print("   （近 30 分钟无新论题；改用事件表推断方向）")
            rows2 = db.execute(text("""
                SELECT payload_json FROM mlto_thesis_events
                WHERE event_type='open_blocked' AND ts >= now() - interval '30 minutes'
                LIMIT 200
            """)).fetchall()
            d = Counter()
            for (x,) in rows2:
                m = re.search(r'"direction"\s*:\s*"(\w+)"', str(x))
                if m:
                    d[m.group(1)] += 1
            print("   open_blocked 的 direction 分布:", dict(d))
        for r in rows:
            print(f"   tier={str(r[0]):6s} dir={str(r[1]):7s} accepted={str(r[2]):5s} "
                  f"rec_open={str(r[3]):5s} n={r[4]}")
    finally:
        db.close()
except Exception as exc:  # noqa: BLE001
    print(f"   （读取失败: {type(exc).__name__}: {str(exc)[:110]}）")
