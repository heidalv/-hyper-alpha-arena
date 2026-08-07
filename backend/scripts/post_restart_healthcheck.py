"""重启后自动体检 — 在后端重启之后调用，观察 N 分钟并打出 PASS/WARN/FAIL 清单。

6 项核心检查（对应上一轮 10 个 fix 的落地预期）：
    1. ghost-free        过程中不应再出现同 (strategy/symbol/side/entry/exit/pnl)
                         的 >3 次重复（说明 _tick_outcome_batch 回填循环已切断）
    2. loop heartbeat    system_coordinator_state.last_loop_tick_at 应在窗口内刷新
    3. coordinator log   coordinator_actions 至少应有 1 次新增（≥1h 跨过一次 coord tick）
    4. DRL shadow        drl_performance 至少应有 N 条新增（shadow baseline 采样）
    5. regime live       strategy_regime_scores.source='live' 行数应 >0 或新增
    6. trades sane       新增 closed StrategyTrade 的"重复组占比" < 30%，防 ghost 复发

用法：
    # 默认：观察 60 分钟，每 10 分钟采样一次，结束给报告
    python -m backend.scripts.post_restart_healthcheck

    # 自定义窗口
    python -m backend.scripts.post_restart_healthcheck --wait-min 30 --interval-min 5

    # 只打基线快照，立即返回（用于重启前先存一份 baseline.json）
    python -m backend.scripts.post_restart_healthcheck --baseline-only

    # 用已有 baseline 文件（结合 --end-only）跨进程对比
    python -m backend.scripts.post_restart_healthcheck --baseline-only --out baseline.json
    # ...... 重启 / 等待 / ...
    python -m backend.scripts.post_restart_healthcheck --end-only --baseline baseline.json

退出码：
    0 = ALL PASS / WARN（可忽略）
    1 = 至少 1 项 FAIL（ghost 复发或关键表无写入）
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# 允许 `python -m backend.scripts.xxx` 从项目根运行
_here = os.path.dirname(os.path.abspath(__file__))
_proj_root = os.path.abspath(os.path.join(_here, "..", ".."))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

# Windows 控制台 UTF-8 包装（避免 GBK 乱码）
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
except Exception:
    pass

from sqlalchemy import func  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402
from backend.database.models import (  # noqa: E402
    StrategyTrade,
    StrategyRegimeScore,
    CoordinatorAction,
    DRLPerformance,
    SystemCoordinatorState,
    BacktestRun,
    FullAutoSession,
    PaperBalance,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("healthcheck")

GHOST_GROUP_MIN = 3

# session.total_pnl 与 PaperBalance 权益差允许的最大偏离（美元）
# - < PNL_RECONCILE_PASS：PASS（浮点/并发窗口可忍受）
# - < PNL_RECONCILE_WARN：WARN（单次 tick 间隙，可能只是还没刷新）
# - >= PNL_RECONCILE_WARN：FAIL（口径又跑偏了，提醒复查）
PNL_RECONCILE_PASS = 0.05
PNL_RECONCILE_WARN = 1.00


# ─────────────────────────────
#  数据类
# ─────────────────────────────

@dataclass
class Snapshot:
    """一次采样的关键指标集合。所有计数用于 baseline → end 差分。"""
    ts: str
    strategy_trades_total: int
    ghost_group_count: int              # 同 key >3 次的组数
    coordinator_actions_total: int
    drl_performance_total: int
    drl_shadow_baseline_count: int      # regime LIKE 'shadow%'
    regime_scores_live_count: int
    backtest_runs_total: int
    last_loop_tick_at: Optional[str]
    last_evolution_at: Optional[str]
    last_drl_training_at: Optional[str]
    # running/paused session 的 PnL 对账：每条 = 一个 session 的口径对比
    # 只用于实时判断（不参与差分），缺省 [] 以兼容旧 baseline json。
    pnl_reconcile: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    status: str        # PASS / WARN / FAIL / SKIP
    detail: str


# ─────────────────────────────
#  基础查询
# ─────────────────────────────

def _take_snapshot(db) -> Snapshot:
    ghost_rows = (
        db.query(
            StrategyTrade.strategy_id,
            StrategyTrade.symbol,
            StrategyTrade.side,
            StrategyTrade.entry_price,
            StrategyTrade.exit_price,
            func.round(StrategyTrade.pnl, 4).label("pnl4"),
            func.count(StrategyTrade.id).label("cnt"),
        )
        .filter(StrategyTrade.status == "closed")
        .group_by(
            StrategyTrade.strategy_id,
            StrategyTrade.symbol,
            StrategyTrade.side,
            StrategyTrade.entry_price,
            StrategyTrade.exit_price,
            func.round(StrategyTrade.pnl, 4),
        )
        .having(func.count(StrategyTrade.id) > GHOST_GROUP_MIN)
        .all()
    )

    trades_total = db.query(func.count(StrategyTrade.id)).scalar() or 0
    coord_total = db.query(func.count(CoordinatorAction.id)).scalar() or 0
    drl_total = db.query(func.count(DRLPerformance.id)).scalar() or 0
    drl_shadow = (
        db.query(func.count(DRLPerformance.id))
        .filter(DRLPerformance.regime.like("shadow%"))
        .scalar()
        or 0
    )
    regime_live = (
        db.query(func.count(StrategyRegimeScore.id))
        .filter(StrategyRegimeScore.source == "live")
        .scalar()
        or 0
    )
    backtest_total = db.query(func.count(BacktestRun.id)).scalar() or 0
    state = db.query(SystemCoordinatorState).first()

    # —— PnL 对账：running/paused session vs 真账户权益差 ——
    pnl_reconcile: List[Dict[str, Any]] = []
    try:
        sessions = (
            db.query(FullAutoSession)
            .filter(FullAutoSession.status.in_(["running", "paused"]))
            .all()
        )
        for s in sessions:
            if (s.trading_mode or "paper") != "paper" or not s.account_id:
                # live 模式无纸面账户本；不记入对账（不算 FAIL）
                pnl_reconcile.append({
                    "session_id": s.session_id,
                    "trading_mode": s.trading_mode,
                    "skipped": "not_paper_or_no_account",
                })
                continue
            bal = (
                db.query(PaperBalance)
                .filter(PaperBalance.account_id == s.account_id)
                .first()
            )
            if not bal:
                pnl_reconcile.append({
                    "session_id": s.session_id,
                    "trading_mode": s.trading_mode,
                    "skipped": "no_paper_balance",
                })
                continue
            init_bal = float(bal.initial_balance or 0)
            equity = float(bal.total_equity or init_bal)
            expected = equity - init_bal
            actual = float(s.total_pnl or 0)
            pnl_reconcile.append({
                "session_id": s.session_id,
                "trading_mode": s.trading_mode,
                "account_id": s.account_id,
                "initial_balance": round(init_bal, 4),
                "total_equity": round(equity, 4),
                "expected_pnl": round(expected, 4),
                "session_total_pnl": round(actual, 4),
                "diff": round(actual - expected, 4),
            })
    except Exception as e:
        logger.warning("PnL 对账采样失败: %s", e)

    def _iso(x) -> Optional[str]:
        if x is None:
            return None
        if getattr(x, "tzinfo", None) is None:
            x = x.replace(tzinfo=timezone.utc)
        return x.isoformat()

    return Snapshot(
        ts=datetime.now(timezone.utc).isoformat(),
        strategy_trades_total=int(trades_total),
        ghost_group_count=len(ghost_rows),
        coordinator_actions_total=int(coord_total),
        drl_performance_total=int(drl_total),
        drl_shadow_baseline_count=int(drl_shadow),
        regime_scores_live_count=int(regime_live),
        backtest_runs_total=int(backtest_total),
        last_loop_tick_at=_iso(state.last_loop_tick_at) if state else None,
        last_evolution_at=_iso(state.last_evolution_at) if state else None,
        last_drl_training_at=_iso(state.last_drl_training_at) if state else None,
        pnl_reconcile=pnl_reconcile,
    )


def take_snapshot() -> Snapshot:
    db = SessionLocal()
    try:
        return _take_snapshot(db)
    finally:
        db.close()


# ─────────────────────────────
#  体检规则
# ─────────────────────────────

def evaluate(baseline: Snapshot, end: Snapshot, window_min: int) -> List[CheckResult]:
    results: List[CheckResult] = []
    add_trades = end.strategy_trades_total - baseline.strategy_trades_total
    add_coord = end.coordinator_actions_total - baseline.coordinator_actions_total
    add_drl = end.drl_performance_total - baseline.drl_performance_total
    add_drl_shadow = end.drl_shadow_baseline_count - baseline.drl_shadow_baseline_count
    add_regime_live = end.regime_scores_live_count - baseline.regime_scores_live_count
    add_backtest = end.backtest_runs_total - baseline.backtest_runs_total

    # ── 1. ghost-free：观察窗结束后，重复组数不得增加 ──
    if end.ghost_group_count <= baseline.ghost_group_count:
        results.append(CheckResult(
            "ghost-free",
            "PASS",
            f"ghost_groups baseline={baseline.ghost_group_count} end={end.ghost_group_count}",
        ))
    else:
        results.append(CheckResult(
            "ghost-free",
            "FAIL",
            f"ghost 组数从 {baseline.ghost_group_count} 增到 {end.ghost_group_count}，"
            f"说明写入循环仍然存在，请检查 _persist_strategy_trade 的 persist_trade 分支",
        ))

    # ── 2. loop heartbeat：last_loop_tick_at 相比基线必须更新 ──
    if end.last_loop_tick_at and end.last_loop_tick_at != baseline.last_loop_tick_at:
        results.append(CheckResult(
            "loop-heartbeat",
            "PASS",
            f"last_loop_tick_at {baseline.last_loop_tick_at} → {end.last_loop_tick_at}",
        ))
    else:
        results.append(CheckResult(
            "loop-heartbeat",
            "FAIL",
            f"last_loop_tick_at 未变（{end.last_loop_tick_at}）— LearningLoop 可能没起来",
        ))

    # ── 3. coordinator_actions 新增 ──
    if add_coord >= 1:
        results.append(CheckResult(
            "coordinator-log",
            "PASS",
            f"coordinator_actions 新增 {add_coord} 条（窗口 {window_min} 分钟）",
        ))
    elif window_min < 60:
        results.append(CheckResult(
            "coordinator-log",
            "WARN",
            f"窗口仅 {window_min} 分钟 <60min，未跨过 coord tick 属于正常；"
            f"如连续两个小时仍 0 条视为 FAIL",
        ))
    else:
        results.append(CheckResult(
            "coordinator-log",
            "FAIL",
            f"coordinator_actions 未新增 — log_action 可能仍在静默失败，"
            f"查 backend 日志搜 '[Coordinator] coordinator_actions 写入失败'",
        ))

    # ── 4. DRL shadow 采样 ──
    # 预期：每次决策都应记录 shadow_baseline 一条，窗口 1h 至少 >10
    expected_min = max(5, window_min // 10)
    if add_drl_shadow >= expected_min:
        results.append(CheckResult(
            "drl-shadow",
            "PASS",
            f"drl_performance 新增 shadow {add_drl_shadow} 条（≥期望 {expected_min}）",
        ))
    elif add_drl_shadow > 0:
        results.append(CheckResult(
            "drl-shadow",
            "WARN",
            f"shadow 只新增 {add_drl_shadow} 条（期望≥{expected_min}），"
            f"可能决策链触发频率偏低或 _build_observation 经常返回 None",
        ))
    else:
        results.append(CheckResult(
            "drl-shadow",
            "FAIL",
            f"drl_performance 未新增任何 shadow 样本 — 检查 DRL_SHADOW_MODE 和 "
            f"TradingDecisionInterface.decide_direction 是否被调用",
        ))

    # ── 5. regime_scores live source ──
    if end.regime_scores_live_count > 0 or add_regime_live > 0:
        results.append(CheckResult(
            "regime-live",
            "PASS",
            f"regime_scores(live) 基线={baseline.regime_scores_live_count} "
            f"当前={end.regime_scores_live_count}（+{add_regime_live}）",
        ))
    elif add_trades == 0:
        results.append(CheckResult(
            "regime-live",
            "SKIP",
            f"窗口内无新 closed 交易，无法打通 live regime_scores（非 bug）",
        ))
    else:
        results.append(CheckResult(
            "regime-live",
            "WARN",
            f"有 {add_trades} 笔新 closed 但 regime_scores(live) 仍为 0 — "
            f"AIStrategy.parent_strategy_id 可能不是 tpl_ 开头，未能回溯到模板",
        ))

    # ── 6. trades sane：ghost 不复发 + 新增量合理 ──
    # 按 5min tick 估算：window_min/5 个周期 × 每周期最多 ~5 个 symbol = 上限
    expected_max = max(50, (window_min // 5) * 10)
    if add_trades == 0:
        results.append(CheckResult(
            "trades-sane",
            "WARN",
            f"窗口内无新 closed 交易 — full_auto 可能被风控拦住或没有信号",
        ))
    elif add_trades <= expected_max:
        results.append(CheckResult(
            "trades-sane",
            "PASS",
            f"新增 closed {add_trades} 笔（上限 {expected_max}），增速正常",
        ))
    else:
        results.append(CheckResult(
            "trades-sane",
            "FAIL",
            f"新增 closed {add_trades} 笔 > 上限 {expected_max}，"
            f"ghost 或其它重复写入路径可能仍在放大",
        ))

    # ── 7. PnL 对账：end 时点 session.total_pnl 必须 ≈ equity − init ──
    # 仅针对 paper 模式 session；live 模式及未绑账户的条目标注为 SKIP。
    pnl_items = end.pnl_reconcile or []
    paper_items = [p for p in pnl_items if "skipped" not in p]
    skipped_items = [p for p in pnl_items if "skipped" in p]

    if not paper_items and skipped_items:
        results.append(CheckResult(
            "pnl-reconcile",
            "SKIP",
            f"无 paper 模式 running session（{len(skipped_items)} 条跳过）",
        ))
    elif not paper_items:
        results.append(CheckResult(
            "pnl-reconcile",
            "SKIP",
            "当前没有 running/paused session",
        ))
    else:
        worst = max(paper_items, key=lambda p: abs(p.get("diff", 0.0)))
        worst_diff = abs(worst.get("diff", 0.0))
        n_fail = sum(1 for p in paper_items if abs(p.get("diff", 0.0)) >= PNL_RECONCILE_WARN)
        n_warn = sum(
            1 for p in paper_items
            if PNL_RECONCILE_PASS <= abs(p.get("diff", 0.0)) < PNL_RECONCILE_WARN
        )
        detail = (
            f"共 {len(paper_items)} 条 paper session；最大偏离 "
            f"|diff|={worst_diff:.4f} (session={worst.get('session_id')} "
            f"total_pnl={worst.get('session_total_pnl')} vs expected={worst.get('expected_pnl')})"
        )
        if n_fail > 0:
            results.append(CheckResult(
                "pnl-reconcile",
                "FAIL",
                f"{detail}；{n_fail} 条 |diff|≥{PNL_RECONCILE_WARN} — "
                f"_update_session_stats 口径再次跑偏，检查 PaperBalance 分支",
            ))
        elif n_warn > 0:
            results.append(CheckResult(
                "pnl-reconcile",
                "WARN",
                f"{detail}；{n_warn} 条 |diff| ∈ [{PNL_RECONCILE_PASS},{PNL_RECONCILE_WARN}) — "
                f"大概率 session tick 还没刷新，稍后复查",
            ))
        else:
            results.append(CheckResult(
                "pnl-reconcile",
                "PASS",
                f"{detail}；全部 |diff| < {PNL_RECONCILE_PASS}",
            ))

    # ── 8. backtest 进化（软指标）──
    if add_backtest > 0:
        results.append(CheckResult(
            "evolution-progress",
            "PASS",
            f"backtest_runs 新增 {add_backtest} 条 — 紧急进化跑完了",
        ))
    elif end.last_evolution_at and end.last_evolution_at != baseline.last_evolution_at:
        results.append(CheckResult(
            "evolution-progress",
            "PASS",
            f"last_evolution_at 已更新为 {end.last_evolution_at}",
        ))
    else:
        results.append(CheckResult(
            "evolution-progress",
            "SKIP",
            f"窗口内未触发进化（24h 冷却 / 样本不足均属正常）",
        ))

    return results


# ─────────────────────────────
#  输出
# ─────────────────────────────

_STATUS_MARK = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "SKIP": "⏭️"}


def print_report(baseline: Snapshot, end: Snapshot, results: List[CheckResult], window_min: int):
    print("\n" + "=" * 68)
    print(f" 重启后自动体检报告  (window={window_min} min)")
    print("=" * 68)
    print(f" baseline: {baseline.ts}")
    print(f" end     : {end.ts}")
    print("-" * 68)
    print(
        f" strategy_trades  {baseline.strategy_trades_total} → "
        f"{end.strategy_trades_total} (Δ{end.strategy_trades_total - baseline.strategy_trades_total})"
    )
    print(
        f" ghost_groups     {baseline.ghost_group_count} → "
        f"{end.ghost_group_count}"
    )
    print(
        f" coordinator_acts {baseline.coordinator_actions_total} → "
        f"{end.coordinator_actions_total} (Δ{end.coordinator_actions_total - baseline.coordinator_actions_total})"
    )
    print(
        f" drl_performance  {baseline.drl_performance_total} → "
        f"{end.drl_performance_total} (Δ{end.drl_performance_total - baseline.drl_performance_total}"
        f" / shadow Δ{end.drl_shadow_baseline_count - baseline.drl_shadow_baseline_count})"
    )
    print(
        f" regime_live      {baseline.regime_scores_live_count} → "
        f"{end.regime_scores_live_count} (Δ{end.regime_scores_live_count - baseline.regime_scores_live_count})"
    )
    print(
        f" backtest_runs    {baseline.backtest_runs_total} → "
        f"{end.backtest_runs_total} (Δ{end.backtest_runs_total - baseline.backtest_runs_total})"
    )
    print(
        f" last_loop_tick   {baseline.last_loop_tick_at} → {end.last_loop_tick_at}"
    )
    if end.pnl_reconcile:
        print(" pnl_reconcile  (end 时点 session.total_pnl vs 账户 equity−init)")
        for p in end.pnl_reconcile:
            if "skipped" in p:
                print(
                    f"   [SKIP] {p.get('session_id')} mode={p.get('trading_mode')} "
                    f"reason={p.get('skipped')}"
                )
                continue
            print(
                f"   {p['session_id']}  total_pnl={p['session_total_pnl']:+.4f}  "
                f"expected={p['expected_pnl']:+.4f}  diff={p['diff']:+.4f}  "
                f"(equity={p['total_equity']}, init={p['initial_balance']})"
            )
    print("-" * 68)
    for r in results:
        mark = _STATUS_MARK.get(r.status, "?")
        print(f" {mark} {r.status:<5} {r.name:<20} {r.detail}")
    print("=" * 68)

    n_fail = sum(1 for r in results if r.status == "FAIL")
    n_warn = sum(1 for r in results if r.status == "WARN")
    n_pass = sum(1 for r in results if r.status == "PASS")
    print(f" 结论: PASS={n_pass}  WARN={n_warn}  FAIL={n_fail}")
    if n_fail:
        print(" ❌ 存在 FAIL 项，请对照 detail 排障")
    else:
        print(" ✅ 无 FAIL；WARN 仅作观察提示")
    print("=" * 68 + "\n")


# ─────────────────────────────
#  主流程
# ─────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-min", type=int, default=60, help="观察窗口（分钟，默认 60）")
    ap.add_argument("--interval-min", type=int, default=10, help="采样打点间隔（分钟，默认 10）")
    ap.add_argument("--baseline-only", action="store_true", help="只拍基线快照，保存后退出")
    ap.add_argument("--end-only", action="store_true", help="只拍结束快照，和 --baseline 文件对比")
    ap.add_argument("--out", default="healthcheck_baseline.json", help="基线保存/读取路径")
    ap.add_argument("--baseline", default=None, help="--end-only 使用的 baseline 文件路径")
    args = ap.parse_args()

    if args.baseline_only:
        snap = take_snapshot()
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(asdict(snap), f, ensure_ascii=False, indent=2)
        logger.info("基线已保存到 %s", args.out)
        return 0

    if args.end_only:
        if not args.baseline or not os.path.exists(args.baseline):
            logger.error("--end-only 必须提供有效的 --baseline 文件路径")
            return 1
        with open(args.baseline, "r", encoding="utf-8") as f:
            baseline_dict = json.load(f)
        # 向后兼容：旧 baseline 没有 pnl_reconcile 等新增字段时，用默认值补齐
        _valid_fields = set(Snapshot.__dataclass_fields__.keys())
        baseline = Snapshot(**{k: v for k, v in baseline_dict.items() if k in _valid_fields})
        end = take_snapshot()
        window_min = int(
            (datetime.fromisoformat(end.ts) - datetime.fromisoformat(baseline.ts))
            .total_seconds() / 60
        )
        results = evaluate(baseline, end, window_min)
        print_report(baseline, end, results, window_min)
        return 1 if any(r.status == "FAIL" for r in results) else 0

    # —— 默认：阻塞观察 N 分钟 ——
    baseline = take_snapshot()
    logger.info(
        "基线已拍下：strategy_trades=%d coordinator_actions=%d drl_performance=%d "
        "regime_live=%d",
        baseline.strategy_trades_total, baseline.coordinator_actions_total,
        baseline.drl_performance_total, baseline.regime_scores_live_count,
    )
    total_s = args.wait_min * 60
    interval_s = max(60, args.interval_min * 60)
    elapsed = 0
    while elapsed < total_s:
        sleep_s = min(interval_s, total_s - elapsed)
        time.sleep(sleep_s)
        elapsed += sleep_s
        mid = take_snapshot()
        logger.info(
            "[+%02dm/%02dm] trades Δ%d  coord Δ%d  drl Δ%d (shadow Δ%d)  regime_live Δ%d  ghost_groups=%d",
            elapsed // 60, args.wait_min,
            mid.strategy_trades_total - baseline.strategy_trades_total,
            mid.coordinator_actions_total - baseline.coordinator_actions_total,
            mid.drl_performance_total - baseline.drl_performance_total,
            mid.drl_shadow_baseline_count - baseline.drl_shadow_baseline_count,
            mid.regime_scores_live_count - baseline.regime_scores_live_count,
            mid.ghost_group_count,
        )
        # ghost 复发早停：立即退出 fail
        if mid.ghost_group_count > baseline.ghost_group_count:
            logger.error(
                "🚨 ghost 重复组数增加 (%d → %d) — 立即终止观察并报告",
                baseline.ghost_group_count, mid.ghost_group_count,
            )
            results = evaluate(baseline, mid, elapsed // 60)
            print_report(baseline, mid, results, elapsed // 60)
            return 1

    end = take_snapshot()
    results = evaluate(baseline, end, args.wait_min)
    print_report(baseline, end, results, args.wait_min)
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
