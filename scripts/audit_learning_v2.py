# -*- coding: utf-8 -*-
"""学习系统体检 v2（列名已对齐真实schema，只读）。"""
import os, sys, sqlite3
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def hr(t): print("\n" + "="*72 + f"\n{t}\n" + "="*72)

# ---------- Hermes ----------
con = sqlite3.connect(os.path.join(ROOT, "data", "hermes_evolution.db"))
con.row_factory = sqlite3.Row
c = con.cursor()
def sq(sql):
    try:
        c.execute(sql); return [dict(r) for r in c.fetchall()]
    except Exception as e:
        return [{"ERR": str(e)[:120]}]

hr("HERMES L2 Prompt 版本（是否真的进化了 prompt）")
for r in sq("SELECT task_id,version,status,change_type,proposals_generated,avg_improved_rate,avg_degraded_rate,avg_quality_score,created_at,activated_at FROM prompt_versions ORDER BY id"):
    print("  ", r)

hr("HERMES L2 A/B 测试结果")
for r in sq("SELECT task_id,version_a,version_b,improved_rate_a,improved_rate_b,winner,status,started_at,concluded_at FROM prompt_ab_tests ORDER BY id"):
    print("  ", r)

hr("HERMES L1 提案智慧 outcome 分布 + 最近")
for r in sq("SELECT outcome,COUNT(*) c FROM proposal_wisdom_records GROUP BY outcome"):
    print("  ", r)
for r in sq("SELECT proposal_id,outcome,param_key,param_direction,pnl_impact,confidence,created_at FROM proposal_wisdom_records ORDER BY id DESC LIMIT 8"):
    print("  ", r)

hr("HERMES L1 参数效果模式库（学到的因果规律）")
for r in sq("SELECT param_key,market_condition,direction,outcome,sample_count,avg_pnl_impact,confidence_avg,causal_ratio FROM param_effect_patterns ORDER BY sample_count DESC"):
    print("  ", r)

hr("HERMES Agent 决策智慧（swing/trend 平仓后采集）")
for r in sq("SELECT agent_type,outcome,COUNT(*) c FROM agent_decision_wisdom GROUP BY agent_type,outcome"):
    print("  ", r)

hr("HERMES L4 策略创生候选 paper_status 分布 + 最优")
for r in sq("SELECT paper_status,COUNT(*) c FROM strategy_genesis_candidates GROUP BY paper_status"):
    print("  ", r)
for r in sq("SELECT variant_name,paper_status,paper_trades,paper_win_rate,paper_pnl,viability_score,created_at FROM strategy_genesis_candidates ORDER BY viability_score DESC NULLS LAST LIMIT 8"):
    print("  ", r)

hr("HERMES L3 架构提案 status 分布")
for r in sq("SELECT status,COUNT(*) c FROM architecture_evolution_proposals GROUP BY status"):
    print("  ", r)

hr("HERMES 任务运行记录（每个定时任务最近状态）")
for r in sq("SELECT job_id,run_count,last_status,last_finished_at,last_run_duration_ms,substr(COALESCE(last_error,''),1,60) err FROM task_run_log ORDER BY last_finished_at DESC"):
    print("  ", r)
con.close()

# ---------- Postgres ----------
from sqlalchemy import create_engine, text
def pg(db): return create_engine(f"postgresql+psycopg://laobao:alpha_pass@localhost:5432/{db}", isolation_level="AUTOCOMMIT")
def run(eng, title, qs):
    hr(title)
    with eng.connect() as cc:
        for name, sql in qs:
            print(f"\n  -- {name}")
            try:
                res = cc.execute(text(sql)); cols = res.keys()
                rows = res.fetchall()
                if not rows: print("     (空)")
                for row in rows[:20]:
                    print("    ", {k:(str(v)[:44] if v is not None else None) for k,v in zip(cols,row)})
            except Exception as e:
                print("     ERR:", str(e)[:150])

