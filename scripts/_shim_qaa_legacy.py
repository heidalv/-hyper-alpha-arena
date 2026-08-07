"""Replace legacy QAA block in monolith with thin shims."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)

start = next(i for i, l in enumerate(lines) if "def _register_qaa_agents(self" in l)
end = next(
    i for i, l in enumerate(lines)
    if i > start and "# ═══════════════════════════════════════════════════════════════════" in l
    and "编排器独立后台评估线程" in lines[i + 1]
)

sync = '''        self._pre_screen_results = host.pre_screen_results
        self._pre_screen_passed = host.pre_screen_passed
        self._qaa_last_decision = host.qaa_last_decision
        self._qaa_agents_registered = host.qaa_agents_registered

'''

shim = '''    def _register_qaa_agents(self):
        from backend.services.full_auto.qaa_legacy_cycle import (
            build_qaa_legacy_host,
            register_qaa_agents,
        )
        host = build_qaa_legacy_host(self)
        register_qaa_agents(host)
''' + sync + '''
    def _get_qaa_handler(self, agent_id: str):
        from backend.services.full_auto.qaa_legacy_cycle import (
            build_qaa_legacy_host,
            get_qaa_handler,
        )
        return get_qaa_handler(agent_id, build_qaa_legacy_host(self))

    def _run_qaa_tick(self, session_id: str):
        from backend.services.full_auto.qaa_legacy_cycle import (
            build_qaa_legacy_host,
            run_qaa_tick,
        )
        host = build_qaa_legacy_host(self)
        run_qaa_tick(session_id, host)
''' + sync

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"shimmed legacy QAA: removed {end - start} lines, kept 3 shims")
