"""套利 LLM 流式响应解析。"""

from backend.services.rebate_arb.arb_llm_planner import _extract_json, _extract_message_content


def test_extract_message_from_reasoning_content():
    resp = {
        "choices": [{
            "message": {
                "content": "",
                "reasoning_content": '{"symbol":"ETH/USDT","direction":"bearish","confidence":70,"risk_level":"normal","reasoning":"test"}',
            }
        }]
    }
    text = _extract_message_content(resp)
    parsed = _extract_json(text)
    assert parsed is not None
    assert parsed["symbol"] == "ETH/USDT"
    assert parsed["direction"] == "bearish"
