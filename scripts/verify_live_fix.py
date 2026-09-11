# -*- coding: utf-8 -*-
"""实盘零成交修复 · 全链路 dry-run 验证（只读 + 门控策略调用，绝不下单）。

验证项：
  1. LiveExecutor.get_balance / get_positions 对 dataclass 返回不再报错
  2. fetch_live_account_snapshot（宪法风控输入）能取到真实权益/持仓
  3. live_gate_policy 引导期判定
  4. decide_scalp 实盘引导期 pwin 地板
  5. scalp_factor_router 实盘自适应门槛（账户自身样本，不再吃 paper 亏损）
  6. scalp_ev_gate 实盘引导期 EV 地板 + meta 硬过滤降级
  7. unified_gate V5 实盘引导期置信度门槛
  8. short_tier_entry_gate 熔断账户隔离（实盘 BTC 不再被 paper 2913 连亏误禁）
  9. get_live_equity 快照路径
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

from backend.core.tenant import set_system_identity  # noqa: E402

set_system_identity()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("[PASS] " if cond else "[FAIL] ") + name + (f"  -> {detail}" if detail else ""))


def main():
    from backend.database.connection import SessionLocal
    from backend.database.models import Account

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == 188).first()
        check("账户188存在且 live/binance", bool(account and account.trading_mode == "live"
              and account.selected_exchange == "binance"), f"name={account.name if account else None}")

        # ── 1/2. LiveExecutor 余额/持仓 ──
        from backend.services.exchange.live_executor import LiveExecutor
        ex = LiveExecutor()
        bal = ex.get_balance(db, 188)
        check("LiveExecutor.get_balance 返回 dict（修复 dataclass bug）",
              isinstance(bal, dict) and float(bal.get("total_equity") or 0) > 0,
              f"bal={bal}")
        pos = ex.get_positions(db, 188)
        # [2026-08-28] 交易所 REST 当前无持仓（VIRTUAL 已于 ~22:41 平仓，
        # REST 是权威口径），这里只验证：不再抛 dataclass 异常、返回规范化 list。
        _pos_ok = isinstance(pos, list) and all(
            isinstance(p, dict) and "symbol" in p for p in pos
        )
        check("LiveExecutor.get_positions 返回规范化列表（修复 dataclass bug）",
              _pos_ok, f"pos={pos}")

        # ── 3. 宪法风控 snapshot ──
        from backend.services.full_auto.live_trading import fetch_live_account_snapshot
        snap = fetch_live_account_snapshot(db, 188)
        check("fetch_live_account_snapshot 权益>0",
              float(snap.get("total_equity") or 0) > 0, f"snap={snap}")

        # ── 4. 引导期策略 ──
        from backend.services.full_auto.live_gate_policy import (
            live_bootstrap_active, live_scalp_pwin_floor,
            live_scalp_v5_confidence, live_scalp_ev_min, live_scalp_daily_open_cap,
        )
        boot = live_bootstrap_active(188)
        check("live_bootstrap_active(188)=True（0 样本）", boot is True,
              f"floor={live_scalp_pwin_floor()} v5={live_scalp_v5_confidence()} "
              f"ev_min={live_scalp_ev_min()} cap={live_scalp_daily_open_cap()}")

        # ── 5. decide_scalp 实盘地板 ──
        from backend.services.decision_fusion_arbiter import decide_scalp
        d1 = decide_scalp(pwin=0.46, factor_score=37, direction="long",
                          tp_pct=0.015, sl_pct=0.0115, mode="live", account_id=188)
        check("decide_scalp(live pwin=0.46) 引导期放行", d1.allowed, f"d1={d1.to_dict()}")
        d2 = decide_scalp(pwin=0.40, factor_score=37, direction="long",
                          tp_pct=0.015, sl_pct=0.0115, mode="live", account_id=188)
        check("decide_scalp(live pwin=0.40) 仍被地板拦（0.45）", not d2.allowed, f"d2={d2.to_dict()}")
        d3 = decide_scalp(pwin=0.46, factor_score=37, direction="long",
                          tp_pct=0.015, sl_pct=0.0115, mode="paper", account_id=14)
        check("decide_scalp(paper) 行为不变（严格 rr_aware 地板拦截 0.46）",
              not d3.allowed, f"d3={d3.to_dict()}")

        # ── 6. 实盘自适应门槛（账户自身样本） ──
        from backend.services.scalp_factor_router import scalp_factor_router as _router
        from backend.config.settings import SCALP_FACTOR_CONFIRM_THRESHOLD
        thr_live = _router._get_adaptive_threshold("BNB", is_paper=False, account_id=188)
        check("实盘 BNB 门槛=CONFIRM（不再吃 paper 亏损的 50）",
              int(thr_live) == int(SCALP_FACTOR_CONFIRM_THRESHOLD),
              f"thr_live={thr_live} confirm={SCALP_FACTOR_CONFIRM_THRESHOLD}")
        thr_paper = _router._get_adaptive_threshold("BNB", is_paper=True)
        check("paper BNB 门槛保持原逻辑(<=38)", int(thr_paper) <= 38, f"thr_paper={thr_paper}")

        # ── 7. EV 闸门实盘引导期 ──
        from backend.services.scalp.scalp_ev_gate import scalp_ev_gate
        ev = scalp_ev_gate.evaluate(
            symbol="BNB", factor_score=37, direction="long",
            tp_pct=0.015, sl_pct=0.0115, notional_usd=30.0,
            exchange="binance", strategy_tag="trend", mode="live",
            account_id=188,
        )
        check("scalp_ev_gate(live) 引导期放行（EV≈-0.68% ≥ -1.0%）", ev.allowed, f"ev={ev.reason}")

        # ── 8. V5 实盘引导期置信度门 ──
        from backend.services.decision_core.unified_gate import evaluate_entry
        g = evaluate_entry(
            db=db, account_id=188, symbol="BNB", action="buy",
            confidence=46, tier="short", trade_nature="scalp",
            tp_pct=0.015, sl_pct=0.0115, market_data={}, mode="live",
        )
        check("unified_gate(live conf=46, RR1.30) 引导期放行（门槛45/RR1.3）",
              g.allowed, f"g={g.reason}")

        # ── 9. 熔断账户隔离 ──
        from backend.services.short_tier_entry_gate import check_short_tier_entry
        cb_live = check_short_tier_entry(
            account_id=188, symbol="BTC", side="buy", action="buy",
            confidence=60, tier="short", trade_nature="scalp", mode="live",
        )
        check("实盘 BTC 不再被 paper 连亏熔断误禁", cb_live.allowed, f"reason={cb_live.reason}")
        cb_paper = check_short_tier_entry(
            account_id=14, symbol="BTC", side="buy", action="buy",
            confidence=60, tier="short", trade_nature="scalp", mode="paper",
        )
        check("paper BTC 保留历史熔断（行为不变）", not cb_paper.allowed, f"reason={cb_paper.reason}")

        # ── 10. live equity ──
        from backend.services.full_auto.live_equity import get_live_equity
        eq = get_live_equity(account, 188)
        check("get_live_equity(188)>0（快照路径）", eq > 0, f"eq={eq}")

    finally:
        db.close()

    print("\n===== 结果: PASS=%d FAIL=%d =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)
    print("全部通过 —— 实盘链路（下单前）已可走通。")


if __name__ == "__main__":
    main()
