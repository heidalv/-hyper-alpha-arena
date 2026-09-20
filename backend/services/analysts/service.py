# -*- coding: utf-8 -*-
"""分析师信号服务：落库 / 读取 / **契约验收** / 供主脑 prompt 的精简块。

关键设计（针对"说了做了其实没做"）：
  · `run_once()` 每次都会把**六域全部结果**写库，包括 `missing` 的行 —— 所以"某域没产出"
    是**可查询的事实**，不是没人知道的状态；
  · `contract_report()` 把"承诺的六域"逐域对照"实际交付"，输出 `undelivered` 列表；
    这条报告同时被单测与画布审计消费；
  · 表在 analytics 库（`alpha_analytics`），与其它审计表同库，便于 JOIN 追溯。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from .scorers import _base, _enabled, _lookback_h, score_all
from .signals import DOMAIN_SPECS, DOMAINS

logger = logging.getLogger(__name__)

TABLE = "analyst_signals"
PRODUCER = "backend/services/analysts/service.py"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMP      NOT NULL,
    symbol        VARCHAR(24)    NOT NULL,
    domain        VARCHAR(24)    NOT NULL,
    score         DOUBLE PRECISION NOT NULL,
    confidence    DOUBLE PRECISION NOT NULL,
    data_quality  VARCHAR(10)    NOT NULL,
    n_samples     INTEGER        NOT NULL DEFAULT 0,
    producer      VARCHAR(160),
    as_of         VARCHAR(32),
    evidence_json TEXT,
    missing_sources TEXT,
    reason        TEXT,
    created_at    TIMESTAMP      NOT NULL DEFAULT now()
)
"""
_INDEX = (f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_lookup "
          f"ON {TABLE} (domain, symbol, ts DESC)")


def _engine():
    from backend.database.connection import analytics_engine

    return analytics_engine


def ensure_table() -> bool:
    """建表（幂等）。失败返回 False 并记录日志（不抛，避免拖垮调用方）。"""
    from sqlalchemy import text

    try:
        with _engine().begin() as c:
            c.execute(text(_DDL))
            c.execute(text(_INDEX))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Analysts] 建表失败（%s 不可写？）: %s", TABLE, exc)
        return False


def default_symbols() -> List[str]:
    try:
        from backend.services.analysis.context_pack import universe

        return [_base(s) for s in universe()]
    except Exception:  # noqa: BLE001
        return ["BTC", "ETH", "SOL"]


def run_once(symbols: Optional[Sequence[str]] = None, *, persist: bool = True) -> Dict[str, Any]:
    """跑满六域并落库。返回逐域统计（含 missing 明细）——**缺失必须可见**。"""
    if not _enabled():
        return {"enabled": False, "reason": "ANALYST_SIGNALS_ENABLED=false", "signals": 0}
    syms = [_base(s) for s in (symbols or default_symbols())]
    sigs = score_all(syms)
    now = datetime.now()

    per_domain: Dict[str, Dict[str, Any]] = {
        d: {"rows": 0, "ok": 0, "weak": 0, "missing": 0, "missing_reasons": []} for d in DOMAINS
    }
    for s in sigs:
        b = per_domain.setdefault(s.domain, {"rows": 0, "ok": 0, "weak": 0, "missing": 0, "missing_reasons": []})
        b["rows"] += 1
        b[s.data_quality if s.data_quality in ("ok", "weak") else "missing"] += 1
        if s.data_quality == "missing":
            b["missing_reasons"].append(f"{s.symbol}: {s.reason}")

    written = 0
    if persist and sigs:
        from sqlalchemy import text

        if not ensure_table():
            return {"enabled": True, "symbols": syms, "signals": len(sigs), "written": 0,
                    "per_domain": per_domain, "error": "建表失败，未落库"}
        rows = [{
            "ts": now, "symbol": s.symbol, "domain": s.domain, "score": s.score,
            "confidence": s.confidence, "data_quality": s.data_quality, "n_samples": s.n_samples,
            "producer": s.producer, "as_of": s.as_of,
            "evidence_json": json.dumps(s.evidence, ensure_ascii=False, default=str)[:8000],
            "missing_sources": json.dumps(s.missing_sources, ensure_ascii=False),
            "reason": s.reason,
        } for s in sigs]
        with _engine().begin() as c:
            c.execute(text(
                f"INSERT INTO {TABLE} (ts, symbol, domain, score, confidence, data_quality, "
                f"n_samples, producer, as_of, evidence_json, missing_sources, reason) VALUES "
                f"(:ts, :symbol, :domain, :score, :confidence, :data_quality, :n_samples, "
                f":producer, :as_of, :evidence_json, :missing_sources, :reason)"), rows)
            written = len(rows)

    undelivered = [d for d, b in per_domain.items() if b["ok"] + b["weak"] == 0]
    summary = {"enabled": True, "symbols": syms, "signals": len(sigs), "written": written,
               "per_domain": per_domain, "undelivered_domains": undelivered, "ran_at": now.isoformat(timespec="seconds")}
    logger.info("[Analysts] 六域信号落库 %d 条；未交付域=%s", written, undelivered or "无")
    return summary


