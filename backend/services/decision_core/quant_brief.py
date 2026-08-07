"""quant_brief — 中长线"量化简报"生成器（S2-1）。

来自实战洞察（薯条哥视频《为什么你的量化模型总也不准》核心观点）：
**技术指标只是"读数"，真正决定胜率的是多周期一致性、结构位有效性、以及数据是否完整。**
LLM 若只拿到一堆原始指标读数，容易被单周期噪声带偏、对残缺数据过度自信。

本模块把三类"元信息"压成一段简报注入 SwingAgent/TrendAgent 的 prompt，引导 LLM
在下结论前先看"证据质量"：

1. **alignment_score（多周期一致性）**：long(1d)/mid(4h)/short(15m) 偏向是否共振，
   以及 MTF 谐振分。共振越强越可信；相互打架应降低置信度或 hold。
2. **structure_levels（结构位）**：最近支撑/阻力与当前价的距离，判断是否在关键位附近
   （关键位附近的信号更有交易价值；夹在中间的信号多为噪声）。
3. **missing_data（数据完整度）**：哪些时间框架/指标缺失。数据残缺时应保守，不可满仓。

flag `MIDLONG_QUANT_BRIEF_IN_PROMPT` 门控，默认开启。生成失败返回空串（不影响决策）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def _fnum(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _alignment_section(ms: Dict[str, Any]) -> str:
    orch = ms.get("orchestrator") or {}
    mtf = ms.get("mtf_resonance") or {}
    lb = str(orch.get("long_bias") or "?")
    mb = str(orch.get("mid_bias") or "?")
    sb = str(orch.get("short_bias") or "?")
    lc = _fnum(orch.get("long_confidence") or orch.get("long_conf"), None)
    mc = _fnum(orch.get("mid_confidence") or orch.get("mid_conf"), None)
    biases = [b for b in (lb, mb, sb) if b in ("bullish", "bearish")]
    if biases:
        agree = len(set(biases)) == 1 and len(biases) >= 2
        align_txt = "共振一致" if agree else ("方向打架" if len(set(biases)) > 1 else "部分可用")
    else:
        align_txt = "偏向不明"
    parts = [
        f"多周期一致性：{align_txt}",
        f"日线={lb}" + (f"({lc:.2f})" if lc is not None else ""),
        f"4h={mb}" + (f"({mc:.2f})" if mc is not None else ""),
        f"15m={sb}",
    ]
    if mtf:
        parts.append(f"MTF谐振分={mtf.get('score')} aligned={mtf.get('aligned')}")
    return " | ".join(parts)


def _structure_section(ms: Dict[str, Any]) -> str:
    # 兼容多种结构位来源字段
    struct = ms.get("structure_levels") or ms.get("structure") or {}
    price = _fnum(ms.get("price") or ms.get("last_price") or ms.get("close"), None)
    sup = None
    res = None
    if isinstance(struct, dict):
        sup = _fnum(struct.get("support") or struct.get("nearest_support"), None)
        res = _fnum(struct.get("resistance") or struct.get("nearest_resistance"), None)
    if price and (sup or res):
        segs = []
        if sup:
            segs.append(f"支撑{sup:.4g}(距{((price - sup) / price * 100):+.1f}%)")
        if res:
            segs.append(f"阻力{res:.4g}(距{((res - price) / price * 100):+.1f}%)")
        return "结构位：" + " ".join(segs)
    return "结构位：无明确支撑/阻力数据（信号价值需谨慎）"


def _missing_section(ms: Dict[str, Any]) -> str:
    missing = []
    for tf in ("indicators_1h", "indicators_4h", "indicators_1d"):
        val = ms.get(tf)
        if not (isinstance(val, dict) and val):
            missing.append(tf.replace("indicators_", ""))
    ml = ms.get("midlong_factors") or {}
    fac_n = int(ml.get("count") or 0) if isinstance(ml, dict) else 0
    if missing:
        return f"数据完整度：⚠ 缺失 {','.join(missing)} 指标（数据残缺→保守，不可满仓）；活跃因子读数={fac_n}"
    return f"数据完整度：1h/4h/1d 指标齐全；活跃因子读数={fac_n}"


def build_quant_brief(
    symbol: str,
    market_envs: Optional[Dict[str, Any]],
    nature: str = "swing",
) -> str:
    """生成一段量化简报文本；关闭 flag 或异常时返回空串。"""
    if not bool(_cfg("MIDLONG_QUANT_BRIEF_IN_PROMPT", True)):
        return ""
    try:
        ms = {}
        if isinstance(market_envs, dict):
            ms = market_envs.get(symbol) or market_envs.get((symbol or "").upper()) or {}
        if not isinstance(ms, dict) or not ms:
            return ""
        lines = [
            "## 量化简报（先看证据质量，再下结论）",
            "> 技术指标只是读数；多周期一致性/结构位有效性/数据完整度决定这条信号是否值得交易。",
            "- " + _alignment_section(ms),
            "- " + _structure_section(ms),
            "- " + _missing_section(ms),
            "- 决策指引：多周期打架或数据残缺→降低 confidence 或 hold；仅在共振+近关键位时给高分。",
        ]
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"[QuantBrief] {symbol} 生成跳过: {e}")
        return ""
