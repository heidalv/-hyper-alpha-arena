# -*- coding: utf-8 -*-
"""[轮156 2026-09-21] deepseek-flash 是**多模态**模型 + 图审的图必须真的发给主票。

## 用户指正 + 官方依据
用户："deepseek fl 是可以识图的，你看看最新的 api 文档" —— 对的，我上一轮判断错了。
官方更新日志（2026-09-10）：「DeepSeek-V4.1-Flash … with **native multimodal visual
understanding** … Change the model name to `deepseek-flash` to call the latest V4.1 Flash model.
… the model names `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp` are temporarily routed
to V4.1 Flash.」
官方 /zh-cn/guides/vision：图片走 OpenAI 兼容的 content **块数组**：
`{"type":"image_url","image_url":{"url":"data:image/png;base64,…"}}`；
**图片只能出现在 user 消息里**，放进 system/assistant 会 400
（实测原文 `Image in system message is unsupported`，已复现）。

## 本测试钉住
  ① 有图时 user 消息必须是块数组（文本块 + image_url data URL），system 保持纯字符串；
  ② 无图时仍是纯字符串（不改变既有行为）；
  ③ 声明了 images 但一张都没有 b64 ⇒ 退回纯文本，**不编造图片块**；
  ④ 图审任务 `images_for` 必须包含主票 `deepseek`（否则图没人看）；
  ⑤ 模型名用官方规范名 `deepseek-flash`（旧名只是被临时路由）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysis import model_gateway as MG  # noqa: E402

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="


@pytest.fixture()
def captured(monkeypatch):
    """拦住真正发出去的 messages，只考察我们的组装。"""
    box = {}

    def _fake(cfg, messages, **kw):
        box["messages"] = messages
        box["cfg"] = cfg
        box["kw"] = kw
        return {"choices": [{"message": {"content": '{"ok":true}'}}], "model": "deepseek-flash",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    import backend.services.llm_config_service as LCS

    monkeypatch.setattr(LCS, "call_llm_api_sync", _fake)
    t = MG.DeepSeekTransport()
    # 绕过 DB/env 解析，给一个确定可用的 cfg
    monkeypatch.setattr(t, "_resolve", lambda: SimpleNamespace(
        id=0, name="t", provider="deepseek", model="deepseek-flash",
        base_url="https://api.deepseek.com", api_key="k"))
    return t, box


def test_images_become_user_content_blocks(captured):
    t, box = captured
    t.complete("SYS", "看图", max_tokens=100, temperature=0.2, timeout_s=30, task="trend_chart_review",
               images=[{"b64": PNG_B64, "media_type": "image/png", "title": "BTC 4h"}])
    msgs = box["messages"]
    assert msgs[0]["role"] == "system" and isinstance(msgs[0]["content"], str), \
        "system 必须保持纯字符串（文档：system 带图会 400）"
    assert msgs[1]["role"] == "user" and isinstance(msgs[1]["content"], list)
    kinds = [b.get("type") for b in msgs[1]["content"]]
    assert kinds == ["text", "image_url"], kinds
    url = msgs[1]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,") and PNG_B64 in url


def test_no_images_keeps_plain_string(captured):
    t, box = captured
    t.complete("SYS", "纯文本", max_tokens=100, temperature=0.2, timeout_s=30, task="midlong_thesis")
    assert isinstance(box["messages"][1]["content"], str)


def test_images_without_b64_fall_back_to_text(captured):
    """声明有图但一张 b64 都没有 ⇒ 退回纯文本，不编造空的 image 块（否则必然 400）。"""
    t, box = captured
    t.complete("SYS", "看图", max_tokens=100, temperature=0.2, timeout_s=30,
               task="trend_chart_review", images=[{"media_type": "image/png"}, {"b64": "  "}])
    assert isinstance(box["messages"][1]["content"], str)


def test_multi_image_order_preserved(captured):
    t, box = captured
    t.complete("SYS", "两张图", max_tokens=100, temperature=0.2, timeout_s=30,
               task="trend_chart_review",
               images=[{"b64": PNG_B64, "media_type": "image/png"},
                       {"b64": PNG_B64, "media_type": "image/jpeg"}])
    parts = box["messages"][1]["content"]
    assert [b["type"] for b in parts] == ["text", "image_url", "image_url"]
    assert parts[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_chart_task_sends_images_to_primary():
    """接线护栏：图审必须把图发给**主票**，否则渲染出来的 K 线图没有任何票会看。"""
    src = (ROOT / "backend/services/analysis/tasks.py").read_text(encoding="utf-8-sig")
    assert 'images_for=["deepseek", "glm_opencode", "glm_opencode_alt"] if images else None' in src, \
        "图审的 images_for 未包含主票 deepseek"


def test_canonical_model_name_is_used():
    """用官方规范名 deepseek-flash；deepseek-v4-flash 已被官方标记为临时路由的旧名。"""
    src = (ROOT / "backend/services/analysis/model_gateway.py").read_text(encoding="utf-8-sig")
    assert '_env("DEEPSEEK_MODEL", "deepseek-flash")' in src
    assert 'model=_env("DEEPSEEK_MODEL", "deepseek-flash")' in src
    assert '"deepseek-v4-flash"' not in src, "代码默认值里不应再出现退役旧名"