def latest_signals(symbols: Optional[Sequence[str]] = None, *, within_hours: Optional[float] = None) -> List[Dict[str, Any]]:
    """读最近一轮信号（每 (domain, symbol) 取最新一条）。"""
    from sqlalchemy import text

    hours = float(within_hours if within_hours is not None else max(2.0, _lookback_h()))
    params: Dict[str, Any] = {"since": datetime.now() - timedelta(hours=hours)}
    sql = (f"SELECT DISTINCT ON (domain, symbol) domain, symbol, score, confidence, data_quality, "
           f"n_samples, as_of, evidence_json, missing_sources, reason, ts "
           f"FROM {TABLE} WHERE ts >= :since")
    if symbols:
        sql += " AND (symbol = ANY(:syms) OR symbol = '*')"
        params["syms"] = [_base(s) for s in symbols]
    sql += " ORDER BY domain, symbol, ts DESC"
    try:
        rows = _q(sql, params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Analysts] 读取信号失败: %s", exc)
        return []
    out = []
    for r in rows:
        try:
            ev = json.loads(r[7]) if r[7] else {}
        except Exception:  # noqa: BLE001
            ev = {}
        try:
            ms = json.loads(r[8]) if r[8] else []
        except Exception:  # noqa: BLE001
            ms = []
        out.append({"domain": r[0], "symbol": r[1], "score": r[2], "confidence": r[3],
                    "data_quality": r[4], "n_samples": r[5], "as_of": r[6],
                    "evidence": ev, "missing_sources": ms, "reason": r[9], "ts": str(r[10])[:19]})
    return out


#: 规模通道的边界（分析师倾向只作用于**规模**，且必须有界）
SIZE_MULT_MIN, SIZE_MULT_MAX = 0.80, 1.10