run(pg("alpha_arena"), "POSTGRES alpha_arena — 学习核心", [
 ("strategy_memories 概览", "SELECT COUNT(*) total, COUNT(*) FILTER(WHERE total_trades>=15) ge15, COUNT(*) FILTER(WHERE total_trades>=15 AND win_rate>=0.5) promotable, MAX(updated_at) last FROM strategy_memories"),
 ("记忆胜率分布", "SELECT CASE WHEN win_rate<0.3 THEN 'a<30%' WHEN win_rate<0.45 THEN 'b30-45%' WHEN win_rate<0.55 THEN 'c45-55%' ELSE 'd>=55%' END bucket, COUNT(*) c FROM strategy_memories WHERE total_trades>=10 GROUP BY 1 ORDER BY 1"),
 ("Top 交易数记忆", "SELECT strategy_id,total_trades,round(win_rate::numeric,3) wr,round(COALESCE(sharpe_ratio,0)::numeric,2) sharpe,round(COALESCE(max_drawdown,0)::numeric,3) mdd,length(COALESCE(key_lessons::text,'')) lesson_len,updated_at FROM strategy_memories ORDER BY total_trades DESC LIMIT 12"),
 ("有key_lessons的记忆数", "SELECT COUNT(*) FROM strategy_memories WHERE key_lessons IS NOT NULL AND key_lessons::text NOT IN ('','[]','{}','null')"),
 ("strategy_trades 概览", "SELECT COUNT(*) total, MAX(closed_at) last_close, COUNT(*) FILTER(WHERE closed_at> NOW()-INTERVAL '24 hours') last24h, COUNT(*) FILTER(WHERE closed_at> NOW()-INTERVAL '7 days') last7d FROM strategy_trades"),
 ("decision_quality_score 有值比例", "SELECT COUNT(*) total, COUNT(decision_quality_score) scored, round(AVG(decision_quality_score)::numeric,3) avg_q FROM strategy_trades WHERE closed_at> NOW()-INTERVAL '30 days'"),
 ("strategy_templates 来源", "SELECT source,COUNT(*) c FROM strategy_templates GROUP BY source ORDER BY c DESC"),
 ("promoted 模板", "SELECT name,tier,backtest_total_trades,round(COALESCE(backtest_win_rate,0)::numeric,3) wr,created_at FROM strategy_templates WHERE source='promoted' ORDER BY created_at DESC LIMIT 8"),
 ("prompt_training_records", "SELECT COUNT(*) total, MAX(created_at) last, COUNT(*) FILTER(WHERE created_at>NOW()-INTERVAL '7 days') last7d FROM prompt_training_records"),
 ("strategy_regime_scores", "SELECT COUNT(*) total, COUNT(DISTINCT template_id) tpls, MAX(last_updated) last FROM strategy_regime_scores"),
 ("drl_performance", "SELECT COUNT(*) total, COUNT(is_correct) graded, MAX(timestamp) last, COUNT(DISTINCT model_version) versions FROM drl_performance"),
 ("coordinator_actions 最近", "SELECT action, COUNT(*) c, MAX(ts) last FROM coordinator_actions GROUP BY action ORDER BY last DESC NULLS LAST LIMIT 10"),
])

run(pg("alpha_analytics"), "POSTGRES alpha_analytics — 复盘/教训/MLTO", [
 ("decision_retrospectives 概览", "SELECT COUNT(*) total, MAX(created_at) last, COUNT(*) FILTER(WHERE created_at>NOW()-INTERVAL '24 hours') last24h FROM decision_retrospectives"),
 ("近7天 was_correct", "SELECT was_correct, COUNT(*) c FROM decision_retrospectives WHERE created_at>NOW()-INTERVAL '7 days' GROUP BY was_correct"),
 ("有lesson_learned比例(近30d)", "SELECT COUNT(*) total, COUNT(*) FILTER(WHERE COALESCE(lesson_learned,'')<>'') has_lesson, COUNT(*) FILTER(WHERE COALESCE(mistake_analysis,'')<>'') has_mistake FROM decision_retrospectives WHERE created_at>NOW()-INTERVAL '30 days'"),
 ("mlto_signal_weights", "SELECT COUNT(*) FROM mlto_signal_weights"),
 ("mlto_thesis_events", "SELECT COUNT(*) total, MAX(ts) last FROM mlto_thesis_events"),
 ("mlto_thesis_events 类型", "SELECT event_type, COUNT(*) c FROM mlto_thesis_events GROUP BY event_type ORDER BY c DESC LIMIT 10"),
])
print("\n完成。")
