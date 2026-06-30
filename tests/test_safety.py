import os
import sqlite3
import pytest
from pathlib import Path
from sre_daemon import (
    count_ast_nodes,
    check_circuit_breaker,
    log_repair_attempt,
    run_canary_probe,
    HealingOrchestrator,
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
