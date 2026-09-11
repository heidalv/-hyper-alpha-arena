# -*- coding: utf-8 -*-
"""Z61: §51 死闸判定自动复核（三准则）——轻量版（限定目录，避免全仓 rglob 超时）。

准则①：生产调用点计数（排除 tests/ 与 _ptmp/）
准则③：真实数据确认「声称的安全属性是否真的在跑」
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

SCAN_DIRS = [ROOT / "backend" / d for d in ("services", "api", "config", "research", "core")]
SKIP = ("_ptmp", "\\data\\", "__pycache__")


def scan(name: str):
    prod, test = [], []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            sp = str(p)
            if any(s in sp for s in SKIP):
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(txt.splitlines(), 1):
                if re.search(rf"(?<![\w.]){name}\s*\(", line) and not line.strip().startswith("def "):
                    rel = p.relative_to(ROOT)
                    (test if "tests" in p.parts else prod).append(f"{rel}:{i}")
    return prod, test


print("=== 准则① 调用点计数（生产 / 测试）===")
for fn in ("check_lane_limits", "assert_live_allowed", "check_side_allowed",
           "should_stop_loss", "vol_regime_blocked", "trend_blocked_side"):
    prod, test = scan(fn)
    print(f"  {fn:<22} 生产 {len(prod):>2} 处 {prod[:3]} | 测试 {len(test)} 处")

print()
print("=== 准则③ 真实数据：MM 影子车道是否在运行 ===")
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
eng = create_engine(URL, connect_args={"connect_timeout": 10})
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for r in c.execute(text("select lane_id, mode, status, updated_at from lane_registry order by 1")).fetchall():
        print("  lane", [str(x) for x in r])
    print("  lane_ledger 行数 =", c.execute(text("select count(*) from lane_ledger")).scalar())
