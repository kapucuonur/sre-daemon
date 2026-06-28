"""
tests/test_strategy_registry.py
Pytest unit tests — strategy registry fonksiyonları için.
Çalıştır: pytest tests/test_strategy_registry.py -v
"""

import os
import sqlite3
import tempfile
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sre_daemon import (
    compute_error_hash,
    get_best_strategy,
    update_strategy_result,
    register_actions_in_registry,
)

STRATEGY_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS strategy_registry (
    error_hash      TEXT    NOT NULL,
    command         TEXT    NOT NULL,
    success_count   INTEGER DEFAULT 0,
    fail_count      INTEGER DEFAULT 0,
    weight          INTEGER DEFAULT 0,
    is_blacklisted  INTEGER DEFAULT 0,
    last_used       TEXT,
    PRIMARY KEY (error_hash, command)
);
"""


@pytest.fixture
def db_path():
    """Her test için geçici SQLite DB."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    with sqlite3.connect(path) as conn:
        conn.execute(STRATEGY_REGISTRY_DDL)
        conn.commit()
    yield path
    os.unlink(path)


# ─────────────────────────────────────────────
# compute_error_hash
# ─────────────────────────────────────────────
class TestComputeErrorHash:
    def test_same_input_same_hash(self):
        h1 = compute_error_hash("myapp", "OOMKilled exit code 137")
        h2 = compute_error_hash("myapp", "OOMKilled exit code 137")
        assert h1 == h2

    def test_same_error_different_container_same_hash(self):
        h1 = compute_error_hash("app1", "connection refused")
        h2 = compute_error_hash("app2", "connection refused")
        assert h1 == h2

    def test_hash_length_16(self):
        h = compute_error_hash("myapp", "some error")
        assert len(h) == 16

    def test_case_insensitive(self):
        h1 = compute_error_hash("myapp", "OOMKilled")
        h2 = compute_error_hash("myapp", "oomkilled")
        assert h1 == h2

    def test_long_snippet_truncated(self):
        base = "x" * 200
        short = compute_error_hash("c", base)
        long_ = compute_error_hash("c", base + "this_extra_content_should_be_ignored" * 10)
        # İlk 200 karakter aynı ise hash aynı olmalı
        assert short == long_


# ─────────────────────────────────────────────
# get_best_strategy
# ─────────────────────────────────────────────
class TestGetBestStrategy:
    def test_returns_none_when_empty(self, db_path):
        result = get_best_strategy(db_path, "nonexistent_hash")
        assert result is None

    def test_returns_highest_weight(self, db_path):
        h = "testhash001"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO strategy_registry VALUES (?,?,?,?,?,?,?)",
                (h, "docker restart app", 5, 0, 10, 0, None),
            )
            conn.execute(
                "INSERT INTO strategy_registry VALUES (?,?,?,?,?,?,?)",
                (h, "docker restart db", 1, 0, 2, 0, None),
            )
            conn.commit()
        assert get_best_strategy(db_path, h) == "docker restart app"

    def test_skips_blacklisted(self, db_path):
        h = "testhash002"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO strategy_registry VALUES (?,?,?,?,?,?,?)",
                (h, "bad command", 0, 5, -2, 1, None),  # blacklisted
            )
            conn.execute(
                "INSERT INTO strategy_registry VALUES (?,?,?,?,?,?,?)",
                (h, "good command", 3, 0, 6, 0, None),
            )
            conn.commit()
        assert get_best_strategy(db_path, h) == "good command"

    def test_returns_none_all_blacklisted(self, db_path):
        h = "testhash003"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO strategy_registry VALUES (?,?,?,?,?,?,?)",
                (h, "bad command", 0, 5, -2, 1, None),
            )
            conn.commit()
        assert get_best_strategy(db_path, h) is None

    def test_weight_decay(self, db_path):
        from datetime import datetime, timezone, timedelta
        h = "decay_hash"
        # old command: 60 days age => decay 0.5**2 = 0.25. weight 10 * 0.25 = 2.
        # new command: 0 days age => weight 4.
        now_60_days_ago = (datetime.now(timezone.utc) - timedelta(days=60.1)).isoformat()
        now_str = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO strategy_registry VALUES (?,?,?,?,?,?,?)",
                (h, "old command", 5, 0, 10, 0, now_60_days_ago)
            )
            conn.execute(
                "INSERT INTO strategy_registry VALUES (?,?,?,?,?,?,?)",
                (h, "new command", 2, 0, 4, 0, now_str)
            )
            conn.commit()
        assert get_best_strategy(db_path, h) == "new command"


