"""sample_pipeline — 交易学习样本标准管线（v2.0 格式）

实现 docs/TRAINING_SAMPLE_FORMAT_SPEC.md 定义的三层格式：
  - 原始层 → 标准层 (TradeLearnSample)
  - 标准层 → 训练层 (SFT / DPO JSONL)
  - 标准层 ↔ Parquet 打包/解包
  - 训练层 CI 验证

设计原则：
  - 零重型依赖：仅用标准库 + 可选 pandas/pyarrow（缺失时优雅降级）。
  - 向后兼容：能读 v1.0 dataset_builder 产出的 {"messages": [...]} JSONL。
  - Turing 友好：所有数值默认 float16 精度范围，序列长度可配。

用法见文档第 6 节，或：
    python -m training.tools.sample_pipeline --help
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量（与 dataset_builder.py / gate_optimizer_service.py 对齐）
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "2.0"
PIPELINE_VERSION = "sample_pipeline@2.0.0"

PARAM_BOUNDS: Dict[str, Tuple[float, float]] = {
    "min_risk_reward": (1.5, 3.0),
    "scalp_min_confidence": (55.0, 85.0),
    "max_daily_trades": (3.0, 10.0),
}

BASELINE_PARAMS: Dict[str, float] = {
    "min_risk_reward": 1.8,
    "scalp_min_confidence": 70,
    "max_daily_trades": 12,
}

DEFAULT_SYSTEM_PROMPT = (
    "你是加密永续合约交易平台的门控参数优化器。根据给定的市场状态和历史交易统计，"
    "输出建议的门控参数。决策原则：手续费占比高则降频收紧，平均亏损>平均盈利则"
    "提高盈亏比门槛，胜率高且赚多亏少则可适度放松。只能输出 JSON，不要输出任何其它内容。"
)


# ---------------------------------------------------------------------------
# 标准层对象
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(obj: Any) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]


def _ulid_like() -> str:
    """简化版 ULID（时间有序），无需第三方库。生产可换真 ULID。"""
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    import secrets
    return f"tls_{ts:013x}{secrets.token_hex(6)}"


def _clamp_param(key: str, val: float) -> float:
    lo, hi = PARAM_BOUNDS[key]
    v = max(lo, min(hi, val))
    if key in ("scalp_min_confidence", "max_daily_trades"):
        v = float(int(round(v)))
    return v


def _fill_gates(gates: Optional[Dict[str, float]]) -> Dict[str, float]:
    """缺省参数补默认值（向后兼容旧样本）。"""
    out = dict(BASELINE_PARAMS)
    if gates:
        for k in PARAM_BOUNDS:
            if k in gates and gates[k] is not None:
                out[k] = _clamp_param(k, float(gates[k]))
    return out


def build_canonical_sample(
    *,
    source_snapshot_id: int,
    source_session_id: str,
    regime: str,
    symbol: str,
    window_start: str,
    window_end: str,
    window_days: int,
    stats: Dict[str, Any],
    current_gates: Dict[str, float],
    target_gates: Dict[str, float],
    reasoning: str,
    label_kind: str,
    label_method: str = "rule_v1",
    label_confidence: float = 0.75,
    volatility_at_window: Optional[float] = None,
    trend_strength: Optional[float] = None,
    max_drawdown_pct: Optional[float] = None,
    sharpe_estimate: Optional[float] = None,
    quality: str = "usable",
    regime_confidence: float = 0.8,
    flags: Optional[List[str]] = None,
    prev_hash: str = "",
    sample_weight: float = 1.0,
    asset_class: str = "crypto",
    sector: Optional[str] = None,
    market_cap_tier: Optional[str] = None,
    crypto_specific: Optional[Dict[str, Any]] = None,
    equity_specific: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一条标准层 TradeLearnSample（v2.0 schema）。

    这是数据管线的核心入口：把交易机产出的原始字段组装成标准样本。
    标签由调用方提供（来自 dataset_builder 的 rule_v1 / backtest_grid_v2）。

    asset_class 决定该样本路由到哪个市场 LoRA（crypto/a_share/hk/us）。
    多市场扩展字段（sector/market_cap_tier/crypto_specific/equity_specific）
    按 asset_class 条件填充，其他市场留 None。
    """
    market_context: Dict[str, Any] = {
        "asset_class": asset_class,
        "regime": regime,
        "symbol": symbol,
        "window_start": window_start,
        "window_end": window_end,
        "window_days": window_days,
        "sector": sector,
        "market_cap_tier": market_cap_tier,
        "crypto_specific": crypto_specific,
        "equity_specific": equity_specific,
    }
    if volatility_at_window is not None:
        market_context["volatility_at_window"] = volatility_at_window
    if trend_strength is not None:
        market_context["trend_strength"] = trend_strength

    stats_full = dict(stats)
    if max_drawdown_pct is not None:
        stats_full.setdefault("max_drawdown_pct", max_drawdown_pct)
    if sharpe_estimate is not None:
        stats_full.setdefault("sharpe_estimate", sharpe_estimate)

    target = _fill_gates(target_gates)
    current = _fill_gates(current_gates)

    sample: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "sample_id": _ulid_like(),
        "source_snapshot_id": source_snapshot_id,
        "source_session_id": source_session_id,
        "created_at": _now_iso(),
        "pipeline_version": PIPELINE_VERSION,
        "market_context": market_context,
        "stats": stats_full,
        "current_gates": current,
        "label": {
            "target_gates": target,
            "label_kind": label_kind,
            "label_method": label_method,
            "label_confidence": label_confidence,
            "reasoning": reasoning,
        },
        "annotation": {
            "quality": quality,
            "regime_confidence": regime_confidence,
            "sample_weight": sample_weight,
            "flags": flags or [],
        },
        "messages": None,
        "split": None,
    }
    # content_hash 基于内容（不含 sample_id/created_at/hash 自身）
    content_for_hash = {k: v for k, v in sample.items() if k not in ("sample_id", "created_at")}
    sample["content_hash"] = _sha256(content_for_hash)
    sample["prev_hash"] = prev_hash
    return sample


