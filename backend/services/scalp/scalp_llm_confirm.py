# -*- coding: utf-8 -*-
"""scalp_llm_confirm — 本地 LLM 短线信号确认层（2026-08-24 短线深挖 I）。

背景：
- 7 天 8 万笔已结算信号实证：因子分几乎不能预测 30 分钟方向（各分桶净收益为负、
  非单调），短线信号层无方向 alpha；而中线（LLM 驱动）盈利——判断质量差异在 LLM。
- 用户指示「本地 LLM 最大化利用」：本层在信号通过全部规则闸门、即将下单前，
  用本地 Ollama（qwen3 14B/35B，think:false 快速通道）做一次 JSON 裁决。
- 安全设计：任何异常/超时/解析失败一律 fail-open 放行（模拟盘），绝不因本层
  停摆短线；LLM 裁决只拦「它明确否掉的」信号，且每次裁决写入 features_json
  （llm_confirm 字段），信号结算后可直接对比 confirm/reject 两组胜率——LLM
  是否真有边际是可测量的，不是信仰。
- 回滚：SCALP_LLM_CONFIRM_ENABLED=0 完全关闭本层。
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_USAGE = "scalp_confirm"
_CONFIRM_TIMEOUT_S = float(os.getenv("SCALP_LLM_CONFIRM_TIMEOUT_S", "10") or 10)
_MAX_PROMPT_KLINE_ROWS = int(os.getenv("SCALP_LLM_CONFIRM_KLINE_ROWS", "6") or 6)


def _enabled() -> bool:
    v = os.getenv("SCALP_LLM_CONFIRM_ENABLED", "true")
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _build_prompt(
    *,
    symbol: str,
    side: str,
    score: float,
    sl_pct: float,
    tp_pct: float,
    is_mr: bool,
    regime: str,
    funding: float,
    kline_summary: str,
) -> str:
    side_cn = "做多" if side in ("long", "buy") else "做空"
    strat = "震荡均值回归(区间高抛低吸)" if is_mr else "趋势短线"
    return (
        "你是加密货币短线交易裁决员。下面是一个已经通过全部风控闸门的短线信号，"
        "请判断是否放行。只输出一行 JSON，不要解释。\n"
        f"币种={symbol} 方向={side_cn} 策略={strat} 市场regime={regime}\n"
        f"因子分={score:.0f} 止损={sl_pct:.2%} 止盈={tp_pct:.2%} 资金费率={funding:.5f}\n"
        f"近{_MAX_PROMPT_KLINE_ROWS}根K线收盘价序列:\n{kline_summary}\n"
        'JSON 格式：{"confirm": true/false, "reason": "<=20字"}\n'
        "规则：价格结构/动量与方向明显矛盾→false；证据不足→true（模拟盘以积累样本优先）。"
    )


def _extract_kline_summary(market_data: Optional[Dict[str, Any]]) -> str:
    try:
        klines = (market_data or {}).get("klines")
        if klines is None:
            return "(无K线)"
        import pandas as pd
        df = klines if isinstance(klines, pd.DataFrame) else pd.DataFrame(klines)
        if df.empty or "close" not in df.columns:
            return "(无K线)"
        tail = df.tail(_MAX_PROMPT_KLINE_ROWS)
        rows = []
        for _, r in tail.iterrows():
            try:
                rows.append(f"{float(r.get('close', 0)):.5g}")
            except Exception:
                continue
        return ", ".join(rows) if rows else "(无K线)"
    except Exception:
        return "(K线解析失败)"


def scalp_llm_confirm(
    *,
    symbol: str,
    side: str,
    score: float,
    sl_pct: float,
    tp_pct: float,
    is_mr: bool = False,
    regime: str = "unknown",
    funding: float = 0.0,
    market_data: Optional[Dict[str, Any]] = None,
    account_id: int = 0,
) -> Dict[str, Any]:
    """返回 {"confirmed": bool, "source": str, "reason": str, "elapsed": float}。

    confirmed=True 表示放行（含 fail-open）。任何异常都 fail-open。
    """
    t0 = time.time()
    _fail_open = {"confirmed": True, "source": "fail_open", "reason": "", "elapsed": 0.0}
    if not _enabled():
        return {"confirmed": True, "source": "disabled", "reason": "", "elapsed": 0.0}
    try:
        from backend.services.llm_config_service import (
            get_llm_config_local_first,
            call_llm_api_sync,
        )
        local, cloud = get_llm_config_local_first(_USAGE, account_id=account_id or None)
        cfg = local or cloud
        if cfg is None:
            return _fail_open
        prompt = _build_prompt(
            symbol=symbol, side=side, score=score, sl_pct=sl_pct, tp_pct=tp_pct,
            is_mr=is_mr, regime=regime, funding=funding,
            kline_summary=_extract_kline_summary(market_data),
        )
        resp = call_llm_api_sync(
            cfg,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=160,
            response_format={"type": "json_object"},
            timeout=_CONFIRM_TIMEOUT_S,
            caller="scalp_confirm",
            account_id=account_id or None,
            bypass_cache=True,  # 每币独立裁决，禁跨币语义缓存误命中
            fallback_config=cloud,
        )
        content = str((resp or {}).get("content") or "")
        if not content:
            # _call_ollama_generate 返回 OpenAI 兼容格式（choices[0].message.content）
            try:
                content = str(
                    ((resp or {}).get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
                )
            except Exception:
                content = ""
        m = re.search(r'"confirm"\s*:\s*(true|false)', content, re.IGNORECASE)
        if not m:
            logger.warning("[ScalpLLM] %s 裁决解析失败(内容=%s) → fail-open", symbol, content[:80])
            return {**_fail_open, "reason": f"parse_fail:{content[:40]}", "elapsed": round(time.time() - t0, 2)}
        confirmed = m.group(1).lower() == "true"
        elapsed = round(time.time() - t0, 2)
        logger.info(
            "[ScalpLLM] %s %s 裁决=%s (%s) %.1fs",
            symbol, side, "confirm" if confirmed else "reject",
            content[:60], elapsed,
        )
        return {
            "confirmed": confirmed,
            "source": "local" if (local is not None and cfg is local) else "cloud",
            "reason": content[:60],
            "elapsed": elapsed,
        }
    except Exception as e:
        logger.warning("[ScalpLLM] %s 调用异常(%s) → fail-open", symbol, str(e)[:80])
        return {**_fail_open, "reason": str(e)[:40], "elapsed": round(time.time() - t0, 2)}