def size_multiplier(symbol: str, *, tier: str = "mid") -> tuple:
    """六分析师信号的**规模乘子**（轮137）—— 返回 `(mult, note)`。

    为什么走规模而不是 conviction（**轮136 的教训**）：`llm_conviction` 同时是下游门槛的
    confidence 输入（`[V5Gate] rule=confidence` 要求 ≥30%），把折减写回去会让"去风险"变成
    **硬拦**（实测辩论 ×0.6 使 40→24 ⇒ 25~28% 撞门槛 20 次）。所以：
      · 门槛永远看 **LLM 原始置信度**；
      · 分析师/辩论的倾向只调**规模**（本函数），有界 [0.80, 1.10]。
    开关：`ANALYST_BLEND_SIZE_ENABLED`（默认 true）、`ANALYST_BLEND_SIZE_GAIN`（默认 0.20）。
    """
    if (os.getenv("ANALYST_BLEND_SIZE_ENABLED", "true") or "true").strip().lower() not in (
            "1", "true", "yes", "on"):
        return 1.0, "规模通道已停用"
    try:
        r = blend_for_symbol(symbol, tier=tier, conviction=0.0)
    except Exception as exc:  # noqa: BLE001
        return 1.0, f"计算失败 {type(exc).__name__}"
    n = int(r.get("n_domains") or 0)
    if n <= 0:
        return 1.0, "无可用域信号"
    score = float(r.get("analyst_score") or 0.0)
    gain = float(os.getenv("ANALYST_BLEND_SIZE_GAIN", "0.20") or 0.20)
    mult = max(SIZE_MULT_MIN, min(SIZE_MULT_MAX, 1.0 + gain * score))
    return round(mult, 4), f"analyst_score={score:+.3f} 域={n} gain={gain}"


#: 探针方向的默认阈值与一致度要求
PROBE_MIN_ABS_DEFAULT = 0.20


def probe_direction(symbol: str, *, tier: str = "mid") -> tuple:
    """[轮141 2026-09-20] **LLM 中性时的探针方向来源**：六域信号一致就给方向。

    背景（轮140 实测）：`core.analysis_runs` 最近 30 条 midlong_thesis 里
    neutral 14 / bearish 4 / bullish 2，`recommend_open` False 18/18，consensus 中位 0.31
    ⇒ **两个模型自己判断观望**，于是中线整段不成交（不是闸门问题）。
    用户架构要的是"分析师数值化后**作为一等 alpha 信号**参与决策"，所以：
    LLM 给不出方向、regime 也判不出方向时，**由六域信号的一致方向**做**有界小仓探针**。

    规则（可解释、有界、可回滚）：
      · 域数 ≥ `ANALYST_PROBE_MIN_DOMAINS`（默认 2），否则不猜；
      · |blend analyst_score| ≥ `ANALYST_PROBE_MIN_ABS`（默认 0.20）；
      · **一致性**：与该方向同号的域 ≥ 2，且反向域 ≤ 1（避免一两票极端值绑架方向）；
    返回 `(direction, note)`；不满足时返回 `("", 原因)`。
    开关 `ANALYST_PROBE_ENABLED`（默认 true）。
    """
    if (os.getenv("ANALYST_PROBE_ENABLED", "true") or "true").strip().lower() not in (
            "1", "true", "yes", "on"):
        return "", "探针方向来源已停用"
    try:
        r = blend_for_symbol(symbol, tier=tier, conviction=0.0)
    except Exception as exc:  # noqa: BLE001
        return "", f"计算失败 {type(exc).__name__}"
    n = int(r.get("n_domains") or 0)
    min_dom = int(float(os.getenv("ANALYST_PROBE_MIN_DOMAINS", "2") or 2))
    if n < min_dom:
        return "", f"域数不足({n}<{min_dom})"
    score = float(r.get("analyst_score") or 0.0)
    thr = abs(float(os.getenv("ANALYST_PROBE_MIN_ABS", str(PROBE_MIN_ABS_DEFAULT)) or PROBE_MIN_ABS_DEFAULT))
    if abs(score) < thr:
        return "", f"|score|={abs(score):.3f}<{thr:.2f}"
    want = "long" if score > 0 else "short"
    contrib = r.get("contributions") or {}
    agree = [k for k, v in contrib.items() if float(v.get("score") or 0) > 0.05 and want == "long"]
    agree += [k for k, v in contrib.items() if float(v.get("score") or 0) < -0.05 and want == "short"]
    against = [k for k, v in contrib.items() if float(v.get("score") or 0) < -0.05 and want == "long"]
    against += [k for k, v in contrib.items() if float(v.get("score") or 0) > 0.05 and want == "short"]
    if len(agree) < 2 or len(against) > 1:
        return "", f"一致度不足(同向={len(agree)} 反向={len(against)})"
    return want, (f"score={score:+.3f} 同向={'/'.join(sorted(agree))} "
                  f"反向={'/'.join(sorted(against)) or '无'}")


