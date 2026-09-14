# -*- coding: utf-8 -*-
"""RTGNN-lite 真实数据冒烟：本地 MARKET_DATABASE_URL 快照上跑影子训练 + Rank IC 报告。

只读；用小宇宙/小纪元（本地快照仅 7 币 × ~4 天 15m，仅证明端到端链路，
不代表生产质量 —— 生产需要 P0 数据积累 N≥50 × 6 个月）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("GRAPH_RANK_MIN_ASSETS", "5")
os.environ.setdefault("GRAPH_RANK_ENABLED", "true")
os.environ.setdefault("GRAPH_RANK_PERIOD", "15m")
os.environ.setdefault("GRAPH_RANK_COUNT", "288")
os.environ.setdefault("GRAPH_RANK_HORIZON", "6")
os.environ.setdefault("GRAPH_RANK_LOOKBACK", "24")
os.environ.setdefault("GRAPH_RANK_EPOCHS", "8")
os.environ.setdefault("GRAPH_RANK_BATCH_SIZE", "8")
os.environ.setdefault("GRAPH_RANK_DEVICE", "cpu")

from backend.services.graph_rank.service import train_shadow_report  # noqa: E402

SYMS = ["BTC", "ETH", "SOL", "BNB", "XPL", "VIRTUAL", "ASTER"]


def main() -> None:
    t0 = time.time()
    model, report, std = train_shadow_report(
        SYMS, period="15m", count=288, horizon=6, lookback=24,
        epochs=8, batch_size=8, lr=2e-3, patience=3, lambda_rank=0.5, seed=42,
    )
    dt = time.time() - t0
    if model is None:
        print(f"训练未就绪（fail-closed）：{report.note if report else 'unknown'}")
        return
    print(f"耗时 {dt:.1f}s")
    print(f"symbols      : {report.symbols}")
    print(f"train_ic     : {report.train_ic:.4f}")
    print(f"val_ic/icir  : {report.val_ic:.4f} / {report.val_icir:.4f}")
    print(f"test_ic/icir : {report.test_ic:.4f} / {report.test_icir:.4f}")
    print(f"epochs_used  : {report.epochs_used}")
    print(f"样本量(train/val/test): {report.n_train_samples}/{report.n_val_samples}/{report.n_test_samples}")
    print(f"note         : {report.note}")
    print("提醒：小样本冒烟仅验证链路；Rank IC 数值无统计意义（N=7 且 ~4 天）")


if __name__ == "__main__":
    main()
