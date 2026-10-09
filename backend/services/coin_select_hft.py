# -*- coding: utf-8 -*-
"""[F248] AI 选币 · 超短（做市）专用链路

设计依据：`docs/AI选币_超短交易专用设计.md`。

## 与现有选币链（`coin_select_platform_service`）的关系

**并行，不改动现有链路**。现有链路服务方向性中长线（`horizon ∈ {mid,long}`），
本模块服务**被动双边挂单**。两者选币量根本不同：

    方向性：预测价格方向 → 排序量是动量/资金/事件
    做市  ：赚点差 − 逆选择 → 排序量是 **点差宽度 × 吞吐**（方向无关）

## 第一原则（实证依据，务必保留）

H13 已证：**方向/动量对做市净额无预测力**（`spread`/`gap` 的高 IC 无法转成每笔进场
正净额：无条件均值 −0.426bp t=−23.8；最好子集 +0.047bp t=+0.7）；
而往返净 ≈ **整个点差**（H8/H10 双实现复现 ASTER +1.383/+1.375bp）。

⇒ **机械量排序，LLM 只做事件风险否决**（见 `llm_veto`）。
让 LLM 按"方向动量"选做市标的 = 用错误的维度选币。

## 三层结构

    ① hard_gates()   ：不可交易直接排除（无深度/停采/点差过窄）
    ② score()        ：机械评分（点差 × 吞吐，扣队列拥挤）
    ③ llm_veto()     ：窄契约风险否决（不参与排序）

## 命名（本仓库已踩 4 次）

    `market_trades_aggregated.symbol` = 裸标的（`BTC`）
    `asterdex_book_ticker.symbol`     = 带后缀（`BTCUSDT`）
    `asterdex_depth_snapshots.symbol` = 带后缀（`ASTERUSDT`）
    `lane_registry.meta.symbols`      = 裸标的（`ASTER`）
⇒ 对外一律**裸标的**，进出库时用 `to_venue_symbol()` 拼后缀。
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 硬闸阈值（可 env 覆盖）
MIN_SPREAD_BP = float(os.getenv("HFT_MIN_SPREAD_BP", "0.8"))
MIN_BOOK_UPDATES = int(os.getenv("HFT_MIN_BOOK_UPDATES", "5000"))
MAX_AGE_SEC = float(os.getenv("HFT_MAX_AGE_SEC", "1800"))


def to_bare(symbol: str) -> str:
    u = str(symbol or "").strip().upper()
    for suf in ("USDT", "USDC", "USD"):
        if u.endswith(suf) and len(u) > len(suf):
            return u[: -len(suf)]
    return u


def to_venue(symbol: str) -> str:
    s = to_bare(symbol)
    return f"{s}USDT" if s else ""


@dataclass
class HftStats:
    """单标的的超短做市统计量（全部来自已落库数据，无前视）。"""
    symbol: str
    spread_med_bp: Optional[float] = None
    spread_p25_bp: Optional[float] = None
    book_updates: int = 0
    book_age_s: Optional[float] = None
    trades_total: int = 0
    depth_age_s: Optional[float] = None
    has_depth: bool = False
    # 评分结果
    score: Optional[float] = None
    passed: bool = False
    reject_reason: Optional[str] = None


@dataclass
class UniverseDecision:
    """选币结果。`fixed` ∪ `ai` 才是最终交易宇宙。"""
    fixed: List[str] = field(default_factory=list)
    ai: List[str] = field(default_factory=list)
    as_of: str = ""
    scored: List[Dict[str, Any]] = field(default_factory=list)
    rejected: Dict[str, str] = field(default_factory=dict)
    vetoed: Dict[str, str] = field(default_factory=dict)
    note: str = ""

    @property
    def symbols(self) -> List[str]:
        seen, out = set(), []
        for s in list(self.fixed) + list(self.ai):
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def to_meta(self) -> Dict[str, Any]:
        return {
            "fixed": self.fixed, "ai": self.ai, "as_of": self.as_of,
            "scored": self.scored, "rejected": self.rejected,
            "vetoed": self.vetoed, "note": self.note,
            "symbols": self.symbols,
        }


# ═══════════════════════════════════════════════════════════════
# ① 统计量（只读，不做判定）
# ═══════════════════════════════════════════════════════════════

# 统计量缓存。**为什么必须有**：实测三个查询合计约 20s——
#   · `asterdex_book_ticker`（1.03 亿行）近 2h 分位：11.4s
#   · `asterdex_depth_snapshots`（2680 万行）近 1h 分组：7.9s
#   · `asterdex_trades` 分组：0.3s
# 而宇宙是**每 30 分钟**才重算一次的，前端若每 120s 触发一次就是纯浪费。
# TTL 取 300s：既能让前端拿到近乎实时的评分（评分本身变化很慢），
# 又不会让查询频率超过数据本身的变化速度。
_STATS_CACHE: Dict[str, Any] = {"ts": 0.0, "key": None, "val": None}
_STATS_TTL_SEC = 300.0


def _cache_key(symbols: Optional[Sequence[str]]) -> Optional[tuple]:
    return tuple(sorted(to_bare(s) for s in symbols)) if symbols else None


def compute_hft_stats(symbols: Optional[Sequence[str]] = None, *,
                      force: bool = False) -> Dict[str, HftStats]:
    """拉取每个标的的点差 / 盘口更新数 / 成交数 / 深度状态（**带 300s 缓存**）。

    点差用**近 2 小时**（不是 3.9 天全历史）：实测全历史中位会把已收窄的点差
    算高（ARB 全历史 11.5bp vs 近期 1.5bp，差 7 倍）。

    `force=True` 跳过缓存（调度任务重算宇宙时应传，保证不是陈旧统计）。
    """
    import time as _t

    key = _cache_key(symbols)
    now = _t.time()
    if (not force and _STATS_CACHE["val"] is not None
            and _STATS_CACHE["key"] == key
            and (now - _STATS_CACHE["ts"]) < _STATS_TTL_SEC):
        return dict(_STATS_CACHE["val"])

    out = _compute_hft_stats_uncached(symbols)
    _STATS_CACHE.update({"ts": now, "key": key, "val": dict(out)})
    return out


def _compute_hft_stats_uncached(symbols: Optional[Sequence[str]] = None) -> Dict[str, HftStats]:
    from sqlalchemy import text as sa_text

    from backend.database.connection import MarketSessionLocal

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = now_ms - 2 * 3600 * 1000
    out: Dict[str, HftStats] = {}

    with MarketSessionLocal() as db:
        # 点差 + 更新数（近 2h）
        rows = db.execute(
            sa_text(
                "SELECT symbol, count(*) n, "
                "  percentile_disc(0.5) WITHIN GROUP (ORDER BY "
                "    (ask_px - bid_px) / nullif((ask_px + bid_px)/2, 0) * 1e4) AS spr_med, "
                "  percentile_disc(0.25) WITHIN GROUP (ORDER BY "
                "    (ask_px - bid_px) / nullif((ask_px + bid_px)/2, 0) * 1e4) AS spr_p25, "
                "  max(event_ts_ms) AS last_ts "
                "FROM asterdex_book_ticker WHERE event_ts_ms >= :since "
                "GROUP BY symbol"
            ),
            {"since": since_ms},
        ).fetchall()
        for sym, n, med, p25, last in rows:
            b = to_bare(sym)
            out[b] = HftStats(
                symbol=b,
                spread_med_bp=float(med) if med is not None else None,
                spread_p25_bp=float(p25) if p25 is not None else None,
                book_updates=int(n or 0),
                book_age_s=((now_ms - int(last)) / 1000.0) if last else None,
            )

        # 深度状态（近 1h 有落库 = 在采）
        drows = db.execute(
            sa_text(
                "SELECT symbol, max(event_ts_ms) AS last_ts FROM asterdex_depth_snapshots "
                "WHERE event_ts_ms >= :since GROUP BY symbol"
            ),
            {"since": now_ms - 3600 * 1000},
        ).fetchall()
        dset = {}
        for sym, last in drows:
            dset[to_bare(sym)] = ((now_ms - int(last)) / 1000.0) if last else None

        # 成交笔数（全历史，作为活跃度代理；逐笔表冷门币可能长期无成交）
        trows = db.execute(
            sa_text("SELECT symbol, count(*) FROM asterdex_trades GROUP BY symbol")
        ).fetchall()
        tset = {to_bare(s): int(n or 0) for s, n in trows}

    for b, st in out.items():
        st.has_depth = b in dset
        st.depth_age_s = dset.get(b)
        st.trades_total = tset.get(b, 0)

    if symbols:
        want = {to_bare(s) for s in symbols}
        # 允许请求的币即使近 2h 无盘口也返回（标记为不可用）
        for b in want - set(out):
            out[b] = HftStats(symbol=b, has_depth=b in dset, depth_age_s=dset.get(b),
                              trades_total=tset.get(b, 0))
        out = {k: v for k, v in out.items() if k in want}
    return out


# ═══════════════════════════════════════════════════════════════
# ② 硬闸 + 机械评分
# ═══════════════════════════════════════════════════════════════

def hard_gates(st: HftStats) -> Optional[str]:
    """返回拒绝原因；None = 通过。顺序即优先级（错误信息取首个命中）。"""
    if not st.has_depth:
        return "无 20 档深度采集（无法做队列感知模拟）"
    if st.depth_age_s is None or st.depth_age_s > MAX_AGE_SEC:
        return f"深度停采（age={st.depth_age_s:.0f}s > {MAX_AGE_SEC:.0f}s）" \
            if st.depth_age_s is not None else "无深度数据"
    if st.spread_med_bp is None:
        return "近 2h 无有效盘口（点差不可得）"
    if st.spread_med_bp < MIN_SPREAD_BP:
        return f"点差过窄（{st.spread_med_bp:.3f}bp < {MIN_SPREAD_BP}bp）——往返净为负"
    if st.book_updates < MIN_BOOK_UPDATES:
        return f"盘口更新过少（{st.book_updates} < {MIN_BOOK_UPDATES}）"
    if st.book_age_s is not None and st.book_age_s > MAX_AGE_SEC:
        return f"盘口停更（age={st.book_age_s:.0f}s）"
    return None


def score(st: HftStats) -> Optional[float]:
    """机械评分：点差（毛收益）× log10(吞吐) − 队列拥挤惩罚。

    ⚠️ `score` 只用于**相对排序**，不代表可盈利（H13 已证现有特征无法把
    每笔进场净额拉正）。它的正确用途是在同样为负的候选里挑最不差的。
    """
    if st.spread_med_bp is None or st.book_updates <= 0:
        return None
    throughput = max(st.book_updates, 1)
    base = st.spread_med_bp * math.log10(throughput)
    # 队列拥挤惩罚：盘口更新越密 ⇒ 竞争者越多 ⇒ 排队越靠后（对数衰减，避免压倒点差项）
    crowd = math.log10(throughput) * 0.15
    return base - crowd


def select_universe(
    *,
    fixed: Sequence[str],
    eligible: Optional[Sequence[str]] = None,
    ai_slots: int = 5,
    vetoed: Optional[Dict[str, str]] = None,
    force_stats: bool = False,
) -> UniverseDecision:
    """按机械评分选出 AI 位；`fixed` 不参与评分（人工指定、全天候交易）。

    `force_stats=True` 强制重算统计量（调度任务重算宇宙时应传）。
    """
    fixed_b = [to_bare(s) for s in fixed if s]
    stats = compute_hft_stats(eligible, force=force_stats)
    vetoed = {to_bare(k): v for k, v in (vetoed or {}).items()}

    passed: List[Tuple[float, HftStats]] = []
    rejected: Dict[str, str] = {}
    for b, st in stats.items():
        if b in fixed_b:
            continue
        if b in vetoed:
            rejected[b] = f"LLM 否决: {vetoed[b]}"
            continue
        reason = hard_gates(st)
        if reason:
            rejected[b] = reason
            st.reject_reason = reason
            continue
        sc = score(st)
        if sc is None:
            rejected[b] = "评分不可得"
            continue
        st.score = sc
        passed.append((sc, st))

    passed.sort(key=lambda x: -x[0])
    ai = [st.symbol for _, st in passed[: max(0, int(ai_slots))]]
    for _, st in passed[: max(0, int(ai_slots))]:
        st.passed = True

    scored = []
    for sc, st in passed:
        d = asdict(st)
        d["selected"] = st.symbol in ai
        scored.append(d)

    return UniverseDecision(
        fixed=fixed_b, ai=ai,
        as_of=datetime.now(timezone.utc).isoformat(),
        scored=scored, rejected=rejected,
        vetoed=vetoed,
        note=("机械评分（点差×吞吐）排序；LLM 仅做风险否决。"
              "score>0 不代表可盈利，只代表相对占优——见 docs/AI选币_超短交易专用设计.md §2.2"),
    )


# ═══════════════════════════════════════════════════════════════
# ③ LLM 风险否决（窄契约，不参与排序）
# ═══════════════════════════════════════════════════════════════

VETO_PROMPT = """你是做市风控审核员。下面每个币都有客观盘口统计。你的**唯一任务**是判断未来数小时
是否会发生让**被动挂单被系统性逆向选择**的事件（重大公告、资金费率极端、上线/下线、链上异常、
流动性骤变）。你**不排序、不打分、不建议方向**。

