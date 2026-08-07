"""Calibration — 通用置信度校准（分数→历史校准胜率 p_win）。"""

from backend.services.calibration.confidence_calibrator import (
    ConfidenceCalibrator,
    CalibrationResult,
    swing_calibrator,
    trend_calibrator,
    get_calibrator_for_nature,
)

__all__ = [
    "ConfidenceCalibrator",
    "CalibrationResult",
    "swing_calibrator",
    "trend_calibrator",
    "get_calibrator_for_nature",
]
