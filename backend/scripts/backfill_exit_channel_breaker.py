# -*- coding: utf-8 -*-
"""[§79 执行 2026-09-11 / 决策 P21] 回填出场通道熔断的滚动窗（以 DB 为真相源）。

用法：
    python backend/scripts/backfill_exit_channel_breaker.py --dry-run      # 只看会变成什么
    python backend/scripts/backfill_exit_channel_breaker.py --days 30      # 执行（自动备份）
    python backend/scripts/backfill_exit_channel_breaker.py --days 30 --account 14

安全设计：
  * 覆盖写状态文件前**先备份**（`data/fusion_attribution.json.bak_<ts>`）；
  * 原子写入（tmp + os.replace）；
  * 计数**只增不减**（`merge_into_state` 硬规则）；
  * 阈值取**实际生效值**（`EXIT_CHANNEL_SHADOW_MIN_N` / `_MAX_WR`，.env 为 15/0.40）；
  * 回填后**立即预览**会被 shadow 的通道（但不动 `breaker_shadow` 字段本身 ——
    由 `EXIT_CHANNEL_REBUILD_ON_LOAD=true` 在下次加载时统一重建，避免两处口径打架）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from backend.services.exit.breaker_backfill import (  # noqa: E402
    merge_into_state,
    series_from_rows,
    shadow_snapshot,
)
from backend.services import source_attribution as sa  # noqa: E402

#: 与 `portfolio_budget._strategy_drawdown_sigma` 同源的净口径（含部分平仓与已付费用）
NET_SQL = (
    "((p.close_price - p.entry_price) * p.size * "
    "case when lower(p.side) in ('long','buy') then 1 else -1 end"
    " + coalesce(p.partial_realized_pnl,0) - coalesce(p.partial_fee_paid,0))"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    # [§91 修复 2026-09-11 / 缺陷 #74] 默认**按活跃 PAPER 账户**（14）回填：
    # 熔断是在线风控闸，证据窗不得混入已归档测试账户（#147/#149）与短期实验账户（#156）。
    # 现场：30 天全层平仓 400/1864 笔（21.5%）来自非 14 账户；实测**当前不改变**判定，
    # 但口径必须正确。`--all-accounts`（等价 --account 0）仅用于对照。
    ap.add_argument("--account", type=int,
                    default=int(os.environ.get("AUDIT_ACCOUNT_ID", "14") or 14),
                    help="默认 14=活跃 PAPER 账户；0=全部账户（仅对照）")
    ap.add_argument("--all-accounts", action="store_true",
                    help="显式使用全部账户（等价 --account 0）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--state", default="", help="状态文件路径（默认取 source_attribution 的）")
    args = ap.parse_args()
    if args.all_accounts:
        args.account = 0

    state_path = Path(args.state or sa._STATE_PATH)
    if not state_path.exists():
        print(f"❌ 状态文件不存在: {state_path}")
        return 2

    from sqlalchemy import text as t

    from backend.database.connection import SessionLocal

    db = SessionLocal()
    db.execute(t("set app.is_admin='on'"))
    try:
        sql = f"""
            select coalesce(close_reason,'') reason, coalesce(timeframe_tier,'') tier,
                   ({NET_SQL}) net,
                   extract(epoch from p.closed_at) ts
            from paper_positions p
            where p.status='closed' and p.close_price is not null
              and p.closed_at > now() - interval '{int(args.days)} days'
              {'and p.account_id = ' + str(int(args.account)) if args.account else ''}
            order by p.closed_at asc
        """
        rows = db.execute(t(sql)).fetchall()
    finally:
        db.rollback()
        db.close()

    # [§84/P27-A] 带上 `closed_at` 时间戳 ⇒ 回填同时补齐"证据新鲜度"判据（缺陷 #69）
    seq = [(r[0], r[1], float(r[2] or 0) > 0, float(r[3] or 0)) for r in rows]
    series = series_from_rows(seq)
    print(f"读入已平仓 {len(seq)} 笔（近 {args.days} 天）；折叠出 {len(series)} 个 tier|通道")

    state = json.loads(state_path.read_text(encoding="utf-8"))
    before = shadow_snapshot(state.get("breaker") or {},
                             min_n=int(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "15") or 15),
                             max_wr=float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40))
    new_breaker, stats = merge_into_state(state.get("breaker") or {}, series)
    after = shadow_snapshot(new_breaker,
                            min_n=int(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "15") or 15),
                            max_wr=float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40))

    print(f"回填统计: {stats}")
    print(f"可评估通道: {before['evaluable']} → {after['evaluable']}")
    print(f"会被 shadow: {len(before['shadowed'])} → {len(after['shadowed'])}")
    for k in after["shadowed"]:
        print(f"    shadow: {k}")
    if args.dry_run:
        print("\n（dry-run，未写入）")
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = state_path.with_suffix(state_path.suffix + f".bak_{ts}")
    shutil.copy2(state_path, backup)
    state["breaker"] = new_breaker
    tmp = state_path.with_suffix(state_path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_path)
    print(f"\n已回填并原子写入 {state_path.name}；备份 {backup.name}")
    print("提示：`EXIT_CHANNEL_REBUILD_ON_LOAD=true` 会在下次进程加载时据此重建 shadow 标志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
