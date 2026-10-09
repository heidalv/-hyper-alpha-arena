# -*- coding: utf-8 -*-
"""[2026-10-03] 两个真因的护栏：①LLM 配额默认关闭 ②deepseek-flash 被误判为非推理模型。

## ① 配额（用户明确要求「随便调用，为什么多出个配额」）
`quota_guard.QuotaGuard.check` 现在先读 `ANALYSIS_QUOTA_ENABLED`（**默认 false**）：
关闭时预检恒 `allow`，仅保留 `llm_quota_usage` 用量记账（可观测）。默认值（deep 每日 6 /
5h 30 / 周 150）会在无人察觉时把正常调用降级成"空响应"——实测 `daily_brief` 每天 07:00
必失败（`quota degrade: deepseek 5 小时窗已用 64/30`）。恢复限流：`ANALYSIS_QUOTA_ENABLED=true`。

## ② `deepseek-flash` 漏判（真因，且与用户长期投诉同源）
`llm_config_service.is_reasoning_model()` 决定是否用**新参数协议**
（`max_completion_tokens` + reasoning_content 流式）。名单里只有 `deepseek-v4`，
而本项目配置（id=17「DeepSeek V4 (Flash)」）的 `model`/`model_deep` 字面值是
**`deepseek-flash`** ⇒ 判为"非推理模型" ⇒ 走旧 `max_tokens` ⇒ **新 API 返回空响应**。
代码注释里已记录过同类事故（"此前漏掉 v4-flash → 空响应 → 中长线长期 hold"），
这次是**以另一种模型名复发**。

实测后果与修复对比：
· `POST /api/analysis/tasks/daily_brief`：修复前 550ms / ok=False / in=0 out=0；
  修复后 **11,388ms / ok=True / in=12,232 out=2,799**，`consensus=0.35 accepted=True`。
· `mlto_debate`（大脑牛熊辩论）7 天 6,433 次调用**失败 1,351 次（21%）**，
  与失败行"延迟/token 与成功行同构"完全吻合 ⇒ 辩论降级 → 大脑 hold（长期零开仓）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_QUOTA = (ROOT / "backend/services/analysis/quota_guard.py").read_text(encoding="utf-8")
SRC_LLM = (ROOT / "backend/services/llm_config_service.py").read_text(encoding="utf-8")


def test_quota_defaults_to_disabled():
    assert 'ANALYSIS_QUOTA_ENABLED", False' in SRC_QUOTA, "配额必须默认关闭（用户要求随便调用）"
    seg = SRC_QUOTA.split('ANALYSIS_QUOTA_ENABLED", False')[1][:300]
    assert "Decision(\"allow\"" in seg, "关闭时应直接放行"
    # 记账仍要保留（可观测）
    assert "llm_quota_usage" in SRC_QUOTA


def test_deepseek_flash_counts_as_reasoning_model():
    from backend.services.llm_config_service import is_reasoning_model

    assert is_reasoning_model("deepseek-flash") is True, "flash 必须走新参数协议（否则空响应）"
    assert is_reasoning_model("deepseek-v4-flash") is True
    assert is_reasoning_model("deepseek-v4-pro") is True
    assert is_reasoning_model("deepseek-reasoner") is True
    # 不能误伤经典非推理模型
    assert is_reasoning_model("deepseek-chat") is False


def test_model_name_tokens_present_in_source():
    for token in ('"deepseek-flash"', '"deepseek-v4-flash"', '"deepseek-v4-pro"', '"deepseek-v4"'):
        assert token in SRC_LLM, f"名单缺 {token} —— 漏判会再次导致空响应"
