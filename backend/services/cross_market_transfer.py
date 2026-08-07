"""
跨市场/跨品种知识迁移引擎 — P3.2
将在一个交易对上学到的成功模式、因子关系、策略模板迁移到其他交易对。

加密适配：
- BTC 作为"锚定资产"：山寨币策略必须考虑 BTC 相关性（β值）
- 稳定币流动作为跨品种信号：USDT 净流入 → 整体市场情绪
- 交易所间套利模式 → 不同交易所的流动性差异模式
- 同赛道品种联动：Layer1 之间、DeFi 之间共享相似行为模式

迁移机制：
1. 直接迁移：同类型品种（如 Layer1→Layer1）
2. 映射迁移：不同类型需做参数映射（如 volatility_adjust）
3. 禁止迁移：β 值不稳定的跨赛道品种
"""

from __future__ import annotations

import json
import logging
import threading
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── 品种分组（同组内可直接迁移）──
SYMBOL_GROUPS: Dict[str, List[str]] = {
    "layer1": ["BTC", "ETH", "SOL", "BNB", "AVAX"],  # 不用 FTM/NEAR → 非 Hyperliquid 主战场
    "defi": ["UNI", "AAVE", "LINK", "MKR", "CRV"],
    "meme": ["DOGE", "PEPE"],
    "l2": ["ARB", "OP", "MATIC"],
    "oracle": ["LINK", "PYTH"],
}

# ── 迁移衰减系数（跨组迁移权重衰减）──
TRANSFER_DECAY: Dict[str, Dict[str, float]] = {
    "layer1_decentralized": {"layer1": 1.0, "defi": 0.7, "l2": 0.5, "meme": 0.2},
    "defi": {"defi": 1.0, "layer1": 0.6, "l2": 0.6, "meme": 0.2},
    "l2": {"l2": 1.0, "defi": 0.6, "layer1": 0.5, "meme": 0.2},
    "meme": {"meme": 1.0, "layer1": 0.1, "defi": 0.1, "l2": 0.1},  # meme 高度独立
}

# ── BTC 锚定阈值 ──
BTC_ANCHOR_THRESHOLD_BETA = 0.75  # β > 0.75 → BTC走势主导该品种
BTC_ANCHOR_THRESHOLD_BETA_LOW = 0.30  # β < 0.30 → 该品种高度独立（如某些 meme）


