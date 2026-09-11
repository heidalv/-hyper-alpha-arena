# -*- coding: utf-8 -*-
"""Z47：手续费口径缺口量化——「总 USD」到底少扣了多少费？

Z46 证明：
  - `unrealized_pnl` = **毛盈亏**（(close−entry)×size 精确相等，差 0.000）；
  - 账户恒等式 `equity = initial + realized + unrealized − total_fee_paid` 精确成立；
  - 但 `partial_fee_paid` 全账户仅 $8.73（3044 笔），`total_fee_paid` 仅 $42.15。

若真实成本是「单边 5bp × 2」，则每笔 ~0.1% 名义 → 本分析窗口（~288 笔 × ~$500）
应有 ~$140 费用未被任何列覆盖 ⇒ **此前所有「总 USD」口径都系统性偏乐观**。
本脚本按三档费率量化缺口，并给出修正后的口径。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def main() -> int:
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = c.execute(text("""
            select id, symbol, side, entry_price, close_price, size, original_size,
                   unrealized_pnl, partial_realized_pnl, partial_fee_paid
            from paper_positions
            where account_id=14 and status='closed' and close_price>0 and original_size>0
            order by id desc limit 300
        """)).fetchall()
        n = len(rows)
        tot_u = tot_p = tot_pf = 0.0
        tot_notional = 0.0
        for r in rows:
            tot_u += float(r[7] or 0)
            tot_p += float(r[8] or 0)
            tot_pf += float(r[9] or 0)
            tot_notional += float(r[4]) * float(r[6])
        gross = tot_u + tot_p
        cur = gross - tot_pf
        print(f"样本 n={n}（账户 14 最近 {n} 笔已平仓）")
        print(f"  Σ unrealized（毛）        = {tot_u:+.2f}")
        print(f"  Σ partial_realized        = {tot_p:+.2f}")
        print(f"  Σ partial_fee_paid        = {tot_pf:+.2f}")
        print(f"  当前口径「总USD」= 毛 − partial_fee = {cur:+.2f}")
        print(f"  Σ 平仓名义（close×size）  = {tot_notional:,.0f}")

        print("\n=== 按不同费率估算「应扣费用」与修正后口径 ===")
        print(f"  {'费率假设':<28}{'应扣费':>10}{'修正后总USD':>14}{'与原口径差':>12}")
        for label, side_bp in (("单边 5bp ×2（费）", 0.0005),
                               ("单边 5bp+5bp 滑点 ×2", 0.001),
                               ("单边 5bp 仅开仓", 0.0005)):
            mult = 2 if "仅开仓" not in label else 1
            fee = tot_notional * side_bp * mult
            fixed = cur - (fee - tot_pf)
            print(f"  {label:<28}{fee:>10.2f}{fixed:>+14.2f}{fixed-cur:>+12.2f}")

        print("\n=== 逐笔示例（看 partial_fee_paid 是否≈0）===")
        zero = sum(1 for r in rows if float(r[9] or 0) == 0)
        print(f"  partial_fee_paid == 0 的笔数 = {zero}/{n}（{zero/n:.1%}）")

        print("\n=== 账户级汇总（全生命周期）===")
        b = c.execute(text("""
            select initial_balance, total_equity, realized_pnl, unrealized_pnl, total_fee_paid
            from paper_balances where account_id=14
        """)).mappings().first()
        print(f"  initial={float(b['initial_balance']):.2f} equity={float(b['total_equity']):.2f} "
              f"realized={float(b['realized_pnl']):+.2f} unrealized={float(b['unrealized_pnl']):+.2f} "
              f"total_fee_paid={float(b['total_fee_paid']):.2f}")
        npos = c.execute(text(
            "select count(*) from paper_positions where account_id=14")).scalar()
        fee = float(b["total_fee_paid"])
        print(f"  全账户持仓数={npos}；total_fee_paid/持仓 = {fee/max(npos,1):.4f} 美元/笔")
        print(f"  → 若按 0.1%×名义计费，3044 笔应有费用量级 "
              f"≈ {tot_notional/n*0.001*npos:,.0f} 美元 ⇒ **费用列与实际交易规模不匹配**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
