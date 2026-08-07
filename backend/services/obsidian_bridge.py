#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Obsidian Bridge — Real-time sync service between Hyper-Alpha-Arena
backend and the Obsidian vault.

This service monitors data directories for changes and converts
backend artifacts into Obsidian-friendly Markdown files with
YAML frontmatter, writing them into the vault.

Usage:
    python backend/services/obsidian_bridge.py          # standalone
    python backend/services/obsidian_bridge.py --once   # single sync

Design:
  - Polls source directories every SYNC_INTERVAL seconds
  - Tracks sync state in <vault>/.bridge_state.json
  - Reuses the conversion logic from tools/export_to_obsidian.py
  - Optionally calls Obsidian Local REST API to trigger re-index
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ──────────────────────────────────────────────────────────────
#  Paths (relative to the Hyper-Alpha-Arena project root)
# ──────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent  # backend/services/
PROJECT_ROOT = HERE.parent.parent       # Hyper-Alpha-Arena/
DEFAULT_DATA = PROJECT_ROOT / "data"
DEFAULT_VAULT = PROJECT_ROOT / "obsidian_vault"

DIR_REPORTS = "01-分析报告"
DIR_LESSONS = "02-交易教训"
DIR_HERMES = "03-Hermes进化"
DIR_DECISIONS = "04-Agent决策"

SYNC_INTERVAL = 30  # seconds between polls
OBSIDIAN_API_URL = "http://localhost:27123"  # Local REST API default port

STATE_FILE = "obsidian_bridge_state.json"  # relative to vault


# ──────────────────────────────────────────────────────────────
#  Reusable helpers (ported from tools/export_to_obsidian.py)
# ──────────────────────────────────────────────────────────────

