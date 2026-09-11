# -*- coding: utf-8 -*-
"""短线做空车道策略（2026-09-02 P2.4）。

为什么需要这个模块
==================
2026-09-02 用 triple-barrier 口径（按信号当时的 TP/SL 复算"谁先被触及"，垂直轨
取实盘短线持仓上限 7200s）回填并结算了近 18 天 5.5 万条信号，结果：

| 方向  | 样本   | 胜率  | 净收益/笔 | 总贡献      |
| ----- | ------ | ----- | --------- | ----------- |
| short | 32,880 | 30.3% | -34.14bp  | -1,122,374bp |
| long  | 22,507 | 55.0% | +12.82bp  |   +288,528bp |

叠加 pwin 门槛后做空仍不成立：

| 组合                | 样本  | 胜率  | 净收益/笔 |
| ------------------- | ----- | ----- | --------- |
| short + pwin>=0.50  | 6,700 | 35.9% | -30.91bp  |
| short + pwin>=0.55  | 2,055 | 42.0% | -14.15bp  |
| short + pwin>=0.60  |   215 | 49.3% |  +5.18bp  |
| long  + pwin>=0.55  | 2,542 | 63.7% | +28.73bp  |

即：做空不是"门槛没调好"，而是在当前因子体系下整体负期望；唯一转正的
pwin>=0.60 档只有 215 条样本、边际 +5.18bp，覆盖不了滑点与执行误差。

设计取舍
--------
1. **默认关闭，而非删除**。闸门放在 `log_signal` 之后，做空信号照常落库、
   triple-barrier 标签继续结算 —— 样本持续积累，等做空自己转正（可用本模块的
   `short_lane_stats()` 复核）再放开。这是"独立实验车道"，不是把做空从学习闭环
   里摘掉。
2. **保留一条可配的实验缝隙**。`SCALP_SHORT_MIN_PWIN` 默认 999（等价全关）；
   想按 pwin>=0.60 小仓试水时设成 0.60 即可，无需改代码。
3. **只拦新开仓**。已有空头持仓的平仓/止损/减仓完全不受影响 —— 闸门位于开仓
   路径上，不参与退出决策。
4. **fail-closed**。调用方在本模块异常时不放行做空（见 scalp_loop）。
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 全关时的哨兵值：任何真实 pwin 都不可能 >= 999
_ALL_CLOSED = 999.0


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def short_lane_enabled() -> bool:
    """做空车道总开关。默认 False —— 依据见模块 docstring 的回溯表。"""
    return _env_bool("SCALP_SHORT_ENABLED", False)


def short_min_pwin() -> float:
    """做空所需的 pwin 下限。默认 999（等价全关）。

    设为 0.60 可开启"高 pwin 小仓实验"：该档回溯 215 条、胜率 49.3%、净 +5.18bp
    —— 样本偏薄，仅供受控试水，不建议直接当生产门槛。
    """
    try:
        v = os.getenv("SCALP_SHORT_MIN_PWIN", "")
        if str(v).strip():
            return float(v)
    except (TypeError, ValueError):
        pass
    return _ALL_CLOSED


def short_min_score() -> float:
    """做空所需的 factor_score 下限（默认 0 = 不额外约束）。

    刻意不设默认门槛：同批回溯显示 factor_score 与净收益**反向**
    （<35 档 -1.80bp，>=60 档 -22.88bp），拿它当做空的准入条件只会挑出更差的样本。
    """
    try:
        return float(os.getenv("SCALP_SHORT_MIN_SCORE", "0") or 0)
    except (TypeError, ValueError):
        return 0.0


def short_open_allowed(
    *,
    pwin: Optional[float] = None,
    factor_score: Optional[float] = None,
) -> Tuple[bool, str]:
    """判定这条做空信号是否允许开新仓。

    Args:
        pwin: 元标签胜率预测；None 表示不可用。
        factor_score: 因子合成分。

    Returns:
        (allowed, reason)。reason 用于日志与阻塞计数，便于面板上看清
        "做空是被闸断的"而不是"没有信号"。
    """
    if not short_lane_enabled():
        return False, "short_lane_off"

    _min_pwin = short_min_pwin()
    if _min_pwin >= _ALL_CLOSED:
        return False, "short_pwin_gate_closed"
    if pwin is None:
        # pwin 缺失时不放行：做空的负期望结论是在有 pwin 的样本上得出的，
        # 无 pwin 等于没有任何质量证据。
        return False, "short_pwin_missing"
    try:
        _p = float(pwin)
    except (TypeError, ValueError):
        return False, "short_pwin_invalid"
    if _p < _min_pwin:
        return False, f"short_pwin_below_min({_p:.3f}<{_min_pwin:.3f})"

    _min_score = short_min_score()
    if _min_score > 0:
        try:
            if float(factor_score or 0) < _min_score:
                return False, "short_score_below_min"
        except (TypeError, ValueError):
            return False, "short_score_invalid"

    return True, f"short_allowed(pwin={_p:.3f})"


def short_lane_stats(days: int = 14) -> dict:
    """复核做空车道近况：用 triple-barrier 标签统计，供放开决策使用。

    之所以用 tb_* 而不是旧的 net_ret：旧标签是"固定 30 分钟收盘收益"，与实盘
    TP/SL 触发口径错配，会把做多的正边际显示成负（实测 long 旧口径 -10.13bp
    vs TB 口径 +12.82bp）。放开做空这种决策必须用与执行一致的标签。
    """
    out: dict = {"days": int(days), "ok": False}
    try:
        import time

        from sqlalchemy import text

        from backend.database.connection import SessionLocal

        since = int(time.time()) - int(days) * 86400
        with SessionLocal() as db:
            for d in ("short", "long"):
                r = db.execute(text("""
                    SELECT count(*) n,
                           avg(CASE WHEN tb_win THEN 1.0 ELSE 0.0 END) wr,
                           avg(tb_net_ret) bp
                    FROM scalp_signal_log
                    WHERE tb_settled = true AND direction = :d
                      AND signal_ts >= :since
                """), {"d": d, "since": since}).fetchone()
                out[d] = {
                    "n": int(r.n or 0),
                    "win_rate": round(float(r.wr or 0) * 100, 2),
                    "net_bp": round(float(r.bp or 0) * 10000, 2),
                }
            out["ok"] = True
    except Exception as e:
        out["error"] = str(e)[:200]
        logger.debug("[ShortLane] 统计失败: %s", e)
    return out
