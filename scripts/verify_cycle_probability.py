#!/usr/bin/env python3
"""周期方向概率引擎 —— 落地验收脚本。

检查项（PASS/FAIL）：
  1. 三个 tier 的概率模型文件是否存在且可加载（prior/likelihood/校准齐全）。
  2. 每个模型的校准指标是否合理（Brier < 三态随机基线 0.667；三态准确率 > 0.33 随机基线）。
  3. 引擎 estimate 输出是否为合法概率分布（三值和≈1、均在 [0,1]）。
  4. runtime indicators / 快照特征提取能否喂进引擎产出结果。
  5. 证据链是否注入了 cycle_prob_* fact（swing→mid, trend→long）。
  6. 门禁配置项与自适应 governor 同步是否可用（不实际改变高优先级意图）。

运行：
  backend\\.venv\\Scripts\\python.exe scripts\\verify_cycle_probability.py
若模型缺失，先跑：
  backend\\.venv\\Scripts\\python.exe -m backend.services.cycle_direction_probability
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass


class Reporter:
    def __init__(self) -> None:
        self.results = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(ok)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" - {detail}" if detail else ""))

    def summary(self) -> int:
        p = sum(1 for r in self.results if r)
        f = len(self.results) - p
        print(f"\n==== 验收结果 PASS={p} FAIL={f} ====")
        return 0 if f == 0 else 1


def main() -> int:
    rep = Reporter()
    from backend.services.cycle_direction_probability import (
        cycle_probability_engine as engine,
        extract_features_from_indicators,
        extract_tier_features_from_snapshot,
        TIER_PRIMARY,
    )

    # 1 + 2: 模型加载与校准
    for tier in ("short", "mid", "long"):
        model = engine._load_if_needed(tier)
        rep.check(f"{tier} 概率模型可加载", model is not None,
                  "缺失请先运行训练" if model is None else f"tf={model.timeframe}")
        if model is None:
            continue
        calib = model.calibration or {}
        brier = calib.get("brier")
        acc = calib.get("accuracy")
        rep.check(f"{tier} Brier < 0.667(随机基线)",
                  brier is not None and brier < 0.667, f"brier={brier}")
        rep.check(f"{tier} 三态准确率 > 0.33(随机基线)",
                  acc is not None and acc > 0.33, f"accuracy={acc}")
        rep.check(f"{tier} 校准质量字段存在", "quality" in calib, f"quality={calib.get('quality')}")

    # 3: 概率分布合法性
    feats = {"rsi": 70, "adx": 30, "ema_align": 1, "di_diff": 20, "macd_sign": 1,
             "atr_pct": 0.02, "vol_ratio": 1.5, "mom": 0.02, "hh_hl": 2}
    for tier in ("short", "mid", "long"):
        r = engine.estimate(tier, feats)
        if not r.available:
            rep.check(f"{tier} estimate 可用", False, "模型未加载")
            continue
        s = r.prob_up + r.prob_down + r.prob_range
        legal = abs(s - 1.0) < 1e-6 and all(0 <= p <= 1 for p in (r.prob_up, r.prob_down, r.prob_range))
        rep.check(f"{tier} 输出为合法概率分布", legal, f"sum={s:.4f} dir={r.direction} q={r.calibration_quality}")

    # 4: 特征提取
    flat = {"rsi": 60, "adx": 25, "ema_9": 105, "ema_21": 102, "ema_50": 100,
            "macd_hist": 0.3, "atr": 150, "close": 10000, "vol_ratio": 1.2,
            "plus_di": 26, "minus_di": 15, "rsi_4h": 55, "adx_4h": 22,
            "ema_9_4h": 205, "ema_21_4h": 202, "ema_50_4h": 200, "atr_4h": 400,
            "short_rsi": 68, "short_macd_hist": 0.1, "short_ema_trend": 0.002}
    f_flat = extract_features_from_indicators(flat)
    rep.check("扁平 indicators 特征提取非空",
              any(v is not None for v in f_flat.values()), f"{sum(v is not None for v in f_flat.values())} 项")
    for tier in ("short", "mid", "long"):
        ft = extract_tier_features_from_snapshot(flat, tier)
        rep.check(f"{tier} 分周期快照特征提取非空",
                  any(v is not None for v in ft.values()),
                  f"{sum(v is not None for v in ft.values())} 项")

    # 5: 证据链注入
    try:
        from backend.services.agent_evidence_builder import build_swing_evidence, build_trend_evidence
        envs = {"BTC": {"indicators_1h": flat, "indicators_4h": flat,
                        "orchestrator": {"mid_bias": "bullish", "mid_confidence": 0.6}}}
        sids = {f.id for f in build_swing_evidence("BTC", envs)}
        tids = {f.id for f in build_trend_evidence("BTC", envs)}
        rep.check("swing 证据含 cycle_prob_dir_mid", "cycle_prob_dir_mid" in sids)
        rep.check("trend 证据含 cycle_prob_dir_long", "cycle_prob_dir_long" in tids)
    except Exception as e:
        rep.check("证据链注入", False, f"异常: {e}")

    # 6: 门禁配置 + governor 同步
    try:
        from backend.config import settings
        rep.check("门禁配置项存在",
                  hasattr(settings, "CYCLE_PROB_GATE_ENABLED")
                  and hasattr(settings, "CYCLE_PROB_GATE_MIN_CALIBRATION"),
                  f"enabled={settings.CYCLE_PROB_GATE_ENABLED}")
        from backend.services.cycle_direction_probability import sync_calibration_to_governor
        res = sync_calibration_to_governor()
        rep.check("governor 校准同步可执行", isinstance(res, dict), f"{res}")
    except Exception as e:
        rep.check("门禁/governor 配置", False, f"异常: {e}")

    # 7: 仲裁接入（orchestrator + coordinator 冲突分支）
    try:
        from backend.services.multi_timeframe_orchestrator import MultiTimeframeOrchestrator
        from backend.services.strategy_coordinator import StrategyCoordinator, MarketEnvironment
        rep.check("orchestrator 概率仲裁方法存在",
                  hasattr(MultiTimeframeOrchestrator, "_cycle_prob_arbitration"))
        rep.check("coordinator 概率仲裁方法存在",
                  hasattr(StrategyCoordinator, "_cycle_prob_tier_lean"))

        class _D:
            pass
        env = MarketEnvironment()
        env.symbol = "BTC"; env.current_price = 10000; env.m1h_rsi = 60
        env.m1h_ema20 = 101; env.m1h_ema50 = 100; env.atr_value = 150
        lean, active = StrategyCoordinator._cycle_prob_tier_lean(_D(), env, "mid")
        rep.check("coordinator tier lean 输出合法",
                  isinstance(lean, float) and isinstance(active, bool),
                  f"lean={lean:.4f} active={active}")
    except Exception as e:
        rep.check("仲裁接入", False, f"异常: {e}")

    return rep.summary()


if __name__ == "__main__":
    sys.exit(main())
