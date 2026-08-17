"""阶段3冒烟：① 瘦身审计保护集（factor_active_set 可交易行）② 清理决策文件读取 ③ PAPER 因子集合。"""
import os, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + "/../backend")
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

# ① 保护集
from backend.services.factor_engine.factor_slimming_audit import _load_tradable_factor_ids
protected = _load_tradable_factor_ids()
print("① 瘦身审计保护集（factor_active_set 可交易行）:", sorted(protected))

# ② 清理决策文件读取（未跑过清理则为空集——空而非假数据）
from backend.services.factor_cleanup_service import (
    get_rejected_factor_ids, get_low_signal_factor_ids,
)
print("② 清理决策 rejected:", sorted(get_rejected_factor_ids()))
print("② 清理决策 low_signal:", sorted(get_low_signal_factor_ids()))

# ③ PAPER 因子集合（权重上限用）
from backend.services.scalp.scalp_factor_exclude import get_paper_factor_ids
print("③ PAPER 因子集合:", sorted(get_paper_factor_ids()))

# ④ 恢复函数可用性
from backend.services.factor_engine.factor_slimming_audit import restore_quarantined_factors
print("④ restore_quarantined_factors 可用:", restore_quarantined_factors())
