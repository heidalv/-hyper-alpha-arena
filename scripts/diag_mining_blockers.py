"""诊断 2026-08-16 挖矿面板三卡点（只读，不写库）。

运行: backend\\.venv\\Scripts\\python.exe scripts\\diag_mining_blockers.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    import numpy as np
    import pandas as pd

    # ── 1) 进化深度分档（修复后应回到周期默认，不再被 mining_boost 覆盖成 190 天）──
    from backend.services.evolution.factor_evolution_loop import (
        _lookback_for_period,
        _split_days_for_period,
    )
    print("== 1) 进化切分窗口（修复后预期: 15m=50d/4850根, 5m=50d/14450根, 4h=270d/1670根） ==")
    for p in ("1m", "5m", "15m", "30m", "1h", "4h", "1d"):
        td, vd, ted = _split_days_for_period(p)
        print(f"   {p}: split=({td},{vd},{ted})  need_bars={_lookback_for_period(p)}")

    # ── 2) 中线 lookback 分档 ──
    from backend.services.factor_engine.factor_backtest_scorer import (
        FactorBacktestScorer,
        midlong_lookback_for,
    )
    print("== 2) 中线打分 lookback ==",
          {tf: midlong_lookback_for(tf) for tf in ("4h", "1d")})

    # ── 3) registry 因子在 BTC 4h/1d 上的实际表现 ──
    from backend.services.factor_engine.factor_calculator import FactorCalculator

    scorer = FactorBacktestScorer()
    calc = FactorCalculator()
    for tf in ("4h", "1d"):
        lb = midlong_lookback_for(tf)
        klines = scorer._load_klines("BTC", tf, lb)
        n = len(klines) if klines else 0
        print(f"== 3) BTC {tf} klines: {n} (lookback={lb})")
        if not klines:
            continue
        df = pd.DataFrame(klines)
        print("   cols:", sorted(df.columns)[:24])
    # ── 4) 真实评分路径：_score_one_registry_factor（仅 BTC，只读）──
    from backend.services.factor_engine.midlong_registry_factors import _score_one_registry_factor
    print("== 4) registry 因子真实评分路径（BTC, 4h） ==")
    for fid in ("oi_delta", "taker_ratio", "cvd_ratio",
                "supertrend", "ema_trend", "sma_cross"):
        try:
            r = _score_one_registry_factor(f"{fid}@4h", fid, "4h", symbols=("BTC",))
            if r:
                print(f"   {fid}: grade={r.get('grade')} reason={r.get('reason')} "
                      f"ic={r.get('ic_mean')} sharpe={r.get('oos_sharpe')} trades={r.get('oos_trades')}")
            else:
                print(f"   {fid}: None")
        except Exception as e:  # noqa: BLE001
            print(f"   {fid}: EXC {type(e).__name__}: {e}")

    # ── 4b) supertrend 死因细查（滚动重算 → 评估器 → walk-forward 逐段）──
    print("== 4b) supertrend 死因细查（BTC 4h） ==")
    from backend.services.factor_engine.midlong_registry_factors import _rolling_recompute
    from backend.services.factor_engine.factor_evaluator import get_factor_evaluator
    lb4 = midlong_lookback_for("4h")
    k4 = scorer._load_klines("BTC", "4h", lb4)
    df4 = pd.DataFrame(k4)
    fwd4 = 6
    for fid in ("supertrend", "ema_trend", "sma_cross"):
        vals = _rolling_recompute(calc, fid, df4, "BTC", "4h", fwd4)
        fin = int(np.isfinite(vals).sum())
        ev = get_factor_evaluator(forward_period=fwd4)
        closes4 = df4["close"].astype(float).to_numpy()
        rep = ev.evaluate_factor(fid, pd.Series(vals), pd.Series(closes4), forward_period=fwd4)
        bt = scorer._walk_forward_backtest(
            vals, closes4, fwd4, 0.0021,
            funding_per_hold=0.0001 * (fwd4 * 4.0 / 8.0), bars_per_year=2190,
        )
        uniq = dict(zip(*np.unique(vals[np.isfinite(vals)], return_counts=True)))
        print(f"   {fid}: rolling_finite={fin} uniq={uniq} data_points={rep.data_points} "
              f"ic={getattr(rep, 'ic_mean', None)} trades={bt.get('trades')} "
              f"sharpe={bt.get('sharpe')}")
    # supertrend 单点路径直测（全窗口一次计算 + 滑动切片）
    _f = calc.registry.get("supertrend", params=None)
    try:
        _f.validate_data(df4)
        _proc = _f.preprocess_data(df4)
        _res = np.asarray(_f.calculate(_proc), dtype=float)
        print(f"   supertrend 全窗口单次: type={type(_proc).__name__} "
              f"len={len(_res)} last={_res[-1] if len(_res) else None}")
    except Exception as e:  # noqa: BLE001
        print("   supertrend 全窗口单次 EXC:", type(e).__name__, e)
    _hits = 0
    for t in range(80, len(df4), 6):
        _sub = df4.iloc[: t + 1]
        try:
            _f.validate_data(_sub)
            _p = _f.preprocess_data(_sub)
            _r = np.asarray(_f.calculate(_p), dtype=float)
            if len(_r) and abs(_r[-1]) > 0.5:
                _hits += 1
        except Exception:
            pass
    print(f"   supertrend 滚动切片 |±1| 命中: {_hits}/{(len(df4) - 80) // 6}")

    # ── 5) 订单流历史数据深度 ──
    print("== 5) 订单流历史深度（Market 库） ==")
    try:
        from sqlalchemy import text as _sa_text
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            q2 = db.execute(_sa_text(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM market_trades_aggregated "
                "WHERE symbol='BTC'"
            )).first()
            print(f"   trades_agg BTC: rows={q2[0]} span={q2[1]}..{q2[2]}")
            q3 = db.execute(_sa_text(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM market_asset_metrics "
                "WHERE symbol='BTC' AND open_interest IS NOT NULL"
            )).first()
            print(f"   asset_metrics OI BTC: rows={q3[0]} span={q3[1]}..{q3[2]}")
            q4 = db.execute(_sa_text(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM market_orderbook_snapshots "
                "WHERE symbol='BTC'"
            )).first()
            print(f"   orderbook BTC: rows={q4[0]} span={q4[1]}..{q4[2]}")
    except Exception as e:  # noqa: BLE001
        print("   DB 查询失败:", e)


if __name__ == "__main__":
    main()
