"""
组合级风险预算 PortfolioBudget（v6 计划 阶段1 第4项）。

幻方顶层"CVaR 风险预算 + 自动熔断"在本项目的落地（按 crypto 波动率缩放，
不照搬 A 股 2% 回撤锁）。下单前最后一道检查，四条规则：

1. 单币种集中度上限：同币所有方向名义 / 权益 ≤ PB_MAX_SYMBOL_EXPOSURE_PCT
2. 组合日 VaR：持仓币 1d 收益按名义权重（含本单）加权 → 历史模拟 95% VaR，
   VaR / 权益 > PB_MAX_DAILY_VAR_PCT 拒开
3. 单策略回撤 3σ 熔断：策略历史已平仓 PnL 序列的当前回撤 > 3σ → 冻结该策略
4. 组合级冻结信号：硬指标触发 → 全局冻结 PB_FREEZE_COOLDOWN_SEC（可查可解冻）

接入：scalp_loop / midlong_helpers（下单前最后一道检查）。
热路径友好：收益序列 600s / 持仓 30s / 策略 PnL 300s TTL 缓存。
异常语义：paper fail-open（保样本）、live fail-closed（可配 PB_FAIL_CLOSED_LIVE）。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

_MIDLONG_NATURES = frozenset({"swing", "trend_follow", "position"})
_MIDLONG_TIERS = frozenset({"mid", "long"})

# [2026-08-06 修复] 策略回撤 σ 计算只看最近 N 天已平仓交易：
# ① 8 月前是废弃/迁移数据，全历史窗口会把 30.82σ 假熔断喂给短线策略（永久冻结）；
# ② 无时间窗查询每次扫全表（paper_positions 1271+ 行），实测出现 98s 挂起事务。
PB_DD_LOOKBACK_DAYS = 30


def _cfg_bool(name: str, default: bool = True) -> bool:
    v = os.getenv(name)
    if v is not None:
        return v.strip().lower() in ("1", "true", "yes", "on")
    try:
        from backend.config import settings
        return bool(getattr(settings, name, default))
    except Exception:
        return default


def _cfg_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    try:
        from backend.config import settings
        return float(getattr(settings, name, default) or default)
    except Exception:
        return default


def _cfg_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    try:
        from backend.config import settings
        return int(getattr(settings, name, default) or default)
    except Exception:
        return default


@dataclass
class BudgetDecision:
    """组合预算决策结果。"""
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    freeze_until: float = 0.0
    strategy: str = ""


def _pos_notional(pos: Dict[str, Any]) -> float:
    """单仓名义（size × 最新价，缺失时 margin × leverage 兜底）。"""
    try:
        size = float(pos.get("size") or pos.get("quantity") or 0)
        px = float(
            pos.get("mark_price")
            or pos.get("current_price")
            or pos.get("entry_price")
            or 0
        )
        if size > 0 and px > 0:
            return abs(size * px)
        margin = float(pos.get("margin") or 0)
        lev = float(pos.get("leverage") or 1) or 1
        if margin > 0:
            return abs(margin * lev)
    except Exception:
        return 0.0
    return 0.0


def _pos_dir(side: Any) -> str:
    s = str(side or "").lower()
    if s in ("long", "buy", "b"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return ""


def _action_dir(action: str) -> str:
    a = (action or "").lower()
    if a in ("buy", "long"):
        return "long"
    if a in ("sell", "short"):
        return "short"
    return ""


def _is_strategy_pos(pos: Dict[str, Any], strategy: str) -> bool:
    nature = str(pos.get("trade_nature") or "").lower()
    tier = str(pos.get("timeframe_tier") or "").lower()
    if strategy == "midlong":
        return nature in _MIDLONG_NATURES or tier in _MIDLONG_TIERS
    if strategy == "scalp":
        return nature == "scalp" or tier == "short"
    # 默认：全部
    return True


def _signed_notional(pos: Dict[str, Any]) -> float:
    d = _pos_dir(pos.get("side"))
    n = _pos_notional(pos)
    if d == "long":
        return n
    if d == "short":
        return -n
    return 0.0


class PortfolioBudget:
    """组合级风险预算（单例：portfolio_budget）。"""

    def __init__(self) -> None:
        self._frozen_until: float = 0.0
        self._strategy_frozen_until: Dict[str, float] = {}
        self._cache: Dict[str, Any] = {}
        self._lock = __import__("threading").Lock()
        self._last_decision: Optional[BudgetDecision] = None

    # ── 公共入口 ──────────────────────────────────────────────

    def evaluate_open(
        self,
        *,
        symbol: str,
        action: str,
        notional_usd: float,
        equity: float,
        strategy: str = "scalp",
        mode: str = "paper",
        db=None,
        account_id: int = 0,
        positions: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> BudgetDecision:
        """下单前最后一道组合预算检查。

        positions: 全账户 open 持仓 dict 列表（缺省时模块内按 account 拉取并缓存）。
        """
        strategy = (strategy or "scalp").strip().lower()
        mode = (mode or "paper").strip().lower()
        sym = str(symbol or "").upper()
        reasons: List[str] = []
        metrics: Dict[str, Any] = {"strategy": strategy}

        if not _cfg_bool("PB_ENABLED", True):
            return BudgetDecision(True, [], metrics, strategy=strategy)

        try:
            # ── 0. 冻结信号 ──
            now = time.time()
            frozen_until = max(self._frozen_until, self._strategy_frozen_until.get(strategy, 0.0))
            if frozen_until > now:
                reasons.append(f"portfolio_frozen_until={int(frozen_until - now)}s")
                metrics["frozen"] = True
                return BudgetDecision(False, reasons, metrics, freeze_until=frozen_until, strategy=strategy)

            # ── 1. 单币种集中度 ──
            pos_list = self._load_positions(db, account_id, positions)
            conc = self._concentration_pct(pos_list, sym, notional_usd, equity)
            metrics["concentration_pct"] = conc
            max_conc = _cfg_float("PB_MAX_SYMBOL_EXPOSURE_PCT", 0.30)
            if equity > 0 and conc > max_conc:
                reasons.append(f"concentration {conc:.0%}>{max_conc:.0%} ({sym})")
                self._freeze(strategy, why=reasons[-1], global_scope=True)
                return BudgetDecision(False, reasons, metrics, strategy=strategy)

            # ── 2. 组合日 VaR（95% 历史模拟，含本单） ──
            var_ratio = self._daily_var_ratio(pos_list, sym, action, notional_usd, equity)
            metrics["var_95_pct"] = var_ratio
            max_var = _cfg_float("PB_MAX_DAILY_VAR_PCT", 0.05)
            if var_ratio is not None and var_ratio > max_var:
                reasons.append(f"daily_var {var_ratio:.1%}>{max_var:.1%}")
                self._freeze(strategy, why=reasons[-1], global_scope=True)
                return BudgetDecision(False, reasons, metrics, strategy=strategy)

            # ── 3. 单策略回撤 3σ 熔断 ──
            dd_sigma = self._strategy_drawdown_sigma(strategy, db, account_id)
            metrics["drawdown_sigma"] = dd_sigma
            sigma_cap = _cfg_float("PB_STRATEGY_DRAWDOWN_SIGMA", 3.0)
            if dd_sigma is not None and dd_sigma > sigma_cap:
                reasons.append(f"{strategy} drawdown={dd_sigma:.2f}σ>{sigma_cap:.0f}σ")
                self._freeze(strategy, why=reasons[-1])
                return BudgetDecision(False, reasons, metrics, strategy=strategy)

            reasons.append("ok")
            return BudgetDecision(True, reasons, metrics, strategy=strategy)
        except Exception as e:
            # 异常语义：paper fail-open 保样本；live fail-closed 保资金（可配）
            live_fail_closed = _cfg_bool("PB_FAIL_CLOSED_LIVE", True)
            if mode == "live" and live_fail_closed:
                reasons.append(f"pb_error_live_fail_closed: {str(e)[:120]}")
                logger.warning("[PortfolioBudget] live fail-closed: %s", e)
                return BudgetDecision(False, reasons, metrics, strategy=strategy)
            logger.debug("[PortfolioBudget] %s 异常(fail-open): %s", strategy, e)
            metrics["pb_error"] = str(e)[:200]
            return BudgetDecision(True, ["pb_error_fail_open"], metrics, strategy=strategy)

    # ── 冻结信号 ─────────────────────────────────────────────

    def _freeze(self, strategy: str, why: str, *, global_scope: bool = False) -> None:
        """冻结信号：global_scope=True 全局冻结（硬指标），否则只冻结该策略（3σ 熔断）。"""
        cooldown = max(0.0, _cfg_float("PB_FREEZE_COOLDOWN_SEC", 3600.0))
        until = time.time() + cooldown
        with self._lock:
            if global_scope:
                self._frozen_until = max(self._frozen_until, until)
            self._strategy_frozen_until[strategy] = max(
                self._strategy_frozen_until.get(strategy, 0.0), until
            )
        logger.warning(
            "[PortfolioBudget] %s 触发%s冻结 %ds: %s",
            strategy, "全局" if global_scope else "策略级", int(cooldown), why,
        )

    def manual_unfreeze(self, strategy: Optional[str] = None) -> None:
        """手动解除冻结（运维/测试）。strategy=None 解除全局。"""
        with self._lock:
            if strategy:
                self._strategy_frozen_until.pop(strategy, None)
            else:
                self._frozen_until = 0.0

    def status(self) -> Dict[str, Any]:
        """供监控看板/前端：预算状态与最近决策。"""
        now = time.time()
        return {
            "enabled": _cfg_bool("PB_ENABLED", True),
            "global_frozen": self._frozen_until > now,
            "global_frozen_until": self._frozen_until,
            "strategy_frozen": {
                k: v for k, v in self._strategy_frozen_until.items() if v > now
            },
            "last_decision": self._last_decision,
        }

    # ── 持仓与数据 ───────────────────────────────────────────

    def _load_positions(
        self, db, account_id: int,
        positions: Optional[Sequence[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        if positions is not None:
            return [p for p in positions if isinstance(p, dict)]
        if not db or not account_id:
            return []
        cache_key = f"pos:{account_id}"
        hit = self._cache.get(cache_key)
        if hit and time.time() - hit[0] < _cfg_float("PB_POS_CACHE_TTL_SEC", 30.0):
            return hit[1]
        try:
            from backend.services.paper_trading_engine import paper_engine
            rows = paper_engine.get_positions(db, account_id, status="open") or []
            out = [p for p in rows if isinstance(p, dict)]
        except Exception:
            out = []
        self._cache[cache_key] = (time.time(), out)
        return out

    @staticmethod
    def _concentration_pct(
        positions: List[Dict[str, Any]], symbol: str, add_notional: float, equity: float,
    ) -> float:
        """同币所有方向名义（含本单）/ 权益。"""
        sym = (symbol or "").upper()
        total = abs(float(add_notional or 0))
        for p in positions:
            if (str(p.get("symbol") or "").upper()) != sym:
                continue
            total += _pos_notional(p)
        if equity <= 0:
            return 0.0
        return total / equity

    def _daily_var_ratio(
        self,
        positions: List[Dict[str, Any]],
        symbol: str,
        action: str,
        add_notional: float,
        equity: float,
    ) -> Optional[float]:
        """历史模拟 95% 日 VaR / 权益。数据不足返回 None（该项 fail-open）。"""
        if equity <= 0:
            return None
        lookback = max(30, _cfg_int("PB_VAR_LOOKBACK_DAYS", 90))
        conf = float(_cfg_float("PB_VAR_CONFIDENCE", 0.95))
        alpha = (1.0 - conf) * 100.0  # 5.0

        # 组合名义权重：现有持仓（按 symbol 汇总净名义）+ 本单
        sym_notional: Dict[str, float] = {}
        sym_dir: Dict[str, str] = {}
        for p in positions:
            s = str(p.get("symbol") or "").upper()
            d = _pos_dir(p.get("side"))
            if not d:
                continue
            n = _pos_notional(p)
            cur = sym_notional.get(s, 0.0)
            sym_notional[s] = cur + (n if d == "long" else -n)
            sym_dir[s] = d
        add_dir = _action_dir(action)
        if add_dir:
            cur = sym_notional.get(symbol, 0.0)
            sym_notional[symbol] = cur + (abs(float(add_notional or 0)) if add_dir == "long" else -abs(float(add_notional or 0)))
            sym_dir[symbol] = add_dir

        total_notional = sum(abs(v) for v in sym_notional.values())
        if total_notional <= 0:
            return None

        # 各币 1d 收益序列（TTL 缓存），方向翻转：short 用 -r
        rets: List[np.ndarray] = []
        weights: List[float] = []
        for s, signed_n in sym_notional.items():
            r = self._daily_returns(s)
            if r is None or len(r) < 30:
                continue
            d = sym_dir.get(s, "long")
            if d == "short":
                r = -r
            rets.append(r)
            weights.append(abs(signed_n) / total_notional)

        if not rets:
            return None
        # 组合日收益 = Σ w_i × r_i（历史模拟：同期对齐取最短长度）
        n = min(len(r) for r in rets)
        combo = sum(w * r[-n:] for w, r in zip(weights, rets))
        var = float(-np.percentile(combo, alpha))
        if not np.isfinite(var):
            return None
        # 组合日最大损失比例（预算比较对象为权益比例 PB_MAX_DAILY_VAR_PCT）
        return max(0.0, var)

    def _daily_returns(self, symbol: str) -> Optional[np.ndarray]:
        """单币 1d 收益序列（缓存 600s）。"""
        sym = (symbol or "").upper()
        cache_key = f"ret1d:{sym}"
        hit = self._cache.get(cache_key)
        if hit and time.time() - hit[0] < _cfg_float("PB_KL_CACHE_TTL_SEC", 600.0):
            return hit[1]
        try:
            from backend.services.data_center import data_center
            result = data_center.get_klines(sym, "1d", count=_cfg_int("PB_VAR_LOOKBACK_DAYS", 90))
            df = result.to_dataframe()
            if df is None or len(df) < 30 or "close" not in getattr(df, "columns", []):
                return None
            close = df["close"].values.astype(float)
            r = close[1:] / close[:-1] - 1.0
            r = r[np.isfinite(r)]
            if len(r) < 30:
                return None
            self._cache[cache_key] = (time.time(), r)
            return r
        except Exception as e:
            logger.debug("[PortfolioBudget] %s 1d 收益获取失败: %s", sym, e)
            return None

    def _strategy_drawdown_sigma(
        self, strategy: str, db, account_id: int,
    ) -> Optional[float]:
        """策略历史已平仓 PnL 序列：当前回撤 / 序列 σ。数据不足返回 None。"""
        if not db or not account_id:
            return None
        cache_key = f"dd:{strategy}:{account_id}"
        hit = self._cache.get(cache_key)
        if hit and time.time() - hit[0] < _cfg_float("PB_DD_CACHE_TTL_SEC", 300.0):
            return hit[1]
        min_trades = _cfg_int("PB_MIN_TRADES_FOR_CIRCUIT", 10)
        try:
            from backend.database.models import PaperPosition
            import datetime as _dt
            cutoff = _dt.datetime.now() - _dt.timedelta(
                days=_cfg_int("PB_DD_LOOKBACK_DAYS", PB_DD_LOOKBACK_DAYS)
            )
            rows = (
                db.query(PaperPosition)
                .filter(
                    PaperPosition.account_id == account_id,
                    PaperPosition.status == "closed",
                    # [2026-08-06 修复] 时间窗：废弃数据（8 月前）不参与 σ 计算；
                    # closed_at 为空的老记录一并排除（无法确认时间的废弃数据）。
                    PaperPosition.closed_at >= cutoff,
                )
                .order_by(PaperPosition.closed_at.asc())
                .all()
            )
        except Exception as e:
            logger.debug("[PortfolioBudget] %s 历史交易查询失败: %s", strategy, e)
            return None
        pnls = []
        for r in rows:
            try:
                if not _is_strategy_pos(
                    {"trade_nature": r.trade_nature, "timeframe_tier": r.timeframe_tier},
                    strategy,
                ):
                    continue
                pnl = float(r.unrealized_pnl or 0) + float(r.partial_realized_pnl or 0)
                if np.isfinite(pnl):
                    pnls.append(pnl)
            except Exception:
                continue
        if len(pnls) < min_trades:
            return None
        arr = np.asarray(pnls, dtype=float)
        sigma = float(arr.std())
        if sigma <= 0:
            return None
        equity_curve = np.cumsum(arr)
        peak = float(np.maximum.accumulate(equity_curve)[-1])
        current_dd = float(peak - equity_curve[-1])
        dd_sigma = current_dd / sigma
        self._cache[cache_key] = (time.time(), dd_sigma)
        return dd_sigma


# 单例
portfolio_budget = PortfolioBudget()
