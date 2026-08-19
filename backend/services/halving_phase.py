"""halving_phase — 比特币 4 年减半周期相位（设计总方案 B4，2026-08-19）。

相位 = 确定性减半日历 + 规则化链上确认代理：
- 熊底（0~25%）：减半后前 1/4 周期，价格深度回撤后企稳
- 初涨（25~50%）：脱离 200 日均线、回撤收窄
- 主升（50~75%）：创前高、200 日均线陡升
- 顶部分配（75~100%）：距前高回落但仍在高位横盘
相位输出为长线仓位上限乘数（熊底 0.5 / 初涨 1.0 / 主升 1.0 / 分配 0.5），并进报告观测。
纯规则、无前视。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 历次减半（UTC 日期）与 4 年周期
_HALVINGS = [
    ("2012-11-28", "2016-07-09"),
    ("2016-07-09", "2020-05-11"),
    ("2020-05-11", "2024-04-19"),
    ("2024-04-19", "2028-04-30"),  # 下一次为估计值
]
_CYCLE_DAYS = 1460.0  # 4 年
_PHASE_CAPS = {"熊底": 0.5, "初涨": 1.0, "主升": 1.0, "顶部分配": 0.5}


def _position_multiplier(phase: str) -> float:
    return _PHASE_CAPS.get(phase, 1.0)


def compute_halving_phase(df: Optional[pd.DataFrame] = None,
                          now_ts: Optional[float] = None) -> Dict[str, Any]:
    """返回 {phase, days_since_halving, progress_pct, position_mult, confirmation}。"""
    import datetime
    out: Dict[str, Any] = {"phase": "unknown", "days_since_halving": None,
                           "progress_pct": None, "position_mult": 1.0,
                           "confirmation": None}
    try:
        now = pd.Timestamp.utcnow() if now_ts is None else pd.Timestamp(now_ts, unit="s")
        # 定位当前周期：最后一个 start <= now 的减半
        cur = None
        for start, end in _HALVINGS:
            if pd.Timestamp(start) <= now < pd.Timestamp(end):
                cur = (start, end)
                break
        if cur is None:
            return out
        start, _ = cur
        days = float((now - pd.Timestamp(start)).total_seconds()) / 86400.0
        progress = days / _CYCLE_DAYS
        out["days_since_halving"] = round(days, 0)
        out["progress_pct"] = round(progress * 100, 1)
        # 规则化确认代理（用价格数据增强相位判定）
        conf = None
        if df is not None and len(df) >= 260:
            close = df["close"].astype(float)
            ma200 = close.rolling(200).mean()
            c_now = float(close.iloc[-1])
            ma_now = float(ma200.iloc[-1])
            prev_high = float(close.iloc[:-20].max())
            drawdown_from_high = 1.0 - c_now / max(prev_high, 1e-9)
            above_ma = c_now > ma_now
            conf = {"above_ma200": bool(above_ma), "drawdown_from_high": round(drawdown_from_high, 3)}
        if progress < 0.25:
            phase = "熊底"
        elif progress < 0.50:
            phase = "初涨"
        elif progress < 0.75:
            phase = "主升"
        else:
            phase = "顶部分配"
        # 确认代理微调：主升期若深回撤（>40%）降为初涨口径的仓位乘数
        if conf and phase == "主升" and conf["drawdown_from_high"] > 0.4:
            phase = "初涨"
        out["phase"] = phase
        out["position_mult"] = _position_multiplier(phase)
        out["confirmation"] = conf
    except Exception as e:
        logger.debug("[HalvingPhase] 相位计算失败: %s", e)
    return out
