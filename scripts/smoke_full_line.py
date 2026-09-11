# -*- coding: utf-8 -*-
"""全线冒烟测试：验证本轮全部修复是否生效。"""
import json
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

# 先 import connection 触发 .env 加载（arbiter 依赖 env）
from backend.database.connection import SessionLocal  # noqa: F401

print("=" * 60)
print("[P0-3] FUSION_PWIN_TIERS 分档泄漏修复")
from backend.services.decision_fusion_arbiter import _pwin_tiers, decide_scalp

tiers = _pwin_tiers()
print("  当前分档:", tiers)
assert all(t[0] >= 0.55 for t in tiers), "仍存在 <0.55 的负 EV 档!"
print("  ✓ 0.50-0.55 负 EV 档已移除")
d1 = decide_scalp(pwin=0.62, factor_score=60, direction="long", tp_pct=0.01, sl_pct=0.005)
d2 = decide_scalp(pwin=0.56, factor_score=60, direction="long", tp_pct=0.01, sl_pct=0.005)
d3 = decide_scalp(pwin=0.52, factor_score=60, direction="long", tp_pct=0.01, sl_pct=0.005)
print(f"  pwin=0.62 → {d1.action} size={d1.size_mult}")
print(f"  pwin=0.56 → {d2.action} size={d2.size_mult}")
print(f"  pwin=0.52 → {d3.action} size={d3.size_mult} tags={d3.tags.get('path', d3.source)}")
assert d1.allowed and d1.size_mult >= 0.5, "0.62 应放行"
assert not d3.allowed or d3.size_mult <= 0.25, "0.52 不应按常规档入场"
print("  ✓ 分档行为符合预期")

print("=" * 60)
print("[P0-1] 冷池晋升候选登记")
try:
    store = json.loads(open("D:/001Alpha/Hyper-Alpha-Arena/data/discovered_factors.json", encoding="utf-8").read())
    cold = [k for k, v in store.items() if k.startswith("t326:cold_")]
    print(f"  cold_* 候选登记数: {len(cold)}")
    print("  样例:", [k.split(":")[1] for k in cold[:5]])
    assert len(cold) >= 20, f"冷池候选应≥20，实际 {len(cold)}"
except Exception as e:
    import traceback
    traceback.print_exc()
    raise

print("=" * 60)
print("[P1-4] LLM 因子引导接入活跃集 SSOT")
import pandas as pd
from backend.services.ai_decision_integration import build_factor_guidance_for_prompt

fake_k = pd.DataFrame({
    "open": [100 + i * 0.1 for i in range(60)],
    "high": [101 + i * 0.1 for i in range(60)],
    "low": [99 + i * 0.1 for i in range(60)],
    "close": [100.5 + i * 0.1 for i in range(60)],
    "volume": [1000.0] * 60,
})
text = build_factor_guidance_for_prompt(["BTC"], {"BTC": fake_k}, {"BTC": 100.5})
print("  输出片段:")
for line in text.splitlines()[-8:]:
    print("   ", line[:100])
assert "量化层活跃因子集" in text, "引导未接入活跃集"
print("  ✓ 活跃集已进入 LLM 引导")

print("=" * 60)
print("[回归] 在线 API 冒烟")
import time as _t
import requests as _req


def _get_json(url, timeout=40, retries=4):
    last = None
    for i in range(retries):
        try:
            # 禁用环境代理：import 后端模块会把 .env 的 HTTP_PROXY 写入
            # os.environ，requests 默认走代理 → localhost 请求被 Privoxy 拦成 500。
            r = _req.get(url, timeout=timeout, proxies={"http": None, "https": None})
            if r.status_code != 200 or not r.content:
                last = f"status={r.status_code} len={len(r.content)}"
            else:
                return r.json()
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:80]}"
        _t.sleep(3)
    raise RuntimeError(f"GET {url} failed after retries: {last}")


base = "http://127.0.0.1:8000"
r1 = _get_json(f"{base}/api/full-auto/tier-activity/fa_7e12e7a1b6")
print(f"  tier-activity: short={len(r1.get('short',[]))} mid={len(r1.get('mid',[]))} long={len(r1.get('long',[]))}")
assert len(r1.get("long", [])) > 0, "固定长线列不应为空"
r2 = _get_json(f"{base}/api/rebate/asterdex-points/summary")
print(f"  points: policy={r2.get('policy',{}).get('enabled')} reason={r2.get('policy',{}).get('reason')}")
r3 = _get_json(f"{base}/api/rebate/funding-matrix")
print(f"  funding-matrix: venues={r3.get('venue_count')} symbols={r3.get('symbol_count')} arb_status={'arb_status' in r3}")
assert "arb_status" in r3
r4 = _get_json(f"{base}/api/intelligence/trading-signal/BTC")
f = r4.get("funding") or {}
print(f"  intel: funding rate={f.get('rate')} pct={f.get('percentile')} regime={f.get('regime')}")
print("  ✓ 全部回归通过")

print("=" * 60)
print("SMOKE ALL PASSED")
