import os
import sys
import shutil
import pytest
from unittest.mock import MagicMock, patch
import time

# Set test DB path before importing sre_daemon
TEST_DB_PATH = "sre_state_test.db"

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
