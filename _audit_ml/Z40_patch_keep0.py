# -*- coding: utf-8 -*-
"""补丁脚本：修「`or default` 吞掉显式 0/False」的配置读取（第 1 轮）。

原则：**对现有配置零行为改变**——只有当某键被显式设成 0/0.0/False（此前不可能生效）时才不同。
每处替换都断言原文恰好出现一次，失败即中止（不做静默部分替换）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (相对路径, 原文, 新文, 说明)
PATCHES = [
    # ── open_gate：开仓就绪度/论题阈值（设 0 = 不作要求，此前会被吞成默认）──
    ("backend/services/mlto/open_gate.py",
     'int(getattr(settings, "MIDLONG_OPEN_READINESS_MIN_LONG", 78) or 78)',
     '_cfg_int_keep0("MIDLONG_OPEN_READINESS_MIN_LONG", 78)', "readiness long"),
    ("backend/services/mlto/open_gate.py",
     'int(getattr(settings, "MIDLONG_OPEN_READINESS_MIN_MID", 72) or 72)',
     '_cfg_int_keep0("MIDLONG_OPEN_READINESS_MIN_MID", 72)', "readiness mid"),
    ("backend/services/mlto/open_gate.py",
     'int(getattr(settings, "MIDLONG_THESIS_MIN_REVIEWS", 3) or 3)',
     '_cfg_int_keep0("MIDLONG_THESIS_MIN_REVIEWS", 3)', "min reviews"),
    ("backend/services/mlto/open_gate.py",
     'int(getattr(settings, "MIDLONG_THESIS_STABLE_MIN_SEC_LONG", 7200) or 7200)',
     '_cfg_int_keep0("MIDLONG_THESIS_STABLE_MIN_SEC_LONG", 7200)', "stable long"),
    ("backend/services/mlto/open_gate.py",
     'int(getattr(settings, "MIDLONG_THESIS_STABLE_MIN_SEC_MID", 1800) or 1800)',
     '_cfg_int_keep0("MIDLONG_THESIS_STABLE_MIN_SEC_MID", 1800)', "stable mid"),
    ("backend/services/mlto/open_gate.py",
     'int(getattr(settings, "MIDLONG_THESIS_STALE_MAX_SEC", 120) or 120)',
     '_cfg_int_keep0("MIDLONG_THESIS_STALE_MAX_SEC", 120)', "stale max"),
    ("backend/services/mlto/open_gate.py",
     'persist_ticks = max(1, int(getattr(settings, "MIDLONG_PERSISTENCE_TICKS", 2) or 2))',
     'persist_ticks = max(1, _cfg_int_keep0("MIDLONG_PERSISTENCE_TICKS", 2))', "persist ticks"),
    # ── 其余 HIGH+safety 站点 ──
    ("backend/services/multi_venue_funding_collector.py",
     'threshold = int(getattr(_settings, "MULTI_VENUE_FUNDING_ALERT_THRESHOLD", 3) or 0)',
     'threshold = _keep0_int(getattr(_settings, "MULTI_VENUE_FUNDING_ALERT_THRESHOLD", 3), 3)',
     "funding alert threshold"),
    ("backend/services/paper_fast_trial_controller.py",
     'min_ready = int(getattr(settings, "MIDLONG_OPEN_READINESS_MIN_MID", 35) or 35)',
     'min_ready = _keep0_int(getattr(settings, "MIDLONG_OPEN_READINESS_MIN_MID", 35), 35)',
     "fast trial min_ready"),
    ("backend/services/full_auto/midlong_helpers.py",
     '_risk_pct = float(getattr(_cfg_pf, "MIDLONG_RISK_PCT", 0.01) or 0.01)',
     '_risk_pct = _keep0_float(getattr(_cfg_pf, "MIDLONG_RISK_PCT", 0.01), 0.01)',
     "midlong risk pct"),
    ("backend/services/agent_quant_feature_table.py",
     '_trend_daily_cap = int(getattr(_qs, "TREND_DAILY_OPEN_CAP", 15) or 15)',
     '_trend_daily_cap = _keep0_int(getattr(_qs, "TREND_DAILY_OPEN_CAP", 15), 15)',
     "trend daily cap"),
    ("backend/services/agent_quant_feature_table.py",
     '_trend_week_cap = int(getattr(_qs, "TREND_MAX_OPENS_PER_WEEK", 6) or 6)',
     '_trend_week_cap = _keep0_int(getattr(_qs, "TREND_MAX_OPENS_PER_WEEK", 6), 6)',
     "trend week cap"),
    # ── 三个恶意助手：`getattr(settings,...) or default` ──
    ("backend/services/mlto/midlong_portfolio_risk.py",
     "        return float(getattr(settings, name, default) or default)",
     "        _v = getattr(settings, name, default)\n"
     "        return default if _v is None else float(_v)", "_cfg_float keep0"),
    ("backend/services/mlto/midlong_trade_design.py",
     "        return float(getattr(settings, name, default) or default)",
     "        _v = getattr(settings, name, default)\n"
     "        return default if _v is None else float(_v)", "_cfg_float keep0"),
    ("backend/services/mlto/midlong_trade_design.py",
     "        return int(getattr(settings, name, default) or default)",
     "        _v = getattr(settings, name, default)\n"
     "        return default if _v is None else int(_v)", "_cfg_int keep0"),
    ("backend/services/full_auto/midlong_position_manager.py",
     "        return int(getattr(settings, key, default) or 0)",
     "        _v = getattr(settings, key, default)\n"
     "        return 0 if _v is None else int(_v)", "_cfg_int keep0"),
    ("backend/services/full_auto/midlong_position_manager.py",
     "        return float(getattr(settings, key, default) or default)",
     "        _v = getattr(settings, key, default)\n"
     "        return default if _v is None else float(_v)", "_cfg_float keep0"),
]

HELPER_INLINE = '''

def _keep0_int(v, default):
    """保留显式 0 的整数读取（`v or default` 会把 0 吞掉）。[2026-09-10 审计轮]"""
    return int(default) if v is None else int(v)


def _keep0_float(v, default):
    """保留显式 0 的浮点读取。[2026-09-10 审计轮]"""
    return float(default) if v is None else float(v)

'''

OPEN_GATE_HELPER = '''

def _cfg_int_keep0(name: str, default: int) -> int:
    """读 int 配置并**保留显式 0**。

    [2026-09-10 审计轮] 原写法 `int(getattr(settings, name, default) or default)`
    会把显式设成 0 的阈值悄悄换回默认值——运维想用「0 = 不作要求」时
    看到的却是「按 78/72/3 拦」，是典型的静默失效。
    """
    try:
        v = getattr(settings, name, default)
    except Exception:
        return int(default)
    return int(default) if v is None else int(v)

'''


def main() -> int:
    ok, fail = 0, []
    touched = set()
    for rel, old, new, label in PATCHES:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        if old not in text:
            fail.append((rel, label, "原文未命中"))
            continue
        if text.count(old) != 1:
            fail.append((rel, label, f"命中 {text.count(old)} 次（要求唯一）"))
            continue
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        touched.add(rel)
        ok += 1
        print(f"  ✓ {rel:<58}{label}")

    # 插入行内小助手
    for rel in touched:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        if "_keep0_int" in text and "def _keep0_int" not in text:
            anchor = "logger = logging.getLogger(__name__)"
            if anchor in text:
                text = text.replace(anchor, anchor + HELPER_INLINE, 1)
                p.write_text(text, encoding="utf-8")
                print(f"  + 插入 _keep0_int/_keep0_float → {rel}")
            else:
                fail.append((rel, "_keep0 助手", "找不到 logger 锚点"))
        if "_cfg_int_keep0" in text and "def _cfg_int_keep0" not in text:
            anchor = "logger = logging.getLogger(__name__)"
            if anchor in text:
                text = text.replace(anchor, anchor + OPEN_GATE_HELPER, 1)
                p.write_text(text, encoding="utf-8")
                print(f"  + 插入 _cfg_int_keep0 → {rel}")
            else:
                fail.append((rel, "_cfg_int_keep0 助手", "找不到 logger 锚点"))

    print(f"\n成功 {ok} / 失败 {len(fail)}")
    for rel, label, why in fail:
        print(f"  ✗ {rel} [{label}] {why}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
