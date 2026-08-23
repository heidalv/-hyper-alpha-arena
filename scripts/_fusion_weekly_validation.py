"""每周 pwin 分桶验证（衰减监控，阶段2）。

用法：Hyper-Alpha-Arena\.venv\Scripts\python.exe scripts\_fusion_weekly_validation.py [days]
判据：pwin>=0.55 桶最近 N 天已结算信号 wr>=55% 且样本>=100 → OK；
不达标 → 人工决定 FUSION_SCALP_PWIN_MIN 上移或 FUSION_MODE=factor。
"""
import sys

sys.path.insert(0, ".")
from backend.services.source_attribution import weekly_pwin_validation  # noqa: E402
from backend.services.source_attribution import attribution  # noqa: E402

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    r = weekly_pwin_validation(days)
    print("[pwin bucket] ", r)
    snap = attribution.snapshot()
    print("[sources]", {k: v for k, v in snap["stats"].items() if v["n"] >= 5})
    print("[shadow]", snap["shadow"])
    print("[breaker_shadow]", snap["breaker_shadow"])
    print("RESULT:", "OK" if r.get("ok") else "NEED_ACTION")
