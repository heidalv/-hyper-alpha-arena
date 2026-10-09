# -*- coding: utf-8 -*-
"""[h901 2026-10-07] 统一进化总督(Evolution Governor)—— 高频模块的"一个大脑"。

用户最终定案:「完全智能的学习进化——把所有的修改和实时变化都监控,
高级学习进化,然后实施的系统」。

定位:**统一**所有学习环路的总督。它不取代它们,而是站在它们之上:
  监控(实时表现 + 所有环路输出 + 所有参数改动)
    → 归因(哪个币/哪条出场/哪个参数在赚在亏)
    → 进化(提出一个有界的、单变量的改进)
    → 验证(证据充分性 + 样本量 + 显著性)
    → 实施(写 lane params 热加载 + 登记待验证)
    → 回滚(下一周期验证上一个改动,不达标自动回滚)
    → 记忆(玩法手册 + 经验账本,跨周期积累)

纪律(沿用 self_tuner 的护栏,绝不裸奔):
  · 只动白名单数值参数,且落在边界内;
  · 单变量(一次只改一个,才能归因);
  · 证据不足 ⇒ 不动;
  · 改后实测变差 ⇒ 自动回滚;
  · 全程审计落盘(data/evolution_governor_log.jsonl)。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
LANE = "mm_asterdex"
LOG = ROOT / "data" / "evolution_governor_log.jsonl"
STATE = ROOT / "data" / "evolution_governor_state.json"
PENDING = ROOT / "data" / "evolution_governor_pending.json"
# [h901b] 玩法手册:总督自己积累的"定律"(LLM 反思写成,跨周期学习)
PLAYBOOK = ROOT / "data" / "evolution_governor_playbook.json"

# 总督可调的参数白名单(数值 + 边界)。只放经过验证、语义清晰的执行层参数。
GOVERNOR_PARAMS: Dict[str, tuple] = {
    "stop_loss_bp": (30.0, 100.0),        # 止损线
    "take_profit_bp": (15.0, 80.0),       # 止盈目标
    "trail_lock_bp": (10.0, 40.0),        # 移动止盈启动
    "stop_taker_bp": (40.0, 200.0),       # 穿透硬兜底(绝对)
    "compound_ratio": (0.10, 0.40),       # 基础腿量
    "timeout_hard_taker_sec": (300.0, 1800.0),  # 超时硬吃单
}


@dataclass
class Finding:
    """一条归因发现。"""
    dimension: str          # symbol / exit_path / param / gate
    subject: str            # 具体对象(如 "SI" / "hold_hard_taker")
    severity: float         # 亏损贡献(USD,负=亏)
    evidence_n: int         # 样本量
    detail: str
    actionable: str = ""    # 建议动作


@dataclass
class Proposal:
    """一次有界改进提案。"""
    param: str
    old: float
    new: float
    reason: str
    evidence_n: int
    expected: str


# ══════════════════════════════════════════════════════════════════════
# 监控采集:把全系统的实时表现汇总成一份"进化状态"
# ══════════════════════════════════════════════════════════════════════
def gather_performance(hours: float = 6.0) -> Dict[str, Any]:
    """从 lane_ledger 汇总近 N 小时的表现(盈亏比/逐币/逐出场路径)。"""
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    with system_identity():
        with SessionLocal() as db:
            tot = db.execute(text(
                "SELECT COUNT(*) n, SUM(net_bp*notional)/10000.0 pnl,"
                " AVG(net_bp) FILTER (WHERE net_bp>0) avg_win,"
                " AVG(net_bp) FILTER (WHERE net_bp<=0) avg_loss,"
                " COUNT(*) FILTER (WHERE net_bp>0) n_win,"
                " COUNT(*) FILTER (WHERE net_bp<=0) n_loss "
                "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                " AND ts > NOW() - make_interval(hours => :h)"),
                {"l": LANE, "h": int(hours)}).fetchone()
            by_sym = db.execute(text(
                "SELECT symbol, COUNT(*) n, SUM(net_bp*notional)/10000.0 pnl "
                "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                " AND ts > NOW() - make_interval(hours => :h)"
                " GROUP BY 1 ORDER BY 3"), {"l": LANE, "h": int(hours)}).fetchall()
            by_path = db.execute(text(
                "SELECT meta_json->>'exit_path' p, COUNT(*) n,"
                " SUM(net_bp*notional)/10000.0 pnl "
                "FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                " AND ts > NOW() - make_interval(hours => :h)"
                " AND meta_json->>'exit_path' IS NOT NULL"
                " GROUP BY 1 ORDER BY 3"), {"l": LANE, "h": int(hours)}).fetchall()
    n = int(tot[0] or 0)
    avg_win = float(tot[2] or 0.0)
    avg_loss = float(tot[3] or 0.0)
    n_win = int(tot[4] or 0)
    n_loss = int(tot[5] or 0)
    pl_ratio = (avg_win / abs(avg_loss)) if avg_loss < 0 else (1.0 if avg_win > 0 else 0.0)
    return {
        "hours": hours, "n": n, "pnl": float(tot[1] or 0.0),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "n_win": n_win, "n_loss": n_loss,
        "win_rate": (n_win / n) if n else 0.0,
        "pl_ratio": pl_ratio,
        "by_symbol": {str(s): {"n": int(nn), "pnl": float(p or 0.0)}
                      for s, nn, p in by_sym},
        "by_path": {str(p): {"n": int(nn), "pnl": float(pp or 0.0)}
                    for p, nn, pp in by_path},
    }


# ══════════════════════════════════════════════════════════════════════
# 归因:找出最亏的维度
# ══════════════════════════════════════════════════════════════════════
def attribute(perf: Dict[str, Any]) -> List[Finding]:
    """从表现数据里找出可归因的亏损点(按严重度排序)。"""
    findings: List[Finding] = []
    # ① 盈亏比恶化(赚小亏大)
    if perf["n"] >= 20 and perf["pl_ratio"] < 0.8 and perf["avg_loss"] < 0:
        findings.append(Finding(
            dimension="param", subject="盈亏比", severity=perf["pnl"],
            evidence_n=perf["n"],
            detail=f"盈亏比 {perf['pl_ratio']:.2f}(赢+{perf['avg_win']:.1f}/"
                   f"亏{perf['avg_loss']:.1f}bp)",
            actionable="收紧止损/穿透兜底 或 放大止盈"))
    # ② 逐币:谁在亏
    for sym, d in perf["by_symbol"].items():
        if d["n"] >= 5 and d["pnl"] < -0.5:
            findings.append(Finding(
                dimension="symbol", subject=sym, severity=d["pnl"],
                evidence_n=d["n"], detail=f"近{perf['hours']}h 净亏 ${d['pnl']:.2f}",
                actionable="逐币降权(成绩单已自动处理)"))
    # ③ 出场路径:哪条在出血
    for path, d in perf["by_path"].items():
        if d["n"] >= 3 and d["pnl"] < -0.5:
            findings.append(Finding(
                dimension="exit_path", subject=path, severity=d["pnl"],
                evidence_n=d["n"], detail=f"近{perf['hours']}h 净亏 ${d['pnl']:.2f}",
                actionable="出场路径参数调整"))
    findings.sort(key=lambda f: f.severity)
    return findings


# ══════════════════════════════════════════════════════════════════════
# 进化决策:针对最亏的维度,提出一个有界改进
# ══════════════════════════════════════════════════════════════════════
def propose(findings: List[Finding], current_params: Dict[str, float],
            perf: Dict[str, Any]) -> Optional[Proposal]:
    """单变量、有界、证据驱动。证据不足 ⇒ None(不动)。"""
    if not findings:
        return None
    top = findings[0]
    # 盈亏比恶化 ⇒ 调止损/穿透/止盈
    if top.dimension == "param" and top.subject == "盈亏比":
        if top.evidence_n < 30:
            return None
        # 亏得比赢的狠 ⇒ 要么止损更紧(少亏),要么止盈更宽(多赚)
        cur_stop = float(current_params.get("stop_loss_bp") or 40.0)
        cur_tp = float(current_params.get("take_profit_bp") or 60.0)
        # 盈亏比 < 0.8 且平均亏损大 ⇒ 先收紧穿透兜底(把灾难尾巴砍掉)
        cur_taker = float(current_params.get("stop_taker_bp") or 100.0)
        if cur_taker > 60.0:
            new = max(60.0, cur_taker * 0.8)
            return Proposal("stop_taker_bp", cur_taker, new,
                            f"盈亏比 {perf['pl_ratio']:.2f} 且有大额穿透亏损 ⇒ "
                            f"穿透硬兜底 {cur_taker:.0f}→{new:.0f}bp(砍灾难尾巴)",
                            top.evidence_n, "单笔最大亏损下降")
        # 否则放宽止盈让赢家跑大
        if cur_tp < 80.0 and perf["avg_win"] < abs(perf["avg_loss"]):
            new = min(80.0, cur_tp * 1.2)
            return Proposal("take_profit_bp", cur_tp, new,
                            f"赢腿均 +{perf['avg_win']:.1f}bp 太小 ⇒ "
                            f"止盈 {cur_tp:.0f}→{new:.0f}bp(让赢家跑大)",
                            top.evidence_n, "平均赢腿 bp 上升")
    return None


# ══════════════════════════════════════════════════════════════════════
# LLM 高级推理(DeepSeek-flash):像量化研究员一样读证据、找根因、提改进
# ══════════════════════════════════════════════════════════════════════
def _call_deepseek(messages: List[Dict[str, str]]) -> Optional[str]:
    """项目自有 DeepSeek(env 直连)。失败 ⇒ None(回退规则提案,不阻塞)。"""
    import os
    # 脚本/计划任务直跑时 .env 没加载 ⇒ 先加载(否则 DEEPSEEK_API_KEY 拿不到)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass
    try:
        from backend.services.llm_config_service import LLMConfig, call_llm_api_sync
    except Exception:
        return None
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    cfg = LLMConfig(
        id=0, name="evolution-governor", provider="deepseek",
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash",
        base_url=(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
                  or "https://api.deepseek.com"),
        api_key=key,
    )
    try:
        resp = call_llm_api_sync(config=cfg, messages=messages, temperature=0.2,
                                 max_tokens=700, response_format={"type": "json_object"},
                                 timeout=120.0, caller="evolution_governor")
        if isinstance(resp, dict):
            # 直连 DeepSeek 返回完整 API 包:内容在 choices[0].message.content
            if resp.get("content") or resp.get("text"):
                return resp.get("content") or resp.get("text")
            try:
                return resp["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                return json.dumps(resp)
        return str(resp or "")
    except Exception:
        return None


def _load_playbook() -> List[str]:
    """读玩法手册(总督自己积累的定律)。"""
    try:
        return list(json.loads(PLAYBOOK.read_text(encoding="utf-8")).get("laws") or [])
    except Exception:
        return []


def llm_propose(perf: Dict[str, Any], findings: List[Finding],
                current_params: Dict[str, float],
                leading: Optional[Dict[str, Any]] = None) -> Optional[Proposal]:
    """让 DeepSeek 读证据提一个改进。严格解析 + 白名单/边界护栏。
    [h901b] 读玩法手册(自己历史积累的定律)⇒ 从自己历史里学习,不重复犯错。
    [h901c] 还读领先指标(markout 漂移/成交率趋势)⇒ 在亏损前预防。"""
    wl = {k: list(v) for k, v in GOVERNOR_PARAMS.items()}
    findings_txt = "\n".join(
        f"- [{f.dimension}] {f.subject}: {f.detail}(n={f.evidence_n},净{f.severity:+.2f}USD)"
        for f in findings[:8]) or "（无明显亏损点）"
    laws = _load_playbook()
    laws_txt = "\n".join(f"- {x}" for x in laws[-12:]) or "（还没有积累）"
    lead = leading or {}
    leading_txt = (f"markout_30s漂移 {lead.get('markout_30s_bp')}bp({lead.get('markout_verdict')}),"
                   f" 近1h成交 {lead.get('fills_last_1h')} vs 前1h {lead.get('fills_prior_1h')}"
                   f"({lead.get('fill_trend')})")
    prompt = f"""你是高频快进快出策略的量化研究员。目标:让系统正收益。

