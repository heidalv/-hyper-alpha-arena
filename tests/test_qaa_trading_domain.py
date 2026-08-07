"""
P2.2 QAA Trading Domain 测试。

完成标准（方案 P2.2）：
    - trading plugin 可导入、domain_id 正确
    - Lean 5 层 agent 清单完整（L2→L7）
    - R1 验证：热路径（L2→L5）全 NONE LLM
    - 监督层（L7）DEEP LLM 但非热路径
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def trading_module():
    """导入 QAA trading domain（需把 $QAA 加入 path）。"""
    import os
    qaa_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..",
                     "QAA通信协议构架")
    )
    if qaa_root not in sys.path:
        sys.path.insert(0, qaa_root)
    from qaa.domains.trading import cards, plugin
    return cards, plugin


class TestTradingPlugin:
    def test_domain_id(self, trading_module):
        cards, plugin = trading_module
        p = plugin.TradingPlugin()
        assert p.domain_id == "trading"

    def test_get_agent_cards(self, trading_module):
        cards, plugin = trading_module
        p = plugin.TradingPlugin()
        agent_cards = p.get_agent_cards()
        assert len(agent_cards) >= 15  # L2(4) + L3(4) + L4(3) + L5(1) + L6(4) + L7(2)

    def test_event_types(self, trading_module):
        cards, plugin = trading_module
        p = plugin.TradingPlugin()
        events = p.get_event_types()
        assert "market_snapshot" in events
        assert "order_event" in events


class TestHotpathZeroLLM:
    """R1：热路径（L2→L5）全部 NONE LLM。"""

    def test_verify_hotpath_no_llm(self, trading_module):
        cards, plugin = trading_module
        assert cards.verify_hotpath_no_llm() is True

    def test_each_hotpath_card_none(self, trading_module):
        cards, plugin = trading_module
        hotpath = cards.get_hotpath_cards()
        for agent_id, card in hotpath.items():
            assert card["llm_level"] == "none", f"{agent_id} 不是 NONE"

    def test_supervision_is_deep(self, trading_module):
        """监督层（L7）是 DEEP 但不在热路径。"""
        cards, plugin = trading_module
        sup = cards.get_supervision_cards()
        assert all(c["llm_level"] == "deep" for c in sup.values())


class TestLayerCoverage:
    def test_all_layers_present(self, trading_module):
        cards, plugin = trading_module
        layers = {c["layer"] for c in cards.ALL_TRADING_CARDS.values()}
        assert "L2" in layers
        assert "L3" in layers
        assert "L4" in layers
        assert "L5" in layers
        assert "L6" in layers
        assert "L7" in layers

    def test_contract_types_declared(self, trading_module):
        """每张 card 声明 produces/consumes 契约类型。"""
        cards, plugin = trading_module
        for agent_id, card in cards.ALL_TRADING_CARDS.items():
            assert "produces" in card, f"{agent_id} 缺 produces"
            assert "consumes" in card, f"{agent_id} 缺 consumes"
