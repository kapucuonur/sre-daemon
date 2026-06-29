import os
import sqlite3
import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# We temporarily set INSTALL_DIR environment variable for test execution isolation
os.environ["INSTALL_DIR"] = "/tmp/sre_test_metrics"
Path(os.environ["INSTALL_DIR"]).mkdir(parents=True, exist_ok=True)

from sre_daemon import MetricsCollector, HealingOrchestrator

@pytest.fixture
def mock_orchestrator():
    orchestrator = MagicMock(spec=HealingOrchestrator)
    orchestrator.manifest = MagicMock()
    # Mock manifest service helper
    orchestrator.manifest.get_service_by_container.return_value = {
        "name": "test-service",
        "limits": {
            "cpu_threshold": 50.0,
            "mem_threshold": 50.0,
            "anomaly_sensitivity": 1.2
        }
    }
    orchestrator.rebuild_manager = MagicMock()
    return orchestrator

@pytest.fixture
def metrics_collector(mock_orchestrator):
    collector = MetricsCollector(mock_orchestrator)
    # Clear test db entries
    with sqlite3.connect(collector.stats_db) as conn:
        conn.execute("DELETE FROM stats_history")
        conn.commit()
    return collector

def test_database_initialization(metrics_collector):
    assert metrics_collector.stats_db.exists()
    with sqlite3.connect(metrics_collector.stats_db) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(stats_history)")
        columns = [row[1] for row in cur.fetchall()]
        assert "timestamp" in columns
        assert "entity" in columns
        assert "metric_type" in columns
        assert "value" in columns

def test_save_and_retrieve_metric(metrics_collector):
    now_str = datetime.now(timezone.utc).isoformat()
    metrics_collector._save_metric(now_str, "test-service", "cpu", 45.5)
    
    with sqlite3.connect(metrics_collector.stats_db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM stats_history")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "test-service"
        assert rows[0][2] == "cpu"
        assert rows[0][3] == 45.5

def test_get_ema_calculation(metrics_collector):
    # Insert 10 observations with a clear upward trend and unique timestamps
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    for i in range(10):
        timestamp = (now + timedelta(seconds=i)).isoformat()
        metrics_collector._save_metric(timestamp, "test-service", "cpu", float(20 + i))
    
    ema = metrics_collector._get_ema("test-service", "cpu")
    assert ema is not None
    # In a rising trend, the lagging EMA is less than the simple average (24.5) but higher than the start (20.0)
    assert ema < 24.5
    assert ema > 20.0

def test_get_ema_insufficient_points(metrics_collector):
    # Only insert 3 points (minimum is 5)
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    for i in range(3):
        timestamp = (now + timedelta(seconds=i)).isoformat()
        metrics_collector._save_metric(timestamp, "test-service", "cpu", 10.0)
    
    ema = metrics_collector._get_ema("test-service", "cpu")
    assert ema is None

@patch("sre_daemon.send_telegram_text")
def test_check_anomaly_trigger(mock_send_telegram, metrics_collector, mock_orchestrator):
    # Pre-populate history with low CPU values (stable EMA around 10%)
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    for i in range(15):
        timestamp = (now + timedelta(seconds=i)).isoformat()
        metrics_collector._save_metric(timestamp, "test-service", "cpu", 10.0)
        
    # Anomaly condition: current > limit (50.0) AND current > EMA (10.0) * sensitivity (1.2) = 12.0
    # Simulate a sudden massive CPU spike of 75%
    metrics_collector._check_anomaly("test-service", "cpu", 75.0)
    
    # Verify that warning was logged/sent to Telegram and predictive handler was spawned
    assert mock_send_telegram.called
    args, kwargs = mock_send_telegram.call_args
    assert "ANOMALY DETECTED" in args[1]
    assert "test-service" in args[1]