def yaml_escape(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    if any(c in s for c in [':', '#', '[', ']', '{', '}', ',', '"', "'", '\n', '\r']):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            items = ", ".join(yaml_escape(x) for x in v)
            lines.append(f"{k}: [{items}]")
        else:
            lines.append(f"{k}: {yaml_escape(v)}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def safe_filename(name: str, maxlen: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return (name or "untitled")[:maxlen]


def ts_from_name(name: str) -> str:
    m = re.search(r'(\d{8})_(\d{6})', name)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return ""


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


# ──────────────────────────────────────────────────────────────
#  Sync State
# ──────────────────────────────────────────────────────────────

def _state_path(vault: Path) -> Path:
    return vault / STATE_FILE


def load_state(vault: Path) -> dict:
    sp = _state_path(vault)
    if sp.exists():
        try:
            return json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_sync": {}, "synced_files": {}}


def save_state(vault: Path, state: dict) -> None:
    sp = _state_path(vault)
    sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ──────────────────────────────────────────────────────────────
#  1. Analysis Reports Sync
# ──────────────────────────────────────────────────────────────

def parse_analysis_json(json_path: Path) -> dict | None:
    try:
        raw = json_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return None
    findings = data.get("findings") or []
    categories = sorted({f.get("category", "?") for f in findings if isinstance(f, dict)})
    return {
        "severity": data.get("severity", "unknown"),
        "domain": data.get("domain", "unknown"),
        "categories": categories,
        "finding_count": len(findings),
    }


def sync_reports(data_dir: Path, vault: Path, state: dict) -> int:
    src = data_dir / "opencode_reports"
    if not src.exists():
        return 0
    dst = ensure_dir(vault / DIR_REPORTS)
    synced_key = "reports"
    synced = state.setdefault("synced_files", {}).setdefault(synced_key, set())

    count = 0
    for md in sorted(src.glob("*.md")):
        if md.stem in synced:
            continue
        meta = parse_analysis_json(md.with_suffix(".json")) or {}
        date_str = ts_from_name(md.stem) or datetime.fromtimestamp(md.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        date_only = date_str.split(" ")[0] if date_str else ""

        fm_meta = {
            "type": "analysis",
            "severity": meta.get("severity", "unknown"),
            "domain": meta.get("domain", "unknown"),
            "date": date_only,
            "datetime": date_str,
            "categories": meta.get("categories", []),
            "finding_count": meta.get("finding_count", 0),
            "source": "opencode",
        }
        body = md.read_text(encoding="utf-8").lstrip("\ufeff").lstrip()
        content = frontmatter(fm_meta) + "\n" + f"# 📳 {md.stem}\n\n" + body
        out = dst / f"{md.stem}.md"
        out.write_text(content, encoding="utf-8")
        synced.add(md.stem)
        count += 1

    if count:
        print(f"  [sync] 分析报告: {count} new files")
    return count


# ──────────────────────────────────────────────────────────────
#  2. Trading Lessons Sync
# ──────────────────────────────────────────────────────────────

def sync_lessons(data_dir: Path, vault: Path, state: dict) -> int:
    src = data_dir / "qaa_knowledge" / "trading_lessons.jsonl"
    if not src.exists():
        return 0
    dst = ensure_dir(vault / DIR_LESSONS)
    synced_key = "lessons"
    synced = state.setdefault("synced_files", {}).setdefault(synced_key, set())

    count = 0
    with src.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            chunk_id = d.get("chunk_id", "")
            if not chunk_id or chunk_id in synced:
                continue
            md = d.get("metadata", {}) or {}

            text = d.get("text", "")
            symbol = md.get("symbol", "?")
            side = md.get("side", "")
            pnl = md.get("pnl")
            pnl_pct = md.get("pnl_pct")
            strategy_id = md.get("strategy_id", "")
            tier = md.get("tier", "")
            exit_reason = md.get("exit_reason", "")
            trade_nature = md.get("trade_nature", "")
            was_correct = md.get("was_correct", "")

            pnl_label = f"pnl: {pnl_emoji(pnl)} {pnl}" if pnl else ""
            title = f"lesson-{chunk_id[:12]}"

            fm_meta = {
                "type": "lesson",
                "chunk_id": chunk_id,
                "symbol": symbol if symbol != "?" else "",
                "side": side,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "strategy_id": strategy_id,
                "tier": tier,
                "exit_reason": exit_reason,
                "trade_nature": trade_nature,
                "was_correct": was_correct,
                "source": d.get("source", "qaa"),
            }

            content = frontmatter(fm_meta)
            content += f"## {title}\n\n"
            if pnl_label:
                content += f"> {pnl_label}\n\n"
            content += text

            out = dst / f"lesson-{chunk_id}.md"
            out.write_text(content, encoding="utf-8")
            synced.add(chunk_id)
            count += 1

    if count:
        print(f"  [sync] 交易教训: {count} new entries")
    return count


def pnl_emoji(pnl) -> str:
    if pnl is None:
        return ""
    try:
        v = float(pnl)
        return "✅" if v >= 0 else "❌"
    except (ValueError, TypeError):
        return ""


# ──────────────────────────────────────────────────────────────
#  3. Governance / Arbitration Sync
# ──────────────────────────────────────────────────────────────

def sync_decisions(data_dir: Path, vault: Path, state: dict) -> int:
    """Scan data/decision_policies/ and data/runtime_tuning_snapshots/
    for governance records not yet in the vault."""
    dst = ensure_dir(vault / DIR_DECISIONS)
    synced_key = "decisions"
    synced = state.setdefault("synced_files", {}).setdefault(synced_key, set())

    sources = [
        data_dir / "decision_policies",
        data_dir / "runtime_tuning_snapshots",
    ]

    count = 0
    for src in sources:
        if not src.exists():
            continue
        for f in src.glob("*.json"):
            if f.stem in synced:
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue

            fm_meta = {
                "type": "governance",
                "source": f.stem,
                "date": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
            content = frontmatter(fm_meta)
            content += f"# ⚖️ {f.stem}\n\n```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```\n"

            out = dst / f"治理-{f.stem}.md"
            out.write_text(content, encoding="utf-8")
            synced.add(f.stem)
            count += 1

    if count:
        print(f"  [sync] 治理决策: {count} new files")
    return count


# ──────────────────────────────────────────────────────────────
#  4. Full refresh (re-run the original export script)
# ──────────────────────────────────────────────────────────────

def full_refresh(data_dir: Path, vault: Path) -> None:
    """Re-run the original export_to_obsidian.py for a complete rebuild."""
    export_script = PROJECT_ROOT / "tools" / "export_to_obsidian.py"
    if export_script.exists():
        print("  [sync] Running full refresh via export_to_obsidian.py...")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(export_script),
             "--data", str(data_dir),
             "--vault", str(vault)],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            print(f"    {line}")
        if result.returncode != 0:
            print(f"    [warn] export exited {result.returncode}: {result.stderr[:200]}")
    else:
        print(f"  [warn] export script not found at {export_script}")


# ──────────────────────────────────────────────────────────────
#  Obsidian REST API integration
# ──────────────────────────────────────────────────────────────

def notify_obsidian_reindex(vault: Path) -> bool:
    """Call Obsidian Local REST API to trigger vault re-index."""
    try:
        import urllib.request
        import json as j
        req = urllib.request.Request(
            f"{OBSIDIAN_API_URL}/api/command/",
            data=j.dumps({"command": "workspace:re-index-vault"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def notify_obsidian_open(vault: Path) -> bool:
    """Tell Obsidian to switch to this vault (if Local REST API is running)."""
    vault_name = vault.name
    try:
        import urllib.request
        import json as j
        req = urllib.request.Request(
            f"{OBSIDIAN_API_URL}/api/vault/{vault_name}/open",
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────
#  Main loop
# ──────────────────────────────────────────────────────────────

def sync_once(data_dir: Path, vault: Path, state: dict) -> dict:
    print(f"[{datetime.now():%H:%M:%S}] Obsidian Bridge sync...")
    sync_reports(data_dir, vault, state)
    sync_lessons(data_dir, vault, state)
    sync_decisions(data_dir, vault, state)
    state["last_sync"]["last_full"] = datetime.now(tz=timezone.utc).isoformat()
    save_state(vault, state)

    # Try to notify Obsidian (non-blocking)
    if notify_obsidian_reindex(vault):
        print("  [api] Obsidian re-index triggered")
    return state


def run_loop(data_dir: Path, vault: Path, interval: int = SYNC_INTERVAL) -> None:
    print(f"Obsidian Bridge — watching {data_dir} → {vault}")
    print(f"Poll interval: {interval}s  (state: {_state_path(vault)})")
    print("─" * 60)

    state = load_state(vault)
    do_full = True  # first run: full refresh

    while True:
        try:
            if do_full:
                full_refresh(data_dir, vault)
                state = load_state(vault)
                do_full = False
            else:
                state = sync_once(data_dir, vault, state)
        except KeyboardInterrupt:
            print("\nBridge stopped.")
            break
        except Exception as e:
            print(f"  [error] {e}")
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Obsidian Bridge — real-time vault sync")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="Data directory")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT, help="Obsidian vault directory")
    parser.add_argument("--once", action="store_true", help="Single sync pass, then exit")
    parser.add_argument("--interval", type=int, default=SYNC_INTERVAL, help="Poll interval (seconds)")
    parser.add_argument("--full", action="store_true", help="Force full refresh on next sync")
    args = parser.parse_args()

    state = load_state(args.vault)

    if args.full:
        full_refresh(args.data, args.vault)
        return

    if args.once:
        sync_once(args.data, args.vault, state)
        return

    run_loop(args.data, args.vault, args.interval)


if __name__ == "__main__":
    main()
