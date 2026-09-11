"""live_learning_hooks — 实盘交易接入因子学习闭环的开仓/平仓钩子。

[2026-09-02 因子闭环修复 D10] 背景：实盘路径完全不进因子学习闭环。
全仓扫描 ``record_entry_signals`` / ``update_trade_pnl`` 的调用点后确认：

  - paper 路径：``paper_trading_engine._fill_market_order`` 内部写开仓因子快照、
    ``_notify_learning_on_close`` 内部回填平仓 pnl —— 闭环完整；
  - live  路径：``LiveExecutor`` → ``LivePositionManager`` 全程零调用，
    ``signal_trade_feedback`` 表里没有任何实盘样本。

后果：``factor_ic_evaluator`` 计算因子 IC 的唯一数据源就是
``signal_trade_feedback``，因此实盘成交对因子权重的贡献恒为 0 —— 真金白银
换来的样本被丢弃，因子权重完全由模拟盘决定。

设计取舍（三条都是踩过的坑，改动前请先读）：

  1. **独立会话**：``record_entry_signals`` / ``update_trade_pnl`` 内部都会
     ``db.commit()``。若复用主链路 session，会把下单事务里尚未完成的中间状态
     一并提交；钩子内部异常还会让主 session 进入 InFailedSqlTransaction，
     污染后续所有 SQL。故这里全部使用独立会话，与主链路彻底隔离。
  2. **只记因子、不记情报信号**：paper 侧还会采集 funding/oi/whale 等情报信号，
     但那要走 ``IntelligenceSignalEngine``（含外部网络调用），挂在实盘下单
     路径上不可接受。因子值从本地 K 线库计算，且因子 IC 闭环正是本次修复目标。
  3. **幂等**：同一子仓位加仓会二次触发开仓钩子。重复写快照会让同一因子在同一
     ``trade_id`` 下出现多行、并被 ``update_trade_pnl`` 赋予相同 pnl，IC 计算中
     该样本即被重复计数（等于偷偷加权）。故以首次快照为准，重复调用直接跳过。

单位约定：``pnl_pct`` 用 ROI 小数（与 paper 侧 ``_notify_learning_on_close``
的 ``pnl / (entry_price * full_size)`` 同口径），不是百分数。混用会让实盘样本
与模拟盘样本相差 100 倍，直接毁掉 IC 估计。
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# 实盘学习钩子总开关。默认开启；若钩子拖慢下单或异常刷屏，可在 .env 置 false
# 快速摘除而无需回滚代码（摘除代价：实盘样本重新停止进入因子闭环）。
_ENV_FLAG = "LIVE_LEARNING_HOOKS_ENABLED"

# 因子快照所用 K 线根数。与 paper 侧保持一致：多数因子回看窗口 <= 50 根，
# 100 根既够用又不至于让 compute_all_factors 明显变慢。
_KLINE_COUNT = 100


def _hooks_enabled() -> bool:
    """读取总开关（默认 true）。"""
    return str(os.environ.get(_ENV_FLAG, "true")).strip().lower() in (
        "1", "true", "yes", "on",
    )


def _period_for(trade_nature: str) -> str:
    """按交易性质选因子计算周期（与 paper 侧同口径：scalp 用 5m，其余 15m）。"""
    return "5m" if str(trade_nature or "").lower() == "scalp" else "15m"


def compute_factor_snapshot(
    symbol: str, trade_nature: str = "",
) -> Optional[Dict[str, float]]:
    """计算当前因子快照。失败返回 None（调用方据此告警，不静默当作空快照）。

    Args:
        symbol: 交易对基础符号（如 "BTC"）。
        trade_nature: 交易性质，决定 K 线周期（scalp → 5m，其余 → 15m）。

    Returns:
        {因子名: 因子值} —— 已剔除 NaN/Inf；无可用数据时返回 None。
    """
    _sym = str(symbol or "").upper()
    if not _sym:
        return None
    try:
        import pandas as pd

        from backend.services.factor_engine import factor_engine as _fe
        from backend.services.kline_data_service import kline_service

        raw = kline_service.get_klines_from_db(
            _sym, _period_for(trade_nature), _KLINE_COUNT,
        )
        if not raw:
            logger.warning(
                "[LiveLearn] %s 无 %s K 线数据，本笔实盘无因子快照",
                _sym, _period_for(trade_nature),
            )
            return None
        vals = _fe.compute_all_factors(pd.DataFrame(raw))
        if not vals:
            return None
        out: Dict[str, float] = {}
        for name, item in vals.items():
            try:
                fv = item.value if hasattr(item, "value") else item
                if fv is None:
                    continue
                fv = float(fv)
            except (TypeError, ValueError):
                continue
            # NaN/Inf 若写进 signal_trade_feedback.signal_value，会在 IC 计算里
            # 让整个因子的样本相关系数变成 NaN（一条坏样本毁掉一个因子的权重）。
            if not math.isfinite(fv):
                continue
            out[str(name)] = fv
        return out or None
    except Exception as exc:
        logger.warning("[LiveLearn] %s 因子快照计算失败: %s", _sym, exc)
        return None


def record_live_entry_snapshot(
    *,
    account_id: int,
    sub_position_id: Any,
    symbol: str,
    position_side: str,
    trade_nature: str = "",
) -> bool:
    """实盘开仓后写因子快照到 ``signal_trade_feedback``（幂等、独立会话）。

    对称于 paper 侧 ``paper_trading_engine._fill_market_order`` 内的快照逻辑。
    ``trade_id`` 取 ``LiveSubPosition.id``，与平仓回填 ``backfill_live_close_pnl``
    使用同一口径，否则回填永远匹配不上。

    Args:
        account_id: 实盘账户 ID。
        sub_position_id: LPM 返回的 ``sub_position_id``。
        symbol: 交易对。
        position_side: 持仓方向（long/short，也接受 buy/sell）。
        trade_nature: 交易性质（scalp/swing/...），决定因子计算周期。

    Returns:
        True 表示本次确实写入了快照；False 表示跳过（开关关闭/幂等命中/
        无因子数据/异常），均已记日志，不抛异常给主链路。
    """
    if not _hooks_enabled():
        return False
    try:
        _tid = int(sub_position_id or 0)
    except (TypeError, ValueError):
        _tid = 0
    if _tid <= 0:
        # 下单未产生子仓位（拒单/零成交），无可归因对象，静默跳过。
        return False

    _sym = str(symbol or "").upper()
    _side = "long" if str(position_side or "").lower() in ("long", "buy") else "short"

    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import SignalTradeFeedback
        from backend.services.signal_feedback_tracker import signal_feedback_tracker

        with SessionLocal() as db:
            _dup = db.query(SignalTradeFeedback.id).filter(
                SignalTradeFeedback.trade_id == _tid,
            ).first()
            if _dup:
                logger.debug(
                    "[LiveLearn] 子仓 %s 已有开仓快照，跳过重复记账（加仓场景）", _tid,
                )
                return False

            factors = compute_factor_snapshot(_sym, trade_nature)
            if not factors:
                logger.warning(
                    "[LiveLearn] %s 因子快照为空，子仓 %s 不进因子闭环", _sym, _tid,
                )
                return False

            # active_signals 传空：情报信号由 paper 侧闭环负责，此处只做因子归因。
            signal_feedback_tracker.record_entry_signals(
                db, int(account_id), _tid, _sym, _side, {}, factor_values=factors,
            )
        logger.info(
            "[LiveLearn] 实盘开仓因子快照已记录: %s %s 子仓=%s 因子数=%d",
            _sym, _side, _tid, len(factors),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[LiveLearn] 实盘开仓快照失败(不影响下单) %s 子仓=%s: %s", _sym, _tid, exc,
        )
        return False


def backfill_live_close_pnl(
    *,
    sub_ids: Iterable[Any],
    pnl: float,
    pnl_pct: float,
) -> int:
    """实盘平仓后把盈亏回填到该子仓位的因子快照行（独立会话）。

    对称于 paper 侧 ``_notify_learning_on_close`` 里的 ``update_trade_pnl``。
    部分平仓也会回填（与 paper 同语义：后一次平仓覆盖前一次），否则只在全平时
    回填，长期持有的剩余仓位会让样本永久停在 pending、永远不进 IC 计算。

    Args:
        sub_ids: 本次平仓涉及的 ``LiveSubPosition.id`` 列表。
        pnl: 已实现盈亏（USD）。
        pnl_pct: ROI 小数（非百分数），须与 paper 侧口径一致。

    Returns:
        实际被回填的快照行数（0 表示无匹配行 —— 通常意味着开仓钩子当时没成功
        写快照，日志会告警）。
    """
    if not _hooks_enabled():
        return 0
    _ids: List[int] = []
    for sid in sub_ids or []:
        try:
            _v = int(sid)
        except (TypeError, ValueError):
            continue
        if _v > 0 and _v not in _ids:
            _ids.append(_v)
    if not _ids:
        return 0

    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import SignalTradeFeedback
        from backend.services.signal_feedback_tracker import signal_feedback_tracker

        _pnl = float(pnl)
        _pct = float(pnl_pct)
        if not (math.isfinite(_pnl) and math.isfinite(_pct)):
            logger.warning(
                "[LiveLearn] 平仓盈亏非有限值(pnl=%s pct=%s)，跳过回填", pnl, pnl_pct,
            )
            return 0

        total = 0
        with SessionLocal() as db:
            for _tid in _ids:
                # 先数行数再更新：update_trade_pnl 不返回影响行数，而"回填了几行"
                # 是判断闭环是否真的通了的唯一可观测信号（0 行=开仓钩子没生效）。
                _n = db.query(SignalTradeFeedback.id).filter(
                    SignalTradeFeedback.trade_id == _tid,
                ).count()
                if _n <= 0:
                    logger.warning(
                        "[LiveLearn] 子仓 %s 无开仓快照，平仓盈亏无处回填", _tid,
                    )
                    continue
                signal_feedback_tracker.update_trade_pnl(db, _tid, _pnl, _pct)
                total += int(_n)
        if total:
            logger.info(
                "[LiveLearn] 实盘平仓盈亏已回填: 子仓=%s 行数=%d pnl=%.4f roi=%.4f",
                _ids, total, _pnl, _pct,
            )
        return total
    except Exception as exc:
        logger.warning("[LiveLearn] 实盘平仓回填失败(不影响平仓) %s: %s", _ids, exc)
        return 0
