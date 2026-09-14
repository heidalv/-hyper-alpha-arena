# -*- coding: utf-8 -*-
"""RTGNN-lite 生产训练：用项目自产 K 线（生产库 1h，~8.5 个月 × 30+ 币）。

用法：python scripts/graph_rank_train.py [--epochs N] [--no-save]
- 从生产 MARKET_DATABASE_URL 装配面板（asterdex 1h，深度优先）
- walk-forward 训练 → 打印 Rank IC 报告 → 保存 data/graph_rank_model.pt
- 供 graph_score_for_symbols（AutoCoin V3 graph 维度）与每周重训任务复用
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.graph_rank.model import RTGNNConfig  # noqa: E402
from backend.services.graph_rank.service import train_shadow_report  # noqa: E402

# 生产宇宙：asterdex 1h 深度 ≥8 个月的币（2026-09-12 体检确认）
PROD_SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "APT", "UNI",
    "JUP", "TON", "AAVE", "INJ", "ONDO", "HYPE", "TAO", "FIL", "TIA", "FET",
    "ARB", "WIF", "AR", "PUMP", "VIRTUAL", "XPL", "ASTER", "ZEC", "WLFI", "PAXG",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--lookback", type=int, default=48)
    ap.add_argument("--horizon", type=int, default=6)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    if args.device != "auto":
        os.environ["GRAPH_RANK_DEVICE"] = args.device
    save_path = None if args.no_save else "data/graph_rank_model.pt"

    cfg = RTGNNConfig(
        d_model=32, n_heads=4, gru_hidden=32, gat_heads=4, dropout=0.1,
        lambda_rank=0.5, top_k=8, ema_alpha=0.2,
    )
    t0 = time.time()
    print(f"[GraphRankTrain] 开始训练 N={len(PROD_SYMBOLS)} epochs={args.epochs} "
          f"lookback={args.lookback} horizon={args.horizon}")
    model, report, std = train_shadow_report(
        PROD_SYMBOLS, period="1h", count=5000,
        horizon=args.horizon, lookback=args.lookback,
        epochs=args.epochs, batch_size=32, lr=2e-3, patience=8,
        lambda_rank=0.5, config=cfg, seed=42, save_path=save_path,
    )
    dt = time.time() - t0
    if model is None:
        print(f"[GraphRankTrain] 训练未就绪（fail-closed）: {report.note if report else '?'}")
        sys.exit(2)
    print(f"[GraphRankTrain] 完成 耗时 {dt:.1f}s")
    print(f"[GraphRankTrain] symbols: {len(report.symbols)} 币")
    print(f"[GraphRankTrain] train_ic={report.train_ic:.4f}")
    print(f"[GraphRankTrain] val_ic={report.val_ic:.4f} val_icir={report.val_icir:.4f}")
    print(f"[GraphRankTrain] test_ic={report.test_ic:.4f} test_icir={report.test_icir:.4f}")
    print(f"[GraphRankTrain] epochs_used={report.epochs_used} 样本={report.n_train_samples}/{report.n_val_samples}/{report.n_test_samples}")
    print(f"[GraphRankTrain] note: {report.note}")
    if save_path:
        print(f"[GraphRankTrain] 模型已保存: {save_path}")


if __name__ == "__main__":
    main()