# ---------------------------------------------------------------------------
# 标准层 → 训练层（SFT 派生）
# ---------------------------------------------------------------------------


_ASSET_CLASS_LABEL = {
    "crypto": "加密货币",
    "a_share": "A股",
    "hk": "港股",
    "us": "美股",
}


def _build_user_prompt(s: Dict[str, Any]) -> str:
    mc = s["market_context"]
    st = s["stats"]
    wr = st.get("win_rate", 0)
    wr_str = f"{wr * 100:.1f}%" if isinstance(wr, (int, float)) else str(wr)
    ac = mc.get("asset_class", "crypto")
    ac_label = _ASSET_CLASS_LABEL.get(ac, ac)
    sector_line = f"- 板块：{mc['sector']}\n" if mc.get("sector") else ""
    return (
        f"资产类别：{ac_label}（{mc['symbol']}）\n"
        f"当前市场环境：{mc['regime']}\n"
        f"统计窗口（{mc.get('window_days', 7)}天）：\n"
        f"{sector_line}"
        f"- 交易笔数：{st.get('trades', 'N/A')}\n"
        f"- 胜率：{wr_str}\n"
        f"- 平均盈利：{st.get('avg_win_pct', st.get('avg_win', 0)):.4f}\n"
        f"- 平均亏损：{st.get('avg_loss_pct', st.get('avg_loss', 0)):.4f}\n"
        f"- 净盈亏：{st.get('net_pnl_pct', st.get('net_pnl', 0)):+.4f}\n"
        f"- 手续费侵蚀指标：{st.get('fee_gross_ratio', 0):.4f}\n\n"
        f"请输出建议的门控参数，严格 JSON 格式：\n"
        f'{{"min_risk_reward": <1.5-3.0>, '
        f'"scalp_min_confidence": <55-85整数>, '
        f'"max_daily_trades": <3-10整数>, '
        f'"confidence": <0-1>, '
        f'"reasoning": "<一句话理由>"}}'
    )


