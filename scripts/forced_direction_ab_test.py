# -*- coding: utf-8 -*-
"""受控行为验证：对 3 个关键标的**强制重析一次**，做方向的前后对照。

## 为什么必须强制
`persist_brain_decision` 只在 **LLM 分析路径**写入 ⇒ 行为样本天生受 `expires_at` 制约：
mid 的 TTL = **14400s（4h）**，而 13:2x–13:4x 刚刷新过 ⇒ 下一批自然到 **~17:30**。
靠等不是合理窗口；本脚本用**子进程自带的入口**（`python -m ...brain_subprocess`）触发一次。

## 边界（据实声明）
- **只对本次子进程**注入 `MIDLONG_THESIS_TTL_MID_S=600` 与 `MIDLONG_THESIS_MAX_REFRESH_PER_CYCLE=5`
  （**不改 `.env`、不改运行中进程的 env**）；
- `analysis_only=True` ⇒ **只写论题、不开仓**；
- 副作用：这 3 个标的的 `expires_at` 会变成 +600s ⇒ 生产调度会在 ~10 分钟后**多析一次**
  （一次即自愈，之后回到 4h TTL）；
- 3 次 LLM 调用。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYMS = ["VIRTUAL", "ASTER", "XRP"]
TIER = "mid"

from sqlalchemy import text  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal  # noqa: E402

# mlto_thesis 在 **Analytics 库**（不是业务库）——我第一版用 SessionLocal 报了 UndefinedTable。
db0 = AnalyticsSessionLocal()
try:
    db0.execute(text("SET app.is_admin='on'"))
    _COLS = [c for (c,) in db0.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='mlto_thesis'"
    )).fetchall()]
finally:
    db0.close()
print("mlto_thesis 列:", ", ".join(_COLS))


def snap(tag: str):
    want = [c for c in ("symbol", "tier", "direction", "llm_conviction", "accepted",
                        "recommend_open", "expires_at", "updated_at", "created_at")
            if c in _COLS]
    db = AnalyticsSessionLocal()
    try:
        db.execute(text("SET app.is_admin='on'"))
        print(f"\n=== {tag} ===")
        rows = db.execute(text(
            f"SELECT {', '.join(want)} FROM mlto_thesis "
            "WHERE tier = :t AND symbol = ANY(:syms) ORDER BY symbol"
        ), {"t": TIER, "syms": SYMS}).fetchall()
        out = {}
        for r in rows:
            d = dict(zip(want, r))
            out[d.get("symbol")] = str(d.get("direction"))
            print("   " + "  ".join(f"{k}={d.get(k)}" for k in want))
        if not rows:
            print("   （无行）")
        return out
    finally:
        db.close()

before = snap("重析前（生产 TTL=4h 下最后一次分析的结果）")

env = dict(os.environ)
env["MIDLONG_THESIS_TTL_MID_S"] = "600"
env["MIDLONG_THESIS_MAX_REFRESH_PER_CYCLE"] = "5"
env["PYTHONIOENCODING"] = "utf-8"

print("\n=== 触发受控重析（只影响本子进程）===")
cmd = [sys.executable, "-m", "backend.services.mlto.brain_subprocess",
       "--tier", TIER, "--symbols", ",".join(SYMS), "--trigger", "forced_verify"]
print("   " + " ".join(cmd))
r = subprocess.run(cmd, cwd=str(ROOT), env=env, timeout=900,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
txt = (r.stdout or b"").decode("utf-8", errors="replace")
print(f"   退出码 = {r.returncode}")
for ln in txt.splitlines()[-25:]:
    print("   | " + ln[:180])

after = snap("重析后")

print("\n=== 方向前后对照 ===")
print(f"   {'symbol':10s} {'前':>8s} {'后':>8s}  变化")
for s in SYMS:
    b, a = before.get(s, "?"), after.get(s, "?")
    print(f"   {s:10s} {b:>8s} {a:>8s}  {'← 改变' if b != a else '（未变）'}")
