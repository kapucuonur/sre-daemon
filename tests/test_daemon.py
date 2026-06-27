import os
import sys
import shutil
import pytest
from unittest.mock import MagicMock, patch
import time

from pathlib import Path

# Set test DB path before importing sre_daemon
TEST_DB_PATH = Path("sre_state_test.db")

# Import sre-daemon module path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sre_daemon

# Mock DB_PATH directly in the imported module
sre_daemon.DB_PATH = TEST_DB_PATH

@pytest.fixture(autouse=True)
def setup_and_teardown():
    sre_daemon.init_db()
    yield
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except OSError:
            pass

def test_rate_limiter():
    limiter = sre_daemon.RateLimiter()
    # First attempt should succeed
    assert limiter.should_process("test-key", limit_seconds=2) is True
    # Second immediate attempt should fail
    assert limiter.should_process("test-key", limit_seconds=2) is False
    
    # Wait for expiry
    time.sleep(2.1)
    # Third attempt after cooldown should succeed
    assert limiter.should_process("test-key", limit_seconds=2) is True

def test_handle_error_err_hash_generation():
    orchestrator = sre_daemon.HealingOrchestrator()
    
    # Mock self._heal to make sure we don't call actual APIs or threads
    with patch.object(orchestrator, "_heal") as mock_heal:
        # Call handle_error without err_hash
        orchestrator.handle_error("error line 123", "[Test]")
        
        # Wait a short moment since it runs in a thread
        time.sleep(0.5)
        
        # Verify that _heal was called
        mock_heal.assert_called_once()
        args = mock_heal.call_args[0]
        assert args[0] == "error line 123"
        assert args[1] == "[Test]"
        # The third argument (err_hash) must have been automatically generated!
        assert args[2] is not None
        assert len(args[2]) == 32  # MD5 hash length is 32 characters

def test_ollama_client_query():
    client = sre_daemon.OllamaClient()
    
    with patch("requests.post") as mock_post:
        # Mock requests.post response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "test ai output"}
        mock_post.return_value = mock_resp
        
        result = client.query("http://localhost:11434", "test-model", "test-prompt")
        assert result == "test ai output"
        mock_post.assert_called_once()

def test_daemon_settings():
    # Verify default setting
    assert sre_daemon.get_daemon_setting("autonomous_mode", "0") == "0"
    
    # Verify modification
    sre_daemon.set_daemon_setting("autonomous_mode", "1")
    assert sre_daemon.get_daemon_setting("autonomous_mode", "0") == "1"

def test_heal_history_and_prompt_injection():
    # Save a test history record
    sre_daemon.save_heal_history(
        error_hash="test_hash_123",
        error_message="critical database exception line 45",
        project_tag="[Test-Servis]",
        risk_level="High",
        prompt="original prompt info",
        llm_response="{}",
        llm_source="mock-gemini",
        actions=[{"type": "shell", "payload": "docker restart test"}],
        execution_output=[{"status": "success"}],
        success=True,
        duration=1.5
    )

    # Retrieve history
    history = sre_daemon.get_heal_history_for_hash("test_hash_123")
    assert len(history) == 1
    assert history[0]["success"] == 1
    assert "test" in history[0]["actions_json"]

    # Verify that build prompt injects the past history context
    orchestrator = sre_daemon.HealingOrchestrator()
    prompt = orchestrator._build_prompt("new error details", "[Test-Servis]", "test_hash_123")
    
    # The prompt should contain "GEÇMİŞ ONARIM GİRİŞİMLERİ" and mention success/actions
    assert "GEÇMİŞ ONARIM GİRİŞİMLERİ" in prompt
    assert "BAŞARILI" in prompt
    assert "shell -> ? (docker restart test)" in prompt
