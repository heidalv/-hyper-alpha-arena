"""narrative_tag — LLM 叙事标签 → 板块胜率表（设计总方案 B6，2026-08-19）。

- suggest_narrative_tags：LLM 从宏观日历/新闻产出叙事标签（AI/DeFi/RWA/降息/ETF/监管），
  可选（LLM_NARRATIVE_TAG=1），失败静默——标签只做 L1/L2 加权/过滤参考，绝不直接下单。
- sector_perf_table：规则侧统计「标签→板块近 20 日收益」胜率表（骨架，板块映射缺省用核心币）。
纯非交易路径。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SECTOR_PROXY = {
    "AI": ["VIRTUAL", "WLD", "FET"],
    "DeFi": ["UNI", "AAVE", "LINK"],
    "L1": ["BTC", "ETH", "SOL", "BNB"],
    "MEME": ["DOGE", "SHIB"],
}


def suggest_narrative_tags(db=None) -> Dict[str, Any]:
    """LLM 叙事标签（可选）。返回 {tags: [...], llm: bool}。"""
    out: Dict[str, Any] = {"tags": [], "llm": False}
    try:
        import os
        if os.getenv("LLM_NARRATIVE_TAG", "0").strip().lower() not in ("1", "true", "yes", "on"):
            return out
        from backend.services.llm_config_service import get_llm_config_for_account, call_llm_api_sync
        cfg = get_llm_config_for_account(0)
        if not cfg:
            return out
        prompt = (
            "你是加密市场叙事分析师。根据近期宏观与加密市场环境，给出当前最值得关注的"
            " 3 个市场叙事标签（如 AI/DeFi/RWA/降息/ETF/监管/减半周期），每个标签一行，"
            "只输出标签名，不要解释。"
        )
        text = call_llm_api_sync(cfg, [{"role": "user", "content": prompt}], caller="NarrativeTag")
        tags = [t.strip() for t in str(text or "").splitlines() if t.strip()][:3]
        out = {"tags": tags, "llm": bool(tags)}
    except Exception as e:
        logger.debug("[NarrativeTag] LLM 标签生成跳过: %s", e)
    return out


def sector_perf_table(db, days: int = 20) -> Dict[str, Any]:
    """板块近 days 日收益表（代理映射；数据缺失安全返回空）。"""
    out: Dict[str, Any] = {"window_days": days, "sectors": {}}
    try:
        from backend.services.kline_data_service import kline_service
        for sector, syms in _SECTOR_PROXY.items():
            rets = []
            for sym in syms:
                try:
                    kl = kline_service.get_klines_from_db(sym, "1d", days + 1)
                    if not kl or len(kl) < 2:
                        continue
                    import pandas as pd
                    closes = pd.to_numeric(pd.DataFrame(kl)["close"], errors="coerce").dropna()
                    if len(closes) >= 2:
                        rets.append(float(closes.iloc[-1] / closes.iloc[0] - 1))
                except Exception:
                    continue
            if rets:
                out["sectors"][sector] = {
                    "n": len(rets),
                    "mean_ret": round(float(sum(rets) / len(rets)), 4),
                }
    except Exception as e:
        logger.debug("[NarrativeTag] 板块收益表失败: %s", e)
    return out
