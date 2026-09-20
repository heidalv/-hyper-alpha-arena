# -*- coding: utf-8 -*-
"""轮126：把中线补齐到与长线对称（用户："长线有着几个了，那中线呢"）。

## 审计（`reports/_probe126` 输出）

| 车道 | 现有卡 | 缺 |
|---|---|---|
| 长线 | `midlong_exec`(执行) / `trend_agent`(持仓复核) / `trend_e1_engine`(E1 开仓) = **3 张** | — |
| 中线 | `midlong_executor`(执行) = **1 张** | 主脑、持仓管理、出场 |

中线的真实模块在 `logs/backend.log` 尾 4 万行**都有数据**（不是空壳）：
`[MidLongBrain]` 38 行、`midlong_position_manager` 5 行、`ExitPolicy|exit_policy` 17 行、
`MidLongAudit` 17 行。

## 本次改动

补 3 张中线卡 + 与长线对称的边；**不新增长线卡**（避免再显得重复）。
"""
import io

p = "backend/services/agent_wall.py"
s = io.open(p, encoding="utf-8", errors="surrogateescape", newline="").read()
print("现有节点 id 含 brain_mid:", '"id": "brain_mid"' in s)

# ── 在 midlong_executor 卡之前插入"中线主脑"卡 ──
ANCHOR = '    {"id": "midlong_executor", "group": "G4", "size": "L", "label": "中线执行（tier=mid）",'
NEW_CARDS = '''    # ── [轮126 2026-09-19 车道对称] 用户："长线有着几个了，那中线呢" ──
    # 审计：长线 3 张（执行/持仓复核/E1 开仓），中线只有 1 张（执行）⇒ 两边不对称。
    # 中线的真实模块在日志里都有数据（尾 4 万行：`[MidLongBrain]` 38、`midlong_position_manager` 5、
    # `ExitPolicy` 17、`MidLongAudit` 17），不是空壳，所以按"中线自己那套"补齐：
    #   主脑（论题/候选/开仓决策）→ 执行（tier=mid）→ 持仓管理 → 出场（ExitPolicy）
    {"id": "brain_mid", "group": "G4", "size": "L", "label": "中线主脑（brain_mid · 论题与开仓决策）",
     "role": "中线决策源：`run_midlong_open_sweep` 读论题库 → 候选筛选（含小仓试探）→ 交 Writer；"
             "论题由 `brain_subprocess` 按 tier=mid 产出",
     "cadence_label": "随 tick",
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"\\[MidLongBrain\\]"},
     "deps": ["coin_select"]},
'''
if ANCHOR in s and '"id": "brain_mid"' not in s:
    s = s.replace(ANCHOR, NEW_CARDS + ANCHOR, 1)
    print("[1] 已加『中线主脑』卡")

# ── 在 direction_audit 之前插入"中线持仓管理"与"中线出场" ──
ANCHOR2 = '    {"id": "direction_audit", "group": "G4", "size": "M", "label": "中线决策审计漏斗",'
NEW2 = '''    {"id": "mid_position_mgr", "group": "G4", "size": "M", "label": "中线持仓管理（midlong_position_manager）",
     "role": "中线持仓的读取/合并/减仓与退出触发（`_open_midlong_positions` 等）；"
             "长线对应物是 `trend_agent.review_position`——**两条车道各有自己的持仓层**",
     "cadence_label": "随 tick",
     "source": {"kind": "file", "path": "logs/backend.log",
                "filter": r"midlong_position_manager|MidLongPositionManager"},
     "deps": ["midlong_executor"]},
    {"id": "mid_exit", "group": "G4", "size": "M", "label": "中线出场（ExitPolicy · 与长线参数已分离）",
     "role": "出场唯一权威 `ExitPolicy`（轮100/101 已把中/长线参数拆开：止损来源、复查节奏、"
             "TP 分档各自独立）。长线走 Chandelier + 滚仓，中线走 ExitPolicy 分档止盈——"
             "**这正是"中线与长线最后完全不同"的落点之一**",
     "cadence_label": "事件驱动",
     "source": {"kind": "file", "path": "logs/backend.log", "filter": r"ExitPolicy|exit_policy"},
     "deps": ["mid_position_mgr"]},
'''
if ANCHOR2 in s and '"id": "mid_position_mgr"' not in s:
    s = s.replace(ANCHOR2, NEW2 + ANCHOR2, 1)
    print("[2] 已加『中线持仓管理』『中线出场』两张卡")

# ── 边：中线链 + 与长线对称 ──
EDGE_ANCHOR = '    {"from": "coin_select", "to": "midlong_exec", "kind": "data", "label": "AI 长线候选"},'
NEW_EDGES = EDGE_ANCHOR + '''
    # [轮126 车道对称] 中线链：主脑 → 执行 → 持仓管理 → 出场 → 审计（与长线链同形）
    {"from": "coin_select", "to": "brain_mid", "kind": "data", "label": "中线候选池"},
    {"from": "brain_mid", "to": "midlong_executor", "kind": "trigger", "label": "开仓决策(tier=mid)"},
    {"from": "midlong_executor", "to": "mid_position_mgr", "kind": "data", "label": "持仓管理"},
    {"from": "mid_position_mgr", "to": "mid_exit", "kind": "trigger", "label": "退出触发"},
    {"from": "mid_exit", "to": "direction_audit", "kind": "audit", "label": "出场审计"},
    {"from": "midlong_exec", "to": "direction_audit", "kind": "audit", "label": "开仓审计(long)"},'''
if EDGE_ANCHOR in s and '"to": "mid_position_mgr"' not in s:
    s = s.replace(EDGE_ANCHOR, NEW_EDGES, 1)
    print("[3] 已补 6 条中线链边")

io.open(p, "w", encoding="utf-8", errors="surrogateescape", newline="").write(s)
print("[OK] agent_wall.py 已更新")
