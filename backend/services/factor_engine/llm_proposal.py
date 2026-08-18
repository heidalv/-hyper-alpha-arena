"""LLM 提案层（升级计划 v3.0 S2/M6 · P4）。

LLM 只提案不判分：生成 numpy 公式候选（带经济逻辑 + 预期 IC 符号 + 周期标注），
注册进 custom_factor_store（source=llm），走同一 score_formula 门禁，且 llm 源
额外收紧（min_sharpe +0.1、max_pbo 0.4）+ 符号反作弊（实际 IC 符号与
expected_ic_sign 相反 → 直接 rejected）。取代 D7 写 ai_gen_*.py 文件热加载路径。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join("data", "llm_proposal_state.json")
_DEDUP_SEC = 6 * 24 * 3600  # 默认节流 6 天（每周 job + 手动端点不受节流限制）

_TIER_MAP = {
    "scalp": {"horizon": "scalp", "timeframe": "1h", "period": "1h"},
    "midlong": {"horizon": "midlong", "timeframe": "4h", "period": "4h"},
}


def _resolve_tenant():
    try:
        from backend.services.factor_engine.factor_backtest_scorer import _resolve_admin_tenant
        return _resolve_admin_tenant()
    except Exception:
        return None


def build_proposal_prompt(tier: str) -> str:
    """结构化输入：被拒因子 scores + active 公式 + DSL 函数表 + 周期域约束。"""
    from backend.services.factor_engine.custom_factor_store import custom_factor_store

    t = _TIER_MAP.get(str(tier).lower(), _TIER_MAP["midlong"])
    _t = _resolve_tenant()
    all_recs = []
    try:
        all_recs = (custom_factor_store.list_rejected(tenant_id=_t) if hasattr(custom_factor_store, "list_rejected")
                    else [])
    except Exception:
        all_recs = []
    rejected_hints = []
    for r in all_recs[:30]:
        s = r.get("scores") or {}
        rejected_hints.append(
            f"{r.get('factor_id')}: ic={s.get('ic_mean')} icir={s.get('icir')} reason={(s.get('reason') or '')[:80]}"
        )
    active_hints = []
    try:
        for r in (custom_factor_store.list_active(tenant_id=_t) or []):
            _h = str((r.get("extra") or {}).get("horizon") or "scalp").lower()
            if (_h == "midlong") != (t["horizon"] == "midlong"):
                continue
            active_hints.append(f"{r.get('factor_id')}: {(r.get('formula') or '')[:80]}")
    except Exception:
        pass
    try:
        from backend.services.factor_engine.formula_ops import FORMULA_OPS
        _fns = ", ".join(sorted(FORMULA_OPS.keys()))
    except Exception:
        _fns = "delay, delta, ts_sum, ts_mean, ts_std, ts_max, ts_min, ts_rank, ts_argmax, ts_argmin, ts_corr, scale, sign, rank, decay_linear"
    prompt = (
        f"为加密货币 {t['period']} 周期（{t['horizon']} 线）提案 {8} 个新的 numpy 公式因子。\n"
        f"可用字段: open, high, low, close, volume, returns, vwap。\n"
        f"可用函数: {_fns}。\n"
        f"现有活跃因子（避免重复）: {active_hints or ['无']}。\n"
        f"最近被拒因子及原因（避免重蹈覆辙）: {rejected_hints or ['无']}。\n"
        f"每个因子必须: 1) 经济逻辑明确（≤60字）2) 预期 IC 符号 (+1 或 -1) 3) 换手适度"
        f"（窗口>=5根为主）4) 不引入未来数据。\n"
        f'输出 JSON 数组: [{{"formula": "...", "note": "...", "expected_ic_sign": 1}}]。只输出 JSON。'
    )
    return prompt


def _parse_proposals(raw: str) -> List[Dict[str, Any]]:
    if not raw:
        return []
    text = str(raw).strip()
    if text.startswith("```"):
        lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("["), text.rfind("]")
        if s < 0 or e <= s:
            return []
        try:
            data = json.loads(text[s : e + 1])
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = data.get("proposals") or data.get("formulas") or []
    return [d for d in data if isinstance(d, dict) and d.get("formula")]


def _trial_eval(formula: str) -> bool:
    """假数据试算：公式能在合成 OHLCV 上求值且有限。"""
    try:
        from backend.services.factor_engine.formula_ops import FORMULA_OPS
        rng = np.random.default_rng(0)
        n = 200
        c = np.abs(rng.normal(100, 2, n))
        ns = {"np": np,
              "open": c + rng.normal(0, 0.3, n), "high": c + 1, "low": c - 1,
              "close": c, "volume": np.abs(rng.normal(1e3, 2e2, n)) + 10,
              "returns": np.diff(c, prepend=c[0]) / c, "vwap": c}
        ns.update(FORMULA_OPS)
        vals = eval(formula, {"__builtins__": {}}, ns)  # noqa: S307 受限命名空间
        arr = np.asarray(vals, dtype=float)
        return bool(arr.size > 0 and np.isfinite(arr).sum() >= 60)
    except Exception:
        return False


def propose_and_register(tier: str, k: int = 8) -> Dict[str, Any]:
    """LLM 提案 → 校验 → 注册（source=llm）。返回统计。"""
    from backend.services.factor_engine.code_safety import ast_whitelist_check
    from backend.services.factor_engine.custom_factor_store import custom_factor_store
    from backend.services.evolution.alpha_miner import CodegenCritic

    t = _TIER_MAP.get(str(tier).lower(), _TIER_MAP["midlong"])
    critic = CodegenCritic()
    config = critic._load_config()
    if not config or not getattr(config, "api_key", None):
        return {"ok": False, "registered": 0, "error": "llm_config_unavailable"}
    from backend.services.llm_config_service import call_llm_api_sync
    messages = [
        {"role": "system", "content": (
            "你是量化因子研究员。输出 numpy 公式因子 JSON 数组，"
            "公式只能用给定字段与函数，禁止 import/属性访问/未来数据。"
        )},
        {"role": "user", "content": build_proposal_prompt(tier)},
    ]
    try:
        resp_data = call_llm_api_sync(
            config, messages=messages,
            response_format={"type": "json_object"}, max_tokens=3000,
            temperature=0.6, caller="llm_proposal",
        )
    except Exception as e:
        return {"ok": False, "registered": 0, "error": f"llm_error: {e}"}
    resp = ""
    if resp_data:
        choices = resp_data.get("choices") or []
        if choices:
            resp = (choices[0].get("message") or {}).get("content") or ""
    proposals = _parse_proposals(resp)[: max(1, min(int(k), 10))]
    registered = 0
    rejected = []
    _tid = _resolve_tenant()
    for p in proposals:
        formula = str(p.get("formula") or "").strip()
        if not formula:
            continue
        ok_wh, why = ast_whitelist_check(formula)
        if not ok_wh:
            rejected.append(f"{formula[:50]}: whitelist {why}")
            continue
        if not _trial_eval(formula):
            rejected.append(f"{formula[:50]}: 假数据试算失败")
            continue
        try:
            _sign = 1 if float(p.get("expected_ic_sign", 1)) >= 0 else -1
        except Exception:
            _sign = 1
        _slug = re.sub(r"[^a-zA-Z0-9_]+", "_", formula)[:30]
        res = custom_factor_store.register(
            f"llm_{_slug}", formula, category="discovered", source="llm",
            extra={
                "horizon": t["horizon"], "timeframe": t["timeframe"],
                "note": str(p.get("note") or "")[:60],
                "expected_ic_sign": _sign,
            },
            tenant_id=_tid,
        )
        if res.get("ok"):
            registered += 1
        else:
            rejected.append(f"{formula[:50]}: {res.get('reason')}")
    logger.info("[LLMProposal] tier=%s 提案=%d 注册=%d 拒绝=%d", tier, len(proposals), registered, len(rejected))
    return {"ok": True, "proposed": len(proposals), "registered": registered, "rejected": rejected}


def propose_with_throttle(tier: str, k: int = 8) -> Dict[str, Any]:
    """周频节流版（后台 job 用）；手动端点不走节流。"""
    state: Dict[str, float] = {}
    try:
        if os.path.exists(_STATE_PATH):
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
    except Exception:
        state = {}
    last = float(state.get(tier) or 0.0)
    if time.time() - last < _DEDUP_SEC:
        return {"ok": True, "registered": 0, "skipped": f"throttled（距上次 {time.time()-last:.0f}s）"}
    res = propose_and_register(tier, k=k)
    if res.get("ok"):
        state[tier] = time.time()
        try:
            os.makedirs(os.path.dirname(_STATE_PATH) or ".", exist_ok=True)
            with open(_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception:
            pass
    return res
