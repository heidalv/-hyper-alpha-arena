"""scalp_lane 的 TP/SL 必须原样送达 RR 门控，不得被"AI 占位符"启发式替换。

[2026-09-02 P2.2 联动修复] 背景
--------------------------------
decision_core.pipeline.evaluate_open_decision 里有一条启发式
`_looks_like_ai_placeholder_tp_sl`，用来把 Master LLM 输出的 2%/1% 占位 TP/SL
换成 tier 默认值再做门控。判据是 "TP<=3% 且 SL<=1.5%" 或 "RR<=2.05"——几乎命中
所有短线单。ScalpExecutionGate 算好的 0.9%/1.8%（RR 2.0）被换成 short tier 默认
1.0%/1.5%（RR 1.5）：

- P2.2 之前 min_rr=1.3 → 1.5 恰好过门 → RR 门两周来检查的是常数 1.5 而非
  订单真实 RR（RR<1.3 的单照放，实现盈亏比仅 1.042）；
- P2.2 把 min_rr 抬到 2.0 → 1.5 过不了 → 短线 100% 被 TCP/V5 拦。

实际执行一直用的是 ScalpGate 算价（近 14 天 795 笔有 204 种 TP/SL 组合、恰为
默认值的 0 笔），故只有门控在看错数。修复：scalp_lane 与 ranging_mr 同款豁免。

测试手法：在 evaluate_entry 处用自定义异常截获门控实际收到的 tp/sl，避免 mock
整条下游管线（DB / 账户 / 风控）。
"""
from __future__ import annotations

import pytest


class _Captured(Exception):
    def __init__(self, tp_pct, sl_pct):
        super().__init__("captured")
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct


@pytest.fixture
def _capture_entry(monkeypatch):
    """放行前置门（数据契约 / DCP），在 evaluate_entry 处截获 tp/sl。"""
    import backend.services.decision_core.data_contract as dc
    import backend.services.decision_core.direction_coherence as dcoh
    import backend.services.decision_core.unified_gate as ug

    monkeypatch.setattr(dc, "apply_data_contract_gate", lambda *a, **k: (True, ""))

    class _DCP:
        allowed = True
        penalty = 0
        rule = ""
        reason = ""

    monkeypatch.setattr(dcoh, "evaluate_direction_coherence", lambda *a, **k: _DCP())

    def _fake_entry(**kw):
        raise _Captured(kw.get("tp_pct"), kw.get("sl_pct"))

    monkeypatch.setattr(ug, "evaluate_entry", _fake_entry)
    # 占位符判定不依赖 env，但 tier 默认值可能读 runtime 配置；这里不关心具体值，
    # 只关心"是否被改写"。
    yield


def _dec(**extra):
    base = {
        "action": "buy",
        "operation": "buy",
        "symbol": "BNB",
        "confidence": 69,
        "confidence_pct": 69.0,
        "timeframe_tier": "short",
        "tier": "short",
        "trade_nature": "scalp",
        # ScalpGate 典型算价：range_hard_tp，SL 0.9% / TP 1.8%（RR 2.0）
        "stop_loss_pct": 0.009,
        "take_profit_pct": 0.018,
        "_agent_independent": True,
    }
    base.update(extra)
    return base


def _run(dec):
    from backend.services.decision_core.pipeline import evaluate_open_decision

    with pytest.raises(_Captured) as ei:
        evaluate_open_decision(
            db=None, account_id=1, symbol="BNB", dec=dec,
            market_data={"price": 100.0, "atr": 0.8}, mode="paper",
        )
    return ei.value.tp_pct, ei.value.sl_pct


def test_scalp_lane_tpsl_reaches_gate_unchanged(_capture_entry):
    """scalp_lane：0.9%/1.8% 必须原样到达 RR 门控。"""
    tp, sl = _run(_dec(_source_lane="scalp_lane"))
    assert tp == pytest.approx(0.018), f"scalp_lane TP 被改写为 {tp}"
    assert sl == pytest.approx(0.009), f"scalp_lane SL 被改写为 {sl}"
    assert tp / sl == pytest.approx(2.0), "RR 应保持 ScalpGate 给出的 2.0"


def test_non_scalp_lane_placeholder_still_replaced(_capture_entry):
    """对照组：同样数值但不是 scalp_lane（如 Master LLM 提案）→ 仍按占位符替换。

    这条锁住"修复没有把启发式整体关掉"。替换后的值来自 tier 默认，具体数值随
    runtime 配置变化，这里只断言"确实发生了替换"（与原值不同）。
    """
    from backend.services.decision_core.pipeline import _looks_like_ai_placeholder_tp_sl

    assert _looks_like_ai_placeholder_tp_sl(0.018, 0.009), "前提：该数值对应占位符判据为真"
    tp, sl = _run(_dec())  # 无 _source_lane
    assert not (tp == pytest.approx(0.018) and sl == pytest.approx(0.009)), (
        "非 scalp_lane 的占位符 TP/SL 应被 tier 默认替换，但原值被原样放行"
    )


def test_ranging_mr_exemption_unchanged(_capture_entry):
    """回归保护：原有 ranging_mr 豁免不受影响。"""
    from backend.services.decision_core.pipeline import evaluate_open_decision

    with pytest.raises(_Captured) as ei:
        evaluate_open_decision(
            db=None, account_id=1, symbol="BNB",
            dec=_dec(stop_loss_pct=0.006, take_profit_pct=0.008),
            market_data={"price": 100.0, "ranging_mr": True}, mode="paper",
        )
    assert ei.value.tp_pct == pytest.approx(0.008)
    assert ei.value.sl_pct == pytest.approx(0.006)
