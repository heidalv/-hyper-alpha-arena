# -*- coding: utf-8 -*-
"""训练入口：python -m backend.services.hybrid_scoring.train_cli

从 alpha_market 构建流动性 top 宇宙的 1d 面板 → 时间切分训练 lambdarank → 落盘。
"""
from __future__ import annotations

import argparse
import json
import sys

from backend.services.hybrid_scoring import config, features, kpanel, ltr


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="混合打分中心 · 通道A LTR 训练")
    ap.add_argument("--universe", type=int, default=config.train_universe_limit(),
                    help="训练宇宙币数（按流动性 top）")
    ap.add_argument("--bars", type=int, default=400, help="每币装载K线根数")
    ap.add_argument("--period", default=config.PANEL_PERIOD)
    ap.add_argument("--min-symbols", type=int, default=20, help="有效币数下限")
    args = ap.parse_args(argv)

    print(f"[train_cli] 宇宙：流动性 top {args.universe}（{args.period}）…")
    syms = kpanel.top_liquid_symbols(args.universe, days=60, period=args.period)
    print(f"[train_cli] 宇宙就绪 n={len(syms)}: {syms[:10]} …")
    panel = features.build_panel(syms, period=args.period, bars=args.bars)
    n_syms = 0 if panel is None or panel.empty else panel.index.get_level_values("symbol").nunique()
    print(f"[train_cli] 面板：rows={0 if panel is None else len(panel)} "
          f"dates={0 if panel is None else panel.index.get_level_values('date').nunique()} symbols={n_syms}")
    if n_syms < args.min_symbols:
        print(f"[train_cli] 有效币数 {n_syms} < {args.min_symbols}，中止（防窄宇宙假训练）")
        return 2
    try:
        metrics = ltr.train(panel)
    except ValueError as e:
        print(f"[train_cli] 训练拒绝：{e}")
        return 3
    print("[train_cli] 完成：", json.dumps(metrics, ensure_ascii=False))
    print(f"[train_cli] 模型 → {config.model_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
