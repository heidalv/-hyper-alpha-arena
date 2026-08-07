"""assistant_badge_service / feishu bridge 单元测试。"""
from backend.services.assistant_badge_service import build_assistant_badge
from backend.services.feishu_assistant_bridge import (
    extract_im_message,
    parse_message_text,
    strip_feishu_mentions,
)


def test_strip_feishu_mentions():
    assert strip_feishu_mentions("@_user_1 你好") == "你好"


def test_parse_message_text_json():
    assert parse_message_text('{"text":"hello"}') == "hello"


def test_extract_im_message():
    event = {
        "message": {
            "chat_id": "oc_abc",
            "message_id": "om_x",
            "message_type": "text",
            "content": '{"text":"@bot 今天有什么报错"}',
        },
        "sender": {"sender_id": {"open_id": "ou_123"}},
    }
    parsed = extract_im_message(event)
    assert parsed is not None
    assert parsed["chat_id"] == "oc_abc"
    assert "报错" in parsed["text"]


def test_build_assistant_badge_shape():
    badge = build_assistant_badge(window_hours=24)
    assert "count" in badge
    assert badge["label"] == "错误"
    assert badge["kind"] in ("none", "p0", "error_types")
    assert "pushed_alerts" not in badge  # 仅 API 层注入
