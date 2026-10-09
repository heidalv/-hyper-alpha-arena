# -*- coding: utf-8 -*-
"""factors_lab 配置 —— 五Agent因子研究闭环（ADR-21 落地）。

隔离纪律：本包只写 backend/data/factors_lab/ 与 v7 记忆表；不碰主交易热路径、
不改主库 schema、不动 lifecycle 状态机（晋级仍走 REV-P10/P12 评审）。
"""
from __future__ import annotations

import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_DATA_DIR = Path(os.getenv("FACTORS_LAB_DATA_DIR", str(_PKG_DIR.parent.parent / "data" / "factors_lab")))

PANEL_PERIOD = os.getenv("FACTORS_LAB_PANEL_PERIOD", "1d")


def enabled() -> bool:
    """cron 自动轮开关（默认开；手动端点不受限）。"""
    return os.getenv("FACTORS_LAB_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def max_hypotheses() -> int:
    try:
        return max(1, min(int(os.getenv("FACTORS_LAB_MAX_HYPOTHESES", "4")), 8))
    except Exception:
        return 4


def candidates_per_hypothesis() -> int:
    try:
        return max(1, min(int(os.getenv("FACTORS_LAB_CANDIDATES_PER_HYP", "2")), 4))
    except Exception:
        return 2


def universe_n() -> int:
    try:
        return max(8, int(os.getenv("FACTORS_LAB_UNIVERSE", "24")))
    except Exception:
        return 24


def round_timeout_sec() -> float:
    try:
        return max(120.0, float(os.getenv("FACTORS_LAB_ROUND_TIMEOUT", "900")))
    except Exception:
        return 900.0


def diversity_token_threshold() -> float:
    """假设多样性前置拒收：token Jaccard 超此值拒收（ADR-21 逻辑层分量）。"""
    try:
        return float(os.getenv("FACTORS_LAB_DIV_JACCARD", "0.6"))
    except Exception:
        return 0.6


def ast_sim_threshold() -> float:
    """候选 AST 相似去重阈值（ADR-21 结构层分量）。"""
    try:
        return float(os.getenv("FACTORS_LAB_AST_SIM", "0.75"))
    except Exception:
        return 0.75


def ic_pass_threshold() -> float:
    """|mean RankIC| 达标线（⑤ 归因用）。"""
    try:
        return float(os.getenv("FACTORS_LAB_IC_PASS", "0.02"))
    except Exception:
        return 0.02


def crowded_corr_threshold() -> float:
    """与已接受因子价值序列的最大相关超此值 → crowded。"""
    try:
        return float(os.getenv("FACTORS_LAB_CROWDED_CORR", "0.7"))
    except Exception:
        return 0.7


def data_dir() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


def knowledge_path() -> Path:
    return data_dir() / "knowledge_cards.jsonl"


def hypotheses_path() -> Path:
    return data_dir() / "hypotheses.jsonl"


def candidates_path() -> Path:
    return data_dir() / "candidates.jsonl"


def guidance_path() -> Path:
    return data_dir() / "next_round_guidance.jsonl"


def reports_dir() -> Path:
    p = data_dir() / "round_reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def rounds_index_path() -> Path:
    return data_dir() / "rounds_index.jsonl"


def golden_path() -> Path:
    return data_dir() / "calibration_golden.json"
