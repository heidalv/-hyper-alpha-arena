"""置信度归一化回归测试 — 防止 1% 被误判为 100%。"""
import pytest

from backend.services.decision_core.threshold_resolver import normalize_confidence_pct
from backend.services.decision_core.proposal import TradeProposal


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, 1.0),
        (0.01, 1.0),
        (45, 45.0),
        (0.45, 45.0),
        (100, 100.0),
        (1.0, 1.0),
        (0, 0.0),
        (-5, 0.0),
        (150, 100.0),
    ],
)
def test_normalize_confidence_pct(raw, expected):
    assert normalize_confidence_pct(raw) == pytest.approx(expected)


def test_trade_proposal_low_confidence_not_inflated():
    p = TradeProposal(
        symbol="XPL",
        tier="mid",
        trade_nature="swing",
        action="buy",
        confidence=1,
    )
    assert p.confidence == 1.0
