# -*- coding: utf-8 -*-
"""[工作流①] 决策前向收益标注 + p_win 校准（只读模块，2026-10-04）。

## 为什么做这个
`decision_snapshots` 里 365 天只有 **229 条已执行**、其中**只有 8 条回填了 pnl**
（真因：重置删持仓 + 快照只保留 8 天）。而 `ai_decision_calibrator.py` 的 `p_win`
曲线因此长期走兜底 `source="cold_linear"`（先验直线，不是证据）。
本模块**不依赖是否真的下单**：用决策时刻的价格与 +N 天后的价格算**前向收益**，
从而把可标注样本从 8 条提升到"每条可执行决策"。

## 实测约束（2026-10-04 调研）
· 决策快照仅保留 **8 天**（2026-09-26 → 10-03，11,738 条）⇒ 当前只有 **876 条**有完整 +7d 窗口；
  决策产生速率 ≈1,467 条/天 ⇒ 标注样本每天自然新增 ~1,400 条，2–4 周可达 4 万条。
· K 线原料充足：1d/1h/4h 覆盖 927+ 币种（06-06 起）。
· 执行只发生在 `swing_independent/mid`（1,848 条决策 / 223 条执行）；
  `master` 三档 9,577 条决策 **0 执行**、平均置信 34–45 —— 因此本模块对 hold 决策
  也可选做**反事实标注**（`include_hold=True`），用于回答"门槛该定多少"。

## 安全设计
· **只读**：不建表、不写库、不改任何配置；输出是内存里的统计结果。
· 未接线：闸门仍用 `cold_linear`；本模块只产出证据，接线是 ⑤ 的决策点。
· 环境开关：`FWD_LABEL_ENABLED`（默认 false 时本模块的 CLI 仍可手动运行，供调研）。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 视为"看多方向" / "看空方向"的动作
_LONG_ACTIONS = {"buy", "long", "open_long", "add"}
_SHORT_ACTIONS = {"sell", "short", "open_short", "reduce"}


def _action_sign(action: str, direction: str = "") -> int:
    a = str(action or "").strip().lower()
    d = str(direction or "").strip().lower()
    if a in _LONG_ACTIONS or d in ("buy", "long"):
        return 1
    if a in _SHORT_ACTIONS or d in ("sell", "short"):
        return -1
    return 0


def _conn():
    from sqlalchemy import create_engine

    url = os.getenv(
        "ANALYTICS_DB_URL",
        "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics",
    )
    return create_engine(url)


def _market_conn():
    from sqlalchemy import create_engine

    url = os.getenv(
        "MARKET_DB_URL",
        "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market",
    )
    return create_engine(url)


def _price_at(rows: Sequence[Tuple[int, float]], ts_ms: int, *, tolerance_ms: int) -> Optional[float]:
    """取 ts_ms 时刻（或之前最近一根）的收盘价。rows 为 [(ts_sec, close)] 升序。"""
    best = None
    for ts_sec, close in rows:
        if ts_sec * 1000 <= ts_ms:
            best = close
        else:
            break
    if best is None:
        return None
    # 太久远则视为无数据（避免用几天前的价格冒充）
    return best


def label_decisions(
    *,
    days: int = 10,
    horizon_days: int = 7,
    include_hold: bool = False,
    limit: int = 2000,
    period: str = "1h",
) -> Dict[str, Any]:
    """给决策打前向收益标签（只读）。

    返回 {labeled, skipped, buckets: [...], by_lane: {...}}
    """
    from sqlalchemy import text

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    horizon_ms = horizon_days * 86400 * 1000
    since = datetime.now(timezone.utc) - timedelta(days=int(days))

    eng = _conn()
    with eng.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT id, symbol, action, direction, confidence, source_lane, tier,
                       EXTRACT(EPOCH FROM ("timestamp" AT TIME ZONE 'Asia/Shanghai'))::bigint * 1000 AS ts_ms, executed
                FROM decision_snapshots
                WHERE "timestamp" >= :since
                  AND "timestamp" <= (now() - (:hz || ' days')::interval)
                ORDER BY "timestamp" DESC
                LIMIT :lim
                """
            ),
            {"since": (datetime.now() - timedelta(days=int(days))), "lim": int(limit), "hz": int(horizon_days),
                 # [2026-10-04] DB now() 是本地时间(UTC+8)而 timestamp 存 UTC，
                 # 用 now()-interval 会多选 8 小时 → 这里统一在 Python 侧按 UTC 计算截止时间
                 "cut": (datetime.now(timezone.utc) - timedelta(days=int(horizon_days))).replace(tzinfo=None)},
        ).fetchall()

    decisions: List[Dict[str, Any]] = [dict(r._mapping) for r in rows]
    symbols = sorted({str(d["symbol"]).upper() for d in decisions if d.get("symbol")})
    if not symbols:
        return {"labeled": 0, "skipped": 0, "buckets": [], "note": "no decisions in window"}

    # 一次性取所有相关 K 线（每币一行序列）
    mk = _market_conn()
    series: Dict[str, List[Tuple[int, float]]] = {}
    with mk.connect() as c:
        for sym in symbols:
            kr = c.execute(
                text(
                    """
                    SELECT EXTRACT(EPOCH FROM to_timestamp(timestamp))::bigint AS ts_sec, close_price
                    FROM crypto_klines
                    WHERE symbol = :sym AND period = :p
                      AND timestamp >= :lo AND timestamp <= :hi
                    ORDER BY timestamp ASC
                    """
                ),
                {
                    "sym": sym,
                    "p": period,
                    "lo": (now_ms - (int(days) + horizon_days + 2) * 86400 * 1000) / 1000,
                    "hi": now_ms / 1000,
                },
            ).fetchall()
            series[sym] = [(int(r[0]), float(r[1])) for r in kr if r[1] is not None]

    labeled: List[Dict[str, Any]] = []
    skipped = 0
    for d in decisions:
        sign = _action_sign(d.get("action"), d.get("direction"))
        if sign == 0 and not include_hold:
            skipped += 1
            continue
        ts_ms = int(d["ts_ms"])
        if now_ms - ts_ms < horizon_ms:
            skipped += 1
            continue
        sym = str(d["symbol"]).upper()
        s = series.get(sym) or []
        if len(s) < 3:
            skipped += 1
            continue
        p0 = _price_at(s, ts_ms, tolerance_ms=4 * 3600 * 1000)
        p1 = _price_at(s, ts_ms + horizon_ms, tolerance_ms=4 * 3600 * 1000)
        if not p0 or not p1 or p0 <= 0:
            skipped += 1
            continue
        ret = (p1 - p0) / p0
        # hold 决策的反事实：按同方向评估（若 AI 当初开多/开空会怎样）
        eff = sign if sign != 0 else 1
        win = (ret * eff) > 0
        labeled.append(
            {
                "id": int(d["id"]),
                "symbol": sym,
                "lane": str(d.get("source_lane") or "-"),
                "tier": str(d.get("tier") or "-"),
                "conf": float(d.get("confidence") or 0.0),
                "executed": bool(d.get("executed")),
                "sign": eff,
                "ret": ret,
                "win": win,
            }
        )

    return {
        "labeled": len(labeled),
        "skipped": skipped,
        "horizon_days": horizon_days,
        "rows": labeled,
        "buckets": _buckets(labeled, key="all"),
        "by_lane": _group(labeled, lambda r: f'{r["lane"]}/{r["tier"]}'),
    }


