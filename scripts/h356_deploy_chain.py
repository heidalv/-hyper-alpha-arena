# -*- coding: utf-8 -*-
"""H356 部署链：宇宙扩容部署 + worker 重启（供计划任务 DSH_HFT_H356_DEPLOY 调用）。

P2 判定（12:38）→ 本任务（13:00）：h356 部署 → h218 重启 → 回填新币 mid_hist。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import h218_restart_worker as rst  # noqa: E402
import h356_universe_trial as t  # noqa: E402


def main() -> int:
    import datetime as _dt
    import traceback
    _log = ROOT / "logs" / "h356_deploy_chain.log"
    _t0 = _dt.datetime.now()
    rc = -1
    try:
        import psycopg
        from h356_universe_trial import read_env_dsn
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                rc = t.deploy(cur)
                c.commit()
                if rc:
                    return rc
        # 重启 worker（回填新币 mid_hist + 热采用宇宙）
        rc = rst.main()
        return rc
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        _line = (f"{_t0:%Y-%m-%d %H:%M:%S} rc={rc} "
                 f"dur={( _dt.datetime.now() - _t0).total_seconds():.0f}s")
        _log.parent.mkdir(parents=True, exist_ok=True)
        with open(_log, "a", encoding="utf-8") as f:
            f.write(_line + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
