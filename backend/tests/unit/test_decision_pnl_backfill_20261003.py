# -*- coding: utf-8 -*-
"""[2026-10-03] 决策↔成交回填修复护栏（用户指令「继续」的最后一环）。

## 实测量化
`decision_snapshots`：executed **229** 条，回填 pnl 的只有 **17 条（7.4%）**，212 条待回填。
排除法定位根因：
· **不是持有时长**：近 90 天 403 笔已平仓里 0 笔 >48h（243 笔 <2h、160 笔 2-48h）；
· ①**主库 RLS 未注入** ⇒ 回填脚本只看到 115/403 笔持仓（修好后 403）；
· ②**匹配窗口是 `now-48h` + 候选歧义即跳过**（宁缺勿错）⇒ 引擎侧改为**以 opened_at 为中心 ±30min**；
· ③部分流程快照写于成交后数小时（实测 ETH 差 173min）⇒ 回填用**两段窗口 ±30min → ±6h**（均要求唯一）。
· ④**持仓被「完整重置」删除**的无法回填（40 条抽样里 22 条最近同名持仓在 7 天外）——盈亏已不存在，不猜。

## 结果
· `pnl` 回填：**17 → 47 / 229（7.4% → 20.5%）**，`ambiguous = 0`（无错配）；
· 新暴露数据缺陷：47 条里 **30 条 `pnl_pct = pnl/margin` 畸变**（保证金极小，最高上千）
  ⇒ 金额照写、**比例置空**（`PNL_PCT_MAX_ABS=20`），避免污染 RL 奖励与质量标签；
· 回放缓冲：清掉 31 条畸变 live 样本后重灌 → `live 34 / synthetic 300`，`avg_reward 7.26 → 0.0024`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_ENGINE = (ROOT / "backend/services/paper_trading_engine.py").read_text(encoding="utf-8")
SRC_BF = (ROOT / "backend/services/decision_pnl_backfill.py").read_text(encoding="utf-8")
SRC_SEED = (ROOT / "backend/services/learning_core/replay_seed.py").read_text(encoding="utf-8")
SRC_ROUTES = (ROOT / "backend/api/learning_core_routes.py").read_text(encoding="utf-8")


def test_engine_matches_by_open_window_not_now_minus_48h():
    assert "def _snap_time_filter(" in SRC_ENGINE, "引擎需用开仓窗口过滤候选"
    seg = SRC_ENGINE.split("def _snap_time_filter(")[1][:400]
    assert "_snap_lo" in seg and "_snap_hi" in seg
    assert "DecisionSnapshot.timestamp >= _snap_lo" in seg
    # 两条策略都必须走该过滤器（不得再有裸 `timestamp >= _cutoff` 的旧写法）
    assert SRC_ENGINE.count("_snap_time_filter(_q") >= 2
    assert "DecisionSnapshot.timestamp >= _cutoff," not in SRC_ENGINE


def test_backfill_injects_rls_and_uses_two_pass_window():
    # RLS：不注入 system identity 时 paper_positions 只返回部分行（实测 115/403）
    assert "set_system_identity()" in SRC_BF
    assert "WIDE_WINDOW_MIN" in SRC_BF and "for _win in (WINDOW_MIN, WIDE_WINDOW_MIN)" in SRC_BF
    assert "if len(matches) != 1:" in SRC_BF, "两段窗口都必须要求唯一匹配（宁缺勿错）"


def test_backfill_keeps_amount_but_drops_implausible_pct():
    assert 'os.getenv("PNL_PCT_MAX_ABS", "20")' in SRC_BF
    assert 'stats["implausible_pct"]' in SRC_BF
    assert "snap.pnl_pct = round(pnl_pct, 6) if pnl_pct is not None else None" in SRC_BF
    assert "snap.pnl = pnl" in SRC_BF, "真实金额必须保留"


def test_replay_seed_reward_guard_and_stronger_dedup():
    assert 'RL_MAX_ABS_REWARD", "20"' in SRC_SEED
    assert "implausible += 1" in SRC_SEED
    # 去重键必须含决策时间戳（否则同币同向同 reward 会被误判重复：实测 47 条只进 1 条）
    assert "snap_ts" in SRC_SEED
    assert "json_extract(state, '$.snap_ts')" in SRC_SEED


def test_backfill_prevents_many_to_one_leak_and_test_sources():
    """[2026-10-03 严重修复] 实测：同一个测试克隆仓被 ±6h 宽窗口**同时匹配给 30 条快照**
    （pnl 4522.944853309662 重复 30 次）⇒ 必须"一个持仓只记一次"。
    同时排除测试/孵化账户与价格错位行（entry 118.97 → close 2673.38 的克隆行）。"""
    assert "_used_pos_ids" in SRC_BF and "position_reused" in SRC_BF
    assert 'NOT LIKE \'\\_audit%\'' in SRC_BF or "NOT LIKE" in SRC_BF
    assert "0.5 <= (float(p[\"close_price\"]) / float(p[\"entry_price\"])) <= 2.0" in SRC_BF
    assert "Hermes 孵化器" in SRC_BF


def test_routes_expose_backfill():
    assert '@router.post("/backfill-decision-pnl")' in SRC_ROUTES
    assert "backfill_decision_pnl(days=days, limit=limit, dry_run=dry_run)" in SRC_ROUTES