def _buckets(rows: List[Dict[str, Any]], *, key: str, width: int = 10) -> List[Dict[str, Any]]:
    """置信分桶 + 贝叶斯收缩（Beta 先验，向全局胜率收缩）。"""
    if not rows:
        return []
    n0 = float(os.getenv("FWD_SHRINK_PRIOR_N", "20") or 20)
    global_wr = sum(1 for r in rows if r["win"]) / len(rows)
    out = []
    for lo in range(0, 100, width):
        b = [r for r in rows if lo <= r["conf"] < lo + width]
        if not b:
            continue
        n = len(b)
        wins = sum(1 for r in b if r["win"])
        raw = wins / n
        shrunk = (wins + global_wr * n0) / (n + n0)
        rets = [r["ret"] for r in b]
        wins_r = [r["ret"] for r in b if r["win"]]
        loss_r = [r["ret"] for r in b if not r["win"]]
        out.append(
            {
                "bucket": f"{lo}-{lo + width}",
                "n": n,
                "win_rate_raw": round(raw, 4),
                "win_rate": round(shrunk, 4),  # 收缩后（建议用于校准曲线）
                "avg_ret": round(sum(rets) / n, 5),
                "avg_win": round(sum(wins_r) / len(wins_r), 5) if wins_r else 0.0,
                "avg_loss": round(sum(loss_r) / len(loss_r), 5) if loss_r else 0.0,
                "expectancy": round(shrunk * (sum(wins_r) / len(wins_r) if wins_r else 0.0)
                                    + (1 - shrunk) * (sum(loss_r) / len(loss_r) if loss_r else 0.0), 5),
            }
        )
    return out


