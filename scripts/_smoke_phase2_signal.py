"""阶段2冒烟：真实 K 线验证 ① D7 因子引导不再"因子计算失败" ② compute_fusion_decision 正常产出
③ funding 方向不再恒 -1.0。"""
import os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

from backend.services.kline_data_service import kline_service

syms = ["BTC", "ETH"]
klines_data = {}
prices = {}
for sym in syms:
    rows = kline_service.get_klines_from_db(sym, "5m", 200)
    if not rows:
        print(f"{sym}: 无K线")
        continue
    import pandas as pd
    klines_data[sym] = pd.DataFrame(rows)
    prices[sym] = float(klines_data[sym]["close"].iloc[-1])

# ① D7 因子引导
from backend.services.ai_decision_integration import build_factor_guidance_for_prompt
guidance = build_factor_guidance_for_prompt(syms, klines_data, prices)
print("=== D7 因子引导（前 12 行）===")
print("\n".join(guidance.split("\n")[:12]))
print("包含'因子计算失败':", "因子计算失败" in guidance)

# ② 融合决策
from backend.services.ai_decision_integration import compute_fusion_decision
print("=== compute_fusion_decision ===")
for sym in syms:
    r = compute_fusion_decision(sym, klines_data[sym])
    if r:
        print(sym, "| action:", r["action"], "| dir:", round(r["signal_direction"], 3),
              "| conf:", round(r["confidence"], 3), "| regime:", r["regime"])
    else:
        print(sym, "| 融合失败(None)")

# ③ funding 方向映射实测（0.01% 费率不应为 -1.0）
from backend.services.factor_engine.factor_signal_generator import _funding_rate_direction
print("=== funding 方向 ===")
for v in (0.005, 0.01, 0.02, 0.05, 0.1):
    print(f"rate={v}% -> direction={_funding_rate_direction(v):+.3f}")
