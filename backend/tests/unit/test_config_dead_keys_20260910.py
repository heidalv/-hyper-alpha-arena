# -*- coding: utf-8 -*-
"""[2026-09-10 §54] 配置「死键」治理契约测试（含工具盲点回归）。

背景（§54.1）：首版 `audit_config_effective.py` 只会按**字面**搜键名，于是把
`os.getenv(f"PC_{param}_{lane.upper()}")` 这类**动态前缀读取**判成"死键"——
我据此在 §39.2/§53.6 里错误宣称 `PC_RISK_PER_TRADE_PCT_LONG` 从未生效
（实测 `LaneLimits.for_lane("long").risk_per_trade_pct == 0.0125`，**是生效的**）。
本测试做三件事：
  1. 锁住「动态前缀读取」识别能力（含本条误报的回归护栏）；
  2. 锁住「真·死键」集合：必须是显式白名单的子集——**任何新增死键都会让测试变红**；
  3. 白名单每一项都必须写明原因（非缺陷 / 已按注释移除 / 由代码常量承担 / 待决策）。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

ace = importlib.import_module("audit_config_effective")

# ── 真·死键白名单（键 → 原因分类）。新增死键不在表中 ⇒ 测试失败，必须先判定归属 ──
DEAD_ALLOWLIST = {
    # 非缺陷：操作系统/第三方库消费的环境变量
    "NO_PROXY": "non_defect:OS/HTTP 客户端消费",
    "no_proxy": "non_defect:OS/HTTP 客户端消费（小写形式）",
    "LOG_FILE": "non_defect:仅 config/production.py 的类属性同名，非 env 读取",
    # 有代码注释证明是「有意移除」
    "SCALP_MAX_HOLD_SEC": "intentionally_removed:scalp_config_routes.py:45 注明该 env 从未被读取并已移除声明",
    # 属性由**代码常量**承担（env 覆盖无效，但安全属性本身在生效）
    "ARBITRAGE_MIN_ANNUAL_YIELD": "enforced_by_code_constant:opportunity_scanner.MIN_ANNUAL_YIELD=0.15",
    "FACTOR_MAX_TURNOVER": "enforced_by_code_constant:factor_engine 阈值 0.70（非 env）",
    "NSGA2_POPULATION_SIZE": "enforced_by_code_constant:NSGA2 种群大小取代码常量",
    # 功能车道未实现/未启用：这些 env 属"开关装饰"
    "ANOMALY_DETECTOR_ENABLED": "lane_unimplemented:全仓无 ANOMALY_DETECTOR 引用",
    "ARBITRAGE_ENABLED": "lane_unimplemented:套利车道由其它开关控制（.env=false）",
    "MARKET_SCANNER_ENABLED": "lane_unimplemented:全仓无 MARKET_SCANNER 引用",
    "MARKET_SCANNER_MIN_VOLUME": "lane_unimplemented:全仓无 MARKET_SCANNER 引用",
    "MARKET_SCANNER_TOP_N": "lane_unimplemented:全仓无 MARKET_SCANNER 引用",
    "ONCHAIN_DATA_ENABLED": "lane_unimplemented:全仓无 ONCHAIN_DATA 引用（.env=false）",
    "HERMES_L2_AB_MIN_SAMPLES": "lane_unimplemented:仅 ENABLED 键被读，样本下限未接线",
    "HERMES_L4_MAX_INCUBATIONS": "lane_unimplemented:全仓无 HERMES_L4 引用",
    # 近名误配：用户设的键与代码读的键不同 ⇒ 意图静默落空，**待决策**（改名/接线/删除）
    "V5_MAX_TRADE_PCT": "near_miss:代码读 V5_MAX_TRADE_RISK_PCT（默认同为 0.015，今日无实际差异）",
    "ONCHAIN_GLASSNODE_KEY": "near_miss:代码读 GLASSNODE_API_KEY（.env 该键为空）",
    "SCALP_TARGET_TP_PCT": "near_miss:代码读 SCALP_EXIT_TP_PCT",
    "SCALP_TARGET_SL_PCT": "near_miss:代码读 SCALP_EXIT_SL_PCT",
    "REENTRY_COOLDOWN_SEC": "near_miss:代码读 REENTRY_COOLDOWN_SECONDS（默认 600）⇒ 60s 意图变 600s（§39.2 D2 / 待决策 P2）",
    "BINANCE_ENCRYPTION_KEY": "non_defect:binance 凭证走 DB 凭证表（§live_trading_routes 注释），env 键未被读取；仅 tests/conftest 引用",
}

# [§54 修复 2] 后缀拼接读取（`os.getenv(name + _suffix)`）：必须被识别为"生效"
SUFFIX_CASES = {
    "FUSION_PROBE_MIN_PWIN_PAPER": "_PAPER",
    "MIDLONG_HUB_BUILD_PAPER": "_PAPER",
    "DECISION_PRICE_MAX_DEVIATION_PCT_PAPER": "_PAPER",
}

# 动态前缀读取：必须被识别（否则又会误判成死键）
DYNAMIC_CASES = {
    "PC_RISK_PER_TRADE_PCT_LONG": "PC_",
    "LLM2_CAP_MASTER": "LLM2_CAP_",
    "KLINE_RETENTION_DAYS_1M": "KLINE_RETENTION_DAYS_",
    "KLINE_P1_DEPTH_DAYS_1H": "KLINE_P1_DEPTH_DAYS_",
    "COLD_EXCHANGE_MAX_REQ_PER_MIN_OKX": "COLD_EXCHANGE_MAX_REQ_PER_MIN_",
}


def _report():
    return ace.build_report(ROOT)["findings"]


def test_dynamic_prefix_reads_are_detected():
    f = _report()
    dyn = f["env_dynamic_prefix_read"]
    missing = [k for k in DYNAMIC_CASES if k not in dyn]
    assert not missing, f"动态前缀读取未被识别（会退化成死键误报）: {missing}"
    for k, pref in DYNAMIC_CASES.items():
        assert dyn[k].startswith(pref), f"{k} 前缀识别错误: {dyn[k]}"


def test_pc_lane_param_false_alarm_regression():
    """§54.1 的误报回归：`PC_*_LONG` 不得再被判成死键。"""
    f = _report()
    assert "PC_RISK_PER_TRADE_PCT_LONG" not in f["env_truly_dead"]


def test_suffix_composed_reads_are_detected():
    """[§54 修复 2] `os.getenv(name + "_PAPER")` 这类后缀拼接读取必须被识别。"""
    f = _report()
    suf = f["env_suffix_composed_read"]
    assert suf, "后缀拼接读取未被识别（会把 *_PAPER/_LIVE 误判成死键）"
    missing = [k for k in SUFFIX_CASES if k not in suf]
    assert not missing, f"未识别的后缀拼接键: {missing}"
    for k, expect in SUFFIX_CASES.items():
        assert suf[k] == expect, f"{k} 后缀识别错误: {suf[k]}"


def test_fusion_probe_paper_threshold_is_live_runtime():
    """运行时证据：`FUSION_PROBE_MIN_PWIN_PAPER` 真的被 `_f_mode` 读取（paper 走它）。"""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os
    from backend.services.decision_fusion_arbiter import _f_mode

    want = os.getenv("FUSION_PROBE_MIN_PWIN_PAPER")
    if not want:
        return
    got = _f_mode("FUSION_PROBE_MIN_PWIN", 0.40, "paper")
    assert abs(got - float(want)) < 1e-12, f"paper 模式读到 {got}，.env _PAPER={want}"


def test_registry_gap_count_is_tracked():
    """治理项跟踪：settings 定义了但未登记 env_registry 的键必须为 **0**（§63 已清零）。"""
    f = _report()
    gap = f["settings_not_in_registry"]
    assert gap == [], f"未登记 env_registry 的 settings 键: {gap}——请补登记（见 §63）"


# ── [§54.4] .env 编码损坏护栏（实测 350 行注释被写成 `?`）──

ENV_DAMAGE_BASELINE = 352  # 2026-09-10 记录：连续 ≥5 个 `?` 的行数（注释为主）


def _env_lines():
    return (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines()


def test_env_damage_does_not_increase():
    """`.env` 注释已有 350 行被写成 `?`（编码损坏）。基线锁定：**不得再增加**。"""
    import re
    bad = [ln for ln in _env_lines() if re.search(r"\?{5,}", ln)]
    assert len(bad) <= ENV_DAMAGE_BASELINE, (
        f".env 编码损坏行数增至 {len(bad)}（基线 {ENV_DAMAGE_BASELINE}）——"
        "写 .env 时请用 UTF-8（勿用 PowerShell 默认编码覆盖）"
    )


def test_env_active_values_are_not_corrupted():
    """**生效值**（`#` 之前的 KEY=VALUE）不得含 `?` —— 值损坏会让系统静默走错配置。"""
    offenders = []
    for i, ln in enumerate(_env_lines(), 1):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        head = s.split("#", 1)[0]
        if "?" in head:
            offenders.append(f".env:{i} {head[:80]}")
    assert not offenders, "生效值被编码损坏: " + "; ".join(offenders[:5])


def test_dead_keys_are_within_allowlist():
    f = _report()
    dead = set(f["env_truly_dead"])
    new = sorted(dead - set(DEAD_ALLOWLIST))
    assert not new, (
        "出现**未判定**的死键（.env 设了但全仓无读取）——必须先在 DEAD_ALLOWLIST 里"
        f"给出原因分类，或把它接线上/删掉: {new}"
    )


def test_allowlist_entries_have_reasons():
    bad = [k for k, v in DEAD_ALLOWLIST.items() if not str(v).strip()]
    assert not bad, f"白名单缺少原因说明: {bad}"
    assert len(DEAD_ALLOWLIST) >= 15


def test_safety_hinted_dead_keys_are_flagged_for_decision():
    """安全类死键必须落在"待决策/非缺陷/常量承担"三类之一，不能是"车道未实现"以外的沉默项。"""
    f = _report()
    for k in f["env_truly_dead"]:
        if not ace.SAFETY_HINT.search(k):
            continue
        reason = DEAD_ALLOWLIST.get(k)
        assert reason, f"安全类死键未判定: {k}"
        assert reason.split(":", 1)[0] in (
            "non_defect", "intentionally_removed", "enforced_by_code_constant",
            "lane_unimplemented", "near_miss",
        ), f"{k} 的原因分类不合法: {reason}"


def test_pc_lane_param_is_live_runtime():
    """运行时证据：`PC_RISK_PER_TRADE_PCT_LONG` 真的改变了 long 车道的单笔风险。"""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os
    from backend.services.position_construction import LaneLimits

    want = os.getenv("PC_RISK_PER_TRADE_PCT_LONG")
    if not want:
        return  # 未配置则跳过（不构成本条断言的对象）
    got = LaneLimits.for_lane("long").risk_per_trade_pct
    assert abs(got - float(want)) < 1e-12, f"long 车道 risk_per_trade_pct={got} ≠ .env {want}"
    mid = LaneLimits.for_lane("mid").risk_per_trade_pct
    assert abs(mid - float(os.getenv("PC_RISK_PER_TRADE_PCT", "0.0075"))) < 1e-12
