"""check_config_drift 单元测试（R1 配置漂移治理）"""
import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("check_config_drift", REPO / "scripts" / "check_config_drift.py")
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def test_load_facts_parses_table(tmp_path):
    md = tmp_path / "facts.md"
    md.write_text(
        "\n".join([
            "# Facts",
            "| 配置键 | 声明意图 | 期望值 | 备注 |",
            "| --- | --- | --- | --- |",
            "| MARKET_DATA_DC_ONLY | 行情唯一来源 | true | 备注A |",
            "| MIDLONG_EXEC_AUTHORITY | 执行权 | mlto | 备注B |",
            "| 普通文本 | 不应被解析 |",
        ]),
        encoding="utf-8",
    )
    facts = mod.load_facts(md)
    assert facts == [
        ("MARKET_DATA_DC_ONLY", "true", "行情唯一来源"),
        ("MIDLONG_EXEC_AUTHORITY", "mlto", "执行权"),
    ]


def test_load_env_skips_secrets_and_collects_duplicates(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "# comment",
            "V5_DECISION_CORE_ENABLED=true",
            "DEEPSEEK_API_KEY=sk-xxxx",        # 必须被跳过，绝不进入结果
            "DB_PASSWORD=secret",
            "SCALP_FACTOR_SCAN_INTERVAL_SEC=10",
            "SCALP_FACTOR_SCAN_INTERVAL_SEC=10",
            "invalid line without equals",
        ]),
        encoding="utf-8",
    )
    values, duplicates = mod.load_env(env)
    assert "DEEPSEEK_API_KEY" not in values
    assert "DB_PASSWORD" not in values
    assert values["V5_DECISION_CORE_ENABLED"] == "true"
    assert duplicates == ["SCALP_FACTOR_SCAN_INTERVAL_SEC"]


def test_run_check_ok(capsys):
    env = {"V5_DECISION_CORE_ENABLED": "true", "MIDLONG_EXEC_AUTHORITY": "mlto"}
    facts = [("V5_DECISION_CORE_ENABLED", "true", "x"), ("MIDLONG_EXEC_AUTHORITY", "mlto", "x")]
    assert mod.run_check(env, [], facts) == 0


def test_run_check_reports_drift_missing_duplicate(capsys):
    env = {"V5_DECISION_CORE_ENABLED": "false", "SCALP_X": "1", "SCALP_X": "2"}
    facts = [
        ("V5_DECISION_CORE_ENABLED", "true", "应开"),   # DRIFT
        ("ABSENT_TRUE_FLAG", "true", "缺键"),           # MISSING（期望 true 且 .env 无）
        ("ABSENT_FALSE_FLAG", "false", "缺键但默认"),    # OK(absent)
    ]
    code = mod.run_check(env, ["SCALP_X"], facts)
    out = capsys.readouterr().out
    assert code == 1
    assert "DRIFT" in out and "MISSING" in out and "DUPLICATE" in out


def test_same_boolean_equivalence():
    assert mod._same("yes", "true")
    assert mod._same("1", "true")
    assert mod._same("no", "false")
    assert mod._same("false", "off")
    assert not mod._same("true", "false")
    assert not mod._same("mlto", "trend")
