# -*- coding: utf-8 -*-
"""② 假设生成 agent —— 知识卡 + 经验记忆 + 市场状态 → 研究假设（AlphaAgent 五组件 schema）。

多样性正则（ADR-21 逻辑层分量）：候选假设与近 30 天已立假设 token Jaccard > 阈值 → 拒收重生成。
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from backend.services.factors_lab import agent1_literature, common, config

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是加密货币量化因子研究员。基于给定的文献知识、历史教训、市场状态，提出可检验的因子研究假设。"
    "每个假设 JSON：{"
    '"knowledge_ref":"引用的知识卡标题或none","observation":"近期市场观察(≤40字)",'
    '"argument":"经济学论证(≤80字)","hypothesis":"可检验命题(≤60字)",'
    '"spec":"实现约束：字段(open/high/low/close/volume/vwap/returns)+窗口域+方向",'
    '"expected_ic_sign":1或-1,"horizon":"midlong","applicable_regime":"trend|range|any",'
    '"diversity_note":"与动量/均值回归/波动率等常见族的差异(≤30字)"}。'
    "输出 JSON 数组，每个命题必须能用一维时序算子(滚动窗口类)实现。只输出 JSON。"
)


def _market_state() -> str:
    """市场状态摘要（regime + BTC 动量/波动，尽力而为）。"""
    try:
        from backend.services.hybrid_scoring import kpanel
        from backend.services.hybrid_scoring.fusion import read_regime
        df = kpanel.load_klines("BTC", period="1d", bars=40)
        if df is None or len(df) < 25:
            return f"regime={read_regime()}"
        close = df["close"].to_numpy(dtype=float)
        mom20 = close[-1] / close[-21] - 1.0
        import numpy as np
        rets = close[1:] / close[:-1] - 1.0
        vol20 = float(np.std(rets[-20:]))
        return (f"regime={read_regime()}, BTC 20日动量={mom20:+.1%}, 20日日波动={vol20:.2%}")
    except Exception as e:  # noqa: BLE001
        logger.debug("[FactorsLab②] 市场状态获取失败: %s", str(e)[:100])
        return "regime=unknown"


def _experience_context() -> str:
    """lab 自身经验（带 outcome 的历史假设）+ v7 教训摘要。"""
    lines = []
    hyps = [h for h in common.read_jsonl(config.hypotheses_path(), limit=60)
            if h.get("outcome")]
    for h in hyps[-8:]:
        o = h.get("outcome")
        if isinstance(o, dict):
            v, reason = o.get("verdict"), str(o.get("reason") or "")[:50]
        elif o is True:  # 历史脏数据（首版 outcome 布尔）
            v, reason = h.get("verdict"), str(h.get("reason") or "")[:50]
        else:
            continue
        lines.append(f"- {str(h.get('hypothesis'))[:60]} → {v}({reason})")
    try:
        from backend.services.evolution.evolution_memory_v7 import build_codegen_context
        v7 = build_codegen_context("4h", limit=5)
        if v7:
            lines.append("v7教训: " + str(v7)[:400])
    except Exception:
        pass
    try:
        guidance = common.read_jsonl(config.guidance_path(), limit=2)
        for g in guidance:
            lines.append(f"上轮指导: {str(g.get('guidance'))[:300]}")
    except Exception:
        pass
    return "\n".join(lines) or "暂无"


def _diversity_gate(text: str, recent: List[Dict]) -> Optional[float]:
    """返回与最近假设的最大 Jaccard；超阈值由调用方拒收。"""
    best = 0.0
    cutoff = time.time() - 30 * 86400
    for h in recent:
        if float(h.get("ts") or 0) < cutoff:
            continue
        j = common.jaccard(text, " ".join(str(h.get(k)) for k in ("hypothesis", "argument", "spec")))
        best = max(best, j)
    return best


def generate(n: Optional[int] = None) -> Dict[str, object]:
    """生成 n 条假设（拒收重生成≤2轮）。返回统计+落盘条数。"""
    n = n or config.max_hypotheses()
    knowledge = agent1_literature.retrieve("加密货币 因子 收益预测", k=3)
    kctx = "\n".join(
        f"[{i+1}] {c.get('title')} | {str((c.get('card') or {}).get('phenomenon'))} | "
        f"机制:{str((c.get('card') or {}).get('mechanism'))}"
        for i, c in enumerate(knowledge)) or "暂无新文献"
    prompt = (f"文献知识：\n{kctx}\n\n历史经验与教训：\n{_experience_context()}\n\n"
              f"当前市场状态：{_market_state()}\n\n提出 {n} 个彼此异质的假设（JSON 数组）。")

    recent = common.read_jsonl(config.hypotheses_path(), limit=200)
    accepted: List[Dict[str, object]] = []
    rejected: List[str] = []
    for attempt in range(3):
        need = n - len(accepted)
        if need <= 0:
            break
        raw = common.call_llm(_SYSTEM, prompt if attempt == 0 else
                              f"{prompt}\n\n（补充要求：与以下已拒收命题进一步异质：{rejected[-2:]}）",
                              caller="factors_lab_2", max_tokens=2200)
        arr = common.parse_json_block(raw or "")
        if not isinstance(arr, list):
            arr = [arr] if isinstance(arr, dict) else []
        for h in arr:
            if not isinstance(h, dict) or not h.get("hypothesis"):
                continue
            text = f"{h.get('hypothesis')} {h.get('argument')} {h.get('spec')}"
            j = _diversity_gate(text, recent + accepted)
            if j > config.diversity_token_threshold():
                rejected.append(str(h.get("hypothesis"))[:50])
                continue
            accepted.append({
                "hyp_id": f"h{int(time.time()*1000)}_{len(accepted)}",
                "ts": time.time(), **{k: h.get(k) for k in
                                      ("knowledge_ref", "observation", "argument", "hypothesis",
                                       "spec", "expected_ic_sign", "horizon",
                                       "applicable_regime", "diversity_note")},
                "max_recent_jaccard": round(j, 3), "outcome": None,
            })
    for h in accepted:
        common.append_jsonl(config.hypotheses_path(), h)
    logger.info("[FactorsLab②] 假设生成 accepted=%d rejected=%d", len(accepted), len(rejected))
    return {"ok": bool(accepted), "accepted": len(accepted), "rejected": len(rejected),
            "hypotheses": accepted}
