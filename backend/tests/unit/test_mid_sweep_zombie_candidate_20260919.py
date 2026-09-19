# -*- coding: utf-8 -*-
"""轮118 中线"全冻结"现场 —— 三个结论 + 修了什么（2026-09-19）。

## 用户反馈

> 「中线又冻结，并且还是全不全冻结。设计理念一直是除非大行情极端反转才全冻结；
>   单币亏钱，就是冻结单个亏钱的币。这回怎么又给我全冻结了」

## 现场（`reports/_probe118*.txt`）

**① 没有任何"全局闸"在拦** —— 逐项实测（23:23）：

| 全局机制 | 读数 |
|---|---|
| `tier_circuit_breaker`（整层熔断） | short/mid/long 全部 `blocked=False` |
| `risk_constitution` 日亏硬停 | 日亏 **0.87%** ≪ 6% 阈值 |
| 全账户总保证金 | **10.2%** ≪ 60% |
| `freeze_coordinator` / `portfolio_budget` | `active_freeze_count=0`、`global_frozen=false` |
| `midlong_circuit_gate` 单币 ban | **无任何币在禁开期** |
| `proposal_block_streaks` 冷却 | 无（仅 ZEC 有一条 30 分钟窗） |

⇒ 「全冻结」不是全局闸造成的。真实构成是两条：

**② 唯一候选是"僵尸候选"。** 22:10–23:21 连续 **24 轮**日志都是
`开仓扫描 tier=mid 候选=1 成交=0`；那唯一候选是 **ZEC** —— 它
`recommend_open=1` 但**解析不到 mid 独立策略**（不在会话标的内、无策略），
于是每 3 分钟进一次执行层 → `strategy_detached` →（轮115 棘轮修复后）5 次同因
→ 装配 30 分钟冷却 → 再试。**唯一的候选位被它占满，审计里堆的也全是它的噪音**
（近 6 小时 mid 拒绝事件里 ZEC 占 5/10）。这就是"看起来整条车道冻住"的主因。

**③ 其余 5 个"已接受"的论题自己写着 `recommend_open=0`。**
BTC/ASTER/SOL/ETH/LINK 的 mid 论题 `accepted=1` 但 `recommend_open=0`
（主脑 LLM 判断"现在别开"），刷新时间 20:02–22:46，`expires_at` 到明晨仍在有效期。
**这是主脑的决策，不是闸门** —— 但旧的日志只说"候选=N 成交=0"，两者混在一起
看不出来，所以用户只能理解为"被冻住了"。

**④ 你设计的"单币亏钱只冻那个币"目前是被关掉的**（不是被违反）：
`record_midlong_outcome()` 里写着

```
[2026-09-11 用户指令] 模拟账户直接返回：不做任何亏损触发的冷却/熔断记账
```

⇒ `data/midlong_circuit_state.json` 的 mtime 停在 **09-10 20:48**（9 天没写），
今天 UNI 连吃 **3 笔 SL（−13.43）**也没有得到"单币 12h 冷却"。
**这一条需要你的口径**：是否恢复 paper 的"单币连亏冻结"（只冻该币、不动全局）。
恢复=改一行（去掉提前返回）；保持=维持现状。我**没擅自改**。

## 本轮修了什么

`brain.run_midlong_open_sweep`（唯一的中线开仓扫描入口）：

1. **僵尸候选不再进执行层**：解析不到独立策略的候选直接跳过（限流 30 分钟记一次
   INFO + 审计 `sweep_skip:no_strategy`），不再制造 `strategy_detached` 循环与冷却。
2. **扫描日志带上"为什么没进候选"**：
   `候选=1 成交=0｜未进候选: no_thesis=6, not_recommended=5, has_position=1, no_strategy=1`
   —— 以后"整条车道不动"能一眼分清是**闸门拦的**、**主脑说别开**、还是**没论题**。
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════════
# ① 僵尸候选不再进执行层 + 日志能区分"谁拦的"
# ══════════════════════════════════════════════════════════════════════

def test_sweep_skips_candidates_without_strategy():
    src = _src("backend/services/mlto/brain.py")
    i = src.index("def run_midlong_open_sweep(")
    seg = src[i:i + 6000]
    assert "resolve_independent_strategy" in seg, "必须在扫描入口解析独立策略"
    assert "sweep_skip:no_strategy" in seg, "跳过必须留审计（否则又是一条无信息事件）"
    assert "_bump(\"no_strategy\")" in seg


def test_sweep_log_distinguishes_reasons():
    src = _src("backend/services/mlto/brain.py")
    i = src.index("def run_midlong_open_sweep(")
    seg = src[i:i + 6000]
    for k in ("no_thesis", "not_recommended", "stale", "has_position", "no_strategy"):
        assert f'"{k}"' in seg, f"扫描统计缺少分类 {k}"
    assert "未进候选" in seg, "日志必须把分类打出来"


def test_sweep_behavior_with_stub_host(monkeypatch):
    """行为验证：无策略的符号被跳过（不调用 maybe_open），有策略的才进候选。"""
    from backend.services.mlto import brain as B

    calls = []

    class _Dto:
        accepted = True
        recommend_open = True
        direction = "long"
        tranche_stage = 0
        llm_conviction = 50

    class _Store:
        @staticmethod
        def get(sid, sym, tier):
            return _Dto()

    monkeypatch.setattr(B, "thesis_is_fresh", lambda dto: True)
    monkeypatch.setattr(B, "maybe_open", lambda **k: calls.append(k["symbol"]) or True)
    monkeypatch.setitem(sys.modules, "backend.services.mlto.thesis_store", _Store)
    monkeypatch.setattr(
        "backend.services.full_auto.midlong_position_manager.has_open_position_of_nature",
        lambda db, acct, sym, tier: False)

    class _Host:
        @staticmethod
        def resolve_independent_strategy(db, session, sym, tier):
            return None if sym == "ZEC" else object()

    class _Session:
        session_id = "fa_test"
        paper_account_id = 14

    out = B.run_midlong_open_sweep(
        host=_Host(), session=_Session(), symbols=["ZEC", "ASTER"], tier="mid")

    assert calls == ["ASTER"], f"ZEC（无策略）不应进执行层，实际 {calls}"
    assert [r["symbol"] for r in out] == ["ASTER"]


def test_no_global_gate_change_and_evidence_recorded():
    """本轮**没有**动任何全局闸；且把"单币冻结被 09-11 指令关掉"的证据留在测试里。"""
    src = _src("backend/services/full_auto/midlong_circuit_gate.py")
    assert "2026-09-11 用户指令" in src, "该指令的留痕必须在（它解释了为什么状态文件 9 天没写）"
    assert "loss_locks_disabled" in src
    import json
    st = json.loads(io.open(os.path.join(_ROOT, "data/midlong_circuit_state.json"),
                            encoding="utf-8").read())
    assert isinstance(st, dict)
