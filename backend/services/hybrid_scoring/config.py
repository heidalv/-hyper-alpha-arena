# -*- coding: utf-8 -*-
"""混合打分中心（ADR-23 × AI选币集成）—— 配置与路径。

模式纪律（继承 08-26 选币重设计与 09-03 影子隔离审查）：
    off    ：完全不参与（默认安全位之外的最保守档）
    shadow ：只计算只落盘只统计（默认）——不接管注入，不影响任何实盘口径
    fusion ：影子期 IC 达标并经 ROI 问责门复核后的人工晋级档（消费位权重封顶 0.3）
"""
from __future__ import annotations

import os
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_DATA_DIR = Path(os.getenv("HYBRID_SCORE_DATA_DIR", str(_PKG_DIR.parent.parent / "data" / "hybrid_scoring")))

# K线面板默认周期（选币为日频级决策；训练与部署同周期，杜绝训练/推理口径漂移）
PANEL_PERIOD = os.getenv("HYBRID_SCORE_PANEL_PERIOD", "1d")


def mode() -> str:
    """shadow（默认）| fusion | off。"""
    m = os.getenv("HYBRID_SCORE_MODE", "shadow").strip().lower()
    return m if m in ("shadow", "fusion", "off") else "shadow"


def channel_b_enabled() -> bool:
    return os.getenv("HYBRID_SCORE_CHANNEL_B", "true").lower() in ("1", "true", "yes", "on")


def max_symbols() -> int:
    try:
        return max(5, int(os.getenv("HYBRID_SCORE_MAX_SYMBOLS", "30")))
    except Exception:
        return 30


def hook_interval_sec() -> float:
    """选币钩子节流：两次影子轮之间的最小间隔（默认 30min，对齐扫描节奏）。"""
    try:
        return max(300.0, float(os.getenv("HYBRID_SCORE_INTERVAL_SEC", "1800")))
    except Exception:
        return 1800.0


def train_universe_limit() -> int:
    try:
        return max(20, int(os.getenv("HYBRID_SCORE_TRAIN_UNIVERSE", "80")))
    except Exception:
        return 80


def model_max_age_days() -> float:
    """模型过期阈值：超期则通道A降级 IC 加权兜底。"""
    try:
        return max(1.0, float(os.getenv("HYBRID_SCORE_MODEL_MAX_AGE_DAYS", "14")))
    except Exception:
        return 14.0


def fusion_weight_cap() -> float:
    """fusion 模式下 hybrid 分数介入 composite 的权重上限（硬顶）。"""
    try:
        return min(0.3, max(0.0, float(os.getenv("HYBRID_SCORE_FUSION_W_CAP", "0.3"))))
    except Exception:
        return 0.3


def ic_entry_threshold() -> float:
    """影子期晋级参考线：融合臂滚动 RankIC 需 ≥ 此值才建议转 fusion（仅提示，晋级仍走 ROI 门）。"""
    try:
        return float(os.getenv("HYBRID_SCORE_IC_ENTRY", "0.05"))
    except Exception:
        return 0.05


# ---------------- 落盘路径 ----------------
def data_dir() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


def model_path() -> Path:
    return data_dir() / "ltr_model.txt"


def model_meta_path() -> Path:
    return data_dir() / "model_meta.json"


def latest_path() -> Path:
    return data_dir() / "latest.json"


def score_log_path() -> Path:
    return data_dir() / "score_log.jsonl"


def ic_stats_path() -> Path:
    return data_dir() / "ic_stats.json"


def report_path() -> Path:
    return data_dir() / "hybrid_ablation_report.json"
