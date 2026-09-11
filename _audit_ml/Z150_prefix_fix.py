"""Z150: 补齐 SYSTEM_PREFIXES（此前漏了 ANOMALY_/ONCHAIN_/HERMES_/NSGA2_/REENTRY_/MARKET_SCANNER_/TIER_/KLINE_ 等命名空间），
然后批量登记新暴露出的"确实会读"的键，剩下的即是死键。"""
from __future__ import annotations
import re, sys
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"backend"/"scripts"))
import audit_config_effective as ace

p = ROOT / "backend" / "config" / "env_registry.py"
src = p.read_text(encoding="utf-8")
add_pref = ["ANOMALY_", "ONCHAIN_", "HERMES_", "NSGA2_", "REENTRY_", "MARKET_SCANNER_",
            "TIER_", "KLINE_", "COLD_", "MM_", "MIDLONG_", "LOOP_", "SESSION_", "ACCOUNT_"]
anchor = '    "MASTER_", "EXIT_", "FACTOR_", "PURGE_",\n)'
assert anchor in src, "SYSTEM_PREFIXES 锚点未找到"
new_block = '    "MASTER_", "EXIT_", "FACTOR_", "PURGE_",\n'
new_block += '    # [§63 补齐] 此前遗漏的命名空间（未覆盖 ⇒ 这些前缀下的死键/拼错键**不会被校验发现**）\n'
for i in range(0, len(add_pref), 5):
    new_block += "    " + ", ".join(f'"{x}"' for x in add_pref[i:i+5]) + ",\n"
new_block += ")"
src = src.replace(anchor, new_block, 1)
p.write_text(src, encoding="utf-8")
print("已补齐 SYSTEM_PREFIXES:", len(add_pref), "个")

# 重新导入并统计
import importlib
import backend.config.env_registry as reg
importlib.reload(reg)
ks = reg.find_unknown_flags()
print(f"补齐前缀后未登记 {len(ks)} 个：")
for k in ks:
    print("   ", k)
