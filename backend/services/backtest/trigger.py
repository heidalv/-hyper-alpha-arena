"""
事件驱动重回测触发器 — BacktestEventTrigger（P1，规划文档§4.1）。

背景（规划文档已核实）：v2.0文档提到的 `services/backtest/trigger.py` 和
`AlphaBus` 事件总线接线都不存在，本文件从零搭建，且不依赖那个孤立、topic
不匹配的 AlphaBus——直接挂在两个真实存在的调用点之后触发：

    1. factor_backtest_scorer.validate_and_promote() 判定 admitted=True 时
       （因子从候选晋升为可用）→ on_factor_promoted
    2. factor_evolution_loop.run_online_weight_update() 里某因子权重变化
       超过20% → on_weight_delta_exceeds_threshold

消费方复用现有 `factor_jobs.factor_job_manager`（进程内单worker任务队列，
本来就是为"重活后台化+带进度"设计的），而不是重新发明一套队列。

目标：新因子晋升/权重巨变后 5 分钟内产出针对性回测报告，不用等次日3点的
每日调度——回测本身很快（单因子样本外打分是秒级/分钟级），真正的价值是
"不用等一整天"。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TriggerType(str, Enum):
    NEW_FACTOR = "new_factor"
    FACTOR_WEIGHT_UPDATE = "weight_update"
    PARAM_CHANGE = "param_change"
    REGIME_CHANGE = "regime_change"
    SCHEDULED = "scheduled"


@dataclass
class BacktestTrigger:
    trigger_type: TriggerType
    target_id: str
    training_days: int = 90
    validation_days: int = 30
    priority: int = 5
    detail: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# 最近触发记录（供 /api 查询 + 人工核查"是否真的触发过"，规划文档验收标准
# 明确要求"新因子晋升后5分钟内自动产出回测报告"——这里留痕方便验证）。
_MAX_RECENT = 100
_recent_triggers: List[Dict[str, Any]] = []


def _record_trigger(trig: BacktestTrigger, job_id: Optional[str]) -> None:
    _recent_triggers.append({
        "trigger_type": trig.trigger_type.value,
        "target_id": trig.target_id,
        "detail": trig.detail,
        "created_at": trig.created_at,
        "job_id": job_id,
    })
    if len(_recent_triggers) > _MAX_RECENT:
        del _recent_triggers[: len(_recent_triggers) - _MAX_RECENT]


def get_recent_triggers(limit: int = 20) -> List[Dict[str, Any]]:
    """供 API/人工核查：最近触发过的事件驱动回测（验证"真的触发过"而非空转）。"""
    return list(reversed(_recent_triggers[-limit:]))


class BacktestEventTrigger:
    """单例：订阅因子晋升/权重巨变事件，enqueue 针对性回测任务。

    2026-07-18：不做真正的异步事件总线（那需要额外的pub/sub基础设施，
    收益不确定），改为"调用方在关键节点直接调用本类方法"——这与规划文档
    §4.1"挂在...调用之后发布事件"的描述一致，同步调用+内部enqueue到后台
    任务队列，效果等价（不阻塞调用方），实现成本更低更可控。
    """

    _instance: Optional["BacktestEventTrigger"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def on_factor_promoted(self, factor_id: str, source: str = "") -> Optional[str]:
        """因子从 CANDIDATE 晋升（validate_and_promote admitted=True）时触发。"""
        trig = BacktestTrigger(
            trigger_type=TriggerType.NEW_FACTOR, target_id=factor_id,
            priority=8, detail={"source": source},
        )
        return self._enqueue(trig)

    def on_weight_delta_exceeds_threshold(
        self, factor_id: str, old_w: float, new_w: float, threshold: float = 0.20,
    ) -> Optional[str]:
        """权重变化超过阈值(默认20%)才触发，避免噪声抖动触发风暴。"""
        denom = max(abs(old_w), 1e-6)
        delta_pct = abs(new_w - old_w) / denom
        if delta_pct <= threshold:
            return None
        trig = BacktestTrigger(
            trigger_type=TriggerType.FACTOR_WEIGHT_UPDATE, target_id=factor_id,
            priority=5, detail={"old_weight": old_w, "new_weight": new_w, "delta_pct": delta_pct},
        )
        logger.info(
            f"[BacktestTrigger] 因子{factor_id}权重变化{delta_pct:.1%}(>{threshold:.0%})，触发针对性回测"
        )
        return self._enqueue(trig)

    def on_param_change(self, target_id: str, detail: Optional[Dict[str, Any]] = None) -> Optional[str]:
        trig = BacktestTrigger(
            trigger_type=TriggerType.PARAM_CHANGE, target_id=target_id,
            priority=4, detail=detail or {},
        )
        return self._enqueue(trig)

    def _enqueue(self, trig: BacktestTrigger) -> Optional[str]:
        try:
            from backend.services.factor_engine.factor_jobs import factor_job_manager

            def _fn(job):
                job.total = 1
                job.message = f"事件驱动回测: {trig.trigger_type.value}/{trig.target_id}"
                result = _run_targeted_backtest(trig)
                job.progress = 1
                return result

            job = factor_job_manager.submit(
                kind=f"event_backtest_{trig.trigger_type.value}_{trig.target_id}",
                fn=_fn,
                single_flight=True,
            )
            _record_trigger(trig, job.id)
            logger.info(
                f"[BacktestTrigger] 触发事件驱动回测 type={trig.trigger_type.value} "
                f"target={trig.target_id} job_id={job.id}"
            )
            return job.id
        except Exception as e:
            logger.warning(f"[BacktestTrigger] enqueue失败(不阻断主流程): {e}")
            _record_trigger(trig, None)
            return None


def _run_targeted_backtest(trig: BacktestTrigger) -> Dict[str, Any]:
    """针对触发目标跑一次回测报告（真正产出结果，而非空转）。

    [重要-避免递归] on_factor_promoted 挂在 factor_evolution_loop 的 AlphaMiner
    晋升路径之后（该路径从不调用 factor_backtest_scorer.validate_and_promote，
    两者是完全独立的两套晋升评分体系）。因此这里故意**不**再调用
    validate_and_promote 本身去处理 evo_* 因子——那是 custom_factor_store
    体系的打分入口，对 evo_* 表达式因子会直接返回"因子不存在于目录"，没有意义；
    如果反过来在 validate_and_promote 内部触发本事件，再在这里回调
    validate_and_promote，则会造成"晋升→触发→再晋升→再触发"的递归。
    这里改为跑一次独立的轻量单因子交易模拟(_simple_factor_backtest)，产出
    Sharpe/胜率/交易数——一份 AlphaMiner 晋升路径此前完全没有的、独立于IC/ICIR
    的"这个因子拿去真实交易大概长什么样"报告。
    """
    result = _simple_factor_backtest(trig.target_id)
    result["trigger_type"] = trig.trigger_type.value
    return result


def _resolve_factor_expr(factor_id: str):
    """解析因子表达式：优先复用 FactorEngine 桥接层已解析好的 expr 对象
    （见 base_factors.py._load_active_evolution_factors 新增的 "_expr" 字段），
    避免重新查库解析。兜底直接查 FactorActiveSet。
    """
    bare_id = factor_id[4:] if factor_id.startswith("evo_") else factor_id
    try:
        from backend.services.factor_engine.base_factors import factor_engine
        for candidate_id in (factor_id, f"evo_{bare_id}"):
            entry = factor_engine.FACTORS.get(candidate_id)
            if entry and entry.get("_expr") is not None:
                return entry["_expr"]
    except Exception:
        pass
    try:
        from backend.database.models import FactorActiveSet
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.factor_engine.expr.parser import parse as _parse_expr
        db = AnalyticsSessionLocal()
        try:
            row = db.query(FactorActiveSet).filter(
                FactorActiveSet.factor_id == bare_id
            ).first()
            if row and row.expr_ast:
                return _parse_expr(row.expr_ast)
        finally:
            db.close()
    except Exception:
        pass
    return None


def _simple_factor_backtest(
    factor_id: str, symbols: Optional[List[str]] = None,
    period: str = "1h", lookback: int = 720, horizon: int = 5,
) -> Dict[str, Any]:
    """轻量单因子交易模拟：因子z-score符号定多空方向，固定持有horizon根后平仓。

    不是策略级完整回测（不含仓位管理/手续费/滑点），是"这个因子单独拿去交易
    方向大概靠不靠谱"的快速探针——足以在5分钟内给出可读的Sharpe/胜率反馈，
    比等次日调度的完整流程快得多，符合规划文档§4.1的验收目标。
    """
    try:
        import numpy as np
        from backend.services.data_center import data_center
        from backend.services.alpha.factor_compute import kline_df_to_fields
    except Exception as e:
        return {"factor_id": factor_id, "error": f"依赖加载失败: {e}"}

    expr = _resolve_factor_expr(factor_id)
    if expr is None:
        return {"factor_id": factor_id, "skipped": "无法解析因子表达式(非AST因子或已失效)"}

    syms = symbols or ["BTC", "ETH", "SOL", "BNB"]
    trade_returns: List[float] = []
    per_symbol: Dict[str, int] = {}
    for sym in syms:
        try:
            df = data_center.get_klines_df(sym, period, count=lookback)
            if len(df) < 100:
                continue
            fields = kline_df_to_fields(df)
            vals = np.asarray(expr.evaluate(fields), dtype=float)
            if vals.ndim == 0 or len(vals) != len(df):
                continue
            close = df["close"].values.astype(float)
            std = np.nanstd(vals)
            if not np.isfinite(std) or std < 1e-12:
                continue
            z = (vals - np.nanmean(vals)) / (std + 1e-10)
            n_before = len(trade_returns)
            for i in range(len(z) - horizon):
                if not np.isfinite(z[i]) or abs(z[i]) < 0.5:  # 弱信号不交易，减少噪声交易
                    continue
                direction = 1.0 if z[i] > 0 else -1.0
                ret = direction * (close[i + horizon] / close[i] - 1.0)
                trade_returns.append(float(ret))
            per_symbol[sym] = len(trade_returns) - n_before
        except Exception as e:
            logger.debug(f"[BacktestTrigger] {sym} 单因子模拟失败: {e}")

    if not trade_returns:
        return {"factor_id": factor_id, "skipped": "无有效交易样本(信号过弱或数据不足)"}

    arr = np.array(trade_returns)
    win_rate = float((arr > 0).mean())
    total_return = float(arr.sum())
    ann_factor = np.sqrt(252 * (24 / max(1, {"1h": 1, "4h": 4, "1d": 24}.get(period, 1))))
    sharpe = float(arr.mean() / (arr.std() + 1e-10) * ann_factor)
    return {
        "factor_id": factor_id,
        "n_trades": len(arr),
        "win_rate": round(win_rate, 4),
        "total_return_sum": round(total_return, 4),
        "sharpe_approx": round(sharpe, 3),
        "per_symbol_trades": per_symbol,
        "horizon_bars": horizon,
        "period": period,
    }


backtest_event_trigger = BacktestEventTrigger()
