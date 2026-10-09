# -*- coding: utf-8 -*-
"""[2026-10-03] L4 独立孵化通道护栏（用户指令「继续」= 补齐最后一段断链）。

## 实证根因
· `strategy_genesis_candidates` 345 条 `paper_status='incubating'`，但全库只有 **12 个 `gen_*`
  策略**存在，且 `account_id` **全是 14（用户模拟账户）** ⇒ 其余策略已归档、
  `_get_paper_performance()` 永远 0 笔（`paper_trades` 全 0）。
· 即便策略存在，`check_incubation_results()` 只能**唤醒**策略，真正执行它们的是 FullAuto 会话；
  用户会话停止 ⇒ 永远攒不到 `MIN_PAPER_TRADES=30` 笔 ⇒ `validated`/`promoted` 恒 0（成熟度 L4=0）。
· 附带缺陷：孵化交易此前落在**用户账户**上，污染用户绩效账。

## 修复（本文件钉住）
`backend/services/hermes_incubation_channel.py`：专用孵化账户 + 策略重建/改绑 + 专用会话 + 状态/阻塞原因；
接口 `GET /api/hermes/genesis/incubation`、`POST /api/hermes/genesis/incubation/{repair,start,stop}`；
前端 Hermes 面板新增「L4 孵化通道」卡片。

## 实测验证（本机）
· `repair_candidate_strategies(12)` → 孵化账户 **#255 Hermes 孵化器**（钱包 1000）、
  **12 条策略改绑**（rebound=12）、账户 14 仅剩 1 条旧 `gen_` 策略、**账户 14 无运行中会话**；
· `start_channel` → 会话 `fa_7e78577621` **status=running**、12 条 active 策略；
· `stop_channel` → 会话 **stopped**（验证后即停，不留后患）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_CH = (ROOT / "backend/services/hermes_incubation_channel.py").read_text(encoding="utf-8")
SRC_ROUTES = (ROOT / "backend/api/hermes_routes.py").read_text(encoding="utf-8")
SRC_PANEL = (ROOT / "frontend-next/src/components/learning/HermesLevelsPanel.tsx").read_text(encoding="utf-8")


def test_channel_module_has_all_four_capabilities():
    for fn in ("ensure_incubator_account", "repair_candidate_strategies",
               "channel_status", "start_channel", "stop_channel"):
        assert f"def {fn}(" in SRC_CH, f"孵化通道缺能力: {fn}"
    # 专用账户：不再复用用户账户
    assert "INCUBATOR_ACCOUNT_NAME" in SRC_CH and "Hermes 孵化器" in SRC_CH
    assert "row.account_id = incubator_id" in SRC_CH, "必须把候选策略改绑到孵化账户"


def test_start_is_gated_by_default_off():
    """关键安全约束：默认不允许自动开始纸面交易，必须显式开启。"""
    assert 'HERMES_L4_INCUBATION_ENABLED", "false"' in SRC_CH
    seg = SRC_CH.split("def start_channel(")[1]
    assert seg.index("if not _enabled():") < seg.index("repair_candidate_strategies(db, limit)"), \
        "开关检查必须在任何动作之前"


def test_status_reports_honest_blocking_reasons():
    seg = SRC_CH.split("def channel_status(")[1].split("def start_channel(")[0]
    for reason in ("总开关未开", "孵化账户不存在", "还没有候选策略", "候选策略都不在 active",
                   "没有运行中的孵化会话"):
        assert reason in seg, f"缺阻塞原因: {reason}"
    assert "min_paper_trades" in seg and "blocking" in seg


def test_routes_and_panel_wired():
    for ep in ('@router.get("/genesis/incubation")',
               '@router.post("/genesis/incubation/repair")',
               '@router.post("/genesis/incubation/start")',
               '@router.post("/genesis/incubation/stop")'):
        assert ep in SRC_ROUTES, f"缺接口: {ep}"
    assert "genesis/incubation" in SRC_PANEL
    for label in ("修复/改绑策略", "启动孵化会话", "停止孵化会话", "L4 孵化通道"):
        assert label in SRC_PANEL, f"面板缺: {label}"
    assert "不占用你的账户" in SRC_PANEL, "必须说清孵化交易不污染用户账户"
