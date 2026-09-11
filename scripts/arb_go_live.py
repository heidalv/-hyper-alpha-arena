# -*- coding: utf-8 -*-
"""
资金费套利转实盘 — go-live 脚本（幂等，默认 dry-run）。

前置条件（全部满足才真正生效）：
  1) .env 已设 FUNDING_ARB_ENABLED=true（总开关，已就绪）
  2) /exchange 页已添加 asterdex 实盘凭证并绑定账户 188（对冲腿 binance 已有）
  3) 本脚本 --apply：flip arb_config.yaml default_mode=live +
     会话 fa_185f162052.arb_enabled=true + 恢复会话 + 提示重启后端

用法：
  python scripts/arb_go_live.py            # 只检查，不改任何东西
  python scripts/arb_go_live.py --apply    # 执行翻转
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

SESSION_ID = "fa_185f162052"
YAML_PATH = Path("D:/001Alpha/Hyper-Alpha-Arena/backend/config/arb_config.yaml")


def check_credentials() -> dict:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from sqlalchemy import text

    with system_identity(), SessionLocal() as db:
        rows = db.execute(text(
            "SELECT exchange, enabled, testnet, account_id FROM exchange_credentials "
            "WHERE enabled = true AND testnet = false"
        )).fetchall()
    by_ex = {}
    for ex, en, tn, acc in rows:
        by_ex.setdefault(ex, []).append(acc)
    return by_ex


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行翻转")
    args = ap.parse_args()

    print("== 1) 实盘凭证检查 ==")
    creds = check_credentials()
    for ex in ("asterdex", "binance"):
        accounts = creds.get(ex, [])
        print(f"  {ex}: {'OK account=' + str(accounts) if accounts else '缺失!'}")
    ok_legs = bool(creds.get("asterdex")) and bool(creds.get("binance"))
    print("  双腿凭证:", "齐备 ✓" if ok_legs else "缺失 ✗（先去 /exchange 页添加 asterdex 凭证并绑定账户）")

    print("== 2) 引擎模式（arb_config.yaml）==")
    yaml_text = YAML_PATH.read_text(encoding="utf-8")
    cur_mode = "live" if "default_mode: live" in yaml_text else "paper"
    print(f"  current default_mode: {cur_mode}")

    print(f"== 3) 会话 {SESSION_ID} 状态 ==")
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from sqlalchemy import text

    with system_identity(), SessionLocal() as db:
        row = db.execute(text(
            "SELECT status, arb_enabled, trading_mode FROM full_auto_sessions "
            "WHERE session_id = :sid"
        ), {"sid": SESSION_ID}).fetchone()
        print(f"  status={row[0]} arb_enabled={row[1]} trading_mode={row[2]}")

        if args.apply:
            if not ok_legs:
                print("\n[ABORT] 双腿凭证不齐，拒绝翻转（先加 asterdex key）")
                return
            # 1) yaml default_mode → live（幂等）
            if "default_mode: live" not in yaml_text:
                new_yaml = yaml_text.replace(
                    "default_mode: paper          # paper | live",
                    "default_mode: live           # paper | live  [2026-09 转实盘]",
                )
                YAML_PATH.write_text(new_yaml, encoding="utf-8")
                print("  [APPLY] arb_config.yaml default_mode → live")
            else:
                print("  [SKIP] default_mode 已是 live")
            # 2) 会话 arb_enabled=true
            db.execute(text(
                "UPDATE full_auto_sessions SET arb_enabled = true WHERE session_id = :sid"
            ), {"sid": SESSION_ID})
            db.commit()
            print("  [APPLY] session arb_enabled → true")
            # 3) 恢复会话（若 paused）
            if row[0] != "running":
                db.execute(text(
                    "UPDATE full_auto_sessions SET status = 'running' WHERE session_id = :sid"
                ), {"sid": SESSION_ID})
                db.commit()
                print("  [APPLY] session status → running")
            print("\n[NEXT] 重启后端生效：scripts\\stop-dev.ps1 -Ports 8000,8001（看门狗自动拉起）")
            print("[NEXT] 观察日志：logs\\backend.log 搜 [ArbTick]，确认 mode=live 且 executed 正常")
        else:
            print("\n[dry-run] 未做任何修改；确认凭证齐备后运行: python scripts/arb_go_live.py --apply")


if __name__ == "__main__":
    main()
