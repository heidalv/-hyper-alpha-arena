import os, sys, json
sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv
load_dotenv('.env', override=False)
from backend.services.mlto.decision_hub import fuse_signals
from backend.services.mlto.types import Signal
from backend.services.mlto.hub_decision_log import _llm_qual_value, _fw_mean
sigs = [
    Signal("orch_long_bias", 0.60, 0.9, "framework"),
    Signal("quant_alignment", 0.55, 0.9, "framework"),
    Signal("entry_timing", 0.50, 0.9, "framework"),
    Signal("thesis_health", 0.50, 0.85, "framework"),
    Signal("analyst_consensus", 0.50, 0.7, "framework"),
    Signal("feedback_loop", 0.50, 0.8, "framework"),
    Signal("llm_qual", 0.80, 0.5, "llm"),
]
hub = fuse_signals(sigs, tier="long")
print("hub.direction =", repr(hub.direction))
print("hub.dir_src =", repr(hub.dir_src))
print("hub.action =", hub.action, "adjusted =", round(hub.adjusted,3), "readiness =", hub.open_readiness)
print("_llm_qual_value =", _llm_qual_value(sigs))
print("_fw_mean =", _fw_mean(sigs))