def _q(sql: str, params: Dict[str, Any]):
    from sqlalchemy import text

    with _engine().connect() as c:
        return c.execute(text(sql), params).fetchall()


# ───────────────────── 混合打分（一等 alpha 信号入权重，轮131）─────────────────────
# 用户架构：「分析师每日 thesis 数值化后**直接作为一等 alpha 信号进混合打分**」。
# 落地策略：先**影子**（只记录"如果生效会变成多少"），积累对照后再开生效 —— 因为这会改
# 主脑 conviction，属于策略变更，必须先看到"影子 vs 实际"的差异分布。
BLEND_TABLE = "analyst_blend_shadow"

_BLEND_DDL = f"""
CREATE TABLE IF NOT EXISTS {BLEND_TABLE} (
    id                BIGSERIAL PRIMARY KEY,
    ts                TIMESTAMP      NOT NULL DEFAULT now(),
    symbol            VARCHAR(24)    NOT NULL,
    tier              VARCHAR(8),
    conviction_before DOUBLE PRECISION,
    analyst_score     DOUBLE PRECISION,
    gain              DOUBLE PRECISION,
    blended           DOUBLE PRECISION,
    would_change      BOOLEAN,
    applied           BOOLEAN        NOT NULL DEFAULT false,
    contributions_json TEXT,
    missing_json      TEXT,
    producer          VARCHAR(160)
)
"""

#: 各域在混合打分里的默认权重（**只在有信号的域之间**归一化；缺域不摊薄、不冒充 0）
DEFAULT_BLEND_WEIGHTS: Dict[str, float] = {
    "technical": 0.30,     # 量价：最直接的价格证据
    "flow": 0.25,          # 资金流：拥挤度/清算/鲸鱼
    "sentiment": 0.20,     # 舆情：新闻情绪
    "fundamental": 0.10,   # 基本面：事件+利率（弱源，权重最低）
    "macro": 0.10,         # 宏观：全局风险偏好（symbol='*'）
    "kline_deep": 0.05,    # K线深度：产物当前缺失，权重留位
}


