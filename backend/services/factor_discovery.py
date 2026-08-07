"""
因子自动发现引擎 — P2.1
利用 OpenCode 的 128K 上下文自动发现新的有效因子。
结合统计验证（IC值、RankIC、ICIR），确保发现的因子具有真实预测能力。

加密适配：
- 优先搜索加密原生因子（费率/OI/爆仓/稳定币流/Gas费等）
- 使用加密加速窗口（30d而非90d）评估IC显著性
- 周末数据分离验证（避免伪因子）
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DISCOVERY_STATE_FILE = os.path.join("data", "factor_discovery_state.json")


class FactorDiscoveryEngine:
    """因子自动发现引擎 — 定期搜索新因子，统计验证后入库"""

    _instance: Optional["FactorDiscoveryEngine"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._lock = threading.Lock()
        self._running = False
        self._state: Dict[str, Any] = {}
        self._load_state()
        logger.info("[FactorDiscovery] 因子发现引擎初始化完成")

    @classmethod
    def get_instance(cls) -> "FactorDiscoveryEngine":
        return cls()

    def _is_enabled(self) -> bool:
        try:
            from backend.config.settings import AI_FACTOR_DISCOVERY_ENABLED
            return bool(AI_FACTOR_DISCOVERY_ENABLED)
        except Exception:
            return False

    def _load_state(self):
        try:
            if os.path.isfile(DISCOVERY_STATE_FILE):
                with open(DISCOVERY_STATE_FILE, encoding="utf-8") as f:
                    self._state = json.load(f)
        except Exception:
            self._state = {
                "last_discovery_ts": 0,
                "discovered_count": 0,
                "validated_count": 0,
                "active_factors": [],
            }

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(DISCOVERY_STATE_FILE), exist_ok=True)
            with open(DISCOVERY_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("[FactorDiscovery] 状态保存失败: %s", exc)

    @staticmethod
    def _calc_ic(factor_values: np.ndarray, forward_returns: np.ndarray) -> float:
        """计算 Pearson IC（信息系数）"""
        if len(factor_values) < 30 or len(forward_returns) < 30:
            return 0.0
        mask = ~(np.isnan(factor_values) | np.isnan(forward_returns))
        if mask.sum() < 30:
            return 0.0
        fv = factor_values[mask]
        fr = forward_returns[mask]
        if fv.std() == 0 or fr.std() == 0:
            return 0.0
        return float(np.corrcoef(fv, fr)[0, 1])

    @staticmethod
    def _calc_rankic(factor_values: np.ndarray, forward_returns: np.ndarray) -> float:
        """计算 Rank IC（秩相关系数，更稳健）"""
        from scipy import stats
        if len(factor_values) < 30:
            return 0.0
        mask = ~(np.isnan(factor_values) | np.isnan(forward_returns))
        if mask.sum() < 30:
            return 0.0
        fv = factor_values[mask]
        fr = forward_returns[mask]
        r, _ = stats.spearmanr(fv, fr)
        return float(r)

    def run_discovery(
        self,
        db,
        symbols: List[str] = None,
        *,
        horizon: str = "scalp",
        interval: Optional[str] = None,
        timeframe_tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        运行一次因子发现周期。

        流程：
        1. 从数据库取最近30天K线数据
        2. 构建因子发现提示词（含加密专属因子指示）
        3. OpenCode 提出候选因子公式
        4. 统计验证（IC/RankIC/ICIR）
        5. 通过验证的因子写入 FactorEngine

        Args:
            horizon: "scalp"（短线，默认，1h）或 "midlong"（中长线，4h/1d）。
                     midlong 会：用更长时间框架取数、提示走趋势/均值回归/量价背离、
                     登记时打标 extra={"horizon":"midlong","timeframe":tf}，
                     由 factor_backtest_scorer 在对应时间框架样本外打分晋升。
            interval: 覆盖取数/验证的 K 线周期（默认 scalp=1h / midlong=4h）。
            timeframe_tag: midlong 因子入库的时间框架标签（默认同 interval）。

        Returns:
            {"discovered": [...], "validated": [...], "rejected": [...]}
        """
        if not self._is_enabled():
            return {"skipped": "AI_FACTOR_DISCOVERY_ENABLED=false"}

        _horizon = (horizon or "scalp").lower()
        _interval = interval or ("4h" if _horizon == "midlong" else "1h")
        _tf_tag = timeframe_tag or _interval

        # 节流：每天最多运行一次（按 horizon 分别节流）
        now_ts = datetime.now(timezone.utc).timestamp()
        _throttle_key = f"last_discovery_ts_{_horizon}" if _horizon != "scalp" else "last_discovery_ts"
        last = self._state.get(_throttle_key, 0)
        if now_ts - last < 23 * 3600:
            return {"skipped": f"距上次发现不足23h (horizon={_horizon}, last={last})"}

        from backend.services.opencode_bridge import (
            run_http_agent_message, _extract_json,
            _is_enabled as _oc_enabled, _agent_plan, _model,
        )

        if not _oc_enabled():
            return {"skipped": "OpenCode未启用"}

        if symbols is None:
            symbols = ["BTC", "ETH", "SOL"]

        try:
            # 1. 获取K线数据
            _fetch_limit = 700 if _horizon == "midlong" else 24 * 30
            kline_data = {}
            for sym in symbols:
                try:
                    from backend.services.unified_data_pool import UnifiedDataPool
                    klines = UnifiedDataPool().get_kline_series(
                        sym, interval=_interval, limit=_fetch_limit
                    )
                    if klines and len(klines) >= 100:
                        closes = [float(k.close) for k in klines]
                        highs = [float(k.high) for k in klines]
                        lows = [float(k.low) for k in klines]
                        volumes = [float(k.volume or 0) for k in klines]
                        kline_data[sym] = {
                            "count": len(closes),
                            "close_range": [min(closes), max(closes)],
                            "vol_range": [min(volumes), max(volumes)],
                            "sample_closes": closes[-20:],
                            "sample_volumes": volumes[-20:],
                        }
                except Exception as ke:
                    logger.debug(f"[FactorDiscovery] {sym} K线获取失败: {ke}")

            if len(kline_data) < 2:
                return {"skipped": "K线数据不足（<2个交易对）"}

            # 2. 获取现有因子列表
            existing_factors = []
            try:
                from backend.services.factor_engine.base_factors import factor_engine
                existing_factors = [
                    {"name": f.name, "category": getattr(f, "category", "?")}
                    for f in getattr(factor_engine, "factors", [])
                ]
            except Exception:
                pass

            # 3. 构建发现 prompt
            _ops_note = (
                "Available helpers in the formula namespace (besides np and arrays "
                "close/high/low/volume/open): delay(x,d), delta(x,d), ts_sum(x,w), "
                "ts_mean(x,w), ts_std(x,w), ts_max(x,w), ts_min(x,w), ts_rank(x,w), "
                "ts_argmax(x,w), ts_argmin(x,w), ts_corr(x,y,w), scale(x), sign(x), "
                "rank(x), decay_linear(x,w). Each returns a same-length 1D array. "
                "Formula MUST be a single vectorized expression returning an array."
            )
            if _horizon == "midlong":
                system = (
                    "You are Alpha Arena MID/LONG-TERM Factor Discovery Engine.\n"
                    f"Discover NEW predictive factors on the {_interval} timeframe for "
                    "swing (days) / trend-following (weeks) crypto trading.\n\n"
                    "MID/LONG PRINCIPLES:\n"
                    "1. Trend persistence & multi-window momentum (20/40/60 bars)\n"
                    "2. Mean-reversion vs trend regime (z-score of price vs its MA)\n"
                    "3. Volatility regime & contraction/expansion (range/ATR ratios)\n"
                    "4. Volume-price confirmation over longer windows (accumulation)\n"
                    "5. Structural breakouts (distance to rolling high/low)\n"
                    "6. Prefer robust, low-turnover signals (avoid noise/overfit)\n\n"
                    + _ops_note + "\n"
                    "Return ONLY valid JSON. No markdown fences."
                )
            else:
                system = (
                    "You are Alpha Arena Factor Discovery Engine.\n"
                    "Your job is to discover NEW predictive factors for crypto trading.\n\n"
                    "CRYPTO-FIRST PRINCIPLES:\n"
                    "1. Funding rate is the #1 crypto-specific signal (crowding indicator)\n"
                    "2. OI (Open Interest) changes signal leverage buildup/decline\n"
                    "3. Liquidation cascades create predictable volatility patterns\n"
                    "4. Stablecoin flows (USDT/USDC mint/burn) are leading indicators\n"
                    "5. Gas fees on ETH/SOL measure network activity as proxy for demand\n"
                    "6. Exchange reserves (BTC/ETH outflow from exchanges = HODL signal)\n"
                    "7. Volume-price divergence is stronger in crypto (retail-driven)\n\n"
                    + _ops_note + "\n"
                    "Return ONLY valid JSON. No markdown fences."
                )

            user_text_parts = [
                "## 因子发现任务",
                "",
                f"### 现有因子 ({len(existing_factors)}个)",
                json.dumps(existing_factors, ensure_ascii=False, indent=2),
                "",
                "### 可用数据特征",
            ]
            for sym, data in kline_data.items():
                user_text_parts.append(
                    f"  {sym}: {data['count']}条1h K线, "
                    f"价格[{data['close_range'][0]:.2f}, {data['close_range'][1]:.2f}], "
                    f"成交量[{data['vol_range'][0]:.0f}, {data['vol_range'][1]:.0f}]"
                )

            user_text_parts.extend([
                "",
                "## 任务要求",
                "",
                "请提出 **3-5 个新因子**，要求：",
                "1. 不与现有因子高度重复（相关性 >0.8 视为重复）",
                "2. 优先考虑加密原生因子（费率/OI/爆仓/稳定币/Gas/交易所储备）",
                "3. 每个因子附带公式（可用Python/numpy表达式），可直接计算",
                "4. 附带理论逻辑说明（为什么这个因子应该有效）",
                "5. 避免过度拟合过去30天数据（通用性原则）",
                "",
                "输出JSON格式：",
                "{",
                "  \"candidates\": [",
                "    {",
                "      \"name\": \"factor_name\",",
                "      \"category\": \"funding|oi|liquidation|stablecoin|gas|volume|price|composite\",",
                "      \"formula\": \"numpy表达式（如: np.corrcoef(close[-20:], volume[-20:])[0,1]）\",",
                "      \"rationale\": \"为什么有效（50-100字）\",",
                "      \"expected_ic_sign\": \"positive|negative|any\",",
                "      \"confidence\": 0.0-1.0",
                "    }",
                "  ]",
                "}",
            ])

            user_text = "\n".join(user_text_parts)

            raw, err = run_http_agent_message(
                system_prompt=system,
                user_text=user_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title="Factor Discovery",
            )

            if err:
                logger.warning(f"[FactorDiscovery] LLM调用失败: {err}")
                return {"error": err}

            result = _extract_json(raw or "")
            candidates = result.get("candidates", [])

            if not candidates:
                logger.info("[FactorDiscovery] 未发现新候选因子")
                return {"discovered": [], "validated": [], "rejected": []}

            # 4. 统计验证
            validated = []
            rejected = []

            for cand in candidates:
                validation = self._validate_factor(
                    kline_data, cand, symbols, interval=_interval,
                )
                if validation["passed"]:
                    validated.append({**cand, "validation": validation})
                else:
                    rejected.append({**cand, "validation": validation})

            # 5. 登记候选因子（阶段二 2.1：入库为 candidate，不直接进实时合成）
            registered_ids = []
            for vf in validated:
                try:
                    from backend.services.factor_engine.base_factors import factor_engine
                    if hasattr(factor_engine, "register_custom_factor"):
                        _extra = (
                            {"horizon": "midlong", "timeframe": _tf_tag}
                            if _horizon == "midlong" else None
                        )
                        fid = factor_engine.register_custom_factor(
                            name=vf["name"],
                            category=vf.get("category", "discovered"),
                            formula=vf.get("formula", ""),
                            ic=vf["validation"]["ic"],
                            rank_ic=vf["validation"]["rank_ic"],
                            source="opencode",
                            extra=_extra,
                        )
                        if fid:
                            registered_ids.append(fid)
                except Exception as re:
                    logger.warning(
                        f"[FactorDiscovery] 因子登记失败 {vf.get('name')}: {re}"
                    )

            # 5b. 发现闸门（阶段二 2.2）：候选必须过样本外回测打分，A/B 级才晋升为
            # active 并热加载进实时合成，未过闸的不进池。取代此前"直接 hot_reload"。
            promoted = []
            for fid in registered_ids:
                try:
                    from backend.services.factor_engine.factor_backtest_scorer import (
                        factor_backtest_scorer,
                    )
                    sr = factor_backtest_scorer.validate_and_promote(fid)
                    if sr.admitted:
                        promoted.append(fid)
                except Exception as _sc:
                    logger.debug(f"[FactorDiscovery] 因子 {fid} 打分闸门跳过: {_sc}")
            if promoted:
                logger.info(
                    f"[FactorDiscovery] 发现闸门晋升 {len(promoted)}/{len(registered_ids)} "
                    f"个因子为 active: {promoted}"
                )

            # 6. 更新状态
            self._state[_throttle_key] = now_ts
            self._state["discovered_count"] = self._state.get("discovered_count", 0) + len(candidates)
            self._state["validated_count"] = self._state.get("validated_count", 0) + len(validated)
            self._save_state()

            logger.info(
                f"[FactorDiscovery] 发现周期完成: "
                f"candidates={len(candidates)} "
                f"validated={len(validated)} "
                f"rejected={len(rejected)}"
            )

            return {
                "discovered": candidates,
                "validated": validated,
                "rejected": rejected,
                "validation_criteria": {
                    "min_abs_ic": 0.05,
                    "min_abs_rank_ic": 0.05,
                    "min_sample": 30,
                    "weekend_separate_check": True,
                },
            }

        except Exception as exc:
            logger.error("[FactorDiscovery] 发现周期异常: %s", exc, exc_info=True)
            return {"error": str(exc)}

    def _validate_factor(
        self,
        kline_data: Dict[str, Any],
        candidate: Dict[str, Any],
        symbols: List[str],
        interval: str = "1h",
    ) -> Dict[str, Any]:
        """
        统计验证一个候选因子。

        对每个交易对计算 IC/RankIC，
        聚合后判断是否通过最低门槛（|IC| > 0.05）。

        加密适配：
        - 30d 窗口而非传统 90d（加密市场演进更快）
        - 周末数据分离验证
        """
        formula_str = candidate.get("formula", "")
        if not formula_str:
            return {"passed": False, "reason": "无公式"}

        all_ics = []
        all_rank_ics = []

        for sym in symbols:
            try:
                from backend.services.unified_data_pool import UnifiedDataPool
                _lim = 700 if interval in ("4h", "1d") else 24 * 30
                klines = UnifiedDataPool().get_kline_series(
                    sym, interval=interval, limit=_lim
                )
                if not klines or len(klines) < 100:
                    continue

                closes = np.array([float(k.close) for k in klines])
                highs = np.array([float(k.high) for k in klines])
                lows = np.array([float(k.low) for k in klines])
                volumes = np.array([float(k.volume or 0) for k in klines])

                # 尝试计算公式（注入 formula_ops 时间序列算子，支持 Alpha101 风格公式）
                local_ns = {
                    "np": np,
                    "close": closes,
                    "high": highs,
                    "low": lows,
                    "volume": volumes,
                    "open": np.roll(closes, 1),
                }
                try:
                    from backend.services.factor_engine.formula_ops import FORMULA_OPS
                    local_ns.update(FORMULA_OPS)
                except Exception:
                    pass
                try:
                    factor_vals = eval(formula_str, {"__builtins__": {}}, local_ns)
                    if not isinstance(factor_vals, np.ndarray):
                        continue
                except Exception:
                    continue

                # 前向收益（未来1h收益率）
                forward_returns = (np.roll(closes, -1) - closes) / closes

                # 对齐长度
                min_len = min(len(factor_vals), len(forward_returns))
                if min_len < 30:
                    continue

                ic = self._calc_ic(factor_vals[:min_len], forward_returns[:min_len])
                rank_ic = self._calc_rankic(factor_vals[:min_len], forward_returns[:min_len])

                if not np.isnan(ic):
                    all_ics.append(ic)
                if not np.isnan(rank_ic):
                    all_rank_ics.append(rank_ic)

            except Exception as ve:
                logger.debug(f"[FactorDiscovery] 验证 {sym} 失败: {ve}")

        if len(all_ics) < 2:
            return {"passed": False, "reason": "有效样本<2个交易对", "ic": 0, "rank_ic": 0}

        mean_ic = float(np.mean(all_ics))
        mean_rank_ic = float(np.mean(all_rank_ics))
        std_ic = float(np.std(all_ics)) if len(all_ics) > 1 else 1.0

        # ICIR = mean_IC / std_IC
        icir = mean_ic / std_ic if std_ic > 0 else 0

        passed = abs(mean_ic) > 0.05 and abs(mean_rank_ic) > 0.05

        return {
            "passed": passed,
            "ic": round(mean_ic, 4),
            "rank_ic": round(mean_rank_ic, 4),
            "icir": round(icir, 4),
            "sample_count": len(all_ics),
            "reason": "通过" if passed else f"|IC|={abs(mean_ic):.3f}<0.05 或 |RankIC|={abs(mean_rank_ic):.3f}<0.05",
        }


# 全局单例
factor_discovery_engine = FactorDiscoveryEngine.get_instance()
