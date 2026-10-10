# -*- coding: utf-8 -*-
"""[2026-10-10 主脑接入高频学习] 大模型是主脑的**一张嘴**：每条车道一轮，只提一个旋钮。

用户设计原话："模型必须交出三样：一个旋钮、一句原因、用到的知识条目编号。没有编号，
这次作废。每次只动一条车道，旋钮只在该车道白名单里。"

## 五道硬闸（任何一道不过 ⇒ 只写审计日志，参数一个字节都不改）

1. **知识编号**：`cited[].card_id` 必须**真实存在于**本轮检索到的卡片集合里。
   模型说"我把吃单进场打开，因为成本更低"——没有编号，作废。
2. **白名单**：`param` 必须在该车道白名单内，且 `new` 落在边界内，且**一次只改一个**。
   高频只许 `pp_rest_sec / pp_thin_frac / pp_exit_ticks / pp_bucket_min_n`。
3. **现值一致**：`old` 必须等于**此刻**的现值（防止基于过期快照改参数，也顺带
   发现"别处已经改过"的竞态）。
4. **该车道的统计关**：高频要求最近来回 ≥ 20 笔、平均仍为正、胜率不低于打平线、
   赚的幅度大于亏的幅度（与 `flow_universe.pp_membership`/`pp_situation` 同源判据）。
   这一关由 `self_tuner.apply_proposal` 之后 `self_tuner_verdict` 在改后继续把关
   （改后 < 改前 × 0.8 就写回）。
5. **降级即放弃**：知识库 degraded / 模型不可用 / 返回不是合法 JSON ⇒ 本轮放弃。

## 落地路径（复用现有链路，不新写一条）
`proposal → self_tuner.apply_proposal` ⇒ 写 `data/flow_learn_params.json`
（`flow_rules.load_learn_params` 会按边界钳制；`pingpong.py` 每拍读它 ⇒ 下一拍生效）
+ 写 `logs/self_tuner_pending.json`（带 `verdict_at`，由 `DSH_MM_SELF_TUNE_VERDICT`
每 15 分钟比对改前/改后，变差就回滚）。

## 开关
`MM_BRAIN_APPLY=0` ⇒ 只出提案（写 journal，不改参数）。默认 1（与既有
`DSH_MM_SELF_TUNE ... --apply` 的线上行为一致）。`MM_BRAIN_ENABLED=0` ⇒ 整体停摆。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

LANES = ("hft", "mid", "long")

# ── 各车道白名单：(lo, hi) ────────────────────────────────────────────
# 高频：与 `flow_rules.FLOW_SAFE_PARAMS` 的 ping-pong 四项**同边界**（单一来源在那边，
# 这里只是本模块能提议的子集；两面都改不动对方的键）。
HFT_WHITELIST: Dict[str, Tuple[float, float]] = {
    "pp_rest_sec": (0.0, 60.0),
    "pp_thin_frac": (0.1, 0.9),
    "pp_exit_ticks": (1.0, 3.0),
    "pp_bucket_min_n": (10.0, 100.0),
}
# 中/长线：只允许动「**热生效**且**有机器边界**」的旋钮。
#
# 为什么只收 runtime_tuning 这一层（侦察结论，见 `.verify_tmp` 的一次性报告）：
# 中/长线的其它旋钮（`MIDLONG_*` / `TREND_*` / `EXIT_POLICY_*`）几乎全是
# `os.getenv(...)` 在 **import 期解析一次** ⇒ 改 `.env` 要重启进程才生效。
# 而本模块的落地后把关（`self_tuner_verdict`）是**改后 30 分钟就比指标**：
# 一个要重启才生效的参数在那个窗口里"什么都没变"，会被判成"变差"⇒ 误回滚。
# 所以这些键**不进白名单**，它们的变更应当走人工/重启路径，而不是自动闭环。
#
# runtime_tuning 的写入口是 `runtime_tuning_store.apply_patches`（带 schema 钳制），
# 读侧有 60s 缓存热刷新 ⇒ 唯一满足"改完下一拍就生效"的中/长线通道。
MIDLONG_WHITELIST: Dict[str, Dict[str, Tuple[float, float]]] = {
    "mid": {
        "mid_daily_cap": (0.0, 60.0),
        "master_reduce_min_loss_pct": (0.05, 0.20),
        # 该键在 runtime_tuning.json 里是 tier→比例的字典且**没有 schema 边界**
        # （`apply_patches` 对 `master_close_min_loss_pct_by_tier` 不钳制）
        # ⇒ 边界必须由本白名单给，否则模型能把它设到 0.9 变成"不砍仓"。
        "master_close_min_loss_pct_by_tier.mid": (0.02, 0.10),
        "by_nature.swing.min_confidence": (45.0, 80.0),
        "by_nature.swing.min_risk_reward": (1.2, 3.0),
        "by_nature.swing.min_alignment": (3.0, 10.0),
    },
    "long": {
        "trend_daily_cap": (0.0, 60.0),
        "master_reduce_min_loss_pct": (0.05, 0.20),
        "master_close_min_loss_pct_by_tier.long": (0.04, 0.15),
        "by_nature.trend_follow.min_score": (40.0, 85.0),
        "by_nature.trend_follow.min_risk_reward": (1.2, 3.0),
        "by_nature.trend_follow.min_alignment": (3.0, 10.0),
    },
}

# 任何车道都禁止的键（策略身份/危险开关）。命中 ⇒ 直接作废 + 告警。
FORBIDDEN_KEYS = {
    "taker_entry", "allow_taker_entry", "taker_entry_enabled", "spread_min_bp",
    "min_spread_bp", "entry_spread_min_bp", "compound_ratio", "leverage",
    "exchange_leverage", "active_flow_mode", "mm_auto_evolve", "mode",
    "pp_universe", "mm_pingpong", "min_notional_60s", "notional_loss_frac",
    "disaster_stop_floor_bp", "disaster_stop_cap_bp",
}

ADVISOR_JOURNAL = "data/lane_advisor_journal.jsonl"
FLOW_ROUNDTRIP_LOG = "data/flow_roundtrip_log.jsonl"
FLOW_PARAMS_FILE = "data/flow_learn_params.json"

DEFAULT_MIN_N = 20          # 高频统计关与提案触发的最少样本
DEFAULT_MIN_GAP_S = 3600.0  # 距上次提案的最长间隔（到点就算样本不够也重新看一次）
DEFAULT_TOPK = 8

SYSTEM_PROMPT = (
    "你是这个量化项目「同一条车道」的参数顾问。三条车道（hft/mid/long）共用一本知识库，"
    "但每条车道的规矩不同，绝不许把一种车道的规矩套到另一种上。\n"
    "铁律：\n"
    "1. 每轮只提**一个**白名单参数，给出 old/new/rollback；一次改两个即作废；\n"
    "2. 必须给出 `cited`：你依据的知识条目编号（来自给你的卡片列表），"
    "**没有编号这次作废**。卡片是参考、不是下单指令；与项目自己的账冲突时以账为准；\n"
    "3. 证据不足、或没有明确改进方向 ⇒ 输出 hold，宁可不改；\n"
    "4. 禁止建议：吃单进场、加杠杆、加宽挂单、改 compound_ratio、改策略身份、"
    "把高频的挂单规矩套到中线上；\n"
    "5. 高频的判据是 rt_bp（开仓价→平仓价）的均值/胜率/赚亏幅度，**不是每小时成交笔数**；"
    "笔数变多但平均变差视为失败；\n"
    "6. 只输出 JSON，不要输出任何其它文字。\n"
    '格式：{"lane":"hft","action":"adjust","param":"pp_rest_sec","old":15,"new":20,'
    '"rollback":15,"reason":"...","cited":[{"card_id":"kb-...","use":"..."}]}'
    ' 或 {"lane":"hft","action":"hold","reason":"..."}'
)


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(raw)) if raw not in (None, "") else int(default)
    except (TypeError, ValueError):
        return int(default)


def enabled() -> bool:
    raw = os.getenv("MM_BRAIN_ENABLED")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def apply_enabled() -> bool:
    """默认 True（与线上 `DSH_MM_SELF_TUNE --apply` 一致）；置 0 ⇒ 只出提案。"""
    raw = os.getenv("MM_BRAIN_APPLY")
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


# ══════════════════════════════════════════════════════════════════════
#  证据
# ══════════════════════════════════════════════════════════════════════

def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _roundtrip_rows(root: Path) -> List[Dict[str, Any]]:
    """读 PP 往返账（`strategy=PP` 且 `y_bp` 非空）。失败 ⇒ 空列表。"""
    rows: List[Dict[str, Any]] = []
    try:
        for line in (root / FLOW_ROUNDTRIP_LOG).read_text(
                encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if str(r.get("strategy") or "") != "PP" or r.get("y_bp") is None:
                continue
            rows.append(r)
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain] 往返账读取失败: %s", e)
    return rows


def window_metrics(rows: Sequence[Dict[str, Any]], t0: float,
                   t1: float) -> Dict[str, Any]:
    """窗口内的往返统计（与 `flow_rules.window_stats` 同口径的判定字段）。"""
    ys: List[float] = []
    for r in rows:
        try:
            ts = float(r.get("ts") or 0.0)
            if not (float(t0) <= ts < float(t1)):
                continue
            ys.append(float(r["y_bp"]))
        except (TypeError, ValueError, KeyError):
            continue
    n = len(ys)
    if n == 0:
        return {"n": 0, "mean_y": 0.0, "win_rate": 0.0, "avg_win_bp": 0.0,
                "avg_loss_bp": 0.0}
    wins = [y for y in ys if y > 0]
    losses = [y for y in ys if y < 0]
    return {
        "n": n,
        "mean_y": sum(ys) / n,
        "win_rate": len(wins) / n,
        "avg_win_bp": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss_bp": (sum(losses) / len(losses)) if losses else 0.0,
    }


def hft_stats_gate(m: Dict[str, Any], *, min_n: int = DEFAULT_MIN_N) -> Tuple[bool, str]:
    """高频统计关（纯函数，可单测）。与 `pp_situation.bucket_negative` 同源判据：
    样本够、平均为正、胜率不低于打平线、赚的幅度大于亏的幅度。"""
    n = int(m.get("n") or 0)
    if n < int(min_n):
        return False, f"样本不足({n}<{min_n})"
    mean_y = float(m.get("mean_y") or 0.0)
    if mean_y <= 0:
        return False, f"平均非正({mean_y:+.3f}bp)"
    try:
        from backend.services.market_maker.pp_situation import breakeven_win_rate
        be = breakeven_win_rate(m.get("avg_win_bp") or 0.0, m.get("avg_loss_bp") or 0.0)
    except Exception:  # noqa: BLE001
        be = 1.0
    wr = float(m.get("win_rate") or 0.0)
    if wr + 1e-9 < be:
        return False, f"胜率{wr:.1%}低于打平线{be:.1%}"
    if float(m.get("avg_win_bp") or 0.0) <= abs(float(m.get("avg_loss_bp") or 0.0)):
        return False, (f"赚的({float(m.get('avg_win_bp') or 0):+.2f}bp)不比亏的"
                       f"({float(m.get('avg_loss_bp') or 0):+.2f}bp)大")
    return True, "通过"


def gather_evidence(lane: str, *, root: Optional[Path] = None,
                    now: Optional[float] = None) -> Dict[str, Any]:
    """该车道的证据摘要（只能读账与现值，绝不写）。"""
    r = root or _root()
    now_ts = float(now if now is not None else time.time())
    lane_key = str(lane or "").lower()
    ev: Dict[str, Any] = {"lane": lane_key, "now": now_ts}

    if lane_key == "hft":
        try:
            from backend.services.market_maker.flow_rules import load_learn_params
            cur = dict(load_learn_params(r))
        except Exception as e:  # noqa: BLE001
            ev["error"] = f"current_params:{e}"
            cur = {}
        ev["current_params"] = {k: cur.get(k) for k in HFT_WHITELIST}
        rows = _roundtrip_rows(r)
        ev["roundtrips_total"] = len(rows)
        # 三个窗口：近 30 分钟 / 近 3 小时 / 全时代（判据以最近窗口为准）
        for label, secs in (("recent_30m", 1800.0), ("recent_3h", 10800.0)):
            ev[label] = window_metrics(rows, now_ts - secs, now_ts)
        ev["lifetime"] = window_metrics(rows, 0.0, now_ts + 1.0)
        stats = _load_json(r / "data" / "pp_symbol_stats.json")
        if isinstance(stats, dict) and isinstance(stats.get("coins"), dict):
            coins = sorted(
                ((str(k).upper(), v) for k, v in stats["coins"].items()
                 if isinstance(v, dict)),
                key=lambda kv: -float(kv[1].get("n") or 0.0))[:8]
            ev["per_symbol"] = [{"symbol": k, **{kk: v.get(kk) for kk in
                                                 ("n", "win_rate", "avg_win_bp",
                                                  "avg_loss_bp", "avg_bp")}}
                                for k, v in coins]
        sit = _load_json(r / "data" / "pp_situation_last.json")
        if isinstance(sit, dict) and isinstance(sit.get("coins"), dict):
            resting = 0
            for sides in sit["coins"].values():
                if not isinstance(sides, dict):
                    continue
                for buckets in sides.values():
                    if isinstance(buckets, dict):
                        resting += len(buckets)
            ev["situation_buckets"] = resting
        return ev

    if lane_key in MIDLONG_WHITELIST:
        # 中/长线：值来自 runtime_tuning_store（唯一热生效通道）。
        # 证据取该车道**自己平仓账**的聚合：`strategy_memories.performance_by_regime`
        # 的 `nature_<nature>` 桶（由 `unified_learning._update_strategy_memory` 维护，
        # 与"中线下单"用的是同一批平仓记录）。
        cur: Dict[str, Any] = {}
        try:
            from backend.services import runtime_tuning_store as rts
            all_tuning = rts.get_all_tuning() or {}
            for key in allowed_keys(lane_key):
                if "." in key:
                    head, _, tail = key.partition(".")
                    node = all_tuning.get(head) or {}
                    if head == "by_nature":
                        nat, _, field = tail.partition(".")
                        node = (node.get(nat) or {})
                        cur[key] = node.get(field)
                    else:
                        cur[key] = node.get(tail)
                else:
                    node = all_tuning.get(key)
                    cur[key] = node.get("value") if isinstance(node, dict) else node
        except Exception as e:  # noqa: BLE001
            ev["error"] = f"runtime_tuning:{e}"
        ev["current_params"] = cur
        ev["nature_stats"] = _nature_stats(lane_key)
        ev["hot_apply"] = "runtime_tuning.apply_patches（60s 内生效，无需重启）"
        ev["whitelist_reason"] = (
            "只收热生效且有边界的键：其它 MIDLONG_*/TREND_*/EXIT_POLICY_* 是 import 期读 env，"
            "改完要重启，而改后 30 分钟就要判指标 ⇒ 会被误判成变差")
        return ev

    ev["error"] = f"unknown_lane:{lane_key}"
    return ev


def _nature_stats(lane: str) -> Dict[str, Any]:
    """该车道 nature 桶的平仓统计（来自 StrategyMemory.performance_by_regime）。

    只读；任何失败都返回空 dict（缺证据 ⇒ 调用方只能 hold）。
    """
    nature = "swing" if str(lane).lower() == "mid" else "trend_follow"
    out: Dict[str, Any] = {}
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import StrategyMemory
        db = SessionLocal()
        try:
            rows = db.query(StrategyMemory).all()
            agg: Dict[str, Any] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
            for mem in rows:
                perf = mem.performance_by_regime or {}
                blk = perf.get(f"nature_{nature}")
                if not isinstance(blk, dict):
                    continue
                agg["trades"] += int(blk.get("trades") or 0)
                agg["wins"] += int(blk.get("wins") or 0)
                agg["total_pnl"] += float(blk.get("total_pnl") or 0.0)
            if agg["trades"]:
                agg["win_rate"] = round(agg["wins"] / agg["trades"], 4)
                agg["total_pnl"] = round(agg["total_pnl"], 4)
                out[nature] = agg
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("[brain] nature 统计读取失败: %s", e)
    return out


# ══════════════════════════════════════════════════════════════════════
#  提案解析与护栏（纯函数）
# ══════════════════════════════════════════════════════════════════════

def allowed_keys(lane: str) -> Dict[str, Tuple[float, float]]:
    lane_key = str(lane or "").lower()
    if lane_key == "hft":
        return dict(HFT_WHITELIST)
    return dict(MIDLONG_WHITELIST.get(lane_key) or {})


def parse_llm_json(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """解析模型的 JSON。

    兼容两条真实返回形态（实测都遇到过）：
      ① 直接是提案 JSON（`{"lane":...}`）；
      ② OpenAI 兼容的**完整响应体**（`{"choices":[{"message":{"content":"{...}"}}]}`）
         —— `call_llm_api_sync` 在某些通道下返回的就是这一层。
    第 ② 种若不拆开，会把"模型说了 hold"误判成"返回了非法 JSON"。
    """
    if not text:
        return None
    t = str(text).strip()
    if "```" in t:
        parts = t.split("```")
        if len(parts) >= 2:
            t = parts[1]
        if t.startswith("json"):
            t = t[4:]
            t = t.strip()
    try:
        p = json.loads(t)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(p, dict):
        return None
    if "choices" in p and isinstance(p.get("choices"), list) and p["choices"]:
        inner = (p["choices"][0] or {}).get("message") or {}
        content = inner.get("content")
        if isinstance(content, str) and content.strip():
            return parse_llm_json(content)
    return p


def validate_proposal(p: Optional[Dict[str, Any]], lane: str,
                      current: Dict[str, Any],
                      card_ids: Sequence[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """把模型输出变成可落地的提案；任何不合规 ⇒ `(None, 原因)`。

    这里是本模块的**核心护栏**：白名单、单旋钮、边界、现值一致、知识编号真实存在。
    """
    lane_key = str(lane or "").lower()
    if not isinstance(p, dict):
        return None, "not_json"
    if str(p.get("lane") or lane_key).lower() != lane_key:
        return None, "lane_mismatch"
    if str(p.get("action") or "").lower() == "hold":
        return None, f"model_hold:{str(p.get('reason') or '')[:160]}"

    param = str(p.get("param") or "").strip()
    if param in FORBIDDEN_KEYS:
        return None, f"forbidden_key:{param}"
    wl = allowed_keys(lane_key)
    if not wl:
        return None, "lane_whitelist_empty"
    if param not in wl:
        return None, f"not_in_whitelist:{param}"

    # 知识编号：必须真实存在于本轮检索到的卡片编号集合里
    cited = p.get("cited")
    ids: List[str] = []
    if isinstance(cited, list):
        for c in cited:
            if isinstance(c, dict) and str(c.get("card_id") or "").strip():
                ids.append(str(c["card_id"]).strip())
            elif isinstance(c, str) and c.strip():
                ids.append(c.strip())
    if not ids:
        return None, "no_cited_card"
    known = {str(x) for x in (card_ids or [])}
    unknown = [c for c in ids if c not in known]
    if unknown:
        return None, f"cited_not_retrieved:{','.join(unknown[:3])}"

    try:
        new = float(p["new"])
        old = float(p["old"])
    except (KeyError, TypeError, ValueError):
        return None, "bad_numbers"
    lo, hi = wl[param]
    if not (float(lo) <= new <= float(hi)):
        return None, f"out_of_bounds:{param}={new}({lo}~{hi})"
    cur_val = current.get(param)
    if cur_val is None:
        return None, f"no_current_value:{param}"
    if abs(float(cur_val) - old) > 1e-6:
        return None, f"stale_old:{param} old={old} current={cur_val}"
    if abs(new - old) < 1e-9:
        return None, "no_change"

    return {
        "action": "adjust", "param": param, "old": old, "new": new,
        "rollback": old,
        "reason": str(p.get("reason") or "")[:300],
        "cited": ids,
        "verdict_metric": "roundtrip_y",
        "verdict_rule": "改后 30 笔内 mean_y < 改前 × 0.8 ⇒ 回滚（self_tuner_verdict）",
    }, "ok"


def bucket_min_n_counterfactual(proposed_new: float,
                                runner: Optional[Callable[..., Dict[str, Any]]] = None
                                ) -> Tuple[bool, str]:
    """`pp_bucket_min_n` 的**事前**反事实关（其余三项旋钮做不到，只能事后回滚）。

    为什么只有它：每个 PP 往返都记了入场语境（价差档 × 前档量档），所以"如果当时
    门开着（min_n=X），哪些来回不会发生"可以从历史往返里**重放**出来——
    `evolution.pp_evolution_round` 实现的正是这件事。而 `pp_rest_sec`/`pp_thin_frac`/
    `pp_exit_ticks` 的反事实在账里根本不存在（账里没有"如果当时歇 20 秒会不会成交"），
    所以那三项走的是"改完 30 笔内变差就退回"（`self_tuner_verdict` 已在做）。

    判定：反事实最优值必须等于提议值；样本不足（`action="none"`）⇒ 放行并如实标注。
    """
    try:
        if runner is None:
            from backend.services.market_maker.evolution import pp_evolution_round
            runner = pp_evolution_round
        rnd = runner(apply=False) or {}
    except Exception as e:  # noqa: BLE001
        return False, f"counterfactual_failed:{e}"
    if str(rnd.get("action") or "") == "none":
        return True, f"样本不足，按事后回滚把关({str(rnd.get('reason') or '')[:120]})"
    best = rnd.get("best")
    if not isinstance(best, dict) or best.get("min_n") is None:
        return False, f"反事实无更优候选（在位 {rnd.get('incumbent_min_n')}），本轮不改"
    best_v = float(best["min_n"])
    if abs(best_v - float(proposed_new)) > 1e-9:
        return False, (f"反事实最优 {best_v} ≠ 提议 {float(proposed_new)} ⇒ 只许按反事实走")
    return True, f"反事实走查通过（min_n={best_v}）"


def apply_lane_proposal(lane: str, prop: Dict[str, Any]) -> bool:
    """按车道选择落地通道（复用既有链路，不新写一套）。

    · `hft`     → `self_tuner.apply_proposal`：写 `data/flow_learn_params.json`
                  （下一拍生效）+ 登记 `logs/self_tuner_pending.json` 供
                  `self_tuner_verdict` 改后把关。
    · `mid/long` → `runtime_tuning_store.apply_patches`：唯一**热生效**的中/长线通道
                  （60s 内生效；其它 `MIDLONG_*`/`TREND_*` 键是 import 期读 env，
                  要重启，会被 30 分钟后的改后判定误判成"变差"）。

    `apply_patches` 自带 schema 钳制，所以返回后**必须回读校验**：写进去的值不等于
    提案值（被钳过/被拒）⇒ 视为失败，不改就是最好的结果。
    """
    lane_key = str(lane or "").lower()
    if lane_key == "hft":
        from backend.services.market_maker.self_tuner import apply_proposal
        return bool(apply_proposal(prop))

    from backend.services import runtime_tuning_store as rts

    key = str(prop["param"])
    new = float(prop["new"])
    patch: Dict[str, Any] = {}
    if "." in key:
        head, _, tail = key.partition(".")
        node = rts.get_all_tuning().get(head) or {}
        if head == "by_nature":
            nat, _, field = tail.partition(".")
            if nat not in (node if isinstance(node, dict) else {}):
                logger.warning("[brain] 未知 nature: %s", key)
                return False
            patch[head] = {nat: {field: new}}
        elif head == "master_close_min_loss_pct_by_tier":
            tier = tail.split(".")[0]
            cur = node.get(tier) if isinstance(node, dict) else None
            if cur is None:
                logger.warning("[brain] 未知 tier 键: %s", key)
                return False
            patch[head] = {tier: new}
        else:
            logger.warning("[brain] 不支持的嵌套键: %s", key)
            return False
    else:
        patch[key] = new

    try:
        applied = rts.apply_patches(patch) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain] runtime_tuning 落地失败: %s", e)
        return False
    # 回读校验：写进去的必须就是提案值（被钳制/被拒 ⇒ 视为失败）
    back = gather_evidence(lane_key).get("current_params") or {}
    got = back.get(key)
    try:
        if got is None or abs(float(got) - new) > 1e-9:
            logger.warning("[brain] 落地值被钳制或未生效: %s 提案=%s 实际=%s（patch=%s）",
                           key, new, got, applied)
            return False
    except (TypeError, ValueError):
        return False
    return True


# ══════════════════════════════════════════════════════════════════════
#  LLM 通道（复用 self_tuner 的既有 DeepSeek 直连约定）
# ══════════════════════════════════════════════════════════════════════

def call_llm(messages: List[Dict[str, str]],
             caller: str = "lane_brain_advisor") -> Optional[str]:
    import os as _os
    try:
        from backend.services.llm_config_service import LLMConfig, call_llm_api_sync
    except Exception:  # noqa: BLE001
        return None
    key = _os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    cfg = LLMConfig(
        id=0, name="lane-brain-advisor", provider="deepseek",
        model=_os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash",
        base_url=(_os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
                  or "https://api.deepseek.com"),
        api_key=key,
    )
    try:
        resp = call_llm_api_sync(config=cfg, messages=messages, temperature=0.2,
                                 max_tokens=700,
                                 response_format={"type": "json_object"},
                                 timeout=120.0, caller=caller)
        if isinstance(resp, dict):
            return resp.get("content") or resp.get("text") or json.dumps(resp)
        return str(resp or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain] LLM 调用失败: %s", e)
        return None


def build_messages(evidence: Dict[str, Any], kb: Dict[str, Any],
                   lane: str) -> List[Dict[str, str]]:
    cards = [
        {"card_id": c.get("card_id"), "text": (c.get("text") or "")[:400]}
        for c in (kb.get("cards") or [])
    ]
    lessons = [
        {"text": (l.get("text") or "")[:300], "strategy_id": l.get("strategy_id") or ""}
        for l in (kb.get("lessons") or [])
    ]
    user = (
        f"车道: {lane}\n"
        f"白名单(只许改这些, 边界): {json.dumps(allowed_keys(lane), ensure_ascii=False)}\n\n"
        f"当前参数:\n{json.dumps(evidence.get('current_params') or {}, ensure_ascii=False)}\n\n"
        f"证据摘要:\n{json.dumps({k: v for k, v in evidence.items() if k not in ('current_params',)}, ensure_ascii=False, indent=2)}\n\n"
        f"知识卡片(必须引用其中的 card_id):\n{json.dumps(cards, ensure_ascii=False, indent=2)}\n\n"
        f"本项目自己的教训(事实, 与卡片冲突以它为准):\n{json.dumps(lessons, ensure_ascii=False, indent=2)}\n\n"
        f"口径声明: {kb.get('conflict_rule') or ''}\n"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


# ══════════════════════════════════════════════════════════════════════
#  审计日志
# ══════════════════════════════════════════════════════════════════════

def journal(entry: Dict[str, Any], root: Optional[Path] = None) -> None:
    try:
        p = (root or _root()) / ADVISOR_JOURNAL
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain] 审计日志写入失败: %s", e)


def last_journal_ts(lane: str, root: Optional[Path] = None) -> float:
    try:
        p = (root or _root()) / ADVISOR_JOURNAL
        last = 0.0
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if str(e.get("lane") or "") == str(lane):
                last = max(last, float(e.get("ts") or 0.0))
        return last
    except Exception:  # noqa: BLE001
        return 0.0


# ══════════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════════

def run_lane(lane: str, *, root: Optional[Path] = None,
             kb_service: Optional[Any] = None,
             llm: Optional[Callable[[List[Dict[str, str]]], Optional[str]]] = None,
             apply_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
             cf_runner: Optional[Callable[..., Dict[str, Any]]] = None,
             now: Optional[float] = None,
             query: str = "") -> Dict[str, Any]:
    """跑一条车道的一轮：证据 → 知识 → 一个旋钮 → 五道闸 → （可选）落地。

    绝不抛异常（返回体里 `ok=False` + `why`）。
    """
    r = root or _root()
    lane_key = str(lane or "").lower()
    now_ts = float(now if now is not None else time.time())
    out: Dict[str, Any] = {"ok": True, "lane": lane_key, "decision": "",
                           "why": "", "ts": now_ts}
    if not enabled():
        out.update(decision="disabled", why="MM_BRAIN_ENABLED=0")
        return out

    ev = gather_evidence(lane_key, root=r, now=now_ts)
    out["evidence_digest"] = {
        k: ev.get(k) for k in ("roundtrips_total", "recent_30m", "recent_3h",
                               "lifetime", "current_params", "note",
                               "nature_stats", "error") if k in ev
    }

    # 触发条件：该车道样本够，或距上轮已超时（先到先算）
    last = last_journal_ts(lane_key, root=r)
    min_gap = _env_float("MM_BRAIN_MAX_GAP_S", DEFAULT_MIN_GAP_S)
    due_by_gap = (now_ts - last) >= min_gap if last > 0 else True
    min_n = _env_int("MM_BRAIN_MIN_N", DEFAULT_MIN_N)
    if lane_key == "hft":
        n_recent = int((ev.get("recent_3h") or {}).get("n") or 0)
    else:
        _ns = ev.get("nature_stats") or {}
        n_recent = int(sum(int((v or {}).get("trades") or 0)
                           for v in _ns.values()))
    if not (n_recent >= min_n or due_by_gap):
        out.update(decision="not_due",
                   why=f"该车道近期样本 {n_recent} 笔 < {min_n}，且距上轮不足 {min_gap}s")
        return out

    wl = allowed_keys(lane_key)
    if not wl:
        out.update(decision="no_whitelist",
                   why="该车道白名单为空（只能 hold；先把现有风控数值登记进来）")
        journal({"ts": now_ts, "lane": lane_key, "decision": out["decision"],
                 "why": out["why"], "evidence_digest": out["evidence_digest"]}, root=r)
        return out

    # 知识库
    try:
        from backend.services.knowledge_lane_retriever import retrieve_for_lane
        q = query or (f"{lane_key} 当前参数 {json.dumps(ev.get('current_params') or {}, ensure_ascii=False)} "
                      "盘口 库存 出场")
        kb = retrieve_for_lane(q, lane_key, top_k=_env_int("MM_BRAIN_KB_TOPK", DEFAULT_TOPK),
                               service=kb_service)
    except Exception as e:  # noqa: BLE001
        kb = {"degraded": True, "reason": f"exception:{e}", "cards": [], "card_ids": []}
    out["kb"] = {"degraded": kb.get("degraded"), "reason": kb.get("reason"),
                 "card_ids": kb.get("card_ids"), "cards": len(kb.get("cards") or []),
                 "lessons": len(kb.get("lessons") or [])}
    if kb.get("degraded") or not kb.get("card_ids"):
        out.update(decision="kb_unavailable",
                   why=f"知识库不可用或没有可用卡片({kb.get('reason')}) ⇒ 本轮不改参数")
        journal({"ts": now_ts, "lane": lane_key, "decision": out["decision"],
                 "why": out["why"], "kb": out["kb"],
                 "evidence_digest": out["evidence_digest"]}, root=r)
        return out

    # 模型
    messages = build_messages(ev, kb, lane_key)
    raw = (llm or call_llm)(messages)
    prop = parse_llm_json(raw)
    out["raw_llm"] = (str(raw) if raw else "")[:600]
    if prop is None:
        out.update(decision="llm_invalid",
                   why="模型不可用或未返回合法 JSON ⇒ 本轮不改参数")
        journal({"ts": now_ts, "lane": lane_key, "decision": out["decision"],
                 "why": out["why"], "raw_llm": out["raw_llm"], "kb": out["kb"],
                 "evidence_digest": out["evidence_digest"]}, root=r)
        return out

    # 护栏（含知识编号校验）
    current = dict(ev.get("current_params") or {})
    ok_prop, why = validate_proposal(prop, lane_key, current, kb.get("card_ids") or [])
    if ok_prop is None and not why.startswith("model_hold"):
        out.update(decision="rejected", why=why)
        journal({"ts": now_ts, "lane": lane_key, "decision": "rejected", "why": why,
                 "raw_llm": out["raw_llm"], "kb": out["kb"],
                 "evidence_digest": out["evidence_digest"]}, root=r)
        return out
    if ok_prop is None:  # 模型自己说 hold
        out.update(decision="hold", why=why)
        journal({"ts": now_ts, "lane": lane_key, "decision": "hold", "why": why,
                 "raw_llm": out["raw_llm"], "kb": out["kb"],
                 "evidence_digest": out["evidence_digest"]}, root=r)
        return out

    # 该车道的统计关
    if lane_key == "hft":
        m = dict(ev.get("recent_3h") or {})
        passed, gwhy = hft_stats_gate(m, min_n=_env_int("MM_BRAIN_MIN_N", DEFAULT_MIN_N))
        out["gate"] = {"name": "hft_stats", "passed": passed, "why": gwhy,
                       "metrics": m}
        if not passed:
            out.update(decision="blocked_by_gate", why=gwhy,
                       param=ok_prop["param"], new=ok_prop["new"])
            journal({"ts": now_ts, "lane": lane_key, "decision": out["decision"],
                     "why": gwhy, "proposal": ok_prop, "kb": out["kb"],
                     "gate": out["gate"], "evidence_digest": out["evidence_digest"]},
                    root=r)
            return out
        # 桶门阈值另走**事前反事实**关（唯一可从历史往返重放的一条旋钮）
        if ok_prop["param"] == "pp_bucket_min_n":
            cf_ok, cf_why = bucket_min_n_counterfactual(
                ok_prop["new"], runner=(cf_runner or None))
            out["gate"] = {"name": "pp_bucket_counterfactual", "passed": cf_ok,
                           "why": cf_why}
            if not cf_ok:
                out.update(decision="blocked_by_counterfactual", why=cf_why,
                           param=ok_prop["param"], new=ok_prop["new"])
                journal({"ts": now_ts, "lane": lane_key, "decision": out["decision"],
                         "why": cf_why, "proposal": ok_prop, "kb": out["kb"],
                         "gate": out["gate"],
                         "evidence_digest": out["evidence_digest"]}, root=r)
                return out

    # 落地（复用 self_tuner.apply_proposal：写 flow_learn_params + pending 供 verdict 把关）
    out.update(param=ok_prop["param"], old=ok_prop["old"], new=ok_prop["new"],
               cited=ok_prop["cited"], reason=ok_prop["reason"])
    if not apply_enabled():
        out.update(decision="proposal_only", why="MM_BRAIN_APPLY=0（只出提案）")
        journal({"ts": now_ts, "lane": lane_key, "decision": out["decision"],
                 "why": out["why"], "proposal": ok_prop, "kb": out["kb"],
                 "gate": out.get("gate"),
                 "evidence_digest": out["evidence_digest"]}, root=r)
        return out

    applied = False
    try:
        if apply_fn is not None:
            applied = bool(apply_fn(ok_prop))
        else:
            applied = bool(apply_lane_proposal(lane_key, ok_prop))
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain] 提案落地失败: %s", e)
        applied = False
    out.update(decision=("applied" if applied else "apply_failed"),
               why=("" if applied else "落地被拒/被钳制/写盘失败（现值不一致或超出机器边界）"))
    journal({"ts": now_ts, "lane": lane_key, "decision": out["decision"],
             "why": out["why"], "proposal": ok_prop, "kb": out["kb"],
             "gate": out.get("gate"), "evidence_digest": out["evidence_digest"]},
            root=r)
    return out


def run_all(*, root: Optional[Path] = None, kb_service: Optional[Any] = None,
            llm: Optional[Callable[[List[Dict[str, str]]], Optional[str]]] = None,
            apply_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
            cf_runner: Optional[Callable[..., Dict[str, Any]]] = None,
            lanes: Sequence[str] = LANES,
            now: Optional[float] = None) -> Dict[str, Any]:
    """三条车道各跑一轮（一条车道一轮只动一个旋钮；互不影响）。"""
    res = {}
    for lane in lanes:
        try:
            res[lane] = run_lane(lane, root=root, kb_service=kb_service, llm=llm,
                                 apply_fn=apply_fn, cf_runner=cf_runner, now=now)
        except Exception as e:  # noqa: BLE001
            res[lane] = {"ok": False, "lane": lane, "decision": "error", "why": str(e)}
    return {"ok": True, "lanes": res}


if __name__ == "__main__":  # pragma: no cover - DSH 桥/运维手动入口
    import argparse
    import io
    import sys

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="车道参数顾问（每条车道每轮只提一个旋钮）")
    ap.add_argument("--lane", default="", help="只跑一条车道（hft/mid/long）；缺省三条都跑")
    ap.add_argument("--dry-run", action="store_true", help="只出提案（等价 MM_BRAIN_APPLY=0）")
    a = ap.parse_args()
    if a.dry_run:
        os.environ["MM_BRAIN_APPLY"] = "0"
    lanes = (a.lane,) if a.lane else LANES
    print(json.dumps(run_all(lanes=lanes), ensure_ascii=False, indent=2, default=str))