def blend_weights() -> Dict[str, float]:
    """权重表（可用 env `ANALYST_BLEND_W_<DOMAIN>` 覆盖单项；非法值忽略）。"""
    out = dict(DEFAULT_BLEND_WEIGHTS)
    for d in DOMAINS:
        raw = os.getenv(f"ANALYST_BLEND_W_{d.upper()}")
        if raw is None:
            continue
        try:
            out[d] = max(0.0, float(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def blend_for_symbol(symbol: str, *, tier: str = "mid",
                     conviction: float = 0.0) -> Dict[str, Any]:
    """把六域信号合成一个 [-1,1] 的 `analyst_score`，并给出**影子**混合结果。

    规则（可解释、可复核）：
      · 只在该标的有信号的域之间按权重归一化（缺域既不摊薄、也不当 0 —— 那会假装"中性"）；
      · `macro` 是全局信号（symbol='*'），所有标的都吃它；
      · 混合方式：`blended = conviction × (1 + gain × analyst_score)`，再钳到 [0,100]；
        gain 默认 0.15（即最强信号也只能把 conviction 拉动 ±15%），**有界**。
    """
    sym = _base(symbol)
    sigs = latest_signals([sym])
    weights = blend_weights()
    contrib: Dict[str, Any] = {}
    missing: List[str] = []
    num = den = 0.0
    for s in sigs:
        dom = str(s["domain"])
        if dom not in weights:
            continue
        if s["symbol"] not in (sym, "*"):
            continue
        if s["data_quality"] == "missing":
            missing.append(f"{dom}: {str(s.get('reason') or '')[:70]}")
            continue
        w = float(weights[dom])
        if w <= 0:
            continue
        score = float(s["score"])
        conf = float(s["confidence"])
        w_eff = w * max(0.2, conf)          # 低信心信号按比例减权（不是丢掉）
        num += score * w_eff
        den += w_eff
        contrib[dom] = {"score": round(score, 3), "conf": round(conf, 2), "w": w,
                        "symbol": s["symbol"], "quality": s["data_quality"]}
    analyst_score = (num / den) if den > 0 else 0.0
    gain = float(os.getenv("ANALYST_BLEND_GAIN", "0.15") or 0.15)
    before = float(conviction or 0.0)
    blended = max(0.0, min(100.0, before * (1.0 + gain * analyst_score))) if before > 0 else before
    return {
        "symbol": sym, "tier": tier,
        "conviction_before": round(before, 2),
        "analyst_score": round(analyst_score, 4),
        "gain": gain,
        "blended": round(blended, 2),
        "would_change": abs(blended - before) >= 0.5,
        "contributions": contrib,
        "missing": missing,
        "n_domains": len(contrib),
    }


def ensure_blend_table() -> bool:
    from sqlalchemy import text

    try:
        with _engine().begin() as c:
            c.execute(text(_BLEND_DDL))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Analysts] 混合打分影子表建表失败: %s", exc)
        return False


def record_blend_shadow(result: Dict[str, Any], *, applied: bool = False) -> bool:
    """把影子结果落库（供对照"如果生效会怎样"）。失败不阻塞主链。"""
    from sqlalchemy import text

    try:
        if not ensure_blend_table():
            return False
        with _engine().begin() as c:
            c.execute(text(
                f"INSERT INTO {BLEND_TABLE} (symbol, tier, conviction_before, analyst_score, gain, "
                f"blended, would_change, applied, contributions_json, missing_json, producer) VALUES "
                f"(:symbol, :tier, :cb, :score, :gain, :blended, :wc, :applied, :contrib, :missing, :producer)"),
                {"symbol": result["symbol"], "tier": result.get("tier"), 
                 "cb": result.get("conviction_before"), "score": result.get("analyst_score"),
                 "gain": result.get("gain"), "blended": result.get("blended"),
                 "wc": bool(result.get("would_change")), "applied": bool(applied),
                 "contrib": json.dumps(result.get("contributions") or {}, ensure_ascii=False)[:4000],
                 "missing": json.dumps(result.get("missing") or [], ensure_ascii=False)[:2000],
                 "producer": PRODUCER})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Analysts] 影子落库失败: %s", exc)
        return False


