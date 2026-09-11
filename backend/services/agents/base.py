# -*- coding: utf-8 -*-
"""Agent 群公共骨架（v3 方向 3，p1-agents-a，2026-09-03）。

方案原则：**每个 Agent = 确定性算法核心 + LLM 解释/假设层 + 预测落库 + 事后评分；
可信度不足自动降为观察模式**。本模块把这四段固化成一条不可绕过的流水线：

    run()  →  analyze()          确定性核心（纯计算，只读库，无 LLM、无副作用）
           →  predict()          把核心结论写成可证伪的预测 → agent_predictions（到期由 score_due 评分）
           →  advise()           产出建议（改配置 / 改 TradingState / 实验卡）
           →  _apply()           **按生效模式决定是否执行**；observe 模式只记录不执行
           →  latest json        落盘 backend/data/agents/latest_{agent}.json（看板与人工复核）

三档模式（`AGENT_MODE_{AGENT_ID}` 环境变量，缺省 observe）：
  observe   只算、只落预测、只落盘；**绝不**触碰生效配置、TradingState、权重
  advise    额外允许产出 proposed 实验卡（仍不直接改生效值）
  act       允许直接执行 advice（Phase 1 全部 Agent 均不给 act；接口先留好）

可信度门（`credibility_gate`）：从 agent_predictions 读该 Agent 的已评分样本，
样本量 / 平均得分 / Brier 任一不达标 → **实际生效模式强制降为 observe**，并把降级原因写进结果。
这保证"没被验证过的 Agent 永远动不了钱"。

设计约束：
  - 单向依赖：只依赖 analysis.ledgers（账本）与只读的数据层，不反向依赖策略层；
  - 不造数：任何一层取数失败 → 记 errors，该项留空，绝不用占位值继续推断；
  - 幂等：run() 可重复调用；重复预测由各 Agent 自己的去重键控制。
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "agents"

MODE_OBSERVE = "observe"
MODE_ADVISE = "advise"
MODE_ACT = "act"
_MODE_RANK = {MODE_OBSERVE: 0, MODE_ADVISE: 1, MODE_ACT: 2}

# advise 模式唯一允许落库的建议类型：写一张 proposed 实验卡（不改任何生效配置）
ACTION_PROPOSE_EXPERIMENT = "propose_experiment"


def now_ms() -> int:
    return int(time.time() * 1000)


def env_true(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


def parse_mode(value: Any, default: str = MODE_OBSERVE) -> str:
    m = str(value or "").strip().lower()
    return m if m in _MODE_RANK else default


def min_mode(a: str, b: str) -> str:
    """取两个模式中更保守的一个（observe < advise < act）。"""
    return a if _MODE_RANK.get(a, 0) <= _MODE_RANK.get(b, 0) else b


# ─────────────────────────── 可信度门 ───────────────────────────
@dataclass
class CredibilityGate:
    """Agent 升级到 advise/act 的最低证据要求（默认值取自方案「影子 N ≥ 300 / 事件 N ≥ 30」的量级）。"""

    min_scored: int = 30          # 已评分预测数下限
    min_avg_score: float = 0.55   # 平均得分下限（direction/regime 类 ≈ 命中率）
    max_avg_brier: float = 0.30   # 平均 Brier 上限（校准度；0.25 = 全押 0.5 的水平）

    @classmethod
    def from_env(cls, agent_id: str) -> "CredibilityGate":
        suffix = agent_id.upper().replace("-", "_").replace(".", "_")
        return cls(
            min_scored=env_int(f"AGENT_GATE_MIN_SCORED_{suffix}", env_int("AGENT_GATE_MIN_SCORED", 30)),
            min_avg_score=env_float(f"AGENT_GATE_MIN_SCORE_{suffix}", env_float("AGENT_GATE_MIN_SCORE", 0.55)),
            max_avg_brier=env_float(f"AGENT_GATE_MAX_BRIER_{suffix}", env_float("AGENT_GATE_MAX_BRIER", 0.30)),
        )


def credibility_of(agent_id: str, *, days: int = 60) -> Dict[str, Any]:
    """该 Agent 近 N 天的可信度汇总（跨 kind 合并；样本量加权平均）。"""
    out: Dict[str, Any] = {"agent": agent_id, "days": days, "n": 0, "n_scored": 0,
                           "avg_score": None, "avg_brier": None, "by_kind": []}
    try:
        from backend.services.analysis import ledgers

        rows = [r for r in ledgers.agent_credibility(days) if str(r.get("agent") or "") == agent_id]
    except Exception as exc:
        out["error"] = str(exc)[:200]
        return out
    tot_n = tot_scored = 0
    ws = wb = 0.0
    nb = 0
    for r in rows:
        n_scored = int(r.get("n_scored") or 0)
        tot_n += int(r.get("n") or 0)
        tot_scored += n_scored
        if n_scored and r.get("avg_score") is not None:
            ws += float(r["avg_score"]) * n_scored
        if n_scored and r.get("avg_brier") is not None:
            wb += float(r["avg_brier"]) * n_scored
            nb += n_scored
        out["by_kind"].append({
            "kind": r.get("kind"), "n": int(r.get("n") or 0), "n_scored": n_scored,
            "avg_score": r.get("avg_score"), "avg_brier": r.get("avg_brier"), "last_ms": r.get("last_ms"),
        })
    out["n"] = tot_n
    out["n_scored"] = tot_scored
    out["avg_score"] = (ws / tot_scored) if tot_scored else None
    out["avg_brier"] = (wb / nb) if nb else None
    return out


def gate_verdict(cred: Dict[str, Any], gate: CredibilityGate) -> Dict[str, Any]:
    """可信度是否够格升级。返回 {"passed": bool, "reason": str, "gate": {...}}。"""
    n_scored = int(cred.get("n_scored") or 0)
    avg_score = cred.get("avg_score")
    avg_brier = cred.get("avg_brier")
    gate_d = asdict(gate)
    if n_scored < gate.min_scored:
        return {"passed": False, "reason": f"已评分样本 {n_scored} < {gate.min_scored}", "gate": gate_d}
    if avg_score is None or float(avg_score) < gate.min_avg_score:
        return {"passed": False, "reason": f"平均得分 {avg_score} < {gate.min_avg_score}", "gate": gate_d}
    if avg_brier is not None and float(avg_brier) > gate.max_avg_brier:
        return {"passed": False, "reason": f"Brier {avg_brier:.3f} > {gate.max_avg_brier}", "gate": gate_d}
    return {"passed": True, "reason": "可信度达标", "gate": gate_d}


# ─────────────────────────── 结果结构 ───────────────────────────
@dataclass
class Prediction:
    """待落库的预测草稿（run() 内统一写 agent_predictions）。"""

    kind: str
    subject: str
    prediction: Dict[str, Any]
    horizon_ms: int
    confidence: Optional[float] = None


@dataclass
class Advice:
    """Agent 的建议。observe 模式下只记录，不执行。"""

    action: str                       # 例如 set_trading_state / propose_experiment / adjust_source_weight
    target: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    severity: int = 1                 # 1..5，越大越紧急
    applied: bool = False
    apply_note: str = ""


@dataclass
class AgentResult:
    agent: str
    ts_ms: int
    ok: bool = True
    mode: str = MODE_OBSERVE           # 实际生效模式（可信度门后的）
    configured_mode: str = MODE_OBSERVE
    downgraded: bool = False
    downgrade_reason: str = ""
    credibility: Dict[str, Any] = field(default_factory=dict)
    findings: Dict[str, Any] = field(default_factory=dict)
    predictions: List[str] = field(default_factory=list)
    advice: List[Dict[str, Any]] = field(default_factory=list)
    experiments: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    latest_path: Optional[str] = None
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────── 基类 ───────────────────────────
class ObservationAgent:
    """观察模式 Agent 基类。子类只需实现 analyze()，按需实现 predict()/advise()/apply_advice()。"""

    agent_id: str = "base"
    description: str = ""
    kinds: Sequence[str] = ()          # 该 Agent 会产出的 agent_predictions.kind
    default_mode: str = MODE_OBSERVE
    max_mode: str = MODE_ADVISE        # 该 Agent 允许的最高模式（Phase 1 一律不给 act）

    def __init__(self, mode: Optional[str] = None, gate: Optional[CredibilityGate] = None):
        env_key = f"AGENT_MODE_{self.agent_id.upper().replace('-', '_')}"
        self.configured_mode = min_mode(
            parse_mode(mode or os.getenv(env_key), self.default_mode), self.max_mode
        )
        self.gate = gate or CredibilityGate.from_env(self.agent_id)
        self.dry_run = False   # run(dry_run=True) 时置位；analyze() 内的落盘副作用须自行跳过

    # ---- 子类实现 ----
    def analyze(self, errors: List[str]) -> Dict[str, Any]:
        raise NotImplementedError

    def predict(self, findings: Dict[str, Any]) -> List[Prediction]:
        return []

    def advise(self, findings: Dict[str, Any]) -> List[Advice]:
        return []

    def apply_advice(self, adv: Advice, findings: Dict[str, Any]) -> str:
        """act 模式下执行建议；返回说明。基类默认拒绝执行（Phase 1 无 act）。"""
        return "基类不执行任何建议"

    # ---- 流水线 ----
    def effective_mode(self) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
        cred = credibility_of(self.agent_id)
        verdict = gate_verdict(cred, self.gate)
        mode = self.configured_mode
        if mode != MODE_OBSERVE and not verdict.get("passed"):
            mode = MODE_OBSERVE
        return mode, cred, verdict

    def run(self, *, dry_run: bool = False) -> AgentResult:
        t0 = time.time()
        errors: List[str] = []
        self.dry_run = bool(dry_run)
        res = AgentResult(agent=self.agent_id, ts_ms=now_ms(), configured_mode=self.configured_mode, dry_run=dry_run)
        mode, cred, verdict = self.effective_mode()
        res.mode = mode
        res.credibility = {**cred, "verdict": verdict}
        if mode != self.configured_mode:
            res.downgraded = True
            res.downgrade_reason = f"可信度不足自动降级：{verdict.get('reason')}"
            logger.info("[Agent:%s] %s", self.agent_id, res.downgrade_reason)

        try:
            res.findings = self.analyze(errors) or {}
        except Exception as exc:
            logger.exception("[Agent:%s] analyze 失败", self.agent_id)
            res.ok = False
            errors.append(f"analyze: {exc}")
            res.errors = errors
            res.elapsed_sec = round(time.time() - t0, 3)
            return res

        if not dry_run:
            for p in self._safe(self.predict, res.findings, errors, "predict") or []:
                pid = self._record(p, errors)
                if pid:
                    res.predictions.append(pid)

        for adv in self._safe(self.advise, res.findings, errors, "advise") or []:
            if adv.action == ACTION_PROPOSE_EXPERIMENT:
                # 实验卡是 advise 模式的**唯一**产物：写一张 proposed 卡（不改任何生效值），
                # 由 experiments 的生命周期任务到期用真实账本判定。observe 模式只记录不落卡。
                if mode == MODE_OBSERVE or dry_run:
                    adv.apply_note = ("observe 模式：实验卡仅记录不落库" if mode == MODE_OBSERVE
                                      else "dry_run：实验卡仅记录不落库")
                else:
                    eid = self._propose_experiment(adv, errors)
                    if eid:
                        res.experiments.append(eid)
                        adv.applied = True
                        adv.apply_note = f"已建实验卡 {eid}（proposed）"
                    else:
                        adv.apply_note = "实验卡创建失败（字段不完整或落库异常）"
            elif mode == MODE_ACT and not dry_run:
                try:
                    adv.apply_note = self.apply_advice(adv, res.findings)
                    adv.applied = True
                except Exception as exc:
                    adv.apply_note = f"执行失败: {exc}"
                    errors.append(f"apply_advice[{adv.action}]: {exc}")
            else:
                adv.apply_note = f"{mode} 模式：仅记录不执行"
            res.advice.append(asdict(adv))

        res.errors = errors
        res.ok = res.ok and not any(e.startswith("analyze") for e in errors)
        res.elapsed_sec = round(time.time() - t0, 3)
        if not dry_run:
            res.latest_path = write_latest(self.agent_id, res.to_dict())
        return res

    # ---- 内部 ----
    def _safe(self, fn: Callable, findings: Dict[str, Any], errors: List[str], label: str):
        try:
            return fn(findings)
        except Exception as exc:
            logger.warning("[Agent:%s] %s 失败: %s", self.agent_id, label, exc)
            errors.append(f"{label}: {exc}")
            return []

    def _propose_experiment(self, adv: Advice, errors: List[str]) -> Optional[str]:
        """把 propose_experiment 建议落成一张 proposed 实验卡。

        params 必须给全方案要求的五要素：title / hypothesis / change / expected_metrics /
        window_hours（+ 可选 rollback_condition）。缺任何一项 `create_experiment` 会拒收，
        这正是我们要的——不许写含糊的"再观察观察"式建议。
        """
        try:
            from backend.services.analysis import ledgers

            p = adv.params or {}
            return ledgers.create_experiment(
                source=self.agent_id,
                title=str(p.get("title") or adv.target or "")[:200],
                hypothesis=str(p.get("hypothesis") or adv.reason or ""),
                change=p.get("change") or {},
                expected_metrics=p.get("expected_metrics") or [],
                window_hours=int(p.get("window_hours") or 168),
                rollback_condition=p.get("rollback_condition"),
                notes=p.get("notes"),
                experiment_id=p.get("experiment_id"),
            )
        except Exception as exc:
            logger.warning("[Agent:%s] 实验卡创建失败: %s", self.agent_id, exc)
            errors.append(f"propose_experiment: {exc}")
            return None

    def _record(self, p: Prediction, errors: List[str]) -> Optional[str]:
        try:
            from backend.services.analysis import ledgers

            return ledgers.record_prediction(
                agent=self.agent_id,
                kind=p.kind,
                subject=p.subject,
                prediction=p.prediction,
                horizon_ms=p.horizon_ms,
                confidence=p.confidence,
            )
        except Exception as exc:
            errors.append(f"record_prediction[{p.kind}]: {exc}")
            return None


# ─────────────────────────── 注册表 ───────────────────────────
_REGISTRY: Dict[str, Callable[[], ObservationAgent]] = {}


def register_agent(agent_id: str, factory: Callable[[], ObservationAgent]) -> None:
    _REGISTRY[agent_id] = factory


def get_agent(agent_id: str) -> Optional[ObservationAgent]:
    factory = _REGISTRY.get(agent_id)
    return factory() if factory else None


def registered_agents() -> List[str]:
    return sorted(_REGISTRY.keys())


def agents_status(days: int = 60) -> Dict[str, Any]:
    """看板用：每个 Agent 的配置模式 / 生效模式 / 可信度 / 最近一次运行摘要。"""
    out: List[Dict[str, Any]] = []
    for aid in registered_agents():
        agent = get_agent(aid)
        if agent is None:
            continue
        try:
            mode, cred, verdict = agent.effective_mode()
        except Exception as exc:
            out.append({"agent": aid, "error": str(exc)[:200]})
            continue
        latest = read_latest(aid) or {}
        out.append({
            "agent": aid,
            "description": agent.description,
            "kinds": list(agent.kinds),
            "configured_mode": agent.configured_mode,
            "effective_mode": mode,
            "max_mode": agent.max_mode,
            "downgraded": mode != agent.configured_mode,
            "gate": verdict.get("gate"),
            "gate_passed": verdict.get("passed"),
            "gate_reason": verdict.get("reason"),
            "credibility": {k: cred.get(k) for k in ("n", "n_scored", "avg_score", "avg_brier")},
            "last_run_ms": latest.get("ts_ms"),
            "last_run_ok": latest.get("ok"),
            "last_predictions": len(latest.get("predictions") or []),
            "last_advice": len(latest.get("advice") or []),
            "last_errors": latest.get("errors") or [],
        })
    return {"days": days, "agents": out, "ts_ms": now_ms()}


# ─────────────────────────── 落盘 ───────────────────────────
def _ensure_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def write_latest(agent_id: str, payload: Dict[str, Any]) -> Optional[str]:
    try:
        path = _ensure_dir() / f"latest_{agent_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return str(path)
    except Exception as exc:
        logger.warning("[Agent:%s] 落盘失败: %s", agent_id, exc)
        return None


def read_latest(agent_id: str) -> Optional[Dict[str, Any]]:
    try:
        path = DATA_DIR / f"latest_{agent_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ─────────────────────────── 统计小工具 ───────────────────────────
def zscore(series: Sequence[float], value: Optional[float] = None) -> Optional[float]:
    """样本 z-score。value 缺省用序列最后一个值；样本 < 8 或标准差为 0 → None（不造数）。"""
    xs = [float(x) for x in series if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if len(xs) < 8:
        return None
    v = float(value) if value is not None else xs[-1]
    base = xs[:-1] if value is None else xs
    if len(base) < 8:
        return None
    mu = sum(base) / len(base)
    var = sum((x - mu) ** 2 for x in base) / max(1, len(base) - 1)
    sd = math.sqrt(var)
    if sd <= 1e-12:
        return None
    return (v - mu) / sd


def cusum(series: Sequence[float], *, k: float = 0.5, h: float = 5.0) -> Dict[str, Any]:
    """标准化 CUSUM 漂移检测（Page 检验）。

    对序列做 z 标准化后累积：S+ = max(0, S+ + z − k)，S− = max(0, S− − z − k)。
    任一超过 h → 判定发生均值漂移（up / down）。样本 < 12 返回 detected=False。
    """
    xs = [float(x) for x in series if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if len(xs) < 12:
        return {"detected": False, "direction": 0, "s_pos": 0.0, "s_neg": 0.0, "n": len(xs), "reason": "样本不足"}
    mu = sum(xs) / len(xs)
    var = sum((x - mu) ** 2 for x in xs) / max(1, len(xs) - 1)
    sd = math.sqrt(var)
    if sd <= 1e-12:
        return {"detected": False, "direction": 0, "s_pos": 0.0, "s_neg": 0.0, "n": len(xs), "reason": "无波动"}
    s_pos = s_neg = 0.0
    peak_pos = peak_neg = 0.0
    for x in xs:
        z = (x - mu) / sd
        s_pos = max(0.0, s_pos + z - k)
        s_neg = max(0.0, s_neg - z - k)
        peak_pos = max(peak_pos, s_pos)
        peak_neg = max(peak_neg, s_neg)
    direction = 1 if peak_pos >= h and peak_pos >= peak_neg else (-1 if peak_neg >= h else 0)
    return {
        "detected": direction != 0,
        "direction": direction,
        "s_pos": round(peak_pos, 3),
        "s_neg": round(peak_neg, 3),
        "h": h,
        "k": k,
        "n": len(xs),
    }
