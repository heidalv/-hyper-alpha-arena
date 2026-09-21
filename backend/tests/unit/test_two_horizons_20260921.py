# -*- coding: utf-8 -*-
"""[轮159 2026-09-21] 周期档只有**两档**：日内 + 长期趋势（用户口径）。

## 背景（用户指正）
用户看到辩论产出 `horizon_verdicts={intraday, swing, trend}` 后问：
「现在不是两个周期么？… 你说的多出的中线是怎么回事」。

事实核查：
- `brain_debate.HORIZONS` 自轮130（提交 51632cb）起是三档（日内/中期/长期趋势），
  来源是当时用户原话「牛熊分析需要明确周期，日内中期 和长期趋势」；
- 但**没有任何车道以"中期"为主周期** —— `TIER_PRIMARY_HORIZON` 只有 `mid→intraday`、
  `long→trend` ⇒ 中期的裁决从不成为 `primary_verdict`（从不影响决策），只被记进
  `horizon_verdicts`；同时它让每轮辩论多写一段中期论点，吃输出 token
  （轮158 定位的 900 截断里就有它一份）；
- 用户的两周期口径有唯一真源：`backend/config/cycle_semantics.py`（轮48 记录的原话）
  = **日内(intraday) + 长期趋势(trend)**。

## 本轮改动
1. `HORIZONS` 三档 → **两档**（intraday 日内 / trend 长期趋势）；`primary_horizon` 兜底值
   `"swing"` → `"intraday"`；
2. 原本挂在"中期"桶的证据（4h 结构、7d/30d 收益、technical 信号、因子路线）**并入日内桶**；
   顺带修掉一行自相矛盾的代码：`by_hz["swing" if primary_horizon("mid")=="intraday" else "intraday"]`
   （结果恒为 "swing"）；
3. `schemas` 的辩论契约模板改为**可调用**并从 `HORIZONS` 派生（单一真源）——
   周期档只允许定义一次，杜绝"代码改一处、system prompt 里还留着旧档"的漂移；
4. 画布 `agent_wall` 的辩论节点文案：三档 → 两周期；
5. 测试同步：`test_brain_debate_wiring_20260920`（档数与未知档断言）、
   `test_analysis_schemas_20260904`（支持可调用模板）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import cycle_semantics as CS  # noqa: E402
from backend.services.analysis import schemas as S  # noqa: E402
from backend.services.mlto import brain_debate as BD  # noqa: E402


def test_exactly_two_horizons():
    assert [h[0] for h in BD.HORIZONS] == ["intraday", "trend"]
    assert BD.HORIZON_KEYS == ("intraday", "trend")
    assert BD.HORIZON_CN == {"intraday": "日内", "trend": "长期趋势"}


def test_horizons_match_the_canonical_two_cycles():
    """周期档必须与 config/cycle_semantics.py（用户口径的唯一真源）一致。"""
    assert set(BD.HORIZON_KEYS) == {CS.INTRADAY, CS.TREND}


def test_every_horizon_has_a_lane():
    """不许再出现"没有车道以它为主周期"的多余档。"""
    primaries = set(BD.TIER_PRIMARY_HORIZON.values())
    for k in BD.HORIZON_KEYS:
        assert k in primaries, f"周期 {k} 不对应任何车道的主周期 —— 多余档"


def test_no_swing_horizon_left_anywhere():
    src = (ROOT / "backend/services/mlto/brain_debate.py").read_text(encoding="utf-8-sig")
    body = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert '"swing"' not in body.replace("TIER_PRIMARY_HORIZON", ""), \
        "brain_debate 仍把 swing 当周期键（应只剩车道 tier 语义）"


def test_contract_template_derives_from_horizons():
    """契约里的 horizons 键必须与 HORIZONS 同源（不是手写第二份）。"""
    tpl = S.TASK_SCHEMAS["mlto_debate"]["template"]
    assert callable(tpl), "模板应为可调用（惰性派生自 HORIZONS）"
    hz = (tpl() or {}).get("horizons") or {}
    assert set(hz) == set(BD.HORIZON_KEYS) == {"intraday", "trend"}

    txt = S.output_contract("mlto_debate")
    assert '"swing"' not in txt and "中期" not in txt, "注入 system 的契约里还残留中期档"


def test_debate_log_line_has_two_horizons():
    """日志行不再打印"中期=%s"（否则格式化参数数量会对不上）。"""
    src = (ROOT / "backend/services/mlto/brain_debate.py").read_text(encoding="utf-8-sig")
    assert "中期=%s" not in src
    assert "日内=%s 长期=%s" in src
