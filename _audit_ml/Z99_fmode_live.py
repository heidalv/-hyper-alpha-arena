import sys, os
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena"); sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT/".env")
from backend.services.decision_fusion_arbiter import _f_mode
for mode in ("paper", "live"):
    print(f"  _f_mode('FUSION_PROBE_MIN_PWIN', 0.40, '{mode}') = {_f_mode('FUSION_PROBE_MIN_PWIN', 0.40, mode)}")
print("  env FUSION_PROBE_MIN_PWIN        =", os.getenv("FUSION_PROBE_MIN_PWIN"))
print("  env FUSION_PROBE_MIN_PWIN_PAPER  =", os.getenv("FUSION_PROBE_MIN_PWIN_PAPER"))
print("  env FUSION_PROBE_DAILY_QUOTA_PAPER =", os.getenv("FUSION_PROBE_DAILY_QUOTA_PAPER"))
print("  env REENTRY_COOLDOWN_SEC =", os.getenv("REENTRY_COOLDOWN_SEC"), " / REENTRY_COOLDOWN_SECONDS =", os.getenv("REENTRY_COOLDOWN_SECONDS"))
from backend.config.settings import REENTRY_COOLDOWN_SECONDS as _R
print("  settings.REENTRY_COOLDOWN_SECONDS =", _R, "（.env 写的是 REENTRY_COOLDOWN_SEC=60）")
