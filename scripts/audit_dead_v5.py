# -*- coding: utf-8 -*-
"""断线审计 v5：25 个未配置字段的**默认值**（判断是"默认开着"还是"默认关着"）。只读。"""
import sys
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CORE = ROOT / "backend" / "services" / "market_maker" / "core.py"
src = CORE.read_text(encoding="utf-8", errors="replace")


def defaults(cls):
    m = re.search(rf"^class {cls}\b", src, re.M)
    tail = src[m.end():]
    nxt = re.search(r"^(class |def |@)", tail, re.M)
    body = tail[:nxt.start()] if nxt else tail
    out = {}
    for name, expr in re.findall(r"^    (\w+)\s*:\s*[^=\n]+=\s*(.+)$", body, re.M):
        out[name] = expr.strip()
    return out


lim = defaults("LaneRiskLimits")
qp = defaults("QuoteParams")
UNCONFIGURED = [
    "exit_skew_scale_bp", "flow_persist_floor", "flow_persist_pause", "k_trend",
    "max_quote_age_sec", "mid_splice_on_gap", "min_edge_frac", "mp_block_bp",
    "p1_hold_sec", "p1_trigger_bp", "p3_spike_gate", "p45_hold_sec",
    "p4_breakout_gate", "p5_squeeze_gate", "post_stop_decay", "pullback_flow_block",
    "quote_hold_tol_bp", "slow_rev_min_bp", "toxic_bp", "toxic_streak",
    "trail_lock_bp", "trend_lookback", "trend_skew_scale_bp", "vol_window",
    "vwap_flow_block",
]
print("== 未配置字段的实际默认值（决定运行时是否生效）==")
print(f"{'字段':<24}{'默认值':<28}{'含义'}")
OFF_HINT = ("0", "0.0", "None", "False")
for k in UNCONFIGURED:
    v = lim.get(k, qp.get(k, "(不存在)"))
    raw = v.split("#")[0].strip()
    try:
        is_off = float(eval(raw)) == 0.0  # noqa: S307 - 本地静态默认值
    except Exception:
        is_off = raw in ("None", "False")
    tag = "默认关（未启用）" if is_off else "默认开（生效中）"
    print(f"  {k:<22}{raw:<30}{tag}")
