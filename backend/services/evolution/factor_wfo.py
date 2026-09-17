"""
M5 因子级 Walk-Forward 门禁

对应《短期因子策略全链路详细技术设计.md》§5。
在因子晋升前用 WalkForwardAnalyzer 做样本外滚动验证：
- 门禁：pbo ≤ 0.30 且 overfitting_score ≥ 0.5 且 consistency ≥ 0.6；
- 报告写入 walk_forward_reports 表；
- [2026-08-13 P1-8] 失败/异常默认 fail-closed（对齐 FACTOR_EVO_GATE_FAIL_CLOSED），
  由 FEATURE_WFO_GATE_ENABLED 控制是否启用该门禁。

[v6 阶段 2 S2-5] 新增因子级 IC-WFO（5.4.2）：滚动训练窗 → 测试窗 → 步长，
输出 OOS IC 序列；判据 = OOS IC 均值 + OOS IC 显著性 + 相对训练 IC 衰退率
(<50% 视为稳定)。窗口按周期分档：4h 默认 60/15/7 天（env 可配），
替代原静态单次切分。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FEATURE_WFO_GATE_ENABLED = os.getenv("FEATURE_WFO_GATE_ENABLED", "true").lower() in (
    "1", "true", "yes", "on",
)

# [2026-08-13 P1-8] 数据不足/运行异常时的放行方向：默认 fail-closed（对齐
# FACTOR_EVO_GATE_FAIL_CLOSED）；回滚：FACTOR_EVO_GATE_FAIL_CLOSED=0|false|off。
_WFO_FAIL_CLOSED = (os.getenv("FACTOR_EVO_GATE_FAIL_CLOSED") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# [v6 5.4.2 S2-5] IC-WFO 窗口与判据（env 可配；4h 周期默认 训练60天→测试15天→步长7天）
_WFO_IC_TRAIN_DAYS = int(os.getenv("WFO_IC_TRAIN_DAYS", "60"))
_WFO_IC_TEST_DAYS = int(os.getenv("WFO_IC_TEST_DAYS", "15"))
_WFO_IC_STEP_DAYS = int(os.getenv("WFO_IC_STEP_DAYS", "7"))
_WFO_IC_MIN_OOS_IC = float(os.getenv("WFO_IC_MIN_OOS_IC", "0.01"))
_WFO_IC_MAX_DECAY = float(os.getenv("WFO_IC_MAX_DECAY", "0.50"))  # 衰退率 <50% 视为稳定
_WFO_IC_MIN_WINDOWS = int(os.getenv("WFO_IC_MIN_WINDOWS", "3"))
# [轮46 2026-09-17] 单边 t 检验 p 的显著性阈值。此前**硬编码 0.05**（唯一无开关的判据），
# 实测它是唯一普遍性杀手：181 条"失败币"记录中 100% 都有 p ≥ 0.05，
# 而 IC（62% 达标）与衰退率（56% 达标）都不是主因；
# 41 个晋级候选在 p<0.05 下通过 0 个，与线上连续 7 天 promoted=0 完全吻合。
# 默认 0.05 = 原行为，改动它需显式设置 env（回滚：删除该键或置 0.05）。
_WFO_IC_MAX_P = float(os.getenv("WFO_IC_MAX_P", "0.05"))

# [2026-09-03 审查修正 B] IC-WFO 窗口按周期分档。原 60/15/7 天是 4h 档口径，对
# 5m（进化取数 50 天）/15m（70 天）永远凑不出一个窗 → insufficient_windows:0 →
# fail-closed 全拒（实测 09-03 03:20 BTC 5m 14085 根：windows=0）。分档口径与
# factor_evolution_loop._PERIOD_SPLIT_DAYS 的数据深度匹配；env WFO_IC_*_DAYS
# 显式设置时对所有周期生效（保持旧语义），未设置时按周期取默认。
_WFO_STRAT_WINDOWS_BY_FREQ: Dict[str, tuple] = {
    # 策略级 WFO（WalkForwardAnalyzer）：(train_days, test_days, step_days)
    "1min": (20, 5, 5), "3min": (20, 5, 5), "5min": (20, 5, 5),
    "15min": (20, 5, 5), "30min": (30, 10, 7),
    "1h": (30, 10, 7), "2h": (60, 15, 15), "4h": (60, 15, 15), "8h": (60, 15, 15), "1d": (60, 15, 15),
}
_WFO_IC_WINDOWS_BY_FREQ: Dict[str, tuple] = {
    # freq: (train_days, test_days, step_days)。step == test：测试窗**不重叠**，
    # 每根 OOS K 线只参与一次评估，窗间 IC 才近似独立、单边 t 检验才成立
    # （原 60/15/7 相邻测试窗重叠 8/15 根，47 个"窗"有效自由度不到一半）。
    "1min": (20, 5, 5), "3min": (20, 5, 5), "5min": (20, 5, 5),
    "15min": (30, 10, 10), "30min": (45, 15, 15),
    "1h": (60, 15, 15), "2h": (60, 15, 15), "4h": (60, 15, 15), "8h": (60, 15, 15), "1d": (60, 15, 15),
}
# 单窗 OOS 至少要有这么多个点：短窗内的样本相关系数对"滞后收益均值"类因子有
# ≈ -k/n 的小样本负偏（i.i.d. 噪声上 15 点窗实测 |IC|≈0.2、p≈0），60 点把偏差
# 压到 ~0.02 以内。测试窗按天算出来不足时按根数抬高（步长同步）。
_WFO_IC_MIN_TEST_BARS = int(os.getenv("WFO_IC_MIN_TEST_BARS", "60"))


def _wfo_ic_windows(freq: str, total_bars: int, bpd: float) -> tuple:
    """返回 (train_bars, test_bars, step_bars)：env 覆盖 > 周期分档 > 按数据跨度自适应收缩。

    自适应：若按分档窗口连 ``_WFO_IC_MIN_WINDOWS`` 个窗都凑不出，把训练/测试窗
    等比例缩到"刚好能出 MIN_WINDOWS 个窗"的尺度（训练窗不低于 5 天、测试窗不低于
    2 天）；再不够则如实返回、由调用方按 insufficient_windows fail-closed。
    宁可窗短、也不能没有滚动 OOS 检验。
    """
    env_set = any(os.getenv(k) for k in ("WFO_IC_TRAIN_DAYS", "WFO_IC_TEST_DAYS", "WFO_IC_STEP_DAYS"))
    if env_set:
        td, sd, pd_ = _WFO_IC_TRAIN_DAYS, _WFO_IC_TEST_DAYS, _WFO_IC_STEP_DAYS
    else:
        td, sd, pd_ = _WFO_IC_WINDOWS_BY_FREQ.get((freq or "").strip().lower(), (60, 15, 7))
    train_bars = int(td * bpd)
    test_bars = int(sd * bpd)
    step_bars = max(1, int(pd_ * bpd))
    # 单窗 OOS 点数下限（低频周期 15 天只有 15 根日线 → 抬到 60 根；训练窗同比例
    # 至少 2 倍测试窗；步长不小于测试窗，保持不重叠）
    if test_bars < _WFO_IC_MIN_TEST_BARS:
        test_bars = _WFO_IC_MIN_TEST_BARS
        train_bars = max(train_bars, 2 * test_bars)
    step_bars = max(step_bars, test_bars) if not env_set else step_bars
    need = train_bars + test_bars + step_bars * max(0, _WFO_IC_MIN_WINDOWS - 1)
    if total_bars >= need or env_set:
        return train_bars, test_bars, step_bars
    # 自适应收缩：保持 train:test 比例、step=test，使 need == total_bars；
    # 测试窗不低于 _WFO_IC_MIN_TEST_BARS（低于它宁可窗少也不出偏 IC）
    scale = total_bars / float(max(need, 1))
    test2 = max(_WFO_IC_MIN_TEST_BARS, int(test_bars * scale))
    train2 = max(int(5 * bpd), int(train_bars * scale))
    logger.info(
        "[FactorWFO-IC] %s 数据 %d 根不足以按 train=%d/test=%d/step=%d 出 %d 个窗，收缩为 %d/%d/%d",
        freq, total_bars, train_bars, test_bars, step_bars, _WFO_IC_MIN_WINDOWS,
        train2, test2, test2,
    )
    return train2, test2, test2

# [2026-08-13 P1-5] IC 前瞻期按周期分档（对齐 scalp ATR 持仓节奏；与
# factor_evolution_loop._PERIOD_FWD_BARS 同口径），未知周期回退 env/5。
_WFO_IC_FWD_BARS: Dict[str, int] = {
    "1min": 12, "3min": 12, "5min": 12, "15min": 6, "30min": 4,
    "1h": 2, "2h": 1, "4h": 1, "8h": 1, "1d": 1,
}


def _wfo_ic_fwd_bars(freq: str) -> int:
    """周期 → 标签前瞻 K 线根数（IC-WFO 用，与进化链 _PERIOD_FWD_BARS 同口径）。"""
    key = (freq or "").strip().lower()
    if key in _WFO_IC_FWD_BARS:
        return _WFO_IC_FWD_BARS[key]
    try:
        return int(os.getenv("WFO_IC_FWD_BARS", "5") or 5)
    except (TypeError, ValueError):
        return 5


def _strategy_gate_binding() -> bool:
    """策略级 WFO 是否作为硬门（默认否，见 run_factor_wfo 内注释）。"""
    return (os.getenv("FACTOR_EVO_WFO_STRATEGY_GATE", "0") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _ensure_reports_table() -> None:
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from sqlalchemy import text as _sa_text
        with AnalyticsSessionLocal() as db:
            db.execute(_sa_text(
                "CREATE TABLE IF NOT EXISTS walk_forward_reports ("
                " id BIGSERIAL PRIMARY KEY,"
                " subject_type VARCHAR(16) NOT NULL,"
                " subject_id VARCHAR(64) NOT NULL,"
                " run_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                " pbo DOUBLE PRECISION,"
                " dsr DOUBLE PRECISION,"
                " consistency DOUBLE PRECISION,"
                " overfitting_score DOUBLE PRECISION,"
                " n_periods INT,"
                " params_json JSONB,"
                " passed BOOLEAN,"
                " meta_json JSONB DEFAULT '{}'::jsonb)"
            ))
            db.execute(_sa_text(
                "CREATE INDEX IF NOT EXISTS idx_wfr_subject "
                "ON walk_forward_reports(subject_type, subject_id, run_at DESC)"
            ))
            db.commit()
    except Exception:
        pass


def _report_consistency_and_periods(report: Any) -> tuple[float, int]:
    """从 WalkForwardResult 取 (一致性, 有效期数)。

    [2026-09-03 审查修正 B · 根因] WalkForwardResult 的字段叫 ``consistency_score``
    与 ``periods``（列表），本模块自 M5 起一直读 ``consistency`` / ``n_periods``
    ——两个不存在的属性，getattr 默认值 0 → ``consistency>=0.6`` 与 ``n_periods>=3``
    永远不成立 → **策略级 WFO 门禁从写出来那天起就不可能通过**。08-27 观察到
    "总报 n_periods=0/consistency=0" 后把门禁整体关掉，把症状当成了数据不足。
    这里做字段兼容：新旧两套名字都认。
    """
    try:
        c = getattr(report, "consistency_score", None)
        if c is None:
            c = getattr(report, "consistency", 0.0)
        consistency = float(c or 0.0)
    except Exception:
        consistency = 0.0
    try:
        n = getattr(report, "n_periods", None)
        if n is None:
            _ps = getattr(report, "periods", None) or []
            n = len([p for p in _ps if getattr(p, "test_result", None) is not None])
        n_periods = int(n or 0)
    except Exception:
        n_periods = 0
    return consistency, n_periods


def _persist_report(
    subject_type: str,
    subject_id: str,
    report: Any,
    passed: bool,
) -> None:
    try:
        _ensure_reports_table()
        from backend.database.connection import AnalyticsSessionLocal
        from sqlalchemy import text as _sa_text
        _c, _n = _report_consistency_and_periods(report)
        with AnalyticsSessionLocal() as db:
            db.execute(_sa_text(
                "INSERT INTO walk_forward_reports "
                "(subject_type, subject_id, pbo, dsr, consistency, overfitting_score, "
                " n_periods, params_json, passed, meta_json) "
                "VALUES (:t, :id, :pbo, :dsr, :c, :o, :n, :pj, :passed, :mj)"
            ), {
                "t": subject_type, "id": subject_id,
                "pbo": float(getattr(report, "pbo", 0) or 0),
                "dsr": float(getattr(report, "deflated_sharpe", None)
                             or getattr(report, "dsr", 0) or 0),
                "c": _c,
                "o": float(getattr(report, "overfitting_score", 0) or 0),
                "n": _n,
                "pj": "{}",
                "passed": passed,
                "mj": "{}",
            })
            db.commit()
    except Exception as exc:
        logger.debug("[FactorWFO] 报告落库失败: %s", exc)


class _FactorStrategy:
    """极简因子策略：z-score 上穿 entry_z 开多，下穿 -entry_z 开空，反向/exit_z 平仓。"""

    def __init__(self, expr, entry_z: float = 1.0, exit_z: float = 0.5):
        self.expr = expr
        self.entry_z = float(entry_z)
        self.exit_z = float(exit_z)

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        try:
            from backend.services.alpha.factor_compute import kline_df_to_fields
            fields = kline_df_to_fields(data)
            vals = pd.Series(self.expr.evaluate(fields))
            z = (vals - vals.rolling(30, min_periods=10).mean()) / vals.rolling(
                30, min_periods=10
            ).std().replace(0, float("nan"))
        except Exception:
            return pd.Series(0, index=data.index)
        pos = 0
        sig = []
        for i, v in enumerate(z):
            if pd.isna(v):
                sig.append(pos)
                continue
            if pos == 0 and v >= self.entry_z:
                pos = 1
            elif pos == 0 and v <= -self.entry_z:
                pos = -1
            elif pos == 1 and v <= self.exit_z:
                pos = 0
            elif pos == -1 and v >= -self.exit_z:
                pos = 0
            sig.append(pos)
        return pd.Series(sig, index=data.index)


def run_factor_wfo(
    expr,
    df: pd.DataFrame,
    factor_id: str,
    freq: str = "5min",
) -> Dict[str, Any]:
    """对因子跑 WFO 并落库；返回 {passed, report, error}。"""
    if not FEATURE_WFO_GATE_ENABLED:
        return {"passed": True, "report": None, "skipped": True}
    # advisory 模式下数据不足/异常同样不阻断（约束由 IC-WFO 承担）
    _fail_verdict = (not _WFO_FAIL_CLOSED) or (not _strategy_gate_binding())
    if df is None or len(df) < 500:
        return {
            "passed": _fail_verdict, "report": None, "skipped": True,
            "reason": "insufficient_data", "binding": _strategy_gate_binding(),
        }
    try:
        # WFO 需要 DatetimeIndex（train_start + timedelta）
        if not isinstance(df.index, pd.DatetimeIndex):
            df = df.copy()
            df.index = pd.date_range(
                end=pd.Timestamp.now(tz="UTC"),
                periods=len(df),
                freq=freq,
            )
        from backend.services.backtest_engine.walk_forward import (
            WalkForwardAnalyzer,
            WalkForwardConfig,
        )
        # [2026-09-03 审查修正 B] 策略级 WFO 窗口按周期分档（env 显式设置优先）：
        # 原 20/5/5 天对 4h 档 390 天数据要滚 70+ 期×网格×CSCV，慢且每期只有
        # 120 根 K 线；5m/15m 保持 20/5/5。
        _s_td, _s_sd, _s_pd = _WFO_STRAT_WINDOWS_BY_FREQ.get(
            (freq or "").strip().lower(), (20, 5, 5))
        cfg = WalkForwardConfig(
            train_period_days=int(os.getenv("WFO_TRAIN_DAYS", str(_s_td))),
            test_period_days=int(os.getenv("WFO_TEST_DAYS", str(_s_sd))),
            step_days=int(os.getenv("WFO_STEP_DAYS", str(_s_pd))),
            purge_days=int(os.getenv("WFO_PURGE_DAYS", "2")),
            embargo_days=int(os.getenv("WFO_EMBARGO_DAYS", "1")),
            optimizer="grid",
            run_cscv=True,
            cscv_n_blocks=8,
        )
        analyzer = WalkForwardAnalyzer(cfg)
        report = analyzer.analyze(
            strategy_factory=lambda params: _FactorStrategy(
                expr,
                entry_z=float(params.get("entry_z", 1.0)),
                exit_z=float(params.get("exit_z", 0.5)),
            ),
            data=df,
            param_grid={"entry_z": [0.8, 1.0, 1.5], "exit_z": [0.0, 0.5]},
        )
        pbo = float(getattr(report, "pbo", 0.5) or 0.5)
        overfit = float(getattr(report, "overfitting_score", 0.0) or 0.0)
        consistency, n_periods = _report_consistency_and_periods(report)
        passed = (
            pbo <= 0.30
            and overfit >= 0.5
            and consistency >= 0.6
            and n_periods >= 3
        )
        _persist_report("factor", factor_id, report, passed)
        logger.info(
            "[FactorWFO] %s passed=%s pbo=%.3f overfit=%.2f consistency=%.2f n=%d%s",
            factor_id, passed, pbo, overfit, consistency, n_periods,
            "" if _strategy_gate_binding() else " (advisory：不阻断晋升，IC-WFO 为约束门)",
        )
        out = {
            "passed": passed, "report": report, "skipped": False,
            "strategy_passed": passed, "binding": _strategy_gate_binding(),
            "pbo": round(pbo, 4), "consistency": round(consistency, 4), "n_periods": n_periods,
        }
        if not _strategy_gate_binding():
            # [2026-09-03 审查修正 B] 策略级 WFO 只作参考：它评估的是"z>1 开仓、
            # 回到 0.5 平仓"这条与线上加权融合无关的固定规则，其 PBO 反映的是
            # 这条规则 6 个阈值组合的稳定性，不是因子的预测力（实测 4h 档
            # IC-WFO 47 窗 OOS IC 0.14/p≈0 的因子，策略级 pbo=0.74 照样拒）。
            # 滚动 OOS 约束由 IC-WFO 承担；FACTOR_EVO_WFO_STRATEGY_GATE=1 可恢复硬门。
            out["passed"] = True
            out["advisory"] = True
        return out
    except Exception as exc:
        logger.warning(
            "[FactorWFO] %s 运行异常(%s): %s",
            factor_id,
            ("advisory" if not _strategy_gate_binding()
             else ("fail-closed" if _WFO_FAIL_CLOSED else "fail-open")),
            str(exc)[:150],
        )
        return {
            "passed": _fail_verdict, "report": None, "skipped": True,
            "error": str(exc)[:150], "binding": _strategy_gate_binding(),
        }


# ═══════════════════════════════════════════════════════════
#  [v6 阶段 2 S2-5] 因子级 IC-WFO：滚动训练窗 OOS IC 序列（5.4.2）
# ═══════════════════════════════════════════════════════════

def _freq_to_bars_per_day(freq: str) -> Optional[float]:
    """周期字符串 → 每日K线根数（'4h'→6, '5min'→288, '1d'→1）。解析失败返回 None。"""
    freq = (freq or "").strip().lower()
    m = re.fullmatch(r"(\d+)(min|h|d)", freq)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "min":
        return 24 * 60 / n
    if unit == "h":
        return 24 / n
    return 1 / n


def _forward_returns(close: np.ndarray, horizon: int) -> np.ndarray:
    # 尾部 horizon 根无未来收益 → NaN（而非 0），避免伪 0 收益扭曲 IC
    fwd = np.full(len(close), np.nan)
    if len(close) > horizon:
        fwd[:-horizon] = close[horizon:] / close[:-horizon] - 1.0
    return fwd


def _persist_ic_report(subject_id: str, result: Dict[str, Any]) -> None:
    """IC-WFO 结果落库 walk_forward_reports（subject_type='factor_ic'）。"""
    try:
        _ensure_reports_table()
        from backend.database.connection import AnalyticsSessionLocal
        from sqlalchemy import text as _sa_text
        meta = {
            "oos_ic_mean": result.get("oos_ic_mean"),
            "oos_ic_std": result.get("oos_ic_std"),
            "oos_ic_p": result.get("oos_ic_p"),
            "decay_rate": result.get("decay_rate"),
            "oos_ic_series": result.get("oos_ic_series"),
            "train_ic_series": result.get("train_ic_series"),
        }
        with AnalyticsSessionLocal() as db:
            db.execute(_sa_text(
                "INSERT INTO walk_forward_reports "
                "(subject_type, subject_id, consistency, overfitting_score, "
                " n_periods, params_json, passed, meta_json) "
                "VALUES ('factor_ic', :id, :c, :o, :n, :pj, :passed, :mj)"
            ), {
                "id": subject_id,
                "c": float(result.get("decay_rate", 0) or 0),
                "o": float(result.get("oos_ic_mean", 0) or 0),
                "n": int(result.get("n_windows", 0) or 0),
                "pj": json.dumps({"freq": result.get("freq")},
                                 ensure_ascii=False),
                "passed": bool(result.get("passed", False)),
                "mj": json.dumps(meta, ensure_ascii=False),
            })
            db.commit()
    except Exception as exc:
        logger.debug("[FactorWFO-IC] 落库失败: %s", exc)


def run_factor_wfo_ic(
    expr,
    df: pd.DataFrame,
    factor_id: str,
    freq: str = "4h",
) -> Dict[str, Any]:
    """
    滚动训练窗 OOS IC 序列 WFO（v6 5.4.2，替代静态单次切分）。

    从数据尾部向前逐窗滚动（步长 step 天）：
        [训练窗 train 天] | [测试窗 test 天]  ← 当前游标
    每窗：训练段算 train_IC（方向基准），测试段算 OOS IC。

    判据（全配置化）：
        - OOS IC 均值 ≥ WFO_IC_MIN_OOS_IC（默认 0.01）
        - OOS IC 单边 t 检验 p < WFO_IC_MAX_P（默认 0.05；[轮46] 此前为硬编码 0.05）
        - 相对训练 IC 衰退率 < 50%（WFO_IC_MAX_DECAY，|train|−|oos| 相对 |train|）

    返回 dict：{passed, skipped, oos_ic_series, train_ic_series, oos_ic_mean,
                 oos_ic_std, oos_ic_p, decay_rate, n_windows, error?}；
    异常/窗口不足默认 fail-closed（skipped=True, passed=False；
    对齐 FACTOR_EVO_GATE_FAIL_CLOSED，回滚 = 0|false|off）。
    """
    if df is None or len(df) < 200:
        return {"passed": not _WFO_FAIL_CLOSED, "skipped": True, "reason": "insufficient_data"}
    bpd = _freq_to_bars_per_day(freq)
    if not bpd or bpd <= 0:
        return {"passed": not _WFO_FAIL_CLOSED, "skipped": True, "reason": f"unknown_freq:{freq}"}
    try:
        from backend.services.alpha.factor_compute import kline_df_to_fields
        from backend.services.factor_engine.evaluation import (
            ic_significance,
            information_coefficient,
        )

        train_bars, test_bars, step_bars = _wfo_ic_windows(freq, len(df), bpd)
        if train_bars <= 0 or test_bars <= 0:
            return {"passed": not _WFO_FAIL_CLOSED, "skipped": True, "reason": "invalid_window"}

        # [2026-08-13 P1-5] 标签前瞻期按周期分档（原 horizon=5 全局，1h 周期即 5 小时前瞻）
        _fwd = _wfo_ic_fwd_bars(freq)
        total = len(df)
        windows = []
        # [2026-09-03 审查修正 B] 因子值在全序列上**一次**求值，再按窗切片。
        # 原实现每窗单独 evaluate(test_df)：滚动类因子（mean/std 50 根等）的
        # 预热期在测试窗内部重新开始 → 4h 档 15 天测试窗 90 根里 49 根是 NaN，
        # 只剩 41 个点算 OOS IC，噪声 |IC|≈0.16 淹没一切（实测 47 窗 decay 全部
        # 钉在 -1：OOS "远好于" 训练，纯属小样本方差）。因子表达式是因果的
        # （加载器拒绝前视算子），全序列求值后切片不引入任何未来信息；训练窗
        # 尾部 _fwd 根的标签跨越训练/测试边界，按 purge 从训练 IC 中剔除。
        _vals_all: Optional[np.ndarray] = None
        _fwd_all: Optional[np.ndarray] = None
        try:
            _va = np.asarray(expr.evaluate(kline_df_to_fields(df)), dtype=float).ravel()
            if len(_va) == total:
                _vals_all = _va
                _fwd_all = _forward_returns(df["close"].values.astype(float), horizon=_fwd)
        except Exception:
            _vals_all = None
        end = total
        while end - train_bars - test_bars >= 0:
            tr_lo, tr_hi = end - train_bars - test_bars, end - test_bars
            te_lo, te_hi = end - test_bars, end
            try:
                if _vals_all is not None and _fwd_all is not None:
                    _tr_hi_purged = max(tr_lo + 5, tr_hi - _fwd)  # purge：训练尾部标签跨界的 _fwd 根
                    train_ic = information_coefficient(
                        _vals_all[tr_lo:_tr_hi_purged], _fwd_all[tr_lo:_tr_hi_purged])
                    oos_ic = information_coefficient(
                        _vals_all[te_lo:te_hi], _fwd_all[te_lo:te_hi])
                else:
                    # 回退：表达式返回长度异常时沿用逐窗求值（旧路径）
                    train_df = df.iloc[tr_lo:tr_hi]
                    test_df = df.iloc[te_lo:te_hi]
                    train_vals = expr.evaluate(kline_df_to_fields(train_df))
                    test_vals = expr.evaluate(kline_df_to_fields(test_df))
                    tr_close = train_df["close"].values.astype(float)
                    te_close = test_df["close"].values.astype(float)
                    train_ic = information_coefficient(
                        train_vals, _forward_returns(tr_close, horizon=_fwd))
                    oos_ic = information_coefficient(
                        test_vals, _forward_returns(te_close, horizon=_fwd))
                if np.isfinite(train_ic) and np.isfinite(oos_ic):
                    windows.append({
                        "train_ic": float(train_ic),
                        "oos_ic": float(oos_ic),
                        "end_bars": int(end),
                    })
            except Exception:
                pass  # 单窗失败跳过，不终止滚动
            end -= step_bars
            if end <= 0:
                break

        if len(windows) < _WFO_IC_MIN_WINDOWS:
            return {
                "passed": not _WFO_FAIL_CLOSED, "skipped": True,
                "reason": f"insufficient_windows:{len(windows)}",
                "n_windows": len(windows),
            }

        oos_ics = np.array([w["oos_ic"] for w in windows])
        train_ics = np.array([w["train_ic"] for w in windows])
        # [2026-09-03 审查修正 B] 方向由训练段决定、测试段验证：用全部训练窗 IC 均值
        # 的符号做**全局**定向（与晋升时锁定 expected_sign、线上按固定符号使用的
        # 口径一致），再算定向后的 OOS IC 均值/单边 p。原实现要求原始 OOS IC ≥ +0.01，
        # 反转类因子（负 IC、线上按反向使用）会被系统性拒绝。
        _orient = 1.0 if float(np.mean(train_ics)) >= 0 else -1.0
        oos_ics = _orient * oos_ics
        train_ics = _orient * train_ics
        oos_mean = float(np.mean(oos_ics))
        oos_std = float(np.std(oos_ics))
        oos_p = float(ic_significance(oos_ics))
        # 衰退率 = (train − oos) / train，两边都是**定向后的均值**。
        # [2026-09-03 审查修正 B] 原用 mean(|IC|) 对比：测试窗（90 根）比训练窗
        # （360 根）短 4 倍，|IC| 的抽样噪声大一倍，mean(|oos|) 被系统性抬高 →
        # decay 恒为 -1（"OOS 远好于训练"），这个判据形同虚设。定向均值不受
        # 该偏差影响：噪声在均值里正负相抵。
        train_abs = float(np.mean(train_ics))
        oos_abs = float(np.mean(oos_ics))
        decay_rate = float(np.clip(1.0 - oos_abs / train_abs, -1.0, 1.0)) \
            if train_abs > 1e-9 else (0.0 if oos_abs > 0 else 1.0)

        passed = (
            oos_mean >= _WFO_IC_MIN_OOS_IC
            and oos_p < _WFO_IC_MAX_P
            and decay_rate < _WFO_IC_MAX_DECAY
        )
        result = {
            "passed": passed,
            "skipped": False,
            "freq": freq,
            "orientation": int(_orient),
            "train_bars": int(train_bars),
            "test_bars": int(test_bars),
            "oos_ic_series": [round(float(v), 6) for v in oos_ics],
            "train_ic_series": [round(float(v), 6) for v in train_ics],
            "oos_ic_mean": round(oos_mean, 6),
            "oos_ic_std": round(oos_std, 6),
            "oos_ic_p": round(oos_p, 6),
            "decay_rate": round(decay_rate, 6),
            "n_windows": len(windows),
        }
        _persist_ic_report(factor_id, result)
        logger.info(
            "[FactorWFO-IC] %s passed=%s oos_ic=%.4f p=%.3f decay=%.2f n=%d",
            factor_id, passed, oos_mean, oos_p, decay_rate, len(windows),
        )
        return result
    except Exception as exc:
        logger.warning(
            "[FactorWFO-IC] %s 异常(%s): %s",
            factor_id, "fail-closed" if _WFO_FAIL_CLOSED else "fail-open",
            str(exc)[:150],
        )
        return {"passed": not _WFO_FAIL_CLOSED, "skipped": True, "error": str(exc)[:150]}

