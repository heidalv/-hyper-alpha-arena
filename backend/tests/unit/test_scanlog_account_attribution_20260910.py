# -*- coding: utf-8 -*-
"""[2026-09-10 §53.6] 决策审计落的「账户归因」契约测试。

实测（`_audit_ml/Z77`）：`ai_decision_logs` 79,218 行里 **7,424 行（9.4%）** `account_id=0`，
即 mid/long 决策审计有近一成无法归因到账户（且当天仍在写入）。
根因：`mlto_cycle` 里两处取账户 id 只读 `session.paper_account_id`，
与本文件其它处（第 91/360/709 行）的惯例 `paper_account_id or account_id` 不一致。

本测试锁住：这两个调用点必须使用回落链，防止再次退化为 0。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "backend/services/full_auto/mlto_cycle.py"


def test_scan_log_call_site_uses_account_fallback_chain():
    src = SRC.read_text(encoding="utf-8")
    i = src.index("host.persist_independent_scan_log(")
    seg = src[i: i + 700]
    assert 'getattr(session, "paper_account_id", None)' in seg
    assert 'getattr(session, "account_id", None)' in seg, (
        "scan log 调用点缺少 account_id 回落 → 会退化成 account_id=0（归因丢失）"
    )
    # 回落链：paper_account_id 优先，account_id 兜底，中间用 or 串联
    assert re.search(
        r'paper_account_id",\s*None\)\s*or\s*getattr\(session,\s*"account_id"', seg
    ), seg[:400]


def test_trend_agent_call_site_uses_same_chain():
    src = SRC.read_text(encoding="utf-8")
    i = src.index("trend_agent.analyze_direction(")
    seg = src[i: i + 400]
    assert re.search(
        r'paper_account_id",\s*None\)\s*or\s*getattr\(session,\s*"account_id"', seg
    ), seg[:300]


def test_no_bare_paper_account_id_lookup_left_in_this_module():
    """本模块内不得再出现「只读 paper_account_id、无 or 回落」的账户取值。

    注意：回落链可能跨行书写，故把「本行 + 后两行」压平空白后再匹配。
    """
    lines = SRC.read_text(encoding="utf-8").splitlines()
    offenders = []
    for i, line in enumerate(lines, 1):
        if 'getattr(session, "paper_account_id", None)' not in line:
            continue
        blob = re.sub(r"\s+", " ", " ".join(lines[i - 1: i + 2]))
        if not re.search(r'paper_account_id", None\) or getattr\( session, "account_id"',
                         blob.replace("getattr(session", "getattr( session")):
            offenders.append(f"mlto_cycle.py:{i}")
    assert not offenders, "仍有无回落的账户取值: " + ", ".join(offenders)