def _build_assistant(s: Dict[str, Any]) -> str:
    tg = s["label"]["target_gates"]
    return json.dumps(
        {
            "min_risk_reward": tg["min_risk_reward"],
            "scalp_min_confidence": tg["scalp_min_confidence"],
            "max_daily_trades": tg["max_daily_trades"],
            "confidence": s["label"].get("label_confidence", 0.75),
            "reasoning": s["label"].get("reasoning", ""),
        },
        ensure_ascii=False,
    )


def filter_by_asset_class(
    samples: List[Dict[str, Any]], asset_class: str
) -> List[Dict[str, Any]]:
    """按市场过滤样本（多市场独立训练用）。

    crypto 样本只训 crypto LoRA，A 股样本只训 a_share LoRA，互不干扰。
    """
    return [
        s for s in samples
        if s.get("market_context", {}).get("asset_class", "crypto") == asset_class
    ]


def canonical_to_sft_jsonl(
    samples: List[Dict[str, Any]],
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> List[Dict[str, Any]]:
    """把标准样本列表转成 SFT messages JSONL 行（Unsloth/TRL 兼容）。"""
    rows: List[Dict[str, Any]] = []
    for s in samples:
        if s.get("annotation", {}).get("quality") == "reject":
            continue
        rows.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": _build_user_prompt(s)},
                    {"role": "assistant", "content": _build_assistant(s)},
                ]
            }
        )
    return rows


