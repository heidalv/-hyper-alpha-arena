"""F17 配置治理测试（2026-09-02 因子系统闭环修复）。

原病症：决定「因子能否拿到权重」「信号能否变成订单」「实盘结果能否回流学习」的
一批开关，长期既不在 `.env` 也不在 `docs/RUNTIME_CONFIG_FACTS.md` —— 只活在代码
默认值里。松紧被谁改过既无留痕，漂移检查也查不到。

本测试守住三条线：这批开关必须留在事实清单里；清单里的每个键都要能被校验脚本的
表格正则解析（★ 之类的装饰字符会让整行静默失配，等于没登记）；以及校验脚本自身
要能正确处理带行尾注释的 `.env` 行。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

FACTS_PATH = _REPO_ROOT / "docs" / "RUNTIME_CONFIG_FACTS.md"

# 因子闭环上的关键开关：改动任一个都会改变「开不开仓 / 因子权重 / 学习通断」
_MUST_REGISTER = (
    # pwin 仲裁：决定信号能否变成订单
    "FUSION_PWIN_ABSOLUTE_MIN",
    "FUSION_PWIN_TIERED",
    "FUSION_PROBE_MIN_PWIN",
    "FUSION_PROBE_DAILY_QUOTA",
    "FUSION_SHADOW_PROBE_MIN_PWIN",
    "FUSION_PWIN_UNUSABLE_MODE",
    # 因子权重口径
    "FACTOR_COMBO_MODE",
    "FACTOR_IC_WEIGHT_MODE",
    "FACTOR_MIN_NET_IC",
    # 晋升门禁
    "FACTOR_OVERSIGHT_MAX_PBO",
    "FACTOR_HELDOUT_ENABLED",
    "FEATURE_WFO_GATE_ENABLED",
    # 学习闭环通断
    "LIVE_LEARNING_HOOKS_ENABLED",
)


def _load_facts():
    from check_config_drift import load_facts
    return {k: v for k, v, _ in load_facts(FACTS_PATH)}


@pytest.mark.parametrize("key", _MUST_REGISTER)
def test_critical_switch_is_registered(key):
    """因子闭环关键开关必须登记在事实清单里。"""
    assert key in _load_facts(), (
        f"{key} 未登记 —— 它能改变风控松紧或闭环通断，却不受漂移检查覆盖"
    )


def test_no_drift_between_env_and_facts():
    """.env 与事实清单零漂移（项目硬规矩：提交前必须为 0）。"""
    from check_config_drift import load_env, load_facts, _same

    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        pytest.skip(".env 不存在（CI 环境）")

    env, duplicates = load_env(env_path)
    assert not duplicates, f".env 存在重复键（会静默覆盖）: {sorted(set(duplicates))}"

    drifts = [
        f"{k}: doc={v} env={env[k]}"
        for k, v, _ in load_facts(FACTS_PATH)
        if k in env and not _same(env[k], v)
    ]
    assert not drifts, "存在配置漂移:\n  " + "\n  ".join(drifts)


def test_every_documented_row_is_parseable():
    """清单里长得像配置行的每一行都必须能被校验脚本解析。

    校验脚本的表格正则要求键名后紧跟空白+竖线。曾经在键名后加过 ★ 标记，
    结果那些行整行静默失配 —— 表面上"登记了"，实际完全不受监管。
    """
    from check_config_drift import FACT_ROW_RE

    parsed = set(_load_facts())
    unparsed = []
    for ln in FACTS_PATH.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or not re.fullmatch(r"[A-Z][A-Z0-9_]*\s*\S*", cells[0] or "x"):
            continue
        key = cells[0].split()[0] if cells[0] else ""
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            continue
        if not FACT_ROW_RE.match(s) or key not in parsed:
            unparsed.append(s[:90])

    assert not unparsed, (
        "以下行看着是配置登记、实际未被解析（键名后有多余字符？）:\n  "
        + "\n  ".join(unparsed)
    )


def test_env_loader_strips_trailing_comment(tmp_path):
    """校验脚本必须剥离 .env 的行尾注释，否则注释会被当成值比对。"""
    from check_config_drift import load_env

    p = tmp_path / ".env"
    p.write_text(
        "PLAIN=false\n"
        "WITH_COMMENT=false  # 关闭理由写在这里\n"
        "QUOTED=\"true\"  # 带引号\n"
        "HASH_IN_VALUE=a#b\n",
        encoding="utf-8",
    )
    env, _ = load_env(p)

    assert env["PLAIN"] == "false"
    assert env["WITH_COMMENT"] == "false", "行尾注释未剥离 → 会报假漂移"
    assert env["QUOTED"] == "true"
    assert env["HASH_IN_VALUE"] == "a#b", "值内部的 # 不应被当作注释"


def test_changed_switches_carry_rationale():
    """本次变更过的开关，备注里要留下依据（★ 标记 + 说明）。"""
    changed = (
        "FUSION_PROBE_MIN_PWIN", "FUSION_PROBE_DAILY_QUOTA",
        "FUSION_SHADOW_PROBE_MIN_PWIN", "FUSION_PWIN_UNUSABLE_MODE",
        "FACTOR_OVERSIGHT_MAX_PBO", "LIVE_LEARNING_HOOKS_ENABLED",
    )
    text = FACTS_PATH.read_text(encoding="utf-8")
    for key in changed:
        row = next(
            (ln for ln in text.splitlines() if ln.strip().startswith(f"| {key} ")),
            None,
        )
        assert row, f"{key} 行缺失"
        note = row.split("|")[-2] if row.count("|") >= 4 else ""
        assert len(note.strip()) >= 15, f"{key} 备注过简，未说明变更依据: {note!r}"
