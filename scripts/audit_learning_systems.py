# -*- coding: utf-8 -*-
"""
学习系统体检脚本（只读）
- Hermes 自进化 SQLite: data/hermes_evolution.db
- Postgres: alpha_arena（主）/ alpha_analytics（复盘）
输出：各学习产出表的行数、最近更新时间、样本，判断"到底学没学到"。
"""
import os
import sys
import sqlite3
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

PG = {
    "arena": "postgresql+psycopg://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_arena",
    "analytics": "postgresql+psycopg://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_analytics",
}


def hr(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def q_sqlite(cur, sql, params=()):
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception as e:
        return [("ERR", str(e)[:120])]


# ---------------- Hermes ----------------
def audit_hermes():
    hr("HERMES 自进化系统 (SQLite: data/hermes_evolution.db)")
    path = os.path.join(ROOT, "data", "hermes_evolution.db")
    if not os.path.exists(path):
        print("  [缺失] hermes_evolution.db 不存在")
        return
    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    print(f"  文件: {path}")
    print(f"  最后写入: {mtime}  大小: {os.path.getsize(path)} bytes")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    tables = [r[0] for r in q_sqlite(cur, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print(f"  表: {tables}")
    for t in tables:
        cnt = q_sqlite(cur, f"SELECT COUNT(*) FROM {t}")
        n = cnt[0][0] if cnt and cnt[0][0] != "ERR" else cnt
        print(f"    - {t:38s}: {n} 行")

    hr("L2 Prompt 版本 (prompt_versions)")
    rows = q_sqlite(cur, "SELECT task_id, version, status, improved_rate, degraded_rate, avg_quality, created_at FROM prompt_versions ORDER BY id DESC LIMIT 30")
    if rows and rows[0][0] != "ERR":
        for r in rows:
            print(f"    {r['task_id']:32s} v{r['version']:8s} {str(r['status']):10s} imp={r['improved_rate']} deg={r['degraded_rate']} q={r['avg_quality']} {r['created_at']}")
    else:
        print("   ", rows)

    hr("L2 A/B 测试 (prompt_ab_tests)")
    rows = q_sqlite(cur, "SELECT task_id, version_a, version_b, winner, status, created_at FROM prompt_ab_tests ORDER BY id DESC LIMIT 20")
    for r in (rows or []):
        if r and r[0] != "ERR":
            print(f"    {r['task_id']:32s} A={r['version_a']} B={r['version_b']} winner={r['winner']} {r['status']} {r['created_at']}")
        else:
            print("   ", r)

    hr("L1 提案智慧 (proposal_wisdom_records) 最近")
    rows = q_sqlite(cur, "SELECT COUNT(*) AS c FROM proposal_wisdom_records")
    print("    total:", dict(rows[0]) if rows and rows[0][0] != "ERR" else rows)
    rows = q_sqlite(cur, "SELECT param_path, verdict, pnl_impact, created_at FROM proposal_wisdom_records ORDER BY id DESC LIMIT 12")
    for r in (rows or []):
        if r and r[0] != "ERR":
            print(f"    {str(r['param_path'])[:40]:40s} {r['verdict']:10s} pnl={r['pnl_impact']} {r['created_at']}")
        else:
            print("   ", r)

    hr("L1 参数效果模式库 (param_effect_patterns)")
    rows = q_sqlite(cur, "SELECT param_path, direction, sample_count, avg_pnl_impact, confidence FROM param_effect_patterns ORDER BY sample_count DESC LIMIT 15")
    for r in (rows or []):
        if r and r[0] != "ERR":
            print(f"    {str(r['param_path'])[:40]:40s} dir={r['direction']} n={r['sample_count']} avgpnl={r['avg_pnl_impact']} conf={r['confidence']}")
        else:
            print("   ", r)

    hr("Agent 决策智慧 (agent_decision_wisdom)")
    rows = q_sqlite(cur, "SELECT agent_type, COUNT(*) c, SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) wins FROM agent_decision_wisdom GROUP BY agent_type")
    for r in (rows or []):
        if r and r[0] != "ERR":
            print(f"    {r['agent_type']:12s} n={r['c']} wins={r['wins']}")
        else:
            print("   ", r)

    hr("L4 策略创生候选 (strategy_genesis_candidates)")
    rows = q_sqlite(cur, "SELECT name, status, trades_count, win_rate, avg_pnl, created_at FROM strategy_genesis_candidates ORDER BY id DESC LIMIT 15")
    for r in (rows or []):
        if r and r[0] != "ERR":
            print(f"    {str(r['name'])[:30]:30s} {str(r['status']):12s} trades={r['trades_count']} wr={r['win_rate']} pnl={r['avg_pnl']} {r['created_at']}")
        else:
            print("   ", r)

    hr("L3 架构进化提案 (architecture_evolution_proposals)")
    rows = q_sqlite(cur, "SELECT title, status, created_at FROM architecture_evolution_proposals ORDER BY id DESC LIMIT 10")
    for r in (rows or []):
        if r and r[0] != "ERR":
            print(f"    {str(r['title'])[:50]:50s} {str(r['status']):10s} {r['created_at']}")
        else:
            print("   ", r)

    hr("任务运行记录 (task_run_log) 每个任务最近一次")
    rows = q_sqlite(cur, """
        SELECT task_name, MAX(finished_at) last_finished, COUNT(*) runs,
               SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) ok_runs
        FROM task_run_log GROUP BY task_name ORDER BY last_finished DESC
    """)
    for r in (rows or []):
        if r and r[0] != "ERR":
            print(f"    {r['task_name']:36s} runs={r['runs']:3d} ok={r['ok_runs']:3d} last={r['last_finished']}")
        else:
            print("   ", r)
    # 最近失败
    hr("task_run_log 最近失败/异常")
    rows = q_sqlite(cur, "SELECT task_name, ok, note, finished_at FROM task_run_log WHERE ok=0 OR ok IS NULL ORDER BY id DESC LIMIT 15")
    if rows and (not rows or rows[0][0] != "ERR"):
        if not rows:
            print("    （无失败记录）")
        for r in rows:
            print(f"    {r['task_name']:32s} ok={r['ok']} {str(r['note'])[:70]} {r['finished_at']}")
    else:
        print("   ", rows)
    con.close()


# ---------------- Postgres ----------------
def audit_pg():
    try:
        from sqlalchemy import create_engine, text
    except Exception as e:
        print("  [跳过 PG] sqlalchemy 不可用:", e)
        return

    def run(engine, label, queries):
        hr(label)
        with engine.connect() as c:
            for title, sql in queries:
                print(f"\n  -- {title}")
                try:
                    res = c.execute(text(sql))
                    cols = res.keys()
                    rows = res.fetchall()
                    if not rows:
                        print("     (空)")
                    for row in rows[:25]:
                        d = dict(zip(cols, row))
                        print("    ", {k: (str(v)[:38] if v is not None else None) for k, v in d.items()})
                except Exception as e:
                    print("     ERR:", str(e)[:160])

    try:
        arena = create_engine(PG["arena"])
    except Exception as e:
        print("  [PG arena 连接失败]", e)
        return

    run(arena, "POSTGRES alpha_arena — 学习核心表", [
        ("strategy_memories 概览", "SELECT COUNT(*) total, SUM(CASE WHEN total_trades>0 THEN 1 ELSE 0 END) with_trades, MAX(updated_at) last_update FROM strategy_memories"),
        ("strategy_memories Top10 按交易数", "SELECT strategy_id, total_trades, win_rate, sharpe_ratio, max_drawdown, array_length(string_to_array(COALESCE(key_lessons,''),'|'),1) lessons, updated_at FROM strategy_memories ORDER BY total_trades DESC LIMIT 10"),
        ("strategy_trades 概览", "SELECT COUNT(*) total, MAX(created_at) last, MIN(created_at) first FROM strategy_trades"),
        ("strategy_trades 近7天", "SELECT COUNT(*) FROM strategy_trades WHERE created_at > NOW() - INTERVAL '7 days'"),
        ("strategy_templates 来源分布", "SELECT source, COUNT(*) FROM strategy_templates GROUP BY source"),
        ("promoted 模板样例", "SELECT name, source, created_at FROM strategy_templates WHERE source='promoted' ORDER BY created_at DESC LIMIT 8"),
        ("prompt_training_records 概览", "SELECT COUNT(*) total, MAX(created_at) last FROM prompt_training_records"),
        ("prompt_training_records 近30天按类型", "SELECT record_type, COUNT(*) FROM prompt_training_records WHERE created_at > NOW() - INTERVAL '30 days' GROUP BY record_type"),
        ("strategy_regime_scores 概览", "SELECT COUNT(*) total, MAX(updated_at) last FROM strategy_regime_scores"),
        ("drl_performance 概览", "SELECT COUNT(*) total, SUM(CASE WHEN is_correct IS NOT NULL THEN 1 ELSE 0 END) graded, MAX(created_at) last FROM drl_performance"),
        ("coordinator_actions 近", "SELECT action_type, COUNT(*) c, MAX(created_at) last FROM coordinator_actions GROUP BY action_type ORDER BY last DESC LIMIT 10"),
    ])

    try:
        ana = create_engine(PG["analytics"])
        run(ana, "POSTGRES alpha_analytics — 复盘/教训表", [
            ("decision_retrospectives 概览", "SELECT COUNT(*) total, MAX(created_at) last FROM decision_retrospectives"),
            ("近30天 was_correct 分布", "SELECT was_correct, COUNT(*) FROM decision_retrospectives WHERE created_at > NOW() - INTERVAL '30 days' GROUP BY was_correct"),
            ("最近复盘教训样例", "SELECT strategy_id, was_correct, LEFT(COALESCE(lesson_learned,''),60) lesson, created_at FROM decision_retrospectives ORDER BY created_at DESC LIMIT 8"),
            ("mlto_signal_weights", "SELECT source, weight, updated_at FROM mlto_signal_weights ORDER BY updated_at DESC LIMIT 10"),
            ("mlto_thesis_events 概览", "SELECT COUNT(*) total, MAX(created_at) last FROM mlto_thesis_events"),
        ])
    except Exception as e:
        print("  [PG analytics] ", str(e)[:160])


if __name__ == "__main__":
    print("学习系统体检  @", datetime.now())
    audit_hermes()
    audit_pg()
    print("\n完成。")
