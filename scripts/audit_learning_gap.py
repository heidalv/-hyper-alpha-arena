# -*- coding: utf-8 -*-
"""[F335 2026-09-18] 学习↔策略"割裂"缺口体检（只读、可反复跑）。

背景（第 5 路复查结论）：真正在交易的 mid/long 车道是 MLTO 脑，而 MLTO 只读它自己那套
学习（`reflexion_memory`/`consolidated_lessons`/`similar_episodes`/`backtest_wisdom`），
对 **v7 教训池 / 因子权重 / 衰减退役 / RAG** 的读取命中为 0 ⇒ 两套学习系统各自闭环。

本脚本把该结论变成**可复跑的读数**：分别列出四类"学习产物"的实际存量与新鲜度，
再对 `backend/services/mlto/` 做源码级读取点扫描（打印命中数，期望 0）。
只读；不写库、不改文件、不重启。
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

V7_DB = ROOT / "backend" / "data" / "factor_evolution_memory_v7.db"
V7_DB_ALT = ROOT / "data" / "factor_evolution_memory_v7.db"
DECAY = ROOT / "data" / "factor_decay_status.json"
WEIGHTS = ROOT / "data" / "factor_runtime_weights.json"
MLTO = ROOT / "backend" / "services" / "mlto"

READ_KEYS = ["v7_lessons", "factor_weights", "factor_decay", "rag_knowledge_service",
             "signal_feedback", "runtime_factor_weights", "learning_readback"]


def sec(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def v7_stats() -> None:
    sec("① v7 教训池（sqlite）—— 写入方：evolution_memory_v7 / agent5_feedback / weekly_loop")
    p = V7_DB if V7_DB.exists() else (V7_DB_ALT if V7_DB_ALT.exists() else None)
    if not p:
        print("  未找到 v7 库（尝试过 backend/data 与 data/）")
        return
    print(f"  文件: {p}  ({p.stat().st_size/1024:.0f} KB, mtime="
          f"{__import__('datetime').datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M})")
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print(f"  表: {tables}")
        for t in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                n = cur.fetchone()[0]
                print(f"    {t}: {n} 行")
                cols = [d[1] for d in cur.execute(f"PRAGMA table_info({t})").fetchall()]
                if "kind" in cols:
                    cur.execute(f"SELECT kind, COUNT(*) FROM {t} GROUP BY kind ORDER BY 2 DESC")
                    print(f"      kind 分布: {cur.fetchall()}")
                if "use_count" in cols:
                    cur.execute(f"SELECT COUNT(*) FROM {t} WHERE COALESCE(use_count,0)=0")
                    print(f"      use_count=0 的行: {cur.fetchone()[0]}（从未被检索）")
            except Exception as e:
                print(f"    {t}: 读取失败 {str(e)[:70]}")
        con.close()
    except Exception as e:
        print("  读取失败:", str(e)[:120])


def decay_stats() -> None:
    sec("② 因子衰减/退役状态（json）—— 消费点：仅短线车道（已停用）")
    if not DECAY.exists():
        print("  未找到", DECAY)
        return
    d = json.loads(DECAY.read_text(encoding="utf-8"))
    items = d.get("factors") if isinstance(d, dict) and "factors" in d else d
    if isinstance(items, dict):
        recs = {}
        for v in items.values():
            r = (v or {}).get("recommendation") if isinstance(v, dict) else None
            recs[r] = recs.get(r, 0) + 1
        print(f"  文件 mtime={__import__('datetime').datetime.fromtimestamp(DECAY.stat().st_mtime):%Y-%m-%d %H:%M}"
              f"  条目={len(items)}")
        print(f"  recommendation 分布: {recs}")


def weights_stats() -> None:
    sec("③ 运行时因子权重（json）—— 消费点：4 条信号管线")
    if not WEIGHTS.exists():
        print("  未找到", WEIGHTS)
        return
    d = json.loads(WEIGHTS.read_text(encoding="utf-8"))
    w = d.get("weights") if isinstance(d, dict) and "weights" in d else d
    zero = sum(1 for v in w.values() if isinstance(v, (int, float)) and v == 0)
    print(f"  updated_at={d.get('updated_at')}  条目={len(w)}  零权重={zero}")


def mlto_reads() -> None:
    sec("④ MLTO 脑对上述产物的读取点扫描")
    print("  期望：`learning_readback` ≥1（F345 接线的唯一入口）；"
          "其余关键词仍应为 0（MLTO 自有学习不经这些模块）。")
    if not MLTO.exists():
        print("  未找到", MLTO)
        return
    src = []
    for f in MLTO.rglob("*.py"):
        try:
            src.append((f, f.read_text(encoding="utf-8", errors="replace")))
        except Exception:
            continue
    print(f"  扫描 {len(src)} 个文件")
    for k in READ_KEYS:
        hits = [(f.name, i + 1) for f, t in src
                for i, ln in enumerate(t.splitlines()) if k in ln]
        print(f"    {k:<26} 命中 {len(hits)} 处  {hits[:3]}")


def lane_matrix() -> None:
    """[F337] 学习产物 × 决策车道 的**读取点矩阵**（把"写必有读"落成可复跑的读数）。

    这是报告 §6.3 判据 A5 的基线：每个"学习产物"应当至少被**某条决策车道**读取；
    矩阵为空格的组合即为"只写不读"。只做**源码级**读取点扫描（关键词 + 上下文），
    不做运行验证——运行验证见 §5/§15 与 `docs/复查报告_因子×LLM架构_20260917.md`。
    """
    sec("⑤ 学习产物 × 决策车道 读取点矩阵（源码级；空格 = 无读取点）")
    lanes = {
        "master(主控LLM)": ["backend/services/trading_analysts.py",
                            "backend/services/ai_decision_service.py",
                            "backend/services/prompt_context"],
        "MLTO(实际下单)": ["backend/services/mlto"],
        "scalp(短线·已停用)": ["backend/services/scalp_loop.py",
                               "backend/services/factor_engine/scalp_factor_router.py"],
        "midlong(中线)": ["backend/services/full_auto/midlong_helpers.py",
                          "backend/services/full_auto/mlto_cycle.py",
                          "backend/services/factor_engine/midlong_active_factor_set.py"],
        "learning(学习侧自读)": ["backend/services/strategy_learning_service.py",
                                 "backend/services/learning_loop_service.py",
                                 "backend/services/unified_strategy"],
    }
    keys = {
        # [F345] 读回路统一后，各车道不再各写一份读取口径，而是经
        # `learning_readback.decision_block` 这一唯一入口读取。关键词矩阵必须
        # 认得这个入口，否则会给出"MLTO 仍无读取点"的**假阴性**
        # （本次复核实测：接线上线后矩阵仍显示 `·`）。
        "v7教训池": ["evolution_memory_v7", "v7_lessons", "learning_readback"],
        "因子权重(DB)": ["factor_weights"],
        "因子权重(运行时文件)": ["load_runtime_factor_weights", "factor_runtime_weights",
                                 "learning_readback"],
        "因子衰减/退役": ["get_factor_weight_penalty", "factor_decay_status",
                          "factor_decay_monitor", "learning_readback"],
        "RAG知识/经验": ["rag_knowledge_service", "experience_retriever"],
        "逐单归因": ["signal_feedback_tracker", "signal_trade_feedback"],
    }
    cache: dict = {}

    def files_of(paths):
        out = []
        for p in paths:
            fp = ROOT / p
            if fp.is_file():
                out.append(fp)
            elif fp.is_dir():
                out.extend(fp.rglob("*.py"))
        return out

    hdr = "  " + "产物".ljust(24) + "".join(k.ljust(16) for k in lanes)
    print(hdr)
    for kname, pats in keys.items():
        row = "  " + kname.ljust(24)
        for lname, paths in lanes.items():
            n = 0
            for f in files_of(paths):
                txt = cache.get(f)
                if txt is None:
                    try:
                        txt = f.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        txt = ""
                    cache[f] = txt
                if any(p in txt for p in pats):
                    n += 1
            row += (str(n) if n else "·").ljust(16)
        print(row)
    print("  说明：数字=该车道内**含关键词的文件数**（粗略下界）；`·` = 无读取点。")


def readback_stats() -> None:
    """[F345] 读回路留痕读数：把「只写不读」从修辞变成可复跑的对比。

    合入读回路前的实测基线（2026-09-18）：
      active=810，其中 use_count>0 的仅 **59**（7.3%），且全部来自 **codegen** 路径
      （因子挖掘 prompt）；交易侧（master/mlto）读取**从不计次** ⇒ 既看不见，
      又会被 `maintenance()` 按 `use_count=0 AND 30 天` 退役（读了照样被清）。
    """
    sec("⑥ 读回路留痕（F345；交易侧读取是否可测量）")
    try:
        sys.path.insert(0, str(ROOT))
        from backend.services.learning_readback import stats as _st
        s = _st()
        if s.get("error"):
            print("  读取失败:", s["error"])
            return
        a, u = int(s.get("active") or 0), int(s.get("used_active") or 0)
        print(f"  active={a}  被读过={u}（{u / a:.1%}）  mode={s.get('mode')}")
        print("  按 kind（n / 被读过）:")
        for row in s.get("by_kind") or []:
            print(f"    {row['kind']:<16}{row['n']:>5} / {row['used']}")
        lanes = s.get("reads_by_lane") or {}
        if lanes:
            print("  按车道留痕（query 前缀）:")
            for k, v in lanes.items():
                print(f"    {k:<28}{v}")
        else:
            print("  按车道留痕: 无 —— 交易侧读取尚未发生（读回路默认关，"
                  "`LEARNING_READBACK_ENABLED=1` 启用后此处应出现 [master]/[mlto] 行）")
        print(f"  最近一次检索: {s.get('last_read_at')}")
    except Exception as exc:
        print("  读回路读数失败:", str(exc)[:200])


def main() -> int:
    print("学习↔策略 割裂缺口体检（只读）")
    v7_stats()
    decay_stats()
    weights_stats()
    mlto_reads()
    lane_matrix()
    readback_stats()
    print("\n提示：修复方案见 docs/复查报告_因子×LLM架构_20260917.md §15（最小接线 5 条）；"
          "读回路落地见 backend/services/learning_readback.py（F345）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
