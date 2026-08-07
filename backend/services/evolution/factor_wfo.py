"""
M5 因子级 Walk-Forward 门禁

对应《短期因子策略全链路详细技术设计.md》§5。
在因子晋升前用 WalkForwardAnalyzer 做样本外滚动验证：
- 门禁：pbo ≤ 0.30 且 overfitting_score ≥ 0.5 且 consistency ≥ 0.6；
- 报告写入 walk_forward_reports 表；
- 失败/异常时 fail-open（记录但不阻断晋升），由 FEATURE_WFO_GATE_ENABLED 控制。

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

# [v6 5.4.2 S2-5] IC-WFO 窗口与判据（env 可配；4h 周期默认 训练60天→测试15天→步长7天）
_WFO_IC_TRAIN_DAYS = int(os.getenv("WFO_IC_TRAIN_DAYS", "60"))
_WFO_IC_TEST_DAYS = int(os.getenv("WFO_IC_TEST_DAYS", "15"))
_WFO_IC_STEP_DAYS = int(os.getenv("WFO_IC_STEP_DAYS", "7"))
_WFO_IC_MIN_OOS_IC = float(os.getenv("WFO_IC_MIN_OOS_IC", "0.01"))
_WFO_IC_MAX_DECAY = float(os.getenv("WFO_IC_MAX_DECAY", "0.50"))  # 衰退率 <50% 视为稳定
_WFO_IC_MIN_WINDOWS = int(os.getenv("WFO_IC_MIN_WINDOWS", "3"))


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
        with AnalyticsSessionLocal() as db:
            db.execute(_sa_text(
                "INSERT INTO walk_forward_reports "
                "(subject_type, subject_id, pbo, dsr, consistency, overfitting_score, "
                " n_periods, params_json, passed, meta_json) "
                "VALUES (:t, :id, :pbo, :dsr, :c, :o, :n, :pj, :passed, :mj)"
            ), {
                "t": subject_type, "id": subject_id,
                "pbo": float(getattr(report, "pbo", 0) or 0),
                "dsr": float(getattr(report, "dsr", 0) or 0),
                "c": float(getattr(report, "consistency", 0) or 0),
                "o": float(getattr(report, "overfitting_score", 0) or 0),
                "n": int(getattr(report, "n_periods", 0) or 0),
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
    if df is None or len(df) < 500:
        return {"passed": True, "report": None, "skipped": True, "reason": "insufficient_data"}
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
        cfg = WalkForwardConfig(
            train_period_days=int(os.getenv("WFO_TRAIN_DAYS", "20")),
            test_period_days=int(os.getenv("WFO_TEST_DAYS", "5")),
            step_days=int(os.getenv("WFO_STEP_DAYS", "5")),
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
        consistency = float(getattr(report, "consistency", 0.0) or 0.0)
        n_periods = int(getattr(report, "n_periods", 0) or 0)
        passed = (
            pbo <= 0.30
            and overfit >= 0.5
            and consistency >= 0.6
            and n_periods >= 3
        )
        _persist_report("factor", factor_id, report, passed)
        logger.info(
            "[FactorWFO] %s passed=%s pbo=%.3f overfit=%.2f consistency=%.2f n=%d",
            factor_id, passed, pbo, overfit, consistency, n_periods,
        )
        return {"passed": passed, "report": report, "skipped": False}
    except Exception as exc:
        logger.warning("[FactorWFO] %s 运行异常(fail-open): %s", factor_id, str(exc)[:150])
        return {"passed": True, "report": None, "skipped": True, "error": str(exc)[:150]}


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
        - OOS IC 单边 t 检验 p < 0.05（显著性）
        - 相对训练 IC 衰退率 < 50%（WFO_IC_MAX_DECAY，|train|−|oos| 相对 |train|）

    返回 dict：{passed, skipped, oos_ic_series, train_ic_series, oos_ic_mean,
                 oos_ic_std, oos_ic_p, decay_rate, n_windows, error?}；
    异常/窗口不足 fail-open（skipped=True, passed=True）。
    """
    if df is None or len(df) < 200:
        return {"passed": True, "skipped": True, "reason": "insufficient_data"}
    bpd = _freq_to_bars_per_day(freq)
    if not bpd or bpd <= 0:
        return {"passed": True, "skipped": True, "reason": f"unknown_freq:{freq}"}
    try:
        from backend.services.alpha.factor_compute import kline_df_to_fields
        from backend.services.factor_engine.evaluation import (
            ic_significance,
            information_coefficient,
        )

        train_bars = int(_WFO_IC_TRAIN_DAYS * bpd)
        test_bars = int(_WFO_IC_TEST_DAYS * bpd)
        step_bars = max(1, int(_WFO_IC_STEP_DAYS * bpd))
        if train_bars <= 0 or test_bars <= 0:
            return {"passed": True, "skipped": True, "reason": "invalid_window"}

        total = len(df)
        windows = []
        end = total
        while end - train_bars - test_bars >= 0:
            train_df = df.iloc[end - train_bars - test_bars: end - test_bars]
            test_df = df.iloc[end - test_bars: end]
            try:
                train_vals = expr.evaluate(kline_df_to_fields(train_df))
                test_vals = expr.evaluate(kline_df_to_fields(test_df))
                tr_close = train_df["close"].values.astype(float)
                te_close = test_df["close"].values.astype(float)
                train_ic = information_coefficient(
                    train_vals, _forward_returns(tr_close, horizon=5))
                oos_ic = information_coefficient(
                    test_vals, _forward_returns(te_close, horizon=5))
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
                "passed": True, "skipped": True,
                "reason": f"insufficient_windows:{len(windows)}",
                "n_windows": len(windows),
            }

        oos_ics = np.array([w["oos_ic"] for w in windows])
        train_ics = np.array([w["train_ic"] for w in windows])
        oos_mean = float(np.mean(oos_ics))
        oos_std = float(np.std(oos_ics))
        oos_p = float(ic_significance(oos_ics))
        # 衰退率 = (|train| − |oos|) / |train|；训练段均值绝对IC为基准
        train_abs = float(np.mean(np.abs(train_ics)))
        oos_abs = float(np.mean(np.abs(oos_ics)))
        decay_rate = float(np.clip(1.0 - oos_abs / train_abs, -1.0, 1.0)) \
            if train_abs > 1e-9 else 0.0

        passed = (
            oos_mean >= _WFO_IC_MIN_OOS_IC
            and oos_p < 0.05
            and decay_rate < _WFO_IC_MAX_DECAY
        )
        result = {
            "passed": passed,
            "skipped": False,
            "freq": freq,
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
        logger.warning("[FactorWFO-IC] %s 异常(fail-open): %s", factor_id, str(exc)[:150])
        return {"passed": True, "skipped": True, "error": str(exc)[:150]}

