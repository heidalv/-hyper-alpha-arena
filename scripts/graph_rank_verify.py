# -*- coding: utf-8 -*-
"""RTGNN 迁移全链路体检（生产配置，只读）。

用法（项目根目录）：python scripts/graph_rank_verify.py

检查项：
1. 开关状态（.env/settings 实际生效值）
2. RTGNN-lite 模型加载与分数提供（graph_score_for_symbols）
3. 零训练试点信号（lead/dm）覆盖
4. CoinRank 初排的图信号融合（rank_universe 抽样 explain）
5. 周度重训 cron 是否注册（backend.log 检查，尽力而为）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _flag(name, default):
    try:
        from backend.config import settings as s
        return getattr(s, name, default)
    except Exception:
        return os.getenv(name, default)


def main() -> None:
    print("== 1. 开关状态 ==")
    flags = {
        "COIN_RANK_GRAPH_SIGNAL_ENABLED": "false",
        "COIN_RANK_GRAPH_WEIGHT": "0.10",
        "GRAPH_RANK_ENABLED": "false",
        "AUTO_COIN_GRAPH_SCORE_ENABLED": "false",
        "AUTO_COIN_W_GRAPH": "0.0",
    }
    for k, d in flags.items():
        print(f"  {k} = {_flag(k, d)}")
    # UNIVERSE_* 定义在 universe_manager 模块级（settings 加载 .env 后才可见）
    try:
        from backend.services.alpha import universe_manager as um
        print(f"  UNIVERSE_DEDUP_DIRECTIONAL = {um.UNIVERSE_DEDUP_DIRECTIONAL}")
        print(f"  UNIVERSE_MAX_LEAD_REDUNDANT = {um.UNIVERSE_MAX_LEAD_REDUNDANT}")
    except Exception as e:
        print(f"  UNIVERSE_DEDUP_DIRECTIONAL 检查失败: {e}")

    print("\n== 2. RTGNN-lite 模型分数提供 ==")
    from backend.services.graph_rank.service import graph_score_for_symbols

    t0 = time.time()
    scores = graph_score_for_symbols(["BTC", "ETH", "SOL", "BNB", "ONDO", "HYPE", "INJ"])
    if scores:
        for k, v in sorted(scores.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<8} {v:.3f}")
        print(f"  （耗时 {time.time()-t0:.1f}s，模型分=RTGNN-lite；空=回退零训练试点）")
    else:
        print("  ⚠ 模型与试点信号均不可用（检查 GRAPH_RANK_ENABLED / COIN_RANK_GRAPH_SIGNAL_ENABLED）")

    print("\n== 3. 零训练试点信号（lead/dm）覆盖 ==")
    from backend.services.coin_rank.graph_signal import compute_graph_signals

    sig = compute_graph_signals(["BTC", "ETH", "SOL", "BNB", "ONDO", "HYPE", "INJ"], force=True)
    if sig:
        for k, v in list(sig.items())[:6]:
            print(f"  {k:<8} lead={v['lead']:.3f} dm={v['dm']:.3f}")
    else:
        print("  ⚠ 试点信号不可用")

    print("\n== 4. CoinRank 初排图信号融合（抽样）==")
    from backend.services.coin_rank.engine import rank_universe

    results = rank_universe(limit=6, apply_factor=False, apply_gate=False, apply_decay=False)
    for r in results[:6]:
        gs = f"{r.graph_score:.3f}" if r.graph_score is not None else "n/a"
        print(f"  {r.symbol:<10} composite={r.composite:.4f} graph={gs}")

    print("\n== 5. 周度重训 cron（backend.log 检查）==")
    try:
        log_path = "logs/backend.log"
        if os.path.exists(log_path):
            found = False
            for line in open(log_path, encoding="utf-8", errors="ignore"):
                if "graph_rank_retrain_weekly" in line and "Added" in line:
                    print("  ✅ 已注册:", line.strip()[:100])
                    found = True
                    break
            if not found:
                print("  ⚠ 当前日志未找到注册行（可能已轮转；重启后应有 'Added cron task graph_rank_retrain_weekly'）")
        else:
            print("  ⚠ 无 backend.log")
    except Exception as e:
        print(f"  ⚠ 日志检查失败: {e}")

    print("\n体检完成。")


if __name__ == "__main__":
    main()