只输出 JSON 数组，每项：
{"symbol":"XXX","veto":true|false,"severity":"low|medium|high","reason":"必须引用给定数字"}

规则：severity=high 必须 veto=true；reason 必须引用给定统计里的具体数字；无异常则 veto=false。

统计：
"""


def llm_veto(candidates: Sequence[Dict[str, Any]], *, timeout: int = 60) -> Dict[str, Any]:
    """调 LLM 做风险否决。**失败一律返回空否决**（fail-open）——

    理由：LLM 不可用不应阻塞选币；而误否决会让宇宙凭空变小（更难发现）。
    风险由机械硬闸兜底，不靠 LLM。
    """
    if not candidates:
        return {}
    try:
        from backend.services.llm_client import get_llm_client  # type: ignore
    except Exception:
        try:
            from backend.services.llm_client import llm_client as _c  # type: ignore
            get_llm_client = lambda: _c  # noqa: E731
        except Exception as e:  # noqa: BLE001
            logger.info("[HFT选币] LLM 客户端不可用，跳过否决: %s", e)
            return {}

    payload = json.dumps(candidates, ensure_ascii=False)
    try:
        client = get_llm_client()
        resp = client.chat(VETO_PROMPT + payload, timeout=timeout) \
            if hasattr(client, "chat") else None
        text = resp if isinstance(resp, str) else getattr(resp, "content", None)
        if not text:
            return {}
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            text = text[text.find("["):] if "[" in text else text
        items = json.loads(text)
        out: Dict[str, Any] = {}
        for it in items if isinstance(items, list) else []:
            sym = to_bare(it.get("symbol"))
            if not sym:
                continue
            sev = str(it.get("severity") or "low").lower()
            veto = bool(it.get("veto")) or sev == "high"
            if veto:
                out[sym] = {"severity": sev, "reason": str(it.get("reason") or "")[:200]}
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("[HFT选币] LLM 否决失败（fail-open，不阻塞）: %s", e)
        return {}


# ═══════════════════════════════════════════════════════════════
# ④ 落库
# ═══════════════════════════════════════════════════════════════

def apply_to_lane(lane_id: str, decision: UniverseDecision, *, dry_run: bool = False) -> Dict[str, Any]:
    """把选币结果写入 `lane_registry.meta_json`（`symbols` 由 fixed ∪ ai 派生）。"""
    from sqlalchemy import text as sa_text

    from backend.database.connection import SessionLocal

    with SessionLocal() as db:
        row = db.execute(
            sa_text("SELECT meta_json FROM lane_registry WHERE lane_id = :lid"),
            {"lid": lane_id},
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"车道不存在: {lane_id}"}
        meta = row[0]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (ValueError, TypeError):
                meta = {}
        meta = dict(meta or {})
        before = list(meta.get("symbols") or [])
        meta["universe"] = decision.to_meta()
        meta["symbols"] = decision.symbols
        if dry_run:
            return {"ok": True, "dry_run": True, "before": before, "after": decision.symbols}
        db.execute(
            sa_text("UPDATE lane_registry SET meta_json = :m, updated_at = now() WHERE lane_id = :lid"),
            {"m": json.dumps(meta, ensure_ascii=False), "lid": lane_id},
        )
        db.commit()
        return {"ok": True, "before": before, "after": decision.symbols,
                "fixed": decision.fixed, "ai": decision.ai}
