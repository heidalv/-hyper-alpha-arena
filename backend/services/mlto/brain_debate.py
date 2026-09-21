# -*- coding: utf-8 -*-
"""牛熊研究员对抗辩论 —— **接线到活主脑**（轮130）。

## 为什么需要这个模块
架构（用户原话）：「五分析师 … → 牛熊研究员对抗辩论 → 风控官（有否决权）→ 交易员」。
实测（轮129 审计）：`mlto/debate_layer.py` 是完整实现（牛/熊论点 + 3 个风险角色 + 裁决
`proceed/reduce/reject`），但**唯一调用者是 `mlto/orchestrator.py`**，而该模块全 backend
生产调用点 = 0（旧 MLTO 主脑 2026-09-05 下线）⇒ 辩论层从未在活链路上跑过，
`alpha_analytics.mlto_debate_log` **0 行**即为实证。

本模块把辩论接到活主脑 `mlto/brain.py::refresh_thesis`（OWM 调整之后、accepted/recommend_open 定型之前），
并且只用**真实的上下文证据**（context_pack 的市场/资金流/因子/**六分析师信号**层）喂它。

## 成本与闸门（**用户 2026-09-20 明确：先不要考虑 LLM 预算**）
> 原话：「强调过，先不要考虑 llm 预算，你怎么还想给我省，你这个省会坏大事情」。
> 因此下列默认值**不再为省钱设限**：风控角色也用 LLM（`MIDLONG_DEBATE_LLM_RISK=true`）、
> 轮数 2、小时上限 0（不限）、冷却 120s（仅防同一 tick 内重复，不是预算控制）。
> 保留的开关只是**可控性**（便于定位问题/回滚），不是省钱手段。

| 开关 | 默认 | 作用 |
|---|---|---|
| `MIDLONG_DEBATE_ENABLED` | true | 总闸（false = 完全不跑，回滚开关） |
| `MIDLONG_DEBATE_LLM` | true | true=LLM 生成论点；false=规则降级（仅用于离线/故障排查） |
| `MIDLONG_DEBATE_LLM_RISK` | **true** | 风控角色**也走 LLM**（架构要求"风控官"独立评估） |
| `MIDLONG_DEBATE_APPLY` | true | 是否让**主周期**裁决有界影响 conviction |
| `MIDLONG_DEBATE_MAX_ROUNDS` | **2** | 辩论轮数（每轮 = 牛 + 熊 + 3 风险角色） |
| `MIDLONG_DEBATE_COOLDOWN_S` | **120** | 同标的两次辩论最小间隔（防同 tick 重复，非预算控制） |
| `MIDLONG_DEBATE_HOURLY_CAP` | **0** | 0 = **不限**（仅防失控；设 >0 时才生效） |
| `MIDLONG_DEBATE_TRANSPORT` | 空 | 指定传输；空=取 `ANALYSIS_PRIMARY_TRANSPORTS` 第一项 |

## 生效幅度（**有界**，且不与"风控官否决权"混淆）
· `reject` → conviction ×0.6（下限 10），并把 `debate_verdict` 写进论题事件；
· `reduce` → conviction ×0.85；
· `proceed` → 不变。
**否决权不在这一层**：辩论只产出裁决与证据，硬拦截属于"风控官"（下一块的
`constitutional_veto` / 闸门层），避免两个地方都能改方向。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PRODUCER = "backend/services/mlto/brain_debate.py"

_LOCK = threading.Lock()
_LAST_BY_SYMBOL: Dict[str, float] = {}
_HOURLY: List[float] = []
_STATS: Dict[str, int] = {"runs": 0, "skipped_cooldown": 0, "skipped_cap": 0,
                          "skipped_zone": 0, "llm_runs": 0, "rule_runs": 0, "persist_rows": 0,
                          "apply_reject": 0, "apply_reduce": 0, "errors": 0}


def _flag(name: str, default: str) -> bool:
    return (os.getenv(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:  # noqa: BLE001
        return float(default)


def enabled() -> bool:
    return _flag("MIDLONG_DEBATE_ENABLED", "true")


def _llm_transport() -> str:
    t = (os.getenv("MIDLONG_DEBATE_TRANSPORT") or "").strip()
    if t:
        return t
    # [轮155 2026-09-21·用户指令「LLM 全部换 deepseek，不用 minimax」]
    # 默认值随之改为 deepseek；不再回落到 minimax（MiniMaxTransport 仍注册，供回滚）。
    prim = (os.getenv("ANALYSIS_PRIMARY_TRANSPORTS", "deepseek") or "deepseek").split(",")
    return (prim[0] or "deepseek").strip()


def _make_llm_client(tier: str, recorder: Optional[List[Tuple[str, str]]] = None):
    """给辩论层用的 `Callable[[str], str]`：走统一网关（记账 + 配额，task=mlto_debate）。

    [轮130 成本细化] 风控角色的 LLM 默认**关**（`MIDLONG_DEBATE_LLM_RISK=false`）：
    辩论层对风险角色的 LLM 失败会**自动回退规则裁决**（`_rule_risk_turn`），
    所以我在这里对有意的风控 prompt 直接抛错即可走回退 —— 每轮辩论的成本从 5 次降到 2 次
    （牛 + 熊），LLM 预算留给后面的"风控官"专项（它才是架构里真正有否决权的角色）。
    [轮130 周期化] 每个 prompt 末尾追加**周期契约**（`_horizon_instruction`），
    并把 (role, 原始回答) 记进 `recorder`，供 `_parse_horizons` 抽出分周期裁决 ——
    辩论层本身的解析（argument/confidence）保持不变，互不干扰。
    失败一律抛给调用方（辩论层内部会规则降级），不在这里吞异常。
    """
    from backend.services.analysis.model_gateway import get_model_gateway

    gw = get_model_gateway()
    transport = _llm_transport()
    allow_risk_llm = _flag("MIDLONG_DEBATE_LLM_RISK", "true")
    suffix = _horizon_instruction()

    def _client(prompt: str) -> str:
        if (not allow_risk_llm) and ("风险评估结论" in prompt or "是否应继续" in prompt):
            raise RuntimeError("risk-role LLM disabled (MIDLONG_DEBATE_LLM_RISK=false) → 规则裁决")
        role = "bear" if "看空(bear)" in prompt else ("bull" if "看多(bull)" in prompt else "risk")
        res = gw.call(
            "mlto_debate",
            f"你是加密货币中长线（tier={tier}）交易台的辩论参与者，只输出 JSON。",
            prompt + suffix,
            transport=transport,
            # [轮158 2026-09-21] 900 → 2500：实测 175 条"无可解析 JSON"的失败行
            # output_tokens **全部等于 900**（= 上限），即回答被截断成半截 JSON；
            # 而成功的行只需 400~775。辩论每轮要写 argument+evidence+counterargument+
            # weakness + 三个周期的 horizons，且 DeepSeek 的 thinking token 也计入
            # completion_tokens ⇒ 900 根本不够。max_tokens 只是上限、不预扣费，
            # 故抬高不影响成本（用户明确"先不要考虑 LLM 预算"）。
            # 回滚：MIDLONG_DEBATE_MAX_OUTPUT_TOKENS=900。
            max_output_tokens=int(_num("MIDLONG_DEBATE_MAX_OUTPUT_TOKENS", 2500)),
            temperature=0.3,
            timeout_s=float(_num("MIDLONG_DEBATE_TIMEOUT_S", 120)),
        )
        txt = getattr(res, "text", None)
        if txt is None:
            txt = str(getattr(res, "raw", "") or "")
        txt = txt or ""
        if recorder is not None:
            recorder.append((role, txt))
        return txt

    return _client


def _gate(symbol: str, conviction: float, tier: str) -> Optional[str]:
    """闸门：返回拒绝原因（None=放行）。顺序：总闸 → 灰区 → 冷却 → 小时上限。"""
    if not enabled():
        return "disabled"
    try:
        from backend.services.mlto.debate_layer import should_debate
    except Exception as exc:  # noqa: BLE001
        _STATS["errors"] += 1
        logger.debug("[Debate] should_debate 不可用: %s", exc)
        return "no_layer"
    hub = max(0.0, min(1.0, float(conviction) / 100.0))
    if not should_debate(hub, tier):
        _STATS["skipped_zone"] += 1
        return f"not_gray_zone(hub={hub:.2f})"
    now = time.time()
    cd = _num("MIDLONG_DEBATE_COOLDOWN_S", 120)
    with _LOCK:
        last = _LAST_BY_SYMBOL.get(symbol.upper(), 0.0)
        if now - last < cd:
            _STATS["skipped_cooldown"] += 1
            return f"cooldown({int(cd - (now - last))}s)"
        # [轮132 用户指令] 不再为省 LLM 设限：cap=0 表示**不限**（只保留"设了才生效"的失控保护）
        cap = _num("MIDLONG_DEBATE_HOURLY_CAP", 0)
        _HOURLY[:] = [t for t in _HOURLY if now - t < 3600]
        if cap > 0 and len(_HOURLY) >= cap:
            _STATS["skipped_cap"] += 1
            return f"hourly_cap({cap})"
    return None


#: 辩论必须**明确周期**（用户 2026-09-20 指令）：「牛熊分析需要明确周期，日内中期 和长期趋势」。
#: 三档周期各自独立举证与裁决 —— 禁止用一个不分周期的结论糊过去
#: （「日内看空、长期看多」在实盘是常态，混在一起只会得到一个没用的平均值）。
HORIZONS: Tuple[Tuple[str, str, str], ...] = (
    ("intraday", "日内", "小时~1 天：1h/4h 结构、24h 区间位置、资金费/OI/清算、舆情"),
    ("swing", "中期", "数天~两周：4h/1d 均线结构、7d/30d 收益、因子路线、基本面事件"),
    ("trend", "长期趋势", "数周~数月：1d/1w 结构、距 EMA200、30d 收益、宏观偏向"),
)
HORIZON_KEYS: Tuple[str, ...] = tuple(h[0] for h in HORIZONS)
HORIZON_CN: Dict[str, str] = {h[0]: h[1] for h in HORIZONS}

#: 每条车道以哪个周期为主（中线=日内+中期，长线=长期趋势）；其余周期仍要报，只不作为生效依据。
TIER_PRIMARY_HORIZON: Dict[str, str] = {
    "mid": "intraday", "intraday": "intraday", "short": "intraday",
    "long": "trend", "trend": "trend",
}

#: 分周期裁决阈值（与 debate_layer._synthesize 同口径，便于对照）
_VERDICT_REJECT_NET = -0.15
_VERDICT_REDUCE_NET = 0.15
_VERDICT_REJECT_RISK = 0.35
_VERDICT_REDUCE_RISK = 0.55


def primary_horizon(tier: str) -> str:
    return TIER_PRIMARY_HORIZON.get(str(tier or "").lower(), "swing")


def _horizon_instruction() -> str:
    """追加到辩论 prompt 的**周期契约**：牛/熊都必须逐周期给论点与信心。"""
    lines = "\n".join(f'  "{k}": {{"stance":"long|short|neutral","confidence":0-1,"argument":"{cn}周期的核心论点"}}'
                      for k, cn, _ in HORIZONS)
    return (
        "\n\n【必须按周期分别立论】（这是硬要求，缺周期视为无效回答）\n"
        "三个周期：日内（小时~1天）/ 中期（数天~两周）/ 长期趋势（数周~数月）。\n"
        "论点必须锚定**该周期自己的证据**（例：日内看资金费与 24h 区间位置；中期看 4h/1d 结构与因子路线；"
        "长期看距 EMA200、30d 收益与宏观偏向）。\n"
        "若你负责的这一侧在某个周期上并不成立，就如实写 neutral 或给低 confidence —— 不要为了赢而编。\n"
        "输出 JSON 时必须额外带上 `horizons` 字段：\n{\n" + lines + "\n}\n"
        "原有的 argument/confidence/evidence 仍要给出（作为该侧的总论点）。"
    )


def _salvage_horizons(text: str) -> Dict[str, Dict[str, Any]]:
    """**容错解析**：模型 JSON 里常嵌未转义引号（实测熊方就是这样，json.loads 整段失败）。

    直接按 `"horizons"` 之后的每个周期键做**局部正则**抽取 stance/confidence/argument，
    不要求整段 JSON 合法 —— 否则一次引号事故就把"分周期"退化成文本启发式（方向都可能反过来）。
    """
    import re as _re

    out: Dict[str, Dict[str, Any]] = {}
    try:
        m = _re.search(r'"horizons"\s*:\s*\{', text or "")
        if not m:
            return out
        tail = text[m.end():]
        for k in HORIZON_KEYS:
            km = _re.search(rf'"{k}"\s*:\s*\{{', tail)
            if not km:
                continue
            window = tail[km.end(): km.end() + 800]
            st = _re.search(r'"stance"\s*:\s*"([A-Za-z]+)"', window)
            cf = _re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', window)
            arg = _re.search(r'"argument"\s*:\s*"(.*?)(?<!\\)"', window, _re.DOTALL)
            if not st and not cf:
                continue
            try:
                conf = max(0.0, min(1.0, float(cf.group(1)))) if cf else 0.5
            except Exception:  # noqa: BLE001
                conf = 0.5
            out[k] = {"stance": (st.group(1).lower() if st else "unknown"),
                      "confidence": conf,
                      "argument": (arg.group(1) if arg else "")[:300],
                      "salvaged": True}
    except Exception:  # noqa: BLE001
        return {}
    return out


def _parse_horizons(text: str) -> Dict[str, Dict[str, Any]]:
    """从模型文本里解析 `horizons` 块；缺失/畸形则该周期标记为 unknown（不猜）。"""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        import re as _re

        m = _re.search(r"\{.*\}", text or "", _re.DOTALL)
        obj = json.loads(m.group(0)) if m else {}
        hz = obj.get("horizons") if isinstance(obj, dict) else None
        if isinstance(hz, dict):
            for k in HORIZON_KEYS:
                v = hz.get(k)
                if isinstance(v, dict):
                    try:
                        conf = max(0.0, min(1.0, float(v.get("confidence", 0.5))))
                    except Exception:  # noqa: BLE001
                        conf = 0.5
                    out[k] = {"stance": str(v.get("stance") or "unknown").lower(),
                              "confidence": conf, "argument": str(v.get("argument") or "")[:300]}
                else:
                    out[k] = {"stance": "unknown", "confidence": 0.5, "argument": ""}
    except Exception:  # noqa: BLE001
        pass
    if not any(str(v.get("stance")) != "unknown" for v in out.values()):
        # 整段解析失败或没有立场 ⇒ 走容错解析（JSON 引号事故的兜底）
        salvaged = _salvage_horizons(text)
        for k, v in salvaged.items():
            out[k] = v
    for k in HORIZON_KEYS:                       # 保证三档都有键（缺=unknown，可被测试与审计发现）
        out.setdefault(k, {"stance": "unknown", "confidence": 0.5, "argument": ""})
    return out


def _horizon_verdict(net: float, risk_min: float) -> str:
    if net <= _VERDICT_REJECT_NET or risk_min < _VERDICT_REJECT_RISK:
        return "reject"
    if net < _VERDICT_REDUCE_NET or risk_min < _VERDICT_REDUCE_RISK:
        return "reduce"
    return "proceed"


#: 单侧未按周期作答时的兜底：从它的总论点文本里按周期分句抽立场。
#: 为什么必须兜底：实测（11:25 那次）**熊方把三个周期写进了 argument 却没给结构化 horizons**，
#: 于是「净倾向 = 牛方信心 − 0.5」成了半盲比较 —— 一侧缺答绝不能机械地产生裁决。
_BULL_WORDS = ("看多", "偏多", "做多", "上行", "转多", "bullish", "强势", "逢低", "多头")
_BEAR_WORDS = ("看空", "偏空", "做空", "下行", "转空", "bearish", "弱势", "空头")
#: 否定式看多短语：**必须先剥离**，否则「不支持做多」里的"做多"会把空方句子数成看多
#: （实测：熊方总起句「三重周期均不支持做多」被子串误判 → 立场算成 neutral）。
_NEGATED_BULL = ("不支持做多", "不宜做多", "不建议做多", "不看好", "难以做多", "避免做多", "不构成做多")


def _derive_horizon_stance(text: str, cn: str) -> Optional[Dict[str, Any]]:
    """在总论点文本里找提到该周期的分句，按多空词判立场；找不到返回 None（不猜）。

    [轮130 实测形态] 模型常写成「总起句：日内…；中期…；长期趋势…」。
    若该周期自己的分句里没有多空词（如"长期趋势regime=up但出现背离"），
    再回退到**总起句**（"三重周期均不支持做多"）——先具体后总体，避免总体口号覆盖具体分歧。
    """
    import re as _re

    if not text:
        return None
    seg = next((s for s in _re.split(r"[；;。\n]", text) if cn in s), "")

    def _score(s: str) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        bear_extra = 0
        for p in _NEGATED_BULL:
            if p in s:
                bear_extra += s.count(p)
                s = s.replace(p, " ")
        b = sum(s.count(w) for w in _BULL_WORDS)
        s_ = sum(s.count(w) for w in _BEAR_WORDS) + bear_extra
        if b == 0 and s_ == 0:
            return None
        stance = "long" if b > s_ else ("short" if s_ > b else "neutral")
        # 启发式来得的分信心**压低到 0.6**：文本抽取只配当兜底，不该压过结构化回答
        return {"stance": stance, "confidence": min(0.6, 0.5 + 0.08 * abs(b - s_)),
                "argument": seg.strip()[:220] or s.strip()[:220], "derived": True}

    hit = _score(seg)
    if hit:
        return hit
    head = _re.split(r"[：:]", text, maxsplit=1)[0]
    return _score(head) if head and head != text else None


def _evidence_from_pack(pack: Any, symbol: str) -> Tuple[List[str], Dict[str, Any], Dict[str, List[str]]]:
    """从主脑真实上下文里抽取**给辩论用的证据**（含六分析师信号），并按周期分组。

    [轮130 周期化] 证据带上【日内】【中期】【长期趋势】标签：牛/熊必须按周期引用对应证据，
    否则"周期"只是嘴上说说。返回 (evidence_list, context_dict, per_horizon_evidence)。
    """
    ev: List[str] = []
    by_hz: Dict[str, List[str]] = {k: [] for k in HORIZON_KEYS}
    ctx: Dict[str, Any] = {}
    layers = getattr(pack, "layers", None) or {}
    sym = str(symbol).upper()

    mkt = ((layers.get("market") or {}).get("symbols") or {}).get(sym) or {}
    if mkt:
        ctx["momentum"] = (mkt.get("ret_24h_pct") or 0) / 100.0
        ctx["trend_alignment"] = {"bullish": 1.0, "bearish": -1.0}.get(str(mkt.get("ema_trend_1h")), 0.0)
        ctx["volatility_value"] = (mkt.get("atr14_1h_pct") or 0) / 100.0
        # ── 日内 ──
        by_hz["intraday"].append(
            f"1h 结构 ema_trend_1h={mkt.get('ema_trend_1h')} rsi14_1h={mkt.get('rsi14_1h')} "
            f"atr14_1h={mkt.get('atr14_1h_pct')}%")
        by_hz["intraday"].append(
            f"24h 表现 ret_24h={mkt.get('ret_24h_pct')}% 区间位置 pos24={mkt.get('pos24_pct')} "
            f"（{mkt.get('range_24h_low')}~{mkt.get('range_24h_high')}）")
        # ── 中期 ──
        by_hz["swing"].append(
            f"4h 结构 ema_trend_4h={mkt.get('ema_trend_4h')} regime={mkt.get('regime')}"
            f"(conf {mkt.get('confidence')})")
        by_hz["swing"].append(
            f"中期收益 ret_7d={mkt.get('ret_7d_pct')}% ret_30d={mkt.get('ret_30d_pct')}% "
            f"rsi14_1d={mkt.get('rsi14_1d')} rv30={mkt.get('rv30_annual_pct')}%")
        # ── 长期趋势 ──
        by_hz["trend"].append(
            f"1d 结构 above_ema200_1d={mkt.get('above_ema200_1d')} 距EMA200={mkt.get('dist_ema200_pct')}% "
            f"ret_30d={mkt.get('ret_30d_pct')}% atr14_1d={mkt.get('atr14_1d_pct')}%")
        ev.append(f"量价：ret_24h={mkt.get('ret_24h_pct')}% pos24={mkt.get('pos24_pct')} "
                  f"rsi1h={mkt.get('rsi14_1h')} regime={(mkt.get('regime') or '?')}")

    fl = layers.get("flows") or {}
    fund = (fl.get("funding_8h_pct") or {}).get(sym)
    if fund:
        s = f"资金费 {json.dumps(fund, ensure_ascii=False)[:120]}"
        ev.append(s)
        by_hz["intraday"].append(s + "（拥挤度的即时读数）")
    ps = (fl.get("position_structure") or {}).get(sym)
    if ps:
        s = f"持仓结构 OI={ps.get('oi_usd')} ls={ps.get('global_ls')}"
        ev.append(s)
        by_hz["intraday"].append(s)
        ctx["leverage"] = 1.0
    liq = (fl.get("liquidations_24h") or {}).get(sym)
    if liq:
        s = f"24h 清算 long={liq.get('long_liq_usd')} short={liq.get('short_liq_usd')}"
        ev.append(s)
        by_hz["intraday"].append(s)
    for e in (fl.get("events") or [])[:3]:
        if e.get("title"):
            t = f"事件 sev={e.get('sev')} {str(e.get('title'))[:90]}"
            ev.append(t)
            by_hz["intraday"].append(t)

    an = ((layers.get("analysts") or {}).get("symbols") or {}).get(sym) or {}
    if an:
        parts = [f"{k}={v.get('score')}(conf{v.get('conf')})" for k, v in list(an.items())[:6]]
        ev.append("六分析师信号：" + " ".join(parts))
        if "technical" in an:
            ctx["composite_score"] = float((an.get("technical") or {}).get("score") or 0.0)
            by_hz["swing" if primary_horizon("mid") == "intraday" else "intraday"].append(
                f"量价信号 technical={an['technical'].get('score')}")
        for dom, hz in (("sentiment", "intraday"), ("flow", "intraday"),
                        ("technical", "swing"), ("fundamental", "trend")):
            if dom in an:
                by_hz[hz].append(f"{dom} 信号={an[dom].get('score')}(conf{an[dom].get('conf')})")
    gmacro = ((layers.get("analysts") or {}).get("global") or {}).get("macro")
    if gmacro:
        s = f"宏观（全局）：score={gmacro.get('score')} conf={gmacro.get('conf')}"
        ev.append(s)
        by_hz["trend"].append(s + "（战略报告的周期阶段/风险预算）")

    fa = ((layers.get("factors") or {}).get("symbols") or {}).get(sym) or {}
    route = fa.get("route") or {}
    if route:
        s = f"因子路线：action={route.get('action')} score={route.get('score')} n={route.get('n')}"
        ev.append(s)
        by_hz["swing"].append(s)
        try:
            ctx["composite_score"] = float(route.get("score") or ctx.get("composite_score") or 0.0)
        except Exception:  # noqa: BLE001
            pass
    return ev[:8], ctx, by_hz


def run_debate_for_thesis(
    *,
    symbol: str,
    tier: str,
    conviction: float,
    direction: str,
    pack: Any = None,
    extras: Optional[Dict[str, Any]] = None,
    regime: str = "",
    thesis_id: str = "",
    session_id: str = "",
) -> Optional[Dict[str, Any]]:
    """跑一次对抗辩论；返回结果 dict（含 verdict/net/risk/used_llm/persist_rows），被闸门拦下则 None。"""
    gate = _gate(symbol, conviction, tier)
    if gate:
        logger.debug("[Debate] 跳过 %s %s：%s", symbol, tier, gate)
        return None

    try:
        from backend.services.mlto.debate_layer import AdversarialDebateLayer
    except Exception as exc:  # noqa: BLE001
        _STATS["errors"] += 1
        logger.debug("[Debate] layer 导入失败: %s", exc)
        return None

    use_llm = _flag("MIDLONG_DEBATE_LLM", "true")
    evidence, ctx, ev_by_hz = _evidence_from_pack(pack, symbol)
    ctx.update({"tier": tier, "regime": regime or "",
                "has_position": bool((extras or {}).get("current_position"))})
    proposal = {"symbol": symbol, "tier": tier,
                "direction": direction if direction in ("long", "short") else "neutral",
                "confidence": max(0.0, min(1.0, float(conviction) / 100.0))}

    recorder: List[Tuple[str, str]] = []
    llm_client = None
    if use_llm:
        try:
            llm_client = _make_llm_client(tier, recorder=recorder)
        except Exception as exc:  # noqa: BLE001
            _STATS["errors"] += 1
            logger.warning("[Debate] LLM 客户端不可用，规则降级: %s", exc)
            llm_client = None

    # 证据按周期分组传入：辩论层把 evidence 原样交给牛/熊，故分组标签本身即"周期锚"。
    # [轮130 修正] 首版用全局 `[:12]` 截断 ⇒ 【日内】条目多时会把【长期趋势】整段挤掉，
    # 实测牛方因此抱怨"缺乏距EMA200/宏观证据"（而那些证据其实采到了）。
    # 现在**按周期配额**（每周期 ≤4 条，合计 ≤12），保证三档都有话说。
    ev_sorted: List[str] = []
    for k in HORIZON_KEYS:
        for t in (ev_by_hz.get(k) or [])[:4]:
            ev_sorted.append(f"【{HORIZON_CN[k]}】{t}")
    if len(ev_sorted) < 12:
        for e in evidence:
            if not any(e in v for v in ev_by_hz.values()):
                ev_sorted.append(e)
            if len(ev_sorted) >= 12:
                break
    layer = AdversarialDebateLayer(llm_client=llm_client,
                                   max_rounds=int(_num("MIDLONG_DEBATE_MAX_ROUNDS", 2)))
    t0 = time.time()
    res = layer.debate(proposal, ctx, ev_sorted[:12])
    used_llm = llm_client is not None
    _STATS["runs"] += 1
    _STATS["llm_runs" if used_llm else "rule_runs"] += 1
    with _LOCK:
        _LAST_BY_SYMBOL[str(symbol).upper()] = time.time()
        _HOURLY.append(time.time())

    # ── 分周期裁决（用户指令：牛熊分析必须明确周期）──
    _bull_raw = next((t for r, t in recorder if r == "bull"), "")
    _bear_raw = next((t for r, t in recorder if r == "bear"), "")
    bull_hz = _parse_horizons(_bull_raw) if recorder else \
        {k: {"stance": "unknown", "confidence": 0.5, "argument": ""} for k in HORIZON_KEYS}
    bear_hz = _parse_horizons(_bear_raw) if recorder else \
        {k: {"stance": "unknown", "confidence": 0.5, "argument": ""} for k in HORIZON_KEYS}
    risk_min = min((float(t.confidence) for t in res.risk_turns), default=0.5)
    horizons: Dict[str, Any] = {}
    insufficient: List[str] = []
    for k in HORIZON_KEYS:
        b, r = dict(bull_hz.get(k, {})), dict(bear_hz.get(k, {}))
        # 单侧未按周期作答 → 先从总论点文本里按周期分句兜底抽取（并标记 derived）
        if str(b.get("stance")) == "unknown":
            b = _derive_horizon_stance(_bull_raw, HORIZON_CN[k]) or b
        if str(r.get("stance")) == "unknown":
            r = _derive_horizon_stance(_bear_raw, HORIZON_CN[k]) or r
        b_known = str(b.get("stance")) != "unknown"
        r_known = str(r.get("stance")) != "unknown"
        if b_known and r_known:
            net_h = float(b.get("confidence", 0.5)) - float(r.get("confidence", 0.5))
            verdict_h = _horizon_verdict(net_h, risk_min)
        else:
            # 两侧没有可比立场 ⇒ 该周期**证据不足**，不产生裁决（既不拦也不放，如实记录）
            net_h, verdict_h = None, "insufficient"
            insufficient.append(k)
        horizons[k] = {
            "cn": HORIZON_CN[k],
            "bull": round(float(b.get("confidence", 0.5)), 3),
            "bear": round(float(r.get("confidence", 0.5)), 3),
            "bull_stance": b.get("stance"), "bear_stance": r.get("stance"),
            "derived": {"bull": bool(b.get("derived")), "bear": bool(r.get("derived"))},
            "net": (round(net_h, 3) if net_h is not None else None),
            "verdict": verdict_h,
            # [轮142 2026-09-20 用户指令] 该周期的**方向**：与裁决同口径（net ±0.15 分档）
            # —— 风控官据此只否决"方向与辩论相反"的探针，同向放行。
            "direction": ("long" if (net_h is not None and net_h >= _VERDICT_REDUCE_NET)
                          else ("short" if (net_h is not None and net_h <= _VERDICT_REJECT_NET)
                                else "neutral")),
            "bull_arg": str(b.get("argument") or "")[:220],
            "bear_arg": str(r.get("argument") or "")[:220],
        }
    prim = primary_horizon(tier)
    stances = {k: h["verdict"] for k, h in horizons.items()}
    conflict = len({v for v in stances.values() if v != "insufficient"}) > 1
    _prim_dir = str((horizons.get(prim) or {}).get("direction") or "neutral")

    out: Dict[str, Any] = {
        "verdict": res.final_verdict,                 # 辩论层总口径（含风险共识）
        "primary_horizon": prim,
        "horizons": horizons,
        "horizon_verdicts": stances,
        "horizon_conflict": conflict,
        "horizons_insufficient": insufficient,
        # [轮142] 主周期的**方向**（风控官据此只否决"方向相反"的探针，同向放行）
        "primary_direction": _prim_dir,
        "primary_verdict": horizons.get(prim, {}).get("verdict", res.final_verdict),
        "net_sentiment": float(res.net_sentiment),
        "consensus_confidence": float(res.consensus_confidence),
        "risk_min": round(risk_min, 3),
        "used_llm": used_llm,
        "elapsed_s": round(time.time() - t0, 2),
        "evidence_by_horizon": {k: v[:4] for k, v in ev_by_hz.items()},
        "evidence": ev_sorted[:8],
        "context": {k: v for k, v in ctx.items() if isinstance(v, (int, float, str, bool))},
        "bull": [t.argument for t in res.bull_turns][:2],
        "bear": [t.argument for t in res.bear_turns][:2],
        "risk": [{"role": t.role, "conf": t.confidence, "arg": t.argument[:160]} for t in res.risk_turns],
    }
    out["persist_rows"] = _persist(thesis_id, symbol, tier, proposal, out)
    logger.info(
        "[MidLongBrain] 辩论 %s %s 主周期=%s(%s) 日内=%s 中期=%s 长期=%s 冲突=%s 证据不足=%s risk=%.2f llm=%s %.1fs",
        symbol, tier, HORIZON_CN[prim], out["primary_verdict"],
        stances.get("intraday"), stances.get("swing"), stances.get("trend"),
        conflict, (insufficient or "无"), risk_min, used_llm, out["elapsed_s"],
    )
    return out


def _persist(thesis_id: str, symbol: str, tier: str, proposal: Dict[str, Any], out: Dict[str, Any]) -> int:
    """写 bull/bear 两行（沿用既有表结构 `mlto_debate_log`，此前 0 行）。

    [轮131] content_json 里**必须**带分周期字段：风控官（risk_officer）要靠它读"辩论风险姿态"，
    否则两环之间又靠内存变量传值 —— 跨进程（子进程写论题、主进程执行开仓）就会丢。
    """
    try:
        import uuid

        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.mlto.db_models import MltoDebateLog

        rows = 0
        with AnalyticsSessionLocal() as db:
            for side, args in (("bull", out.get("bull") or []), ("bear", out.get("bear") or [])):
                db.add(MltoDebateLog(
                    debate_id=str(uuid.uuid4()),
                    thesis_id=str(thesis_id or ""),
                    round_num=1,
                    side=side,
                    content_json=json.dumps({
                        "symbol": symbol, "tier": tier, "proposal": proposal,
                        "verdict": out["verdict"], "net_sentiment": out["net_sentiment"],
                        "consensus_confidence": out["consensus_confidence"],
                        "used_llm": out["used_llm"], "arguments": args,
                        "risk": out.get("risk"), "evidence": out.get("evidence"),
                        # ── 分周期（风控官与画布都读这些字段） ──
                        "primary_horizon": out.get("primary_horizon"),
                        "primary_direction": out.get("primary_direction"),
                        "primary_verdict": out.get("primary_verdict"),
                        "horizon_verdicts": out.get("horizon_verdicts"),
                        "horizon_conflict": out.get("horizon_conflict"),
                        "horizons_insufficient": out.get("horizons_insufficient"),
                        "risk_min": out.get("risk_min"),
                        # [轮146] 落库分周期明细（net/方向/裁决）：轮145 模拟时发现**缺这一项**
                        # ⇒ 只能拿总 net 做代理，无法精确评估"只否强反向"这类阈值方案。
                        "horizons": out.get("horizons"),
                    }, ensure_ascii=False)[:4000],
                    cited_event_ids_json=json.dumps([]),
                ))
                rows += 1
            db.commit()
        _STATS["persist_rows"] += rows
        return rows
    except Exception as exc:  # noqa: BLE001
        _STATS["errors"] += 1
        logger.debug("[Debate] 落库失败（不阻塞主链）: %s", exc)
        return 0


def debate_context(symbol: str, tier: str = "", *, hours: float = 3.0) -> Optional[Dict[str, Any]]:
    """读最近一次辩论的**风险姿态**（给风控官用）。

    为什么读库而不是读内存：辩论跑在主脑子进程里，而开仓执行在主进程 —— 跨进程传值必丢。
    """
    try:
        from sqlalchemy import text

        from backend.database.connection import analytics_engine

        with analytics_engine.connect() as c:
            row = c.execute(text(
                "select ts, content_json from mlto_debate_log "
                "where side = 'bull' and content_json like :pat "
                "and ts >= now() - make_interval(secs => :secs) "
                "order by id desc limit 1"),
                {"pat": f'%"symbol": "{str(symbol).upper()}"%', "secs": float(hours) * 3600},
            ).fetchone()
        if not row:
            return None
        d = json.loads(row[1] or "{}")
        if tier and str(d.get("tier") or "").lower() != str(tier).lower():
            return None
        return {
            "ts": str(row[0])[:19],
            "verdict": d.get("verdict"),
            "primary_horizon": d.get("primary_horizon"),
            "primary_direction": d.get("primary_direction"),
            "primary_verdict": d.get("primary_verdict"),
            "horizon_verdicts": d.get("horizon_verdicts"),
            "horizon_conflict": d.get("horizon_conflict"),
            "risk_min": d.get("risk_min"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Debate] debate_context 读取失败: %s", exc)
        return None


def apply_conviction_effect(conviction: float, result: Optional[Dict[str, Any]]) -> Tuple[float, str]:
    """**有界**生效，且**按周期**取用（用户指令：牛熊分析必须明确周期）。

    ⚠️ [轮136 2026-09-20 实测教训] 这个函数的返回值**不能写回 `dto.llm_conviction`**：
    该字段同时是下游门槛的 confidence 输入（`[V5Gate] rule=confidence` 要求 ≥30%），
    辩论 ×0.6 把 40 折到 24 后，提案以 25~28% **被 v5gate 硬拦**（UNI/BTC 实测 20 次）——
    "去风险"变成了"事实否决"，与架构分工相反（否决权属风控官）。
    因此 `MIDLONG_DEBATE_APPLY` 默认 **false**：只记录、不改写；折减值留给规模链消费
    （或由 `risk_officer` 按辩论姿态行使否决）。

    生效依据 = 该 tier 的**主周期**裁决（mid→日内，long→长期趋势），而不是跨周期平均的总 verdict
    —— 否则「日内看空、长期看多」会被平均成"什么都没说"。
    reject→×0.6（下限 10）/ reduce→×0.85 / proceed→不变。
    """
    if not result or not _flag("MIDLONG_DEBATE_APPLY", "false"):
        return float(conviction), "off"
    prim = str(result.get("primary_horizon") or "")
    v = str(result.get("primary_verdict") or result.get("verdict") or "")
    c = float(conviction)
    tag = f"{HORIZON_CN.get(prim, prim)}:{v}"
    if v == "insufficient":
        # 主周期两侧没有可比立场（某侧没按周期作答且文本也抽不出）⇒ 不生效，如实标注
        return c, f"{tag}→不生效(缺可比立场)"
    # 乘数可调（实测：×0.6 会把 40 压到 24 —— 对中线是小仓探针可接受的去风险，
    # 但若发现压得过狠导致车道不动，用 MIDLONG_DEBATE_REJECT_MULT / _REDUCE_MULT 调，
    # 不必改代码；下限 MIDLONG_DEBATE_CONVICTION_FLOOR 保证不会压到"事实静默"）。
    floor = _num("MIDLONG_DEBATE_CONVICTION_FLOOR", 10)
    if v == "reject":
        _STATS["apply_reject"] += 1
        m = _num("MIDLONG_DEBATE_REJECT_MULT", 0.6)
        return max(floor, c * m), f"{tag}→×{m}"
    if v == "reduce":
        _STATS["apply_reduce"] += 1
        m = _num("MIDLONG_DEBATE_REDUCE_MULT", 0.85)
        return max(floor, c * m), f"{tag}→×{m}"
    return c, f"{tag}→不变"


def status() -> Dict[str, Any]:
    """给画布/审计用：闸门状态 + 计数（含被拦原因分布）。"""
    now = time.time()
    with _LOCK:
        recent = [t for t in _HOURLY if now - t < 3600]
        last = {k: datetime.fromtimestamp(v).strftime("%H:%M:%S") for k, v in _LAST_BY_SYMBOL.items()}
    return {
        "enabled": enabled(),
        "llm": _flag("MIDLONG_DEBATE_LLM", "true"),
        "apply": _flag("MIDLONG_DEBATE_APPLY", "true"),
        "transport": _llm_transport(),
        "max_rounds": int(_num("MIDLONG_DEBATE_MAX_ROUNDS", 2)),
        "cooldown_s": _num("MIDLONG_DEBATE_COOLDOWN_S", 120),
        "hourly_cap": _num("MIDLONG_DEBATE_HOURLY_CAP", 0),
        "horizons": [{"key": k, "cn": cn, "evidence_scope": scope} for k, cn, scope in HORIZONS],
        "tier_primary_horizon": dict(TIER_PRIMARY_HORIZON),
        "runs_last_hour": len(recent),
        "last_by_symbol": last,
        "stats": dict(_STATS),
        "producer": PRODUCER,
    }