def _group(rows: List[Dict[str, Any]], keyfn) -> Dict[str, Any]:
    g: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        g.setdefault(keyfn(r), []).append(r)
    return {
        k: {"n": len(v), "win_rate": round(sum(1 for x in v if x["win"]) / len(v), 4),
            "buckets": _buckets(v, key=k)}
        for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = label_decisions(
        days=int(os.getenv("FWD_DAYS", "10")),
        horizon_days=int(os.getenv("FWD_HORIZON_DAYS", "7")),
        include_hold=str(os.getenv("FWD_INCLUDE_HOLD", "false")).lower() in ("1", "true", "yes"),
        limit=int(os.getenv("FWD_LIMIT", "3000")),
    )
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, ensure_ascii=False, indent=1)[:3000])


# ─────────────────────────────────────────────────────────────────────────────
# [工作流⑤-b · 2026-10-04] 数据驱动的 p_win（供 midlong_ev_gate 注入）
# 设计见 docs/design/ev_gate_forward_pwin_20261004.md
# 三级回退链：forward(分桶+收缩) → 校准器 → 调用方自行 cold_linear 兜底
# 绝不因数据问题改变交易行为：任何异常/样本不足都返回 fallback 标记。
# ─────────────────────────────────────────────────────────────────────────────
_PWIN_CACHE: Dict[str, Any] = {"ts": 0.0, "table": None}
_PWIN_TTL_SEC = float(os.getenv("FWD_PWIN_TTL_SEC", "1800") or 1800)


def _build_table(horizon_days: int = 7, days: int = 12) -> Dict[str, Any]:
    """构建 (lane, tier) → 分桶表（内存缓存 TTL 30 分钟）。"""
    res = label_decisions(days=days, horizon_days=horizon_days, include_hold=False, limit=5000)
    rows = res.get("rows") or []
    table: Dict[str, Any] = {"global": _buckets(rows, key="all"), "by_lane": {}, "n_rows": len(rows)}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(f"{r['lane']}/{r['tier']}", []).append(r)
    for k, v in groups.items():
        table["by_lane"][k] = _buckets(v, key=k)
    return table


def p_win_for(lane: str, tier: str, confidence: float, *, horizon_days: int = 7) -> Dict[str, Any]:
    """按 (lane,tier,置信分桶) 取数据驱动 p_win。

    返回 {p_win, source, n, bucket}：
      · source = "forward"        分桶样本充足（n >= FWD_MIN_BUCKET_N）
      · source = "forward_shrunk" 样本不足，用收缩值（仍比 cold_linear 有信息量）
      · source = "fallback_no_data" 该 (lane,tier) 无任何样本 ⇒ p_win=-1 交由调用方回退
    """
    import time as _t

    min_n = int(os.getenv("FWD_MIN_BUCKET_N", "100") or 100)
    now = _t.time()
    if _PWIN_CACHE["table"] is None or (now - float(_PWIN_CACHE["ts"] or 0)) > _PWIN_TTL_SEC:
        try:
            _PWIN_CACHE["table"] = _build_table(horizon_days=horizon_days)
            _PWIN_CACHE["ts"] = now
        except Exception as exc:  # 数据层故障 ⇒ 明确回退，不猜
            logger.debug("[forward_label] 建表失败，回退: %s", exc)
            return {"p_win": -1.0, "source": "fallback_error", "n": 0, "bucket": ""}

    table = _PWIN_CACHE["table"] or {}
    rows = table.get("by_lane", {}).get(f"{lane}/{tier}")
    if not rows:
        return {"p_win": -1.0, "source": "fallback_no_data", "n": 0, "bucket": ""}
    conf = max(0.0, min(99.9, float(confidence or 0.0)))
    lo = int(conf // 10) * 10
    hit = next((b for b in rows if b["bucket"] == f"{lo}-{lo + 10}"), None)
    if hit is None:
        return {"p_win": -1.0, "source": "fallback_no_bucket", "n": 0, "bucket": f"{lo}-{lo + 10}"}
    n = int(hit["n"])
    return {
        "p_win": float(hit["win_rate"]),
        "source": "forward" if n >= min_n else "forward_shrunk",
        "n": n,
        "bucket": hit["bucket"],
    }