def canonical_to_dpo_jsonl(
    samples: List[Dict[str, Any]],
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> List[Dict[str, Any]]:
    """把含 preference 字段的标准样本转成 TRL DPO 格式。

    要求 sample["preference"] 存在（chosen_gates / rejected_gates）。
    """
    rows: List[Dict[str, Any]] = []
    for s in samples:
        pref = s.get("preference")
        if not pref:
            continue
        if s.get("annotation", {}).get("quality") == "reject":
            continue
        prompt = _build_user_prompt(s)
        chosen = json.dumps(
            {**_fill_gates(pref.get("chosen_gates")), "reasoning": s["label"].get("reasoning", "")},
            ensure_ascii=False,
        )
        rejected = json.dumps(
            {**_fill_gates(pref.get("rejected_gates")), "reasoning": "较差参数"},
            ensure_ascii=False,
        )
        rows.append({"prompt": prompt, "chosen": chosen, "rejected": rejected})
    return rows


# ---------------------------------------------------------------------------
# Parquet 打包/解包（可选依赖）
# ---------------------------------------------------------------------------


def pack_parquet(samples: List[Dict[str, Any]], out_path: str) -> None:
    """标准 JSONL 样本列表 → Parquet。需 pyarrow/pandas。"""
    try:
        import pandas as pd
    except ImportError as e:
        raise SystemExit(
            "打包 Parquet 需要 pandas/pyarrow：pip install pandas pyarrow\n" f"({e})"
        ) from e
    import os

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    df = pd.DataFrame(samples)
    df.to_parquet(out_path, index=False, engine="pyarrow")


def unpack_parquet(parquet_path: str) -> List[Dict[str, Any]]:
    """Parquet → 标准样本列表。"""
    try:
        import pandas as pd
    except ImportError as e:
        raise SystemExit(
            "读取 Parquet 需要 pandas/pyarrow：pip install pandas pyarrow\n" f"({e})"
        ) from e
    df = pd.read_parquet(parquet_path)
    return df.to_dict("records")


# ---------------------------------------------------------------------------
# 验证（CI 必过）
# ---------------------------------------------------------------------------


def _validate_param_bounds(gates: Dict[str, float]) -> List[str]:
    errs: List[str] = []
    for k, (lo, hi) in PARAM_BOUNDS.items():
        if k not in gates:
            errs.append(f"label.target_gates 缺字段 {k}")
            continue
        v = gates[k]
        if not isinstance(v, (int, float)):
            errs.append(f"{k} 非数值: {v!r}")
        elif not (lo <= v <= hi):
            errs.append(f"{k}={v} 超出边界 [{lo}, {hi}]")
    return errs


def validate_sft_line(line: str, lineno: int) -> List[str]:
    """验证一行 SFT JSONL，返回错误列表（空=通过）。"""
    errs: List[str] = []
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        return [f"行{lineno}: JSON 解析失败: {e}"]

    msgs = obj.get("messages")
    if not isinstance(msgs, list) or len(msgs) != 3:
        return [f"行{lineno}: messages 应为长度3的数组"]

    roles = [m.get("role") for m in msgs]
    if roles != ["system", "user", "assistant"]:
        errs.append(f"行{lineno}: role 顺序应为 system/user/assistant，实际 {roles}")

    for m in msgs:
        if not isinstance(m.get("content"), str) or not m["content"].strip():
            errs.append(f"行{lineno}: role={m.get('role')} content 为空")

    # assistant 内容必须是合法 JSON 且参数在边界内
    try:
        assistant_obj = json.loads(msgs[2]["content"])
        errs += [f"行{lineno}: {e}" for e in _validate_param_bounds(assistant_obj)]
    except json.JSONDecodeError as e:
        errs.append(f"行{lineno}: assistant 内容非合法 JSON: {e}")
    return errs


def validate_file(path: str) -> int:
    """验证整个 JSONL 文件，返回错误数（0=通过）。"""
    total_errs = 0
    n = 0
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            n += 1
            errs = validate_sft_line(line, i)
            for e in errs:
                print(f"  ❌ {e}", file=sys.stderr)
            total_errs += len(errs)
    status = "✅ 通过" if total_errs == 0 else f"❌ {total_errs} 个错误"
    print(f"验证 {path}：{n} 条样本，{status}")
    return total_errs


# ---------------------------------------------------------------------------
# 哈希链（审计）
# ---------------------------------------------------------------------------


def stamp_hash_chain(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为样本列表打上顺序哈希链（prev_hash 指向上一条 content_hash）。"""
    prev = ""
    for s in samples:
        if "content_hash" not in s:
            content = {k: v for k, v in s.items() if k not in ("sample_id", "created_at", "content_hash", "prev_hash")}
            s["content_hash"] = _sha256(content)
        s["prev_hash"] = prev
        prev = s["content_hash"]
    return samples


# ---------------------------------------------------------------------------
# I/O 辅助
# ---------------------------------------------------------------------------


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def write_jsonl(rows: List[Dict[str, Any]], path: str) -> int:
    import os

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    p = argparse.ArgumentParser(description="交易学习样本管线 v2.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="验证 SFT JSONL 文件")
    pv.add_argument("path")

    pp = sub.add_parser("pack", help="标准 JSONL → Parquet")
    pp.add_argument("input")
    pp.add_argument("output")

    up = sub.add_parser("unpack", help="Parquet → 标准 JSONL")
    up.add_argument("input")
    up.add_argument("output")

    sft = sub.add_parser("to-sft", help="标准 JSONL → SFT JSONL")
    sft.add_argument("input")
    sft.add_argument("output")

    dpo = sub.add_parser("to-dpo", help="标准 JSONL → DPO JSONL")
    dpo.add_argument("input")
    dpo.add_argument("output")

    args = p.parse_args()

    if args.cmd == "validate":
        return 0 if validate_file(args.path) == 0 else 1

    if args.cmd == "pack":
        samples = read_jsonl(args.input)
        pack_parquet(samples, args.output)
        print(f"打包 {len(samples)} 条 → {args.output}")
        return 0

    if args.cmd == "unpack":
        samples = unpack_parquet(args.input)
        n = write_jsonl(samples, args.output)
        print(f"解包 {n} 条 → {args.output}")
        return 0

    if args.cmd == "to-sft":
        samples = read_jsonl(args.input)
        rows = canonical_to_sft_jsonl(samples)
        n = write_jsonl(rows, args.output)
        print(f"派生 SFT {n} 条 → {args.output}")
        return 0

    if args.cmd == "to-dpo":
        samples = read_jsonl(args.input)
        rows = canonical_to_dpo_jsonl(samples)
        n = write_jsonl(rows, args.output)
        print(f"派生 DPO {n} 条 → {args.output}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(_cli())
