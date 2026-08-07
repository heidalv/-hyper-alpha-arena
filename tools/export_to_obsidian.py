#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_to_obsidian.py
=====================
把 001Alpha 的三类离线 Agent 数据导出成一个 Obsidian vault:

1. data/opencode_reports/*.md (190 个)         → 01-分析报告/  (加 YAML frontmatter)
2. data/qaa_knowledge/trading_lessons.jsonl    → 02-交易教训/  (一教训一笔记 + 双链)
3. data/hermes_evolution.db (SQLite, 8 张表)   → 03-Hermes进化/ + Hermes四层进化.canvas

外加:
- Agent进化中心.md        (MOC 主页,Dataview 动态表)
- _canvas/*.canvas        (Canvas 流程图)
- .obsidian/              (含 dataview 插件预热配置,启用 DQL)

纯标准库,无第三方依赖。幂等可重跑。

用法:
    python tools/export_to_obsidian.py
    # 或自定义路径
    python tools/export_to_obsidian.py --data ./data --vault ./obsidian_vault
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent                  # Hyper-Alpha-Arena/
DEFAULT_DATA = PROJECT_ROOT / "data"
DEFAULT_VAULT = PROJECT_ROOT / "obsidian_vault"

DIR_REPORTS = "01-分析报告"
DIR_LESSONS = "02-交易教训"
DIR_HERMES = "03-Hermes进化"
DIR_DECISIONS = "04-Agent决策"
DIR_CANVAS = "_canvas"
DIR_LAYOUTS = "_layouts"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def yaml_escape(value) -> str:
    """把 Python 值安全地塞进 YAML frontmatter。"""
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    # 含特殊字符就用双引号包起来,内部双引号转义
    if any(c in s for c in [':', '#', '[', ']', '{', '}', ',', '"', "'", '\n', '\r']):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def frontmatter(meta: dict) -> str:
    """生成 YAML frontmatter 块(包含起始/结束 ---)。"""
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            items = ", ".join(yaml_escape(x) for x in v)
            lines.append(f"{k}: [{items}]")
        else:
            lines.append(f"{k}: {yaml_escape(v)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_filename(name: str, maxlen: int = 80) -> str:
    """把任意字符串做成安全的文件名片段。"""
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return (name or "untitled")[:maxlen]


def reset_dir(p: Path) -> Path:
    """清空并重建目录(保证幂等)。"""
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    return ensure_dir(p)


# ---------------------------------------------------------------------------
# 1. OpenCode 分析报告 → 01-分析报告/
# ---------------------------------------------------------------------------
def parse_analysis_json(json_path: Path) -> dict | None:
    """从配对的 .json 提取 severity/domain/findings。容错。"""
    try:
        raw = json_path.read_text(encoding='utf-8')
        data = json.loads(raw)
    except Exception:
        return None
    findings = data.get("findings") or []
    categories = sorted({f.get("category", "?") for f in findings if isinstance(f, dict)})
    return {
        "severity": data.get("severity", "unknown"),
        "domain": data.get("domain", "unknown"),
        "categories": categories,
        "finding_count": len(findings),
    }


def ts_from_name(name: str) -> str:
    """analysis_20260613_020400 → 2026-06-13 02:04:00。失败回退文件 mtime。"""
    m = re.search(r'(\d{8})_(\d{6})', name)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return ""


def export_reports(data_dir: Path, vault: Path) -> int:
    src = data_dir / "opencode_reports"
    if not src.exists():
        print(f"[skip] {src} 不存在")
        return 0
    dst = reset_dir(vault / DIR_REPORTS)

    md_files = sorted(src.glob("*.md"))
    count = 0
    for md in md_files:
        meta = parse_analysis_json(md.with_suffix(".json")) or {}
        date_str = ts_from_name(md.stem) or datetime.fromtimestamp(md.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        date_only = (date_str.split(" ")[0]) if date_str else ""

        fm_meta = {
            "type": "analysis",
            "severity": meta.get("severity", "unknown"),
            "domain": meta.get("domain", "unknown"),
            "date": date_only,
            "datetime": date_str,
            "categories": meta.get("categories", []),
            "finding_count": meta.get("finding_count", 0),
            "source": "opencode",
        }
        body = md.read_text(encoding='utf-8').lstrip("\ufeff").lstrip()
        # 如果原文已是 frontmatter 开头就不重复加(目前不是)
        content = frontmatter(fm_meta) + "\n" + f"# 📊 {md.stem}\n\n" + body
        out = dst / f"{md.stem}.md"
        out.write_text(content, encoding='utf-8')
        count += 1
    print(f"[ok] 分析报告导出 {count} 个 → {DIR_REPORTS}/")
    return count


# ---------------------------------------------------------------------------
# 2. 交易教训 → 02-交易教训/ (一教训一笔记 + 双链)
# ---------------------------------------------------------------------------
def export_lessons(data_dir: Path, vault: Path) -> int:
    src = data_dir / "qaa_knowledge" / "trading_lessons.jsonl"
    if not src.exists():
        print(f"[skip] {src} 不存在")
        return 0
    dst = reset_dir(vault / DIR_LESSONS)

    count = 0
    with src.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            md = d.get("metadata", {}) or {}
            chunk_id = d.get("chunk_id", f"unknown-{count}")
            symbol = md.get("symbol", "?")
            side = md.get("side", "")
            pnl = md.get("pnl")
            pnl_pct = md.get("pnl_pct")
            strategy_id = md.get("strategy_id", "")
            tier = md.get("tier", "")
            exit_reason = md.get("exit_reason", "")
            trade_nature = md.get("trade_nature", "")
            holding_minutes = md.get("holding_minutes")
            was_correct = md.get("was_correct", "")
            text = d.get("text", "")
            source = d.get("source", "")

            # 双链:策略、币种。Graph view 会自动织出关系网
            strategy_link = f"[[{strategy_id}]]" if strategy_id else ""
            symbol_link = f"[[{symbol}]]" if symbol and symbol != "?" else ""

            fm_meta = {
                "type": "lesson",
                "symbol": symbol,
                "side": side,
                "pnl": pnl if pnl is not None else 0,
                "pnl_pct": pnl_pct if pnl_pct is not None else 0,
                "strategy": strategy_id,
                "tier": tier,
                "trade_nature": trade_nature,
                "exit_reason": exit_reason,
                "holding_minutes": holding_minutes if holding_minutes is not None else 0,
                "was_correct": was_correct,
                "source": source,
                "profitable": (pnl is not None and pnl > 0),
            }
            title_pnlsign = "盈利" if (pnl is not None and pnl > 0) else "亏损"
            title = f"#{symbol} {side} {title_pnlsign}" + (f" {pnl_pct:.2f}%" if pnl_pct is not None else "")
            body_parts = [
                f"# 💡 {title}",
                "",
                f"> {text}",
                "",
                "## 上下文",
                f"- 币种: {symbol_link}",
                f"- 方向: {side}",
                f"- 策略: {strategy_link}",
                f"- 层级: {tier}  ·  性质: {trade_nature}",
                f"- PnL: `{pnl}`  ({pnl_pct}%)",
                f"- 持仓时长: {holding_minutes} 分钟",
                f"- 退出原因: {exit_reason}",
                f"- 判断正确: **{was_correct}**",
                f"- 来源: `{source}`",
                "",
                "## 关联",
                f"- 策略详情: {strategy_link}",
                f"- 同币种其他教训: {symbol_link}",
            ]
            content = frontmatter(fm_meta) + "\n".join(body_parts) + "\n"
            out = dst / f"lesson-{safe_filename(chunk_id, 60)}.md"
            out.write_text(content, encoding='utf-8')
            count += 1
    print(f"[ok] 交易教训导出 {count} 个 → {DIR_LESSONS}/")
    return count


# ---------------------------------------------------------------------------
# 3. Hermes 进化库 → 03-Hermes进化/
# ---------------------------------------------------------------------------
def _parse_json_field(val):
    """数据库里的 evidence_patterns / variant_config 是 TEXT 存的 JSON 字符串。"""
    if not val:
        return None
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return val  # 原样返回(可能是普通字符串)


def _truncate(s, n=2000):
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= n else s[:n] + " …*(截断)*"


def export_hermes(data_dir: Path, vault: Path) -> dict:
    db_path = data_dir / "hermes_evolution.db"
    stats = {}
    if not db_path.exists():
        print(f"[skip] {db_path} 不存在")
        return stats
    dst = reset_dir(vault / DIR_HERMES)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ---- 3a. architecture_evolution_proposals ----
    proposals = []
    try:
        rows = cur.execute(
            "SELECT id, title, category, description, feasibility, expected_impact, "
            "implementation_notes, status, created_at, reviewed_at, evidence_patterns "
            "FROM architecture_evolution_proposals ORDER BY id"
        ).fetchall()
    except Exception as e:
        print(f"[warn] 读 architecture_evolution_proposals 失败: {e}")
        rows = []
    for r in rows:
        evid = _parse_json_field(r["evidence_patterns"])
        evid_str = json.dumps(evid, ensure_ascii=False, indent=2) if isinstance(evid, (list, dict)) else (evid or "")
        fm = {
            "type": "arch_proposal",
            "layer": "L3",
            "title": r["title"],
            "category": r["category"],
            "feasibility": r["feasibility"],
            "expected_impact": r["expected_impact"],
            "status": r["status"],
            "created_at": r["created_at"],
            "reviewed_at": r["reviewed_at"] or "",
        }
        body = [
            f"# 🏗️ {r['title']}",
            "",
            f"**状态**: `{r['status']}`  ·  **影响**: `{r['expected_impact']}`  ·  **可行性**: `{r['feasibility']}`",
            f"**类别**: `{r['category']}`  ·  **创建**: {r['created_at']}  ·  **评审**: {r['reviewed_at'] or '—'}",
            "",
            "## 描述",
            _truncate(r["description"], 3000),
            "",
            "## 实施要点",
            _truncate(r["implementation_notes"], 3000),
            "",
            "## 证据模式",
            "```json",
            _truncate(evid_str, 4000),
            "```",
        ]
        content = frontmatter(fm) + "\n".join(body) + "\n"
        fname = f"L3-提案-{r['id']:04d}-{safe_filename(r['title'], 50)}.md"
        (dst / fname).write_text(content, encoding='utf-8')
        proposals.append(r["title"])
    stats["proposals"] = len(proposals)
    print(f"[ok] L3 架构提案导出 {len(proposals)} 个")

    # ---- 3b. proposal_wisdom_records → 聚合成一张静态表(MOC 也用 Dataview 查) ----
    wisdom_rows = []
    try:
        rows = cur.execute(
            "SELECT id, proposal_id, outcome, focus, market_condition, param_key, "
            "param_direction, param_delta_pct, pnl_impact, win_rate_delta, confidence, created_at "
            "FROM proposal_wisdom_records ORDER BY id"
        ).fetchall()
        for r in rows:
            fm = {
                "type": "wisdom",
                "layer": "L1",
                "outcome": r["outcome"],
                "focus": r["focus"],
                "market_condition": r["market_condition"],
                "param_key": r["param_key"],
                "param_direction": r["param_direction"] or "",
                "param_delta_pct": r["param_delta_pct"] or 0,
                "pnl_impact": r["pnl_impact"] or 0,
                "win_rate_delta": r["win_rate_delta"] or 0,
                "confidence": r["confidence"] or 0,
                "proposal_id": r["proposal_id"],
                "created_at": r["created_at"] or "",
            }
            content = frontmatter(fm) + f"# 💎 智慧 #{r['id']} ({r['outcome']})\n\n" \
                f"**参数**: `{r['param_key']}` · **方向**: `{r['param_direction']}`\n\n" \
                f"- PnL 影响: `{r['pnl_impact']}`\n- 胜率变化: `{r['win_rate_delta']}`\n- 置信度: `{r['confidence']}`\n" \
                f"- 市场状态: `{r['market_condition']}` · 焦点: `{r['focus']}`\n"
            (dst / f"L1-智慧-{r['id']:04d}.md").write_text(content, encoding='utf-8')
            wisdom_rows.append(r["param_key"])
    except Exception as e:
        print(f"[warn] 读 proposal_wisdom_records 失败: {e}")
    stats["wisdom"] = len(wisdom_rows)
    print(f"[ok] L1 参数智慧导出 {len(wisdom_rows)} 个")

    # ---- 3c. strategy_genesis_candidates ----
    genesis = 0
    try:
        rows = cur.execute(
            "SELECT id, template_seed, variant_name, variant_config, paper_status, "
            "paper_pnl, paper_win_rate, paper_trades, paper_days, viability_score, created_at, validated_at "
            "FROM strategy_genesis_candidates ORDER BY viability_score DESC"
        ).fetchall()
        for r in rows:
            cfg = _parse_json_field(r["variant_config"])
            cfg_str = json.dumps(cfg, ensure_ascii=False, indent=2) if isinstance(cfg, (dict, list)) else (cfg or "")
            fm = {
                "type": "genesis",
                "layer": "L4",
                "variant_name": r["variant_name"],
                "paper_status": r["paper_status"],
                "paper_pnl": r["paper_pnl"] or 0,
                "paper_win_rate": r["paper_win_rate"] or 0,
                "paper_trades": r["paper_trades"] or 0,
                "paper_days": r["paper_days"] or 0,
                "viability_score": r["viability_score"] or 0,
                "created_at": r["created_at"] or "",
                "validated_at": r["validated_at"] or "",
            }
            content = frontmatter(fm) + f"# 🧬 {r['variant_name']}\n\n" \
                f"**状态**: `{r['paper_status']}` · **可行性**: `{r['viability_score']}`\n\n" \
                f"- 模拟 PnL: `{r['paper_pnl']}` · 胜率: `{r['paper_win_rate']}`\n" \
                f"- 模拟 {r['paper_trades']} 笔 / {r['paper_days']} 天\n" \
                f"- 模板种子: {_truncate(r['template_seed'], 400)}\n\n" \
                "## 变体配置\n```json\n" + _truncate(cfg_str, 3000) + "\n```\n"
            (dst / f"L4-创生-{r['id']:04d}-{safe_filename(r['variant_name'], 50)}.md").write_text(content, encoding='utf-8')
            genesis += 1
    except Exception as e:
        print(f"[warn] 读 strategy_genesis_candidates 失败: {e}")
    stats["genesis"] = genesis
    print(f"[ok] L4 策略创生导出 {genesis} 个")

    # ---- 3d. prompt_versions → Prompt 进化时间线笔记 ----
    prompt_count = 0
    try:
        rows = cur.execute(
            "SELECT id, task_id, version, change_summary, change_type, parent_version, "
            "proposals_generated, avg_quality_score, avg_approval_rate, avg_improved_rate, "
            "avg_degraded_rate, status, created_at, activated_at "
            "FROM prompt_versions ORDER BY created_at"
        ).fetchall()
        for r in rows:
            fm = {
                "type": "prompt_version",
                "layer": "L2",
                "task_id": r["task_id"],
                "version": r["version"],
                "change_type": r["change_type"],
                "parent_version": r["parent_version"] or "",
                "proposals_generated": r["proposals_generated"] or 0,
                "avg_quality_score": r["avg_quality_score"] or 0,
                "avg_improved_rate": r["avg_improved_rate"] or 0,
                "avg_degraded_rate": r["avg_degraded_rate"] or 0,
                "status": r["status"],
                "created_at": r["created_at"] or "",
            }
            content = frontmatter(fm) + f"# 📝 Prompt {r['version']} ({r['status']})\n\n" \
                f"**任务**: `{r['task_id']}` · **变更类型**: `{r['change_type']}`\n\n" \
                f"- 生成提案: `{r['proposals_generated']}`\n" \
                f"- 平均质量: `{r['avg_quality_score']}` · 改进率: `{r['avg_improved_rate']}` · 退化率: `{r['avg_degraded_rate']}`\n\n" \
                f"**变更摘要**: {_truncate(r['change_summary'], 1500)}\n"
            (dst / f"L2-Prompt-{safe_filename(r['version'], 20)}.md").write_text(content, encoding='utf-8')
            prompt_count += 1
    except Exception as e:
        print(f"[warn] 读 prompt_versions 失败: {e}")
    stats["prompt_versions"] = prompt_count
    print(f"[ok] L2 Prompt 版本导出 {prompt_count} 个")

    # ---- 3e. prompt_ab_tests ----
    ab_count = 0
    try:
        rows = cur.execute(
            "SELECT id, task_id, version_a, version_b, winner, status, "
            "proposals_a, proposals_b, avg_quality_a, avg_quality_b, p_value, started_at, concluded_at "
            "FROM prompt_ab_tests ORDER BY id"
        ).fetchall()
        for r in rows:
            fm = {
                "type": "ab_test",
                "layer": "L2",
                "task_id": r["task_id"],
                "version_a": r["version_a"],
                "version_b": r["version_b"],
                "winner": r["winner"],
                "status": r["status"],
                "p_value": r["p_value"] if r["p_value"] is not None else "",
                "started_at": r["started_at"] or "",
                "concluded_at": r["concluded_at"] or "",
            }
            content = frontmatter(fm) + f"# 🧪 A/B 测试 #{r['id']} → 胜者: {r['winner']}\n\n" \
                f"**{r['version_a']} vs {r['version_b']}**\n\n" \
                f"| 指标 | A | B |\n|---|---|---|\n" \
                f"| 提案数 | {r['proposals_a']} | {r['proposals_b']} |\n" \
                f"| 平均质量 | {r['avg_quality_a']} | {r['avg_quality_b']} |\n\n" \
                f"- p 值: `{r['p_value']}`\n- 状态: `{r['status']}`\n"
            (dst / f"L2-AB测试-{r['id']:04d}.md").write_text(content, encoding='utf-8')
            ab_count += 1
    except Exception as e:
        print(f"[warn] 读 prompt_ab_tests 失败: {e}")
    stats["ab_tests"] = ab_count
    print(f"[ok] L2 A/B 测试导出 {ab_count} 个")

    # ---- 3f. agent_decision_wisdom ----
    adw = 0
    try:
        rows = cur.execute(
            "SELECT id, agent_type, symbol, side, regime, close_reason, decision_action, "
            "confidence, pnl, pnl_pct, outcome, pattern_key, created_at "
            "FROM agent_decision_wisdom ORDER BY id"
        ).fetchall()
        for r in rows:
            fm = {
                "type": "agent_wisdom",
                "layer": "L1",
                "agent_type": r["agent_type"] or "",
                "symbol": r["symbol"] or "",
                "side": r["side"] or "",
                "regime": r["regime"] or "",
                "outcome": r["outcome"] or "",
                "pattern_key": r["pattern_key"] or "",
                "confidence": r["confidence"] or 0,
                "pnl": r["pnl"] or 0,
                "pnl_pct": r["pnl_pct"] or 0,
                "created_at": r["created_at"] or "",
            }
            content = frontmatter(fm) + f"# 🧠 {r['agent_type']} 智慧 #{r['id']}\n\n" \
                f"- 标的: `{r['symbol']}` {r['side']} · regime: `{r['regime']}`\n" \
                f"- 结果: **{r['outcome']}** · PnL: `{r['pnl']}` ({r['pnl_pct']}%)\n" \
                f"- 模式: `{r['pattern_key']}` · 置信度: `{r['confidence']}`\n"
            (dst / f"L1-Agent智慧-{r['id']:04d}.md").write_text(content, encoding='utf-8')
            adw += 1
    except Exception as e:
        print(f"[warn] 读 agent_decision_wisdom 失败: {e}")
    stats["agent_wisdom"] = adw
    print(f"[ok] L1 Agent 决策智慧导出 {adw} 个")

    # ---- 3g. param_effect_patterns → 单个聚合笔记 ----
    try:
        rows = cur.execute(
            "SELECT param_key, market_condition, direction, outcome, sample_count, "
            "avg_pnl_impact, avg_win_rate_delta, confidence_avg, causal_ratio, last_updated "
            "FROM param_effect_patterns ORDER BY sample_count DESC"
        ).fetchall()
        lines = ["| 参数 | 市场 | 方向 | 结果 | 样本 | 平均PnL影响 | 胜率Δ | 置信 | 因果 | 更新 |",
                 "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(
                f"| `{r['param_key']}` | {r['market_condition']} | {r['direction']} | "
                f"{r['outcome']} | {r['sample_count']} | {r['avg_pnl_impact']} | "
                f"{r['avg_win_rate_delta']} | {r['confidence_avg']} | {r['causal_ratio']} | {r['last_updated']} |"
            )
        fm = {"type": "patterns_overview", "layer": "L1"}
        content = frontmatter(fm) + "# 🔬 参数效应模式总览\n\n" + "\n".join(lines) + "\n"
        (dst / "L1-参数效应模式总览.md").write_text(content, encoding='utf-8')
        stats["patterns"] = len(rows)
        print(f"[ok] 参数效应模式总览导出 {len(rows)} 行")
    except Exception as e:
        print(f"[warn] 读 param_effect_patterns 失败: {e}")

    conn.close()
    return stats


# ---------------------------------------------------------------------------
# 4. Agent 决策过程 → 04-Agent决策/
#    数据源: decision_arbiter.jsonl + governor_decisions.jsonl + runtime_governor_decisions.jsonl
#    这是 "agent 怎么一步步做决策" 的过程流,区别于前面的 "agent 产出物"
# ---------------------------------------------------------------------------
def _parse_ts(ts) -> str:
    """把 ISO 或 epoch 秒转成 'YYYY-MM-DD HH:MM:SS'。失败原样返回。"""
    if ts is None:
        return ""
    s = str(ts)
    # epoch 秒
    if re.match(r'^\d{10}(\.\d+)?$', s):
        try:
            return datetime.fromtimestamp(float(s), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return s
    # ISO
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return s


def _parse_extra(extra_str) -> dict:
    """decision_arbiter 的 extra 字段是 Python repr(dict) 字符串,容错解析。"""
    if not extra_str:
        return {}
    if isinstance(extra_str, dict):
        return extra_str
    # 先试 JSON
    try:
        return json.loads(extra_str)
    except Exception:
        pass
    # Python repr: {'detail': '...', 'flag': 'shadow'}
    try:
        import ast
        v = ast.literal_eval(extra_str)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def export_decisions(data_dir: Path, vault: Path) -> dict:
    """导出 agent 决策仲裁流 + 治理器裁决。"""
    dst = reset_dir(vault / DIR_DECISIONS)
    stats = {"arbiter": 0, "governor": 0, "runtime_governor": 0}

    # ---- 4a. decision_arbiter.jsonl —— 决策仲裁(shadow/enforce 模式) ----
    src = data_dir / "decision_arbiter.jsonl"
    if src.exists():
        rows = []
        with src.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        # 按时间排序,取全部(2204 条尚可);每条一个笔记会太多,改为按 (symbol, block_rule) 聚合 + 保留最近若干条原始
        # 策略: 1) 每条仲裁都建笔记(agent 思考流的"一帧") 2) 另建一个聚合总览
        # 但 2204 笔记会让 Graph 太密。折中: 只导出 would_block=True 的(blocked 决策最有价值)+ 最近 200 条
        blocked = [r for r in rows if str(r.get("would_block")).lower() == "true"]
        # 按 ts 排序
        def _sort_key(r):
            return _parse_ts(r.get("ts")) or ""
        blocked.sort(key=_sort_key, reverse=True)
        recent = sorted(rows, key=_sort_key, reverse=True)[:200]
        seen_ts = set()
        exported = []
        for r in blocked + recent:
            ts = _parse_ts(r.get("ts"))
            if ts in seen_ts:
                continue
            seen_ts.add(ts)
            symbol = r.get("symbol", "?")
            source = r.get("source", "")
            reason = r.get("reason_intended", "")
            tier = r.get("pos_tier", "")
            side = r.get("pos_side", "")
            pnl_pct = r.get("pnl_pct")
            sl_breach = r.get("sl_breach")
            confidence = r.get("confidence")
            would_block = r.get("would_block")
            block_rule = r.get("block_rule", "")
            extra = _parse_extra(r.get("extra"))
            flag = extra.get("flag", "")
            detail = extra.get("detail", "")

            fm = {
                "type": "arbiter",
                "symbol": symbol,
                "source": source,
                "reason_intended": reason,
                "tier": tier,
                "side": side,
                "pnl_pct": (float(pnl_pct) if pnl_pct is not None else 0),
                "sl_breach": (float(sl_breach) if sl_breach is not None else 0),
                "confidence": (float(confidence) if confidence is not None else 0),
                "would_block": (str(would_block).lower() == "true"),
                "block_rule": block_rule,
                "flag": flag,
                "ts": ts,
                "date": ((ts.split(" ")[0]) if ts else ""),
            }
            icon = "🚫" if str(would_block).lower() == "true" else "✅"
            body = [
                f"# {icon} 仲裁: {symbol} {reason}",
                "",
                f"**时间**: {ts}",
                f"**结果**: {'**拦截**' if str(would_block).lower() == 'true' else '放行'}  ·  规则: `{block_rule}`",
                f"**来源**: `{source}` · 标的: `[[{symbol}]]` · 层级: `{tier}` · 方向: `{side}`",
                "",
                "## 上下文",
                f"- PnL%: `{pnl_pct}`",
                f"- 止损触发: `{sl_breach}`",
                f"- 置信度: `{confidence}`",
                f"- 标志: `{flag or '—'}`",
                f"- 详情: {detail}",
                "",
                f"> 意图动作: `{reason}`",
            ]
            content = frontmatter(fm) + "\n".join(body) + "\n"
            fname = f"仲裁-{(ts or 'nots').replace(':','').replace(' ','-')}-{safe_filename(symbol,20)}.md"
            (dst / fname).write_text(content, encoding='utf-8')
            exported.append(symbol)
        stats["arbiter"] = len(exported)
        print(f"[ok] 决策仲裁导出 {len(exported)} 条(拦截+最近200) → {DIR_DECISIONS}/")

        # 聚合总览:按 block_rule 统计
        from collections import Counter
        rule_counter = Counter(r.get("block_rule", "") for r in rows)
        lines = ["| 规则 | 触发次数 |", "|---|---|"]
        for rule, cnt in rule_counter.most_common():
            lines.append(f"| `{rule}` | {cnt} |")
        fm = {"type": "arbiter_overview", "total": len(rows)}
        content = frontmatter(fm) + f"# 🛡️ 决策仲裁总览\n\n共 **{len(rows)}** 条仲裁记录(原始)。\n\n" + "\n".join(lines) + "\n"
        (dst / "00-仲裁规则统计.md").write_text(content, encoding='utf-8')

    # ---- 4b. governor_decisions.jsonl —— 治理器决策(opencode 提案裁决) ----
    src = data_dir / "governor_decisions.jsonl"
    if src.exists():
        rows = []
        with src.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        rows.sort(key=lambda r: _parse_ts(r.get("ts")) or "", reverse=True)
        for i, r in enumerate(rows):
            ts = _parse_ts(r.get("ts"))
            key = r.get("key", "")
            applied = r.get("applied")
            deferred = r.get("deferred_to", "")
            proposal = r.get("opencode_proposal", "")
            candidates = r.get("candidates")
            if isinstance(candidates, str):
                candidates = _parse_extra(candidates) if candidates.startswith('{') else candidates
            cand_str = json.dumps(candidates, ensure_ascii=False, indent=2) if isinstance(candidates, (list, dict)) else str(candidates)

            fm = {
                "type": "governor",
                "param_key": key,
                "applied": str(applied).lower() == "true",
                "deferred_to": deferred,
                "opencode_proposal": str(proposal),
                "ts": ts,
                "date": (ts.split(" ")[0] if ts else ""),
            }
            icon = "✅" if str(applied).lower() == "true" else "⏸️"
            content = frontmatter(fm) + f"# {icon} 治理器: `{key}`\n\n" \
                f"**时间**: {ts}\n\n" \
                f"- 应用: **{applied}**\n" \
                f"- 延迟到: `{deferred}`\n" \
                f"- 关联提案: `{proposal}`" + (f" → 见 `[[03-Hermes进化/L3-提案-{int(proposal):04d}]]`" if str(proposal).isdigit() else "") + "\n\n" \
                "## 候选来源\n```json\n" + _truncate(cand_str, 2000) + "\n```\n"
            fname = f"治理-{(ts or 'nots').replace(':','').replace(' ','-')}-{safe_filename(key,30)}.md"
            (dst / fname).write_text(content, encoding='utf-8')
        stats["governor"] = len(rows)
        print(f"[ok] 治理器决策导出 {len(rows)} 条")

    # ---- 4c. runtime_governor_decisions.jsonl —— 运行时参数裁决 ----
    src = data_dir / "runtime_governor_decisions.jsonl"
    if src.exists():
        rows = []
        with src.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        rows.sort(key=lambda r: _parse_ts(r.get("ts")) or "", reverse=True)
        for r in rows:
            ts = _parse_ts(r.get("ts"))
            key = r.get("key", "")
            value = r.get("value")
            winner = r.get("winner_source", "")
            confidence = r.get("confidence")
            reason = r.get("reason", "")
            action = r.get("action", "")

            fm = {
                "type": "runtime_governor",
                "param_key": key,
                "value": value if value is not None else "",
                "winner_source": winner,
                "confidence": float(confidence) if confidence is not None else 0,
                "reason": reason,
                "action": action,
                "ts": ts,
                "date": (ts.split(" ")[0] if ts else ""),
            }
            content = frontmatter(fm) + f"# ⚙️ 运行时治理: `{key}` = `{value}`\n\n" \
                f"**时间**: {ts}\n\n" \
                f"- 胜出来源: `{winner}`\n" \
                f"- 置信度: `{confidence}`\n" \
                f"- 原因: `{reason}`\n" \
                f"- 动作: **{action}**\n"
            fname = f"运行时治理-{(ts or 'nots').replace(':','').replace(' ','-')}-{safe_filename(key,30)}.md"
            (dst / fname).write_text(content, encoding='utf-8')
        stats["runtime_governor"] = len(rows)
        print(f"[ok] 运行时治理决策导出 {len(rows)} 条")

    return stats


# ---------------------------------------------------------------------------
# 5. Canvas 文件:四层进化图
# ---------------------------------------------------------------------------
def make_canvas(vault: Path):
    """生成 Obsidian Canvas JSON。节点按 L1-L4 横向排列,带颜色。"""
    canvas_dir = ensure_dir(vault / DIR_CANVAS)
    nodes = []
    edges = []

    base_x = -800
    layer_gap = 520
    layers = [
        ("L1", "💎 L1 智慧层", "proposal_wisdom_records + agent_decision_wisdom + param_effect_patterns", "#7aa2f7", f"{DIR_HERMES}/L1-参数效应模式总览.md"),
        ("L2", "📝 L2 Prompt 进化", "prompt_versions + prompt_ab_tests", "#bb9af7", None),
        ("L3", "🏗️ L3 架构进化", "architecture_evolution_proposals (347条)", "#f7768e", None),
        ("L4", "🧬 L4 策略创生", "strategy_genesis_candidates (243条)", "#9ece6a", None),
    ]
    for i, (lid, label, desc, color, link) in enumerate(layers):
        x = base_x + i * layer_gap
        node = {
            "id": lid,
            "type": "file" if link else "text",
            "x": x,
            "y": -200,
            "width": 460,
            "height": 220,
            "color": color.lstrip('#'),
        }
        if link:
            node["file"] = link
        else:
            # 给 L2/L3/L4 用一个指向文件夹的说明文字节点(可点链接)
            folder = {"L2": "L2-Prompt", "L3": "L3-提案", "L4": "L4-创生"}[lid]
            node["text"] = f"# {label}\n\n{desc}\n\n👉 在左侧文件树打开 `{DIR_HERMES}/` 筛选 `{folder}-`\n\n或在 MOC 里看 Dataview 表"
        nodes.append(node)

    # 层间箭头:L1 → L2 → L3 → L4
    for i in range(len(layers) - 1):
        edges.append({
            "id": f"e{i}",
            "fromNode": layers[i][0],
            "toNode": layers[i + 1][0],
            "label": "反馈/驱动",
            "color": "#565f89",
        })

    # 决策流节点(横向在四层下方)
    nodes.append({
        "id": "decisions",
        "type": "text",
        "x": base_x,
        "y": 200,
        "width": 460,
        "height": 180,
        "color": "ff9e64",
        "text": "# 🛡️ Agent 决策流\n\ndecision_arbiter + governor + runtime_governor\n\n👉 在左侧文件树打开 `04-Agent决策/`\n\n或在 MOC 里看「🛡️ Agent 决策仲裁」表",
    })

    # 数据源节点 + 总览 MOC
    nodes.append({
        "id": "moc",
        "type": "file",
        "file": "Agent进化中心.md",
        "x": base_x + 1.5 * layer_gap,
        "y": 200,
        "width": 360,
        "height": 160,
        "color": "e0af68",
    })
    edges.append({"id": "em1", "fromNode": "L1", "toNode": "moc", "label": "汇总"})
    edges.append({"id": "em2", "fromNode": "L4", "toNode": "moc", "label": "产出"})
    edges.append({"id": "em3", "fromNode": "decisions", "toNode": "moc", "label": "反馈"})
    edges.append({"id": "ed1", "fromNode": "decisions", "toNode": "L1", "label": "沉淀为智慧", "color": "ff9e64"})

    canvas = {"nodes": nodes, "edges": edges}
    out = canvas_dir / "Hermes四层进化.canvas"
    out.write_text(json.dumps(canvas, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[ok] Canvas 生成 → {DIR_CANVAS}/Hermes四层进化.canvas")


# ---------------------------------------------------------------------------
# 5. MOC 主页
# ---------------------------------------------------------------------------
def write_moc(vault: Path, stats: dict):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    content = f"""---
type: moc
title: Agent 进化中心
generated_at: {generated_at}
---

# 🧬 Agent 进化中心

> 本 vault 由 `tools/export_to_obsidian.py` 自动生成。数据来自 001Alpha 的离线 Agent 数据(OpenCode 报告 / 交易教训 / Hermes 进化库 / 决策仲裁流)。
> **使用前**: 装 **Dataview** 插件(设置 → 第三方插件 → 浏览 → 搜 Dataview → 安装启用),否则下面的表不显示。
> 数据更新后重跑 `python tools/export_to_obsidian.py` 即可刷新。

**📊 数据规模**: 分析报告 `{stats.get('reports', 0)}` · 交易教训 `{stats.get('lessons', 0)}` · 架构提案 `{stats.get('proposals', 0)}` · 参数智慧 `{stats.get('wisdom', 0)}` · 策略创生 `{stats.get('genesis', 0)}` · Prompt 版本 `{stats.get('prompt_versions', 0)}` · A/B 测试 `{stats.get('ab_tests', 0)}` · 决策仲裁 `{stats.get('arbiter', 0)}` · 治理器决策 `{stats.get('governor', 0)}` · 运行时治理 `{stats.get('runtime_governor', 0)}`

---

## 🗺️ Hermes 四层进化图(Canvas)

👉 打开 **[[_canvas/Hermes四层进化.canvas]]** —— 点节点直接跳详情。

---

## 🔥 高严重度分析报告(major / critical)

```dataview
TABLE severity, domain, finding_count, date
FROM "{DIR_REPORTS}"
WHERE severity = "major" OR severity = "critical"
SORT date DESC
LIMIT 12
```

## 📋 最近分析报告(全部)

```dataview
TABLE severity, domain, finding_count, date
FROM "{DIR_REPORTS}"
SORT date DESC
LIMIT 20
```

---

## 📉 亏损最严重的交易教训(知识库核心)

```dataview
TABLE symbol, side, pnl_pct, strategy, exit_reason, was_correct
FROM "{DIR_LESSONS}"
WHERE pnl_pct < 0
SORT pnl_pct ASC
LIMIT 15
```

> 💡 在任意一条教训里点 `[[币种]]` 或 `[[策略id]]` → 顶部工具栏的 **关系图谱**(Graph view)会织出该实体关联的所有教训。这就是 Obsidian 知识库可视化的精髓。

## ✅ 盈利交易教训(可复用经验)

```dataview
TABLE symbol, side, pnl_pct, strategy
FROM "{DIR_LESSONS}"
WHERE pnl_pct > 0
SORT pnl_pct DESC
LIMIT 15
```

---

## 🏗️ L3 架构进化提案(按状态)

```dataview
TABLE category, feasibility, expected_impact, status
FROM "{DIR_HERMES}"
WHERE type = "arch_proposal"
SORT status ASC, file.name DESC
LIMIT 20
```

## 💎 L1 参数智慧(改进 outcome)

```dataview
TABLE param_key, outcome, pnl_impact, win_rate_delta, confidence
FROM "{DIR_HERMES}"
WHERE type = "wisdom" AND outcome = "improved"
SORT pnl_impact DESC
LIMIT 15
```

## 🧬 L4 策略创生候选(按可行性)

```dataview
TABLE variant_name, paper_status, viability_score, paper_win_rate, paper_trades
FROM "{DIR_HERMES}"
WHERE type = "genesis"
SORT viability_score DESC
LIMIT 15
```

## 📝 L2 Prompt 版本演化

```dataview
TABLE version, change_type, status, proposals_generated, avg_improved_rate
FROM "{DIR_HERMES}"
WHERE type = "prompt_version"
SORT file.name ASC
```

## 🧪 L2 Prompt A/B 测试

```dataview
TABLE version_a, version_b, winner, status, p_value
FROM "{DIR_HERMES}"
WHERE type = "ab_test"
```

## 🧠 L1 Agent 决策智慧

```dataview
TABLE agent_type, symbol, outcome, pnl_pct, pattern_key, confidence
FROM "{DIR_HERMES}"
WHERE type = "agent_wisdom"
SORT file.name DESC
LIMIT 15
```

---

## 🛡️ Agent 决策仲裁(被拦截的决策)

> 这是 **agent 怎么做决策** 的过程流:每一笔意图动作,决策仲裁器会判定放行 / 拦截。点开任意一条看完整上下文。

```dataview
TABLE symbol, reason_intended, block_rule, pnl_pct, confidence, flag
FROM "{DIR_DECISIONS}"
WHERE type = "arbiter" AND would_block = true
SORT ts DESC
LIMIT 20
```

## ✅ 最近决策仲裁(全部,放行+拦截)

```dataview
TABLE symbol, reason_intended, would_block, block_rule, ts
FROM "{DIR_DECISIONS}"
WHERE type = "arbiter"
SORT ts DESC
LIMIT 20
```

## ⏸️ 治理器决策(opencode 提案裁决)

```dataview
TABLE param_key, applied, deferred_to, opencode_proposal, ts
FROM "{DIR_DECISIONS}"
WHERE type = "governor"
SORT ts DESC
```

## ⚙️ 运行时参数治理(实时调参裁决)

```dataview
TABLE param_key, value, winner_source, confidence, action, ts
FROM "{DIR_DECISIONS}"
WHERE type = "runtime_governor"
SORT ts DESC
LIMIT 20
```

---

## 🧭 导航

- [[_canvas/Hermes四层进化.canvas|🗺️ Hermes 四层进化 Canvas]]
- 文件夹: `01-分析报告/` · `02-交易教训/` · `03-Hermes进化/` · `04-Agent决策/`
- 想看关系网?点开任意笔记 → 顶部 **关系图谱** 图标
"""
    (vault / "Agent进化中心.md").write_text(content, encoding='utf-8')
    print(f"[ok] MOC 主页生成 → Agent进化中心.md")


# ---------------------------------------------------------------------------
# 6. .obsidian 配置(预热,启用 dataview 预期)
# ---------------------------------------------------------------------------
def write_obsidian_config(vault: Path):
    """写最小化的 community-plugins.json,提示需要装 Dataview。Obsidian 首次打开会自动建其余配置。"""
    cfg_dir = ensure_dir(vault / ".obsidian")
    # 空列表:用户装好 Dataview 后这里会自动填入
    (cfg_dir / "community-plugins.json").write_text("[]", encoding='utf-8')
    # workspace.json 给个最小占位,避免老版本报错
    (cfg_dir / "workspace.json").write_text(json.dumps({
        "main": {"id": "empty", "type": "empty"},
        "left": {"id": "empty", "type": "empty"},
        "right": {"id": "empty", "type": "empty"},
    }), encoding='utf-8')
    print("[ok] .obsidian 配置占位生成(请在 Obsidian 里手动装 Dataview 插件)")


# ---------------------------------------------------------------------------
# 7. 模板(Templater 用,可选)
# ---------------------------------------------------------------------------
def write_templates(vault: Path):
    lay = ensure_dir(vault / DIR_LAYOUTS)
    (lay / "analysis-template.md").write_text("""---
type: analysis
severity: 
domain: 
date: {{date}}
categories: []
---

# 📊 

> 模板:用 Templater 插件可触发填充。或直接复制改名。

## 发现

- 

## 证据

- 

## 建议动作

- 
""", encoding='utf-8')
    print("[ok] 模板生成 → _layouts/")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="导出 001Alpha 离线 Agent 数据到 Obsidian vault")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="data 目录路径")
    ap.add_argument("--vault", default=str(DEFAULT_VAULT), help="vault 输出目录")
    args = ap.parse_args()

    data_dir = Path(args.data).resolve()
    vault = Path(args.vault).resolve()

    if not data_dir.exists():
        print(f"[error] data 目录不存在: {data_dir}")
        return 1

    print(f"=== 导出 Obsidian Vault ===")
    print(f"数据源: {data_dir}")
    print(f"输出到: {vault}")
    print()

    ensure_dir(vault)
    reports = export_reports(data_dir, vault)
    lessons = export_lessons(data_dir, vault)
    hermes = export_hermes(data_dir, vault)
    decisions = export_decisions(data_dir, vault)

    stats = {**hermes, **decisions, "reports": reports, "lessons": lessons}
    make_canvas(vault)
    write_moc(vault, stats)
    write_templates(vault)
    write_obsidian_config(vault)

    print()
    print(f"=== 完成 ✅ ===")
    print(f"打开 Obsidian → Open folder as vault → 选: {vault}")
    print(f"然后设置 → 第三方插件 → 装 'Dataview' 并启用")
    print(f"打开 'Agent进化中心' 笔记即可看到动态表")
    print(f"打开 '_canvas/Hermes四层进化.canvas' 看进化图")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
