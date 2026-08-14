"""FactorBacktestScorer — 单因子样本外回测打分闸门（阶段二 2.2，核心）。

让"自动发现的因子"必须先通过一道严格的科研闸门，才能进入短线活跃因子集：

1. 单因子样本外（walk-forward）回测：按因子符号在历史 K 线上做多空，扣除
   往返手续费+滑点后统计**净收益 / Sharpe / 胜率**。方向由训练窗口的 IC 符号确定，
   只在其后的测试窗口计入绩效 —— 严格样本外，杜绝用未来信息拟合。
2. IC/ICIR/衰减/单调性评级：复用 `FactorEvaluator`。
3. 正交去冗余：与已 active 的因子做相关性检验，|corr| 过高判为冗余。
4. 综合评级 A/B 才准入（晋升为 active）；否则 rejected。

对公式因子（`custom_factor_store` 里的候选）打分并回写 grade/status。
暴露 `POST /api/factors/validate/{factor_id}` 与批量接口。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FactorScoreResult:
    factor_id: str
    grade: str = "F"                 # A/B/C/D/F
    admitted: bool = False           # 是否达到 active 准入
    ic_mean: float = 0.0
    icir: float = 0.0
    ic_decay_halflife: int = 0
    monotonicity: float = 0.0
    oos_net_return: float = 0.0      # 样本外累计净收益（扣费）
    oos_sharpe: float = 0.0
    oos_win_rate: float = 0.0
    oos_trades: int = 0
    redundant_with: Optional[str] = None
    reason: str = ""
    per_symbol: Dict[str, Any] = field(default_factory=dict)


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def _resolve_admin_tenant():
    """[2026-08-13 P1-9] 管理员租户 id。

    custom_factor_store 按 t{tenant_id}:factor_id 隔离，list_*/get/update_scores
    不传租户时拿不到 AI 发现因子（管理链路从未运行）。统一显式取管理员租户。
    """
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        return resolve_admin_tenant_id()
    except Exception:
        return None


# [2026-08-13 短线因子根因修复 P0-3] 周期→小时映射（用于 funding 持仓时长估算）。
_PERIOD_HOURS = {
    "1m": 1 / 60, "3m": 3 / 60, "5m": 5 / 60, "15m": 0.25,
    "30m": 0.5, "1h": 1.0, "2h": 2.0, "4h": 4.0, "8h": 8.0, "1d": 24.0,
}

# [2026-08-13 P1-5] 打分前瞻期与进化侧分档对齐（scalp ATR 持仓节奏）。
_PERIOD_FWD_BARS = {
    "1m": 12, "3m": 12, "5m": 12, "15m": 6, "30m": 4,
    "1h": 2, "2h": 1, "4h": 1, "8h": 1, "1d": 1,
}


def _period_fwd_bars(interval: str) -> int:
    return _PERIOD_FWD_BARS.get(str(interval or "").strip().lower(), 5)


def _period_hours(interval: str) -> float:
    return _PERIOD_HOURS.get(str(interval or "").strip().lower(), 1.0)


class FactorBacktestScorer:
    """单因子样本外回测 + 打分 + 准入（单例）。"""

    _instance: Optional["FactorBacktestScorer"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 数据加载 ──
    @staticmethod
    def _load_klines(symbol: str, interval: str, limit: int):
        """优先 UnifiedDataPool（进程内实时快照），回退 DB（离线/独立进程可用）。"""
        try:
            from backend.services.unified_data_pool import UnifiedDataPool
            k = UnifiedDataPool().get_kline_series(symbol, interval=interval, limit=limit)
            if k and len(k) >= 120:
                return k
        except Exception as e:
            logger.debug(f"[FactorScorer] {symbol} UDP K线加载失败，回退DB: {e}")
        # [2026-08-13 P0-3] 不再固定 hyperliquid：与实盘同源（active_exchange，
        # 当前 asterdex）。因子在 A 所打分、在 B 所成交是短线因子失真的根源之一。
        try:
            from backend.services.kline_data_service import kline_service
            rows = kline_service.get_klines_from_db(symbol.upper(), interval, limit)
            return rows or None
        except Exception as e:
            logger.debug(f"[FactorScorer] {symbol} DB K线加载失败: {e}")
            return None

    @staticmethod
    def _kline_field(k, field: str):
        """兼容对象式（k.close）与字典式（k['close']）K 线记录。"""
        if isinstance(k, dict):
            return k.get(field)
        return getattr(k, field, None)

    @classmethod
    def _to_arrays(cls, klines) -> Optional[Dict[str, np.ndarray]]:
        try:
            closes = np.array([float(cls._kline_field(k, "close")) for k in klines])
            highs = np.array([float(cls._kline_field(k, "high") or cls._kline_field(k, "close")) for k in klines])
            lows = np.array([float(cls._kline_field(k, "low") or cls._kline_field(k, "close")) for k in klines])
            vols = np.array([float(cls._kline_field(k, "volume") or 0) for k in klines])
            # [2026-08-13 P0-3] open 直读 DB/UDP 真实开盘价，不再用前一根收盘近似：
            # 短线因子大量依赖开盘价，np.roll(close) 会系统性污染因子值与 IC。
            opens = np.array([
                float(cls._kline_field(k, "open") or cls._kline_field(k, "close"))
                for k in klines
            ])
            return {"close": closes, "high": highs, "low": lows, "volume": vols, "open": opens}
        except Exception:
            return None

    @staticmethod
    def _eval_formula(formula: str, arrays: Dict[str, np.ndarray]) -> Optional[np.ndarray]:
        try:
            ns = {"np": np, **arrays}
            try:
                from backend.services.factor_engine.formula_ops import FORMULA_OPS
                ns.update(FORMULA_OPS)
            except Exception:
                pass
            vals = eval(formula, {"__builtins__": {}}, ns)  # noqa: S307 受限命名空间
            if not isinstance(vals, np.ndarray):
                return None
            return vals.astype(float)
        except Exception:
            return None

    # ── 样本外回测（walk-forward，方向由训练窗口 IC 决定）──
    def _walk_forward_backtest(
        self,
        factor_vals: np.ndarray,
        closes: np.ndarray,
        fwd: int,
        cost: float,
        funding_per_hold: float = 0.0,
        bars_per_year: Optional[int] = None,
    ) -> Dict[str, Any]:
        n = len(closes)
        fwd_ret = np.full(n, np.nan)
        fwd_ret[:-fwd] = (closes[fwd:] - closes[:-fwd]) / closes[:-fwd]

        # 因子标准化（滚动 z-score，避免量纲影响持仓方向的门限）
        f = factor_vals.copy()
        mask = np.isfinite(f) & np.isfinite(fwd_ret)
        idx = np.where(mask)[0]
        if len(idx) < 60:
            return {"net_return": 0.0, "sharpe": 0.0, "win_rate": 0.0, "trades": 0, "n": len(idx)}

        # walk-forward：3 折，前一折 IC 定方向，本折计绩效。
        # 关键：测试窗口内**非重叠**地每 fwd 根 K 线调一次仓（持有 fwd 根），
        # 避免重叠前向收益的重复计数，也让每笔只计一次往返成本（贴近真实换手）。
        folds = 3
        seg = len(idx) // folds
        oos_returns: List[float] = []
        for k in range(1, folds):
            train_idx = idx[(k - 1) * seg: k * seg]
            test_idx = idx[k * seg: (k + 1) * seg] if k < folds - 1 else idx[k * seg:]
            if len(train_idx) < 20 or len(test_idx) < 10:
                continue
            # 训练窗口 IC（因子 vs 前向收益，皮尔逊即可判方向）
            tf = f[train_idx]
            tr = fwd_ret[train_idx]
            if np.std(tf) < 1e-12 or np.std(tr) < 1e-12:
                continue
            ic = float(np.corrcoef(tf, tr)[0, 1])
            orient = 1.0 if ic >= 0 else -1.0
            # 测试窗口标准化（用训练窗口的均值/方差，避免用测试期未来信息）
            mu, sd = np.mean(tf), np.std(tf)
            if sd < 1e-12:
                continue
            # 非重叠采样：每 fwd 根取一次
            sample = test_idx[::max(1, fwd)]
            prev_pos = 0.0
            for t in sample:
                z = (f[t] - mu) / sd
                pos = np.sign(z) * orient
                r = fwd_ret[t]
                if not np.isfinite(r):
                    continue
                gross = pos * r
                # 换手成本：仓位变化时计一次往返（0→±1 或 +1↔−1）
                turn = abs(pos - prev_pos)
                trade_cost = cost * (turn / 2.0)  # 每条腿 cost/2；一次翻转≈一次往返
                # [2026-08-13 P1-7] funding：满仓持有 fwd 根跨过的 8h 结算成本
                oos_returns.append(float(gross - trade_cost - funding_per_hold))
                prev_pos = pos

        if not oos_returns:
            return {"net_return": 0.0, "sharpe": 0.0, "win_rate": 0.0, "trades": 0, "n": len(idx)}

        arr = np.array(oos_returns)
        net_return = float(np.sum(arr))
        mean_r = float(np.mean(arr))
        std_r = float(np.std(arr))
        # [2026-08-13 P0-3] 年化 Sharpe 周期化尺度修复：sqrt(样本数) 非年化；
        # 每笔代表 fwd 根持有期，年化期数 = bars_per_year / fwd，
        # 用 min 封顶防止「交易笔数 < 年化期数」时放大。
        if std_r > 0:
            annual = (float(bars_per_year) / max(fwd, 1)) if bars_per_year else 0.0
            scale = float(np.sqrt(min(annual, len(arr)))) if annual > 0 else float(np.sqrt(len(arr)))
            sharpe = float(mean_r / (std_r + 1e-12) * scale)
        else:
            sharpe = 0.0
        win_rate = float(np.mean(arr > 0))
        return {
            "net_return": round(net_return, 6),
            "sharpe": round(sharpe, 4),
            "win_rate": round(win_rate, 4),
            "trades": len(arr),
            "n": len(idx),
        }

    def _active_factor_series(
        self,
        arrays_by_symbol: Dict[str, Dict[str, np.ndarray]],
        pool: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, np.ndarray]:
        """已 active 公式因子在同一段数据上的值（用于正交去冗余）。

        pool 为 None 时取全部 active；否则只用给定对照集（如仅中长线活跃因子）。
        """
        out: Dict[str, np.ndarray] = {}
        try:
            from backend.services.factor_engine.custom_factor_store import custom_factor_store
            # 用第一个 symbol 的数组做相关性检验即可
            first = next(iter(arrays_by_symbol.values()), None)
            if first is None:
                return out
            records = pool if pool is not None else custom_factor_store.list_active(tenant_id=_resolve_admin_tenant())
            for rec in records:
                formula = rec.get("formula")
                if not formula:
                    continue
                vals = self._eval_formula(formula, first)
                if vals is not None and np.isfinite(vals).sum() > 30:
                    out[rec["factor_id"]] = vals
        except Exception as e:
            logger.debug(f"[FactorScorer] 载入 active 因子序列失败: {e}")
        return out

    def score_formula(
        self,
        factor_id: str,
        formula: str,
        symbols: Optional[List[str]] = None,
        *,
        interval: Optional[str] = None,
        lookback: Optional[int] = None,
        fwd: Optional[int] = None,
        cost: Optional[float] = None,
        min_sharpe: Optional[float] = None,
        min_net: Optional[float] = None,
        redun_corr: Optional[float] = None,
        redundancy_pool: Optional[List[Dict[str, Any]]] = None,
        funding_rate: Optional[float] = None,
        dsr_required: Optional[bool] = None,
    ) -> FactorScoreResult:
        """对一个公式因子做样本外回测 + 打分。

        全部超参默认取 `FACTOR_SCORER_*` 配置（短线 1h）；中长线因子科研（S4）传入
        `interval='4h'/'1d'` 等覆盖值即可复用同一套 walk-forward 引擎。
        `redundancy_pool` 可限定正交去冗余的对照集（如仅中长线活跃因子），None 时取全部 active。
        """
        import pandas as pd
        from backend.services.factor_engine.factor_evaluator import get_factor_evaluator

        interval = str(interval if interval is not None else _cfg("FACTOR_SCORER_INTERVAL", "1h"))
        lookback = int(lookback if lookback is not None else _cfg("FACTOR_SCORER_LOOKBACK_BARS", 720))
        # [2026-08-13 P1-5] 前瞻期按周期分档对齐实盘：显式传参 > FACTOR_SCORER_FWD_PERIOD
        # env（>0）> 周期分档表（1h→2 根等）。旧全局 5 根（1h=5h 前瞻）与 scalp
        # 分钟~小时级持仓错配，是训练打分与实盘执行三重错配之一。
        if fwd is not None:
            fwd = int(fwd)
        else:
            _fwd_cfg = int(_cfg("FACTOR_SCORER_FWD_PERIOD", 0) or 0)
            fwd = _fwd_cfg if _fwd_cfg > 0 else _period_fwd_bars(interval)
        cost = float(cost if cost is not None else _cfg("FACTOR_SCORER_COST", 0.0021))
        min_sharpe = float(min_sharpe if min_sharpe is not None else _cfg("FACTOR_SCORER_MIN_SHARPE", 0.5))
        min_net = float(min_net if min_net is not None else _cfg("FACTOR_SCORER_MIN_NET_RETURN", 0.0))
        redun_corr = float(redun_corr if redun_corr is not None else _cfg("FACTOR_SCORER_REDUNDANCY_CORR", 0.8))
        funding_rate = float(funding_rate if funding_rate is not None else _cfg("FACTOR_SCORER_FUNDING_RATE", 0.0001))
        dsr_required = bool(dsr_required if dsr_required is not None else _cfg("FACTOR_SCORER_DSR_REQUIRED", True))
        # [2026-08-13 P1-7] 每笔持仓 fwd 根跨过的 8h funding 结算次数 × 费率
        _hold_hours = fwd * _period_hours(interval)
        funding_per_hold = funding_rate * (_hold_hours / 8.0) if funding_rate > 0 else 0.0
        # 年化尺度：每年 bar 数（供 Sharpe 周期化）
        bars_per_year = int(round(365.0 * 24.0 / _period_hours(interval))) if _period_hours(interval) > 0 else None

        syms = symbols or [s.strip().upper() for s in str(_cfg("FACTOR_SCORER_SYMBOLS", "BTC,ETH,SOL")).split(",") if s.strip()]

        result = FactorScoreResult(factor_id=factor_id)

        arrays_by_symbol: Dict[str, Dict[str, np.ndarray]] = {}
        ic_list: List[float] = []
        icir_list: List[float] = []
        decay_list: List[int] = []
        mono_list: List[float] = []
        net_list: List[float] = []
        sharpe_list: List[float] = []
        wr_list: List[float] = []
        trades_total = 0
        net_total = 0.0

        evaluator = get_factor_evaluator(forward_period=fwd)

        for sym in syms:
            klines = self._load_klines(sym, interval, lookback)
            if not klines or len(klines) < 120:
                continue
            arrays = self._to_arrays(klines)
            if arrays is None:
                continue
            factor_vals = self._eval_formula(formula, arrays)
            if factor_vals is None or np.isfinite(factor_vals).sum() < 60:
                continue
            arrays_by_symbol[sym] = arrays

            # IC/ICIR/衰减/单调性（复用 FactorEvaluator）
            try:
                rep = evaluator.evaluate_factor(
                    factor_id,
                    pd.Series(factor_vals),
                    pd.Series(arrays["close"]),
                    # [2026-08-14 P1-A1] 显式传前瞻期，与单例 forward_period 同步更新双保险
                    forward_period=fwd,
                )
                if rep.data_points >= 30:
                    ic_list.append(rep.ic_mean)
                    icir_list.append(rep.icir)
                    decay_list.append(rep.ic_decay_halflife)
                    mono_list.append(rep.monotonicity)
            except Exception as e:
                logger.debug(f"[FactorScorer] {sym} evaluate_factor 失败: {e}")

            # 样本外回测（含 funding 持仓成本 + 年化尺度）
            bt = self._walk_forward_backtest(
                factor_vals, arrays["close"], fwd, cost,
                funding_per_hold=funding_per_hold, bars_per_year=bars_per_year,
            )
            if bt["trades"] > 0:
                net_list.append(bt["net_return"])
                sharpe_list.append(bt["sharpe"])
                wr_list.append(bt["win_rate"])
                trades_total += bt["trades"]
                net_total += bt["net_return"]
                result.per_symbol[sym] = bt

        if not net_list or not ic_list:
            result.reason = "有效样本不足（无法在核心币种上完成回测/IC）"
            return result

        result.ic_mean = round(float(np.mean(ic_list)), 4)
        result.icir = round(float(np.mean(icir_list)), 4)
        result.ic_decay_halflife = int(np.mean(decay_list)) if decay_list else 0
        result.monotonicity = round(float(np.mean(mono_list)), 4) if mono_list else 0.0
        result.oos_net_return = round(float(np.mean(net_list)), 6)
        result.oos_sharpe = round(float(np.mean(sharpe_list)), 4)
        result.oos_win_rate = round(float(np.mean(wr_list)), 4)
        result.oos_trades = trades_total

        # 正交去冗余：与 active 因子相关性过高 → 冗余，不准入
        try:
            active_series = self._active_factor_series(arrays_by_symbol, pool=redundancy_pool)
            if active_series:
                first = next(iter(arrays_by_symbol.values()))
                cand = self._eval_formula(formula, first)
                if cand is not None:
                    for aid, avals in active_series.items():
                        if aid == factor_id:
                            continue
                        m = np.isfinite(cand) & np.isfinite(avals)
                        if m.sum() < 30:
                            continue
                        if np.std(cand[m]) < 1e-12 or np.std(avals[m]) < 1e-12:
                            continue
                        corr = abs(float(np.corrcoef(cand[m], avals[m])[0, 1]))
                        if corr >= redun_corr:
                            result.redundant_with = aid
                            break
        except Exception as e:
            logger.debug(f"[FactorScorer] 正交检验跳过: {e}")

        # ── [2026-08-13 P1-7] DSR/PBO 多重检验闸门 ──
        dsr_ok, pbo_val = True, 0.0
        if dsr_required:
            _n_trials = int(_cfg("FACTOR_SCORER_DSR_N_TRIALS", 40))
            dsr_ok, pbo_val = self._dsr_pbo_gate(icir_list, lookback, _n_trials)

        # ── 综合评级 ──
        # 用 |IC|/|ICIR|：回测已按训练窗口 IC 符号自动定方向，负 IC 因子（反向可交易）
        # 同样有效，不应因符号被误判。准入还要求样本外风险调整收益达标（perf_ok）。
        abs_ic = abs(result.ic_mean)
        abs_icir = abs(result.icir)
        # [2026-08-13 P1-7] min_net 收紧：每笔平均净收益须覆盖往返成本 + 0.05% 缓冲，
        # 不再允许「总净收益>0 但每笔利润薄过成本」的因子准入。
        _avg_net_per_trade = (net_total / max(trades_total, 1)) if trades_total > 0 else 0.0
        _net_buffer = float(_cfg("FACTOR_SCORER_NET_BUFFER", 0.0005))
        perf_ok = (
            result.oos_sharpe >= min_sharpe
            and result.oos_net_return > min_net
            and _avg_net_per_trade > (cost + _net_buffer)
        )
        if result.redundant_with:
            result.grade = "C"
            result.reason = f"与 active 因子 {result.redundant_with} 冗余（corr≥{redun_corr}）"
        elif not dsr_ok:
            result.grade = "C"
            result.reason = f"DSR/PBO 未通过（pbo={pbo_val:.3f}）——多重检验下无显著预测力"
        elif abs_ic >= 0.05 and abs_icir > 0.5 and perf_ok:
            result.grade = "A"
        elif abs_ic >= 0.03 and abs_icir > 0.3 and perf_ok:
            result.grade = "B"
        elif abs_ic >= 0.015:
            result.grade = "C"
        else:
            result.grade = "D"

        result.admitted = result.grade in ("A", "B")
        if not result.reason:
            result.reason = (
                f"grade={result.grade} IC={result.ic_mean} ICIR={result.icir} "
                f"OOS_sharpe={result.oos_sharpe} OOS_net={result.oos_net_return} "
                f"win={result.oos_win_rate} trades={result.oos_trades}"
            )
        # [2026-08-14 P0-1] DSR 跳过的可见性：reason 落库供运维台确认跳过原因
        if pbo_val is None:
            result.reason += " | DSR/PBO 跳过（跨币样本不足 fail-open）"
        return result

    @staticmethod
    def _dsr_pbo_gate(icir_list: List[float], sample_len: int, n_trials: int):
        """[2026-08-13 P1-7] DSR/PBO 多重检验闸门。

        返回 (dsr_ok, pbo)：
        - pbo 为 None 表示「跨币样本不足，显式 fail-open 跳过」（单币/2~3 币一致化），
          由 OOS Sharpe/净收益/冗余等其它门槛兜底；
        - 跨币样本充足（>=FACTOR_SCORER_DSR_MIN_SYMBOLS）时才正常计算；
        - 计算工具异常时仍 fail-closed（宁可误拒，不放无验证因子入实盘）。

        [2026-08-14 P0-1 修复] 此前 3 币场景 pbo 恒 0.5 且门槛 `pbo < 0.5` 恒假 →
        所有因子机械性拒绝（晋升管道死锁）；单币场景又 fail-open，语义不对称。
        现统一为样本不足一律显式跳过并告警。
        """
        _min_symbols = max(1, int(_cfg("FACTOR_SCORER_DSR_MIN_SYMBOLS", 4)))
        if len(icir_list) < _min_symbols:
            logger.warning(
                "[FactorScorer] DSR/PBO 跳过（跨币样本 %d < %d，fail-open）"
                "——多重检验无法估计，由 OOS/净收益/冗余门槛兜底",
                len(icir_list), _min_symbols,
            )
            return True, None
        try:
            from backend.services.factor_engine.dsr_pbo import compute_dsr_pbo_for_factors
            r = compute_dsr_pbo_for_factors(
                icir_list=list(icir_list),
                n_total_candidates=max(int(n_trials), 1),
                sample_len=max(int(sample_len), 50),
            )
            _pbo_r = r.get("pbo_result") or {}
            if bool(_pbo_r.get("indeterminate")):
                logger.warning(
                    "[FactorScorer] DSR/PBO 不可判定（PBO 组合无效），fail-open 跳过"
                )
                return True, None
            dsr_sig = bool((r.get("dsr_result") or {}).get("significant", False))
            pbo = float(_pbo_r.get("pbo", 1.0))
            _max_pbo = float(_cfg("FACTOR_SCORER_MAX_PBO", 0.5))
            # [2026-08-14 P0-1] pbo<=阈值（0.5=无证据，不应成为硬阻断边界陷阱）
            return bool(dsr_sig and pbo <= _max_pbo), pbo
        except Exception as e:
            logger.warning("[FactorScorer] DSR/PBO 计算失败，fail-closed: %s", e)
            return False, 1.0

    def validate_and_promote(self, factor_id: str) -> FactorScoreResult:
        """对目录中的候选因子打分并回写 grade/status（A/B→active，否则 rejected）。"""
        from backend.services.factor_engine.custom_factor_store import custom_factor_store

        rec = custom_factor_store.get(factor_id, tenant_id=_resolve_admin_tenant())
        if not rec:
            r = FactorScoreResult(factor_id=factor_id)
            r.reason = "因子不存在于目录"
            return r
        formula = rec.get("formula") or ""

        # 中长线因子（extra.horizon=="midlong"）按其时间框架(4h/1d)样本外打分
        _extra = rec.get("extra") or {}
        _horizon = str(_extra.get("horizon") or "scalp").lower()
        if _horizon == "midlong":
            _tf = str(_extra.get("timeframe") or "4h").lower()
            result = self.score_formula(
                factor_id, formula,
                interval=_tf,
                lookback=int(_cfg("FACTOR_SCORER_MIDLONG_LOOKBACK", 900)),
                fwd=int(_cfg("FACTOR_SCORER_MIDLONG_FWD_1D", 3)) if _tf == "1d"
                    else int(_cfg("FACTOR_SCORER_MIDLONG_FWD_4H", 6)),
                min_sharpe=float(_cfg("FACTOR_SCORER_MIDLONG_MIN_SHARPE", 0.4)),
                redundancy_pool=[
                    r for r in custom_factor_store.list_active(tenant_id=_resolve_admin_tenant())
                    if str((r.get("extra") or {}).get("horizon") or "scalp").lower() == "midlong"
                ],
            )
        else:
            result = self.score_formula(factor_id, formula)

        # active 因子集上限保护（按 horizon 分别计数）
        status = "active" if result.admitted else "rejected"
        if result.admitted:
            try:
                if _horizon == "midlong":
                    active_n = len([
                        r for r in custom_factor_store.list_active(tenant_id=_resolve_admin_tenant())
                        if str((r.get("extra") or {}).get("horizon") or "scalp").lower() == "midlong"
                    ])
                    _cap = int(_cfg("MIDLONG_ACTIVE_FACTOR_MAX", 30))
                else:
                    active_n = len([
                        r for r in custom_factor_store.list_active(tenant_id=_resolve_admin_tenant())
                        if str((r.get("extra") or {}).get("horizon") or "scalp").lower() != "midlong"
                    ])
                    _cap = int(_cfg("SCALP_ACTIVE_FACTOR_MAX", 40))
                if active_n >= _cap:
                    status = "candidate"
                    result.reason += f" | 活跃因子已满({active_n})，暂不晋升"
                    result.admitted = False
            except Exception:
                pass

        custom_factor_store.update_scores(
            factor_id,
            grade=result.grade,
            scores={
                "ic_mean": result.ic_mean,
                "icir": result.icir,
                "ic_decay_halflife": result.ic_decay_halflife,
                "monotonicity": result.monotonicity,
                "oos_net_return": result.oos_net_return,
                "oos_sharpe": result.oos_sharpe,
                "oos_win_rate": result.oos_win_rate,
                "oos_trades": result.oos_trades,
                "redundant_with": result.redundant_with,
                "per_symbol": result.per_symbol,
                # [2026-08-14 阶段2] 运维台可见性：拒绝原因落库，供中线因子面板展示
                "reason": (result.reason or "")[:200],
            },
            status=status,
            tenant_id=_resolve_admin_tenant(),
        )
        # 晋升后热加载，让 active 公式因子进入 compute_all_factors
        if status == "active":
            try:
                from backend.services.factor_engine.base_factors import factor_engine
                factor_engine._load_active_custom_factors()
            except Exception:
                pass
        logger.info(f"[FactorScorer] {factor_id} 打分完成: {result.reason} → status={status}")
        return result

    def validate_all_candidates(self, limit: int = 20) -> Dict[str, Any]:
        """批量给候选因子打分晋升（供定时任务/接口调用）。"""
        from backend.services.factor_engine.custom_factor_store import custom_factor_store

        candidates = custom_factor_store.list_candidates(tenant_id=_resolve_admin_tenant())[:limit]
        results = []
        for rec in candidates:
            try:
                r = self.validate_and_promote(rec["factor_id"])
                results.append({
                    "factor_id": r.factor_id, "grade": r.grade,
                    "admitted": r.admitted, "reason": r.reason,
                })
            except Exception as e:
                logger.warning(f"[FactorScorer] {rec.get('factor_id')} 打分异常: {e}")
        promoted = [r for r in results if r["admitted"]]
        return {
            "scored": len(results),
            "promoted": len(promoted),
            "results": results,
        }


# 全局单例
factor_backtest_scorer = FactorBacktestScorer()
