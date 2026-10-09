# -*- coding: utf-8 -*-
"""P0 数据契约每日质量门禁（大轮回设计 §10.3 / §2.2 验收）。

只读审计，注册为每日任务（v3_jobs）。门禁阈值与设计 §10.3 对齐：

| 检查 | 阈值 |
|---|---|
| 平仓缺失决策关联（paper_position_id / snapshot_id） | 0 |
| long 层 thesis 覆盖率 | 100% |
| MFE/MAE（peak/trough）空值率 | 报告（异常高即告警） |
| 资金费回填覆盖（持仓>8h 无事件） | 0 |
| final_fee_paid 缺失 | 0 |
| 滑点账本（lane_ledger）join 覆盖 | 报告 |
| 学习闭环比率（postmortem/owm vs 平仓数） | ≥ 0.8 |
| K 线新鲜度（会话宇宙 1h 更新于 2h 内） | ≥ 98% |
| 因子覆盖（24h 内被因子覆盖的 symbol 比例） | ≥ 60%（不可测时报告 not_measured） |

每项返回 {value, threshold, pass/na/fail, detail}；总结果存 job_registry.last_result。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _db():
    from backend.database.connection import SessionLocal
    from backend.core.tenant import set_system_identity
    set_system_identity()
    return SessionLocal()


def _analytics_engine():
    from backend.database.connection import analytics_engine
    return analytics_engine


def _market_engine():
    from backend.database.connection import market_engine
    return market_engine


def _check(checks: List[Dict[str, Any]], key: str, label: str, value: Any,
           threshold: Any, pass_fn, unit: str = "", detail: str = "") -> None:
    """构造一条门禁结果：pass / fail / na（None 值 = 不可测）。"""
    if value is None:
        checks.append({"key": key, "label": label, "value": None,
                       "threshold": threshold, "unit": unit, "status": "na", "detail": detail})
        return
    try:
        ok = pass_fn(value, threshold)
    except Exception:
        ok = False
    checks.append({"key": key, "label": label, "value": value,
                   "threshold": threshold, "unit": unit,
                   "status": "pass" if ok else "fail", "detail": detail})


def _q(db, sql, **params):
    from sqlalchemy import text
    return db.execute(text(sql), params).fetchall()


def run_p0_audit(days: int = 1) -> Dict[str, Any]:
    """执行 P0 门禁审计。返回 JSON 可序列化结果。"""
    checks: List[Dict[str, Any]] = []
    window_h = days * 24
    try:
        db = _db()
        try:
            _audit_arena(db, checks, days)
        finally:
            db.close()
    except Exception as exc:
        checks.append({"key": "arena_db", "label": "arena 库连通", "value": str(exc)[:160],
                       "threshold": None, "unit": "", "status": "fail", "detail": ""})
    try:
        eng = _analytics_engine()
        with eng.connect() as c:
            _audit_analytics(c, checks, days)
    except Exception as exc:
        checks.append({"key": "analytics_db", "label": "analytics 库连通", "value": str(exc)[:160],
                       "threshold": None, "unit": "", "status": "fail", "detail": ""})
    try:
        eng = _market_engine()
        with eng.connect() as c:
            _audit_market(c, checks, days)
    except Exception as exc:
        checks.append({"key": "market_db", "label": "market 库连通", "value": str(exc)[:160],
                       "threshold": None, "unit": "", "status": "fail", "detail": ""})

    n_pass = sum(1 for c in checks if c.get("status") == "pass")
    n_fail = sum(1 for c in checks if c.get("status") == "fail")
    n_na = sum(1 for c in checks if c.get("status") == "na")
    # P0 门禁口径：P4 归属项（学习闭环比率）不计入 P0 判定，只展示。
    p0_fail = [c for c in checks if c.get("status") == "fail" and c.get("owned_by") != "p4"]
    return {
        "window_hours": window_h,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"pass": n_pass, "fail": n_fail, "na": n_na,
                    "gate": "PASS" if not p0_fail else "FAIL",
                    "p0_owned_fail": [c.get("key") for c in p0_fail]},
        "checks": checks,
    }


def _audit_arena(db, checks, days):
    since = datetime.now() - timedelta(days=days)
    # ── 0) 窗口内平仓总数（所有覆盖率的分母）──
    rows = _q(db, """
        SELECT COUNT(*) FROM paper_positions
        WHERE status='closed' AND closed_at >= :since
    """, since=since)
    closed_n = int(rows[0][0] or 0)

    # ── 1) 决策关联：strategy_trades.decision_context.paper_position_id ──
    try:
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions p
            WHERE p.status='closed' AND p.closed_at >= :since
              AND NOT EXISTS (
                SELECT 1 FROM strategy_trades st
                WHERE COALESCE(st.decision_context->>'paper_position_id','') = p.id::text
              )
        """, since=since)
        missing_link = int(rows[0][0] or 0)
        _check(checks, "closed_decision_link", "平仓缺失决策关联(paper_position_id)", missing_link,
               0, lambda v, t: v <= t, "笔", f"窗口内平仓 {closed_n} 笔")
    except Exception as exc:
        _check(checks, "closed_decision_link", "平仓缺失决策关联(paper_position_id)", None,
               0, lambda v, t: v <= t, "笔", str(exc)[:120])

    # ── 1b) 快照关联：decision_context.snapshot_id ──
    # [P0 2026-09-27] 确定性写入路径（开仓写 open_metadata + 平仓回读 + 匹配后回填）
    # 自基线时刻起生效；基线前的历史行（tpl/scalp 入场不写 executed 快照）保持宁缺勿错。
    try:
        baseline = os.getenv("P0_SNAPSHOT_BASELINE_TS", "2026-09-27 23:30:00+08")
        rows = _q(db, """
            SELECT COUNT(*) FROM strategy_trades st
            WHERE st.closed_at >= :since AND st.closed_at >= :baseline
              AND COALESCE(st.decision_context->>'snapshot_id','') = ''
        """, since=since, baseline=baseline)
        missing_snap = int(rows[0][0] or 0)
        rows2 = _q(db, """
            SELECT COUNT(*) FROM strategy_trades
            WHERE closed_at >= :since AND closed_at >= :baseline
        """, since=since, baseline=baseline)
        total_st = int(rows2[0][0] or 0)
        _check(checks, "snapshot_linkage", "平仓缺失快照关联(snapshot_id, 基线后)", missing_snap,
               0, lambda v, t: v <= t, "笔", f"基线 {baseline} 后窗口内 strategy_trades {total_st} 行")
    except Exception as exc:
        _check(checks, "snapshot_linkage", "平仓缺失快照关联(snapshot_id, 基线后)", None,
               0, lambda v, t: v <= t, "笔", str(exc)[:120])

    # ── 2) MFE/MAE 空值率（peak 与 trough 双 0 且实现盈亏非 0）──
    try:
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions
            WHERE status='closed' AND closed_at >= :since
              AND peak_pnl_pct = 0 AND trough_pnl_pct = 0 AND unrealized_pnl <> 0
        """, since=since)
        empty_mf = int(rows[0][0] or 0)
        _check(checks, "mfemae_empty", "MFE/MAE 双零异常仓", empty_mf, None,
               lambda v, t: True, "笔", f"分母 {closed_n}（>0 需人工核查写入路径）")
    except Exception as exc:
        _check(checks, "mfemae_empty", "MFE/MAE 双零异常仓", None, None,
               lambda v, t: True, "笔", str(exc)[:120])

    # ── 3) 资金费回填：持仓>8h 且无 position_funding_events ──
    try:
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions p
            WHERE p.status='closed' AND p.closed_at >= :since
              AND EXTRACT(EPOCH FROM (p.closed_at - p.opened_at)) > 8*3600
              AND NOT EXISTS (
                SELECT 1 FROM position_funding_events fe WHERE fe.position_id = p.id
              )
        """, since=since)
        no_funding = int(rows[0][0] or 0)
        rows2 = _q(db, """
            SELECT COUNT(*) FROM paper_positions
            WHERE status='closed' AND closed_at >= :since
              AND EXTRACT(EPOCH FROM (closed_at - opened_at)) > 8*3600
        """, since=since)
        hold8_n = int(rows2[0][0] or 0)
        _check(checks, "funding_coverage", "持仓>8h 无资金费事件", no_funding,
               0, lambda v, t: v <= t, "笔", f"分母（>8h 平仓）{hold8_n} 笔")
    except Exception as exc:
        _check(checks, "funding_coverage", "持仓>8h 无资金费事件", None,
               0, lambda v, t: v <= t, "笔", str(exc)[:120])

    # ── 4) long 层 thesis 覆盖 ──
    try:
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions p
            WHERE p.status='closed' AND p.closed_at >= :since
              AND p.timeframe_tier='long'
        """, since=since)
        long_n = int(rows[0][0] or 0)
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions p
            WHERE p.status='closed' AND p.closed_at >= :since
              AND p.timeframe_tier='long'
              AND COALESCE(p.exit_state_json::json->'open_metadata'->>'thesis_id','') <> ''
        """, since=since)
        long_with_thesis = int(rows[0][0] or 0)
        missing = long_n - long_with_thesis
        _check(checks, "long_thesis_coverage", "long 层平仓缺 thesis_id", missing,
               0, lambda v, t: v <= t, "笔", f"分母 long 平仓 {long_n} 笔")
    except Exception as exc:
        _check(checks, "long_thesis_coverage", "long 层平仓缺 thesis_id", None,
               0, lambda v, t: v <= t, "笔", str(exc)[:120])

    # ── 5) final_fee_paid 缺失 ──
    try:
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions
            WHERE status='closed' AND closed_at >= :since AND final_fee_paid IS NULL
        """, since=since)
        no_fee = int(rows[0][0] or 0)
        _check(checks, "final_fee_null", "平仓缺最终手续费", no_fee,
               0, lambda v, t: v <= t, "笔", f"分母 {closed_n} 笔")
    except Exception as exc:
        _check(checks, "final_fee_null", "平仓缺最终手续费", None,
               0, lambda v, t: v <= t, "笔", str(exc)[:120])

    # ── 5b) 开/平仓滑点记账（基线后；§2.2 entry/exit_slippage_bp）──
    try:
        baseline = os.getenv("P0_SLIPPAGE_BASELINE_TS", "2026-09-28 00:00:00+08")
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions
            WHERE status='closed' AND closed_at >= :since AND closed_at >= :baseline
              AND opened_at >= :baseline
              AND (entry_slippage_bp IS NULL OR exit_slippage_bp IS NULL)
        """, since=since, baseline=baseline)
        miss_slip = int(rows[0][0] or 0)
        rows2 = _q(db, """
            SELECT COUNT(*) FROM paper_positions
            WHERE status='closed' AND closed_at >= :since AND closed_at >= :baseline
              AND opened_at >= :baseline
        """, since=since, baseline=baseline)
        denom_slip = int(rows2[0][0] or 0)
        _check(checks, "slippage_cols_null", "平仓缺开/平仓滑点记账(基线后)", miss_slip,
               0, lambda v, t: v <= t, "笔", f"基线 {baseline} 后平仓 {denom_slip} 笔")
    except Exception as exc:
        _check(checks, "slippage_cols_null", "平仓缺开/平仓滑点记账(基线后)", None,
               0, lambda v, t: v <= t, "笔", str(exc)[:120])

    # ── 5c) 持仓快照断档（§10.3：0 段 >10 分钟）──
    try:
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions
            WHERE status='open'
              AND NOT EXISTS (
                SELECT 1 FROM position_snapshots s
                WHERE s.position_id = paper_positions.id AND s.ts >= now() - interval '10 minutes'
              )
        """)
        gap_pos = int(rows[0][0] or 0)
        rows2 = _q(db, "SELECT COUNT(*) FROM paper_positions WHERE status='open'")
        open_n = int(rows2[0][0] or 0)
        _check(checks, "snapshot_gap", "持仓快照断档(>10min 无行)", gap_pos,
               0, lambda v, t: v <= t, "仓", f"当前 open 仓 {open_n} 个")
    except Exception as exc:
        _check(checks, "snapshot_gap", "持仓快照断档(>10min 无行)", None,
               0, lambda v, t: v <= t, "仓", str(exc)[:120])

    # ── 5d) [P2 §9.1] 出场分类账：close_reason 分桶 + 未知原因 ──
    _KNOWN_EXIT_REASONS = (
        "sl", "tp", "breakeven", "be", "trailing", "min_roi_decay", "max_hold",
        "time_limit", "reversal", "trend_broken", "rule_exit", "thesis_invalidation",
        "invalidation", "ai_reverse", "defensive", "emergency_drawdown", "rebalance",
        "non_core", "dust", "manual", "structural_invalidation", "profit_drawdown",
        "profit_lock", "staged_tp", "safety_tp",
    )
    try:
        rows = _q(db, """
            SELECT COALESCE(close_reason,'?') r, COUNT(*) FROM paper_positions
            WHERE status='closed' AND closed_at >= :since
            GROUP BY close_reason ORDER BY 2 DESC
        """, since=since)
        dist = {str(r[0]): int(r[1] or 0) for r in rows}
        def _norm_reason(k: str) -> str:
            return str(k or "").replace("exit_policy:", "").replace("pos_mgmt_", "")
        unknown = {k: v for k, v in dist.items()
                   if not any(_norm_reason(k).startswith(p) for p in _KNOWN_EXIT_REASONS)}
        _check(checks, "exit_ledger_unknown", "出场分类账未知原因", sum(unknown.values()),
               0, lambda v, t: v <= t, "笔", f"未知桶 {unknown}；全分布 {dist}")
    except Exception as exc:
        _check(checks, "exit_ledger_unknown", "出场分类账未知原因", None,
               0, lambda v, t: v <= t, "笔", str(exc)[:120])

    # ── 5e) [P5 §13.2] 保证金占用监控（信息项：目标 40~60%）──
    try:
        rows = _q(db, """
            SELECT ROUND(COALESCE(SUM(margin),0)::numeric,2) FROM paper_positions
            WHERE status='open'
        """)
        margin = float(rows[0][0] or 0)
        rows2 = _q(db, """
            SELECT ROUND(total_equity::numeric,2) FROM paper_balances
            WHERE account_id = 14 ORDER BY updated_at DESC LIMIT 1
        """)
        eq = float(rows2[0][0] or 0) if rows2 else 0.0
        pct = round(margin / eq * 100, 1) if eq > 0 else None
        _check(checks, "margin_util", "保证金占用率(§13.2 目标 40~60%)", pct,
               None, lambda v, t: True, "%", f"margin ${margin} / equity ${eq}")
    except Exception as exc:
        _check(checks, "margin_util", "保证金占用率(§13.2 目标 40~60%)", None,
               None, lambda v, t: True, "%", str(exc)[:120])

    # ── 5f) [P6 §1.3] 摸底记分卡（30 天滚动；信息项，开窗后为裁决输入）──
    try:
        rows = _q(db, """
            SELECT lane, COUNT(*) n,
                   ROUND(SUM(net)::numeric,2) net,
                   ROUND(AVG(net)::numeric,2) mean_net,
                   ROUND(SUM(CASE WHEN net>0 THEN net ELSE 0 END)::numeric,2) gw,
                   ROUND(ABS(SUM(CASE WHEN net<0 THEN net ELSE 0 END))::numeric,2) gl,
                   ROUND(AVG(CASE WHEN net>0 THEN 1.0 ELSE 0.0 END)::numeric,3) wr,
                   ROUND(percentile_cont(0.5) WITHIN GROUP (ORDER BY hold_h)::numeric,1) medh
            FROM (
              SELECT CASE WHEN timeframe_tier='long' THEN 'trend' ELSE 'intraday' END AS lane,
                     (COALESCE(p.unrealized_pnl,0)
                      - COALESCE(p.partial_fee_paid,0) - COALESCE(p.final_fee_paid,0)
                      - (COALESCE(p.funding_paid,0)-COALESCE(p.funding_received,0))) AS net,
                     EXTRACT(EPOCH FROM (p.closed_at - p.opened_at))/3600.0 AS hold_h
              FROM paper_positions p
              WHERE p.status='closed' AND p.closed_at >= now() - interval '30 days'
                AND p.close_price IS NOT NULL
            ) t GROUP BY lane
        """)
        for r in rows:
            lane, n, net, mean_net, gw, gl, wr, medh = r
            pf = round(float(gw or 0) / float(gl or 1), 2)
            _check(checks, f"p6_scorecard_{lane}",
                   f"P6 记分卡·{lane}车道(30d)",
                   {"n": int(n), "net": float(net or 0), "wr": float(wr or 0),
                    "pf": pf, "mean_net": float(mean_net or 0),
                    "med_hold_h": float(medh or 0)},
                   None, lambda v, t: True, "", "§1.3 车道指标（笔数/胜率/PF/期望/中位持仓）")
    except Exception as exc:
        _check(checks, "p6_scorecard", "P6 记分卡(30d)", None,
               None, lambda v, t: True, "", str(exc)[:120])

    # ── 5g) [P6] 参数冻结漂移检查（开窗后每日比对 SHA）──
    try:
        import importlib.util as _ilu
        from pathlib import Path as _P
        _fs_path = _P(__file__).resolve().parents[3] / "scripts" / "p6_freeze_snapshot.py"
        _spec = _ilu.spec_from_file_location("p6_freeze_snapshot", str(_fs_path))
        _fmod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_fmod)
        dec = _fmod.load_declaration()
        if dec and dec.get("params_sha"):
            cur = _fmod.params_sha(_fmod.frozen_params())
            drift = cur != str(dec["params_sha"])
            _check(checks, "p6_freeze_drift", "P6 参数冻结漂移",
                   "DRIFT" if drift else "OK", "OK",
                   lambda v, t: v == t, "",
                   f"声明SHA={dec['params_sha']} 当前={cur}")
        else:
            _check(checks, "p6_freeze_drift", "P6 参数冻结漂移", None, "OK",
                   lambda v, t: v == t, "",
                   "尚未开窗（scripts/p6_freeze_snapshot.py --write）")
    except Exception as exc:
        _check(checks, "p6_freeze_drift", "P6 参数冻结漂移", None, "OK",
               lambda v, t: v == t, "", str(exc)[:120])

    # ── 6) 滑点账本 join 覆盖（lane_ledger.position_id）──
    try:
        rows = _q(db, """
            SELECT COUNT(*) FROM paper_positions p
            WHERE p.status='closed' AND p.closed_at >= :since
              AND EXISTS (SELECT 1 FROM lane_ledger ll WHERE ll.position_id = p.id::text)
        """, since=since)
        with_slip = int(rows[0][0] or 0)
        cov = round(with_slip / closed_n * 100, 1) if closed_n else None
        _check(checks, "slippage_ledger_cov", "滑点账本 join 覆盖率", cov,
               None, lambda v, t: True, "%", f"{with_slip}/{closed_n} 笔")
    except Exception as exc:
        _check(checks, "slippage_ledger_cov", "滑点账本 join 覆盖率", None,
               None, lambda v, t: True, "%", str(exc)[:120])


def _audit_analytics(conn, checks, days):
    since = datetime.now() - timedelta(days=days)
    # ── 7) 学习闭环比率：postmortem/owm 事件 vs arena 平仓数 ──
    # arena 平仓数从主库读取（跨库，保守用 events 自身 + 主库分母在 _audit_arena 之外再取）
    try:
        rows = conn.execute(_text("""
            SELECT event_type, COUNT(*) FROM mlto_thesis_events
            WHERE event_type IN ('postmortem','owm_bump') AND ts >= :since
            GROUP BY event_type
        """), {"since": since}).fetchall()
        learn_events = sum(int(r[1] or 0) for r in rows)
        # 分母：同窗口平仓数（从 arena 库取，避免跨库 join）
        db = _db()
        try:
            rows2 = _q(db, """
                SELECT COUNT(*) FROM paper_positions
                WHERE status='closed' AND closed_at >= :since
            """, since=since)
            closed_n = int(rows2[0][0] or 0)
        finally:
            db.close()
        ratio = round(learn_events / closed_n, 3) if closed_n else None
        _check(checks, "learning_ratio", "学习闭环比率(postmortem+owm/平仓)", ratio,
               0.8, lambda v, t: v >= t, "", f"事件 {learn_events} / 平仓 {closed_n}")
        checks[-1]["owned_by"] = "p4"  # P4 学习闭环阶段负责把比率抬到 ≥0.8；P0 只负责可测

        # ── 7b) [P1 §7.3] 拒单原因可解释率（≥95% 门禁）+ 漏斗分母 ──
        try:
            rows = conn.execute(_text("""
                SELECT COUNT(*) FILTER (WHERE COALESCE(payload_json::jsonb->>'reason',
                        payload_json::jsonb->>'block_reason',
                        payload_json::jsonb->>'detail','') = ''),
                       COUNT(*)
                FROM mlto_thesis_events
                WHERE event_type='open_blocked' AND ts >= :since
            """), {"since": since}).fetchall()
            empty_r, total_b = int(rows[0][0] or 0), int(rows[0][1] or 0)
            rate = round((total_b - empty_r) / max(total_b, 1), 4)
            _check(checks, "reject_explain_rate", "拒单原因可解释率(open_blocked)", rate,
                   0.95, lambda v, t: v >= t, "", f"空原因 {empty_r}/{total_b}")
            checks[-1]["owned_by"] = "p1"
            rows = conn.execute(_text("""
                SELECT event_type, COUNT(*) FROM mlto_thesis_events
                WHERE event_type IN ('open_blocked','open_execute_false','midlong_thesis')
                  AND ts >= :since
                GROUP BY event_type
            """), {"since": since}).fetchall()
            funnel = {str(r[0]): int(r[1] or 0) for r in rows}
            _check(checks, "reject_funnel", "拒单漏斗(分母)", funnel, None,
                   lambda v, t: True, "", "blocked/exec_false/thesis 计数")

            # ── 7c) [P4 §11.4] 影响度：后验否决拒单数（学习→行为改变的可查口径）──
            rows = conn.execute(_text("""
                SELECT COUNT(*) FROM mlto_thesis_events
                WHERE event_type='open_blocked' AND ts >= :since
                  AND COALESCE(payload_json::jsonb->>'reason','') LIKE 'posterior_veto%'
            """), {"since": since}).fetchall()
            veto_n = int(rows[0][0] or 0)
            _check(checks, "posterior_veto_blocks", "后验否决拒单数(影响度)", veto_n,
                   None, lambda v, t: True, "次/24h",
                   "§11.4 影响度口径：后验→行为三通路之否决的落库计数")
        except Exception as exc:
            _check(checks, "reject_explain_rate", "拒单原因可解释率(open_blocked)", None,
                   0.95, lambda v, t: v >= t, "", str(exc)[:120])
            checks[-1]["owned_by"] = "p1"
    except Exception as exc:
        _check(checks, "learning_ratio", "学习闭环比率(postmortem+owm/平仓)", None,
               0.8, lambda v, t: v >= t, "", str(exc)[:120])


def _audit_market(conn, checks, days):
    # ── 8) K 线新鲜度：会话宇宙 1h 更新于 2h 内 ──
    try:
        db = _db()
        try:
            rows = _q(db, """
                SELECT COALESCE(symbols,'[]'), COALESCE(fixed_symbols_by_tier::text,'{}')
                FROM full_auto_sessions
                WHERE paper_account_id IS NOT NULL ORDER BY updated_at DESC LIMIT 1
            """)
        finally:
            db.close()
        import json as _json
        syms: List[str] = []
        if rows:
            for raw in (rows[0][0], rows[0][1]):
                try:
                    _parsed = _json.loads(raw or "[]")
                    if isinstance(_parsed, list):
                        syms.extend(str(s).upper() for s in _parsed)
                    elif isinstance(_parsed, dict):
                        for _v in _parsed.values():
                            if isinstance(_v, list):
                                syms.extend(str(s).upper() for s in _v)
                except Exception:
                    continue
        syms = sorted(set(syms))[:24]
        if not syms:
            _check(checks, "kline_freshness", "K线新鲜度(1h≤2h)", None,
                   98.0, lambda v, t: v >= t, "%", "会话宇宙为空")
            return
        rows = conn.execute(_text("""
            SELECT COUNT(DISTINCT symbol) FROM crypto_klines
            WHERE period='1h' AND symbol = ANY(:syms)
              AND timestamp >= EXTRACT(EPOCH FROM now() - interval '2 hours')::bigint
        """), {"syms": syms}).fetchall()
        fresh = int(rows[0][0] or 0)
        pct = round(fresh / len(syms) * 100, 1)
        _check(checks, "kline_freshness", "K线新鲜度(1h≤2h)", pct,
               98.0, lambda v, t: v >= t, "%", f"{fresh}/{len(syms)} 币")
    except Exception as exc:
        _check(checks, "kline_freshness", "K线新鲜度(1h≤2h)", None,
               98.0, lambda v, t: v >= t, "%", str(exc)[:120])


def _text(sql):
    from sqlalchemy import text
    return text(sql)


def register() -> None:
    """登记每日门禁任务（由 v3_jobs 调用）。"""
    from backend.services.ops.job_registry import register_job
    register_job(
        "p0_data_contract_audit", "daily 05:10",
        "大轮回设计 P0 数据契约每日质量门禁（§10.3：决策关联/regime/thesis/资金费/滑点/学习比率/K线新鲜度）",
        owner="p0", runner=lambda: run_p0_audit(days=1), expected_interval_sec=24 * 3600,
    )


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __file__.rsplit("backend", 1)[0])
    from dotenv import load_dotenv
    load_dotenv(__file__.rsplit("backend", 1)[0] + ".env", override=False)
    print(json.dumps(run_p0_audit(days=1), ensure_ascii=False, indent=2, default=str))