def blend_shadow_report(*, hours: float = 24.0) -> Dict[str, Any]:
    """影子对照报告：多少标的会变、平均拉动多少、分域贡献分布。"""
    from sqlalchemy import text

    try:
        if not ensure_blend_table():
            return {"ok": False, "error": "建表失败"}
        with _engine().connect() as c:
            r = c.execute(text(
                f"select count(*), count(*) filter (where would_change), "
                f"avg(blended - conviction_before), max(abs(blended - conviction_before)) "
                f"from {BLEND_TABLE} where ts >= now() - make_interval(secs => :secs)"),
                {"secs": float(hours) * 3600}).fetchone()
            by_dom = c.execute(text(
                f"select contributions_json from {BLEND_TABLE} "
                f"where ts >= now() - make_interval(secs => :secs) limit 500"),
                {"secs": float(hours) * 3600}).fetchall()
        dom_n: Dict[str, int] = {}
        for (cj,) in by_dom:
            try:
                for k in (json.loads(cj or "{}") or {}):
                    dom_n[k] = dom_n.get(k, 0) + 1
            except Exception:  # noqa: BLE001
                continue
        return {"ok": True, "window_hours": hours, "n": int(r[0] or 0),
                "would_change": int(r[1] or 0), "avg_delta": (round(float(r[2]), 3) if r[2] is not None else None),
                "max_abs_delta": (round(float(r[3]), 2) if r[3] is not None else None),
                "domain_presence": dict(sorted(dom_n.items(), key=lambda kv: -kv[1]))}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def contract_report() -> Dict[str, Any]:
    """**承诺 vs 实际交付**：逐域给出最近一轮的行数、质量分布、未交付原因。

    这是防止"设计了没做"复发的核心件：任何域长期没有 ok/weak 行，都会出现在 `undelivered`。
    """
    try:
        rows = _q(
            "SELECT domain, data_quality, count(*), max(ts) FROM " + TABLE +
            " WHERE ts >= :since GROUP BY domain, data_quality",
            {"since": datetime.now() - timedelta(hours=48)},
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                "declared_domains": DOMAINS, "undelivered": list(DOMAINS)}
    agg: Dict[str, Dict[str, Any]] = {}
    for dom, qual, n, last_ts in rows:
        b = agg.setdefault(dom, {"ok": 0, "weak": 0, "missing": 0, "last_ts": None})
        b[qual if qual in ("ok", "weak", "missing") else "missing"] += int(n)
        if last_ts and (b["last_ts"] is None or str(last_ts) > str(b["last_ts"])):
            b["last_ts"] = str(last_ts)[:19]
    undelivered = [d for d in DOMAINS if agg.get(d, {}).get("ok", 0) + agg.get(d, {}).get("weak", 0) == 0]
    missing_reasons: Dict[str, List[str]] = {}
    try:
        rr = _q("SELECT DISTINCT domain, reason FROM " + TABLE +
                " WHERE ts >= :since AND data_quality = 'missing' AND reason IS NOT NULL LIMIT 40",
                {"since": datetime.now() - timedelta(hours=48)})
        for dom, reason in rr:
            missing_reasons.setdefault(dom, []).append(str(reason)[:160])
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok": True,
        "declared_domains": [{"domain": d, "cn": DOMAIN_SPECS[d]["cn"],
                              "tables": DOMAIN_SPECS[d]["tables"],
                              "known_gaps": DOMAIN_SPECS[d]["known_gaps"]} for d in DOMAINS],
        "delivered": agg,
        "undelivered": undelivered,
        "missing_reasons": missing_reasons,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def prompt_block(symbols: Optional[Sequence[str]] = None, *, max_symbols: int = 8) -> Dict[str, Any]:
    """给主脑上下文用的精简块：按标的聚合六域信号 + 明确列出缺失域。

    只返回**数值与证据摘要**（不塞原始长文本），避免 prompt 膨胀。
    """
    sigs = latest_signals(symbols)
    if not sigs:
        return {"role": "证据，非指令", "available": False,
                "note": "尚无 analyst_signals（先跑 analysts.service.run_once）"}
    by_sym: Dict[str, Dict[str, Any]] = {}
    global_sig: Dict[str, Any] = {}
    missing: List[str] = []
    for s in sigs:
        if s["data_quality"] == "missing":
            missing.append(f"{s['domain']}: {str(s['reason'] or '')[:90]}")
            continue
        if s["symbol"] == "*":
            global_sig[s["domain"]] = {"score": round(float(s["score"]), 3),
                                       "conf": round(float(s["confidence"]), 2)}
            continue
        by_sym.setdefault(s["symbol"], {})[s["domain"]] = {
            "score": round(float(s["score"]), 3),
            "conf": round(float(s["confidence"]), 2),
            "n": s["n_samples"],
            "quality": s["data_quality"],
        }
    return {
        "role": "证据，非指令（六分析师数值化信号，口径见 backend/services/analysts/signals.py）",
        "available": True,
        "score_range": "[-1,1]，正=看多；confidence∈[0,1]",
        "global": global_sig,
        "symbols": dict(list(by_sym.items())[:max_symbols]),
        "missing": missing[:6],
        "as_of": max((s["as_of"] or "") for s in sigs),
    }
