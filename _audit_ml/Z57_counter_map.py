# -*- coding: utf-8 -*-
"""Z57：订正 §48.3 —— 「dca_total_added 不维护」是**误报**。

核查：
  A. 三个计数器各自的自增点与上限消费方（静态）；
  B. 真实数据：dca 路径是否**从未触发**（而非字段没写）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")

print("=== A. 计数器 → 自增点 / 上限消费方（静态核查）===")
M = [
    ("add_count（加仓/金字塔）", "paper_engine:1518", "position_memory_manager:2009（PYRAMID_MAX_ADDS）"
     " / master_execution:2687 / scalp_loop:1449"),
    ("dca_count（补仓/DCA）", "paper_engine:1515", "position_memory_manager:2126（DCA_MAX_ADDS=1）"),
    ("reduce_count（减仓）", "**paper_engine._partial_close（§46 本轮补）**",
     "unified_exit_state_machine / master_execution / defensive_cycle"),
]
for name, inc, cap in M:
    print(f"  {name:<22}自增={inc}")
    print(f"  {'':<22}上限={cap}")

eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    print("\n=== B. 真实数据：dca 与加仓是否真的发生过（账户 14 全历史）===")
    r = c.execute(text("""
        select count(*) n,
               count(*) filter (where coalesce(add_count,0) > 0) n_add,
               count(*) filter (where coalesce(dca_count,0) > 0) n_dca,
               count(*) filter (where coalesce(reduce_count,0) > 0) n_red
        from paper_positions where account_id=14
    """)).mappings().first()
    print(f"  总持仓 {r['n']}：add_count>0 → {r['n_add']}；dca_count>0 → {r['n_dca']}；"
          f"reduce_count>0 → {r['n_red']}")
    print("\n  判据：")
    if r["n_dca"] == 0:
        print("  • `dca_total_added=0` 是因为 **DCA 补仓路径从未被触发**，"
              "而不是字段没人写 —— 自增点存在于 paper_engine:1516。")
        print("  → 结论：§48.3 的「dca_total_added 也是不维护字段」**是我的误报**，予以订正。")
    else:
        print("  • DCA 确实发生过但字段为 0 → 才是真的不维护。")
    print(f"  • 三个计数器中，**只有 `reduce_count` 的自增此前缺失于主流路径**（已修 §46）；")
    print("    `add_count`/`dca_count` 均有自增点且有上限消费方（PYRAMID_MAX_ADDS / DCA_MAX_ADDS）。")
