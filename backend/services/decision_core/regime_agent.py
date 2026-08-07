"""RegimeAgent — 市场状态三态判定（趋势 / 震荡 / 极端）。

确定性规则，不调用 LLM。与旧 entry_confidence_gate 的关键差异：
旧逻辑在震荡市**降低**开仓门槛（35%），本模块反向修正 —
震荡市收紧门槛（噪声多、手续费侵蚀重灾区），极端市只允许减仓。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RegimeResult:
    regime: str            # "trend" / "ranging" / "extreme" / "unknown"
    gate_adjust: int       # 在基础置信度门槛上的加点（正数=收紧）
    allow_open: bool       # 极端态禁止新开仓
    size_multiplier: float = 1.0  # 震荡/不确定时缩仓而非一律 block
    detail: str = ""

    def prompt_hint(self) -> str:
        labels = {
            "trend": "趋势市 — 顺势交易可正常评估",
            "ranging": "震荡市 — 可评估但建议缩仓（size×0.5），需结构突破才开",
            "extreme": "极端行情 — 禁止新开仓，只允许减仓/防守",
            "unknown": "状态不明 — 按保守处理，缩仓评估",
        }
        return labels.get(self.regime, labels["unknown"])


def classify_regime(market_data: dict) -> RegimeResult:
    """从单 symbol 市场摘要分类市场状态。

    输入字段（缺失时按保守取值）：
      price_change_1h_pct / price_change_24h_pct / volatility_pct
    """
    if not isinstance(market_data, dict):
        return RegimeResult("unknown", 0, True, 0.75, "no market data")

    def _f(key: str, default: float = 0.0) -> float:
        try:
            return float(market_data.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    chg_1h = _f("price_change_1h_pct")
    chg_24h = _f("price_change_24h_pct")
    vol = _f("volatility_pct", _f("volatility", 0.0))
    # 容错：有的链路 volatility 给 0~1 小数，有的给百分数
    if vol > 1.0:
        vol /= 100.0

    # ── 极端态：大幅单边或波动率爆表 → 禁止新开仓 ──
    # [P5-修复] 不能仅凭单值 vol 判 extreme：volatility_pct 存在量纲混乱——
    # 主链路给 per-bar 小数(ATR/price≈0.01~0.05)、个别链路给百分数(>1)、
    # regime_refined 侧约定年化小数(0.6~2.0)。年化 60%~200% 是加密常态，
    # 若被误当 per-bar 会触发 vol>=0.05 → extreme → 长线禁开（误伤）。
    # 因此 extreme 必须由 price_change 佐证真实单边行情；仅高 vol 无佐证时
    # 回落到 trend/ranging 分支（高波动不知方向 → 缩仓评估，语义不变）。
    if abs(chg_24h) >= 12.0 or abs(chg_1h) >= 5.0:
        return RegimeResult(
            "extreme", 100, False, 0.0,
            f"24h={chg_24h:+.1f}% 1h={chg_1h:+.1f}% vol={vol:.3f}",
        )
    if vol >= 0.05 and abs(chg_24h) >= 4.0:
        return RegimeResult(
            "extreme", 100, False, 0.0,
            f"24h={chg_24h:+.1f}% vol={vol:.3f}",
        )

    # ── 趋势态：24h 有明确方向且 1h 同向 ──
    if abs(chg_24h) >= 4.0 and (chg_1h == 0 or chg_1h * chg_24h > 0):
        return RegimeResult(
            "trend", 0, True, 1.0,
            f"24h={chg_24h:+.1f}% 1h={chg_1h:+.1f}%",
        )

    # ── 震荡态：缩仓而非抬高门槛（行业 block→scale）──
    return RegimeResult(
        "ranging", 0, True, 0.5,
        f"24h={chg_24h:+.1f}% 1h={chg_1h:+.1f}% vol={vol:.3f}",
    )
