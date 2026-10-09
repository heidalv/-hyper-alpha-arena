# -*- coding: utf-8 -*-
"""[2026-10-03 用户指令] Hermes LLM 驱动层护栏。

用户原话：「必须用 opencode 做驱动么？直接使用 deepseek flash 不行么？项目内就配置了啊」

## 实测背景
L2（提示词优化）/L3（架构进化）此前**唯一**通路是 OpenCode sidecar：
  · 需要额外一把 zai key（`.env` 里那行曾被注释 ⇒ 日志 `ZAI key present=False len=0`，任务"跑完但 0 产物"）；
  · 会 `MaxListenersExceededWarning` 崩溃（sidecar 日志 FATAL 后被看门狗重启）；
  · 自 2026-08-16 起停摆 48 天。
项目内**已配置** deepseek：`llm_configurations` id=17「DeepSeek V4 (Flash)」`is_default=true`、
`usage_scope` 含 `evolution,assistant`；id=85 为 deepseek-chat。

## 修复与实测
新增 `services/hermes_llm_driver.py`（与 opencode bridge 同签名 `collect_hermes_text`）：
默认 `HERMES_LLM_DRIVER=direct` → 走 `llm_config_service`（tier=quick ⇒ flash）；
`=opencode` 才回旧通路。两个引擎的 `_call_llm` 已改为调用驱动层。
**实测**：解析到 id=17 `deepseek-flash`；冒烟 `{"ok": true, "model_check": "ping"}` err=None；
`POST /api/hermes/run/architecture_evolution` → **9 秒**（opencode 路径 115 秒）返回
`new_proposals: 8`、`llm_error: null`、`parsed_ok: true`，库内 pending 285 → **293**。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_DRIVER = (ROOT / "backend/services/hermes_llm_driver.py").read_text(encoding="utf-8")
SRC_L3 = (ROOT / "backend/services/hermes_architecture_evolution_engine.py").read_text(encoding="utf-8")
SRC_L2 = (ROOT / "backend/services/hermes_prompt_optimizer_engine.py").read_text(encoding="utf-8")


def test_driver_defaults_to_direct_deepseek():
    assert 'HERMES_LLM_DRIVER", "direct"' in SRC_DRIVER, "默认必须是直连（用户口径）"
    assert 'HERMES_LLM_TIER", "quick"' in SRC_DRIVER, "默认档位 quick ⇒ flash"
    assert "call_llm_api_sync(" in SRC_DRIVER, "直连必须走项目统一 LLM 入口"
    assert "bypass_cache=True" in SRC_DRIVER, "L2/L3 必须绕缓存（否则得出旧结论）"


def test_opencode_is_opt_in_only():
    seg = SRC_DRIVER.split("def collect_hermes_text(")[1]
    assert 'if driver_name() == "opencode":' in seg
    # opencode 分支必须在 direct 之前，且 direct 是默认落点
    assert seg.index('driver_name() == "opencode"') < seg.index("call_llm_api_sync(")
    assert "collect_http_agent_stream_text" in seg, "opencode 通路保留（可回滚/对照）"


def test_no_silent_fallback_between_drivers():
    """失败必须如实返回错误，不能悄悄换驱动（否则又会"看起来跑了其实没产出"）。"""
    seg = SRC_DRIVER.split("def collect_hermes_text(")[1]
    assert "直连 LLM 失败" in seg and "返回空正文" in seg
    assert "return txt, None" in seg


def test_resolution_prefers_tenant_default_then_usage_scope():
    seg = SRC_DRIVER.split("def _resolve_llm_config(")[1]
    assert "HERMES_LLM_CONFIG_ID" in seg, "需支持钉死某条配置"
    assert "get_llm_config(tier=tier, tenant_id=tid)" in seg, "优先租户默认（=本机 flash）"
    assert "get_llm_config_for_usage(" in seg, "再按用途绑定（evolution/assistant）回落"
    # 按**调用形态**比较顺序（import 行里也有函数名，不能用裸名比较）
    assert seg.index("get_llm_config(tier=tier, tenant_id=tid)") < seg.index("get_llm_config_for_usage(usage,")


def test_both_engines_use_the_driver():
    for src, tag, title in ((SRC_L3, "L3", "架构进化"), (SRC_L2, "L2", "提示词优化")):
        assert "from backend.services.hermes_llm_driver import collect_hermes_text" in src, f"{title} 未接驱动层"
        assert "collect_hermes_text(" in src
        # 旧的直连 sidecar 调用不得残留（否则又绕过驱动选择）
        assert "collect_http_agent_stream_text(" not in src, f"{title} 仍在裸调 sidecar"
