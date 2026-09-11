import re
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
p = ROOT / "backend" / "config" / "env_registry.py"
src = p.read_text(encoding="utf-8")

# 1) SAFETY_CRITICAL_FLAGS 增补（闸门型：false = 保护关闭）
safety_add = [
    ("MIDLONG_PORTFOLIO_GATE_ENABLED", "关掉=组合闸（净敞口/相关簇/并发）不拦截"),
    ("MIDLONG_CHOP_GATE_ENABLED", "关掉=震荡市入场闸不拦截"),
    ("MIDLONG_FUNDING_GATE_ENABLED", "关掉=资金费闸不拦截"),
    ("MIDLONG_NO_PROGRESS_EXIT_ENABLED", "关掉=无进展退出（软退出）不评估"),
    ("MIDLONG_POSITION_MGMT_ENABLED", "关掉=中线持仓管理段整体跳过"),
    ("TIER_MID_ENABLED", "关掉=中线车道整体停（含闸门）"),
    ("TIER_LONG_ENABLED", "关掉=长线车道整体停（含闸门）"),
    ("LIVE_TPSL_SYNC", "关掉=实盘 TPSL 不同步到交易所"),
    ("FACTOR_HELDOUT_ENABLED", "关掉=因子留出集验证不跑"),
    ("FACTOR_SCORER_DSR_REQUIRED", "关掉=因子评分不要求 DSR 门槛"),
    ("FUNDING_SETTLE_ENABLED", "关掉=资金费结算不执行"),
]
anchor = '    "RISK_EVENT_WINDOWS_ENABLED",  # [v3 方向4] 关掉=下架/监控标签/清算级联 事件避险窗口不拦截\n})'
assert anchor in src, "SAFETY_CRITICAL_FLAGS 锚点未找到"
lines = "".join(f'    "{k}",  # [§63 登记] {c}\n' for k, c in safety_add)
src = src.replace(anchor, anchor.replace("\n})", "\n" + lines + "})"), 1)

# 2) KNOWN_FLAGS 增补（39 个 settings 定义键）
known_add = """    # ── [§63 登记 2026-09-10] settings.py 已定义但此前未登记的 39 个键 ──
    # （这些键由 .env 读取、被 settings 解析；未登记时启动校验会把它们报成"疑似拼写/遗留"，
    #   而该告警此前因调用时机问题进不了文件日志——见报告 §63。）
    "AI_FACTOR_DISCOVERY_ENABLED",
    "AUTO_COIN_FORBID_LONG",
    "AUTO_COIN_SOURCE",
    "FACTOR_CLOUD_SYNC_ENABLED",
    "FACTOR_FUNDING_DIRECTION_FIX",
    "FACTOR_HELDOUT_ENABLED",
    "FACTOR_SCORER_DSR_REQUIRED",
    "FACTOR_SCORER_LAG1_ENABLED",
    "FACTOR_SCORER_LAG1_GATE",
    "FACTOR_SCORER_NEUTRALIZE",
    "FACTOR_SIGNAL_FILTER_NONDIRECTIONAL",
    "FUNDING_SETTLE_APPLY_PNL",
    "FUNDING_SETTLE_ENABLED",
    "FUSION_LOW_QUALITY_HOLD",
    "FUSION_REGIME_WEIGHT_MULTIPLIERS",
    "INTRADAY_LLM_ENABLED",
    "KLINE_P1_PERIOD_MODE",
    "LIVE_TPSL_SYNC",
    "LLM_BUDGET_PER_TENANT",
    "LLM_HTTPS_PROXY",
    "LLM_HTTP_PROXY",
    "MIDLONG_ATR_SIZING_ENABLED",
    "MIDLONG_CHOP_GATE_ENABLED",
    "MIDLONG_CORE_BASKET",
    "MIDLONG_CORR_CLUSTER_SYMBOLS",
    "MIDLONG_FUNDING_GATE_ENABLED",
    "MIDLONG_NO_PROGRESS_EXIT_ENABLED",
    "MIDLONG_PORTFOLIO_GATE_ENABLED",
    "MIDLONG_POSITION_MGMT_ENABLED",
    "PAPER_FACTOR_LIVE_EXCLUDE",
    "RISK_V2_UNIFIED_STAGED_TP",
    "SCALP_DYNAMIC_HOLD_TPSL",
    "SCALP_EV_NEW_PARAM_EXPLORE",
    "SCALP_MARKET_AWARE_TPSL",
    "SCALP_RESEARCH_ENABLED",
    "SCALP_SHORT_LIVE_STRICT",
    "SCALP_SHORT_REQUIRES_TREND_DOWN",
    "TIER_LONG_ENABLED",
    "TIER_MID_ENABLED",
"""
anchor2 = "KNOWN_FLAGS: frozenset[str] = frozenset({\n"
assert anchor2 in src, "KNOWN_FLAGS 锚点未找到"
src = src.replace(anchor2, anchor2 + known_add, 1)
p.write_text(src, encoding="utf-8")
print("已登记 11 个安全关键 flag + 39 个 settings 键")
