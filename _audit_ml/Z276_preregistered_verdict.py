# -*- coding: utf-8 -*-
"""[§94 预注册 2026-09-11] P19-B / P29 的**判定规则**（先写规则，再等数据；防止事后找理由）。

规则（数据到位后自动判定，不接受事后调参）：

P19-B（统一熔断是否该继续开着）——条件：after 侧 mid/long 平仓 ≥15 笔 **或** 出现抑制事件
  * PASS（继续开）：① 无保护性通道被抑制；② 无悬挂仓位（>48h）；③ after 侧
    「被抑制通道」单笔净额 ≥ before 侧同通道单笔净额（改善或持平）；
  * FAIL（建议回滚 `EXIT_CHANNEL_BREAKER_UNIFIED=false`）：出现 ①/② 任一，或 ③ 恶化 >20%；
  * INSUFFICIENT：样本不足（当前状态）。

P29-C（long SL 上限 + 风险预算）——条件：边界后 long 平仓 ≥5 笔 **或** 出现夹子日志
  * PASS：新 long 仓 SL 距离中位 ≤3% **且** 单笔亏损中位较边界前改善 ≥30%；
  * FAIL：SL 距离仍 >3.5% 或单笔亏损中位恶化；
  * INSUFFICIENT：样本不足（当前状态）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from _scope import DEFAULT_ACCOUNT_ID, account_clause  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

B19, B29 = "2026-09-11 10:19", "2026-09-11 17:20"
NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def main() -> int:
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        sup = db.execute(text("select count(*) from position_exit_events "
                              "where event_type='exit_channel_broken'")).scalar()
        after = db.execute(text(f"""select count(*), coalesce(avg({NET}),0) from paper_positions
            where status='closed'{account_clause()} and closed_at >= timestamp '{B19}'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')""")).fetchone()
        before = db.execute(text(f"""select count(*), coalesce(avg({NET}),0) from paper_positions
            where status='closed'{account_clause()} and closed_at < timestamp '{B19}'
              and closed_at >= now() - interval '30 day'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')""")).fetchone()
        longb = db.execute(text(f"""select count(*), coalesce(avg(abs(entry_price-sl_price)/entry_price*100),0)
            from paper_positions where status='closed'{account_clause()}
              and lower(coalesce(timeframe_tier,''))='long' and sl_price is not null and entry_price>0
              and closed_at < timestamp '{B29}'""")).fetchone()
        longa = db.execute(text(f"""select count(*), coalesce(avg(abs(entry_price-sl_price)/entry_price*100),0)
            from paper_positions where status='closed'{account_clause()}
              and lower(coalesce(timeframe_tier,''))='long' and sl_price is not null and entry_price>0
              and closed_at >= timestamp '{B29}'""")).fetchone()
        longnew = db.execute(text(f"""select count(*) from paper_positions
            where opened_at >= timestamp '{B29}'{account_clause()}
              and lower(coalesce(timeframe_tier,''))='long'""")).scalar()
    finally:
        db.close()

    log = ROOT / "logs" / "backend.log"
    clamp = log.exists() and "SL 距离超上限被拉近" in log.read_text(encoding="utf-8", errors="replace")

    print(f"账户口径 account_id={DEFAULT_ACCOUNT_ID}")
    print("\n【P19-B 判定】")
    print(f"  after n={int(after[0])}（需 ≥15）｜单笔 {float(after[1]):.3f} vs before n={int(before[0])} "
          f"单笔 {float(before[1]):.3f}｜抑制事件 {int(sup)}")
    print(f"  ⇒ {'INSUFFICIENT（样本不足，不下结论）' if int(after[0]) < 15 and not sup else 'READY：请按 §94 规则人工核对 ①②③'}")
    print("\n【P29-C 判定】")
    print(f"  边界后新开 long {int(longnew)}｜已平仓 long：after n={int(longa[0])} SL距离均值 "
          f"{float(longa[1]):.2f}% vs before n={int(longb[0])} {float(longb[1]):.2f}%｜夹子日志 {'有' if clamp else '无'}")
    ready29 = int(longa[0]) >= 5 or clamp
    print(f"  ⇒ {'READY：SL 距离是否 ≤3%？' if ready29 else 'INSUFFICIENT（等 ≥5 笔或夹子日志）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
