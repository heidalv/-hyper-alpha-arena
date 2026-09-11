import json, urllib.request, os, sys
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
print("=== .env 目标值 ===")
for k in ("MIDLONG_LOCATION_GATE_ENABLED","MIDLONG_SHORT_MODE","MLTO_LLM_DIRECTION_REQUIRE_FW_AGREE",
          "MLTO_LLM_WEIGHT_MID","EXIT_POLICY_MID_SL_PCT","FACTOR_SCORER_COST_SOURCE"):
    print(f"  {k} = {os.getenv(k)}")
print("\n=== 运行进程实际加载（通过 API 或日志验证）===")
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=6) as r:
        d=json.loads(r.read().decode())
    print("  boot_at:", d["boot_fingerprint"]["boot_at_iso"])
    print("  markers:", d["boot_fingerprint"]["live_markers"])
except Exception as e:
    print("  ERR", e)
