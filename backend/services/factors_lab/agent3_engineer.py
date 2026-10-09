# -*- coding: utf-8 -*-
"""③ 因子工程 agent —— 假设 → DSL AST 候选（编译审计 + 最小单测 + AST 相似去重）。

安全模型：产物是 expr DSL 的 JSON AST（无字符串 eval、无 import、无 IO），
parse() 先审计后编译（look-ahead/负窗口在编译期拦截）。LLM 只见算子白名单与字段白名单。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.services.factors_lab import common, config

logger = logging.getLogger(__name__)


def _op_registry_names() -> List[str]:
    try:
        from backend.services.factor_engine.expr.ops import OP_REGISTRY
        return sorted(OP_REGISTRY.keys())
    except Exception:
        return []


def _fields() -> List[str]:
    return ["open", "high", "low", "close", "volume", "vwap", "returns"]


_SYSTEM_TMPL = (
    "你是因子工程师。把研究假设翻译为 {k} 个**互异构**的因子表达式 AST 候选。"
    "AST 节点格式：算子 {{\"op\":\"算子名\",\"args\":[...]}}，字段叶 {{\"f\":\"close\"}}，"
    "常量叶 {{\"c\":5}}。只允许算子：{ops}。只允许字段：{fields}。"
    "滚动窗口参数在 5..60 之间。禁止 cross-sectional 算子。"
    "示例（动量排名因子）：{{\"op\":\"ts_rank\",\"args\":[{{\"op\":\"delta\",\"args\":[{{\"f\":\"close\"}},{{\"c\":5}}]}},{{\"c\":10}}]}}；"
    "示例（量价相关）：{{\"op\":\"corr\",\"args\":[{{\"f\":\"volume\"}},{{\"f\":\"close\"}},{{\"c\":15}}]}}。"
    "输出 JSON 数组：[{{\"ast\":{{...}},\"note\":\"实现说明(≤40字)\"}},...]。只输出 JSON。"
)


def _ast_nodes(node, acc: Optional[set] = None) -> set:
    acc = acc if acc is not None else set()
    if isinstance(node, dict):
        if "op" in node:
            acc.add(f"op:{node['op']}")
        for v in node.values():
            _ast_nodes(v, acc)
    elif isinstance(node, list):
        for v in node:
            _ast_nodes(v, acc)
    return acc


def ast_similarity(a: dict, b: dict) -> float:
    """节点集 Jaccard（ADR-21 结构层分量的轻量版；精确子树同构留接口）。"""
    na, nb = _ast_nodes(a), _ast_nodes(b)
    if not na or not nb:
        return 0.0
    return len(na & nb) / len(na | nb)


def ast_hash(ast: dict) -> str:
    canon = json.dumps(ast, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def ast_to_formula(node, depth: int = 0) -> str:
    """AST → 可读公式串（递归）：ts_rank(delta(close,5),10)。"""
    if depth > 12:
        return "…"
    if isinstance(node, dict):
        if "f" in node:
            return str(node["f"])
        if "c" in node:
            v = node["c"]
            return str(int(v)) if isinstance(v, float) and float(v).is_integer() else str(v)
        op = node.get("op") or "?"
        args = ", ".join(ast_to_formula(a, depth + 1) for a in node.get("args") or [])
        return f"{op}({args})"
    return str(node)


def synth_ohlcv(n: int = 300, seed: int = 7) -> Dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 1.5, n))
    return {
        "open": c + rng.normal(0, 0.3, n), "high": c + np.abs(rng.normal(0, 1.0, n)),
        "low": c - np.abs(rng.normal(0, 1.0, n)), "close": c,
        "volume": np.abs(rng.normal(1e6, 2e5, n)) + 1e4,
        "vwap": c + rng.normal(0, 0.2, n),
        "returns": np.diff(c, prepend=c[0]) / (c + 1e-9),
    }


def minimal_unit_tests(ast: dict) -> Dict[str, object]:
    """最小单测：编译已在调用方完成；此处做合成数据数值性质测试。"""
    from backend.services.factor_engine.expr.parser import ExprError, parse

    try:
        expr = parse(ast)
    except ExprError as e:
        return {"pass": False, "reason": f"compile/audit: {e}"}
    fields = synth_ohlcv()
    try:
        vals = np.asarray(expr.evaluate(fields), dtype=float)
    except Exception as e:  # noqa: BLE001
        return {"pass": False, "reason": f"eval: {str(e)[:80]}"}
    if vals.size != fields["close"].size:
        return {"pass": False, "reason": f"输出长度 {vals.size} != 输入 {fields['close'].size}"}
    finite = float(np.isfinite(vals).mean())
    if finite < 0.6:
        return {"pass": False, "reason": f"有限值占比 {finite:.2f} < 0.6"}
    v = vals[np.isfinite(vals)]
    if v.size < 10 or float(np.std(v)) < 1e-12:
        return {"pass": False, "reason": "输出近常数（无区分度）"}
    # 常数输入 → 常数输出（防隐蔽的差分/比率爆炸）
    const_fields = {k: np.full_like(x, float(np.nanmean(x))) for k, x in fields.items()}
    try:
        cv = np.asarray(expr.evaluate(const_fields), dtype=float)
        cv = cv[np.isfinite(cv)]
        if cv.size and float(np.std(cv)) > 1e-6 * max(1.0, abs(float(np.mean(cv)))):
            return {"pass": False, "reason": "常数输入输出非常数（算子语义可疑）"}
    except Exception:
        pass  # 常数输入下某些算子（如除零保护）允许异常
    return {"pass": True, "finite_ratio": round(finite, 3), "std": float(np.std(v))}


def _alignment_scores(hyp: Dict[str, object], cands: List[Dict[str, object]]) -> Dict[int, Dict]:
    """[AlphaAgent c₁/c₂ 语义对齐打分] 批量单次 LLM 调用。

    c1 = 因子是否为假设的有效实现；c2 = 表达式与实现说明的语义一致性（均 ∈[0,1]）。
    LLM 不可用/解析失败 → {}（fail-open，不阻断，对齐记 None）。
    """
    import os as _os

    if _os.getenv("FACTORS_LAB_ALIGN_CHECK", "1").strip().lower() in ("0", "false", "off"):
        return {}
    scoring_items = [{"idx": i, "note": str(c.get("note") or "")[:60],
                      "ast": c.get("ast")} for i, c in enumerate(cands)]
    system = (
        "你是量化因子审查员（AlphaAgent 语义对齐打分）。对每个候选因子打两个分："
        "c1=该表达式是否有效实现了研究假设的经济逻辑(0-1)；"
        "c2=表达式与note实现说明的语义一致性(0-1)。宽松标准：大体相关即≥0.6；"
        "完全无关/无法解释才<0.4。"
        '输出 JSON：{"scores":[{"idx":0,"c1":0.8,"c2":0.7,"reason":"≤30字"},...]}。只输出 JSON。'
    )
    user = (f"研究假设：{hyp.get('hypothesis')}\n论证：{hyp.get('argument')}\n"
            f"约束：{hyp.get('spec')}\n候选：\n{json.dumps(scoring_items, ensure_ascii=False)[:3000]}")
    raw = common.call_llm(system, user, caller="factors_lab_3_align", max_tokens=900)
    parsed = common.parse_json_block(raw or "")
    if not isinstance(parsed, dict):
        return {}
    out: Dict[int, Dict] = {}
    for s in parsed.get("scores") or []:
        try:
            idx = int(s.get("idx"))
            out[idx] = {"c1": round(float(s.get("c1")), 2), "c2": round(float(s.get("c2")), 2),
                        "reason": str(s.get("reason") or "")[:40]}
        except (TypeError, ValueError):
            continue
    return out


def _align_min() -> float:
    import os as _os

    try:
        return float(_os.getenv("FACTORS_LAB_ALIGN_MIN", "0.35"))
    except Exception:
        return 0.35


def realize(hyp: Dict[str, object], k: Optional[int] = None) -> Dict[str, object]:
    """一个假设 → k 个 AST 候选（编译+单测+去重）。返回候选列表（含失败记录）。"""
    k = k or config.candidates_per_hypothesis()
    ops = _op_registry_names()
    if not ops:
        return {"ok": False, "error": "expr OP_REGISTRY 不可用", "candidates": []}
    system = _SYSTEM_TMPL.format(k=k, ops=", ".join(ops), fields=", ".join(_fields()))
    user = (f"假设：{hyp.get('hypothesis')}\n论证：{hyp.get('argument')}\n"
            f"实现约束：{hyp.get('spec')}\n预期IC符号：{hyp.get('expected_ic_sign')}\n"
            f"适用状态：{hyp.get('applicable_regime')}")
    prev = common.read_jsonl(config.candidates_path(), limit=40)
    prev_nodes = [c.get("ast") for c in prev if c.get("ast")]

    out: List[Dict[str, object]] = []
    for attempt in range(2):
        if len(out) >= k:
            break
        raw = common.call_llm(system, user + ("" if attempt == 0 else "\n（上一批编译失败或重复，请换结构）"),
                              caller="factors_lab_3", max_tokens=1800)
        arr = common.parse_json_block(raw or "")
        if not isinstance(arr, list):
            arr = [arr] if isinstance(arr, dict) else []
        for item in arr:
            if len(out) >= k or not isinstance(item, dict):
                continue
            ast = item.get("ast")
            if not isinstance(ast, dict):
                continue
            ut = minimal_unit_tests(ast)
            eid = ast_hash(ast)
            dup = "new"
            if any(eid == c.get("expr_id") for c in out):
                continue
            sim_pool = max([ast_similarity(ast, p) for p in prev_nodes if p] or [0.0])
            sim_round = max([ast_similarity(ast, c["ast"]) for c in out] or [0.0])
            if ut["pass"] and max(sim_pool, sim_round) > config.ast_sim_threshold():
                ut = {"pass": False, "reason": f"AST 相似度过高 pool={sim_pool:.2f} round={sim_round:.2f}",
                      "ast_sim": round(max(sim_pool, sim_round), 3)}
            out.append({
                "cand_id": f"c{int(time.time()*1000)}_{len(out)}",
                "hyp_id": hyp.get("hyp_id"), "ts": time.time(),
                "ast": ast, "expr_id": eid, "note": str(item.get("note") or "")[:60],
                "unit_tests": ut, "status": "unit_pass" if ut.get("pass") else "unit_fail",
                "ast_sim_pool": round(sim_pool, 3),
            })
    # [c₁/c₂] 批量对齐打分 + 低对齐拒收（fail-open）——先打分后落盘，保证对齐字段持久化
    if out:
        align = _alignment_scores(hyp, out)
        if align:
            for i, c in enumerate(out):
                a = align.get(i)
                if a:
                    c["alignment"] = a
                    if (c.get("status") == "unit_pass"
                            and a["c1"] * a["c2"] < _align_min()):
                        c["status"] = "align_fail"
                        c["unit_tests"] = {
                            "pass": False,
                            "reason": f"语义对齐不足 c1={a['c1']} c2={a['c2']}（{a['reason']}）"}
    for c in out:
        common.append_jsonl(config.candidates_path(), c)
    logger.info("[FactorsLab③] hyp=%s 候选=%d 通过单测=%d 对齐拒=%d",
                hyp.get("hyp_id"), len(out),
                sum(1 for c in out if c.get("status") == "unit_pass"),
                sum(1 for c in out if c.get("status") == "align_fail"))
    return {"ok": any(c.get("status") == "unit_pass" for c in out), "candidates": out}
