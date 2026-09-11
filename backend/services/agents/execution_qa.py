# -*- coding: utf-8 -*-
"""ExecutionQA Agent（v3 方向 3，p2-agents-b）：执行质量体检。

**数据现实先说清楚**：本库没有专门的执行质量表。`paper_orders` 有意图价 `price`、成交价
`filled_price`、`fee`、`status`（含 rejected）、`filled_quantity`/`quantity`，但
**没有** maker/taker 标记（只存在于仿真器内存）、没有下单延迟、没有拒单原因字符串。
实盘侧 `trade_facts.source='live'` 的 `fees` 常年为 0（待交易所回填）。
所以本 Agent 只算**真实能算的四项**，算不了的在 `notes` 里明说，不猜：

  滑点 slippage_bp   限价单用 |成交价 − 委托价|/委托价；市价单委托价为 NULL → 用同分钟 K 线收盘做基准
  拒单率 reject_rate  status='rejected' 占比
  部分成交 partial_rate  filled_quantity < quantity 且 status='filled'
  实际费率 fee_bp     fee / 成交名义，用于和理论费率对账

分层：按币、按 order_type、按 close_reason（开仓 / tp / sl / 手动），找出问题集中在哪。

预测 kind `exec_quality`：未来 24h 平均滑点是否仍高于/低于阈值 —— 让"执行在变好还是变坏"
这件事有事后评分，而不是每天看一眼数字就过去了。
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List, Optional, Tuple

from backend.services.agents.base import (
    ACTION_PROPOSE_EXPERIMENT,
    Advice,
    ObservationAgent,
    Prediction,
    env_float,
    env_int,
    now_ms,
)

logger = logging.getLogger(__name__)

AGENT_ID = "execution_qa"
KIND_EXEC_QUALITY = "exec_quality"

# 单笔滑点超过这个量级只可能是基准价取错，不是真实执行成本
ABSURD_SLIP_BP = 5000.0


def _db():
    from backend.database.connection import SessionLocal

    return SessionLocal()


def _market_db():
    from backend.database.connection import MarketSessionLocal

    return MarketSessionLocal()


def _base_symbol(sym: Any) -> str:
    s = str(sym or "").upper().replace("-", "").replace("/", "").replace(":", "").strip()
    for suf in ("USDT", "USDC", "USD", "PERP"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s


def _minute_closes(symbols: List[str], since_ms: int, until_ms: int,
                   exchanges: Optional[List[str]] = None) -> Dict[Tuple[str, str], Dict[int, float]]:
    """{(exchange, base): {minute_ts_s: close}}，市价单滑点的基准价。

    **必须按 exchange 分键**：`crypto_klines` 里同名 symbol 在不同交易所可能是完全不同的
    资产——实测 `ON` 在 okx 约 72 美元、在 binance 约 0.23 美元（372 倍），`OPENAI` 差 10 倍，
    `BB` 差 1138 倍。早先版本用 symbol 单键索引，基准价被跨所数据互相覆盖，
    直接产出过 ±90000bp 的假滑点，把 30 天均值从 5bp 拉到 42bp。
    """
    out: Dict[Tuple[str, str], Dict[int, float]] = {}
    if not symbols:
        return out
    from sqlalchemy import bindparam, text

    where = ["period = '1m'", "symbol IN :syms", "timestamp BETWEEN :lo AND :hi"]
    params: Dict[str, Any] = {
        "syms": sorted(set(symbols)),
        "lo": int(since_ms // 1000) - 120,
        "hi": int(until_ms // 1000) + 120,
    }
    if exchanges:
        where.append("exchange IN :exs")
        params["exs"] = sorted({str(e).lower() for e in exchanges if e})

    db = _market_db()
    try:
        stmt = text(f"SELECT exchange, symbol, timestamp, close_price FROM crypto_klines "
                    f"WHERE {' AND '.join(where)}").bindparams(bindparam("syms", expanding=True))
        if exchanges:
            stmt = stmt.bindparams(bindparam("exs", expanding=True))
        rows = db.execute(stmt, params).fetchall()
    except Exception as exc:
        logger.warning("[%s] 1m K 线读取失败: %s", AGENT_ID, exc)
        return out
    finally:
        db.close()
    for ex, sym, ts, px in rows:
        try:
            out.setdefault((str(ex).lower(), str(sym).upper()), {})[(int(ts) // 60) * 60] = float(px)
        except Exception:
            continue
    return out


def load_orders(account_id: Optional[int], since_ms: int, until_ms: int,
                limit: int = 20000) -> List[Dict[str, Any]]:
    """paper_orders 原始行。created_at 是 naive 本地时间，按本地时区比较。"""
    from datetime import datetime

    from sqlalchemy import text

    where = ["created_at >= :lo", "created_at <= :hi"]
    params: Dict[str, Any] = {
        "lo": datetime.fromtimestamp(since_ms / 1000),
        "hi": datetime.fromtimestamp(until_ms / 1000),
        "lim": int(limit),
    }
    if account_id is not None:
        where.append("account_id = :acct")
        params["acct"] = int(account_id)
    db = _db()
    try:
        rows = db.execute(text(
            "SELECT id, account_id, exchange, symbol, side, order_type, price, quantity, filled_quantity, "
            "filled_price, fee, status, close_reason, trade_nature, created_at, filled_at "
            f"FROM paper_orders WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT :lim"
        ), params).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[%s] paper_orders 读取失败: %s", AGENT_ID, exc)
        return []
    finally:
        db.close()


def execution_stats(*, account_id: Optional[int] = None, since_ms: int, until_ms: int) -> Dict[str, Any]:
    """执行质量统计。experiments.metrics 与本 Agent 共用这一个口径。"""
    orders = load_orders(account_id, since_ms, until_ms)
    notes: List[str] = []
    out: Dict[str, Any] = {
        "account_id": account_id, "since_ms": since_ms, "until_ms": until_ms,
        "n_orders": len(orders), "notes": notes,
    }
    if not orders:
        notes.append("窗口内无订单")
        return out

    filled = [o for o in orders if str(o.get("status")) == "filled"]
    rejected = [o for o in orders if str(o.get("status")) == "rejected"]
    partial = [o for o in filled
               if o.get("filled_quantity") is not None and o.get("quantity")
               and float(o["filled_quantity"]) < float(o["quantity"]) * 0.999]
    out.update({
        "n_filled": len(filled), "n_rejected": len(rejected), "n_partial": len(partial),
        "reject_rate": round(len(rejected) / len(orders), 4),
        "partial_rate": round(len(partial) / len(filled), 4) if filled else None,
    })

    # ── 滑点：限价单用委托价，市价单用**同交易所**同分钟 K 线收盘 ──
    need_kline = [o for o in filled if o.get("price") in (None, 0) and o.get("filled_at")]
    closes = _minute_closes(
        [_base_symbol(o["symbol"]) for o in need_kline],
        since_ms, until_ms,
        exchanges=[str(o.get("exchange") or "") for o in need_kline],
    ) if need_kline else {}

    slips: List[float] = []
    slip_by_type: Dict[str, List[float]] = {}
    slip_by_symbol: Dict[str, List[float]] = {}
    no_ref = 0
    absurd = 0
    for o in filled:
        fp = o.get("filled_price")
        if not fp:
            continue
        fp = float(fp)
        ref = float(o["price"]) if o.get("price") else None
        if ref is None:
            ts = o.get("filled_at") or o.get("created_at")
            if ts is None:
                no_ref += 1
                continue
            key = (str(o.get("exchange") or "").lower(), _base_symbol(o["symbol"]))
            ref = (closes.get(key) or {}).get((int(ts.timestamp()) // 60) * 60)
        if not ref or ref <= 0:
            no_ref += 1
            continue
        # 有向滑点：买贵了 / 卖便宜了 → 正值 = 吃亏
        sign = 1.0 if str(o.get("side")).lower() in ("buy", "long") else -1.0
        bp = sign * (fp - ref) / ref * 1e4
        if abs(bp) > ABSURD_SLIP_BP:
            # 单笔滑点超过 500% 只可能是基准价取错（跨所同名资产、K 线断档），不是真实执行成本
            absurd += 1
            continue
        slips.append(bp)
        slip_by_type.setdefault(str(o.get("order_type") or "?"), []).append(bp)
        slip_by_symbol.setdefault(_base_symbol(o["symbol"]), []).append(bp)

    if slips:
        srt = sorted(slips)
        out["median_slippage_bp"] = round(statistics.median(srt), 3)
        out["avg_slippage_bp"] = round(statistics.fmean(srt), 3)
        out["p90_slippage_bp"] = round(srt[int(len(srt) * 0.9)], 3) if len(srt) >= 10 else None
        out["n_slippage_samples"] = len(srt)
        out["slippage_by_type"] = {
            k: {"n": len(v), "median_bp": round(statistics.median(v), 2)}
            for k, v in sorted(slip_by_type.items(), key=lambda kv: -len(kv[1]))[:8]
        }
        out["worst_symbols"] = sorted(
            ({"symbol": k, "n": len(v), "median_bp": round(statistics.median(v), 2)}
             for k, v in slip_by_symbol.items() if len(v) >= 3),
            key=lambda d: -d["median_bp"])[:5]
    else:
        notes.append("无可算滑点的成交（限价单缺委托价且市价单取不到同分钟 K 线）")
    if no_ref:
        notes.append(f"{no_ref} 笔成交缺基准价，未计入滑点")
    if absurd:
        notes.append(f"{absurd} 笔基准价异常（|滑点| > {ABSURD_SLIP_BP:.0f}bp）已剔除，多为 K 线断档")

    # ── 实际费率 ──
    fee_bps: List[float] = []
    for o in filled:
        fee, fp = o.get("fee"), o.get("filled_price")
        qty = o.get("filled_quantity") or o.get("quantity")
        if fee is None or not fp or not qty:
            continue
        notional = float(fp) * float(qty)
        if notional > 0:
            fee_bps.append(float(fee) / notional * 1e4)
    if fee_bps:
        out["avg_fee_bp"] = round(statistics.fmean(fee_bps), 3)
        out["n_fee_samples"] = len(fee_bps)

    notes.append("maker/taker 占比与下单延迟未落库（仅存在于仿真器内存），本轮不评估")
    return out


class ExecutionQAAgent(ObservationAgent):
    agent_id = AGENT_ID
    description = "执行质量体检：滑点 / 拒单率 / 部分成交 / 实际费率，分层定位问题"
    kinds = (KIND_EXEC_QUALITY,)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.lookback_h = env_float("AGENT_EXEC_QA_LOOKBACK_H", 168.0)
        self.slip_warn_bp = env_float("AGENT_EXEC_QA_SLIP_WARN_BP", 8.0)
        self.reject_warn = env_float("AGENT_EXEC_QA_REJECT_WARN", 0.05)
        self.window_h = env_float("AGENT_EXEC_QA_WINDOW_H", 24.0)
        self.min_orders = env_int("AGENT_EXEC_QA_MIN_ORDERS", 20)

    def _accounts(self) -> List[int]:
        try:
            from backend.services.ledger.edge_ledger import default_ledger_accounts

            return [int(a) for a in (default_ledger_accounts() or [])]
        except Exception:
            return []

    def analyze(self, errors: List[str]) -> Dict[str, Any]:
        until = now_ms()
        since = until - int(self.lookback_h * 3600 * 1000)
        accounts = self._accounts()
        # 没配账户就不限定 account_id，看全库（观察模式下只读，安全）
        targets: List[Optional[int]] = [int(a) for a in accounts] or [None]

        per_account: List[Dict[str, Any]] = []
        for acct in targets:
            try:
                st = execution_stats(account_id=acct, since_ms=since, until_ms=until)
                per_account.append(st)
            except Exception as exc:
                errors.append(f"execution_stats[{acct}]: {exc}")

        usable = [s for s in per_account if int(s.get("n_orders") or 0) >= self.min_orders]
        issues: List[Dict[str, Any]] = []
        for s in usable:
            # 用中位数而非均值：滑点分布尾部极厚（K 线断档、跨所同名资产），
            # 均值会在 2bp 和 42bp 之间乱跳，中位数稳定在 5bp 附近才是"典型一笔"的真实成本
            slip = s.get("median_slippage_bp")
            if slip is not None and slip > self.slip_warn_bp:
                issues.append({"account_id": s.get("account_id"), "type": "slippage",
                               "value": slip, "threshold": self.slip_warn_bp,
                               "worst": s.get("worst_symbols")})
            rr = s.get("reject_rate")
            if rr is not None and rr > self.reject_warn:
                issues.append({"account_id": s.get("account_id"), "type": "reject_rate",
                               "value": rr, "threshold": self.reject_warn,
                               "n_rejected": s.get("n_rejected")})

        return {
            "lookback_h": self.lookback_h, "accounts": accounts,
            "per_account": per_account, "n_usable": len(usable),
            "issues": issues,
            "thresholds": {"slippage_bp": self.slip_warn_bp, "reject_rate": self.reject_warn,
                           "min_orders": self.min_orders},
        }

    def predict(self, findings: Dict[str, Any]) -> List[Prediction]:
        out: List[Prediction] = []
        for s in findings.get("per_account") or []:
            slip = s.get("median_slippage_bp")
            if slip is None or int(s.get("n_orders") or 0) < self.min_orders:
                continue
            # 可证伪的说法：下一个窗口的滑点中位数仍在阈值的同一侧
            out.append(Prediction(
                kind=KIND_EXEC_QUALITY,
                subject=f"account:{s.get('account_id')}",
                prediction={"metric": "median_slippage_bp",
                            "side": "above" if slip > self.slip_warn_bp else "below",
                            "threshold_bp": self.slip_warn_bp, "observed_bp": slip,
                            "account_id": s.get("account_id")},
                horizon_ms=int(self.window_h * 3600 * 1000),
                confidence=min(0.7, 0.4 + abs(slip - self.slip_warn_bp) / max(self.slip_warn_bp, 1e-6) * 0.2),
            ))
        return out

    def advise(self, findings: Dict[str, Any]) -> List[Advice]:
        out: List[Advice] = []
        for iss in findings.get("issues") or []:
            # 未配置 ledger 账户时 account_id 为 None，scope 用 all（全库口径）
            acct_scope = f"exec:{iss['account_id']}" if iss.get("account_id") is not None else "exec:all"
            if iss["type"] == "slippage":
                worst = ", ".join(f"{w['symbol']}({w['avg_bp']}bp)" for w in (iss.get("worst") or [])[:3])
                out.append(Advice(
                    action=ACTION_PROPOSE_EXPERIMENT,
                    target=f"account:{iss['account_id']}",
                    severity=3,
                    reason=f"平均滑点 {iss['value']}bp 超过 {iss['threshold']}bp"
                           + (f"，集中在 {worst}" if worst else ""),
                    params={
                        "title": f"账户 {iss['account_id']} 滑点治理",
                        "hypothesis": f"当前平均滑点 {iss['value']}bp 高于 {iss['threshold']}bp 警戒线；"
                                      "若把高滑点币的下单方式改为限价追价，滑点应回落到警戒线以下。",
                        "change": {"apply": False, "kind": "execution",
                                   "proposal": "高滑点币改限价追价（maker-first）",
                                   "symbols": [w["symbol"] for w in (iss.get("worst") or [])[:5]]},
                        "expected_metrics": [
                            {"metric": "slippage_bp", "op": "<", "threshold": iss["threshold"],
                             "scope": acct_scope, "min_n": self.min_orders},
                        ],
                        "window_hours": 168,
                        "rollback_condition": "若滑点未改善或拒单率上升超过 2 个百分点，回退为市价单",
                    },
                ))
            elif iss["type"] == "reject_rate":
                out.append(Advice(
                    action=ACTION_PROPOSE_EXPERIMENT,
                    target=f"account:{iss['account_id']}",
                    severity=3,
                    reason=f"拒单率 {iss['value']:.1%} 超过 {iss['threshold']:.1%}（{iss.get('n_rejected')} 笔）",
                    params={
                        "title": f"账户 {iss['account_id']} 拒单率治理",
                        "hypothesis": f"拒单率 {iss['value']:.1%} 偏高，多为名义金额低于最小下单额或保证金不足；"
                                      "提高单笔最小名义并前置保证金检查后，拒单率应降到警戒线以下。",
                        "change": {"apply": False, "kind": "execution",
                                   "proposal": "提高单笔最小名义 + 下单前保证金预检"},
                        "expected_metrics": [
                            {"metric": "reject_rate", "op": "<", "threshold": iss["threshold"],
                             "scope": acct_scope, "min_n": self.min_orders},
                        ],
                        "window_hours": 168,
                        "rollback_condition": "若成交笔数下降超过 30%，回退最小名义设置",
                    },
                ))
        return out


def score_exec_quality(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """`exec_quality` 到期评分：预测窗口内的真实滑点是否仍在预测的那一侧。"""
    pred = row.get("prediction") or {}
    if isinstance(pred, str):
        import json

        try:
            pred = json.loads(pred)
        except Exception:
            return None
    thr = pred.get("threshold_bp")
    side = pred.get("side")
    acct = pred.get("account_id")
    if thr is None or side not in ("above", "below"):
        return None
    created = int(row.get("created_ms") or 0)
    expires = int(row.get("expires_ms") or 0)
    if not created or not expires:
        return None
    try:
        st = execution_stats(account_id=int(acct) if acct is not None else None,
                            since_ms=created, until_ms=expires)
    except Exception as exc:
        logger.warning("[%s] 评分取数失败: %s", AGENT_ID, exc)
        return None
    actual = st.get("median_slippage_bp")
    n = int(st.get("n_slippage_samples") or 0)
    if actual is None or n < 10:
        return None      # 样本不足 → 保持 open，不造分
    actual_side = "above" if float(actual) > float(thr) else "below"
    return {"score": 1.0 if actual_side == side else 0.0,
            "outcome": {"actual_bp": actual, "actual_side": actual_side,
                        "predicted_side": side, "threshold_bp": thr, "n": n}}


def build() -> ExecutionQAAgent:
    return ExecutionQAAgent()
