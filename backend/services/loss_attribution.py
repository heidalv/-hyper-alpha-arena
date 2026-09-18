"""loss_attribution — 双车道亏损归因分析（日报/周报的触发式区块）。

某车道近 N 天已实现 PnL < 0 时自动生成归因块：
按币种 / 退出原因 / trade_nature / RR 分桶 + 亏损集中度 top3。

## 轮63 修正（2026-09-18）

1. **分档改走车道真源**：旧版自带一份 `_tier_of_trade`，把 `trade_nature="intraday"` 兜底成
   `"scalp"`，与报告层另一份同名逻辑并存 —— 两处一改就漂移。现在只有
   `backend/config/lane_semantics.py` 一处定义。
2. **文案不再泄漏内部代码**：旧版输出 `"midlong 近 1 天盈利 +5.24，无亏损归因"`，
   把内部枚举名直接写进了给人看的报告（前端原样渲染）。现在用车道展示名「日内 / 长线趋势」。
3. **分桶键名对齐**：`by_symbol`/`by_exit_reason` 每项统一为 `{key, pnl, n}`，
   前端不必再猜某一项有没有 `n`。

数据源：`paper_positions`（closed/liquidated）经 `pnl_authority.realized_pnl` 统一口径。
盈利车道返回空块（一句话说明），不硬编亏损文案。纯规则、非交易路径。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.config import lane_semantics as lane_sem

logger = logging.getLogger(__name__)


def _closed_rows(db, account_id: int, days: int, window=None):
    """已平仓行（未按车道过滤）。window 非空时按该窗口取数（历史回填用）。

    基线用本地时间（与 DB 的 naive 本地时间列同源），不用 `utcnow()`
    —— 见 `pnl_authority` 的时间基准说明。
    """
    from sqlalchemy import or_

    from backend.database.models import PaperPosition
    from backend.services.pnl_authority import rolling_window

    if window is not None:
        start, end = window
    else:
        start, end = rolling_window(days * 24)
    return db.query(PaperPosition).filter(
        PaperPosition.account_id == int(account_id),
        PaperPosition.status.in_(["closed", "liquidated"]),
        or_(PaperPosition.closed_at.between(start, end), PaperPosition.closed_at.is_(None)),
    ).all()


def lane_of_trade(trade_nature: Optional[str], timeframe_tier: Optional[str]) -> str:
    """(nature, tier) → 车道。保留旧函数名，语义改走单一真源。"""
    return lane_sem.resolve_lane(trade_nature, timeframe_tier)


# 旧名别名（有外部调用点，不删除）
_tier_of_trade = lane_of_trade


def _top(d: Dict[str, Dict[str, float]], n: int = 3) -> List[Dict[str, Any]]:
    """按 PnL 升序（最亏在前）取前 n 项，统一输出 {key, pnl, n}。"""
    items = sorted(d.items(), key=lambda x: x[1]["pnl"])[:n]
    return [{"key": k, "pnl": round(v["pnl"], 2), "n": int(v["n"])} for k, v in items]


def build_loss_attribution(db, account_id: int, horizon: str, days: int = 1,
                           window=None) -> Dict[str, Any]:
    """生成某车道的亏损归因块。盈利/无样本时返回 {active: False, note}。

    `horizon` 参数名保留兼容，接受车道名或任何旧 horizon 值（内部统一解析）。
    `window` 非空时按该窗口取数（历史回填），否则按「现在回溯 days 天」。
    """
    from backend.services.pnl_authority import realized_pnl

    spec = lane_sem.get_spec(horizon)
    lane = spec.lane
    rows = [r for r in _closed_rows(db, account_id, days, window=window)
            if lane_sem.resolve_lane_for_position(r) == lane]

    pnls = [realized_pnl(r) for r in rows]
    if not pnls:
        return {"active": False, "lane": lane, "lane_label": spec.label,
                "note": f"{spec.label}车道近 {days} 天无平仓样本"}
    total = sum(pnls)
    if total >= 0:
        return {
            "active": False, "lane": lane, "lane_label": spec.label,
            "note": f"{spec.label}车道近 {days} 天盈利 +{total:.2f}，无亏损归因",
            "total_pnl": round(total, 2),
        }

    losses = [r for r in rows if realized_pnl(r) < 0]
    by_symbol: Dict[str, Dict[str, float]] = {}
    by_reason: Dict[str, Dict[str, float]] = {}
    by_nature: Dict[str, Dict[str, float]] = {}
    by_sym_all: Dict[str, Dict[str, float]] = {}
    for r in rows:
        pnl = realized_pnl(r)
        pnl = float(pnl or 0.0)
        s = str(r.symbol or "?").upper()
        for bucket, key in (
            (by_sym_all, s),
            (by_symbol if pnl < 0 else None, s),
            (by_reason if pnl < 0 else None, str(getattr(r, "close_reason", None) or "unknown")),
            (by_nature if pnl < 0 else None, str(getattr(r, "trade_nature", None) or "untagged")),
        ):
            if bucket is None:
                continue
            d = bucket.setdefault(key, {"pnl": 0.0, "n": 0})
            d["pnl"] += pnl
            d["n"] += 1

    return {
        "active": True,
        "lane": lane,
        "lane_label": spec.label,
        "window_days": days,
        "total_pnl": round(total, 2),
        "n_trades": len(rows),
        "n_losses": len(losses),
        # 旧字段名保留（前端与既有测试按此读取）
        "by_symbol": _top(by_symbol),
        "by_symbol_all": _top(by_sym_all, n=len(by_sym_all)),
        "by_exit_reason": _top(by_reason),
        "by_trade_nature": _top(by_nature),
    }
