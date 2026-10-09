# -*- coding: utf-8 -*-
"""本轮根因修复的**运行态验收**（只读）：方向 / D(分位) / F(互锁)。

边界 = 日志里最后一次 `Application startup complete.`
"""
from __future__ import annotations

import io
import json
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
    f.seek(max(0, size - 20_000_000))
    raw = f.read().decode("utf-8", errors="replace").splitlines()
marks = [ln[:19] for ln in raw if "Application startup complete." in ln]
RS = marks[-1] if marks else ""
post = [ln for ln in raw if ln[:19] >= RS] if RS else raw
print("=" * 92)
print(f"验收窗口 = {RS} → {raw[-1][:19] if raw else '?'}   行数={len(post):,}")
print("=" * 92)

# ── D：分位是否仍 >100% ──
pcts = [int(m.group(1)) for ln in post for m in [re.search(r"分位(\d+)%", ln)] if m]
over = sorted({p for p in pcts if p > 100})
print(f"\n[D] 位置闸分位样本 {len(pcts)} 条；**>100% 的 = {len([p for p in pcts if p > 100])} 条**"
      f" 取值={over}")
if not pcts:
    print("    ⏳ 重启后位置闸尚未被评估（周期未跑到）")
elif not over:
    print("    ⇒ ✅ D 生效：不再出现数学上不可能的分位")
else:
    print("    ⇒ ⚠️ D 未生效（仍出现 >100%）")

# ── F：互锁告警是否接入（等真触发）──
il = [ln for ln in post if "[MidLongInterlock]" in ln]
print(f"\n[F] [MidLongInterlock] 告警行 = {len(il)}")
for ln in il[-5:]:
    print("    " + ln[:170])
if not il:
    print("    （未触发属正常：需同一标的在 900s 内两方向都被拦；"
          "非空且含『两方向同时被否』即证明生效）")

# ── 方向：新快照里的方向份额 ──
print("\n[方向] 重启后的 `_brain_decision_log` 方向分布")
try:
    from sqlalchemy import text
    from backend.database.connection import AnalyticsSessionLocal
    db = AnalyticsSessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        rows = db.execute(text("""
            SELECT symbol, decision_snapshot FROM ai_decision_logs
            WHERE created_at >= :rs AND decision_snapshot LIKE '%_brain_decision_log%'
            ORDER BY id DESC LIMIT 800
        """), {"rs": RS or "1970-01-01"}).fetchall()
        dirs = Counter()
        per = Counter()
        for sym, snap in rows:
            try:
                d = json.loads(snap)
            except Exception:  # noqa: BLE001
                continue
            dr = str(d.get("direction") or "")
            dirs[dr] += 1
            per[(str(sym), dr)] += 1
        tot = sum(dirs.values())
        print(f"    样本 {tot} 条：{dict(dirs)}")
        if tot:
            sh = dirs.get("short", 0) / tot
            print(f"    short 占比 = {sh:.1%}（修复前基线：75%）")
        print(f"    (symbol,direction) Top8: {dict(per.most_common(8))}")
        if not tot:
            print("    ⏳ 重启后尚无新决策快照（LLM 周期未跑到）")
    finally:
        db.close()
except Exception as exc:  # noqa: BLE001
    print(f"    （台账读取失败: {type(exc).__name__}: {str(exc)[:100]}）")