# ─────────────────────────────────────────────
# update_strategy_result
# ─────────────────────────────────────────────
class TestUpdateStrategyResult:
    def test_new_entry_success(self, db_path):
        h, cmd = "hash_new", "docker restart foo"
        update_strategy_result(db_path, h, cmd, success=True)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT success_count, fail_count, weight, is_blacklisted FROM strategy_registry WHERE error_hash=? AND command=?",
                (h, cmd),
            ).fetchone()
        assert row == (1, 0, 2, 0)

    def test_new_entry_failure(self, db_path):
        h, cmd = "hash_fail", "docker restart foo"
        update_strategy_result(db_path, h, cmd, success=False)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT success_count, fail_count, weight, is_blacklisted FROM strategy_registry WHERE error_hash=? AND command=?",
                (h, cmd),
            ).fetchone()
        assert row == (0, 1, -1, 1)  # weight<0 → blacklisted

    def test_accumulates_correctly(self, db_path):
        h, cmd = "hash_acc", "docker restart foo"
        update_strategy_result(db_path, h, cmd, success=True)   # weight=2
        update_strategy_result(db_path, h, cmd, success=True)   # weight=4
        update_strategy_result(db_path, h, cmd, success=False)  # weight=3
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT success_count, fail_count, weight FROM strategy_registry WHERE error_hash=? AND command=?",
                (h, cmd),
            ).fetchone()
        assert row == (2, 1, 3)

    def test_blacklisted_after_repeated_failures(self, db_path):
        h, cmd = "hash_bl", "bad cmd"
        # 1 başarı (weight=2), sonra 3 başarısızlık (weight=-1)
        update_strategy_result(db_path, h, cmd, success=True)
        for _ in range(3):
            update_strategy_result(db_path, h, cmd, success=False)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT weight, is_blacklisted FROM strategy_registry WHERE error_hash=? AND command=?",
                (h, cmd),
            ).fetchone()
        # weight = 2 - 3 = -1 → blacklisted
        assert row[0] == -1
        assert row[1] == 1

    def test_idempotent_insert(self, db_path):
        h, cmd = "hash_idem", "docker restart foo"
        update_strategy_result(db_path, h, cmd, success=True)
        update_strategy_result(db_path, h, cmd, success=True)
        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM strategy_registry WHERE error_hash=? AND command=?",
                (h, cmd),
            ).fetchone()[0]
        assert count == 1  # INSERT OR IGNORE, sadece bir kayıt


# ─────────────────────────────────────────────
# register_actions_in_registry
# ─────────────────────────────────────────────
class TestRegisterActionsInRegistry:
    def test_registers_all_actions(self, db_path):
        h = "hash_multi"
        actions = ["docker restart app", "docker logs app", ""]
        register_actions_in_registry(db_path, h, actions, success=True)
        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM strategy_registry WHERE error_hash=?", (h,)
            ).fetchone()[0]
        assert count == 2  # boş string atlanmalı

    def test_empty_actions_list(self, db_path):
        register_actions_in_registry(db_path, "hash_empty", [], success=True)
        with sqlite3.connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM strategy_registry").fetchone()[0]
        assert count == 0

    def test_failure_blacklists_after_enough_fails(self, db_path):
        h = "hash_bf"
        cmd = "bad action"
        # Hiç başarı yok, direkt fail
        register_actions_in_registry(db_path, h, [cmd], success=False)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT is_blacklisted FROM strategy_registry WHERE error_hash=? AND command=?",
                (h, cmd),
            ).fetchone()
        assert row[0] == 1  # weight=-1 → blacklisted


# ─────────────────────────────────────────────
# Integration: tam senaryo
# ─────────────────────────────────────────────
class TestIntegrationScenario:
    def test_full_learning_cycle(self, db_path):
        """
        1. İlk hata: registry boş → None döner (LLM devreye girer)
        2. LLM başarılı çözüm → registry'e kaydet
        3. Aynı hata tekrar: registry'den çözüm döner
        4. Çözüm başarısız çalışır → kara listeye al
        5. Yeni query → None döner (LLM tekrar devreye)
        """
        h = compute_error_hash("coachonurai", "OOMKilled exit 137")
        cmd = "docker restart coachonurai"

        # Adım 1: Boş registry
        assert get_best_strategy(db_path, h) is None

        # Adım 2: LLM çözüm buldu, kaydet
        register_actions_in_registry(db_path, h, [cmd], success=True)

        # Adım 3: Registry hit
        assert get_best_strategy(db_path, h) == cmd

        # Adım 4: Bu sefer cached cmd başarısız (3 kere)
        for _ in range(3):
            update_strategy_result(db_path, h, cmd, success=False)

        # Adım 5: Blacklisted → None
        assert get_best_strategy(db_path, h) is None
