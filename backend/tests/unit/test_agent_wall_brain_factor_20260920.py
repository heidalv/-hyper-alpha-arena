# -*- coding: utf-8 -*-
"""[轮139 2026-09-20] 画布补全：主脑层（G1）+ 因子区（G5）+ `db_table` 源类型。

## 用户指令（两段）
- 「画布现在开始做 **因子这个大模块**」
- 「之前让弄的 **主脑 那6大agent** 怎么还没弄」

## 本轮画布变化
1. **G1 主脑层**（用户 v2 设计 §1A：「主脑层可是有很多 agent，完全忽略了」）新增 8 张卡：
   证据装配(`mlto_context`) / 批次(`mlto_batch`) / OWM(`mlto_owm`) / **牛熊对抗辩论**(`mlto_debate`) /
   **风控官**(`risk_officer`) / **六分析师数值化信号**(`analysts_layer`) /
   **旧分析师体系（主控+6 · 死路径）**(`analyst_legacy_dead`) / K线深度分析师(`kline_analyst`)。
   —— 用户问的"6 大 agent"就是那张**灰卡**：实测 `[Analysts]`/`[MasterController]` 0 命中、
   `caller=MasterController:synthesize` 0 次、`analyst_reports` NULL、入口 `_ai=[]` 封死。
2. **G5 因子区**新增 8 张卡：单因子计算 / 因子装载 / AI 因子发现 / 因子进化(FactorEvo) /
   代码评审(CodegenCritic) / 退役净化(Purge) / **暴露快照** / **三条因子路线**（旧不可达 · AB 已停用只产证据 · Shadow 只决策）。
3. **`db_table` 源类型**（新增并接线）：表驱动模块按**行数 + 最新 ts** 判状态，0 行 = `never`。
   实测：`risk_officer` 81 行·最新 2 分钟前、`analysts_layer` 841 行·最新 9 分钟前、
   `factor_exposure` 710,148 行（analytics 库，非 core —— 首版指错库被本测试抓住）。
4. 前端 `GROUP_META.G5` + 默认布局列表加入 G5。

## 本文件守什么
- 新卡**必须有真实来源**且被实测证明有数据（`db_table` 卡：行数 > 0 或显式 never）；
- 用户点名的三环（六分析师 / 辩论 / 风控官）**必须在画布上且互相有边**；
- 那张"6 大 agent"卡必须是 `dead` 且带证据（不许当活卡画）；
- 因子区的三条路线必须写清"只有 AB 曾能开仓、现已停用 ⇒ 只产证据"。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import agent_wall as W  # noqa: E402

_BY_ID = {n["id"]: n for n in W.NODES}


def test_brain_layer_cards_exist():
    for nid in ("mlto_context", "mlto_batch", "mlto_owm", "mlto_debate",
                "risk_officer", "analysts_layer", "analyst_legacy_dead", "kline_analyst"):
        assert nid in _BY_ID, f"主脑层缺卡：{nid}"
        assert _BY_ID[nid]["group"] == "G1", f"{nid} 应在 G1"


def test_user_named_six_analysts_card_is_dead_with_evidence():
    n = _BY_ID["analyst_legacy_dead"]
    assert n.get("status_hint") == "static_dead", "「主控+6 分析师」实测是死路径，必须画成灰卡"
    src = n.get("source") or {}
    assert src.get("kind") == "none" and src.get("reason"), "死卡必须给出依据（证据）"
    role = n.get("role") or ""
    for kw in ("MasterController", "死路径", "KlineAnalyst"):
        assert kw in role, f"死卡说明应写清 {kw}（并指出唯一仍在跑的 KlineAnalyst）"


def test_three_architecture_rings_are_wired_with_edges():
    """六分析师 → 辩论 → 风控官 → 交易员：这条链必须在画布上有边。"""
    edges = {(e["from"], e["to"]) for e in W.EDGES}
    assert ("analysts_layer", "mlto_context") in edges, "六域信号未接进主脑上下文"
    assert ("mlto_context", "mlto_debate") in edges, "辩论未接证据装配"
    assert ("mlto_debate", "risk_officer") in edges, "风控官未接辩论姿态"
    assert ("risk_officer", "midlong_executor") in edges, "风控官未接执行（否决权无从落地）"


def test_factor_area_cards_exist_and_are_honest():
    for nid in ("factor_calc", "factor_loader", "factor_ai_discovery", "factor_evolution",
                "factor_codegen_critic", "factor_purge", "factor_exposure", "factor_routes"):
        assert nid in _BY_ID, f"因子区缺卡：{nid}"
        assert _BY_ID[nid]["group"] == "G5", f"{nid} 应在 G5"
    routes = _BY_ID["factor_routes"]["role"]
    assert "FactorRouteAB" in routes and "只产证据" in routes, \
        "三条路线必须写清：只有 AB 曾能开仓、2026-09-19 起已停用 ⇒ 只产证据（禁止再画成开仓边）"


def test_db_table_cards_report_real_numbers():
    """`db_table` 卡必须能报真实行数/新鲜度（而不是拿共享日志 mtime 冒充）。"""
    st = W.build_state()
    by_id = {n["id"]: n for n in st["nodes"]}
    for nid in ("risk_officer", "analysts_layer", "factor_exposure"):
        n = by_id[nid]
        det = n.get("status_detail") or {}
        assert n["source"]["kind"] == "db_table"
        if n["status"] == "unknown":
            raise AssertionError(f"{nid} 表读取失败：{det.get('reason')}")
        assert int(det.get("rows") or 0) > 0, f"{nid} 应报出真实行数（实测有数据）"


def test_g5_group_counts_in_state():
    st = W.build_state()
    g5 = [n for n in st["nodes"] if n["group"] == "G5"]
    assert len(g5) == 8, f"G5 因子区应有 8 张卡，实测 {len(g5)}"
    groups = {g["group"] for g in st["groups"]}
    assert "G5" in groups, "state 的分组摘要必须含 G5"
