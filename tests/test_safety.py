import os
import sqlite3
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from sre_daemon import (
    count_ast_nodes,
    check_circuit_breaker,
    log_repair_attempt,
    run_canary_probe,
    HealingOrchestrator,
    TenantRateLimiter,
    tenant_rate_limiter,
    DB_PATH
)

TEST_DB = Path("/tmp/sre_test_safety.db")

@pytest.fixture(autouse=True)
def setup_teardown_db():
    if TEST_DB.exists():
        TEST_DB.unlink()
    # Initialize the test database
    with sqlite3.connect(TEST_DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS repair_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                service_name TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                outcome TEXT NOT NULL,
                error_summary TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS circuit_breaker_state (
                tenant_id TEXT NOT NULL,
                service_name TEXT NOT NULL,
                frozen_until TEXT,
                PRIMARY KEY (tenant_id, service_name)
            )
        """)
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()

def test_ast_node_counter():
    code_a = "def add(a, b):\n    return a + b"
    code_b = "def add(a, b):\n    # some comment\n    return a + b"
    code_c = "def stub(): pass"
    
    nodes_a = count_ast_nodes(code_a)
    nodes_b = count_ast_nodes(code_b)
    nodes_c = count_ast_nodes(code_c)
    
    assert nodes_a > 0
    assert nodes_b > 0
    assert nodes_c > 0
    
    # Small comment addition shouldn't drift AST nodes significantly
    assert abs(nodes_a - nodes_b) / max(nodes_a, 1) < 0.2
    
    # Drastic functionality deletion (e.g. replacing a function body with pass stub)
    # should drift AST nodes by more than 40%
    assert abs(nodes_a - nodes_c) / max(nodes_a, 1) > 0.4

def test_circuit_breaker_db_tracking():
    tenant_id = "test-tenant"
    service_name = "test-service"
    
    # Initially allowed
    allowed, reason = check_circuit_breaker(tenant_id, service_name, TEST_DB)
    assert allowed is True
    assert reason is None
    
    # Log 3 failures within last 15 minutes
    log_repair_attempt(tenant_id, service_name, "repair_failed", db_path=TEST_DB)
    log_repair_attempt(tenant_id, service_name, "canary_failed", db_path=TEST_DB)
    log_repair_attempt(tenant_id, service_name, "repair_failed", db_path=TEST_DB)
    
    # CB should freeze
    allowed, reason = check_circuit_breaker(tenant_id, service_name, TEST_DB)
    assert allowed is False
    assert reason == "limit_exceeded_freezing"

def test_canary_probing():
    # 1. Successful command canary
    probe_ok = {
        "command": "echo '{\"status\":\"ok\"}'",
        "expected_pattern": '"status"\\s*:\\s*"ok"',
        "timeout_seconds": 2
    }
    ok, details = run_canary_probe(probe_ok)
    assert ok is True
    assert details == "ok"
    
    # 2. Failed command canary (exit code)
    probe_fail_exit = {
        "command": "false",
        "timeout_seconds": 2
    }
    ok, details = run_canary_probe(probe_fail_exit)
    assert ok is False
    assert "exit_code" in details
    
    # 3. Failed pattern mismatch canary
    probe_fail_pattern = {
        "command": "echo '{\"status\":\"failed\"}'",
        "expected_pattern": '"status"\\s*:\\s*"ok"',
        "timeout_seconds": 2
    }
    ok, details = run_canary_probe(probe_fail_pattern)
    assert ok is False
    assert details == "response_body_pattern_mismatch"

def test_tenant_rate_limiter():
    limiter = TenantRateLimiter(calls_per_minute=2)
    tenant_a = "tenant-a"
    tenant_b = "tenant-b"
    
    # First call allowed
    assert limiter.allow(tenant_a) is True
    # Second call allowed
    assert limiter.allow(tenant_a) is True
    # Third call within same minute rate limited (returns False)
    assert limiter.allow(tenant_a) is False
    
    # Tenant B should be independent (Blast Radius Isolation)
    assert limiter.allow(tenant_b) is True

def test_verify_patch_intent():
    orchestrator = HealingOrchestrator()
    original_patch = "def calculate_total(price, tax):\n    return price * (1 + tax)"
    repaired_valid = "def calculate_total(price, tax):\n    # Fix tax value\n    return price * (1 + tax)"
    repaired_invalid = "def calculate_total(price, tax):\n    return price"
    
    # Mock LLM cascade query response
    with patch.object(orchestrator, "query_llm_cascade") as mock_query:
        # Mock semantic consistency pass
        mock_query.return_value = ("CONSISTENT\nThe fix preserves original logic.", "mock-cascade")
        is_consistent, reason = orchestrator.verify_patch_intent(original_patch, repaired_valid, "some error", "tenant-1")
        assert is_consistent is True
        assert "mock-cascade" in reason
        
        # Mock semantic consistency fail (LLM detects code deletion/stubbing)
        mock_query.return_value = ("INCONSISTENT\nReplaced functional code with stub/empty return.", "mock-cascade")
        is_consistent, reason = orchestrator.verify_patch_intent(original_patch, repaired_invalid, "some error", "tenant-1")
        assert is_consistent is False
        assert "structural_drift" in reason or "INCONSISTENT" in reason

@patch("sre_daemon.send_telegram_text")
@patch("subprocess.run")
def test_safe_rollback_success(mock_sub, mock_tg):
    orchestrator = HealingOrchestrator()
    repo_dir = Path("/tmp/mock_repo")
    tag = "mock_tag"
    
    # Success case: git reset and tag delete commands run successfully
    mock_sub.returncode = 0
    
    # Mock manifest loading for rebuild
    orchestrator.manifest = MagicMock()
    orchestrator.manifest.get_service_by_name.return_value = None
    
    ok = orchestrator.safe_rollback("tenant-1", "test-service", repo_dir, tag)
    assert ok is True
    mock_tg.assert_called_once()
    assert "Rollback Yapıldı" in mock_tg.call_args[0][1]

@patch("sre_daemon.send_telegram_text")
@patch("subprocess.run")
def test_safe_rollback_failure_last_resort(mock_sub, mock_tg):
    orchestrator = HealingOrchestrator()
    repo_dir = Path("/tmp/mock_repo")
    tag = "mock_tag"
    
    # Git rollback fails (subprocess raises an exception)
    mock_sub.side_effect = subprocess.SubprocessError("Git error")
    
    # Mock manifest config to simulate service teardown
    orchestrator.manifest = MagicMock()
    mock_svc = {
        "name": "test-service",
        "runtime": "systemd",
        "unit": "test-service.service"
    }
    orchestrator.manifest.get_service_by_name.return_value = mock_svc
    
    # Since rollback failed, should trigger last resort stop and send critical P0 alarm
    ok = orchestrator.safe_rollback("tenant-1", "test-service", repo_dir, tag)
    assert ok is False
    
    # Assert that P0 telegram alert was sent
    calls = [c[0][1] for c in mock_tg.call_args_list]
    assert any("P0: ROLLBACK FAILED" in text for text in calls)
    
    # Assert that systemctl stop was called to teardown the service
    stop_called = any(
        "stop" in str(args) or "systemctl" in str(args)
        for args, kwargs in mock_sub.call_args_list
    )
    assert stop_called is True