当前表现(近 {perf['hours']}h):
- 总净盈亏 {perf['pnl']:+.2f} USD,腿数 {perf['n']},胜率 {perf['win_rate']:.0%}
- 盈亏比 {perf['pl_ratio']:.2f}(赢腿均 {perf['avg_win']:+.1f}bp / 亏腿均 {perf['avg_loss']:+.1f}bp)

亏损归因(最亏在前):
{findings_txt}

领先指标(预测性,在亏损前预警):
{leading_txt}

当前可调参数(只能从这些里选,且必须落在边界内):
{json.dumps(wl, ensure_ascii=False)}
当前值: {json.dumps({k: current_params.get(k) for k in wl}, ensure_ascii=False)}

你过去积累的定律(玩法手册,必须遵守/参考,不要重复已证明无效的改动):
{laws_txt}

任务:基于证据,提出**一个**最能改善净收益的参数调整(单变量)。
规则:只许改白名单参数且落在边界内;证据不足就返回 hold;
不要重复已证明无效的改动;优先考虑"砍亏损尾巴"和"让赢家跑大"。

只输出 JSON:{{"action":"adjust"/"hold","param":"参数名","new_value":数值,
"reason":"引用具体数字的理由","confidence":0.0~1.0}}"""
    text = _call_deepseek([{"role": "user", "content": prompt}])
    if not text:
        return None
    try:
        t = text.strip()
        if t.startswith("```"):
            t = t.split("```")[1]
            t = t[t.find("{"):] if "{" in t else t
        d = json.loads(t)
    except Exception:
        return None
    if str(d.get("action")) != "adjust":
        return None
    param = str(d.get("param") or "")
    if param not in GOVERNOR_PARAMS:
        return None
    try:
        new = float(d.get("new_value"))
    except (TypeError, ValueError):
        return None
    lo, hi = GOVERNOR_PARAMS[param]
    if not (lo <= new <= hi):
        return None
    old = float(current_params.get(param) or 0.0)
    if old <= 0 or abs(new - old) / max(old, 1e-9) < 0.03:
        return None   # 变化太小不值得改
    return Proposal(param, old, new,
                    f"[DeepSeek] {str(d.get('reason') or '')[:200]}",
                    int(perf["n"]), str(d.get("reason") or ""))


def llm_reflect_and_learn(perf: Dict[str, Any], verdict: Optional[str],
                          proposal: Optional[Proposal], applied: bool) -> Optional[str]:
    """[h901b] 反思学习:每个周期结束后,DeepSeek 读"发生了什么"写一条定律进手册。

    这是"学习"的核心:总督不只调参,还**从自己的决策结果里积累经验**,
    下次决策时读手册 ⇒ 越用越聪明,不重复犯错。
    """
    laws = _load_playbook()
    recent = laws[-5:] if laws else []
    prompt = f"""你是高频快进快出策略的量化研究员,正在写自己的"玩法手册"(积累定律)。

