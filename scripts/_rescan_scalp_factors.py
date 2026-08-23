# -*- coding: utf-8 -*-
"""F：scalp 档（1h/15m）因子条件评价重扫 → 晋升/拒绝 → custom_factor_store。

用法: python scripts/_rescan_scalp_factors.py [limit] [--ids id1,id2,...]
- limit: 最多处理 N 个因子（默认全部）
- --ids: 只处理指定 factor_id（逗号分隔）
"""
import io
import json
import sys
import time

sys.path.insert(0, ".")


def _scalp_interval(factor_id: str) -> str:
    if str(factor_id).endswith("15m"):
        return "15m"
    return "1h"


def main() -> None:
    from backend.services.factor_engine.custom_factor_store import custom_factor_store
    from backend.services.coin_select_platform_service import resolve_admin_tenant_id

    tid = resolve_admin_tenant_id()
    print(f"tenant={tid}")

    with io.open("data/discovered_factors.json", encoding="utf-8") as f:
        disc = json.load(f)

    # 筛选 scalp 档候选（1h/15m 后缀）
    ids_arg = ""
    limit = 0
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--ids" and i + 1 < len(argv):
            ids_arg = argv[i + 1]
        elif a.isdigit():
            limit = int(a)
    only_ids = {x.strip() for x in ids_arg.split(",") if x.strip()} if ids_arg else None

    scalp_entries = []
    for key, v in disc.items():
        fid = str(v.get("factor_id") or "")
        if not (fid.endswith("1h") or fid.endswith("15m")):
            continue
        if only_ids and fid not in only_ids:
            continue
        formula = str(v.get("formula") or "").strip()
        if not formula:
            continue
        scalp_entries.append((key, v, formula))

    # 排序：rejected 优先（最需要复评），15m 优先（实盘主频）
    scalp_entries.sort(key=lambda t: (t[1].get("status") != "rejected", not str(t[1].get("factor_id")).endswith("15m"), str(t[1].get("factor_id"))))
    if limit:
        scalp_entries = scalp_entries[:limit]
    print(f"rescan targets: {len(scalp_entries)}")

    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

    report = []
    t0 = time.time()
    for idx, (key, entry, formula) in enumerate(scalp_entries, 1):
        fid = str(entry.get("factor_id"))
        interval = _scalp_interval(fid)
        t1 = time.time()
        try:
            sr = factor_backtest_scorer.score_formula(
                fid, formula, interval=interval,
                with_conditional=True, count_trial=False,
            )
        except Exception as e:
            print(f"[{idx}/{len(scalp_entries)}] {fid} ERROR {type(e).__name__}: {e}")
            report.append({"factor_id": fid, "error": str(e), "elapsed_s": round(time.time() - t1, 1)})
            continue
        elapsed = time.time() - t1
        scores = {
            "ic_mean": sr.ic_mean, "icir": sr.icir,
            "ic_decay_halflife": sr.ic_decay_halflife,
            "oos_net_return": sr.oos_net_return, "oos_sharpe": sr.oos_sharpe,
            "oos_win_rate": sr.oos_win_rate, "oos_trades": sr.oos_trades,
        }
        row = {
            "factor_id": fid, "interval": interval,
            "grade": sr.grade, "admitted": bool(sr.admitted),
            "eval_mode": getattr(sr, "eval_mode", None),
            "reason": (getattr(sr, "reason", "") or "")[:200],
            "scores": scores,
            "conditional": getattr(sr, "conditional", None),
            "elapsed_s": round(elapsed, 1),
        }
        # 晋升写回
        if sr.admitted:
            try:
                reg = custom_factor_store.register(
                    name=fid.replace("ai_", ""),
                    formula=formula,
                    category=str(entry.get("category") or "alpha101"),
                    source=str(entry.get("source") or "scalp_rescan"),
                    tenant_id=tid,
                    extra={"horizon": "scalp", "timeframe": interval},
                )
                extra_upd = {"horizon": "scalp", "timeframe": interval}
                if getattr(sr, "eval_mode", None) == "conditional":
                    extra_upd["role"] = "paper"
                    extra_upd["eval_mode"] = "conditional"
                ok = custom_factor_store.update_scores(
                    fid, grade=sr.grade, scores=scores, status="active",
                    tenant_id=tid, extra_update=extra_upd,
                )
                row["promoted"] = bool(ok)
                # 同步 discovered_factors.json
                entry["status"] = "active"
                entry["grade"] = sr.grade
                entry["scores"] = scores
                entry["scored_at"] = time.time()
                entry.setdefault("extra", {}).update(extra_upd)
                entry["extra"]["eval_mode"] = getattr(sr, "eval_mode", None)
            except Exception as e:
                row["promoted"] = False
                row["promote_err"] = str(e)
        else:
            # 拒绝同步
            entry["status"] = "rejected" if entry.get("status") != "candidate" else "candidate"
            entry["grade"] = sr.grade
            entry["scores"] = scores
            entry["scored_at"] = time.time()
            entry["extra"] = dict(entry.get("extra") or {})
            entry["extra"]["eval_mode"] = getattr(sr, "eval_mode", None)
        report.append(row)
        print(
            f"[{idx}/{len(scalp_entries)}] {fid} {interval} grade={sr.grade} "
            f"admitted={sr.admitted} eval={getattr(sr, 'eval_mode', None)} "
            f"IC={sr.ic_mean:.4f} OOS_sharpe={sr.oos_sharpe:.3f} {elapsed:.0f}s"
        )
        if idx % 5 == 0:
            with io.open("data/factor_rescan_scalp_20260823.json", "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)

    with io.open("data/discovered_factors.json", "w", encoding="utf-8") as f:
        json.dump(disc, f, ensure_ascii=False, indent=1)
    with io.open("data/factor_rescan_scalp_20260823.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    admitted = [r for r in report if r.get("admitted")]
    print(f"\nDONE total={len(report)} admitted={len(admitted)} elapsed={time.time()-t0:.0f}s")
    for r in admitted:
        print("  PROMOTED:", r["factor_id"], r["grade"], r["eval_mode"])


if __name__ == "__main__":
    main()