class CrossMarketTransfer:
    """跨市场/跨品种知识迁移引擎"""

    _instance: Optional["CrossMarketTransfer"] = None

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
        self._transfer_history: List[Dict[str, Any]] = []
        self._btc_beta_cache: Dict[str, float] = {}
        logger.info("[CrossMarketTransfer] 跨市场知识迁移引擎初始化完成")

    @classmethod
    def get_instance(cls) -> "CrossMarketTransfer":
        return cls()

    def _is_enabled(self) -> bool:
        try:
            from backend.config.settings import AI_CROSS_MARKET_TRANSFER_ENABLED
            return bool(AI_CROSS_MARKET_TRANSFER_ENABLED)
        except Exception:
            return False

    def get_symbol_group(self, symbol: str) -> str:
        """获取品种所属分组"""
        for group, symbols in SYMBOL_GROUPS.items():
            if symbol.upper() in [s.upper() for s in symbols]:
                return group
        return "unknown"

    def get_transfer_decay(self, source_group: str, target_group: str) -> float:
        """获取跨组迁移衰减系数"""
        if source_group == target_group:
            return 1.0
        group_map = TRANSFER_DECAY.get(source_group, {})
        return group_map.get(target_group, 0.3)  # 未知组默认 0.3

    def transfer_strategy_template(
        self,
        db,
        *,
        source_symbol: str,
        target_symbol: str,
        template_id: str,
    ) -> Dict[str, Any]:
        """
        将 source 的交易策略模板迁移到 target。

        迁移步骤：
        1. 校验品种分组的可迁移性
        2. 计算 BTC-beta 差异
        3. 调整策略参数（volatility scaling）
        4. 创建 target 的策略模板
        5. 记录迁移历史

        Returns:
            {
                "transferred": bool,
                "new_template_id": str,
                "adjustments_applied": [...],
                "confidence": float,
            }
        """
        if not self._is_enabled():
            return {"skipped": "AI_CROSS_MARKET_TRANSFER_ENABLED=false"}

        try:
            from backend.database.models import StrategyTemplate, StrategyMemory
            from backend.services.unified_data_pool import UnifiedDataPool

            # 1. 获取源模板
            tpl = db.query(StrategyTemplate).filter(
                StrategyTemplate.template_id == template_id
            ).first()
            if not tpl:
                return {"error": f"模板不存在: {template_id}"}

            source_group = self.get_symbol_group(source_symbol)
            target_group = self.get_symbol_group(target_symbol)

            # 2. 计算衰减系数
            decay = self.get_transfer_decay(source_group, target_group)
            if decay < 0.3:
                return {
                    "transferred": False,
                    "reason": f"{source_group}→{target_group} 跨组衰减过大 ({decay:.0%})，禁止迁移",
                }

            # 3. 计算 BTC-beta 差异
            beta_source = self._get_btc_beta(source_symbol)
            beta_target = self._get_btc_beta(target_symbol)
            beta_ratio = beta_target / max(beta_source, 0.01)

            adjustments: List[Dict[str, Any]] = []

            # 4. 波动率调整
            source_vol = self._get_volatility(source_symbol)
            target_vol = self._get_volatility(target_symbol)
            vol_ratio = target_vol / max(source_vol, 0.001)
            adjustments.append({
                "type": "volatility_scaling",
                "source_vol": round(source_vol, 4),
                "target_vol": round(target_vol, 4),
                "ratio": round(vol_ratio, 4),
            })

            # 5. 参数调整
            new_config = dict(getattr(tpl, "config", {}) or {})

            # SL/TP 按波动率缩放
            if "stop_loss_pct" in new_config:
                new_config["stop_loss_pct"] = round(
                    float(new_config["stop_loss_pct"]) * vol_ratio, 4
                )
            if "take_profit_pct" in new_config:
                new_config["take_profit_pct"] = round(
                    float(new_config["take_profit_pct"]) * vol_ratio, 4
                )

            # 仓位按 β 调整（高 β → 实际风险更大 → 降低仓位）
            if beta_ratio > 1.5:
                if "position_pct" in new_config:
                    new_config["position_pct"] = round(
                        float(new_config["position_pct"]) / beta_ratio, 4
                    )
                adjustments.append({
                    "type": "beta_position_scaling",
                    "beta_ratio": round(beta_ratio, 4),
                    "reason": f"target β({beta_target:.2f}) >> source({beta_source:.2f})",
                })

            # 资金费率差异（加密专属）
            try:
                pool = UnifiedDataPool()
                snap = pool.get_snapshot(max_age=30)
                if snap and hasattr(snap, 'indicators'):
                    ind = snap.indicators.get(target_symbol, {})
                    funding = float(ind.get("funding_rate", 0) or 0)
                    if abs(funding) > 0.001:
                        adjustments.append({
                            "type": "funding_rate_warning",
                            "rate": round(funding, 6),
                            "note": "高费率 → 注意短期持仓成本" if abs(funding) > 0.0005
                                     else "中等费率 → 正常迁移",
                        })
            except Exception:
                pass

            # 6. 创建新模板
            import uuid
            new_id = f"{template_id}_xfer_{target_symbol}_{uuid.uuid4().hex[:6]}"

            try:
                new_tpl = StrategyTemplate(
                    template_id=new_id,
                    name=f"{tpl.name}→{target_symbol}",
                    symbol=target_symbol,
                    tier=getattr(tpl, "tier", "mid"),
                    config=new_config,
                    source_template_id=template_id,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(new_tpl)
                db.commit()
            except Exception as ce:
                db.rollback()
                return {"error": f"创建目标模板失败: {ce}"}

            # 7. 记录迁移历史
            transfer_entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "source": source_symbol,
                "target": target_symbol,
                "template_id": template_id,
                "new_template_id": new_id,
                "decay": decay,
                "adjustments": adjustments,
                "confidence": round(decay * min(1.0, 1.0 / max(beta_ratio, 1.0)), 4),
            }
            self._transfer_history.append(transfer_entry)
            self._transfer_history = self._transfer_history[-50:]

            logger.info(
                f"[CrossMarketTransfer] 迁移 {template_id}: "
                f"{source_symbol}({source_group})→{target_symbol}({target_group}) "
                f"decay={decay:.0%} confidence={transfer_entry['confidence']:.0%}"
            )

            return {
                "transferred": True,
                "new_template_id": new_id,
                "adjustments_applied": adjustments,
                "confidence": transfer_entry["confidence"],
                "decay": decay,
            }

        except Exception as exc:
            logger.error(f"[CrossMarketTransfer] 迁移失败: {exc}", exc_info=True)
            return {"error": str(exc)}

    def transfer_learned_patterns(
        self,
        db,
        *,
        source_symbol: str,
        target_symbol: str,
    ) -> Dict[str, Any]:
        """
        将 source 品种学到的交易模式转录到 target。
        使用 OpenCode 分析两个品种的异同，完成知识迁移。
        """
        from backend.services.opencode_bridge import (
            run_http_agent_message, _extract_json,
            _is_enabled, _agent_plan, _model,
        )

        if not _is_enabled():
            return {"skipped": "OpenCode未启用"}

        try:
            from backend.database.models import StrategyMemory

            # 收集 source 的学习经验
            source_memories = (
                db.query(StrategyMemory)
                .filter(StrategyMemory.strategy_id.like(f"%{source_symbol}%"))
                .limit(10)
                .all()
            )
            lessons = []
            for m in source_memories:
                for l in (m.key_lessons or [])[-5:]:
                    if isinstance(l, dict):
                        lessons.append(l)

            if not lessons:
                return {"transferred": False, "reason": "无可迁移的经验"}

            # 构建迁移 prompt
            system = (
                "You are Alpha Arena Cross-Market Knowledge Transfer Engine.\n"
                "Given learned trading patterns on one crypto asset, "
                "translate them to another asset considering:\n"
                "1. Volatility differences (scale SL/TP accordingly)\n"
                "2. BTC-beta differences\n"
                "3. Liquidity profile differences\n"
                "4. Asset-class specific behaviors (Layer1 vs DeFi vs Meme)\n\n"
                "Return ONLY valid JSON."
            )

            user_text = json.dumps({
                "source": source_symbol,
                "target": target_symbol,
                "lessons_to_transfer": lessons[-10:],
                "source_beta": self._get_btc_beta(source_symbol),
                "target_beta": self._get_btc_beta(target_symbol),
            }, ensure_ascii=False, indent=2)

            raw, err = run_http_agent_message(
                system_prompt=system,
                user_text=user_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title=f"Knowledge Transfer: {source_symbol}→{target_symbol}",
            )

            if err:
                return {"error": err}

            result = _extract_json(raw or "")
            return {
                "transferred": True,
                "adapted_patterns": result.get("adapted_patterns", []),
                "recommendations": result.get("recommendations", ""),
            }

        except Exception as exc:
            logger.error(f"[CrossMarketTransfer] 模式迁移失败: {exc}")
            return {"error": str(exc)}

    # ── 辅助方法 ──

    def _get_btc_beta(self, symbol: str) -> float:
        """计算品种对 BTC 的 β 值（简化版）"""
        if symbol.upper() == "BTC":
            return 1.0

        cached = self._btc_beta_cache.get(symbol)
        if cached and _time.time() - self._btc_beta_cache.get(f"{symbol}_ts", 0) < 3600:
            return cached

        try:
            from backend.services.unified_data_pool import UnifiedDataPool
            pool = UnifiedDataPool()
            sym_klines = pool.get_kline_series(symbol, interval="4h", limit=180)
            btc_klines = pool.get_kline_series("BTC", interval="4h", limit=180)

            if not sym_klines or not btc_klines or len(sym_klines) < 30:
                return 0.5

            sym_returns = []
            btc_returns = []
            for i in range(1, min(len(sym_klines), len(btc_klines))):
                s_close = float(sym_klines[i].close)
                s_prev = float(sym_klines[i - 1].close)
                b_close = float(btc_klines[i].close)
                b_prev = float(btc_klines[i - 1].close)
                if s_prev > 0 and b_prev > 0:
                    sym_returns.append((s_close - s_prev) / s_prev)
                    btc_returns.append((b_close - b_prev) / b_prev)

            if len(sym_returns) < 20:
                return 0.5

            beta = float(np.cov(sym_returns, btc_returns)[0, 1] / np.var(btc_returns))
            beta = max(-1.0, min(3.0, beta))
            self._btc_beta_cache[symbol] = beta
            self._btc_beta_cache[f"{symbol}_ts"] = _time.time()
            return beta
        except Exception:
            return 0.5

    def _get_volatility(self, symbol: str) -> float:
        """获取品种的日波动率"""
        try:
            from backend.services.unified_data_pool import UnifiedDataPool
            pool = UnifiedDataPool()
            klines = pool.get_kline_series(symbol, interval="1h", limit=168)
            if not klines or len(klines) < 24:
                return 0.02
            closes = [float(k.close) for k in klines]
            returns = [(closes[i] - closes[i - 1]) / closes[i - 1]
                       for i in range(1, len(closes))]
            return float(np.std(returns) * np.sqrt(24))
        except Exception:
            return 0.02

    def get_status(self) -> Dict[str, Any]:
        return {
            "transfers_total": len(self._transfer_history),
            "recent_transfers": self._transfer_history[-5:],
            "btc_beta_cache": {
                k: v for k, v in self._btc_beta_cache.items()
                if not k.endswith("_ts")
            },
        }


# 全局单例
cross_market_transfer = CrossMarketTransfer.get_instance()