刚发生的一个进化周期:
- 表现:净 {perf['pnl']:+.2f}USD,n={perf['n']},胜率 {perf['win_rate']:.0%},盈亏比 {perf['pl_ratio']:.2f}
- 上一个改动的裁决: {verdict or '无'}
- 本周期提案: {(proposal.param + ' ' + str(proposal.old) + '→' + str(proposal.new)) if proposal else '无'}
- 是否实施: {applied}

你手册里最近的定律:
{chr(10).join('- ' + x for x in recent) or '（空）'}

任务:基于这个周期的结果,总结**一条**新的定律(关于什么有用/什么有害/什么要避免)。
要求:具体、可操作、基于上面的数字;不要重复已有定律;如果这周期没学到新东西就返回空。
只输出 JSON:{{"law":"一条定律","new":true/false}}"""
    text = _call_deepseek([{"role": "user", "content": prompt}])
    if not text:
        return None
    try:
        t = text.strip()
        if t.startswith("```"):
            t = t.split("```")[1]
            t = t[t.find("{"):] if "{" in t else t
        d = json.loads(t)
    except Exception:
        return None
    if not d.get("new") or not d.get("law"):
        return None
    law = str(d["law"])[:300]
    laws.append(f"[{time.strftime('%m-%d %H:%M')}] {law}")
    PLAYBOOK.write_text(json.dumps({"laws": laws[-60:]}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return law


# ══════════════════════════════════════════════════════════════════════
# 实施 + 回滚 + 记忆
# ══════════════════════════════════════════════════════════════════════
def _read_lane_params() -> Dict[str, Any]:
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    with system_identity():
        with SessionLocal() as db:
            row = db.execute(text(
                "SELECT meta_json FROM lane_registry WHERE lane_id=:l"),
                {"l": LANE}).fetchone()
    meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
    return meta


def _write_lane_params(meta: Dict[str, Any]) -> None:
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    with system_identity():
        with SessionLocal() as db:
            db.execute(text(
                "UPDATE lane_registry SET meta_json = CAST(:m AS jsonb),"
                " updated_at=now() WHERE lane_id=:l"),
                {"m": json.dumps(meta, ensure_ascii=False), "l": LANE})
            db.commit()


def apply_proposal(p: Proposal) -> bool:
    """写入 lane params(热加载,worker 60s 内采用)。"""
    lo, hi = GOVERNOR_PARAMS[p.param]
    if not (lo <= p.new <= hi):
        return False
    meta = _read_lane_params()
    params = dict(meta.get("params") or {})
    params[p.param] = p.new
    meta["params"] = params
    _write_lane_params(meta)
    # 登记待验证
    PENDING.write_text(json.dumps({
        "param": p.param, "old": p.old, "new": p.new,
        "applied_at": time.time(), "evidence_n": p.evidence_n,
        "reason": p.reason, "expected": p.expected,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def _leg_samples(hours: float) -> List[float]:
    """取近 N 小时逐腿净 bp 样本(供 t 检验)。"""
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT net_bp FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                " AND ts > NOW() - make_interval(hours => :h)"),
                {"l": LANE, "h": int(hours)}).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def _welch_t(a: List[float], b: List[float]) -> Optional[float]:
    """Welch 双样本 t 值(不要求等方差)。样本不足 ⇒ None。"""
    import math
    na, nb = len(a), len(b)
    if na < 5 or nb < 5:
        return None
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    denom = math.sqrt(va / na + vb / nb)
    if denom <= 0:
        return None
    return (mb - ma) / denom      # >0 ⇒ b(改后)更好


def verify_pending() -> Optional[str]:
    """[h901c 全面加强] 统计严谨的验证:多窗口 + Welch t 检验。

    旧版"1h 窗口 + 点估计 ×0.8"太弱:噪声会把好改动误回滚、坏改动误保留。
    现改为:
      · 样本:改前(改动时刻前 6h) vs 改后(改动时刻至今)逐腿净 bp;
      · 裁决:Welch t 检验——只有**显著变差**(t < −2)才回滚;
        显著变好(t > 2)保留;不显著(|t|≤2)继续观察(不草率决定);
      · 观察期 < 1h 或样本 < 30 ⇒ 不裁决。
    """
    if not PENDING.exists():
        return None
    try:
        pend = json.loads(PENDING.read_text(encoding="utf-8"))
    except Exception:
        return None
    applied_at = float(pend.get("applied_at") or 0.0)
    age_h = (time.time() - applied_at) / 3600.0
    if age_h < 1.0:
        return f"观察期未满 1h({age_h:.1f}h),暂不裁决"
    # 改前样本:改动时刻前 6h;改后样本:改动时刻至今
    from sqlalchemy import text as _t
    from backend.core.tenant import system_identity as _si
    from backend.database.connection import SessionLocal as _SL
    with _si():
        with _SL() as db:
            before_rows = db.execute(_t(
                "SELECT net_bp FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                " AND ts > to_timestamp(:a) - make_interval(hours => 6)"
                " AND ts <= to_timestamp(:a)"),
                {"l": LANE, "a": applied_at}).fetchall()
            after_rows = db.execute(_t(
                "SELECT net_bp FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                " AND ts > to_timestamp(:a)"),
                {"l": LANE, "a": applied_at}).fetchall()
    before = [float(r[0]) for r in before_rows if r[0] is not None]
    after = [float(r[0]) for r in after_rows if r[0] is not None]
    param, old, new = pend["param"], pend["old"], pend["new"]
    if len(after) < 30:
        return f"改后样本 {len(after)}<30,继续观察"
    t = _welch_t(before, after)
    if t is None:
        return "样本不足以做 t 检验,继续观察"
    mb = sum(after) / len(after)
    if t < -2.0:
        # 显著变差 ⇒ 回滚
        meta = _read_lane_params()
        params = dict(meta.get("params") or {})
        params[param] = old
        meta["params"] = params
        _write_lane_params(meta)
        PENDING.unlink(missing_ok=True)
        return f"✗ 显著变差已回滚:{param} {new}→{old}(t={t:.2f},改后每腿 {mb:+.2f}bp)"
    if t > 2.0:
        PENDING.unlink(missing_ok=True)
        return f"✓ 显著变好保留:{param} {old}→{new}(t={t:+.2f},改后每腿 {mb:+.2f}bp)"
    return f"… 不显著(t={t:+.2f}),{param} 暂留继续观察(改后每腿 {mb:+.2f}bp)"


# ══════════════════════════════════════════════════════════════════════
# [h901c 全面加强] 实时异常检测 + 预测性监控(在亏损发生前就发现)
# ══════════════════════════════════════════════════════════════════════
def detect_anomalies() -> List[Finding]:
    """实时异常检测:灾难进行中 / 数据停更 / 单边卡死。返回需立即处理的异常。"""
    out: List[Finding] = []
    # ① 灾难进行中:近 10 分钟有单腿净亏 < −80bp
    try:
        from sqlalchemy import text
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(text(
                    "SELECT symbol, net_bp, ts FROM lane_ledger"
                    " WHERE lane_id=:l AND event='fill' AND net_bp < -80"
                    " AND ts > NOW() - INTERVAL '10 minutes'"
                    " ORDER BY ts DESC"), {"l": LANE}).fetchall()
        for sym, net, ts in rows:
            out.append(Finding("anomaly", f"灾难进行中:{sym}", float(net),
                               1, f"近10分钟单腿 {float(net):+.0f}bp",
                               "立即检查该币是否该熔断"))
    except Exception:
        pass
    # ② 心跳停更(worker 死了)
    try:
        hb = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        age = time.time() - float(hb.get("ts") or 0.0)
        if age > 120:
            out.append(Finding("anomaly", "worker心跳停更", -1.0, 1,
                               f"心跳 {age:.0f}s 未更新", "worker 可能挂了,需重启"))
    except Exception:
        pass
    # ③ 单边卡死:某 skip 原因占 >85%(全部决策被同一闸门拦死)
    try:
        hb = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
        sk = hb.get("skip_counts") or {}
        tot = sum(int(v) for v in sk.values())
        if tot > 50:
            for reason, cnt in sk.items():
                if reason in ("maker_working",):
                    continue
                if int(cnt) / tot > 0.85:
                    out.append(Finding("anomaly", f"闸门卡死:{reason}", -0.5, int(cnt),
                                       f"{reason} 占 {int(cnt)/tot:.0%} 的拦截",
                                       "该闸门可能误伤,需检查"))
    except Exception:
        pass
    return out


def leading_indicators() -> Dict[str, Any]:
    """预测性监控:领先指标趋势(在亏损发生前预警)。

    读 markout 漂移趋势 + 近端成交率 + 灾难前兆(波动骤增)。
    返回 {markout_verdict, fill_trend, note}。
    """
    out: Dict[str, Any] = {}
    # ① markout 漂移(成交后价格是否越来越逆着我们走)
    try:
        mk = json.loads((ROOT / "data" / "flow_fill_markout.json").read_text(encoding="utf-8"))
        agg = mk.get("agg") or {}
        mo30 = (agg.get("mo_30s") or {}).get("mean_bp")
        out["markout_30s_bp"] = mo30
        out["markout_verdict"] = mk.get("verdict")
        out["markout_ts_age_min"] = round((time.time() - float(mk.get("ts") or 0)) / 60, 1)
    except Exception:
        out["markout_verdict"] = None
    # ② 近端成交率趋势(近 1h vs 前 1h)
    try:
        from sqlalchemy import text
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        with system_identity():
            with SessionLocal() as db:
                recent = db.execute(text(
                    "SELECT COUNT(*) FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                    " AND ts > NOW() - INTERVAL '1 hour'"), {"l": LANE}).fetchone()[0]
                prior = db.execute(text(
                    "SELECT COUNT(*) FROM lane_ledger WHERE lane_id=:l AND event='fill'"
                    " AND ts > NOW() - INTERVAL '2 hours'"
                    " AND ts <= NOW() - INTERVAL '1 hour'"), {"l": LANE}).fetchone()[0]
        out["fills_last_1h"] = int(recent or 0)
        out["fills_prior_1h"] = int(prior or 0)
        out["fill_trend"] = ("up" if recent > prior else
                             "down" if recent < prior else "flat")
    except Exception:
        pass
    return out


def run_cycle(hours: float = 6.0) -> Dict[str, Any]:
    """总督一个周期:异常检测 → 预测监控 → 验证旧改动 → 归因 → 提案 → 实施 → 记忆。"""
    # ⓪ [h901c] 实时异常检测(最高优先:灾难/停更/卡死要立刻知道)
    anomalies = detect_anomalies()
    # ⓪b [h901c] 预测性监控(领先指标,在亏损前预警)
    leading = leading_indicators()
    # ① 先验证上一个改动(回滚纪律优先)
    verdict = verify_pending()
    # ② 采集 + 归因(异常也算发现,排在最前)
    perf = gather_performance(hours)
    findings = attribute(perf)
    findings = anomalies + findings
    # ③ 当前参数
    meta = _read_lane_params()
    cur = {k: float(v) for k, v in (meta.get("params") or {}).items()
           if isinstance(v, (int, float))}
    # ④ 提案(只在没有待验证改动时才提新的——单变量纪律)
    #    LLM(DeepSeek)优先:像研究员一样读证据推理;失败/不合规 ⇒ 规则兜底
    proposal = None
    applied = False
    if not PENDING.exists() and findings:
        proposal = llm_propose(perf, findings, cur, leading)
        if proposal is None:
            proposal = propose(findings, cur, perf)
        if proposal is not None:
            # 记录改前每腿净额(供下周期裁决)
            proposal_before = perf["pnl"] / max(1, perf["n"])
            applied = apply_proposal(proposal)
            if applied:
                # 把改前基线写进 pending
                d = json.loads(PENDING.read_text(encoding="utf-8"))
                d["before_pnl_per_leg"] = proposal_before
                PENDING.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    # ④b [h901b] 反思学习:DeepSeek 读这个周期的结果,写一条定律进手册(学习闭环)
    new_law = None
    try:
        new_law = llm_reflect_and_learn(perf, verdict, proposal, applied)
    except Exception:
        new_law = None
    # ⑤ 记忆落盘
    record = {
        "ts": time.time(), "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "perf": {k: perf[k] for k in ("n", "pnl", "win_rate", "pl_ratio",
                                      "avg_win", "avg_loss")},
        "findings_n": len(findings),
        "top_finding": ({"dim": findings[0].dimension, "subj": findings[0].subject,
                         "sev": findings[0].severity} if findings else None),
        "verdict": verdict,
        "proposal": ({"param": proposal.param, "old": proposal.old,
                      "new": proposal.new, "reason": proposal.reason}
                     if proposal else None),
        "applied": applied,
        "new_law": new_law,
        "anomalies": [{"subj": a.subject, "detail": a.detail} for a in anomalies],
        "leading": leading,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    STATE.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record
