# -*- coding: utf-8 -*-
"""解冻模拟账户上「亏损触发」的冻结（2026-09-11 用户指令）。

用户原话：
  「模拟账户交易还配置全局冻结？」
  「模拟账户冻结交易你妈啊，本来就是收集交易数据，你还弄个极端亏损冻结」

纸面账户的唯一目的是**收集交易数据**；代码侧已把四处亏损类冻结改为
paper 豁免（见 `backend/services/risk_management/loss_lock_policy.py`），
但**历史遗留的冻结状态仍在库里**，必须一并解除，否则策略依旧不产样本：

1. `strategy_health_service` / `unified_learning_service` 造成的
   `ai_strategies.status in ('paused',)` 且 **genome 无 pause_reason**
   （有 `pause_reason` 的是 training_orchestrator 的槽位重平衡，属正常生命周期，不动）；
2. 极端连亏造成的 **永久禁用**：`genome.permanently_disabled=true`
   （实测 1 例 `scalp_lane_scalp_f9`「连续亏损1227次永久禁用」）。

用法：
    .venv\\Scripts\\python.exe backend/scripts/unfreeze_paper_loss_locks.py            # 干跑
    .venv\\Scripts\\python.exe backend/scripts/unfreeze_paper_loss_locks.py --apply    # 落库
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"))

from backend.database.connection import SessionLocal  # noqa: E402
from backend.database.models import AIStrategy  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正落库（缺省只干跑）")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        # ── 1. 永久禁用（极端连亏） ──
        perm = [
            s for s in db.query(AIStrategy).filter(AIStrategy.genome.isnot(None)).all()
            if (s.genome or {}).get("permanently_disabled")
        ]
        # ── 2. paused 且无 pause_reason（亏损/健康度冻结；有 reason = 训练重平衡，不动）──
        paused = [
            s for s in db.query(AIStrategy).filter(AIStrategy.status == "paused").all()
            if not (s.genome or {}).get("pause_reason")
        ]

        print(f"永久禁用(极端连亏): {len(perm)} 例")
        for s in perm:
            print(f"  - {s.strategy_id} | {str(s.archive_reason or '')[:60]}")
        print(f"paused 且无 pause_reason: {len(paused)} 例")
        for s in paused[:5]:
            print(f"  - {s.strategy_id}")

        if not args.apply:
            print("\n[干跑] 未改动任何数据；加 --apply 落库。")
            return 0

        now = datetime.now(timezone.utc)
        n = 0
        for s in perm:
            g = dict(s.genome or {})
            g.pop("permanently_disabled", None)
            g["paper_unfreeze_at"] = now.isoformat()
            g["paper_unfreeze_reason"] = "模拟账户不做亏损冻结（用户指令 2026-09-11）"
            s.genome = g
            s.status = "active"
            s.archive_reason = ""
            s.archived_at = None
            n += 1
        for s in paused:
            g = dict(s.genome or {})
            g["paper_unfreeze_at"] = now.isoformat()
            g["paper_unfreeze_reason"] = "模拟账户不做亏损冻结（用户指令 2026-09-11）"
            s.genome = g
            s.status = "active"
            n += 1
        db.commit()
        print(f"\n[已落库] 解冻 {n} 个策略（permanently_disabled={len(perm)}, paused={len(paused)}）")

        rows = db.query(AIStrategy.status).all()
        from collections import Counter

        print("解冻后 status 分布:", dict(Counter(r[0] for r in rows)))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
